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
    grounding: list
    results: list
    columns: list

# Database engine and shared clients
db_engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///data/test_sample.db"))
llm = LLMClient()
neo4j = Neo4jClient()
vector = VectorClient()

@router.post("/query", response_model=QueryResponse)
async def process_query(request: QueryRequest):
    try:
        print(f"--- Processing query: {request.query} ---")
        print(f"Processing query: {request.query}")
        # 1. Intent
        intent_ext = IntentExtractor(llm)
        intent = intent_ext.extract(request.query)
        print(f"Extracted intent: {intent}")
        
        # 2. Context
        retriever = ContextRetriever(neo4j, vector)
        context, grounding = retriever.retrieve_context(intent)
        print("Retrieved context.")
        
        # 3. Plan
        planner = QueryPlanner(llm)
        plan = planner.generate_plan(request.query, intent, context)
        print(f"Generated plan: {plan}")
        
        # 4. SQL & Execute with Self-Correction Loop
        generator = SQLGenerator(llm)
        max_retries = 3
        current_error = None
        sql = ""
        results = []
        columns = []

        for attempt in range(max_retries):
            try:
                sql = generator.generate_sql(request.query, plan, context, error=current_error)
                print(f"Attempt {attempt + 1} - Generated SQL: {sql}")
                
                with db_engine.connect() as conn:
                    df = pd.read_sql(text(sql), conn)
                    results = df.to_dict(orient="records")
                    columns = df.columns.tolist()
                    print(f"Executed SQL successfully on attempt {attempt + 1}")
                    break # Success!
            except Exception as e:
                current_error = str(e)
                print(f"SQL Execution failed (Attempt {attempt + 1}): {current_error}")
                if attempt == max_retries - 1:
                    raise HTTPException(status_code=500, detail=f"Failed to generate valid SQL after {max_retries} attempts. Last error: {current_error}")
            
        return {
            "intent": intent,
            "plan": plan,
            "sql": sql,
            "grounding": grounding,
            "results": results,
            "columns": columns
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
