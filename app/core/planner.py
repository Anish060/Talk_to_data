from app.utils.llm import LLMClient
import json
from typing import Dict, Any

class QueryPlanner:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def generate_plan(self, query: str, intent: Dict[str, Any], context: str) -> Dict[str, Any]:
        system_prompt = """
        You are an expert SQL Architect. Your goal is to create a logical execution plan for a database query based on user intent and schema context.
        The plan should define the tables, joins, filters, and aggregations needed without writing the final SQL yet.
        
        Return a JSON object:
        - steps: List of logical steps (e.g., "Join users and orders", "Filter by date").
        - tables_involved: List of table names.
        - join_conditions: List of join pairs (e.g., ["orders.CustomerID = customers.CustomerID"]). ALWAYS use the 'Suggested Join Paths' from the schema context if provided.
        - aggregations: List of aggregation operations.
        - final_columns: Columns to include in the final result.
        """

        prompt = f"""
        User Query: {query}
        Extracted Intent: {json.dumps(intent, indent=2)}
        Schema Context:
        {context}
        
        Generate the logical execution plan.
        """

        json_str = self.llm.extract_json(prompt, system_prompt)
        try:
            return json.loads(json_str)
        except Exception as e:
            return {"error": "Failed to parse plan", "raw": json_str}

if __name__ == "__main__":
    print("QueryPlanner loaded.")
