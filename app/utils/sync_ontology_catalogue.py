"""
sync_ontology_catalogue.py
--------------------------
Loads ontology_catalogue.json into Neo4j as OntologyConcept nodes, Synonym
nodes, and MAPS_TO_TABLE / MAPS_TO_COLUMN relationships.

Called during sync_database() AFTER ingest_schema() and ingest_ontology(),
because it wires OntologyConcept nodes to the :Table and :Column nodes that
ingest_schema() must have already created.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any, Dict, List, Optional

from app.db.neo4j_client import Neo4jClient


# ─────────────────────────────────────────────────────────────────────────────
# Path resolution
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_catalogue_path() -> Optional[pathlib.Path]:
    """
    Preference order:
      1. ONTOLOGY_CATALOGUE_PATH env variable (if set and file exists)
      2. catalogue/ontology_catalogue.json relative to project root
    """
    env_path = os.getenv("ONTOLOGY_CATALOGUE_PATH")
    if env_path and pathlib.Path(env_path).exists():
        return pathlib.Path(env_path)

    base      = pathlib.Path(__file__).resolve().parents[2] / "catalogue"
    candidate = base / "ontology_catalogue.json"
    return candidate if candidate.exists() else None


def load_ontology_catalogue(path: Optional[str] = None) -> Dict[str, Any]:
    resolved = pathlib.Path(path) if path else _resolve_catalogue_path()
    if not resolved or not resolved.exists():
        raise FileNotFoundError(
            "ontology_catalogue.json not found. "
            "Set ONTOLOGY_CATALOGUE_PATH in .env or place the file at "
            "catalogue/ontology_catalogue.json."
        )
    with open(resolved, "r", encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_ontology_catalogue(
    neo4j: Neo4jClient,
    info_db_path: str = "data/info.db",
) -> List[str]:
    """
    Checks every MAPS_TO_COLUMN relationship created from the catalogue against
    the Info DB.  Returns a list of warning strings for missing columns.
    Run after ingest_ontology_catalogue() during sync.
    """
    from sqlalchemy import create_engine, text as sa_text

    engine   = create_engine(f"sqlite:///{info_db_path}")
    warnings = []

    try:
        results = neo4j.query(
            """
            MATCH (oc:OntologyConcept)-[r:MAPS_TO_COLUMN]->(col:Column)
            OPTIONAL MATCH (col)<-[:HAS_COLUMN]-(t:Table)
            RETURN oc.id      AS oc_id,
                   oc.name    AS concept,
                   t.name     AS table_name,
                   col.name   AS col_name
            """
        )
    except Exception as e:
        warnings.append(f"[validate_ontology_catalogue] Neo4j query failed: {e}")
        return warnings

    if not results:
        return warnings

    with engine.connect() as conn:
        for row in results:
            tbl = row.get("table_name")
            col = row.get("col_name")
            if not tbl or not col:
                continue
            exists = conn.execute(
                sa_text(
                    "SELECT 1 FROM meta_columns "
                    "WHERE table_name = :t AND name = :c LIMIT 1"
                ),
                {"t": tbl, "c": col},
            ).fetchone()
            if not exists:
                warnings.append(
                    f"  ⚠ Ontology entry '{row.get('oc_id')}' maps "
                    f"'{row.get('concept')}' → {tbl}.{col}, "
                    f"but this column was NOT FOUND in Info DB. "
                    f"Update ontology_catalogue.json or re-run sync."
                )
    return warnings


# ─────────────────────────────────────────────────────────────────────────────
# Neo4j ingestion
# ─────────────────────────────────────────────────────────────────────────────

def ingest_ontology_catalogue(
    neo4j: Neo4jClient,
    catalogue: Dict[str, Any],
    info_db_path: str = "data/info.db",
) -> None:
    """
    Ingests all concepts from the ontology catalogue into Neo4j.

    Steps:
      1. Guard: check catalogue.database matches active DATABASE_URL.
      2. Create / MERGE :OntologyConcept nodes.
      3. Create / MERGE :Synonym nodes and IS_SYNONYM_FOR relationships.
      4. Wire each concept to its target :Table or :Column nodes via
         MAPS_TO_TABLE / MAPS_TO_COLUMN relationships.
      5. Run post-ingestion validation and print any warnings.

    Target :Table and :Column nodes must already exist in Neo4j
    (created by Neo4jClient.ingest_schema() before this function is called).
    """

    # ── Guard: cross-database contamination prevention ────────────────────────
    db_url      = os.getenv("DATABASE_URL", "")
    cat_db_name = catalogue.get("database", "").lower()
    if cat_db_name and db_url and cat_db_name not in db_url.lower():
        print(
            f"[OntologyCatalogue] WARNING: catalogue targets '{cat_db_name}' but "
            f"DATABASE_URL is '{db_url}'. "
            f"Skipping ingestion to prevent cross-database contamination."
        )
        return

    concepts: List[Dict] = catalogue.get("concepts", [])
    if not concepts:
        print("[OntologyCatalogue] No concepts found in catalogue — nothing to ingest.")
        return

    print(f"[OntologyCatalogue] Ingesting {len(concepts)} concept entries…")

    # ── Pass 1: OntologyConcept nodes ─────────────────────────────────────────
    concept_nodes = []
    for c in concepts:
        maps_to = c.get("maps_to", {})
        concept_nodes.append({
            "id":               c["id"],
            "name":             c["concept"].lower().strip(),
            "type":             c["type"],
            "confidence":       float(c.get("confidence", 1.0)),
            "priority":         int(c.get("priority", 1)),
            "combination":      maps_to.get("combination", "single"),
            "sql_expression":   maps_to.get("sql_expression"),
            "resolution_hint":  maps_to.get("resolution_hint"),
            "notes":            c.get("notes", ""),
        })

    neo4j.query(
        """
        UNWIND $nodes AS n
        MERGE (oc:OntologyConcept {id: n.id})
        SET oc.name             = n.name,
            oc.type             = n.type,
            oc.confidence       = n.confidence,
            oc.priority         = n.priority,
            oc.combination      = n.combination,
            oc.sql_expression   = n.sql_expression,
            oc.resolution_hint  = n.resolution_hint,
            oc.notes            = n.notes
        """,
        {"nodes": concept_nodes},
    )
    print(f"  ✓ {len(concept_nodes)} OntologyConcept nodes created/updated.")

    # ── Pass 2: Synonym nodes + IS_SYNONYM_FOR relationships ─────────────────
    synonym_entries = []
    for c in concepts:
        oc_id = c["id"]
        for syn in c.get("synonyms", []):
            if syn and syn.strip():
                synonym_entries.append({
                    "oc_id":   oc_id,
                    "synonym": syn.lower().strip(),
                })

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
        print(f"  ✓ {len(synonym_entries)} synonym relationships created/updated.")

    # ── Pass 3: Wire concepts to schema objects ───────────────────────────────
    linked   = 0
    skipped  = 0
    for concept in concepts:
        c_type  = concept["type"]
        oc_id   = concept["id"]
        maps_to = concept.get("maps_to", {})
        conf    = float(concept.get("confidence", 1.0))
        prio    = int(concept.get("priority", 1))

        if c_type == "entity":
            ok = _link_entity(neo4j, oc_id, maps_to, conf, prio)
        elif c_type == "attribute_alias":
            ok = _link_attribute(neo4j, oc_id, maps_to, conf, prio)
        elif c_type == "metric_alias":
            ok = _link_metric(neo4j, oc_id, maps_to, conf, prio)
        elif c_type == "relationship":
            ok = _link_relationship(neo4j, oc_id, maps_to, conf, prio)
        else:
            print(f"  ⚠ Unknown concept type '{c_type}' for id '{oc_id}' — skipping.")
            ok = False

        if ok:
            linked += 1
        else:
            skipped += 1

    print(f"  ✓ {linked} concepts linked to schema objects. {skipped} skipped (missing targets).")

    # ── Pass 4: Post-ingestion validation ─────────────────────────────────────
    warnings = validate_ontology_catalogue(neo4j, info_db_path)
    if warnings:
        print(f"\n[OntologyCatalogue] {len(warnings)} validation warning(s):")
        for w in warnings:
            print(w)
    else:
        print("  ✓ All column references validated against Info DB.")

    print("[OntologyCatalogue] Ingestion complete.\n")


# ─────────────────────────────────────────────────────────────────────────────
# Per-type linkers
# ─────────────────────────────────────────────────────────────────────────────

def _link_entity(
    neo4j: Neo4jClient,
    oc_id: str,
    maps_to: Dict,
    conf: float,
    prio: int,
) -> bool:
    table = maps_to.get("table")
    if not table:
        return False
    try:
        neo4j.query(
            """
            MATCH (oc:OntologyConcept {id: $oc_id})
            MATCH (t:Table {name: $table})
            MERGE (oc)-[r:MAPS_TO_TABLE]->(t)
            SET r.confidence = $conf,
                r.priority   = $prio
            """,
            {"oc_id": oc_id, "table": table, "conf": conf, "prio": prio},
        )
        return True
    except Exception as e:
        print(f"  ⚠ _link_entity failed for '{oc_id}' → '{table}': {e}")
        return False


def _link_attribute(
    neo4j: Neo4jClient,
    oc_id: str,
    maps_to: Dict,
    conf: float,
    prio: int,
) -> bool:
    table   = maps_to.get("table")
    columns = maps_to.get("columns", [])
    combo   = maps_to.get("combination", "single")
    if not table or not columns:
        return False

    any_linked = False
    for ordinal, col_name in enumerate(columns):
        try:
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
                    "oc_id":   oc_id,
                    "table":   table,
                    "col":     col_name,
                    "conf":    conf,
                    "prio":    prio,
                    "combo":   combo,
                    "ordinal": ordinal,
                },
            )
            any_linked = True
        except Exception as e:
            print(f"  ⚠ _link_attribute failed for '{oc_id}' → '{table}.{col_name}': {e}")

    return any_linked


def _link_metric(
    neo4j: Neo4jClient,
    oc_id: str,
    maps_to: Dict,
    conf: float,
    prio: int,
) -> bool:
    """
    Metric aliases store their SQL expression on the OntologyConcept node.
    We additionally link to any required tables so the retriever includes them.
    """
    required = maps_to.get("requires_tables", [])
    if not required:
        # Nothing to wire — the sql_expression is already stored on the node.
        return True

    any_linked = False
    for tbl in required:
        try:
            neo4j.query(
                """
                MATCH (oc:OntologyConcept {id: $oc_id})
                MATCH (t:Table {name: $tbl})
                MERGE (oc)-[r:MAPS_TO_TABLE]->(t)
                SET r.confidence = $conf,
                    r.priority   = $prio
                """,
                {"oc_id": oc_id, "tbl": tbl, "conf": conf, "prio": prio},
            )
            any_linked = True
        except Exception as e:
            print(f"  ⚠ _link_metric failed for '{oc_id}' → table '{tbl}': {e}")

    return any_linked


def _link_relationship(
    neo4j: Neo4jClient,
    oc_id: str,
    maps_to: Dict,
    conf: float,
    prio: int,
) -> bool:
    from_tbl = maps_to.get("from_table")
    to_tbl   = maps_to.get("to_table")
    if not from_tbl or not to_tbl:
        return False
    try:
        neo4j.query(
            """
            MATCH (oc:OntologyConcept {id: $oc_id})
            MATCH (t1:Table {name: $from_tbl})
            MATCH (t2:Table {name: $to_tbl})
            MERGE (oc)-[r1:MAPS_TO_TABLE]->(t1)
            SET r1.confidence = $conf, r1.priority = $prio
            MERGE (oc)-[r2:MAPS_TO_TABLE]->(t2)
            SET r2.confidence = $conf, r2.priority = $prio
            """,
            {
                "oc_id":    oc_id,
                "from_tbl": from_tbl,
                "to_tbl":   to_tbl,
                "conf":     conf,
                "prio":     prio,
            },
        )
        return True
    except Exception as e:
        print(f"  ⚠ _link_relationship failed for '{oc_id}': {e}")
        return False