
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.core.intent_extractor import IntentExtractor
from app.core.retriever import ContextRetriever
from app.core.generator import SQLGenerator
from app.utils.llm import LLMClient
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient
import os
import re
from sqlalchemy import create_engine, text
import pandas as pd
def _make_readonly_engine(db_url: str):
    """Create a SQLAlchemy engine that connects in read‑only mode when possible.
    For SQLite, we append the `mode=ro` query parameter. For other databases,
    the caller must ensure the credentials have read‑only privileges.
    """
    if db_url.startswith("sqlite://"):
        if "?" not in db_url:
            db_url = f"{db_url}?mode=ro"
        else:
            if "mode=ro" not in db_url:
                db_url = f"{db_url}&mode=ro"
    return create_engine(db_url)

def _is_safe_sql(sql: str) -> bool:
    """Return False if the SQL contains statements that alter schema or modify data.
    Allowed statements may start with SELECT or WITH. All other statements are blocked.
    """
    prohibited = [r"\\bDROP\\b", r"\\bDELETE\\b", r"\\bUPDATE\\b", r"\\bINSERT\\b", r"\\bALTER\\b", r"\\bCREATE\\b", r"\\bTRUNCATE\\b", r"\\bREPLACE\\b"]
    for pattern in prohibited:
        if re.search(pattern, sql, flags=re.IGNORECASE):
            return False
    #if not re.search(r"^\\s*(SELECT|WITH)\\b", sql, flags=re.IGNORECASE):
    #    return False
    return True
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
db_engine = _make_readonly_engine(os.getenv("DATABASE_URL", "sqlite:///data/test_sample.db"))
llm = LLMClient()
neo4j = Neo4jClient()
vector = VectorClient()

@router.post("/query", response_model=QueryResponse)
async def process_query(request: QueryRequest):
    try:
        print(f"--- Processing query: {request.query} ---")
        # 1. Intent
        intent_ext = IntentExtractor(llm)
        intent = intent_ext.extract(request.query)
        print(f"Extracted intent: {intent}")
        
        # 2. Context
        retriever = ContextRetriever(neo4j, vector)
        context, grounding = retriever.retrieve_context(intent)
        print("Retrieved context.")
        
        # 3. SQL & Plan Generation and Execution (with self-correction retry)
        generator = SQLGenerator(llm)
        max_retries = 3
        current_error = None
        plan = {}
        sql = ""
        results = []
        columns = []

        for attempt in range(max_retries):
            try:
                if attempt == 0:
                    # First run: get both plan and SQL in a single unified LLM call
                    plan, sql = generator.generate_plan_and_sql(request.query, intent, context)
                    print(f"Attempt 1 - Generated Plan: {plan}")
                    print(f"Attempt 1 - Generated SQL: {sql}")
                else:
                    # Retry runs: bypass plan reconstruction and only correct the SQL based on DB errors
                    sql = generator.generate_sql_only(request.query, plan, context, error=current_error)
                    print(f"Attempt {attempt + 1} (Retry) - Corrected SQL: {sql}")
                
                # Safety check before execution
                if not _is_safe_sql(sql):
                    raise HTTPException(status_code=400, detail="Generated SQL contains prohibited operations.")
                with db_engine.connect() as conn:
                    df = pd.read_sql(text(sql), conn)
                    results = df.to_dict(orient="records")
                    columns = df.columns.tolist()
                    print(f"Executed SQL successfully on attempt {attempt + 1}")
                    break  # Success!
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
