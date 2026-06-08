
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from app.core.intent_extractor import IntentExtractor
from app.core.retriever import ContextRetriever
from app.core.generator import SQLGenerator
from app.core.capability_validator import CapabilityValidator
from app.utils.llm import LLMClient
from app.db.neo4j_client import Neo4jClient
from app.db.vector_client import VectorClient
from app.db.redis_client import redis_client
import os
import re
import hashlib
import json
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
    cached: bool

# Local in-memory fallback for verified query results (when Redis is offline)
_local_verified_query_cache = {}

# Shared LLMClient (stateless, does not hold active connection sockets)
llm = LLMClient()

@router.post("/query", response_model=QueryResponse)
async def process_query(request: QueryRequest, http_request: Request):
    try:
        # Sanitize query and generate unique identifier hash
        query_sanitized = request.query.strip().lower()
        query_hash = hashlib.sha256(query_sanitized.encode('utf-8')).hexdigest()
        cache_key = f"verified_query:{query_hash}"

        # 1. Try Redis cache lookup
        if redis_client.is_connected:
            cached_data = redis_client.get(cache_key)
            if cached_data:
                payload = json.loads(cached_data)
                payload["cached"] = True
                print(f"⚡ [CACHE] Cache hit in Redis for query: '{request.query}'")
                return payload
        # 2. Try Local Memory cache lookup (if Redis is offline)
        elif query_hash in _local_verified_query_cache:
            payload = _local_verified_query_cache[query_hash].copy()
            payload["cached"] = True
            print(f"⚡ [CACHE] Cache hit in Local Memory for query: '{request.query}'")
            return payload

        # Retrieve active connection instances from application state
        db_engine = http_request.app.state.db_engine
        neo4j = http_request.app.state.neo4j
        vector = http_request.app.state.vector

        print(f"--- Processing query: {request.query} ---")
        # 1. Intent
        intent_ext = IntentExtractor(llm)
        intent = intent_ext.extract(request.query)
        print(f"Extracted intent: {intent}")
        
        # 2. Schema Capability Check
        validator = CapabilityValidator(neo4j_client=neo4j, vector_client=vector, info_db_path="data/info.db")
        val_result = validator.validate(intent)
        print(f"Capability Validation Result: accepted={val_result.accepted}")
        print(validator.explain(val_result))
        
        if not val_result.accepted:
            raise HTTPException(
                status_code=400,
                detail=val_result.rejection_reason
            )
        
        # 3. Context
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
            
        # 3. Compile the response payload (marking this initial run as cached=False)
        response_payload = {
            "intent": intent,
            "plan": plan,
            "sql": sql,
            "grounding": grounding,
            "results": results,
            "columns": columns,
            "cached": False
        }

        # 4. Save to cache database for subsequent identical queries
        if redis_client.is_connected:
            redis_client.setex(cache_key, 86400, json.dumps(response_payload))
        else:
            _local_verified_query_cache[query_hash] = response_payload

        return response_payload
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
