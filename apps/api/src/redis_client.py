"""
Redis client for refresh token storage and caching.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import redis.asyncio as aioredis

from src.config import get_settings

_redis_pool: aioredis.Redis | None = None


def get_redis_pool() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        settings = get_settings()
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_pool


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """FastAPI dependency that yields a Redis client."""
    client = get_redis_pool()
    try:
        yield client
    finally:
        pass  # Pool manages connections; no per-request teardown needed
