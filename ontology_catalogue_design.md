# Ontology Catalogue Enhancement — Talk to Data
## Deterministic Semantic Grounding for NL2SQL

---

## 1. Problem Analysis

Your current system has a fundamental ambiguity problem at the attribute level. The schema catalogue and capability validator resolve **entity concepts** (e.g., `customer → Customers`) correctly, but fail at **attribute disambiguation** — deciding which column among semantically similar ones actually represents the user's intent.

The Northwind example exposes this precisely:

| User says | Ambiguous candidates | Correct column |
|---|---|---|
| `customer name` | `CompanyName`, `ContactName`, `ContactTitle` | `ContactName` |
| `customer company` | `CompanyName`, `ContactName` | `CompanyName` |
| `employee name` | `FirstName`, `LastName`, `Title` | `FirstName + LastName` |
| `supplier name` | `CompanyName`, `ContactName` | `CompanyName` |

Fuzzy matching on column name strings cannot resolve this — `CompanyName` scores equally well as `ContactName` for the query "customer name". The system needs an explicit semantic authority that says: *for this database, in this domain, this phrase maps to exactly these columns*.

The proposed **Ontology Catalogue** is that authority.

---

## 2. Ontology Catalogue Structure

### 2.1 File Format: `ontology_catalogue.json`

The catalogue has four concept types, each serving a different resolution purpose:

```json
{
  "database": "Northwind",
  "version": "1.0",
  "description": "Semantic ontology mappings for Northwind trading database",
  "concepts": [
    {
      "id": "ONT_ENT_001",
      "concept": "customer",
      "type": "entity",
      "maps_to": {
        "table": "Customers"
      },
      "synonyms": ["client", "buyer", "account", "purchaser"],
      "confidence": 1.0,
      "priority": 1,
      "notes": "Primary entity for all customer queries"
    },
    {
      "id": "ONT_ATTR_001",
      "concept": "customer name",
      "type": "attribute_alias",
      "maps_to": {
        "table": "Customers",
        "columns": ["ContactName"],
        "combination": "single"
      },
      "synonyms": ["contact name", "customer contact", "person name"],
      "confidence": 1.0,
      "priority": 1,
      "notes": "The human contact at the customer account"
    },
    {
      "id": "ONT_ATTR_002",
      "concept": "customer company",
      "type": "attribute_alias",
      "maps_to": {
        "table": "Customers",
        "columns": ["CompanyName"],
        "combination": "single"
      },
      "synonyms": ["company name", "business name", "organisation name",
                   "firm name", "account name"],
      "confidence": 1.0,
      "priority": 1,
      "notes": "The legal business name of the customer"
    },
    {
      "id": "ONT_ATTR_003",
      "concept": "employee name",
      "type": "attribute_alias",
      "maps_to": {
        "table": "Employees",
        "columns": ["FirstName", "LastName"],
        "combination": "concat",
        "sql_expression": "Employees.FirstName || ' ' || Employees.LastName"
      },
      "synonyms": ["staff name", "worker name", "rep name",
                   "sales rep name", "agent name"],
      "confidence": 1.0,
      "priority": 1,
      "notes": "Full name requires concatenation of both name columns"
    },
    {
      "id": "ONT_ATTR_004",
      "concept": "supplier name",
      "type": "attribute_alias",
      "maps_to": {
        "table": "Suppliers",
        "columns": ["CompanyName"],
        "combination": "single"
      },
      "synonyms": ["vendor name", "provider name", "manufacturer name"],
      "confidence": 1.0,
      "priority": 1
    },
    {
      "id": "ONT_ATTR_005",
      "concept": "product name",
      "type": "attribute_alias",
      "maps_to": {
        "table": "Products",
        "columns": ["ProductName"],
        "combination": "single"
      },
      "synonyms": ["item name", "goods name", "article name"],
      "confidence": 1.0,
      "priority": 1
    },
    {
      "id": "ONT_ATTR_006",
      "concept": "customer location",
      "type": "attribute_alias",
      "maps_to": {
        "table": "Customers",
        "columns": ["City", "Country"],
        "combination": "context_dependent",
        "resolution_hint": "Use City when filter implies a city, Country for country-level filters"
      },
      "synonyms": ["customer address", "customer region", "where customer is"],
      "confidence": 0.8,
      "priority": 2,
      "notes": "Lower confidence because resolution depends on query context"
    },
    {
      "id": "ONT_METRIC_001",
      "concept": "order value",
      "type": "metric_alias",
      "maps_to": {
        "expression": "SUM(\"Order Details\".Quantity * \"Order Details\".UnitPrice * (1 - \"Order Details\".Discount))",
        "requires_tables": ["Order Details"],
        "label": "order_value"
      },
      "synonyms": ["order total", "order amount", "order revenue",
                   "net order value"],
      "confidence": 1.0,
      "priority": 1
    },
    {
      "id": "ONT_REL_001",
      "concept": "customer orders",
      "type": "relationship",
      "maps_to": {
        "from_table": "Customers",
        "to_table": "Orders",
        "join_path": ["Customers", "Orders"],
        "join_condition": "Customers.CustomerID = Orders.CustomerID"
      },
      "synonyms": ["orders by customer", "purchases by customer"],
      "confidence": 1.0,
      "priority": 1
    }
  ],
  "defaults": {
    "fallback_to_schema_matching": true,
    "minimum_confidence_threshold": 0.7,
    "prefer_ontology_over_vector": true
  }
}
```

### 2.2 The Four Concept Types

