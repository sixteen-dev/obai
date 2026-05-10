# Fundamentals MCP Server

MCP server for company fundamental analysis. Powered by FMP API. Optional Qdrant vector search over financial PDFs is shipped but disabled by default (`QDRANT_ENABLED=false`).

## Features

- **Financial Statements**: Income statement, balance sheet, cash flow
- **Company Profile**: Sector, industry, description, key executives
- **Key Metrics**: P/E ratio, ROE, ROIC, revenue per share
- **Financial Ratios**: Liquidity, profitability, efficiency ratios
- **SEC Filings**: 10-K, 10-Q, 8-K and other regulatory filings with direct links
- **Insider Trading**: Corporate insider transactions (executives, directors, 10%+ shareholders)
- **Revenue Segments**: Business line breakdown by product category
- **Educational Context** (optional): Vector search across financial PDFs (options, ETFs, bonds, etc.) via Qdrant. Disabled by default; enable with `QDRANT_ENABLED=true`.
- **Structured Logging**: JSON logs with structlog for observability

## Tools

### `get_fundamentals`
Get financial statements with optional educational context from PDFs.

```json
{
  "symbol": "AAPL",
  "statement_type": "income",
  "period": "annual",
  "limit": 5,
  "include_context": true
}
```

### `get_company_profile`
Get company overview with optional industry context.

```json
{
  "symbol": "AAPL",
  "include_context": false
}
```

### `search_fundamentals`
Search educational content from financial PDFs via Qdrant vector search. Only registered when `QDRANT_ENABLED=true`.

```json
{
  "query": "What are option strategies for volatility?",
  "top_k": 5
}
```

### `get_key_metrics`
Get key financial metrics.

```json
{
  "symbol": "AAPL",
  "period": "annual",
  "limit": 5
}
```

### `get_financial_ratios`
Get financial ratios.

```json
{
  "symbol": "AAPL",
  "period": "quarter",
  "limit": 8
}
```

### `get_sec_filings`
Get SEC filings (10-K, 10-Q, 8-K, etc.) for deep fundamental research.

```json
{
  "symbol": "AAPL",
  "limit": 10,
  "from_date": "2024-01-01",
  "to_date": "2024-12-31"
}
```

### `get_insider_trades`
Get insider trading activity for sentiment analysis.

```json
{
  "symbol": "AAPL",
  "limit": 20
}
```

### `get_revenue_segments`
Get revenue breakdown by product/business segment.

```json
{
  "symbol": "AAPL",
  "period": "annual"
}
```

## Setup

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FMP_API_KEY` | Yes | Financial Modeling Prep API key |
| `OPENAI_API_KEY` | Yes | For embedding generation (vector search) |
| `QDRANT_URL` | No | Qdrant server URL (default: `http://localhost:6333`) |
| `QDRANT_COLLECTION` | No | Qdrant collection name (default: `financial-fundamentals`) |
| `HOST` | No | Server host (default: `0.0.0.0`) |
| `PORT` | No | Server port (default: `8000`) |

### Local Development

```bash
cd src/fundamentals-server
uv sync --all-extras --dev

# Run server
uv run python -m src.server
```

### Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector
```

In the MCP Inspector UI:
- **Connection Type:** Streamable HTTP
- **Server URL:** `http://localhost:8001/mcp`

## Development

```bash
uv run ruff check . --fix
uv run ruff format .
uv run mypy src/ --strict
uv run pytest
```

## Project Structure

```
fundamentals-server/
├── src/
│   ├── server.py           # FastMCP server entry point
│   ├── config.py           # Pydantic settings
│   ├── auth.py             # JWT authentication (optional)
│   ├── logging_config.py   # Structured logging setup
│   ├── audit.py            # Security auditing functions
│   ├── clients/
│   │   ├── fmp_client.py         # FMP API client
│   │   └── qdrant_client.py      # Qdrant vector search client
│   └── tools/
│       ├── fundamentals.py       # Financial analysis tools
│       └── vector_search.py      # Educational content search
├── Dockerfile
├── pyproject.toml
└── README.md
```
