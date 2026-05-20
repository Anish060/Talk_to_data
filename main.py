from app.core.extractor import SchemaExtractor
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient
from app.utils.llm import LLMClient
from app.core.intent_extractor import IntentExtractor
from app.core.retriever import ContextRetriever
from app.core.planner import QueryPlanner
from app.core.generator import SQLGenerator
import json
import os
from sqlalchemy import create_engine, text

def run_pipeline(user_query: str):
    print(f"\n--- Processing Query: {user_query} ---")
    
    # 1. Initialize Clients
    llm = LLMClient() # Defaults to OpenAI if key exists, else error
    neo4j = Neo4jClient()
    vector = VectorClient()
    
    # 2. Extract Intent
    print("[1/5] Extracting Intent...")
    extractor = IntentExtractor(llm)
    intent = extractor.extract(user_query)
    print(f"Intent: {json.dumps(intent, indent=2)}")
    
    # 3. Retrieve Context
    print("[2/5] Retrieving Context...")
    retriever = ContextRetriever(neo4j, vector)
    context = retriever.retrieve_context(intent)
    print(f"Context Found:\n{context}")
    
    # 4. Generate Plan
    print("[3/5] Generating Query Plan...")
    planner = QueryPlanner(llm)
    plan = planner.generate_plan(user_query, intent, context)
    print(f"Plan: {json.dumps(plan, indent=2)}")
    
    # 5. Generate SQL
    print("[4/5] Generating SQL...")
    generator = SQLGenerator(llm)
    sql = generator.generate_sql(user_query, plan, context)
    print(f"Generated SQL:\n{sql}")
    
    # 6. Execution (Optional/Safety check first)
    print("[5/5] Executing Query...")
    try:
        engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///data/test_sample.db"))
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            rows = result.fetchall()
            print(f"Results ({len(rows)} rows):")
            for row in rows[:5]: # Show first 5
                print(row)
    except Exception as e:
        print(f"Execution Error: {e}")

if __name__ == "__main__":
    # Ensure data is ready (one-time setup if needed)
    run_pipeline("List the top 3 customers by total revenue from German-supplied products shipped through United Package. For each customer, display customer name, country, total revenue, distinct product count, most purchased category, and first order date.")
    # For now, just print ready
    print("Pipeline ready. Run with a query to test.")
