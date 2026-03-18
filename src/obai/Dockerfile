# Multi-stage build for tezQ agents package
# Optimized for production deployment

# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /app

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml ./

# Install dependencies (no dev dependencies)
RUN uv sync --no-dev

# Stage 2: Runtime
FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy agents code and prompts
COPY agents/ ./agents/
COPY clients/ ./clients/

# Add venv to PATH
ENV PATH="/app/.venv/bin:$PATH"

# Set Python to run in unbuffered mode (better logging in containers)
ENV PYTHONUNBUFFERED=1

# Health check (placeholder - override based on deployment type)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Default command (override in docker-compose or deployment)
# For CLI test: python -m clients.cli.chat
# For Discord bot (future): python -m clients.discord.bot
CMD ["python", "-m", "agents"]
