import os
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
    
    # 1. Extract Schema
    print("[1/4] Extracting metadata using SQLAlchemy...")
    extractor = SchemaExtractor(db_url)
    schema_data = extractor.get_schema_summary()
    extractor.save_schema_to_json("data/schema_summary.json")
    
    # 2. Update Knowledge Graph
    print("[2/4] Updating Neo4j Knowledge Graph...")
    neo4j = Neo4jClient()
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
    vector.upsert_metadata(schema_data)
    
    print("\n--- Synchronization Complete! ---")
    print("The system is now ready to query the new database.")

if __name__ == "__main__":
    sync_database()
