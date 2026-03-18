# Options MCP Server Docker Image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml .
COPY VERSION .
COPY filter_config.yaml .
COPY src/ ./src/

# Install dependencies (no lockfile, resolve fresh)
RUN uv sync --no-dev

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Run the MCP server
CMD ["uv", "run", "python", "-m", "src.server"]
