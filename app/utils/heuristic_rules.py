import re


def enforce_integer_measures(sql: str) -> str:
    """Round any numeric columns that are marked as integer measures to nearest integer.
    This is a simple post‑processing step that finds numeric literals or expressions
    and applies ROUND(..., 0)."""
    # Example regex to find numeric literals (simplified)
    pattern = re.compile(r"(\b\d+\.\d+\b)")
    return pattern.sub(lambda m: f"ROUND({m.group(1)}, 0)", sql)


def enforce_precision(sql: str, precision_map: dict) -> str:
    """Replace numeric columns with appropriate decimal precision based on a map.
    precision_map is expected to be {"column_name": 2, ...} indicating number of decimal places.
    """
    for col, prec in precision_map.items():
        # Find occurrences of the column in SELECT or ORDER BY and wrap with ROUND
        pattern = re.compile(rf"\b{col}\b", re.IGNORECASE)
        sql = pattern.sub(lambda _: f"ROUND({col}, {prec})", sql)
    return sql


def synonym_lookup(value: str, synonyms: dict) -> str:
    """Replace a value with its canonical identifier using a synonyms dict.
    synonyms = {"tube light": "C1780", ...}
    """
    lowered = value.lower()
    return synonyms.get(lowered, value)
