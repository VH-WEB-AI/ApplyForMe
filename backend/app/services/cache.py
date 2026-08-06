"""Cache Layer: avoids redundant LLM calls when the underlying content hasn't changed."""

import json
from functools import lru_cache

import redis

from app.config import get_settings

DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 7  # one week


@lru_cache
def _client() -> redis.Redis:
    return redis.Redis.from_url(get_settings().redis_url, decode_responses=True)


def _key(namespace: str, content_hash: str) -> str:
    return f"applyforme:{namespace}:{content_hash}"


def get_cached(namespace: str, content_hash: str) -> dict | None:
    raw = _client().get(_key(namespace, content_hash))
    return json.loads(raw) if raw else None


def set_cached(namespace: str, content_hash: str, value: dict, ttl: int = DEFAULT_TTL_SECONDS) -> None:
    _client().set(_key(namespace, content_hash), json.dumps(value, default=str), ex=ttl)
