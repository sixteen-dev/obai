# Backtest MCP Server Docker Image
#
# Multi-stage build: stage 1 installs the locked Python environment, stage 2 is
# the slim runtime image.

# --- Stage 1: Locked dependencies ---
FROM python:3.12-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency files first for better build caching.
COPY pyproject.toml uv.lock VERSION ./

# polars-talib ships manylinux wheels for x86_64 and aarch64 from 0.1.6, so the
# image installs the locked wheel; the old source build pinned 0.1.5 and no
# longer resolves its Rust dependencies (crates.io yanked zip 0.6.x).
RUN uv sync --no-dev --locked --no-install-project

# --- Stage 2: Runtime (no Rust, no build tools) ---
FROM python:3.12-slim

WORKDIR /app

# Copy the fully built virtualenv and project metadata from the builder image.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY --from=builder /app/VERSION /app/VERSION

# Install uv for the runtime entrypoint.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy source after dependencies to keep rebuilds fast.
COPY src/ ./src/

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8007

CMD ["uv", "run", "--no-sync", "python", "-m", "src.server"]
