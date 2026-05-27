import json, os, sys

# Add project root to PYTHONPATH
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

from app.core.retriever import ContextRetriever
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient

# Sample intent that should span multiple tables
intent = {
    "entities": ["Customer", "Product"],
    "metrics": ["Revenue"],
    "dimensions": [],
    "filters": []
}

retriever = ContextRetriever(Neo4jClient(), VectorClient())
context, grounding = retriever.retrieve_context(intent)
print("--- Context (schema snippets) ---")
print(context)
print("--- Grounding mappings ---")
for g in grounding:
    print(g)
