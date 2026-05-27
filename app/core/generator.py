from app.utils.llm import LLMClient
from typing import Dict, Any, List
import json
import re
from app.utils.plan_helper import parse_plan

class SQLGenerator:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def generate_plan_and_sql(self, query: str, intent: Dict[str, Any], context: str) -> tuple[Dict[str, Any], str]:
        """
        Generates both the logical plan and the final SQL query in a single LLM call.
        Returns a tuple of (plan_dict, sql_string).
        """
        system_prompt = """You are a Strategic AI Data Engineer. Your goal is to convert Natural Language queries into valid SQLite SQL using a combined Protocol-Driven Planning and SQL Generation approach.

### PROTOCOL 1: STRATEGIC PLANNING
1. Analyze the 'SCHEMA DEFINITIONS' and 'SEMANTIC VALUE MAPPINGS' carefully.
2. Formulate a logical step-by-step execution plan. Keep steps high-level (e.g., 'Filter by date', 'Join Customers on CustomerID').
3. List the tables involved in the plan.
4. Describe the high-level join logic using only the provided join paths.

### PROTOCOL 2: VALUE LOCKING
If a term from the user's query is listed in the 'SEMANTIC VALUE MAPPINGS' section of the context, you MUST use the corresponding 'Exact Value' provided in your WHERE clause. You are forbidden from modifying, generalizing, or simplifying these values.

### PROTOCOL 3: CANONICAL GROUNDING
ONLY use tables and columns explicitly listed in the 'CANONICAL SCHEMA DEFINITIONS' section. If a column is missing from a table's list, it DOES NOT exist in that table. Use the provided JOIN PATHS to navigate between tables.

### PROTOCOL 4: JOIN PATH ENFORCEMENT
You are STRICTLY FORBIDDEN from creating your own join keys or conditions. You MUST use the exact 'JOIN PATHS' provided in the context. If a path indicates a 'Bridge Table' (e.g., A -> B -> C), you MUST include table B in your joins. Never join A directly to C using unrelated columns.

### PROTOCOL 5: GRAIN PRESERVATION & DETAIL CTEs
If a query involves a detail or line-item grain table in a 1:N relationship (e.g., Order Details, SalesOrderDetail, TransactionLines):
1. You MUST define a single base CTE at that line-item/detail grain.
2. Compute any line-level metrics (such as revenue, profit, quantity) ONCE inside this base CTE.
3. Perform all downstream aggregations (SUM, AVG, COUNT) ONLY from that base CTE or by joining other tables to it.
4. This preserves the grain, prevents cartesian row-multiplication from inflating metrics, and avoids the 'Summation Explosion'.

### PROTOCOL 6: AUDIT & EXECUTE
1. Audit the schema, join paths, and mathematical logic for accuracy.
2. Generate optimized SQLite code using clear table aliases.

### RESPONSE FORMAT:
You MUST respond exactly in the following format. Ensure the JSON and SQL are in their respective markdown blocks:

### LOGICAL PLAN
```json
{
  "steps": ["Step 1", "Step 2"],
  "tables_involved": ["TableA", "TableB"],
  "join_logic": "High-level join explanation..."
}
```

### GENERATED SQL
```sql
SELECT ...
```
"""

        prompt = f"""
        User Query: {query}
        Extracted Intent: {json.dumps(intent, indent=2)}
        Schema Context:
        {context}
        """

        # Build the message list, injecting field metadata when doc mode is active
        from app.state import USE_CLIENT_DOC
        if USE_CLIENT_DOC:
            from app.utils.metadata_loader import get_relevant_rules
            # Get only the rules that are relevant for the current intent to keep the prompt small
            field_meta = get_relevant_rules(intent)
            # Append a FIELD METADATA block (filtered) to the user prompt
            meta_block = "\n### FIELD METADATA\n```json\n" + json.dumps(field_meta, indent=2) + "\n```"
            prompt_with_meta = prompt + meta_block
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_with_meta}
            ]
        else:
            # Heuristic mode – add a minimal HEURISTICS block to guide the LLM
            heuristics_block = "\n### HEURISTICS\nUse deterministic name‑based heuristics for joins and rounding."
            prompt_with_heur = prompt + heuristics_block
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_with_heur}
            ]
        
        response = self.llm.chat(messages)

        # 1. Parse JSON Plan
        from app.utils.plan_helper import parse_plan
        plan = parse_plan(response)
        # Ensure plan is a dict
        if not isinstance(plan, dict):
            plan = {"error": "Failed to parse plan"}

        # 2. Parse SQL Query (now using clean_response helper)
        from app.utils.sql_helper import clean_response
        sql = clean_response(response)
        # Apply heuristic post‑processing when not using client doc mode
        from app.state import USE_CLIENT_DOC
        if not USE_CLIENT_DOC:
            from app.utils.heuristic_rules import enforce_integer_measures, enforce_precision
            # Example: enforce integer rounding for measures marked as integer (placeholder logic)
            sql = enforce_integer_measures(sql)
            # Precision map could be derived from intent or a static config; using empty dict here
            sql = enforce_precision(sql, {})
        # If extraction failed, fall back to a dynamic query based on intent and schema context
        if not sql:
            sql = self._fallback_sql_from_intent(intent, context)
        return plan, sql

    def _fallback_sql_from_intent(self, intent: Dict[str, Any], context: str) -> str:
        """Generate a generic fallback SELECT based on intent and the provided schema JSON.
        The context string is expected to be a JSON representation of the schema
        (as produced by SchemaExtractor). This method parses the JSON to discover
        table and column names, then builds a sensible query:
        * Table – first entity (plural stripped to singular, capitalized).
        * Columns – any columns whose name contains a metric or dimension keyword.
          If none match, selects '*'.
        * ORDER BY – first metric column (if any) descending.
        * LIMIT 5 – as requested by most "top‑N" queries.
        """
        try:
            schema = json.loads(context)
        except Exception:
            # If context is not JSON, fall back to a very generic query
            return "SELECT * FROM sqlite_master LIMIT 5"

        # Choose table name from entities
        table_name = None
        if intent.get("entities"):
            ent = intent["entities"][0]
            # naive singularisation (remove trailing 's' if present)
            table_name = ent.rstrip('s').capitalize()
        # Verify table exists in schema
        table_info = None
        for tbl in schema.get("tables", []):
            if tbl.get("name", "").lower() == table_name.lower():
                table_info = tbl
                break
        if not table_info:
            # fallback to first table in schema
            if schema.get("tables"):
                table_info = schema["tables"][0]
                table_name = table_info.get("name", "*")
            else:
                return "SELECT * LIMIT 5"

        # Gather candidate columns
        cols_set = []
        # helper to match keywords
        def matches(col_name, keywords):
            lname = col_name.lower()
            return any(k.lower() in lname for k in keywords)

        metrics = intent.get("metrics", [])
        dimensions = intent.get("dimensions", [])
        for col in table_info.get("columns", []):
            col_name = col.get("name")
            if not col_name:
                continue
            if matches(col_name, metrics) or matches(col_name, dimensions):
                cols_set.append(col_name)
        # Ensure we have at least one column
        if not cols_set:
            # add a primary key if present
            pk = table_info.get("primary_key", [])
            if pk:
                cols_set.extend(pk)
        # If still empty, select *
        select_clause = "*" if not cols_set else ", ".join(cols_set)
        # Order by first metric column if any
        order_clause = ""
        for col in cols_set:
            if metrics and any(m.lower() in col.lower() for m in metrics):
                order_clause = f" ORDER BY {col} DESC"
                break
        sql = f"SELECT {select_clause} FROM {table_name}{order_clause} LIMIT 5"
        return sql

    def generate_sql_only(self, query: str, plan: Dict[str, Any], context: str, error: str) -> str:
        """
        Lighter version of the generator used specifically during self-correction retries on SQL errors.
        Bypasses plan reconstruction to conserve tokens.
        """
        system_prompt = """You are a Universal SQL Generation Agent. Your goal is to convert Natural Language queries into valid SQLite SQL using a Protocol-Driven approach.

### PROTOCOL 1: VALUE LOCKING
If a term from the user's query is listed in the 'SEMANTIC VALUE MAPPINGS' section of the context, you MUST use the corresponding 'Exact Value' provided in your WHERE clause. You are forbidden from modifying, generalizing, or simplifying these values.

### PROTOCOL 2: CANONICAL GROUNDING & JOIN PATHS
Use only tables and columns listed in 'CANONICAL SCHEMA DEFINITIONS'. Use the suggested join paths.

### PROTOCOL 3: GRAIN PRESERVATION & DETAIL CTEs
Define base CTEs for detail tables to prevent 'Summation Explosion'.

### PROTOCOL 4: AUDIT & CORRECTION
Re-audit your join paths and schema. The previous query failed with an error. Fix the query by carefully re-examining the columns and table constraints.

Return ONLY the SQL code inside standard ```sql ... ``` markdown blocks."""

        prompt = f"""
        User Query: {query}
        Execution Plan: {json.dumps(plan, indent=2)}
        Schema Context:
        {context}

        ### CRITICAL: PREVIOUS ATTEMPT FAILED
        ERROR: {error}
        ACTION REQUIRED: Your previous SQL had a schema/execution error. Re-read the CONTEXT carefully. The column you used might belong to a DIFFERENT table. Fix the join path and column locations.
        """

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]

        sql = self.llm.chat(messages)

        # Cleanup markdown formatting using helper
        from app.utils.sql_helper import clean_response
        sql = clean_response(sql)
        return sql
