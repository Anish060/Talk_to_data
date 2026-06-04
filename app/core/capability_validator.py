"""
capability_validator.py
-----------------------
Database Capability Validation Layer

Sits between Intent Extraction and SQL Generation. Its job is to answer:
  "Can this database satisfy this query?"

The distinction being made:
  - UNSUPPORTED CONCEPT   → a table, column, or domain term that doesn't exist
                            in the current database at all (e.g. "bike color" in Northwind).
  - SUPPORTED DERIVED METRIC → a calculable metric whose inputs (raw columns) DO exist
                               in the current DB, even if the metric name itself isn't a column
                               (e.g. "discounted revenue" in Northwind uses Quantity, UnitPrice,
                               Discount which are all present).

The validator uses three evidence tiers in order of decreasing specificity:

  Tier 1 – Catalogue Rule Match
      If a metric/phrase matches a catalogue rule AND all of that rule's
      `applies_to` columns exist in the current DB → DERIVABLE.

  Tier 2 – Schema Metadata Match (Info DB)
      Exact or fuzzy match against table names, column names, and
      sampled categorical values in meta_tables / meta_columns / meta_values.

  Tier 3 – Ontology / Synonym Match (Neo4j)
      Concept or synonym nodes that map to a known table → MATCHED.

Each concept in the extracted intent receives a ConceptVerdict:
  - DERIVABLE   : supported as a computed metric from existing columns
  - MATCHED     : found directly in schema / ontology
  - UNRESOLVABLE: not found anywhere → candidate for rejection

A query is rejected only when it contains at least one UNRESOLVABLE concept
AND the system cannot find any plausible derivation path for it.
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
    MATCHED = "matched"          # found directly in schema or ontology
    DERIVABLE = "derivable"      # computable from existing columns via catalogue rule
    UNRESOLVABLE = "unresolvable"  # not found anywhere — cross-DB leak candidate


@dataclass
class ConceptVerdict:
    concept: str
    status: ConceptStatus
    evidence: str                # human-readable explanation of *why*
    rule_id: Optional[str] = None        # populated when status == DERIVABLE
    matched_objects: List[str] = field(default_factory=list)  # table/column names


@dataclass
class ValidationResult:
    accepted: bool
    verdicts: List[ConceptVerdict]
    rejection_reason: Optional[str] = None  # only set when accepted == False

    # Convenience helpers
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
    """
    Usage
    -----
    validator = CapabilityValidator(neo4j_client, info_db_path="data/info.db")
    result = validator.validate(intent)

    if not result.accepted:
        raise HTTPException(400, result.rejection_reason)
    """

    # Generic "computation" verbs/adjectives that should never be treated as
    # schema concepts — they describe *how* to aggregate, not *what* to query.
    _AGGREGATION_STOPWORDS = {
        "average", "avg", "total", "sum", "count", "number", "top", "bottom",
        "highest", "lowest", "most", "least", "best", "worst", "per", "by",
        "rate", "ratio", "percentage", "percent", "contribution", "share",
        "distribution", "breakdown", "trend", "over time", "year", "month",
        "quarter", "week", "day", "rank", "ranked", "sorted", "ordered",
    }

    def __init__(
        self,
        neo4j_client,                     # Neo4jClient instance (may be None)
        info_db_path: str = "data/info.db",
        catalogue_path: Optional[str] = None,  # override; else uses catalogue_loader
    ):
        self.neo4j = neo4j_client
        self.engine = create_engine(f"sqlite:///{info_db_path}")
        self._catalogue = self._load_catalogue(catalogue_path)
        # Pre-build lookup structures from the catalogue for O(1) access
        self._rule_trigger_index: Dict[str, List[Dict]] = {}   # trigger_phrase -> [rules]
        self._rule_applies_index: Dict[str, List[Dict]] = {}   # "Table.Column" -> [rules]
        self._build_catalogue_indices()

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    def validate(self, intent: Dict[str, Any]) -> ValidationResult:
        """
        Main entry point. Returns a ValidationResult describing whether the
        query is executable against the current database.
        """
        concepts = self._extract_concepts(intent)
        if not concepts:
            # No extractable concepts → pass through (let relevance check handle it)
            return ValidationResult(accepted=True, verdicts=[])

        verdicts: List[ConceptVerdict] = []
        for concept in concepts:
            verdict = self._classify_concept(concept, intent)
            verdicts.append(verdict)

        unresolvable = [v for v in verdicts if v.status == ConceptStatus.UNRESOLVABLE]

        if unresolvable:
            names = [v.concept for v in unresolvable]
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
        """Return a multi-line human-readable breakdown of all verdicts."""
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
    # Concept extraction
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_concepts(self, intent: Dict[str, Any]) -> List[str]:
        """
        Pull all candidate concepts from the intent dict, deduplicate, and
        filter out pure aggregation stopwords that carry no schema meaning.
        """
        raw: List[str] = []
        for key in ("entities", "metrics", "filters", "dimensions"):
            val = intent.get(key, [])
            if isinstance(val, list):
                raw.extend([str(v).strip() for v in val if v])
            elif isinstance(val, str) and val.strip():
                raw.append(val.strip())

        seen: set = set()
        cleaned: List[str] = []
        for c in raw:
            c_lower = c.lower()
            # Skip pure stopwords
            if c_lower in self._AGGREGATION_STOPWORDS:
                continue
            # Skip numeric-only strings (e.g. "10", "2023")
            if re.fullmatch(r"[\d\s\-/]+", c):
                continue
            if c_lower not in seen:
                seen.add(c_lower)
                cleaned.append(c)

        return cleaned

    # ──────────────────────────────────────────────────────────────────────────
    # Classification pipeline
    # ──────────────────────────────────────────────────────────────────────────

    def _classify_concept(self, concept: str, intent: Dict[str, Any]) -> ConceptVerdict:
        """
        Run through the three tiers and return the first positive match.
        """

        # ── Tier 1: Catalogue derivability check ─────────────────────────────
        tier1 = self._check_catalogue_derivability(concept, intent)
        if tier1 is not None:
            return tier1

        # ── Tier 2: Schema metadata (Info DB) ────────────────────────────────
        tier2 = self._check_schema_metadata(concept)
        if tier2 is not None:
            return tier2

        # ── Tier 3: Ontology / synonym (Neo4j) ───────────────────────────────
        tier3 = self._check_ontology(concept)
        if tier3 is not None:
            return tier3

        # All tiers missed → unresolvable
        return ConceptVerdict(
            concept=concept,
            status=ConceptStatus.UNRESOLVABLE,
            evidence=(
                f"No match found in catalogue rules, schema metadata "
                f"(tables/columns/values), or ontology synonyms."
            ),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Tier 1 — Catalogue derivability
    # ──────────────────────────────────────────────────────────────────────────

    def _check_catalogue_derivability(
        self, concept: str, intent: Dict[str, Any]
    ) -> Optional[ConceptVerdict]:
        """
        Returns DERIVABLE if:
          a) concept matches a rule's trigger_phrases (case-insensitive substring)
          AND
          b) at least one `applies_to` column from that rule exists in the DB.

        This is the key gate: "discounted revenue" matches rule NUM_008 whose
        applies_to columns (Quantity, UnitPrice, Discount) exist in Northwind
        Order Details → DERIVABLE. But "profit margin" matches a rule whose
        applies_to columns (StandardCost, LineTotal) don't exist in Northwind
        → falls through to Tier 2/3.
        """
        concept_lower = concept.lower()

        # Gather candidate rules by trigger phrase match
        candidate_rules: List[Dict] = []
        for phrase, rules in self._rule_trigger_index.items():
            if phrase in concept_lower or concept_lower in phrase:
                candidate_rules.extend(rules)

        # Deduplicate by rule id
        seen_ids: set = set()
        unique_rules: List[Dict] = []
        for r in candidate_rules:
            rid = r.get("id")
            if rid not in seen_ids:
                seen_ids.add(rid)
                unique_rules.append(r)

        for rule in unique_rules:
            applies_to: List[str] = rule.get("applies_to", [])

            # Special sentinel values that aren't real columns — always pass
            sentinel_prefixes = ("all_", "any_")
            real_columns = [
                col for col in applies_to
                if not any(col.lower().startswith(p) for p in sentinel_prefixes)
            ]

            if not real_columns:
                # Rule uses only sentinels → treat as universally applicable
                return ConceptVerdict(
                    concept=concept,
                    status=ConceptStatus.DERIVABLE,
                    evidence=f"Matched catalogue rule '{rule['id']}' (universal sentinel columns).",
                    rule_id=rule["id"],
                    matched_objects=applies_to,
                )

            # Check how many of the rule's columns actually exist in this DB
            existing = self._columns_exist_in_db(real_columns)
            
            # For metric_formula rules, all columns must exist. For others, at least one is required.
            is_applicable = False
            if rule.get("type") == "metric_formula":
                is_applicable = (len(existing) == len(real_columns))
            else:
                is_applicable = (len(existing) > 0)

            if is_applicable:
                return ConceptVerdict(
                    concept=concept,
                    status=ConceptStatus.DERIVABLE,
                    evidence=(
                        f"Matched catalogue rule '{rule['id']}'; "
                        f"required columns found in DB: {', '.join(existing)}."
                    ),
                    rule_id=rule["id"],
                    matched_objects=existing,
                )

        return None

    def _columns_exist_in_db(self, qualified_columns: List[str]) -> List[str]:
        """
        Given a list of "Table.Column" strings, return those that actually
        exist in meta_columns. Matching is case-insensitive.
        """
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
                            text(
                                "SELECT 1 FROM meta_columns WHERE LOWER(name)=LOWER(:c) LIMIT 1"
                            ),
                            {"c": col},
                        ).fetchone()

                    if row:
                        found.append(qcol)
        except Exception as e:
            print(f"[CapabilityValidator] Column existence check failed: {e}")
        return found

    # ──────────────────────────────────────────────────────────────────────────
    # Tier 2 — Schema metadata (Info DB)
    # ──────────────────────────────────────────────────────────────────────────

    def _check_schema_metadata(self, concept: str) -> Optional[ConceptVerdict]:
        """
        Check meta_tables, meta_columns, and meta_values for the concept.
        Uses both exact match and LIKE fuzzy match.
        """
        normalized = "".join(concept.split()).lower()

        try:
            with self.engine.connect() as conn:
                # 1. Table name match
                row = conn.execute(
                    text(
                        "SELECT name FROM meta_tables "
                        "WHERE replace(LOWER(name),' ','') = :n LIMIT 1"
                    ),
                    {"n": normalized},
                ).fetchone()
                if row:
                    return ConceptVerdict(
                        concept=concept,
                        status=ConceptStatus.MATCHED,
                        evidence=f"Exact match to table '{row[0]}' in schema metadata.",
                        matched_objects=[row[0]],
                    )

                # 2. Column name match (exact)
                row = conn.execute(
                    text(
                        "SELECT table_name, name FROM meta_columns "
                        "WHERE replace(LOWER(name),' ','') = :n LIMIT 1"
                    ),
                    {"n": normalized},
                ).fetchone()
                if row:
                    return ConceptVerdict(
                        concept=concept,
                        status=ConceptStatus.MATCHED,
                        evidence=f"Exact match to column '{row[0]}.{row[1]}' in schema metadata.",
                        matched_objects=[f"{row[0]}.{row[1]}"],
                    )

                # 3. Column name fuzzy (LIKE)
                row = conn.execute(
                    text(
                        "SELECT table_name, name FROM meta_columns "
                        "WHERE replace(LOWER(name),' ','') LIKE :n LIMIT 1"
                    ),
                    {"n": f"%{normalized}%"},
                ).fetchone()
                if row:
                    return ConceptVerdict(
                        concept=concept,
                        status=ConceptStatus.MATCHED,
                        evidence=f"Fuzzy match to column '{row[0]}.{row[1]}' in schema metadata.",
                        matched_objects=[f"{row[0]}.{row[1]}"],
                    )

                # 4. Categorical value match (meta_values)
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

    # ──────────────────────────────────────────────────────────────────────────
    # Tier 3 — Ontology / Neo4j synonyms
    # ──────────────────────────────────────────────────────────────────────────

    def _check_ontology(self, concept: str) -> Optional[ConceptVerdict]:
        """
        Check Neo4j for Synonym or Concept nodes that map to a known Table.
        Gracefully skips if Neo4j is unavailable.
        """
        if self.neo4j is None:
            return None

        try:
            results = self.neo4j.query(
                """
                MATCH (s:Synonym)
                WHERE replace(toLower(s.name), ' ', '') = replace(toLower($name), ' ', '')
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
                        f"Ontology synonym match: '{concept}' → "
                        f"concept(s) {concepts_found} → table(s) {tables}."
                    ),
                    matched_objects=tables,
                )

            # Also try direct Concept node match
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
                    evidence=f"Direct ontology concept match to table(s) {tables}.",
                    matched_objects=tables,
                )

        except Exception as e:
            print(f"[CapabilityValidator] Neo4j ontology check failed for '{concept}': {e}")

        return None

    # ──────────────────────────────────────────────────────────────────────────
    # Catalogue loading and indexing
    # ──────────────────────────────────────────────────────────────────────────

    def _load_catalogue(self, override_path: Optional[str]) -> Dict:
        """Load the field catalogue JSON. Falls back to catalogue_loader."""
        if override_path:
            try:
                with open(override_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[CapabilityValidator] Could not load catalogue override '{override_path}': {e}")

        try:
            from app.utils.catalogue_loader import all_rules, get_default
            rules = all_rules()
            return {"rules": rules}
        except Exception as e:
            print(f"[CapabilityValidator] catalogue_loader unavailable: {e}")
            return {"rules": []}

    def _build_catalogue_indices(self) -> None:
        """
        Pre-compute two lookup dicts from the catalogue for fast classification:
          - _rule_trigger_index  : phrase (lower) -> list of rules
          - _rule_applies_index  : "Table.Column" (lower) -> list of rules
        """
        for rule in self._catalogue.get("rules", []):
            for phrase in rule.get("trigger_phrases", []):
                key = phrase.lower()
                self._rule_trigger_index.setdefault(key, []).append(rule)

            for col in rule.get("applies_to", []):
                key = col.lower()
                self._rule_applies_index.setdefault(key, []).append(rule)