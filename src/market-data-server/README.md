# Market Data MCP Server

MCP server providing real-time and historical market data via Financial Modeling Prep API.

## Features

### Critical Tools (5)
1. **get_quote** - Full real-time quote with OHLCV data
2. **get_latest_trade** - Fast price snapshot (condensed quote)
3. **get_candles** - Historical price data (1min to daily intervals)
4. **get_movers** - Market movers (gainers, losers, most active)
5. **get_market_snapshot** - Sector performance overview

### Nice-to-Have Tools (2)
6. **get_afterhours_quote** - Pre/post-market price data
7. **is_market_open** - Market hours status

## Architecture

- **Framework**: FastMCP 2.12.4
- **Transport**: streamable-http (configurable)
- **Authentication**: None (handled externally)
- **Logging**: Structured JSON via structlog
- **Configuration**: pydantic-settings + AWS Secrets Manager
- **Deployment**: Docker (ARM64), AWS App Runner

## Project Structure

```
src/market-data-server/
├── VERSION                 # Auto-bumped version file
├── pyproject.toml         # uv dependencies
├── Dockerfile             # ARM64 Docker image
├── dev/
│   ├── docker-compose.yml # Local testing
│   └── .env               # Environment variables
├── src/
│   ├── server.py          # FastMCP server
│   ├── config.py          # Settings + Secrets Manager
│   ├── logging_config.py  # Structured logging
│   ├── secrets.py         # AWS Secrets Manager client
│   ├── clients/
│   │   └── fmp_client.py  # FMP API client
│   └── tools/
│       ├── quotes.py      # Quote tools
│       ├── candles.py     # Historical data
│       ├── movers.py      # Market movers
│       ├── market.py      # Market overview
│       └── afterhours.py  # After-hours quotes
└── .github/workflows/
    └── market-data-server-ci-cd.yml

## Environment Variables

### Required
- `AWS_REGION` - AWS region (default: us-east-2)
- `SECRETS_ID` - AWS Secrets Manager secret ID (default: prod/teq/all_secrets)

### Optional (with defaults)
- `TRANSPORT` - Transport type (default: streamable-http)
- `HOST` - Server host (default: 0.0.0.0)
- `PORT` - Server port (default: 8000)

### Loaded from Secrets Manager
- `FMP_API_KEY` - Financial Modeling Prep API key

## Local Development

### Prerequisites
- Python 3.12+
- uv package manager
- Docker + Docker Compose
- AWS credentials configured

### Setup

```bash
# Install dependencies
cd src/market-data-server
uv sync --all-extras --dev

# Run linting
uv run ruff check . --fix
uv run ruff format .

# Type checking
uv run mypy src/ --strict

# Run tests
uv run pytest
```

### Local Testing with Docker

```bash
# Build and run
cd src/market-data-server/dev
docker-compose up --build

# Server runs on http://localhost:8001
# Health check: http://localhost:8001/health
```

## API Endpoints

### Health Check
```bash
GET /health
```

Returns:
```json
{
  "status": "healthy",
  "service": "market-data-server",
  "version": "0.1.0"
}
```

### MCP Tools

All tools available via MCP protocol at root `/` endpoint.

## FMP API Endpoints Used

- `/api/v3/quote/{symbol}` - Full quote
- `/api/v3/quote-short/{symbol}` - Short quote
- `/api/v3/historical-chart/{interval}/{symbol}` - Intraday data
- `/api/v3/historical-price-full/{symbol}` - Daily data
- `/api/v3/stock_market/{type}` - Movers
- `/api/v3/sector-performance` - Sector snapshot
- `/api/v3/pre-post-market/{symbol}` - After-hours
- `/api/v3/is-the-market-open` - Market status

## Deployment

### CI/CD Pipeline

Triggers on changes to `src/market-data-server/**`:
1. Lint and type check
2. Security scan (bandit)
3. Build ARM64 Docker image
4. Push to ECR: `obai-market-data-server:{version}`

### ECR Repository
- **Name**: `obai-market-data-server`
- **Region**: `us-east-2`
- **Tags**: Version only (immutable)

## Code Standards

- **Type hints**: Required for all functions/methods (mypy strict)
- **Docstrings**: Google-style for all public APIs
- **Line length**: 100 characters
- **Imports**: Sorted with ruff
- **Error handling**: All exceptions logged with context
- **Security**: API keys via Secrets Manager only

## Monitoring

- **Health checks**: `/health` endpoint
- **Structured logs**: JSON format via structlog
- **Error tracking**: All errors logged with full context
- **API calls**: Logged with sanitized parameters
