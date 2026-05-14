from typing import Dict, Any
import json

class SQLGenerator:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def generate_sql(self, query: str, plan: Dict[str, Any], context: str) -> str:
        system_prompt = """
        You are an expert SQL Developer. Convert the provided execution plan and user query into a single, optimized SQL query.
        Use clear aliases and follow best practices.
        Ensure the SQL is compatible with the database type described in the context (default to PostgreSQL/Standard SQL).
        Return ONLY the SQL code, no markdown blocks.
        """

        prompt = f"""
        User Query: {query}
        Execution Plan: {json.dumps(plan, indent=2)}
        Schema Context:
        {context}
        
        Generate SQL:
        """

        sql = self.llm.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ])
        
        # Cleanup
        if "```sql" in sql:
            sql = sql.split("```sql")[1].split("```")[0].strip()
        elif "```" in sql:
            sql = sql.split("```")[1].split("```")[0].strip()
            
        return sql

if __name__ == "__main__":
    print("SQLGenerator loaded.")