| Type | Purpose | Example |
|---|---|---|
| `entity` | Maps a business noun to a table | `customer → Customers` |
| `attribute_alias` | Maps a phrase to specific column(s) | `customer name → ContactName` |
| `metric_alias` | Maps a phrase to a SQL expression | `order value → SUM(Qty * Price * (1-Disc))` |
| `relationship` | Maps a relationship phrase to a join path | `customer orders → Customers JOIN Orders` |

### 2.3 Combination Types for `attribute_alias`

| Value | Meaning | SQL behaviour |
|---|---|---|
| `single` | Maps to exactly one column | `SELECT ContactName` |
| `concat` | Columns must be concatenated | `SELECT FirstName \|\| ' ' \|\| LastName` |
| `coalesce` | First non-null wins | `SELECT COALESCE(col1, col2)` |
| `context_dependent` | Planner resolves using `resolution_hint` | Passed to LLM for final decision |

### 2.4 Confidence and Priority Fields

```
confidence (float 0.0–1.0):
  1.0 = Authoritative — this IS the correct mapping, no ambiguity
  0.9 = High confidence — almost always correct
  0.7 = Medium — correct in most contexts, may need planner confirmation
  < 0.7 = Low — suggestion only, system should also run schema matching

priority (integer 1–N):
  When multiple entries match a concept (e.g. two synonyms both fire),
  lower number wins. Useful for database-specific overrides.
```

Both fields matter together. A `confidence: 1.0, priority: 1` mapping is deterministic — it completely replaces schema fuzzy matching. A `confidence: 0.7, priority: 2` mapping is advisory — it is presented to the planner as a strong hint but does not suppress schema matching.

---

## 3. Neo4j Graph Schema

### 3.1 Node Types

```cypher
// New node types added alongside existing Table, Column, Concept, Synonym

// Semantic concept node (distinct from the existing Concept used for entity mapping)
(:OntologyConcept {
  id: "ONT_ATTR_001",
  name: "customer name",         // normalised lowercase
  type: "attribute_alias",       // entity | attribute_alias | metric_alias | relationship
  confidence: 1.0,
  priority: 1,
  combination: "single",         // for attribute_alias types
  sql_expression: null,          // populated for concat/metric types
  notes: "..."
})

// Synonym node (reuses existing :Synonym pattern for consistency)
(:Synonym { name: "contact name" })

// These already exist in your graph — no changes needed:
// (:Table), (:Column), (:Concept)
```

### 3.2 Relationship Types

```cypher
// Synonym → OntologyConcept (same pattern as existing IS_SYNONYM_FOR)
(:Synonym)-[:IS_SYNONYM_FOR]->(:OntologyConcept)

// OntologyConcept → Table  (for entity and relationship types)
(:OntologyConcept)-[:MAPS_TO_TABLE {confidence: 1.0, priority: 1}]->(:Table)

// OntologyConcept → Column (for attribute_alias types)
(:OntologyConcept)-[:MAPS_TO_COLUMN {
  confidence: 1.0,
  priority: 1,
  combination: "single",
  ordinal: 0              // position when multiple columns, e.g. FirstName=0, LastName=1
}]->(:Column)

// OntologyConcept → OntologyConcept (for metric_alias referencing attribute_alias)
(:OntologyConcept)-[:DERIVED_FROM]->(:OntologyConcept)
```

### 3.3 Complete Graph Example for "customer name"

```
(Synonym "contact name")       ─[IS_SYNONYM_FOR]─►
(Synonym "person name")        ─[IS_SYNONYM_FOR]─►
(OntologyConcept "customer name") ─[MAPS_TO_COLUMN {confidence:1.0}]─►
  (Column {name:"ContactName"}) ◄─[HAS_COLUMN]─ (Table {name:"Customers"})
```

For "employee name" (multi-column concat):
```
(OntologyConcept "employee name")
  ─[MAPS_TO_COLUMN {combination:"concat", ordinal:0}]─► (Column "FirstName")
  ─[MAPS_TO_COLUMN {combination:"concat", ordinal:1}]─► (Column "LastName")
```

---

## 4. Synchronization Pipeline

Create `app/utils/sync_ontology_catalogue.py`:

