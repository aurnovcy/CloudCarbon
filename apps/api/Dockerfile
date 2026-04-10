# ============================================================
# CloudCarbon API — Production Multi-Stage Dockerfile
# Stage 1: builder — installs Python dependencies with pip
# Stage 2: runtime — minimal image, non-root user, health check
# ============================================================

# ---- Stage 1: builder ----
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install build tools
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt ./

# Install into a prefix directory
RUN pip install --prefix=/install -r requirements.txt

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

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application files
COPY --from=builder /build/src /app/src
COPY --from=builder /build/alembic /app/alembic
COPY --from=builder /build/alembic.ini /app/alembic.ini

# Set ownership
RUN chown -R cloudcarbon:cloudcarbon /app

# Switch to non-root user
USER cloudcarbon

ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Expose API port
EXPOSE 8000

# Health check — polls the /health endpoint every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run migrations then start the server
CMD ["sh", "-c", "alembic upgrade head && uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2 --proxy-headers --forwarded-allow-ips '*'"]
