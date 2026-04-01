"""
CloudCarbon FastAPI application entry point — v0.5.0.

Middleware stack (outermost to innermost):
  1. SecurityHeadersMiddleware  — OWASP security headers
  2. RequestIDMiddleware        — X-Request-ID injection + structlog binding
  3. CORSMiddleware             — CORS policy
  4. SlowAPI state              — rate limiting (via slowapi)

Observability:
  - Prometheus metrics at /metrics (hidden from OpenAPI schema)
  - Structured JSON logging via structlog
  - Custom business metrics in observability.py
"""
from __future__ import annotations

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_settings
from observability import (
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
    configure_logging,
    setup_prometheus,
)
from rate_limiting import limiter, rate_limit_exceeded_handler
from routers import auth, health
from routers.ingestion import accounts_router
from routers.ingestion import router as ingestion_router
from routers.enrichment import router as enrichment_router
from routers.recommendations import router as recommendations_router
from routers.forecasting import router as forecasting_router
from routers.query import router as query_router
from routers.reports import router as reports_router
from routers.agents import router as agents_router

settings = get_settings()

# Configure structured logging at startup
configure_logging(
    log_level=settings.log_level if hasattr(settings, "log_level") else "INFO",
    json_logs=settings.environment == "production",
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CloudCarbon API",
    description=(
        "Open-source multi-cloud GreenOps platform — REST API.\n\n"
        "Provides FOCUS 1.0 ingestion, GHG enrichment, GreenOps recommendations, "
        "autonomous agents, and natural language querying across AWS, Azure, GCP, "
        "and Alibaba Cloud."
    ),
    version="0.5.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
    openapi_url="/openapi.json" if settings.environment != "production" else None,
    license_info={
        "name": "Apache 2.0",
        "url": "https://www.apache.org/licenses/LICENSE-2.0",
    },
    contact={
        "name": "CloudCarbon",
        "url": "https://github.com/cloudcarbon/cloudcarbon",
    },
)

# ---------------------------------------------------------------------------
# Rate limiter state (slowapi)
# ---------------------------------------------------------------------------

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Middleware (added in reverse order — last added is outermost)
# ---------------------------------------------------------------------------

# 3. CORS (innermost of the three custom middlewares)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Request-ID",
        "X-API-Key",
        "Accept",
    ],
    expose_headers=["X-Request-ID", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    max_age=600,
)

# 2. Request ID (middle)
app.add_middleware(RequestIDMiddleware)

# 1. Security headers (outermost)
app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# Prometheus instrumentation
# ---------------------------------------------------------------------------

setup_prometheus(app)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(health.router, tags=["Health"])
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(ingestion_router, tags=["Ingestion"])
app.include_router(accounts_router, tags=["Accounts"])
app.include_router(enrichment_router, tags=["Enrichment"])
app.include_router(recommendations_router, tags=["Recommendations"])
app.include_router(forecasting_router, tags=["Reports"])
app.include_router(query_router, tags=["Query"])
app.include_router(reports_router, tags=["Reports"])
app.include_router(agents_router, tags=["Agents"])

# ---------------------------------------------------------------------------
# Startup / shutdown lifecycle
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup() -> None:
    logger.info(
        "CloudCarbon API starting",
        version="0.5.0",
        environment=settings.environment,
    )


@app.on_event("shutdown")
async def on_shutdown() -> None:
    logger.info("CloudCarbon API shutting down")
