def clean_response(response: str) -> str:
    """
    Extract the first SQL code block from a LLM response.
    Handles:
      - ```sql ... ```
      - ``` ... ```
    Returns stripped SQL string; if no code block found, returns the original response stripped.
    """
    if "```sql" in response:
        return response.split("```sql")[1].split("```")[0].strip()
    if "```" in response:
        return response.split("```")[1].split("```")[0].strip()
    return response.strip()
