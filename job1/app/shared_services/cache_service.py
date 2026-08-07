"""
Shared Cache service (Redis). Used for embedding cache, LLM response cache,
rate limiting counters, and orchestrator context caching.
"""
from typing import Any, Optional

import orjson
import redis.asyncio as redis

from app.core.config import get_settings

settings = get_settings()

_redis_pool = redis.ConnectionPool.from_url(settings.REDIS_URL, decode_responses=True)


class CacheService:
    def __init__(self):
        self._redis = redis.Redis(connection_pool=_redis_pool)

    async def get(self, key: str) -> Optional[str]:
        return await self._redis.get(key)

    async def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        await self._redis.set(key, value, ex=ttl or settings.CACHE_TTL_DEFAULT)

    async def get_json(self, key: str) -> Optional[Any]:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return orjson.loads(raw)

    async def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        await self._redis.set(key, orjson.dumps(value), ex=ttl or settings.CACHE_TTL_DEFAULT)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def incr(self, key: str, ttl: Optional[int] = None) -> int:
        val = await self._redis.incr(key)
        if val == 1 and ttl:
            await self._redis.expire(key, ttl)
        return val

    async def ping(self) -> bool:
        return await self._redis.ping()


cache_service = CacheService()
