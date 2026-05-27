import json
import os
from typing import Any, Dict

def load_client_documentation() -> Dict[str, Any]:
    """Legacy loader kept for backward compatibility – loads the whole catalogue."""
    from app.state import CLIENT_DOC_PATH
    if not CLIENT_DOC_PATH:
        return {}
    try:
        import yaml
        with open(CLIENT_DOC_PATH, "r", encoding="utf-8") as f:
            if CLIENT_DOC_PATH.lower().endswith((".yaml", ".yml")):
                return yaml.safe_load(f)
            else:
                return json.load(f)
    except Exception as e:
        print(f"[metadata_loader] Failed to load full catalogue: {e}")
        return {}

def get_relevant_rules(intent: Dict[str, Any]) -> Dict[str, Any]:
    """Return a trimmed catalogue containing only rules that apply to the given intent.
    This drastically reduces prompt size.
    """
    from app.state import CLIENT_DOC_PATH
    if not CLIENT_DOC_PATH:
        return {}
    try:
        import yaml
        with open(CLIENT_DOC_PATH, "r", encoding="utf-8") as f:
            full = yaml.safe_load(f) if CLIENT_DOC_PATH.lower().endswith((".yaml", ".yml")) else json.load(f)
    except Exception as e:
        print(f"[metadata_loader] Failed to load catalogue for filtering: {e}")
        return {}
    # Gather keywords from intent (entities, metrics, dimensions, trigger phrases)
    keywords = set()
    for key in ["entities", "metrics", "dimensions", "trigger_phrases"]:
        val = intent.get(key)
        if isinstance(val, list):
            keywords.update([str(v).lower() for v in val])
        elif isinstance(val, str):
            keywords.add(val.lower())
    # Add any raw strings from the intent dict values
    for v in intent.values():
        if isinstance(v, str):
            keywords.add(v.lower())
    # Filter rules
    filtered_rules = []
    for rule in full.get("rules", []):
        applies = False
        # Check if any rule's applies_to items contain a keyword
        for col in rule.get("applies_to", []):
            if any(kw in col.lower() for kw in keywords):
                applies = True
                break
        # Also check trigger_phrases against keywords
        if not applies:
            for phrase in rule.get("trigger_phrases", []):
                if any(kw in phrase.lower() for kw in keywords):
                    applies = True
                    break
        if applies:
            filtered_rules.append(rule)
    # Return a minimal catalogue structure
    return {
        "database": full.get("database"),
        "rule_group": full.get("rule_group"),
        "rules": filtered_rules,
        "defaults": full.get("defaults", {})
    }