```python
# app/utils/sync_ontology_catalogue.py
"""
Loads ontology_catalogue.json into Neo4j.
Called during sync_database() after ingest_schema() and ingest_ontology().
"""

from __future__ import annotations
import json
import os
import pathlib
from typing import Any, Dict, List, Optional

from app.db.neo4j_client import Neo4jClient


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution (mirrors catalogue_loader._resolve_path logic)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_ontology_catalogue_path() -> Optional[pathlib.Path]:
    env_path = os.getenv("ONTOLOGY_CATALOGUE_PATH")
    if env_path and pathlib.Path(env_path).exists():
        return pathlib.Path(env_path)
    base = pathlib.Path(__file__).resolve().parents[2] / "catalogue"
    candidate = base / "ontology_catalogue.json"
    return candidate if candidate.exists() else None


def load_ontology_catalogue(path: Optional[str] = None) -> Dict[str, Any]:
    resolved = pathlib.Path(path) if path else _resolve_ontology_catalogue_path()
    if not resolved or not resolved.exists():
        raise FileNotFoundError(
            "ontology_catalogue.json not found. "
            "Set ONTOLOGY_CATALOGUE_PATH in .env or place the file in catalogue/."
        )
    with open(resolved, "r", encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Neo4j ingestion
# ─────────────────────────────────────────────────────────────────────────────

def ingest_ontology_catalogue(
    neo4j: Neo4jClient,
    catalogue: Dict[str, Any],
) -> None:
    """
    Ingests all concepts from the ontology catalogue into Neo4j.

    Strategy:
      1. Create/MERGE OntologyConcept nodes.
      2. Create/MERGE Synonym nodes and IS_SYNONYM_FOR relationships.
      3. Link each concept to its target Table or Column nodes via
         MAPS_TO_TABLE / MAPS_TO_COLUMN relationships.
         These target nodes must already exist (created by ingest_schema()).
    """
    concepts: List[Dict] = catalogue.get("concepts", [])
    print(f"[OntologyCatalogue] Ingesting {len(concepts)} concept entries…")

    # Pass 1: Create OntologyConcept nodes
    concept_nodes = [
        {
            "id":          c["id"],
            "name":        c["concept"].lower().strip(),
            "type":        c["type"],
            "confidence":  c.get("confidence", 1.0),
            "priority":    c.get("priority", 1),
            "combination": c.get("maps_to", {}).get("combination", "single"),
            "sql_expression": c.get("maps_to", {}).get("sql_expression"),
            "notes":       c.get("notes", ""),
        }
        for c in concepts
    ]
    neo4j.query(
        """
        UNWIND $nodes AS n
        MERGE (oc:OntologyConcept {id: n.id})
        SET oc.name        = n.name,
            oc.type        = n.type,
            oc.confidence  = n.confidence,
            oc.priority    = n.priority,
            oc.combination = n.combination,
            oc.sql_expression = n.sql_expression,
            oc.notes       = n.notes
        """,
        {"nodes": concept_nodes},
    )

    # Pass 2: Synonyms
    synonym_entries = []
    for c in concepts:
        oc_id = c["id"]
        for syn in c.get("synonyms", []):
            synonym_entries.append({"oc_id": oc_id, "synonym": syn.lower().strip()})

    if synonym_entries:
        neo4j.query(
            """
            UNWIND $entries AS e
            MATCH (oc:OntologyConcept {id: e.oc_id})
            MERGE (s:Synonym {name: e.synonym})
            MERGE (s)-[:IS_SYNONYM_FOR]->(oc)
            """,
            {"entries": synonym_entries},
        )

    # Pass 3: Wire concepts to schema objects
    for concept in concepts:
        c_type  = concept["type"]
        oc_id   = concept["id"]
        maps_to = concept.get("maps_to", {})
        conf    = concept.get("confidence", 1.0)
        prio    = concept.get("priority", 1)

        if c_type == "entity":
            _link_entity(neo4j, oc_id, maps_to, conf, prio)

        elif c_type == "attribute_alias":
            _link_attribute(neo4j, oc_id, maps_to, conf, prio)

        elif c_type == "metric_alias":
            _link_metric(neo4j, oc_id, maps_to, conf, prio)

        elif c_type == "relationship":
            _link_relationship(neo4j, oc_id, maps_to, conf, prio)

    print(f"[OntologyCatalogue] Ingestion complete.")


def _link_entity(neo4j, oc_id, maps_to, conf, prio):
    table = maps_to.get("table")
    if not table:
        return
    neo4j.query(
        """
        MATCH (oc:OntologyConcept {id: $oc_id})
        MATCH (t:Table {name: $table})
        MERGE (oc)-[r:MAPS_TO_TABLE]->(t)
        SET r.confidence = $conf, r.priority = $prio
        """,
        {"oc_id": oc_id, "table": table, "conf": conf, "prio": prio},
    )


def _link_attribute(neo4j, oc_id, maps_to, conf, prio):
    table   = maps_to.get("table")
    columns = maps_to.get("columns", [])
    combo   = maps_to.get("combination", "single")
    for ordinal, col_name in enumerate(columns):
        neo4j.query(
            """
            MATCH (oc:OntologyConcept {id: $oc_id})
            MATCH (t:Table {name: $table})-[:HAS_COLUMN]->(c:Column {name: $col})
            MERGE (oc)-[r:MAPS_TO_COLUMN]->(c)
            SET r.confidence  = $conf,
                r.priority    = $prio,
                r.combination = $combo,
                r.ordinal     = $ordinal,
                r.table_name  = $table
            """,
            {
                "oc_id": oc_id, "table": table, "col": col_name,
                "conf": conf, "prio": prio, "combo": combo, "ordinal": ordinal,
            },
        )


def _link_metric(neo4j, oc_id, maps_to, conf, prio):
    # Metric aliases don't link to Column nodes directly —
    # they store the SQL expression on the OntologyConcept node itself.
    # We do link to any required tables so the retriever knows what to include.
    for tbl in maps_to.get("requires_tables", []):
        neo4j.query(
            """
            MATCH (oc:OntologyConcept {id: $oc_id})
            MATCH (t:Table {name: $tbl})
            MERGE (oc)-[r:MAPS_TO_TABLE]->(t)
            SET r.confidence = $conf, r.priority = $prio
            """,
            {"oc_id": oc_id, "tbl": tbl, "conf": conf, "prio": prio},
        )


def _link_relationship(neo4j, oc_id, maps_to, conf, prio):
    from_tbl = maps_to.get("from_table")
    to_tbl   = maps_to.get("to_table")
    if not from_tbl or not to_tbl:
        return
    neo4j.query(
        """
        MATCH (oc:OntologyConcept {id: $oc_id})
        MATCH (t1:Table {name: $from_tbl})
        MATCH (t2:Table {name: $to_tbl})
        MERGE (oc)-[r:MAPS_TO_TABLE]->(t1)
        SET r.confidence = $conf, r.priority = $prio
        MERGE (oc)-[r2:MAPS_TO_TABLE]->(t2)
        SET r2.confidence = $conf, r2.priority = $prio
        """,
        {"oc_id": oc_id, "from_tbl": from_tbl, "to_tbl": to_tbl,
         "conf": conf, "prio": prio},
    )
```

### 4.1 Wire into `sync_database()`

In `app/utils/sync_db.py`, add after `neo4j.ingest_ontology(ontology)`:

