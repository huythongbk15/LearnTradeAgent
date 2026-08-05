# Multi-stage Dockerfile — Production Optimized
# Build: docker build -t ghcr.io/your-org/trading-agent:latest .
# Run: docker run -d --name trading-agent ghcr.io/your-org/trading-agent:latest

# =============================================================================
# STAGE 1: Builder — Install dependencies, compile wheels
# =============================================================================
FROM python:3.12-slim AS builder

# System deps for building wheels (cryptography, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
ENV POETRY_VERSION=2.4.1 \
    POETRY_HOME="/opt/poetry" \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install "poetry==$POETRY_VERSION"

WORKDIR /app

# Copy only dependency files first (cache layer)
COPY pyproject.toml poetry.lock ./

# Install production dependencies only (no dev group)
RUN poetry install --only=main --no-root

# =============================================================================
# STAGE 2: Runtime — Minimal, non-root, read-only
# =============================================================================
FROM python:3.12-slim AS runtime

# System runtime deps (libpq for psycopg2, ca-certificates for TLS)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    ca-certificates \
    curl \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
ARG UID=1000
ARG GID=1000
RUN groupadd -g $GID appgroup && \
    useradd -u $UID -g $GID -m -s /bin/bash appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=appuser:appgroup src/ ./src/
COPY --chown=appuser:appgroup config/ ./config/
COPY --chown=appuser:appgroup scripts/ ./scripts/
COPY --chown=appuser:appgroup dashboard/ ./dashboard/
COPY --chown=appuser:appgroup pyproject.toml ./

# Create data directories with correct permissions
RUN mkdir -p /app/data/raw /app/data/execution /app/logs && \
    chown -R appuser:appgroup /app

# Switch to non-root
USER appuser

# Healthcheck endpoint (requires trading-agent CLI health subcommand)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -m trading_agent.cli system health || exit 1

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app/src:$PYTHONPATH" \
    PATH="/home/appuser/.local/bin:$PATH" \
    TRADING_CONFIG_PATH=/app/config/credentials.yaml

# Default command (can be overridden in compose)
ENTRYPOINT ["python", "-m", "trading_agent.cli"]
CMD ["--help"]

# =============================================================================
# STAGE 3: Development (optional) — with dev tools
# =============================================================================
FROM builder AS dev

# Copy all source FIRST so poetry can install the root package
COPY --chown=appuser:appgroup . .

# Create appuser for dev stage too (builder doesn't have it)
ARG UID=1000
ARG GID=1000
RUN groupadd -g $GID appgroup 2>/dev/null || true && \
    useradd -u $UID -g $GID -m -s /bin/bash appuser 2>/dev/null || true && \
    mkdir -p /app/data/raw /app/data/execution /app/logs && \
    chown -R appuser:appgroup /app

# Install dev dependencies (+ re-links root package)
RUN poetry install --with dev

ENV PYTHONPATH="/app/src:$PYTHONPATH"

USER appuser

CMD ["python", "-m", "trading_agent.cli", "--help"]