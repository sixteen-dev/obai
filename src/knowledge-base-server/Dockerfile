# Knowledge Base MCP Server Docker Image
# Private corpus (corpus_private/) is auto-included if present in the build
# context. CI / open-source builds without that gitignored directory produce
# a public-only image; maintainer builds with the directory bake it in.
FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Always-present sources
COPY pyproject.toml VERSION ./
COPY scripts/ ./scripts/
COPY corpus/ ./corpus/
COPY src/ ./src/

# corpus_private/ is gitignored; only present in maintainer's local build context.
# Bracket-wildcard tolerates a missing source path during CI builds.
COPY corpu[s]_private/ ./corpus_private/

# Ensure the directory exists even if the wildcard COPY matched nothing.
RUN mkdir -p corpus_private state

# Install dependencies
RUN uv sync --no-dev

# Build corpus.db at image build time (baked into the image; never re-built at runtime).
# build_index.py auto-detects corpus_private/ — present-and-populated => indexed, else skipped.
RUN uv run python scripts/build_index.py --allow-empty

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8011

CMD ["uv", "run", "python", "-m", "src.server"]
