import hashlib
import json
import os

CACHE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "llm_cache"
)


def _cache_key(stage, payload):

    raw = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()

    return f"{stage}_{digest}"


def get_cached(stage, payload):

    key = _cache_key(stage, payload)
    path = os.path.join(CACHE_DIR, f"{key}.json")

    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def set_cached(stage, payload, result):

    os.makedirs(CACHE_DIR, exist_ok=True)

    key = _cache_key(stage, payload)
    path = os.path.join(CACHE_DIR, f"{key}.json")

    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    except Exception as e:
        print(f"Failed to write cache: {e}")