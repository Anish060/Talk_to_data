import os, sys, json

# Set project root (directory containing this script's parent folder)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

from app.db.neo4j_client import Neo4jClient

def main():
    # Locate JSON files in the top-level data folder
    data_dir = os.path.join(project_root, "data")
    ontology_path = os.path.join(data_dir, "ontology_summary.json")
    schema_path = os.path.join(data_dir, "schema_summary.json")

    if not os.path.isfile(ontology_path):
        print(f"Ontology file not found: {ontology_path}")
        return
    if not os.path.isfile(schema_path):
        print(f"Schema file not found: {schema_path}")
        return

    with open(ontology_path, "r", encoding="utf-8") as f:
        ontology_data = json.load(f)
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_data = json.load(f)

    client = Neo4jClient()
    print("📥 Ingesting schema into Neo4j…")
    client.ingest_schema(schema_data)
    print("📥 Ingesting ontology into Neo4j…")
    client.ingest_ontology(ontology_data)
    client.close()
    print("✅ Ingestion complete.")

if __name__ == "__main__":
    main()
