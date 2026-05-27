import json
import os
import pathlib
from typing import Dict, List, Optional


def _resolve_path() -> Optional[pathlib.Path]:
    """Resolve the catalogue JSON file path.
    Preference order:
    1. CLIENT_DOC_PATH env variable (if set and exists)
    2. Most recently modified catalogue file in the project's catalogue directory.
    """
    env_path = os.getenv('CLIENT_DOC_PATH')
    if env_path and pathlib.Path(env_path).exists():
        return pathlib.Path(env_path)
    # Look for catalogue files in the project catalogue directory
    base_dir = pathlib.Path(__file__).resolve().parents[2] / 'catalogue'
    candidates = [base_dir / 'field_catalogue.json', base_dir / 'field_catalogue2.json']
    existing = [p for p in candidates if p.exists()]
    if not existing:
        return None
    # Return the most recently modified file
    return max(existing, key=lambda p: p.stat().st_mtime)

# Load the catalogue at import time
_CAT_PATH = _resolve_path()
if not _CAT_PATH:
    raise FileNotFoundError('No catalogue JSON found. Set CLIENT_DOC_PATH in .env or place a catalogue file in the project.')

with open(_CAT_PATH, 'r', encoding='utf-8') as f:
    _CATALOGUE = json.load(f)


def get_rule_for_field(field: str) -> Optional[Dict]:
    """Return the rule dict that applies to a given fully qualified field name.
    The field should be in the form 'Table.Column'.
    """
    for rule in _CATALOGUE.get('rules', []):
        if field in rule.get('applies_to', []):
            return rule
    return None


def get_default(key: str):
    """Retrieve a default value from the catalogue's defaults section."""
    return _CATALOGUE.get('defaults', {}).get(key)


def all_rules() -> List[Dict]:
    """Return the list of all rule dictionaries from the catalogue."""
    return _CATALOGUE.get('rules', [])
