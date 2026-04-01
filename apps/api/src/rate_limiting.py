"""
CloudCarbon API Rate Limiting.

Uses slowapi (Starlette/FastAPI wrapper around limits) with Redis as the
storage backend. Falls back to in-memory storage if Redis is unavailable.

Rate limit tiers:
  - Default:          200/minute per IP
  - /auth endpoints:  10/minute per IP (brute-force protection)
  - /ingestion:       30/minute per tenant
  - /query (NL):      20/minute per tenant (LLM cost control)
  - /enrichment/run:  5/minute per tenant
"""
from __future__ import annotations

import logging
import os
from typing import Callable

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _get_tenant_id(request: Request) -> str:
    """
    Rate limit key function that uses tenant_id from the JWT token if available,
    falling back to the remote IP address.
    """
    # Try to get tenant_id from the request state (set by auth middleware)
    tenant_id = getattr(request.state, "tenant_id", None)
    if tenant_id:
        return str(tenant_id)
    return get_remote_address(request)


def _get_ip(request: Request) -> str:
    """Rate limit by remote IP address."""
    return get_remote_address(request)


# ---------------------------------------------------------------------------
# Limiter instances
# ---------------------------------------------------------------------------

try:
    from limits.storage import RedisStorage
    storage_uri = REDIS_URL
    limiter = Limiter(
        key_func=_get_ip,
        default_limits=["200/minute"],
        storage_uri=storage_uri,
    )
    tenant_limiter = Limiter(
        key_func=_get_tenant_id,
        default_limits=["200/minute"],
        storage_uri=storage_uri,
    )
    logger.info("Rate limiter using Redis storage: %s", REDIS_URL)
except Exception as exc:
    logger.warning(
        "Redis unavailable for rate limiting, falling back to memory: %s", exc
    )
    limiter = Limiter(
        key_func=_get_ip,
        default_limits=["200/minute"],
    )
    tenant_limiter = Limiter(
        key_func=_get_tenant_id,
        default_limits=["200/minute"],
    )


# ---------------------------------------------------------------------------
# Rate limit decorators (convenience wrappers)
# ---------------------------------------------------------------------------

# Auth endpoints — strict IP-based limit
auth_limit = limiter.limit("10/minute")

# Ingestion endpoints — per-tenant
ingestion_limit = tenant_limiter.limit("30/minute")

# NL query — per-tenant (LLM cost control)
query_limit = tenant_limiter.limit("20/minute")

# Enrichment run — per-tenant
enrichment_run_limit = tenant_limiter.limit("5/minute")

# Default — per IP
default_limit = limiter.limit("200/minute")


# ---------------------------------------------------------------------------
# Rate limit exceeded handler
# ---------------------------------------------------------------------------

async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Return a structured 429 response when rate limit is exceeded."""
    return JSONResponse(
        status_code=429,
        content={
            "error": "rate_limit_exceeded",
            "message": f"Rate limit exceeded: {exc.detail}",
            "retry_after": getattr(exc, "retry_after", 60),
        },
        headers={"Retry-After": str(getattr(exc, "retry_after", 60))},
    )
