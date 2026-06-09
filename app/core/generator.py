from app.utils.llm import LLMClient
from typing import Dict, Any, List
import json
import re
from app.utils.plan_helper import parse_plan


class SQLGenerator:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def generate_plan_and_sql(
        self, query: str, intent: Dict[str, Any], context: str
    ) -> tuple[Dict[str, Any], str]:
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

### PROTOCOL 2b: ONTOLOGY COLUMN ALIAS ENFORCEMENT
If the context contains an 'ONTOLOGY COLUMN ALIASES' section, those mappings are AUTHORITATIVE.
Rules:
  - When a user term (e.g. "customer name") appears mapped to a specific column
    (e.g. Customers.ContactName), you MUST use that exact column in your SELECT
    and WHERE clauses.
  - You are FORBIDDEN from substituting any other column from the same table,
    even if another column name appears more similar to the user's phrase.
  - When a mapping shows a CONCAT expression (e.g. FirstName || ' ' || LastName),
    use that exact expression — do not select only one of the constituent columns.
  - When a mapping shows a SQL Expression for a metric, use it verbatim —
    do not reconstruct the formula from scratch.
  - Context-dependent mappings include a HINT — read the hint and apply the
    appropriate column based on the filter values in the query.

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

        from app.state import USE_CLIENT_DOC
        if USE_CLIENT_DOC:
            from app.utils.metadata_loader import get_relevant_rules
            field_meta     = get_relevant_rules(intent)
            meta_block     = (
                "\n### FIELD METADATA\n```json\n"
                + json.dumps(field_meta, indent=2)
                + "\n```"
            )
            prompt_final = prompt + meta_block
        else:
            heuristics_block = (
                "\n### HEURISTICS\n"
                "Use deterministic name-based heuristics for joins and rounding."
            )
            prompt_final = prompt + heuristics_block

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": prompt_final},
        ]

        response = self.llm.chat(messages)

        # Parse plan
        plan = parse_plan(response)
        if not isinstance(plan, dict):
            plan = {"error": "Failed to parse plan"}

        # Parse SQL
        from app.utils.sql_helper import clean_response
        sql = clean_response(response)

        # Heuristic post-processing (non-doc mode only)
        if not USE_CLIENT_DOC:
            from app.utils.heuristic_rules import enforce_integer_measures, enforce_precision
            sql = enforce_integer_measures(sql)
            sql = enforce_precision(sql, {})

        if not sql:
            sql = self._fallback_sql_from_intent(intent, context)

        return plan, sql

    # ──────────────────────────────────────────────────────────────────────────

    def _fallback_sql_from_intent(self, intent: Dict[str, Any], context: str) -> str:
        """
        Generate a generic fallback SELECT based on intent and the provided
        schema context string.  Used when the primary LLM extraction fails.
        """
        try:
            schema = json.loads(context)
        except Exception:
            return "SELECT * FROM sqlite_master LIMIT 5"

        table_name = None
        if intent.get("entities"):
            ent        = intent["entities"][0]
            table_name = ent.rstrip("s").capitalize()

        table_info = None
        if table_name:
            for tbl in schema.get("tables", []):
                if tbl.get("name", "").lower() == table_name.lower():
                    table_info = tbl
                    break
        if not table_info:
            if schema.get("tables"):
                table_info = schema["tables"][0]
                table_name = table_info.get("name", "*")
            else:
                return "SELECT * LIMIT 5"

        def matches(col_name, keywords):
            lname = col_name.lower()
            return any(k.lower() in lname for k in keywords)

        metrics    = intent.get("metrics", [])
        dimensions = intent.get("dimensions", [])
        cols_set   = []
        for col in table_info.get("columns", []):
            col_name = col.get("name")
            if not col_name:
                continue
            if matches(col_name, metrics) or matches(col_name, dimensions):
                cols_set.append(col_name)
        if not cols_set:
            pk = table_info.get("primary_key", [])
            if pk:
                cols_set.extend(pk)

        select_clause = "*" if not cols_set else ", ".join(cols_set)
        order_clause  = ""
        for col in cols_set:
            if metrics and any(m.lower() in col.lower() for m in metrics):
                order_clause = f" ORDER BY {col} DESC"
                break

        return f"SELECT {select_clause} FROM {table_name}{order_clause} LIMIT 5"

    # ──────────────────────────────────────────────────────────────────────────

    def generate_sql_only(
        self,
        query: str,
        plan: Dict[str, Any],
        context: str,
        error: str,
    ) -> str:
        """
        Lighter self-correction path used during retry attempts.
        Bypasses plan reconstruction to conserve tokens.
        """
        system_prompt = """You are a Universal SQL Generation Agent. Your goal is to convert Natural Language queries into valid SQLite SQL using a Protocol-Driven approach.

### PROTOCOL 1: VALUE LOCKING
If a term from the user's query is listed in the 'SEMANTIC VALUE MAPPINGS' section of the context, you MUST use the corresponding 'Exact Value' provided in your WHERE clause.

### PROTOCOL 2: ONTOLOGY COLUMN ALIAS ENFORCEMENT
If the context contains an 'ONTOLOGY COLUMN ALIASES' section, those mappings are AUTHORITATIVE.
You MUST use the exact columns or SQL expressions specified. Do NOT substitute similar columns.

### PROTOCOL 3: CANONICAL GROUNDING & JOIN PATHS
Use only tables and columns listed in 'CANONICAL SCHEMA DEFINITIONS'. Use the suggested join paths.

### PROTOCOL 4: GRAIN PRESERVATION & DETAIL CTEs
Define base CTEs for detail tables to prevent 'Summation Explosion'.

### PROTOCOL 5: AUDIT & CORRECTION
The previous query failed. Re-audit your join paths and schema. Fix by carefully re-examining columns and table constraints.

Return ONLY the SQL code inside standard ```sql ... ``` markdown blocks."""

        prompt = f"""
        User Query: {query}
        Execution Plan: {json.dumps(plan, indent=2)}
        Schema Context:
        {context}

        ### CRITICAL: PREVIOUS ATTEMPT FAILED
        ERROR: {error}
        ACTION REQUIRED: Your previous SQL had a schema/execution error. Re-read the CONTEXT carefully.
        The column you used might belong to a DIFFERENT table. Check the ONTOLOGY COLUMN ALIASES
        section first — it specifies the exact columns to use for each user term.
        Fix the join path and column locations accordingly.
        """

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": prompt},
        ]

        sql = self.llm.chat(messages)

        from app.utils.sql_helper import clean_response
        return clean_response(sql)
