# Options Backtest MCP Server image (ADR 0003 §1.10).
# Lockfile-pinned (ADR 0001 §8, design §17.1): every dependency comes from uv.lock, --frozen.

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first, in their own cached layer; the project itself is not installable yet
# because its package lives under src/.
COPY pyproject.toml uv.lock VERSION ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/pyproject.toml /app/pyproject.toml
COPY --from=builder /app/VERSION /app/VERSION

ENV PYTHONUNBUFFERED=1

EXPOSE 8012

CMD ["/app/.venv/bin/python", "-m", "options_backtest.server"]