```python
# 3b. Load ontology catalogue into Neo4j
print("[3b/4] Loading ontology catalogue into Neo4j…")
from app.utils.sync_ontology_catalogue import (
    load_ontology_catalogue,
    ingest_ontology_catalogue,
)
try:
    ont_catalogue = load_ontology_catalogue()
    ingest_ontology_catalogue(neo4j, ont_catalogue)
except FileNotFoundError as e:
    print(f"  ⚠ Skipping ontology catalogue: {e}")
```

---

## 5. Retrieval Logic — `OntologyResolver`

Create `app/core/ontology_resolver.py`:

```python
# app/core/ontology_resolver.py
"""
Resolves user-facing natural language concepts to schema objects by querying
the OntologyConcept nodes ingested from ontology_catalogue.json.

Resolution contract:
  Given a concept string (e.g. "customer name"), returns:
    - The target table name(s)
    - The target column name(s), with combination type
    - An optional pre-built SQL expression (for concat / metric types)
    - Confidence score
    - Whether the match was exact or synonym-based

This is called BEFORE the existing schema metadata fuzzy matching in
CapabilityValidator._check_schema_metadata(), so high-confidence ontology
mappings are authoritative and suppress ambiguous fuzzy matches.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.db.neo4j_client import Neo4jClient


@dataclass
class OntologyResolution:
    concept: str
    match_type: str                    # "exact" | "synonym" | "none"
    confidence: float = 0.0
    priority: int = 99
    ontology_concept_name: str = ""
    ontology_concept_type: str = ""    # entity | attribute_alias | metric_alias | relationship
    tables: List[str] = field(default_factory=list)
    columns: List[Dict[str, Any]] = field(default_factory=list)  # {table, name, ordinal, combination}
    sql_expression: Optional[str] = None
    combination: str = "single"        # single | concat | coalesce | context_dependent

    @property
    def resolved(self) -> bool:
        return self.match_type != "none"

    @property
    def is_authoritative(self) -> bool:
        """True when confidence is high enough to suppress schema fuzzy matching."""
        return self.confidence >= 0.9

    def to_sql_fragment(self, table_alias: Optional[str] = None) -> Optional[str]:
        """
        Produce a SQL SELECT fragment for this resolution.
        Returns None when the caller should fall back to schema matching.
        """
        if self.sql_expression:
            return self.sql_expression
        if not self.columns:
            return None
        combo = self.combination
        cols  = sorted(self.columns, key=lambda c: c.get("ordinal", 0))

        def qualified(c: Dict) -> str:
            tbl = table_alias or f'"{c["table"]}"'
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
            # context_dependent — return the first column and let the planner decide
            return qualified(cols[0])


class OntologyResolver:

    def __init__(self, neo4j_client: Neo4jClient):
        self.neo4j = neo4j_client

    # ─────────────────────────────────────────────────────────────────────────
    # Primary entry point
    # ─────────────────────────────────────────────────────────────────────────

    def resolve(self, concept: str) -> OntologyResolution:
        """
        Attempt to resolve a concept string using:
          1. Exact OntologyConcept name match
          2. Synonym match through IS_SYNONYM_FOR → OntologyConcept
          3. Existing Concept / Synonym nodes (legacy path — entity level only)

        Returns OntologyResolution with match_type="none" when unresolved.
        """
        if not self.neo4j or not self.neo4j.driver:
            return OntologyResolution(concept=concept, match_type="none")

        # Strategy 1: Exact name match
        result = self._query_exact(concept)
        if result:
            return result

        # Strategy 2: Synonym match
        result = self._query_synonym(concept)
        if result:
            return result

        # Strategy 3: Legacy Concept / Synonym nodes (entity-level fallback)
        result = self._query_legacy_ontology(concept)
        if result:
            return result

        return OntologyResolution(concept=concept, match_type="none")

    def resolve_batch(self, concepts: List[str]) -> Dict[str, OntologyResolution]:
        """Resolve multiple concepts, returning a dict keyed by concept string."""
        return {c: self.resolve(c) for c in concepts}

    # ─────────────────────────────────────────────────────────────────────────
    # Cypher queries
    # ─────────────────────────────────────────────────────────────────────────

    def _query_exact(self, concept: str) -> Optional[OntologyResolution]:
        results = self.neo4j.query(
            """
            MATCH (oc:OntologyConcept)
            WHERE replace(toLower(oc.name), ' ', '') = replace(toLower($name), ' ', '')
            OPTIONAL MATCH (oc)-[rt:MAPS_TO_TABLE]->(t:Table)
            OPTIONAL MATCH (oc)-[rc:MAPS_TO_COLUMN]->(col:Column)
            OPTIONAL MATCH (col)<-[:HAS_COLUMN]-(ct:Table)
            RETURN oc, rt, t, rc, col, ct
            ORDER BY oc.priority ASC
            LIMIT 10
            """,
            {"name": concept},
        )
        return self._build_resolution(concept, results, "exact") if results else None

    def _query_synonym(self, concept: str) -> Optional[OntologyResolution]:
        results = self.neo4j.query(
            """
            MATCH (s:Synonym)-[:IS_SYNONYM_FOR]->(oc:OntologyConcept)
            WHERE replace(toLower(s.name), ' ', '') = replace(toLower($name), ' ', '')
            OPTIONAL MATCH (oc)-[rt:MAPS_TO_TABLE]->(t:Table)
            OPTIONAL MATCH (oc)-[rc:MAPS_TO_COLUMN]->(col:Column)
            OPTIONAL MATCH (col)<-[:HAS_COLUMN]-(ct:Table)
            RETURN oc, rt, t, rc, col, ct
            ORDER BY oc.priority ASC
            LIMIT 10
            """,
            {"name": concept},
        )
        return self._build_resolution(concept, results, "synonym") if results else None

    def _query_legacy_ontology(self, concept: str) -> Optional[OntologyResolution]:
        """Falls back to the existing :Concept → :Table path for entity resolution."""
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
        if results:
            tables = [r["table_name"] for r in results if r.get("table_name")]
            return OntologyResolution(
                concept=concept,
                match_type="synonym",
                confidence=0.85,
                priority=3,
                ontology_concept_type="entity",
                tables=tables,
            )
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # Result assembly
    # ─────────────────────────────────────────────────────────────────────────

    def _build_resolution(
        self,
        concept: str,
        records: List[Any],
        match_type: str,
    ) -> Optional[OntologyResolution]:
        if not records:
            return None

        # Extract OntologyConcept node from first record
        first = records[0]
        oc_node = first.get("oc") or {}
        oc_props = dict(oc_node) if hasattr(oc_node, "items") else {}

        resolution = OntologyResolution(
            concept=concept,
            match_type=match_type,
            confidence=oc_props.get("confidence", 1.0),
            priority=oc_props.get("priority", 1),
            ontology_concept_name=oc_props.get("name", ""),
            ontology_concept_type=oc_props.get("type", "entity"),
            sql_expression=oc_props.get("sql_expression"),
            combination=oc_props.get("combination", "single"),
        )

        # Aggregate tables and columns from all returned rows
        seen_tables: set = set()
        seen_cols:   set = set()

        for row in records:
            t = row.get("t")
            if t:
                tbl_name = dict(t).get("name")
                if tbl_name and tbl_name not in seen_tables:
                    seen_tables.add(tbl_name)
                    resolution.tables.append(tbl_name)

            col = row.get("col")
            rc  = row.get("rc")
            ct  = row.get("ct")
            if col:
                col_props = dict(col)
                rc_props  = dict(rc) if rc else {}
                ct_props  = dict(ct) if ct else {}
                col_key = (ct_props.get("name", ""), col_props.get("name", ""))
                if col_key not in seen_cols:
                    seen_cols.add(col_key)
                    resolution.columns.append({
                        "table":    ct_props.get("name", ""),
                        "name":     col_props.get("name", ""),
                        "ordinal":  rc_props.get("ordinal", 0),
                        "combination": rc_props.get("combination", "single"),
                    })
                    if col_key[0] and col_key[0] not in seen_tables:
                        seen_tables.add(col_key[0])
                        resolution.tables.append(col_key[0])

        return resolution if (resolution.tables or resolution.columns) else None
```

