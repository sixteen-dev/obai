# Backtest MCP Server Docker Image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first (cached layer)
COPY pyproject.toml .
COPY VERSION .

# Install dependencies (cached unless pyproject.toml changes)
RUN uv sync --no-dev

# Copy source AFTER deps install — this layer busts on any code change
COPY src/ ./src/

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8007

# Run the MCP server
CMD ["uv", "run", "python", "-m", "src.server"]
