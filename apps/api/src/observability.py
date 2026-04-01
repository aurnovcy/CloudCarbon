"""
CloudCarbon API Observability.

Provides:
  - Prometheus metrics via prometheus-fastapi-instrumentator
  - Custom business metrics (ingestion records, enrichment runs, recommendations)
  - Structured JSON logging via structlog
  - Request ID middleware
"""
from __future__ import annotations

import logging
import sys
import time
import uuid
from typing import Callable

import structlog
from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

# ---------------------------------------------------------------------------
# Custom Prometheus metrics
# ---------------------------------------------------------------------------

INGESTION_RECORDS_TOTAL = Counter(
    "cloudcarbon_ingestion_records_total",
    "Total number of FOCUS records ingested",
    ["provider", "tenant_id"],
)

INGESTION_ERRORS_TOTAL = Counter(
    "cloudcarbon_ingestion_errors_total",
    "Total number of ingestion errors",
    ["provider", "error_type"],
)

ENRICHMENT_RUNS_TOTAL = Counter(
    "cloudcarbon_enrichment_runs_total",
    "Total number of enrichment batch runs",
    ["status"],
)

ENRICHMENT_RECORDS_PROCESSED = Counter(
    "cloudcarbon_enrichment_records_processed_total",
    "Total number of records processed by the enrichment pipeline",
    ["tenant_id"],
)

RECOMMENDATIONS_GENERATED = Counter(
    "cloudcarbon_recommendations_generated_total",
    "Total number of recommendations generated",
    ["type", "severity"],
)

AGENT_RUNS_TOTAL = Counter(
    "cloudcarbon_agent_runs_total",
    "Total number of agent runs",
    ["agent_type", "status", "dry_run"],
)

AGENT_FINDINGS_TOTAL = Counter(
    "cloudcarbon_agent_findings_total",
    "Total number of findings detected by agents",
    ["agent_type", "severity"],
)

ELECTRICITY_MAPS_CACHE_HITS = Counter(
    "cloudcarbon_electricity_maps_cache_hits_total",
    "Electricity Maps API cache hits",
)

ELECTRICITY_MAPS_CACHE_MISSES = Counter(
    "cloudcarbon_electricity_maps_cache_misses_total",
    "Electricity Maps API cache misses",
)

ACTIVE_TENANTS = Gauge(
    "cloudcarbon_active_tenants",
    "Number of active tenants",
)

FOCUS_RECORDS_GAUGE = Gauge(
    "cloudcarbon_focus_records_total",
    "Total FOCUS records in the database",
    ["tenant_id"],
)

ENRICHMENT_LATENCY = Histogram(
    "cloudcarbon_enrichment_latency_seconds",
    "Time to enrich a single FOCUS record",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

NL_QUERY_LATENCY = Histogram(
    "cloudcarbon_nl_query_latency_seconds",
    "Natural language query end-to-end latency",
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)


# ---------------------------------------------------------------------------
# Structlog configuration
# ---------------------------------------------------------------------------

def configure_logging(log_level: str = "INFO", json_logs: bool = True) -> None:
    """Configure structlog for structured JSON logging."""
    log_level_int = getattr(logging, log_level.upper(), logging.INFO)

    # Configure stdlib logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level_int,
    )

    # Shared processors
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if json_logs:
        # Production: JSON output
        processors = shared_processors + [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Development: coloured console output
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level_int),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ---------------------------------------------------------------------------
# Request ID middleware
# ---------------------------------------------------------------------------

class RequestIDMiddleware:
    """Injects a unique X-Request-ID header and binds it to structlog context."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Bind to structlog context for this request
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                headers[b"x-request-id"] = request_id.encode()
                message["headers"] = list(headers.items())
            await send(message)

        await self.app(scope, receive, send_with_request_id)


# ---------------------------------------------------------------------------
# Security headers middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware:
    """Adds OWASP-recommended security headers to all responses."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                headers.update(
                    {
                        b"x-content-type-options": b"nosniff",
                        b"x-frame-options": b"DENY",
                        b"x-xss-protection": b"1; mode=block",
                        b"referrer-policy": b"strict-origin-when-cross-origin",
                        b"permissions-policy": b"geolocation=(), microphone=(), camera=()",
                        b"content-security-policy": (
                            b"default-src 'self'; "
                            b"script-src 'self'; "
                            b"style-src 'self' 'unsafe-inline'; "
                            b"img-src 'self' data:; "
                            b"connect-src 'self'"
                        ),
                        b"strict-transport-security": (
                            b"max-age=31536000; includeSubDomains; preload"
                        ),
                    }
                )
                message["headers"] = list(headers.items())
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


# ---------------------------------------------------------------------------
# Prometheus instrumentation setup
# ---------------------------------------------------------------------------

def setup_prometheus(app: FastAPI) -> None:
    """Attach prometheus-fastapi-instrumentator to the FastAPI app."""
    Instrumentator(
        should_group_status_codes=True,
        should_ignore_untemplated=True,
        should_respect_env_var=True,
        should_instrument_requests_inprogress=True,
        excluded_handlers=["/metrics", "/health"],
        inprogress_name="cloudcarbon_http_requests_inprogress",
        inprogress_labels=True,
    ).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