---

## 6. Integration into `CapabilityValidator`

The `OntologyResolver` is inserted as **Tier 0** — before any existing tier — in `_classify_concept()`. This is the only change required to `capability_validator.py`:

```python
# In CapabilityValidator.__init__(), add:
from app.core.ontology_resolver import OntologyResolver
self._ontology_resolver = OntologyResolver(neo4j_client)

# In _classify_concept(), add Tier 0 BEFORE the existing analytical whitelist check:

def _classify_concept(self, concept: str, intent_key: str) -> ConceptVerdict:

    # ── Tier 0: Ontology Catalogue (NEW — highest priority) ──────────────
    resolution = self._ontology_resolver.resolve(concept)
    if resolution.resolved and resolution.is_authoritative:
        obj_list = []
        for c in resolution.columns:
            obj_list.append(f"{c['table']}.{c['name']}")
        if not obj_list:
            obj_list = resolution.tables

        return ConceptVerdict(
            concept=concept,
            status=ConceptStatus.MATCHED,
            evidence=(
                f"Ontology catalogue {resolution.match_type} match: "
                f"'{concept}' → {resolution.ontology_concept_type} "
                f"'{resolution.ontology_concept_name}' "
                f"(confidence={resolution.confidence:.2f})"
            ),
            matched_objects=obj_list,
        )

    # ── Step 0: Analytical phrase whitelist (existing) ────────────────────
    # ... rest of method unchanged ...
```

---

## 7. Integration into `ContextRetriever`

The retriever needs to use `OntologyResolution` objects to add **precise grounding mappings** and **pre-built SQL fragments** to the context passed to the LLM. This replaces the vague "Field Match" grounding with explicit column assignments.

```python
# In ContextRetriever.__init__(), add:
from app.core.ontology_resolver import OntologyResolver
self._ontology_resolver = OntologyResolver(self.graph)

# Replace _resolve_ontology() with this enhanced version:

def _resolve_ontology(self, entity: str) -> tuple[set[str], list[str]]:
    tables   = set()
    mappings = []

    # 1. Query OntologyCatalogue first (new)
    resolution = self._ontology_resolver.resolve(entity)
    if resolution.resolved:
        for tbl in resolution.tables:
            tables.add(tbl)

        if resolution.columns:
            col_strs = [f"{c['table']}.{c['name']}" for c in resolution.columns]
            sql_frag = resolution.to_sql_fragment()
            if resolution.combination == "concat":
                mappings.append(
                    f"Column Alias: '{entity}' → CONCAT expression: {sql_frag}"
                )
            else:
                mappings.append(
                    f"Column Alias: '{entity}' → Exact Column(s): {', '.join(col_strs)}"
                )
        elif resolution.sql_expression:
            mappings.append(
                f"Metric Alias: '{entity}' → SQL Expression: {resolution.sql_expression}"
            )
        else:
            for tbl in resolution.tables:
                mappings.append(
                    f"Entity Alias: '{entity}' → Table '{tbl}'"
                )
        return tables, mappings

    # 2. Fall back to legacy Concept/Synonym query (existing behaviour)
    synonym_matches = self.graph.query(
        """
        MATCH (s:Synonym)
        WHERE replace(toLower(s.name), ' ', '') = replace(toLower($name), ' ', '')
        MATCH (s)-[:IS_SYNONYM_FOR]->(con:Concept)-[:MAPS_TO]->(t:Table)
        RETURN t.name as table_name, con.name as concept
        """,
        {"name": entity},
    )
    if synonym_matches:
        for m in synonym_matches:
            tables.add(m["table_name"])
            mappings.append(
                f"Business Term: '{entity}' maps to Table '{m['table_name']}'"
            )
    return tables, mappings
```

