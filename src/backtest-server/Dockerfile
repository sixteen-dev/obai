# Backtest MCP Server Docker Image
FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (cached layer)
COPY pyproject.toml .
COPY VERSION .

# Install dependencies (uses native platform wheel — aarch64 on Apple Silicon, x86_64 on Intel)
RUN uv sync --no-dev

# Copy source
COPY src/ ./src/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8007

CMD ["uv", "run", "python", "-m", "src.server"]
