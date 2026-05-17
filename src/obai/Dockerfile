# Multi-stage build for the OBaI central app (hub + clients).
# Optimized for production deployment.

# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy dependency files + the packages they declare. The hatchling backend
# does an editable install of the obai project itself, so the source dirs
# named in [tool.hatch.build.targets.wheel] and the force-include map must
# exist on disk before `uv sync` runs.
COPY pyproject.toml README.md ./
COPY core_agents/ ./core_agents/
COPY clients/ ./clients/
COPY evaluation/ ./evaluation/

# Install dependencies (no dev dependencies)
RUN uv sync --no-dev

# Stage 2: Runtime
FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy package code (central hub + specialist orchestration + clients)
COPY core_agents/ ./core_agents/
COPY clients/ ./clients/
COPY evaluation/ ./evaluation/
COPY pyproject.toml README.md ./

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Set Python to run in unbuffered mode (better logging in containers)
ENV PYTHONUNBUFFERED=1

# Health check (placeholder - override based on deployment type)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Default command (override in docker-compose or deployment).
# `obai` is the project entrypoint configured in pyproject.toml.
CMD ["obai"]
