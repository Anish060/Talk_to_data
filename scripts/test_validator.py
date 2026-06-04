import json
import os
import sys

# Add project root to PYTHONPATH
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(project_root)

from app.core.capability_validator import CapabilityValidator
from app.core.intent_extractor import IntentExtractor
from app.utils.llm import LLMClient
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient

# Setup LLM, Neo4j, and Vector Clients
llm = LLMClient()
neo4j = Neo4jClient()
vector = VectorClient()

# Initialize IntentExtractor and CapabilityValidator
intent_ext = IntentExtractor(llm)
validator = CapabilityValidator(neo4j_client=neo4j, info_db_path="data/info.db")

test_queries = [
    "Show customer names and order dates",
    "Show discounted revenue by customer",
    "Show products bought today",
    "Show customer favorite shoe color"
]

print("==================================================")
print("RUNNING CAPABILITY VALIDATION TESTS")
print("==================================================\n")

for q in test_queries:
    print(f"Query: \"{q}\"")
    intent = intent_ext.extract(q)
    print(f"Extracted Intent: {intent}")
    
    result = validator.validate(intent)
    print(f"Validation Result: accepted={result.accepted}")
    print("Breakdown:")
    print(validator.explain(result))
    print("-" * 50 + "\n")

# Safely close connections
neo4j.close()
