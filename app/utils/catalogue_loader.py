import json
import os
import pathlib
from typing import Dict, List, Optional
from app.db.redis_client import redis_client

# Local process-level in-memory cache fallback variables
_local_catalogue_cache = None
_local_rules_cache = {}

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

def _load_catalogue() -> dict:
    """Lazy load and cache the catalogue file.
    If Redis is active, caches data there. Otherwise, falls back to local in-memory cache.
    """
    global _local_catalogue_cache

    # 1. Redis Cache Path
    if redis_client.is_connected:
        cache_key = "catalogue:raw_data"
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
        
        cat_path = _resolve_path()
        if not cat_path:
            raise FileNotFoundError('No catalogue JSON found. Set CLIENT_DOC_PATH in .env or place a catalogue file in the project.')
        with open(cat_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Save to Redis
        redis_client.setex(cache_key, 86400, json.dumps(data))
        return data

    # 2. Local Fallback Cache Path
    if _local_catalogue_cache is not None:
        return _local_catalogue_cache
        
    cat_path = _resolve_path()
    if not cat_path:
        raise FileNotFoundError('No catalogue JSON found. Set CLIENT_DOC_PATH in .env or place a catalogue file in the project.')
    
    with open(cat_path, 'r', encoding='utf-8') as f:
        _local_catalogue_cache = json.load(f)
    return _local_catalogue_cache

def get_rule_for_field(field: str) -> Optional[Dict]:
    """Return the rule dict that applies to a given fully qualified field name.
    If Redis is active, uses Redis. Otherwise, falls back to local in-memory cache.
    """
    global _local_rules_cache

    # 1. Redis Cache Path
    if redis_client.is_connected:
        cache_key = f"catalogue:rule:{field}"
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached) if cached != "NONE" else None

        catalogue = _load_catalogue()
        for rule in catalogue.get('rules', []):
            if field in rule.get('applies_to', []):
                redis_client.setex(cache_key, 86400, json.dumps(rule))
                return rule
        redis_client.setex(cache_key, 86400, "NONE")
        return None

    # 2. Local Fallback Cache Path
    if field in _local_rules_cache:
        return _local_rules_cache[field]

    catalogue = _load_catalogue()
    for rule in catalogue.get('rules', []):
        if field in rule.get('applies_to', []):
            _local_rules_cache[field] = rule
            return rule
            
    _local_rules_cache[field] = None
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
    """Clear both the Redis cache keys and the local process variables to force a fresh reload."""
    global _local_catalogue_cache, _local_rules_cache
    
    # Reset local variables
    _local_catalogue_cache = None
    _local_rules_cache.clear()
    
    # Reset Redis keys
    if redis_client.is_connected:
        redis_client.clear_pattern("catalogue:*")

