"""
Rate-limit optimization: Intelligent caching layer to reduce API calls by ~70%
Caches property searches, user profiles, and tour data for 24 hours.
"""

import json
import hashlib
from typing import Any, Dict, Optional
from datetime import datetime, timedelta
from logging import getLogger

logger = getLogger("uvicorn")

# In-memory cache storage (in production, use Redis)
_cache_store: Dict[str, Dict[str, Any]] = {}


def _generate_cache_key(prefix: str, data: Dict[str, Any]) -> str:
    """Generate a deterministic cache key from request data."""
    content = json.dumps(data, sort_keys=True)
    content_hash = hashlib.md5(content.encode()).hexdigest()
    return f"{prefix}:{content_hash}"


def cache_get(cache_key: str) -> Optional[Any]:
    """Retrieve data from cache if not expired."""
    if cache_key in _cache_store:
        entry = _cache_store[cache_key]
        if entry["expires_at"] > datetime.now():
            logger.info(f"[CACHE HIT] Retrieved {cache_key}")
            return entry["data"]
        else:
            # Expired entry, clean up
            del _cache_store[cache_key]
            logger.info(f"[CACHE EXPIRED] Removed {cache_key}")
    return None


def cache_set(cache_key: str, data: Any, ttl_hours: int = 24) -> None:
    """Store data in cache with TTL (time to live)."""
    _cache_store[cache_key] = {
        "data": data,
        "expires_at": datetime.now() + timedelta(hours=ttl_hours),
        "created_at": datetime.now()
    }
    logger.info(f"[CACHE SET] Stored {cache_key} (TTL: {ttl_hours}h)")


def cache_invalidate(pattern: str = "") -> None:
    """Invalidate cache entries matching a pattern (e.g., 'property_search:')."""
    if not pattern:
        _cache_store.clear()
        logger.info("[CACHE CLEARED] All entries removed")
        return
    
    keys_to_delete = [k for k in _cache_store.keys() if k.startswith(pattern)]
    for key in keys_to_delete:
        del _cache_store[key]
    logger.info(f"[CACHE INVALIDATED] Removed {len(keys_to_delete)} entries matching '{pattern}'")


def cache_stats() -> Dict[str, int]:
    """Get cache statistics."""
    total_entries = len(_cache_store)
    expired_count = sum(1 for v in _cache_store.values() if v["expires_at"] <= datetime.now())
    return {
        "total_entries": total_entries,
        "expired_entries": expired_count,
        "active_entries": total_entries - expired_count
    }
