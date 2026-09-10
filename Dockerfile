# Production Dockerfile for ZoneGate Backend
# Requirements: CPython 3.14, Zova 1.0.0, Litestar 2.x, uv
FROM python:3.14-slim AS base

# Install uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Node.js runtime: required by the Nokia MCP client, which spawns `npx mcp-remote` over stdio
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm ca-certificates && rm -rf /var/lib/apt/lists/*

# System & runtime environment configuration
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    LITESTAR_APP=zonegate.app:app \
    ZOVA_DB_PATH=/app/.docker/zonegate.zova

WORKDIR /app

# Cache and sync dependencies separately from application code for fast rebuilds
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Copy source tree and metadata
COPY README.md pyproject.toml uv.lock /app/
COPY src/ /app/src/
COPY scripts/ /app/scripts/

# Install the project itself into the virtual environment
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Create persistence directory for Zova embedded storage
RUN mkdir -p /app/.docker

EXPOSE 8000

# Container healthcheck targeting the ZoneGate /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

# Default command: launch Litestar HTTP server
CMD ["litestar", "run", "--host", "0.0.0.0", "--port", "8000"]
