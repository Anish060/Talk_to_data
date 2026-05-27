import logging
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def safe_str(value: Any) -> str:
    """Convert any value to a UTF‑8 string, replacing undecodable bytes.
    - Bytes are decoded with errors='replace'.
    - Other types are cast with str().
    Logs a warning if conversion required.
    """
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception as exc:
            logging.warning("Failed to decode bytes %r: %s", value[:20], exc)
            return "�"
    try:
        return str(value)
    except Exception as exc:
        logging.warning("Failed to cast %r to str: %s", value, exc)
        return "�"
