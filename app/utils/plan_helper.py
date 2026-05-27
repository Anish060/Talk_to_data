import json
import re

def parse_plan(response: str) -> dict:
    """Extract a logical plan JSON from an LLM response.
    Attempts to locate a markdown block titled "LOGICAL PLAN" containing a JSON payload.
    If not found, falls back to the first JSON object present in the text.
    Returns a dict on success or an error dict on failure.
    """
    # Primary: look for a fenced JSON block after the LOGICAL PLAN header
    plan_match = re.search(r"### LOGICAL PLAN\s*```json\s*(.*?)\s*```", response, re.DOTALL | re.IGNORECASE)
    if not plan_match:
        # Second attempt: any JSON fenced block
        plan_match = re.search(r"```json\s*(.*?)\s*```", response, re.DOTALL | re.IGNORECASE)
    if plan_match:
        try:
            return json.loads(plan_match.group(1).strip())
        except Exception as e:
            print(f"Error decoding plan JSON from fenced block: {e}")
    # Fallback: locate first JSON object in the raw text
    start = response.find('{')
    end = response.rfind('}')
    if 0 <= start < end:
        try:
            return json.loads(response[start:end+1])
        except Exception as e:
            print(f"Error decoding plan JSON from raw text: {e}")
    # If all attempts fail, return an error dict
    return {"error": "Failed to parse plan from response"}
