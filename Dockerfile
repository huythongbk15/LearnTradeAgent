# Multi-stage Dockerfile — Production Optimized
# Build: docker build -t ghcr.io/your-org/trading-agent:latest .
# Run: docker run -d --name trading-agent ghcr.io/your-org/trading-agent:latest
#
# NOTE on Python versions: the project is pinned to Python 3.12
# (`requires-python = ">=3.12,<3.13"`). Builder and runtime MUST use the same
# Python line so that compiled site-packages are compatible. The runtime uses
# a virtualenv copied from the builder (`/opt/venv`) instead of hard-coding a
# `/usr/local/lib/pythonX.Y/site-packages` path, so the copy survives Python
# patch-version changes.

# =============================================================================
# STAGE 0: Frontend — deterministic React/Vite production bundle
# =============================================================================
FROM node:24-alpine@sha256:2a49bdf71e9fd965a58c1703fd9ddd205b34e5782b692a72dd1d248abb0beb43 AS frontend

WORKDIR /frontend
COPY webui/frontend/package.json webui/frontend/package-lock.json ./
RUN npm ci
COPY webui/frontend/ ./
RUN npm run build

# =============================================================================
# STAGE 1: Builder — Install Python dependencies into a virtualenv
# =============================================================================
FROM python:3.12-slim@sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64 AS builder

# System deps for building wheels (cryptography, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# Create and activate the virtualenv for the runtime image.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV="/opt/venv"

WORKDIR /app

# Copy only dependency files first (cache layer)
COPY pyproject.toml requirements-web.txt ./

# Install production dependencies into /opt/venv
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir poetry-core \
    && pip install --no-cache-dir ".[web,ml,research,portfolio]" \
    && python -c "import rich, pandas, ccxt; print('pip deps OK:', rich.__file__)"

# =============================================================================
# STAGE 2: Runtime — Minimal, non-root, read-only
# =============================================================================
FROM python:3.12-slim@sha256:d657ab0ade19f404a6ccc883ab399540de667aff751748ce23c07330c5a89e64 AS runtime

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

# Copy the virtualenv built in the builder stage (same Python 3.12 line).
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY --chown=appuser:appgroup src/ ./src/
COPY --chown=appuser:appgroup config/ ./config/
COPY --chown=appuser:appgroup scripts/ ./scripts/
COPY --chown=appuser:appgroup dashboard/ ./dashboard/
COPY --chown=appuser:appgroup webui/backend/ ./webui/backend/
COPY --from=frontend --chown=appuser:appgroup /frontend/dist/ ./webui/frontend/dist/
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
    PATH="/opt/venv/bin:/home/appuser/.local/bin:$PATH" \
    TRADING_CONFIG_PATH=/app/config/config.yaml

# Default command (can be overridden in compose)
ENTRYPOINT ["python", "-m", "trading_agent.cli"]
CMD ["--help"]

# =============================================================================
# STAGE 3: Development (optional) — with dev tools
# =============================================================================
FROM builder AS dev

# Create appuser before COPY --chown.
ARG UID=1000
ARG GID=1000
RUN groupadd -g $GID appgroup 2>/dev/null || true && \
    useradd -u $UID -g $GID -m -s /bin/bash appuser 2>/dev/null || true && \
    mkdir -p /app/data/raw /app/data/execution /app/logs && \
    chown -R appuser:appgroup /app

COPY --chown=appuser:appgroup . .

# Install dev dependencies
RUN pip install --no-cache-dir poetry-core \
    && pip install --no-cache-dir -e ".[dev,web,infra,ml,research,portfolio]"

ENV PYTHONPATH="/app/src:$PYTHONPATH"

USER appuser

CMD ["python", "-m", "trading_agent.cli", "--help"]
