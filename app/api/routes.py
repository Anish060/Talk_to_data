from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.intent_extractor import IntentExtractor
from app.core.retriever import ContextRetriever
from app.core.planner import QueryPlanner
from app.core.generator import SQLGenerator
from app.utils.llm import LLMClient
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient
import os
from sqlalchemy import create_engine, text
import pandas as pd

router = APIRouter()

class QueryRequest(BaseModel):
    query: str

class QueryResponse(BaseModel):
    intent: dict
    plan: dict
    sql: str
    results: list
    columns: list

# Database engine
db_engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///data/test_sample.db"))

@router.post("/query", response_model=QueryResponse)
async def process_query(request: QueryRequest):
    try:
        print(f"--- Processing query: {request.query} ---")
        
        # Initialize clients inside to catch errors
        llm = LLMClient()
        neo4j = Neo4jClient()
        vector = VectorClient()
        print(f"Processing query: {request.query}")
        # 1. Intent
        intent_ext = IntentExtractor(llm)
        intent = intent_ext.extract(request.query)
        print(f"Extracted intent: {intent}")
        
        # 2. Context
        retriever = ContextRetriever(neo4j, vector)
        context = retriever.retrieve_context(intent)
        print("Retrieved context.")
        
        # 3. Plan
        planner = QueryPlanner(llm)
        plan = planner.generate_plan(request.query, intent, context)
        print(f"Generated plan: {plan}")
        
        # 4. SQL
        generator = SQLGenerator(llm)
        sql = generator.generate_sql(request.query, plan, context)
        print(f"Generated SQL: {sql}")
        
        # 5. Execute
        with db_engine.connect() as conn:
            df = pd.read_sql(text(sql), conn)
            results = df.to_dict(orient="records")
            columns = df.columns.tolist()
            print(f"Executed SQL, found {len(results)} rows.")
            
        return {
            "intent": intent,
            "plan": plan,
            "sql": sql,
            "results": results,
            "columns": columns
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
