# Backtest MCP Server

MCP server for backtesting trading strategies with technical indicators, async job execution, and runtime estimation.

## Features

### Tools (8)
1. **backtest_run_strategy** - Run a strategy backtest (sync or async based on estimated complexity)
2. **backtest_get_job_status** - Poll async job status with remaining-time hints
3. **backtest_get_supported_indicators** - List all 20 supported technical indicators
4. **backtest_download_data** - Download OHLCV data from FMP to Parquet
5. **backtest_list_available_data** - Check locally stored data and date ranges
6. **backtest_get_trade_log** - Paginated trade-by-trade details
7. **backtest_compare_strategies** - Compare 2-5 strategies side-by-side
8. **backtest_clear_cache** - Clear cached backtest results

### Async Contract
- **Tri-state `async_mode`**: `None` (server auto-decides), `True` (force async), `False` (force sync)
- **Auto-async threshold**: Configurable cutoff (default 10s) — strategies estimated above this run async
- **Response fields**: `job_id`, `status`, `auto_async`, `estimated_seconds`, `poll_after_seconds`, `expires_at`
- **Job TTL**: Completed jobs are lazily evicted after `job_result_ttl_seconds` (default 1 hour)

### Runtime Estimation
Formula: `(symbols x years x weight) + (indicator_cost) + (download_penalty)`
- Multi-output indicators (MACD, BBANDS, STOCH, etc.) weighted 1.5x
- Uncached symbols add download penalty per symbol
- All weights configurable via env vars

## Architecture

- **Framework**: FastMCP
- **Transport**: streamable-http (configurable)
- **Indicators**: polars-talib (Rust backend, no C deps)
- **Data**: Polars DataFrames, Parquet storage
- **Logging**: Structured JSON via structlog
- **Configuration**: pydantic-settings

## Project Structure

```
src/backtest-server/
├── VERSION
├── pyproject.toml
├── Dockerfile
├── src/
│   ├── server.py           # FastMCP server + tools
│   ├── config.py           # pydantic-settings config
│   ├── jobs.py             # Async job store with TTL
│   ├── logging_config.py   # Structured logging
│   ├── response_utils.py   # Error formatting
│   ├── clients/
│   │   └── fmp_client.py   # FMP API client
│   ├── data/
│   │   ├── downloader.py   # FMP → Parquet pipeline
│   │   └── store.py        # Parquet data store
│   ├── engine/
│   │   ├── backtester.py   # Core backtest engine
│   │   ├── indicators.py   # 20 polars-talib indicators
│   │   ├── signals.py      # Signal generation from rules
│   │   ├── metrics.py      # Performance metrics
│   │   └── cache.py        # Backtest result cache
│   └── models/
│       ├── strategy.py     # StrategyDefinition dataclass
│       └── backtest_result.py
└── tests/
    ├── conftest.py
    ├── test_backtester.py
    ├── test_cache.py
    ├── test_estimation.py
    ├── test_indicators.py
    ├── test_integration.py
    ├── test_metrics.py
    ├── test_server_async.py
    ├── test_signals.py
    └── test_strategy_schema.py
```

## Environment Variables

### Required
- `FMP_API_KEY` - Financial Modeling Prep API key

### Optional (with defaults)
- `TRANSPORT` - Transport type (default: `streamable-http`)
- `HOST` - Server host (default: `0.0.0.0`)
- `PORT` - Server port (default: `8007`)
- `BACKTEST_DATA_DIR` - OHLCV Parquet storage (default: `./data/ohlcv`)
- `BACKTEST_DATA_FRESHNESS_HOURS` - Hours before re-download (default: `24`)
- `BACKTEST_CACHE_DIR` - Result cache directory (default: `./data/cache`)
- `BACKTEST_CACHE_TTL_HOURS` - Cache TTL (default: `24`)
- `AUTO_ASYNC_THRESHOLD_SECONDS` - Sync/async cutoff (default: `10`)
- `JOB_RESULT_TTL_SECONDS` - Completed job retention (default: `3600`)
- `LOG_LEVEL` - Log level (default: `INFO`)

### Estimation Weights
- `ESTIMATE_SYMBOL_YEAR_WEIGHT` - Seconds per symbol-year (default: `0.5`)
- `ESTIMATE_INDICATOR_WEIGHT` - Seconds per indicator (default: `0.1`)
- `ESTIMATE_DOWNLOAD_PENALTY` - Seconds per uncached symbol (default: `2.0`)

## Local Development

### Setup

```bash
cd src/backtest-server
uv sync --all-extras --dev

uv run ruff check . --fix
uv run ruff format .
uv run mypy src/ --strict
uv run pytest
```

### Conformance Suite

The calculation conformance suite is documented in
[`docs/conformance.md`](docs/conformance.md). It is separate from the OBaI
agent-level eval harness and focuses on deterministic indicator, metric,
execution, cost, no-lookahead, portfolio, data-quality, and edge-case behavior.

```bash
cd src/backtest-server
uv run pytest tests/test_conformance_*.py
```

### Run Locally

```bash
cd src/backtest-server
FMP_API_KEY=your_key uv run python -m src.server
# Server runs on http://localhost:8007
# Health check: http://localhost:8007/health
```

### Docker

```bash
cd dev
docker-compose up --build backtest-server
# Server runs on http://localhost:8007
```
