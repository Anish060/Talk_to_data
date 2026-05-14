from app.utils.llm import LLMClient
import json
from typing import Dict, Any, List

class DomainOntologist:
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client

    def extract_ontology(self, schema_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Uses LLM to identify high-level business concepts and synonyms for the schema.
        """
        system_prompt = """
        You are an expert Data Architect and Ontologist.
        Analyze the provided database schema and extract high-level business concepts.
        For each table, identify:
        1. The primary Business Concept it represents.
        2. Common synonyms or business terms for this concept.
        3. A brief description of what this entity represents in a business context.
        
        Return a JSON list of objects:
        [
          {
            "table_name": "actual_table_name",
            "concept": "Business Concept (e.g., Client, Transaction)",
            "synonyms": ["term1", "term2"],
            "description": "..."
          }
        ]
        """
        
        # We only pass table names and brief descriptions to avoid token limits
        schema_summary = []
        for t in schema_data["tables"]:
            schema_summary.append({
                "name": t["name"],
                "columns": [c["name"] for c in t["columns"][:5]] # Just first 5 columns for context
            })
            
        prompt = f"Analyze this schema and return the ontology JSON:\n{json.dumps(schema_summary, indent=2)}"
        
        response = self.llm.chat([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ])
        
        # Cleanup response
        try:
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()
            
            ontology = json.loads(response)
            return ontology
        except Exception as e:
            print(f"Error parsing ontology: {e}")
            return []

    def save_ontology_to_json(self, ontology: List[Dict[str, Any]], filepath: str):
        with open(filepath, 'w') as f:
            json.dump(ontology, f, indent=4)
        print(f"Ontology saved to {filepath}")

if __name__ == "__main__":
    from app.utils.llm import LLMClient
    client = LLMClient()
    ontologist = DomainOntologist(client)
    print("Ontologist loaded.")
