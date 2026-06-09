import os
import json
import sqlite3
from dotenv import load_dotenv
from app.core.extractor import SchemaExtractor
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient
from app.core.ontologist import DomainOntologist
from app.utils.llm import LLMClient
from app.utils.catalogue_loader import get_rule_for_field

load_dotenv()


def _store_rules_sqlite(info_db_path: str, schema_data: dict):
    """Persist field catalogue rule metadata to SQLite."""
    conn = sqlite3.connect(info_db_path)
    cur  = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS column_rules (
            table_name TEXT,
            column_name TEXT,
            rule_id TEXT,
            rule_type TEXT,
            rule_sql TEXT
        )"""
    )
    for tbl in schema_data.get("tables", []):
        for col in tbl.get("columns", []):
            field = f"{tbl['name']}.{col['name']}"
            rule  = get_rule_for_field(field)
            if rule:
                cur.execute(
                    "INSERT INTO column_rules VALUES (?,?,?,?,?)",
                    (
                        tbl["name"],
                        col["name"],
                        rule["id"],
                        rule["type"],
                        json.dumps(rule.get("sql_rule")),
                    ),
                )
    conn.commit()
    conn.close()


def sync_database():
    db_url = os.getenv("DATABASE_URL")
    print(f"--- Synchronizing System with Database: {db_url} ---")

    # Prompt for client documentation mode
    use_doc = input("Enable client documentation mode? (yes/no): ").strip().lower()
    if use_doc == "yes":
        doc_path = input("Enter the absolute path to the documentation file: ").strip()
        if os.path.isfile(doc_path):
            os.environ["CLIENT_DOC_PATH"]  = doc_path
            os.environ["USE_CLIENT_DOC"]   = "true"
            print(f"Client documentation will be used from: {doc_path}")
        else:
            print("File not found. Continuing without client documentation.")
    else:
        os.environ["USE_CLIENT_DOC"] = "false"

    # ── Step 1: Extract schema ─────────────────────────────────────────────
    print("[1/5] Extracting metadata using SQLAlchemy...")
    extractor   = SchemaExtractor(db_url)
    schema_data = extractor.get_schema_summary()
    extractor.save_schema_to_json("data/schema_summary.json")
    extractor.save_to_info_db(schema_data, "data/info.db")
    _store_rules_sqlite("data/info.db", schema_data)

    # ── Step 2: Update Neo4j Knowledge Graph ──────────────────────────────
    print("[2/5] Updating Neo4j Knowledge Graph...")
    neo4j = Neo4jClient()
    neo4j.query("MATCH (n) DETACH DELETE n")
    neo4j.ingest_schema(schema_data)
    neo4j.ingest_rules(schema_data)
    neo4j.ingest_recursive_rules()

    # ── Step 3: Generate and ingest LLM-derived ontology ──────────────────
    print("[3/5] Generating Domain Ontology using LLM...")
    llm       = LLMClient()
    ontologist = DomainOntologist(llm)
    ontology  = ontologist.extract_ontology(schema_data)
    ontologist.save_ontology_to_json(ontology, "data/ontology_summary.json")
    neo4j.ingest_ontology(ontology)

    # ── Step 3b: Load Ontology Catalogue into Neo4j (NEW) ─────────────────
    print("[3b/5] Loading Ontology Catalogue into Neo4j...")
    from app.utils.sync_ontology_catalogue import (
        load_ontology_catalogue,
        ingest_ontology_catalogue,
    )
    try:
        ont_catalogue = load_ontology_catalogue()
        ingest_ontology_catalogue(neo4j, ont_catalogue, info_db_path="data/info.db")
    except FileNotFoundError as exc:
        print(
            f"  ⚠ Ontology catalogue not found — skipping. ({exc})\n"
            f"  Place ontology_catalogue.json in the catalogue/ directory "
            f"or set ONTOLOGY_CATALOGUE_PATH in .env to enable deterministic "
            f"column-level grounding."
        )

    # Close Neo4j connection after all graph operations are complete
    neo4j.close()

    # ── Step 4: Update Vector Search Index ────────────────────────────────
    print("[4/5] Updating Vector Search Index...")
    vector = VectorClient()
    vector.clear()
    vector.upsert_metadata(schema_data)

    print("\n[5/5] Synchronization complete.")
    print("The system is now ready to query the new database.\n")


if __name__ == "__main__":
    sync_database()