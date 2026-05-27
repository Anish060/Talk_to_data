import os
from pathlib import Path
import logging
logging.basicConfig(level=logging.INFO)
from dotenv import load_dotenv

# Load environment variables from .env if it exists
env_path = Path(__file__).parents[1] / ".env"
load_dotenv(dotenv_path=env_path)

# Configuration flags
USE_CLIENT_DOC = os.getenv('USE_CLIENT_DOC', 'false').lower() == 'true'
CLIENT_DOC_PATH = os.getenv('CLIENT_DOC_PATH')
import logging
logging.getLogger("uvicorn.error").info(f"[state] USE_CLIENT_DOC={USE_CLIENT_DOC}, CLIENT_DOC_PATH={CLIENT_DOC_PATH}")

# Loaded catalogue (cached after first load)
_catalogue = None

def get_catalogue():
    """Load and cache the client documentation catalogue if enabled.
    Returns a dict representing the catalogue, or an empty dict if not available.
    """
    global _catalogue
    if not USE_CLIENT_DOC:
        return {}
    if _catalogue is not None:
        return _catalogue
    if not CLIENT_DOC_PATH:
        return {}
    try:
        import json
        import yaml
        with open(CLIENT_DOC_PATH, 'r', encoding='utf-8') as f:
            if CLIENT_DOC_PATH.lower().endswith(('.yaml', '.yml')):
                _catalogue = yaml.safe_load(f)
            else:
                _catalogue = json.load(f)
    except Exception as e:
        print(f"[state] Failed to load client catalogue: {e}")
        _catalogue = {}
    return _catalogue

def sync_db():
    use_doc = input("Would you like to use client documentation? (yes/no): ").strip().lower()
    if use_doc == "yes":
        doc_path = input("Enter the absolute path to the documentation file: ").strip()
        if os.path.isfile(doc_path):
            # Persist to .env file
            with open('.env', 'a', encoding='utf-8') as env_file:
                env_file.write(f"\nUSE_CLIENT_DOC=true\nCLIENT_DOC_PATH={doc_path}\n")
            # Also set in current process env for this run
            os.environ["CLIENT_DOC_PATH"] = doc_path
            os.environ["USE_CLIENT_DOC"] = "true"
            print(f"Client documentation will be used from: {doc_path}")
        else:
            print("File not found. Continuing without client documentation.")
    else:
        # Persist false setting
        with open('.env', 'a', encoding='utf-8') as env_file:
            env_file.write("\nUSE_CLIENT_DOC=false\n")
        os.environ["USE_CLIENT_DOC"] = "false"
