from app.utils.llm import LLMClient
from typing import Dict, Any, List
import json

class SQLGenerator:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def generate_sql(self, query: str, plan: Dict[str, Any], context: str, error: str = None) -> str:
        system_prompt = """You are a Universal SQL Generation Agent. Your goal is to convert Natural Language queries into valid SQLite SQL using a Protocol-Driven approach.

### PROTOCOL 1: VALUE LOCKING
If a term from the user's query is listed in the 'SEMANTIC VALUE MAPPINGS' section of the context, you MUST use the corresponding 'Exact Value' provided in your WHERE clause. You are forbidden from modifying, generalizing, or simplifying these values.

### PROTOCOL 2: CANONICAL GROUNDING
ONLY use tables and columns explicitly listed in the 'CANONICAL SCHEMA DEFINITIONS' section. If a column is missing from a table's list, it DOES NOT exist in that table. Use the provided JOIN PATHS to navigate between tables.

### PROTOCOL 3: JOIN PATH ENFORCEMENT
You are STRICTLY FORBIDDEN from creating your own join keys or conditions. You MUST use the exact 'JOIN PATHS' provided in the context. If a path indicates a 'Bridge Table' (e.g., A -> B -> C), you MUST include table B in your joins. Never join A directly to C using unrelated columns like 'PurchaseOrderNumber'.

### PROTOCOL 4: ANTI-INFLATION CHECK (MATHEMATICAL ACCURACY)
If you join a Header table (e.g., SalesOrderHeader) with a Detail table (e.g., SalesOrderDetail), DO NOT SUM the Header-level totals (e.g., TotalDue). This causes 'Summation Explosion' where the total is multiplied by the number of line items. Instead, either:
1. SUM the granular line items (e.g., LineTotal).
2. Use a CTE/Sub-query to aggregate Header values before joining.

### PROTOCOL 5: AUDIT & EXECUTE
1. Audit the schema, join paths, and mathematical logic for accuracy.
2. Generate optimized SQLite code using clear table aliases.
3. If an ERROR FEEDBACK is provided, re-audit the join paths and math.

Return ONLY the SQL code."""

        prompt = f"""
        User Query: {query}
        Execution Plan: {json.dumps(plan, indent=2)}
        Schema Context:
        {context}
        """
        
        if error:
            prompt += f"\n\n### CRITICAL: PREVIOUS ATTEMPT FAILED\n"
            prompt += f"ERROR: {error}\n"
            prompt += "ACTION REQUIRED: Your previous SQL had a schema error. Re-read the CONTEXT carefully. The column you used might belong to a DIFFERENT table. Fix the join path and column locations."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
        
        sql = self.llm.chat(messages)
        
        # Cleanup markdown formatting if present
        if "```sql" in sql:
            sql = sql.split("```sql")[1].split("```")[0].strip()
        elif "```" in sql:
            sql = sql.split("```")[1].split("```")[0].strip()
            
        return sql

if __name__ == "__main__":
    pass
