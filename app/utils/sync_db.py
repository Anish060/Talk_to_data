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
    """Persist catalogue rule metadata to SQLite"""
    conn = sqlite3.connect(info_db_path)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS column_rules (
        table_name TEXT,
        column_name TEXT,
        rule_id TEXT,
        rule_type TEXT,
        rule_sql TEXT
    )''')
    for tbl in schema_data.get('tables', []):
        for col in tbl.get('columns', []):
            field = f"{tbl['name']}.{col['name']}"
            rule = get_rule_for_field(field)
            if rule:
                cur.execute(
                    'INSERT INTO column_rules VALUES (?,?,?,?,?)',
                    (tbl['name'], col['name'], rule['id'], rule['type'], json.dumps(rule.get('sql_rule')))
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
            os.environ["CLIENT_DOC_PATH"] = doc_path
            os.environ["USE_CLIENT_DOC"] = "true"
            print(f"Client documentation will be used from: {doc_path}")
        else:
            print("File not found. Continuing without client documentation.")
    else:
        os.environ["USE_CLIENT_DOC"] = "false"
    
    # 1. Extract Schema
    print("[1/4] Extracting metadata using SQLAlchemy...")
    extractor = SchemaExtractor(db_url)
    schema_data = extractor.get_schema_summary()
    extractor.save_schema_to_json("data/schema_summary.json")
    extractor.save_to_info_db(schema_data, "data/info.db")
    _store_rules_sqlite("data/info.db", schema_data)
    
    # 2. Update Knowledge Graph
    print("[2/4] Updating Neo4j Knowledge Graph...")
    neo4j = Neo4jClient()
    neo4j.query("MATCH (n) DETACH DELETE n")
    neo4j.ingest_schema(schema_data)
    neo4j.ingest_rules(schema_data)
    neo4j.ingest_recursive_rules()
    
    # 3. Generate and Ingest Ontology
    print("[3/4] Generating Domain Ontology using LLM...")
    llm = LLMClient()
    ontologist = DomainOntologist(llm)
    ontology = ontologist.extract_ontology(schema_data)
    ontologist.save_ontology_to_json(ontology, "data/ontology_summary.json")
    neo4j.ingest_ontology(ontology)
    neo4j.close()
    
    # 4. Update Vector Index
    print("[4/4] Updating Vector Search Index...")
    vector = VectorClient()
    vector.clear()
    vector.upsert_metadata(schema_data)
    
    print("\n--- Synchronization Complete! ---")
    print("The system is now ready to query the new database.")

if __name__ == "__main__":
    sync_database()

'''
import json
from dotenv import load_dotenv
from app.core.extractor import SchemaExtractor
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient
from app.core.ontologist import DomainOntologist
from app.utils.llm import LLMClient

load_dotenv()

def sync_database():
    db_url = os.getenv("DATABASE_URL")
    print(f"--- Synchronizing System with Database: {db_url} ---")
    
    # Prompt for client documentation mode
    use_doc = input("Enable client documentation mode? (yes/no): ").strip().lower()
    if use_doc == "yes":
        doc_path = input("Enter the absolute path to the documentation file: ").strip()
        if os.path.isfile(doc_path):
            os.environ["CLIENT_DOC_PATH"] = doc_path
            os.environ["USE_CLIENT_DOC"] = "true"
            print(f"Client documentation will be used from: {doc_path}")
        else:
            print("File not found. Continuing without client documentation.")
    else:
        os.environ["USE_CLIENT_DOC"] = "false"

    # 1. Extract Schema
    print("[1/4] Extracting metadata using SQLAlchemy...")
    extractor = SchemaExtractor(db_url)
    schema_data = extractor.get_schema_summary()
    extractor.save_schema_to_json("data/schema_summary.json")
    extractor.save_to_info_db(schema_data, "data/info.db")
    
    # 2. Update Knowledge Graph
    print("[2/4] Updating Neo4j Knowledge Graph...")
    neo4j = Neo4jClient()
    # NEW: Clear existing graph to ensure domain purity
    neo4j.query("MATCH (n) DETACH DELETE n")
    neo4j.ingest_schema(schema_data)
    
    # 3. Generate and Ingest Ontology
    print("[3/4] Generating Domain Ontology using LLM...")
    llm = LLMClient()
    ontologist = DomainOntologist(llm)
    ontology = ontologist.extract_ontology(schema_data)
    ontologist.save_ontology_to_json(ontology, "data/ontology_summary.json")
    neo4j.ingest_ontology(ontology)
    neo4j.close()
    
    # 4. Update Vector Index
    print("[4/4] Updating Vector Search Index...")
    vector = VectorClient()
    vector.clear()
    vector.upsert_metadata(schema_data)
    
    print("\n--- Synchronization Complete! ---")
    print("The system is now ready to query the new database.")

if __name__ == "__main__":
    sync_database()
'''