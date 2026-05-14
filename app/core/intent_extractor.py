from app.utils.llm import LLMClient
import json
from typing import Dict, Any

class IntentExtractor:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def extract(self, query: str) -> Dict[str, Any]:
        system_prompt = """
        You are an expert data analyst. Your task is to analyze a natural language query and extract the underlying intent for a database query.
        Return a JSON object with the following fields:
        - entities: List of main business entities mentioned (e.g., customers, orders, products).
        - metrics: List of metrics or values to be calculated (e.g., revenue, count, average price).
        - filters: List of filters or conditions (e.g., "last quarter", "status is active").
        - dimensions: List of dimensions to group by (e.g., "by category", "per month").
        - sort: Sorting preference (e.g., "top 10", "descending").
        - aggregations: Any aggregations needed (e.g., SUM, AVG, COUNT).

        Example:
        Query: "Show the top 10 customers by revenue last quarter"
        Result: {
            "entities": ["customers"],
            "metrics": ["revenue"],
            "filters": ["last quarter"],
            "dimensions": ["customer"],
            "sort": "top 10 by revenue descending",
            "aggregations": ["SUM(revenue)"]
        }
        """

        prompt = f"Query: \"{query}\"\nResult:"
        
        json_str = self.llm.extract_json(prompt, system_prompt)
        try:
            return json.loads(json_str)
        except Exception as e:
            print(f"Error parsing intent JSON: {e}")
            return {"error": "Failed to parse intent", "raw": json_str}

if __name__ == "__main__":
    # Test with mock LLM response or live if possible
    # For now, just a class definition test
    print("IntentExtractor loaded.")