### 7.1 Enriched Context Block

The retriever's `retrieve_context()` should also emit a dedicated `### ONTOLOGY COLUMN ALIASES` section when any resolutions produce column-level mappings. This signals to the LLM that it must use those exact columns:

```python
# In retrieve_context(), after the grounding_mappings are assembled,
# add a dedicated section for column-level ontology resolutions:

ontology_aliases = [
    m for m in grounding_mappings
    if m.startswith("Column Alias:") or m.startswith("Metric Alias:")
]
if ontology_aliases:
    context_parts.insert(1,   # insert right after SEMANTIC VALUE MAPPINGS
        "\n### ONTOLOGY COLUMN ALIASES (MANDATORY — DO NOT SUBSTITUTE)\n"
        "These mappings are authoritative. Use EXACTLY these columns for the "
        "corresponding user terms:\n" +
        "\n".join(f"- {m}" for m in ontology_aliases)
    )
```

The LLM prompt now explicitly contains:
```
### ONTOLOGY COLUMN ALIASES (MANDATORY — DO NOT SUBSTITUTE)
- Column Alias: 'customer name' → Exact Column(s): Customers.ContactName
- Column Alias: 'employee name' → CONCAT expression: Employees.FirstName || ' ' || Employees.LastName
```

This eliminates the guessing problem at the generator level.

---

## 8. Changes to `SQLGenerator`

The generator's system prompt needs one new protocol to enforce ontology aliases:

```python
# In generate_plan_and_sql(), add to system_prompt after PROTOCOL 2:

### PROTOCOL 2b: ONTOLOGY COLUMN ALIAS ENFORCEMENT
If the context contains an 'ONTOLOGY COLUMN ALIASES' section, those mappings
are AUTHORITATIVE. When a user term (e.g. "customer name") appears in that
section mapped to a specific column (e.g. Customers.ContactName), you MUST
use that exact column in your SELECT and WHERE clauses. You are FORBIDDEN from
substituting a "similar sounding" column from the same table.
```

No other generator changes are needed — the information delivery happens through the context string.

---

## 9. Interaction with Existing Catalogues

| Catalogue | Scope | Authority | Interaction |
|---|---|---|---|
| `field_catalogue2.json` | Metric formulas, rounding rules, safety rules | High for metrics | Unchanged. Ontology catalogue handles column identity; field catalogue handles how to aggregate those columns. |
| `ontology_catalogue.json` | Column identity, entity-to-table mapping, relationship paths | Absolute for attribute aliases | New. Resolved in Tier 0, before field catalogue. |
| Schema metadata (Info DB) | Physical column existence, sample values | Ground truth | Still used for validation. Ontology resolution returns column names; Info DB confirms they exist. |
| Vector search (ChromaDB) | Fuzzy semantic similarity | Low-to-medium | Only consulted when both ontology and schema matching fail. Ontology completely eliminates the vector search for known concepts. |

### Resolution Priority Stack

```
Query concept
     │
     ▼
[Tier 0] OntologyCatalogue exact/synonym match (confidence ≥ 0.9)
     │ Hit → MATCHED (authoritative, suppresses lower tiers)
     │ Miss or low-confidence →
     ▼
[Step 0] Analytical phrase whitelist (pure aggregation intents)
     │ Hit → DERIVABLE
     │ Miss →
     ▼
[Step 1] Metric synonym map
     │ Hit → DERIVABLE
     │ Miss →
     ▼
[Tier 1] Field catalogue (metrics only)
     │ Hit → DERIVABLE
     │ Miss →
     ▼
[Tier 2] Schema metadata (exact/fuzzy/morphological/compound)
     │ Hit → MATCHED
     │ Miss →
     ▼
[Tier 3] Neo4j ontology / Vector search
     │ Hit → MATCHED
     │ Miss →
     ▼
UNRESOLVABLE → 400 rejection
```

---

## 10. Potential Drawbacks and Edge Cases

### 10.1 Stale Ontology After Schema Changes

**Problem:** The ontology catalogue maps `customer name → ContactName`. If the database is refactored and the column is renamed to `PersonContact`, the catalogue is wrong.

**Mitigation:** During `sync_database()`, after ingesting the catalogue, run a validation pass:

```python
def validate_ontology_catalogue(neo4j: Neo4jClient, info_db_path: str) -> list[str]:
    """Returns a list of warnings for catalogue entries that reference non-existent columns."""
    from sqlalchemy import create_engine, text
    engine = create_engine(f"sqlite:///{info_db_path}")
    warnings = []
    results = neo4j.query(
        """
        MATCH (oc:OntologyConcept)-[r:MAPS_TO_COLUMN]->(col:Column)
        OPTIONAL MATCH (col)<-[:HAS_COLUMN]-(t:Table)
        RETURN oc.id AS oc_id, oc.name AS concept,
               t.name AS table_name, col.name AS col_name
        """
    )
    with engine.connect() as conn:
        for row in (results or []):
            r = conn.execute(
                text("SELECT 1 FROM meta_columns WHERE table_name=:t AND name=:c LIMIT 1"),
                {"t": row["table_name"], "c": row["col_name"]}
            ).fetchone()
            if not r:
                warnings.append(
                    f"WARNING: Ontology entry '{row['oc_id']}' maps "
                    f"'{row['concept']}' → {row['table_name']}.{row['col_name']}, "
                    f"but this column was NOT FOUND in Info DB. "
                    f"Update ontology_catalogue.json."
                )
    return warnings
```

