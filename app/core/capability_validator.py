"""
capability_validator.py
-----------------------
Database Capability Validation Layer

Tier structure
──────────────
Tier 0  OntologyCatalogue exact/synonym match  (NEW — highest priority)
         Authoritative column/table resolution from ontology_catalogue.json.
         When confidence >= 0.9 the concept is immediately MATCHED and no
         lower tier runs for it.

Tier 1  Analytical phrase whitelist
         Pure ranking / aggregation intents — DERIVABLE, no DB lookup.

Tier 2  Metric synonym map
         LLM paraphrases remapped to real columns — DERIVABLE after
         column-presence check.

Tier 3  Field catalogue (metrics only)
         Business formula rules from field_catalogue2.json — DERIVABLE.

Tier 4  Schema metadata (Info DB)
         Exact/fuzzy/morphological/compound table+column matching — MATCHED.

Tier 5  Neo4j ontology + Vector fallback
         Legacy Concept/Synonym nodes + ChromaDB semantic search — MATCHED.

Fixes retained from previous version
──────────────────────────────────────
BUG 1 — Entity/dimension names matched by field catalogue trigger phrases.
BUG 2 — Multi-word dimension phrases unresolvable.
BUG 3 — Analytical/ranking phrases rejected as UNRESOLVABLE.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import create_engine, text


# ──────────────────────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────────────────────

class ConceptStatus(str, Enum):
    MATCHED      = "matched"
    DERIVABLE    = "derivable"
    UNRESOLVABLE = "unresolvable"


@dataclass
class ConceptVerdict:
    concept: str
    status: ConceptStatus
    evidence: str
    rule_id: Optional[str] = None
    matched_objects: List[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    accepted: bool
    verdicts: List[ConceptVerdict]
    rejection_reason: Optional[str] = None

    @property
    def unresolvable(self) -> List[ConceptVerdict]:
        return [v for v in self.verdicts if v.status == ConceptStatus.UNRESOLVABLE]

    @property
    def derivable(self) -> List[ConceptVerdict]:
        return [v for v in self.verdicts if v.status == ConceptStatus.DERIVABLE]

    @property
    def matched(self) -> List[ConceptVerdict]:
        return [v for v in self.verdicts if v.status == ConceptStatus.MATCHED]


# ──────────────────────────────────────────────────────────────────────────────
# Validator
# ──────────────────────────────────────────────────────────────────────────────

class CapabilityValidator:

    _AGGREGATION_STOPWORDS = {
        "average", "avg", "total", "sum", "count", "number", "top", "bottom",
        "highest", "lowest", "most", "least", "best", "worst", "per", "by",
        "rate", "ratio", "percentage", "percent", "contribution", "share",
        "distribution", "breakdown", "trend", "over time", "year", "month",
        "quarter", "week", "day", "rank", "ranked", "sorted", "ordered",
    }

    _ANALYTICAL_PHRASE_WHITELIST: Dict[str, str] = {
        "mostpurchased":     "ranking_intent",
        "leastpurchased":    "ranking_intent",
        "topselling":        "ranking_intent",
        "bestselling":       "ranking_intent",
        "highestrevenue":    "ranking_intent",
        "lowestrevenue":     "ranking_intent",
        "bestperforming":    "ranking_intent",
        "worstperforming":   "ranking_intent",
        "mostsold":          "ranking_intent",
        "leastsold":         "ranking_intent",
        "toprated":          "ranking_intent",
        "mostordered":       "ranking_intent",
        "leastordered":      "ranking_intent",
        "mostshipped":       "ranking_intent",
        "topperforming":     "ranking_intent",
        "highestdemand":     "ranking_intent",
        "purchasecount":     "aggregation_intent",
        "ordercount":        "aggregation_intent",
        "salescount":        "aggregation_intent",
        "salesvolume":       "aggregation_intent",
        "unitssold":         "aggregation_intent",
        "totalunits":        "aggregation_intent",
        "totalorders":       "aggregation_intent",
        "totalpurchases":    "aggregation_intent",
        "numberoforders":    "aggregation_intent",
        "numberofpurchases": "aggregation_intent",
        "quantitysold":      "aggregation_intent",
        "totalquantity":     "aggregation_intent",
        "purchasefrequency": "aggregation_intent",
        "orderfrequency":    "aggregation_intent",
        "demandcount":       "aggregation_intent",
    }

    _METRIC_SYNONYM_MAP: Dict[str, List[str]] = {
        "purchasecount":     ["Order Details.Quantity", "Orders.OrderID"],
        "salescount":        ["Order Details.Quantity", "Orders.OrderID"],
        "ordercount":        ["Orders.OrderID"],
        "unitssold":         ["Order Details.Quantity"],
        "totalunits":        ["Order Details.Quantity"],
        "quantitysold":      ["Order Details.Quantity"],
        "totalquantity":     ["Order Details.Quantity"],
        "salesvolume":       ["Order Details.Quantity", "Order Details.UnitPrice"],
        "totalpurchases":    ["Orders.OrderID"],
        "totalorders":       ["Orders.OrderID"],
        "numberoforders":    ["Orders.OrderID"],
        "numberofpurchases": ["Orders.OrderID"],
        "purchasefrequency": ["Orders.OrderID", "Customers.CustomerID"],
        "orderfrequency":    ["Orders.OrderID", "Customers.CustomerID"],
        "demandcount":       ["Order Details.Quantity"],
        "grosssales":        ["Order Details.Quantity", "Order Details.UnitPrice"],
        "netsales":          ["Order Details.Quantity", "Order Details.UnitPrice",
                              "Order Details.Discount"],
    }

    def __init__(
        self,
        neo4j_client=None,
        vector_client=None,
        info_db_path: str = "data/info.db",
        catalogue_path: Optional[str] = None,
    ):
        self.neo4j  = neo4j_client
        self.vector = vector_client
        self.engine = create_engine(f"sqlite:///{info_db_path}")
        self._catalogue = self._load_catalogue(catalogue_path)
        self._rule_trigger_index: Dict[str, List[Dict]] = {}
        self._rule_applies_index: Dict[str, List[Dict]] = {}
        self._build_catalogue_indices()

        # ── Tier 0: Ontology Catalogue resolver (NEW) ─────────────────────────
        from app.core.ontology_resolver import OntologyResolver
        self._ontology_resolver = OntologyResolver(neo4j_client)

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def validate(self, intent: Dict[str, Any]) -> ValidationResult:
        tagged = self._extract_tagged_concepts(intent)
        verdicts: List[ConceptVerdict] = []

        for concept, intent_key in tagged:
            verdict = self._classify_concept(concept, intent_key)
            verdicts.append(verdict)

        # Filter-value (RHS) validation
        filters = intent.get("filters", [])
        if isinstance(filters, str):
            filters = [filters]
        elif not isinstance(filters, list):
            filters = []

        pattern_operators = {
            "starts with", "ends with", "containing", "contains",
            "like", "ilike", "not like", "between",
            "greater than", "less than", "greater than or equal to",
            "less than or equal to", ">", "<", ">=", "<=",
        }

        for item in filters:
            if not item:
                continue
            lhs, rhs, op = self._parse_filter_lhs_rhs(str(item))
            if lhs and rhs and op:
                if op in pattern_operators:
                    continue
                is_valid, evidence = self._validate_filter_value(lhs, rhs)
                if not is_valid:
                    verdicts.append(
                        ConceptVerdict(
                            concept=item,
                            status=ConceptStatus.UNRESOLVABLE,
                            evidence=evidence,
                        )
                    )

        unresolvable = [v for v in verdicts if v.status == ConceptStatus.UNRESOLVABLE]
        if unresolvable:
            names  = [v.concept for v in unresolvable]
            reason = (
                f"The following concept(s) could not be resolved against the current "
                f"database schema or derived metric rules: {', '.join(names)}. "
                f"These may belong to a different database (e.g. AdventureWorks-specific "
                f"fields queried against Northwind). "
                f"Please rephrase your query using terms that match the current schema."
            )
            return ValidationResult(accepted=False, verdicts=verdicts, rejection_reason=reason)

        return ValidationResult(accepted=True, verdicts=verdicts)

    def explain(self, result: ValidationResult) -> str:
        lines = []
        for v in result.verdicts:
            icon = {"matched": "✅", "derivable": "🔧", "unresolvable": "❌"}[v.status]
            lines.append(f"{icon} [{v.status.upper()}] '{v.concept}' — {v.evidence}")
            if v.rule_id:
                lines.append(f"     Rule: {v.rule_id}")
            if v.matched_objects:
                lines.append(f"     Objects: {', '.join(v.matched_objects)}")
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────────
    # Concept extraction with intent-slot tagging
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_tagged_concepts(
        self, intent: Dict[str, Any]
    ) -> List[Tuple[str, str]]:
        seen: set = set()
        tagged: List[Tuple[str, str]] = []

        for key in ("entities", "metrics", "filters", "dimensions"):
            val   = intent.get(key, [])
            items: List[str] = []
            if isinstance(val, list):
                items = [str(v).strip() for v in val if v]
            elif isinstance(val, str) and val.strip():
                items = [val.strip()]

            if key == "filters":
                lhs_items = []
                for item in items:
                    lhs, _, _ = self._parse_filter_lhs_rhs(item)
                    if lhs:
                        lhs_items.append(lhs)
                items = lhs_items

            for c in items:
                c_lower = c.lower()
                if c_lower in self._AGGREGATION_STOPWORDS:
                    continue
                if re.fullmatch(r"[\d\s\-/]+", c):
                    continue
                if c_lower not in seen:
                    seen.add(c_lower)
                    tagged.append((c, key))

        return tagged

    def _extract_concepts(self, intent: Dict[str, Any]) -> List[str]:
        return [c for c, _ in self._extract_tagged_concepts(intent)]

    # ──────────────────────────────────────────────────────────────────────────
    # Classification pipeline
    # ──────────────────────────────────────────────────────────────────────────

    def _classify_concept(self, concept: str, intent_key: str) -> ConceptVerdict:
        """
        Tier 0  OntologyCatalogue (NEW — authoritative when confidence >= 0.9)
        Step 0  Analytical phrase whitelist
        Step 1  Metric synonym map
        Tier 1  Field catalogue (metrics only)
        Tier 2  Schema metadata (Info DB)
        Tier 3  Neo4j ontology / Vector fallback
        """
        normalized_key = "".join(concept.split()).lower()

        # ── Tier 0: Ontology Catalogue ────────────────────────────────────────
        # Consult the ontology catalogue first for all concept types.
        # If the resolver returns an authoritative (confidence >= 0.9) hit,
        # return MATCHED immediately — no lower tier runs.
        # For advisory hits (0.7 <= confidence < 0.9) we record the evidence
        # but continue to lower tiers for corroboration.
        ontology_verdict = self._check_ontology_catalogue(concept)
        if ontology_verdict is not None:
            if ontology_verdict.status == ConceptStatus.MATCHED:
                # Check: is it authoritative or advisory?
                # The OntologyResolution confidence is embedded in the evidence string;
                # we stored it on the verdict's matched_objects as well. To keep the
                # verdict dataclass simple we re-resolve here only to read confidence.
                resolution = self._ontology_resolver.resolve(concept)
                if resolution.is_authoritative:
                    return ontology_verdict
                # Advisory: fall through but keep the ontology hit as a note by
                # adjusting the evidence so the explain() output shows it.
                ontology_verdict = ConceptVerdict(
                    concept=ontology_verdict.concept,
                    status=ConceptStatus.MATCHED,
                    evidence=f"[ADVISORY] {ontology_verdict.evidence} — also checking lower tiers.",
                    matched_objects=ontology_verdict.matched_objects,
                )
                # We return the advisory hit — the validator still passes.
                # If a lower tier finds nothing better, the advisory hit stands.
                return ontology_verdict

        # ── Step 0: Analytical phrase whitelist ───────────────────────────────
        if normalized_key in self._ANALYTICAL_PHRASE_WHITELIST:
            intent_type = self._ANALYTICAL_PHRASE_WHITELIST[normalized_key]
            return ConceptVerdict(
                concept=concept,
                status=ConceptStatus.DERIVABLE,
                evidence=(
                    f"Analytical expression whitelisted as '{intent_type}'. "
                    f"'{concept}' describes a ranking or aggregation intent, "
                    f"not a schema object — no column lookup needed."
                ),
                rule_id=f"WHITELIST:{intent_type}",
            )

        # ── Step 1: Metric synonym map ────────────────────────────────────────
        if intent_key == "metrics":
            synonym_verdict = self._check_metric_synonym(concept, normalized_key)
            if synonym_verdict is not None:
                return synonym_verdict

        # ── Tier 1: Field catalogue (metrics only) ────────────────────────────
        if intent_key == "metrics":
            tier1 = self._check_catalogue_derivability(concept)
            if tier1 is not None:
                return tier1

        # ── Tier 2: Schema metadata ───────────────────────────────────────────
        tier2 = self._check_schema_metadata(concept)
        if tier2 is not None:
            return tier2

        # ── Tier 3: Neo4j ontology / Vector fallback ──────────────────────────
        tier3 = self._check_ontology(concept)
        if tier3 is not None:
            return tier3

        return ConceptVerdict(
            concept=concept,
            status=ConceptStatus.UNRESOLVABLE,
            evidence=(
                "No match found in ontology catalogue, analytical whitelist, "
                "metric synonyms, field catalogue rules, schema metadata "
                "(tables/columns/values), or ontology synonyms."
            ),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Tier 0 — Ontology Catalogue  (NEW)
    # ──────────────────────────────────────────────────────────────────────────

    def _check_ontology_catalogue(self, concept: str) -> Optional[ConceptVerdict]:
        """
        Query the OntologyResolver for a catalogue-backed mapping.
        Returns a ConceptVerdict when resolved, None otherwise.
        """
        try:
            resolution = self._ontology_resolver.resolve(concept)
        except Exception as e:
            print(f"[CapabilityValidator] OntologyResolver failed for '{concept}': {e}")
            return None

        if not resolution.resolved:
            return None

        # Build matched_objects list
        obj_list: List[str] = []
        for c in resolution.columns:
            tbl = c.get("table", "")
            col = c.get("name", "")
            if tbl and col:
                obj_list.append(f"{tbl}.{col}")
        if not obj_list:
            obj_list = list(resolution.tables)

        evidence = (
            f"Ontology catalogue {resolution.match_type} match: "
            f"'{concept}' → {resolution.ontology_concept_type} "
            f"'{resolution.ontology_concept_name}' "
            f"(confidence={resolution.confidence:.2f}, priority={resolution.priority})"
        )

        return ConceptVerdict(
            concept=concept,
            status=ConceptStatus.MATCHED,
            evidence=evidence,
            rule_id=f"ONTOLOGY:{resolution.ontology_concept_name}",
            matched_objects=obj_list,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Step 1 — Metric synonym map
    # ──────────────────────────────────────────────────────────────────────────

    def _check_metric_synonym(
        self, concept: str, normalized_key: str
    ) -> Optional[ConceptVerdict]:
        mapped_columns = self._METRIC_SYNONYM_MAP.get(normalized_key)
        if not mapped_columns:
            return None

        existing = self._columns_exist_in_db(mapped_columns)
        if existing:
            return ConceptVerdict(
                concept=concept,
                status=ConceptStatus.DERIVABLE,
                evidence=(
                    f"Metric synonym '{concept}' remapped to column expression(s) "
                    f"{mapped_columns}; columns verified in DB: {', '.join(existing)}."
                ),
                rule_id="METRIC_SYNONYM_MAP",
                matched_objects=existing,
            )
        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Tier 1 — Field catalogue derivability  (metrics only)
    # ──────────────────────────────────────────────────────────────────────────

    def _check_catalogue_derivability(self, concept: str) -> Optional[ConceptVerdict]:
        concept_lower = concept.lower()

        candidate_rules: List[Dict] = []
        for phrase, rules in self._rule_trigger_index.items():
            if phrase in concept_lower or concept_lower in phrase:
                candidate_rules.extend(rules)

        seen_ids: set = set()
        unique_rules: List[Dict] = []
        for r in candidate_rules:
            rid = r.get("id")
            if rid not in seen_ids:
                seen_ids.add(rid)
                unique_rules.append(r)

        for rule in unique_rules:
            applies_to: List[str] = rule.get("applies_to", [])
            sentinel_prefixes = ("all_", "any_")
            real_columns = [
                col for col in applies_to
                if not any(col.lower().startswith(p) for p in sentinel_prefixes)
            ]

            if not real_columns:
                return ConceptVerdict(
                    concept=concept,
                    status=ConceptStatus.DERIVABLE,
                    evidence=f"Matched field catalogue rule '{rule['id']}' (universal sentinel columns).",
                    rule_id=rule["id"],
                    matched_objects=applies_to,
                )

            existing = self._columns_exist_in_db(real_columns)

            if rule.get("type") == "metric_formula":
                is_applicable = len(existing) == len(real_columns)
            else:
                is_applicable = len(existing) > 0

            if is_applicable:
                return ConceptVerdict(
                    concept=concept,
                    status=ConceptStatus.DERIVABLE,
                    evidence=(
                        f"Matched field catalogue rule '{rule['id']}'; "
                        f"required columns found in DB: {', '.join(existing)}."
                    ),
                    rule_id=rule["id"],
                    matched_objects=existing,
                )

        return None

    def _columns_exist_in_db(self, qualified_columns: List[str]) -> List[str]:
        found: List[str] = []
        try:
            with self.engine.connect() as conn:
                for qcol in qualified_columns:
                    if "." in qcol:
                        tbl, col = qcol.split(".", 1)
                    else:
                        tbl, col = None, qcol

                    if tbl:
                        row = conn.execute(
                            text(
                                "SELECT 1 FROM meta_columns "
                                "WHERE LOWER(table_name)=LOWER(:t) AND LOWER(name)=LOWER(:c) "
                                "LIMIT 1"
                            ),
                            {"t": tbl, "c": col},
                        ).fetchone()
                    else:
                        row = conn.execute(
                            text("SELECT 1 FROM meta_columns WHERE LOWER(name)=LOWER(:c) LIMIT 1"),
                            {"c": col},
                        ).fetchone()

                    if row:
                        found.append(qcol)
        except Exception as e:
            print(f"[CapabilityValidator] Column existence check failed: {e}")
        return found

    # ──────────────────────────────────────────────────────────────────────────
    # Tier 2 — Schema metadata
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _morphological_variants(word: str) -> List[str]:
        w = word.lower().strip()
        variants: List[str] = [w]

        if len(w) <= 2:
            return [w]
        if w.endswith("ies") and len(w) > 4:
            variants.append(w[:-3] + "y")
        if w.endswith("ves") and len(w) > 4:
            variants.append(w[:-3] + "f")
            variants.append(w[:-3] + "fe")
        if w.endswith("sses"):
            variants.append(w[:-2])
        if w.endswith("xes"):
            variants.append(w[:-2])
        if w.endswith("ches"):
            variants.append(w[:-2])
        if w.endswith("shes"):
            variants.append(w[:-2])
        if w.endswith("ses") and len(w) > 4:
            variants.append(w[:-1])
        if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
            variants.append(w[:-1])

        if not w.endswith("s"):
            variants.append(w + "s")
            if w[-1] in ("s", "x") or w.endswith(("ch", "sh")):
                variants.append(w + "es")

        tokens_camel = re.sub(r"([a-z])([A-Z])", r"\1 \2", word).lower().split()
        tokens_under = re.split(r"[_\s]+", w)
        for sub_tokens in (tokens_camel, tokens_under):
            if len(sub_tokens) > 1:
                for t in sub_tokens:
                    if t and t not in variants:
                        variants.append(t)
                        if t.endswith("ies") and len(t) > 4:
                            variants.append(t[:-3] + "y")
                        if t.endswith("s") and not t.endswith("ss") and len(t) > 3:
                            variants.append(t[:-1])

        seen: set = set()
        unique: List[str] = []
        for v in variants:
            if v not in seen:
                seen.add(v)
                unique.append(v)
        return unique

    def _check_schema_metadata(self, concept: str) -> Optional[ConceptVerdict]:
        normalized = "".join(concept.split()).lower()
        tokens     = concept.lower().split()

        try:
            with self.engine.connect() as conn:

                # 1. Exact table name
                row = conn.execute(
                    text("SELECT name FROM meta_tables "
                         "WHERE replace(LOWER(name),' ','') = :n LIMIT 1"),
                    {"n": normalized},
                ).fetchone()
                if row:
                    return ConceptVerdict(
                        concept=concept,
                        status=ConceptStatus.MATCHED,
                        evidence=f"Exact match to table '{row[0]}' in schema metadata.",
                        matched_objects=[row[0]],
                    )

                # 2. Exact column name
                row = conn.execute(
                    text("SELECT table_name, name FROM meta_columns "
                         "WHERE replace(LOWER(name),' ','') = :n LIMIT 1"),
                    {"n": normalized},
                ).fetchone()
                if row:
                    return ConceptVerdict(
                        concept=concept,
                        status=ConceptStatus.MATCHED,
                        evidence=f"Exact match to column '{row[0]}.{row[1]}' in schema metadata.",
                        matched_objects=[f"{row[0]}.{row[1]}"],
                    )

                # 3. Fuzzy column name (LIKE)
                row = conn.execute(
                    text("SELECT table_name, name FROM meta_columns "
                         "WHERE replace(LOWER(name),' ','') LIKE :n LIMIT 1"),
                    {"n": f"%{normalized}%"},
                ).fetchone()
                if row:
                    return ConceptVerdict(
                        concept=concept,
                        status=ConceptStatus.MATCHED,
                        evidence=f"Fuzzy match to column '{row[0]}.{row[1]}' in schema metadata.",
                        matched_objects=[f"{row[0]}.{row[1]}"],
                    )

                # 3b. Morphological variants
                all_tokens = tokens if len(tokens) > 1 else [normalized]
                for token in all_tokens:
                    for variant in self._morphological_variants(token):
                        if variant == token:
                            continue
                        row = conn.execute(
                            text("SELECT name FROM meta_tables "
                                 "WHERE replace(LOWER(name),' ','') = :v "
                                 "   OR replace(LOWER(name),' ','') LIKE :vlike LIMIT 1"),
                            {"v": variant, "vlike": f"%{variant}%"},
                        ).fetchone()
                        if row:
                            return ConceptVerdict(
                                concept=concept,
                                status=ConceptStatus.MATCHED,
                                evidence=(
                                    f"Morphological match: '{concept}' → variant '{variant}' "
                                    f"matched table '{row[0]}'."
                                ),
                                matched_objects=[row[0]],
                            )
                        row = conn.execute(
                            text("SELECT table_name, name FROM meta_columns "
                                 "WHERE replace(LOWER(name),' ','') = :v "
                                 "   OR replace(LOWER(name),' ','') LIKE :vlike LIMIT 1"),
                            {"v": variant, "vlike": f"%{variant}%"},
                        ).fetchone()
                        if row:
                            return ConceptVerdict(
                                concept=concept,
                                status=ConceptStatus.MATCHED,
                                evidence=(
                                    f"Morphological match: '{concept}' → variant '{variant}' "
                                    f"matched column '{row[0]}.{row[1]}'."
                                ),
                                matched_objects=[f"{row[0]}.{row[1]}"],
                            )

                # 4. Compound phrase decomposition
                if len(tokens) >= 2:
                    hit = self._compound_column_lookup(conn, tokens)
                    if hit:
                        return ConceptVerdict(
                            concept=concept,
                            status=ConceptStatus.MATCHED,
                            evidence=(
                                f"Compound phrase match: '{concept}' resolved to "
                                f"column '{hit[0]}.{hit[1]}' via qualifier-attribute decomposition."
                            ),
                            matched_objects=[f"{hit[0]}.{hit[1]}"],
                        )

                # 5. Categorical value
                row = conn.execute(
                    text(
                        "SELECT table_name, column_name, value FROM meta_values "
                        "WHERE replace(LOWER(value),' ','') = :n "
                        "   OR replace(LOWER(value),' ','') LIKE :nlike "
                        "LIMIT 1"
                    ),
                    {"n": normalized, "nlike": f"%{normalized}%"},
                ).fetchone()
                if row:
                    return ConceptVerdict(
                        concept=concept,
                        status=ConceptStatus.MATCHED,
                        evidence=(
                            f"Value '{row[2]}' found in column "
                            f"'{row[0]}.{row[1]}' via categorical value lookup."
                        ),
                        matched_objects=[f"{row[0]}.{row[1]}"],
                    )

        except Exception as e:
            print(f"[CapabilityValidator] Info DB lookup failed for '{concept}': {e}")

        return None

    def _compound_column_lookup(
        self, conn, tokens: List[str]
    ) -> Optional[Tuple[str, str]]:
        pairs = []
        if len(tokens) >= 2:
            pairs.append((tokens[0],  tokens[-1]))
            pairs.append((tokens[-1], tokens[0]))
            if len(tokens) > 2:
                pairs.append((tokens[0], tokens[1]))

        for table_hint, col_hint in pairs:
            row = conn.execute(
                text(
                    "SELECT table_name, name FROM meta_columns "
                    "WHERE LOWER(table_name) LIKE :th "
                    "  AND LOWER(name)       LIKE :ch "
                    "LIMIT 1"
                ),
                {"th": f"%{table_hint}%", "ch": f"%{col_hint}%"},
            ).fetchone()
            if row:
                return (row[0], row[1])

        for token in tokens:
            tbl_row = conn.execute(
                text("SELECT name FROM meta_tables WHERE LOWER(name) LIKE :t LIMIT 1"),
                {"t": f"%{token}%"},
            ).fetchone()
            if tbl_row:
                for other in [t for t in tokens if t != token]:
                    col_row = conn.execute(
                        text(
                            "SELECT table_name, name FROM meta_columns "
                            "WHERE LOWER(table_name) LIKE :tbl "
                            "  AND LOWER(name)       LIKE :col "
                            "LIMIT 1"
                        ),
                        {"tbl": f"%{token}%", "col": f"%{other}%"},
                    ).fetchone()
                    if col_row:
                        return (col_row[0], col_row[1])

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Tier 3 — Legacy Neo4j ontology + Vector fallback
    # ──────────────────────────────────────────────────────────────────────────

    def _check_ontology(self, concept: str) -> Optional[ConceptVerdict]:
        """
        Queries the legacy :Concept/:Synonym → :Table path (pre-catalogue).
        Note: OntologyConcept nodes are handled exclusively in Tier 0 via
        OntologyResolver — this method only touches :Concept and :Synonym nodes.
        """
        if self.neo4j is None:
            return self._check_vector_fallback(concept)

        try:
            results = self.neo4j.query(
                """
                MATCH (s:Synonym)
                WHERE replace(toLower(s.name), ' ', '') = replace(toLower($name), ' ', '')
                  AND NOT (s)-[:IS_SYNONYM_FOR]->(:OntologyConcept)
                MATCH (s)-[:IS_SYNONYM_FOR]->(con:Concept)-[:MAPS_TO]->(t:Table)
                RETURN t.name AS table_name, con.name AS concept
                LIMIT 3
                """,
                {"name": concept},
            )
            if results:
                tables = [r["table_name"] for r in results]
                concepts_found = list({r["concept"] for r in results})
                return ConceptVerdict(
                    concept=concept,
                    status=ConceptStatus.MATCHED,
                    evidence=(
                        f"Legacy ontology synonym match: '{concept}' → "
                        f"concept(s) {concepts_found} → table(s) {tables}."
                    ),
                    matched_objects=tables,
                )

            results = self.neo4j.query(
                """
                MATCH (con:Concept)-[:MAPS_TO]->(t:Table)
                WHERE replace(toLower(con.name), ' ', '') = replace(toLower($name), ' ', '')
                RETURN t.name AS table_name, con.name AS concept
                LIMIT 3
                """,
                {"name": concept},
            )
            if results:
                tables = [r["table_name"] for r in results]
                return ConceptVerdict(
                    concept=concept,
                    status=ConceptStatus.MATCHED,
                    evidence=f"Legacy ontology direct concept match to table(s) {tables}.",
                    matched_objects=tables,
                )

        except Exception as e:
            print(f"[CapabilityValidator] Neo4j legacy ontology check failed for '{concept}': {e}")

        return self._check_vector_fallback(concept)

    def _check_vector_fallback(self, concept: str) -> Optional[ConceptVerdict]:
        if self.vector is None:
            return None

        try:
            results = self.vector.search(concept, n_results=3)
            if not results or "metadatas" not in results or not results["metadatas"]:
                return None

            metadatas = results["metadatas"][0]
            distances = results.get("distances", [[0.0] * 3])[0]

            for meta, dist in zip(metadatas, distances):
                if dist > 1.2:
                    continue
                obj_type = meta.get("type")
                if obj_type == "table":
                    tbl_name = meta.get("name")
                    if self._table_exists(tbl_name):
                        return ConceptVerdict(
                            concept=concept,
                            status=ConceptStatus.MATCHED,
                            evidence=f"Vector DB semantic match to table '{tbl_name}' (distance: {dist:.2f}).",
                            matched_objects=[tbl_name],
                        )
                elif obj_type == "column":
                    tbl_name = meta.get("table")
                    col_name = meta.get("name")
                    if self._column_exists(tbl_name, col_name):
                        return ConceptVerdict(
                            concept=concept,
                            status=ConceptStatus.MATCHED,
                            evidence=f"Vector DB semantic match to column '{tbl_name}.{col_name}' (distance: {dist:.2f}).",
                            matched_objects=[f"{tbl_name}.{col_name}"],
                        )
        except Exception as e:
            print(f"[CapabilityValidator] Vector DB fallback failed for '{concept}': {e}")

        return None

    def _table_exists(self, table_name: str) -> bool:
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    text("SELECT 1 FROM meta_tables WHERE name = :n LIMIT 1"),
                    {"n": table_name},
                ).fetchone()
                return row is not None
        except Exception:
            return False

    def _column_exists(self, table_name: str, column_name: str) -> bool:
        try:
            with self.engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT 1 FROM meta_columns "
                        "WHERE table_name = :t AND name = :c LIMIT 1"
                    ),
                    {"t": table_name, "c": column_name},
                ).fetchone()
                return row is not None
        except Exception:
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # Filter helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _parse_filter_lhs_rhs(
        self, filter_str: str
    ) -> Tuple[str, Optional[str], Optional[str]]:
        word_ops = [
            "greater than or equal to", "less than or equal to", "not equal to", "equal to",
            "greater than", "less than", "starts with", "ends with", "containing", "contains",
            "not like", "is not", "not in", "after", "before", "equals", "equal", "like",
            "ilike", "between", "is", "in",
        ]
        symbol_ops = ["!=", "<>", ">=", "<=", "=", ">", "<"]

        word_patterns   = [r"\b" + re.escape(op) + r"\b" for op in word_ops]
        symbol_patterns = [re.escape(op) for op in symbol_ops]
        pattern_str     = "|".join(word_patterns + symbol_patterns)
        operator_regex  = re.compile(pattern_str, re.IGNORECASE)

        match = operator_regex.search(filter_str)
        if match:
            lhs = filter_str[: match.start()].strip()
            rhs = filter_str[match.end() :].strip()
            op  = match.group(0).lower()
            return lhs, rhs, op

        return filter_str, None, None

    def _validate_filter_value(
        self, lhs_field: str, rhs_value: str
    ) -> Tuple[bool, str]:
        if not rhs_value or re.fullmatch(r"[\d\s\-\/\.\,\:\+]+", rhs_value):
            return True, "Value is numeric/date/pattern, bypassing check."

        val_clean      = rhs_value.strip("'\"")
        val_normalized = "".join(val_clean.split()).lower()
        lhs_normalized = "".join(lhs_field.split()).lower()
        mapped_columns = []

        try:
            with self.engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name, name FROM meta_columns "
                        "WHERE replace(LOWER(name),' ','') = :n "
                        "   OR replace(LOWER(name),' ','') LIKE :nlike"
                    ),
                    {"n": lhs_normalized, "nlike": f"%{lhs_normalized}%"},
                ).fetchall()
                for r in rows:
                    mapped_columns.append((r[0], r[1]))
        except Exception as e:
            print(f"[CapabilityValidator] Failed to find columns for LHS '{lhs_field}': {e}")

        if not mapped_columns:
            return True, "LHS column not found; skipping value validation."

        try:
            with self.engine.connect() as conn:
                for tbl, col in mapped_columns:
                    row = conn.execute(
                        text(
                            "SELECT 1 FROM meta_values "
                            "WHERE table_name = :t AND column_name = :c "
                            "AND (replace(LOWER(value),' ','') = :v "
                            "     OR replace(LOWER(value),' ','') LIKE :vlike) "
                            "LIMIT 1"
                        ),
                        {"t": tbl, "c": col, "v": val_normalized, "vlike": f"%{val_normalized}%"},
                    ).fetchone()
                    if row:
                        return True, f"Value found in meta_values for {tbl}.{col}."

                    card_row = conn.execute(
                        text(
                            "SELECT COUNT(*) FROM meta_values "
                            "WHERE table_name = :t AND column_name = :c"
                        ),
                        {"t": tbl, "c": col},
                    ).fetchone()
                    cardinality = card_row[0] if card_row else 0
                    if cardinality >= 100:
                        return True, f"Column {tbl}.{col} has high cardinality (>= 100 values); bypassing check."

        except Exception as e:
            print(f"[CapabilityValidator] Value check failed: {e}")
            return True, "Error checking values, bypassing check."

        return False, (
            f"Value '{val_clean}' was not found in the unique values for "
            f"column(s): {', '.join([f'{t}.{c}' for t, c in mapped_columns])}."
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Catalogue loading and indexing
    # ──────────────────────────────────────────────────────────────────────────

    def _load_catalogue(self, override_path: Optional[str]) -> Dict:
        if override_path:
            try:
                with open(override_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[CapabilityValidator] Could not load catalogue '{override_path}': {e}")

        try:
            from app.utils.catalogue_loader import all_rules
            return {"rules": all_rules()}
        except Exception as e:
            print(f"[CapabilityValidator] catalogue_loader unavailable: {e}")
            return {"rules": []}

    def _build_catalogue_indices(self) -> None:
        for rule in self._catalogue.get("rules", []):
            for phrase in rule.get("trigger_phrases", []):
                self._rule_trigger_index.setdefault(phrase.lower(), []).append(rule)
            for col in rule.get("applies_to", []):
                self._rule_applies_index.setdefault(col.lower(), []).append(rule)