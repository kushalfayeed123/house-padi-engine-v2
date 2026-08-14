"""
Rate-limit optimization: caching layer to reduce API calls by ~70%.
Caches property searches, user profiles, and tour data.

Backend is pluggable via CacheBackend. Default is an in-memory LRU cache;
set UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN to switch to real
Redis with no other code changes.

All methods are async: the Redis backend makes real HTTP calls, and
awaiting them (rather than blocking) matters on a low-CPU host running
inside FastAPI's event loop.
"""

import hashlib
import json
import os
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from logging import getLogger
from typing import Any, Dict, Optional

logger = getLogger("uvicorn")

DEFAULT_TTL_HOURS = 24
# Only used by the in-memory backend — Redis handles its own expiry/eviction,
# so this cap exists purely to bound this process's own memory as a fallback.
MAX_IN_MEMORY_ENTRIES = int(os.getenv("CACHE_MAX_ENTRIES", "1000"))


class CacheBackend(ABC):
    """Storage-agnostic interface. All current callers pass already-
    JSON-encoded strings as `value` — this layer stores/returns them as-is
    and does no serialization of its own, so callers' return types don't
    change based on which backend is active."""

    @abstractmethod
    async def get(self, key: str) -> Optional[Any]: ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl_seconds: int) -> None: ...

    @abstractmethod
    async def delete_prefix(self, prefix: str) -> int: ...

    @abstractmethod
    async def clear(self) -> None: ...

    @abstractmethod
    async def stats(self) -> Dict[str, Any]: ...


class InMemoryCacheBackend(CacheBackend):
    """Bounded LRU cache — OrderedDict capped at `max_entries`, evicting
    least-recently-used once full, plus lazy TTL expiry on read. Pure
    Python/no I/O, so these are `async def` only to satisfy the interface —
    they return immediately, no actual awaiting happens."""

    def __init__(self, max_entries: int = MAX_IN_MEMORY_ENTRIES):
        self._store: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._max_entries = max_entries
        self._lock = threading.Lock()

    async def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry["expires_at"] <= datetime.now(timezone.utc):
                del self._store[key]
                logger.info(f"[CACHE EXPIRED] {key}")
                return None
            self._store.move_to_end(key)
            logger.info(f"[CACHE HIT] {key}")
            return entry["data"]

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        with self._lock:
            self._store[key] = {
                "data": value,
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds),
            }
            self._store.move_to_end(key)
            while len(self._store) > self._max_entries:
                evicted_key, _ = self._store.popitem(last=False)
                logger.info(f"[CACHE EVICTED] {evicted_key} (LRU cap {self._max_entries} reached)")
        logger.info(f"[CACHE SET] {key} (TTL: {ttl_seconds}s)")

    async def delete_prefix(self, prefix: str) -> int:
        with self._lock:
            keys_to_delete = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_delete:
                del self._store[k]
            return len(keys_to_delete)

    async def clear(self) -> None:
        with self._lock:
            self._store.clear()

    async def stats(self) -> Dict[str, Any]:
        with self._lock:
            now = datetime.now(timezone.utc)
            total = len(self._store)
            expired = sum(1 for v in self._store.values() if v["expires_at"] <= now)
            return {
                "backend": "in_memory",
                "total_entries": total,
                "expired_entries": expired,
                "active_entries": total - expired,
                "max_entries": self._max_entries,
            }


class UpstashRedisCacheBackend(CacheBackend):
    """
    Real Redis via Upstash's HTTP REST API — no persistent TCP connection
    to manage, works cleanly from a resource-constrained host.

    Requires: pip install upstash-redis
    Env vars: UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN
    """

    def __init__(self, url: str, token: str):
        from upstash_redis.asyncio import Redis as UpstashRedis
        self._client = UpstashRedis(url=url, token=token)

    async def get(self, key: str) -> Optional[Any]:
        raw = await self._client.get(key)
        if raw is not None:
            logger.info(f"[CACHE HIT] {key}")
        return raw

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        # Every current caller already passes a JSON string; this is a
        # safety net for any future caller that doesn't.
        payload = value if isinstance(value, str) else json.dumps(value)
        await self._client.set(key, payload, ex=ttl_seconds)
        logger.info(f"[CACHE SET] {key} (TTL: {ttl_seconds}s)")

    async def delete_prefix(self, prefix: str) -> int:
        # Page through with SCAN rather than one KEYS call, so this stays
        # cheap even as the keyspace grows.
        deleted = 0
        cursor = "0"
        while True:
            cursor, keys = await self._client.scan(cursor, match=f"{prefix}*", count=200)
            if keys:
                await self._client.delete(*keys)
                deleted += len(keys)
            if cursor == "0":
                break
        return deleted

    async def clear(self) -> None:
        await self._client.flushdb()

    async def stats(self) -> Dict[str, Any]:
        try:
            size = await self._client.dbsize()
        except Exception as e:
            logger.warning(f"[CACHE STATS] dbsize failed: {e}")
            size = -1
        return {"backend": "upstash_redis", "total_entries": size}


def _build_backend() -> CacheBackend:
    redis_url = os.getenv("UPSTASH_REDIS_REST_URL")
    redis_token = os.getenv("UPSTASH_REDIS_REST_TOKEN")
    if redis_url and redis_token:
        try:
            logger.info("[CACHE] Using Upstash Redis backend")
            return UpstashRedisCacheBackend(redis_url, redis_token)
        except Exception as e:
            logger.error(f"[CACHE] Failed to init Redis backend, falling back to in-memory: {e}")
    logger.info("[CACHE] Using in-memory backend (set UPSTASH_REDIS_REST_URL/TOKEN to switch to Redis)")
    return InMemoryCacheBackend()


_backend: CacheBackend = _build_backend()


def _generate_cache_key(prefix: str, data: Dict[str, Any]) -> str:
    """Generate a deterministic cache key from request data."""
    content = json.dumps(data, sort_keys=True)
    content_hash = hashlib.md5(content.encode()).hexdigest()
    return f"{prefix}:{content_hash}"


async def cache_get(cache_key: str) -> Optional[Any]:
    return await _backend.get(cache_key)


async def cache_set(cache_key: str, data: Any, ttl_hours: float = DEFAULT_TTL_HOURS) -> None:
    await _backend.set(cache_key, data, ttl_seconds=int(ttl_hours * 3600))


async def cache_invalidate(pattern: str = "") -> None:
    if not pattern:
        await _backend.clear()
        logger.info("[CACHE CLEARED] All entries removed")
        return
    removed = await _backend.delete_prefix(pattern)
    logger.info(f"[CACHE INVALIDATED] Removed {removed} entries matching '{pattern}'")


async def cache_stats() -> Dict[str, Any]:
    return await _backend.stats()