Print these warnings during sync so developers know the catalogue is stale.

### 10.2 Multi-Database Conflict

**Problem:** Northwind maps `customer name → ContactName`. AdventureWorks maps it to `FirstName + LastName`. If both catalogues are loaded, concepts conflict.

**Mitigation:** The `database` field at the root of the catalogue must match `os.getenv("DATABASE_URL")` (parsed for db name). Add a guard in `ingest_ontology_catalogue()`:

```python
db_url   = os.getenv("DATABASE_URL", "")
db_name  = catalogue.get("database", "").lower()
if db_name and db_name not in db_url.lower():
    print(
        f"[OntologyCatalogue] WARNING: catalogue targets '{db_name}' but "
        f"DATABASE_URL appears to be '{db_url}'. Skipping ingestion to prevent "
        f"cross-database contamination."
    )
    return
```

### 10.3 Partial Concept Matching

**Problem:** User says "show me the names of customers" — not an exact match for "customer name".

**Mitigation:** The existing synonym mechanism already handles common phrasings. For longer natural-language fragments, the intent extractor (LLM) converts them to normalised concept words before resolution. The key is ensuring your intent extractor system prompt instructs it to output canonical short phrases like `customer name`, not long sentences.

Add to `IntentExtractor` system prompt:
```
For entities and dimensions, output SHORT canonical phrases (2-4 words max),
e.g. "customer name" not "the name of the customer".
```

### 10.4 `context_dependent` Combination Ambiguity

**Problem:** `customer location` maps to both `City` and `Country`. Without the right one, the SQL is wrong.

**Mitigation:** For `context_dependent` combinations, the resolution emits **both columns and a hint** into the context. The generator's system prompt instructs it to read the `resolution_hint` and choose based on the filter values present.

```
Column Alias: 'customer location' → Context-dependent: use Customers.City
  when filter implies a city-level match, use Customers.Country for country-level.
  Current filters in query: ['London'] → Use Customers.City
```

The retriever can inject the filter value from the intent to make this determination at retrieval time, not generation time.

### 10.5 Confidence Scores Below Threshold

**Problem:** An entry with `confidence: 0.7` exists for "customer address". The validator passes it through Tier 0 as MATCHED. But the planner then also gets fuzzy schema results for the same concept. Which wins?

**Mitigation:** Two-tier behaviour based on `is_authoritative` (threshold ≥ 0.9):

- **Authoritative** (`≥ 0.9`): Tier 0 returns MATCHED immediately. Lower tiers are not consulted. The column alias goes into the context as mandatory.
- **Advisory** (`< 0.9`): Tier 0 records the resolution as a hint, but the validator continues to Tier 2 and 3. Both results are included in grounding with clear labelling ("Suggested by ontology catalogue", "Confirmed by schema metadata"), and the generator is asked to prefer the ontology suggestion unless schema metadata contradicts it.

---

## 11. Production File Layout

```
catalogue/
├── field_catalogue2.json       # existing — metric/formula rules
├── ontology_catalogue.json     # NEW — semantic concept mappings

app/
├── core/
│   ├── capability_validator.py # modified — add Tier 0 OntologyResolver call
│   ├── ontology_resolver.py    # NEW — OntologyResolver class
│   ├── retriever.py            # modified — use OntologyResolver in _resolve_ontology
│   └── generator.py            # modified — add Protocol 2b to system prompt
├── utils/
│   ├── sync_ontology_catalogue.py  # NEW — ingestion pipeline
│   └── sync_db.py              # modified — call sync_ontology_catalogue
```

---

## 12. Complete `ontology_catalogue.json` for Northwind

