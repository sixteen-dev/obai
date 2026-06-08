# Crypto MCP Server Docker Image

FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml .
COPY VERSION .

RUN uv sync --no-dev

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY --from=builder /app/VERSION /app/VERSION
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY src/ ./src/

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

EXPOSE 8010

CMD ["uv", "run", "--no-sync", "python", "-m", "src.server"]
