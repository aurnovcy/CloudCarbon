# ============================================================
# CloudCarbon API — Production Multi-Stage Dockerfile
# ============================================================
# Stage 1: builder — installs Python dependencies with Poetry
# Stage 2: runtime — minimal image, non-root user, health check
# ============================================================

# ---- Stage 1: builder ----
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.8.3 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_IN_PROJECT=true \
    POETRY_NO_INTERACTION=1

# Install build tools and Poetry
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sSL https://install.python-poetry.org | python3 -

ENV PATH="$POETRY_HOME/bin:$PATH"

# Copy dependency files first (layer caching)
COPY pyproject.toml poetry.lock* ./

# Install dependencies into a virtual environment (no dev deps)
RUN poetry install --only main --no-root --no-interaction --no-ansi

# Copy application source
COPY src/ ./src/
COPY alembic/ ./alembic/
COPY alembic.ini ./

# ---- Stage 2: runtime ----
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="CloudCarbon API" \
      org.opencontainers.image.description="Open-source multi-cloud GreenOps platform" \
      org.opencontainers.image.source="https://github.com/cloudcarbon/cloudcarbon" \
      org.opencontainers.image.licenses="Apache-2.0"

# Security: install only runtime OS deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1001 cloudcarbon \
    && useradd --uid 1001 --gid cloudcarbon --shell /bin/bash --create-home cloudcarbon

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /build/.venv /app/.venv

# Copy application files
COPY --from=builder /build/src /app/src
COPY --from=builder /build/alembic /app/alembic
COPY --from=builder /build/alembic.ini /app/alembic.ini

# Set ownership
RUN chown -R cloudcarbon:cloudcarbon /app

# Switch to non-root user
USER cloudcarbon

# Activate virtual environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Expose API port
EXPOSE 8000

# Health check — polls the /health endpoint every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Production: Uvicorn with uvloop + httptools for maximum throughput
CMD ["uvicorn", "src.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--loop", "uvloop", \
     "--http", "httptools", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--access-log"]
