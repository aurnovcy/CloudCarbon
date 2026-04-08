"""
Synchronous Redis client for token storage and caching.
"""
from __future__ import annotations

import redis

from src.config import get_settings

_redis_pool: redis.Redis | None = None


def get_redis_pool() -> redis.Redis:
    global _redis_pool
    if _redis_pool is None:
        settings = get_settings()
        _redis_pool = redis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_pool


def get_redis() -> redis.Redis:
    """FastAPI dependency that returns a Redis client."""
    return get_redis_pool()