```json
{
  "database": "Northwind",
  "version": "1.0",
  "description": "Semantic attribute mappings for the Northwind trading database",
  "concepts": [
    {
      "id": "ONT_ENT_001", "concept": "customer", "type": "entity",
      "maps_to": {"table": "Customers"},
      "synonyms": ["client", "buyer", "account", "purchaser", "patron"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ENT_002", "concept": "employee", "type": "entity",
      "maps_to": {"table": "Employees"},
      "synonyms": ["staff", "worker", "rep", "sales rep", "agent", "associate"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ENT_003", "concept": "product", "type": "entity",
      "maps_to": {"table": "Products"},
      "synonyms": ["item", "goods", "article", "sku", "merchandise"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ENT_004", "concept": "supplier", "type": "entity",
      "maps_to": {"table": "Suppliers"},
      "synonyms": ["vendor", "provider", "manufacturer", "source"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ENT_005", "concept": "order", "type": "entity",
      "maps_to": {"table": "Orders"},
      "synonyms": ["purchase", "transaction", "sale", "shipment"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ENT_006", "concept": "category", "type": "entity",
      "maps_to": {"table": "Categories"},
      "synonyms": ["product category", "product group", "product type", "segment"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_001", "concept": "customer name", "type": "attribute_alias",
      "maps_to": {"table": "Customers", "columns": ["ContactName"], "combination": "single"},
      "synonyms": ["contact name", "customer contact", "person name", "client name"],
      "confidence": 1.0, "priority": 1,
      "notes": "The human representative at the customer account"
    },
    {
      "id": "ONT_ATTR_002", "concept": "customer company", "type": "attribute_alias",
      "maps_to": {"table": "Customers", "columns": ["CompanyName"], "combination": "single"},
      "synonyms": ["company name", "business name", "organisation name",
                   "firm name", "account name", "customer firm"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_003", "concept": "customer title", "type": "attribute_alias",
      "maps_to": {"table": "Customers", "columns": ["ContactTitle"], "combination": "single"},
      "synonyms": ["contact title", "job title", "role", "position"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_004", "concept": "customer city", "type": "attribute_alias",
      "maps_to": {"table": "Customers", "columns": ["City"], "combination": "single"},
      "synonyms": ["city", "customer location city", "city of customer"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_005", "concept": "customer country", "type": "attribute_alias",
      "maps_to": {"table": "Customers", "columns": ["Country"], "combination": "single"},
      "synonyms": ["country", "customer location country", "nation"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_006", "concept": "employee name", "type": "attribute_alias",
      "maps_to": {
        "table": "Employees", "columns": ["FirstName", "LastName"],
        "combination": "concat",
        "sql_expression": "Employees.FirstName || ' ' || Employees.LastName"
      },
      "synonyms": ["staff name", "worker name", "rep name", "sales rep name",
                   "agent name", "associate name"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_007", "concept": "employee title", "type": "attribute_alias",
      "maps_to": {"table": "Employees", "columns": ["Title"], "combination": "single"},
      "synonyms": ["job title", "staff title", "employee role", "staff role"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_008", "concept": "product name", "type": "attribute_alias",
      "maps_to": {"table": "Products", "columns": ["ProductName"], "combination": "single"},
      "synonyms": ["item name", "goods name", "article name", "sku name"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_009", "concept": "product price", "type": "attribute_alias",
      "maps_to": {"table": "Products", "columns": ["UnitPrice"], "combination": "single"},
      "synonyms": ["unit price", "list price", "catalogue price", "price per unit"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_010", "concept": "supplier name", "type": "attribute_alias",
      "maps_to": {"table": "Suppliers", "columns": ["CompanyName"], "combination": "single"},
      "synonyms": ["vendor name", "provider name", "manufacturer name", "supplier company"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_011", "concept": "supplier country", "type": "attribute_alias",
      "maps_to": {"table": "Suppliers", "columns": ["Country"], "combination": "single"},
      "synonyms": ["vendor country", "source country", "supplier location"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_ATTR_012", "concept": "category name", "type": "attribute_alias",
      "maps_to": {"table": "Categories", "columns": ["CategoryName"], "combination": "single"},
      "synonyms": ["product category name", "group name", "segment name"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_METRIC_001", "concept": "order revenue", "type": "metric_alias",
      "maps_to": {
        "expression": "ROUND(SUM(\"Order Details\".Quantity * \"Order Details\".UnitPrice), 2)",
        "requires_tables": ["Order Details"],
        "label": "order_revenue"
      },
      "synonyms": ["gross revenue", "gross sales", "sales", "total revenue"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_METRIC_002", "concept": "discounted revenue", "type": "metric_alias",
      "maps_to": {
        "expression": "ROUND(SUM(\"Order Details\".Quantity * \"Order Details\".UnitPrice * (1 - \"Order Details\".Discount)), 2)",
        "requires_tables": ["Order Details"],
        "label": "discounted_revenue"
      },
      "synonyms": ["net revenue", "revenue after discount", "net sales"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_REL_001", "concept": "customer orders", "type": "relationship",
      "maps_to": {
        "from_table": "Customers", "to_table": "Orders",
        "join_condition": "Customers.CustomerID = Orders.CustomerID"
      },
      "synonyms": ["orders by customer", "customer purchases", "purchases by customer"],
      "confidence": 1.0, "priority": 1
    },
    {
      "id": "ONT_REL_002", "concept": "order products", "type": "relationship",
      "maps_to": {
        "from_table": "Orders", "to_table": "Products",
        "join_path": ["Orders", "Order Details", "Products"],
        "join_condition": "Orders.OrderID = \"Order Details\".OrderID AND \"Order Details\".ProductID = Products.ProductID"
      },
      "synonyms": ["products in order", "items in order"],
      "confidence": 1.0, "priority": 1
    }
  ],
  "defaults": {
    "fallback_to_schema_matching": true,
    "minimum_confidence_threshold": 0.7,
    "prefer_ontology_over_vector": true
  }
}
```

---

## 13. `.env` Addition

```dotenv
# Ontology catalogue path (optional — defaults to catalogue/ontology_catalogue.json)
ONTOLOGY_CATALOGUE_PATH=d:\Retrival\catalogue\ontology_catalogue.json
```

---

## 14. Summary of All Changes Required

| File | Change Type | Description |
|---|---|---|
| `catalogue/ontology_catalogue.json` | **NEW** | The semantic mapping catalogue |
| `app/core/ontology_resolver.py` | **NEW** | `OntologyResolver` class |
| `app/utils/sync_ontology_catalogue.py` | **NEW** | Ingestion pipeline |
| `app/core/capability_validator.py` | **Modified** | Add Tier 0 resolution call |
| `app/core/retriever.py` | **Modified** | Use `OntologyResolver` in `_resolve_ontology` |
| `app/core/generator.py` | **Modified** | Add Protocol 2b to system prompt |
| `app/utils/sync_db.py` | **Modified** | Call `sync_ontology_catalogue` |
| `.env` | **Modified** | Add `ONTOLOGY_CATALOGUE_PATH` |

The Neo4j graph gains two new node labels (`OntologyConcept`) and two new relationship types (`MAPS_TO_TABLE` with confidence/priority properties, `MAPS_TO_COLUMN` with combination/ordinal). Existing nodes and relationships are untouched.
