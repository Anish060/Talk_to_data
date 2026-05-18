from app.utils.llm import LLMClient
import json
from typing import Dict, Any

class QueryPlanner:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def generate_plan(self, query: str, intent: Dict[str, Any], context: str) -> Dict[str, Any]:
        system_prompt = """You are a Strategic Query Planner. Your goal is to break down a natural language request into logical execution steps for a database.

### PLANNING RULES:
1. DO NOT assume columns exist. Refer only to the 'SCHEMA CONTEXT' provided.
2. If a join is required between two distant tables, identify the need for a 'Bridge Table' based on the schema.
3. Keep steps high-level (e.g., 'Aggregate total revenue', 'Filter by date').
4. Return a JSON object with:
   - 'steps': List of logical steps.
   - 'tables_involved': List of tables required.
   - 'join_logic': High-level description of how tables relate.

Return ONLY JSON."""

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
