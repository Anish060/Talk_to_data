"""
ontology_resolver.py
--------------------
Resolves natural-language concepts to schema objects by querying the
OntologyConcept nodes ingested from ontology_catalogue.json.

Resolution contract
───────────────────
Given a concept string (e.g. "customer name") this module returns:
  - target table name(s)
  - target column name(s) with combination type and ordinal order
  - an optional pre-built SQL expression (concat / metric types)
  - confidence score and match type

Called as Tier 0 inside CapabilityValidator._classify_concept() and as the
primary resolver inside ContextRetriever._resolve_ontology(), so high-confidence
catalogue mappings are authoritative and suppress all downstream fuzzy matching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.db.neo4j_client import Neo4jClient


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OntologyResolution:
    concept: str
    match_type: str                     # "exact" | "synonym" | "legacy" | "none"
    confidence: float = 0.0
    priority: int = 99
    ontology_concept_name: str = ""
    ontology_concept_type: str = ""     # entity | attribute_alias | metric_alias | relationship
    tables: List[str] = field(default_factory=list)
    columns: List[Dict[str, Any]] = field(default_factory=list)
    # Each column dict: {table, name, ordinal, combination}
    sql_expression: Optional[str] = None
    combination: str = "single"         # single | concat | coalesce | context_dependent
    resolution_hint: Optional[str] = None

    @property
    def resolved(self) -> bool:
        return self.match_type != "none"

    @property
    def is_authoritative(self) -> bool:
        """True when confidence is high enough to suppress schema fuzzy matching."""
        return self.confidence >= 0.9

    def to_sql_fragment(self, table_alias: Optional[str] = None) -> Optional[str]:
        """
        Return a SQL SELECT fragment for this resolution.

        Uses the stored sql_expression when present (metric / concat types).
        For single-column mappings returns a qualified column reference.
        Returns None when the caller should fall back to schema matching.
        """
        if self.sql_expression:
            return self.sql_expression

        if not self.columns:
            return None

        combo = self.combination
        cols = sorted(self.columns, key=lambda c: c.get("ordinal", 0))

        def qualified(c: Dict) -> str:
            tbl = table_alias if table_alias else f'"{c["table"]}"'
            return f'{tbl}."{c["name"]}"'

        if combo == "single":
            return qualified(cols[0])
        elif combo == "concat":
            parts = [qualified(c) for c in cols]
            return " || ' ' || ".join(parts)
        elif combo == "coalesce":
            parts = ", ".join(qualified(c) for c in cols)
            return f"COALESCE({parts})"
        else:
            # context_dependent — return first column; planner uses resolution_hint
            return qualified(cols[0])

    def to_grounding_string(self) -> str:
        """Human-readable grounding mapping for inclusion in the LLM context."""
        if self.sql_expression:
            return (
                f"Metric Alias [{self.match_type}]: '{self.concept}' "
                f"→ SQL Expression: {self.sql_expression}"
            )
        if self.columns:
            col_strs = [f"{c['table']}.{c['name']}" for c in self.columns]
            frag = self.to_sql_fragment()
            if self.combination == "concat":
                return (
                    f"Column Alias [{self.match_type}]: '{self.concept}' "
                    f"→ CONCAT: {frag}  (columns: {', '.join(col_strs)})"
                )
            elif self.combination == "context_dependent":
                hint = f"  HINT: {self.resolution_hint}" if self.resolution_hint else ""
                return (
                    f"Column Alias [{self.match_type}]: '{self.concept}' "
                    f"→ Context-dependent columns: {', '.join(col_strs)}.{hint}"
                )
            else:
                return (
                    f"Column Alias [{self.match_type}]: '{self.concept}' "
                    f"→ Exact Column(s): {', '.join(col_strs)}"
                )
        if self.tables:
            return (
                f"Entity Alias [{self.match_type}]: '{self.concept}' "
                f"→ Table(s): {', '.join(self.tables)}"
            )
        return f"Ontology [{self.match_type}]: '{self.concept}' resolved (no column/table details)"


# ─────────────────────────────────────────────────────────────────────────────
# Resolver
# ─────────────────────────────────────────────────────────────────────────────

class OntologyResolver:
    """
    Resolves a concept string against OntologyConcept nodes in Neo4j.

    Lookup order:
      1. Exact OntologyConcept.name match
      2. Synonym → IS_SYNONYM_FOR → OntologyConcept
      3. Legacy Concept/Synonym nodes (entity-level fallback, pre-catalogue path)
    """

    def __init__(self, neo4j_client: Neo4jClient):
        self.neo4j = neo4j_client

    # ── Public API ────────────────────────────────────────────────────────────

    def resolve(self, concept: str) -> OntologyResolution:
        """Resolve a single concept. Returns match_type='none' when unresolved."""
        if not self.neo4j or not self.neo4j.driver:
            return OntologyResolution(concept=concept, match_type="none")

        result = self._query_exact(concept)
        if result:
            return result

        result = self._query_synonym(concept)
        if result:
            return result

        result = self._query_legacy_ontology(concept)
        if result:
            return result

        return OntologyResolution(concept=concept, match_type="none")

    def resolve_batch(self, concepts: List[str]) -> Dict[str, OntologyResolution]:
        """Resolve multiple concepts efficiently, returns dict keyed by concept."""
        return {c: self.resolve(c) for c in concepts}

    # ── Cypher queries ────────────────────────────────────────────────────────

    def _query_exact(self, concept: str) -> Optional[OntologyResolution]:
        try:
            results = self.neo4j.query(
                """
                MATCH (oc:OntologyConcept)
                WHERE replace(toLower(oc.name), ' ', '') = replace(toLower($name), ' ', '')
                OPTIONAL MATCH (oc)-[rt:MAPS_TO_TABLE]->(t:Table)
                OPTIONAL MATCH (oc)-[rc:MAPS_TO_COLUMN]->(col:Column)
                OPTIONAL MATCH (col)<-[:HAS_COLUMN]-(ct:Table)
                RETURN oc, rt, t, rc, col, ct
                ORDER BY oc.priority ASC
                LIMIT 20
                """,
                {"name": concept},
            )
        except Exception as e:
            print(f"[OntologyResolver] exact query failed for '{concept}': {e}")
            return None
        return self._build_resolution(concept, results, "exact") if results else None

    def _query_synonym(self, concept: str) -> Optional[OntologyResolution]:
        try:
            results = self.neo4j.query(
                """
                MATCH (s:Synonym)-[:IS_SYNONYM_FOR]->(oc:OntologyConcept)
                WHERE replace(toLower(s.name), ' ', '') = replace(toLower($name), ' ', '')
                OPTIONAL MATCH (oc)-[rt:MAPS_TO_TABLE]->(t:Table)
                OPTIONAL MATCH (oc)-[rc:MAPS_TO_COLUMN]->(col:Column)
                OPTIONAL MATCH (col)<-[:HAS_COLUMN]-(ct:Table)
                RETURN oc, rt, t, rc, col, ct
                ORDER BY oc.priority ASC
                LIMIT 20
                """,
                {"name": concept},
            )
        except Exception as e:
            print(f"[OntologyResolver] synonym query failed for '{concept}': {e}")
            return None
        return self._build_resolution(concept, results, "synonym") if results else None

    def _query_legacy_ontology(self, concept: str) -> Optional[OntologyResolution]:
        """
        Fallback to the pre-catalogue Concept/Synonym → Table path.
        Returns an entity-level resolution only (no column detail).
        """
        try:
            results = self.neo4j.query(
                """
                MATCH (s:Synonym)-[:IS_SYNONYM_FOR]->(con:Concept)-[:MAPS_TO]->(t:Table)
                WHERE replace(toLower(s.name), ' ', '') = replace(toLower($name), ' ', '')
                RETURN t.name AS table_name
                LIMIT 3
                """,
                {"name": concept},
            )
            if not results:
                results = self.neo4j.query(
                    """
                    MATCH (con:Concept)-[:MAPS_TO]->(t:Table)
                    WHERE replace(toLower(con.name), ' ', '') = replace(toLower($name), ' ', '')
                    RETURN t.name AS table_name
                    LIMIT 3
                    """,
                    {"name": concept},
                )
        except Exception as e:
            print(f"[OntologyResolver] legacy query failed for '{concept}': {e}")
            return None

        if results:
            tables = [r["table_name"] for r in results if r.get("table_name")]
            if tables:
                return OntologyResolution(
                    concept=concept,
                    match_type="legacy",
                    confidence=0.85,
                    priority=3,
                    ontology_concept_type="entity",
                    tables=tables,
                )
        return None

    # ── Result assembly ───────────────────────────────────────────────────────

    def _build_resolution(
        self,
        concept: str,
        records: List[Any],
        match_type: str,
    ) -> Optional[OntologyResolution]:
        if not records:
            return None

        # Extract OntologyConcept properties from first record
        first    = records[0]
        oc_node  = first.get("oc")
        if oc_node is None:
            return None

        # Neo4j driver returns nodes as dict-like objects
        try:
            oc_props = dict(oc_node)
        except Exception:
            oc_props = {}

        resolution = OntologyResolution(
            concept=concept,
            match_type=match_type,
            confidence=float(oc_props.get("confidence", 1.0)),
            priority=int(oc_props.get("priority", 1)),
            ontology_concept_name=oc_props.get("name", ""),
            ontology_concept_type=oc_props.get("type", "entity"),
            sql_expression=oc_props.get("sql_expression"),
            combination=oc_props.get("combination", "single"),
            resolution_hint=oc_props.get("resolution_hint"),
        )

        seen_tables: set = set()
        seen_cols:   set = set()

        for row in records:
            # Collect table nodes (from MAPS_TO_TABLE relationships)
            t = row.get("t")
            if t:
                try:
                    tbl_name = dict(t).get("name")
                except Exception:
                    tbl_name = None
                if tbl_name and tbl_name not in seen_tables:
                    seen_tables.add(tbl_name)
                    resolution.tables.append(tbl_name)

            # Collect column nodes (from MAPS_TO_COLUMN relationships)
            col = row.get("col")
            rc  = row.get("rc")   # relationship props
            ct  = row.get("ct")   # parent table of the column
            if col:
                try:
                    col_props = dict(col)
                    rc_props  = dict(rc) if rc else {}
                    ct_props  = dict(ct) if ct else {}
                except Exception:
                    continue

                col_key = (ct_props.get("name", ""), col_props.get("name", ""))
                if col_key not in seen_cols:
                    seen_cols.add(col_key)
                    resolution.columns.append({
                        "table":       ct_props.get("name", ""),
                        "name":        col_props.get("name", ""),
                        "ordinal":     int(rc_props.get("ordinal", 0)),
                        "combination": rc_props.get("combination", resolution.combination),
                    })
                    # Also register the column's parent table
                    parent_tbl = ct_props.get("name", "")
                    if parent_tbl and parent_tbl not in seen_tables:
                        seen_tables.add(parent_tbl)
                        resolution.tables.append(parent_tbl)

        if not resolution.tables and not resolution.columns:
            return None

        return resolution
