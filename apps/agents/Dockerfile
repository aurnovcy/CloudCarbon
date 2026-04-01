# ============================================================
# CloudCarbon Agents — Production Multi-Stage Dockerfile
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

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sSL https://install.python-poetry.org | python3 -

ENV PATH="$POETRY_HOME/bin:$PATH"

COPY pyproject.toml poetry.lock* ./
RUN poetry install --only main --no-root --no-interaction --no-ansi

COPY src/ ./src/

# ---- Stage 2: runtime ----
FROM python:3.11-slim AS runtime

LABEL org.opencontainers.image.title="CloudCarbon Agents" \
      org.opencontainers.image.description="Autonomous GreenOps agent runner" \
      org.opencontainers.image.source="https://github.com/cloudcarbon/cloudcarbon" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1001 cloudcarbon \
    && useradd --uid 1001 --gid cloudcarbon --shell /bin/bash --create-home cloudcarbon

WORKDIR /app

COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/src /app/src

RUN chown -R cloudcarbon:cloudcarbon /app

USER cloudcarbon

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Health check — verify Python environment is functional
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python3 -c "import asyncio, structlog; print('ok')" || exit 1

# Run the APScheduler-based agent runner
CMD ["python3", "/app/src/runner.py"]
