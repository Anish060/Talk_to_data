import json
import os
import pathlib
from typing import Dict, List, Optional
from functools import lru_cache

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

@lru_cache(maxsize=1)
def _load_catalogue() -> dict:
    """Lazy load and cache the catalogue file."""
    cat_path = _resolve_path()
    if not cat_path:
        raise FileNotFoundError('No catalogue JSON found. Set CLIENT_DOC_PATH in .env or place a catalogue file in the project.')
    
    with open(cat_path, 'r', encoding='utf-8') as f:
        return json.load(f)

@lru_cache(maxsize=2048)
def get_rule_for_field(field: str) -> Optional[Dict]:
    """Return the rule dict that applies to a given fully qualified field name.
    The field should be in the form 'Table.Column'.
    """
    catalogue = _load_catalogue()
    for rule in catalogue.get('rules', []):
        if field in rule.get('applies_to', []):
            return rule
    return None

def get_default(key: str):
    """Retrieve a default value from the catalogue's defaults section."""
    catalogue = _load_catalogue()
    return catalogue.get('defaults', {}).get(key)

def all_rules() -> List[Dict]:
    """Return the list of all rule dictionaries from the catalogue."""
    catalogue = _load_catalogue()
    return catalogue.get('rules', [])

def clear_catalogue_cache() -> None:
    """Clear the in-memory cache to hot-reload the catalogue."""
    _load_catalogue.cache_clear()
    get_rule_for_field.cache_clear()
