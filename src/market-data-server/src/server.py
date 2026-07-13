"""MCP server for real-time and historical market data."""

import asyncio
import time
from typing import Any, Literal

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .clients.fmp_client import FMPClient
from .config import Settings, get_settings, load_settings
from .logging_config import configure_logging, get_logger, log_error
from .response_utils import format_api_error, truncate_response
from .tools import (
    get_afterhours_quote,
    get_candles,
    get_latest_trade,
    get_market_snapshot,
    get_movers,
    get_quote,
    get_short_volume,
    get_technical_indicators,
    is_market_open,
    list_commodities,
)

# Server start time for uptime tracking
_server_start_time: float = time.time()

# Configure logging first
configure_logging()
logger = get_logger(__name__)


def bootstrap() -> Settings:
    """Bootstrap server by loading settings.

    Returns:
        Application settings

    Raises:
        Exception: If bootstrap fails
    """
    logger.info("bootstrap_started", server="market-data-server")

    try:
        logger.info("loading_settings", source="env")
        settings = load_settings()
        logger.info("settings_loaded")

        logger.info("bootstrap_complete")
        return settings

    except Exception as e:
        log_error(logger, e, context={"event": "bootstrap_failed"})
        raise


# Initialize FastMCP server early (without secrets/auth);
# name and version do not require secrets.
mcp = FastMCP("market-data-server", version=__version__)
logger.info("mcp_server_initialized", name="market-data-server", version=__version__)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request: Request) -> JSONResponse:
    """Liveness probe: Is the server process running?

    Returns:
        JSON response with service status and uptime
    """
    try:
        s = get_settings()
        uptime = time.time() - _server_start_time
        return JSONResponse(
            {
                "status": "alive",
                "service": s.server_name,
                "version": s.server_version,
                "uptime_seconds": round(uptime, 2),
            }
        )
    except RuntimeError:
        # Settings not loaded yet
        return JSONResponse(
            {
                "status": "starting",
                "service": "market-data-server",
                "version": __version__,
            }
        )


@mcp.custom_route("/health/ready", methods=["GET"])
async def health_check_ready(_request: Request) -> JSONResponse:
    """Readiness probe: Can the server handle requests?

    Verifies external API connectivity before returning ready status.

    Returns:
        JSON response with readiness status (200 if ready, 503 if not)
    """
    try:
        s = get_settings()
    except RuntimeError:
        return JSONResponse(
            {"status": "not_ready", "reason": "Settings not loaded"},
            status_code=503,
        )

    # Check FMP API connectivity
    try:
        async with FMPClient(s) as client:
            api_healthy = await client.health_check()
    except Exception as e:
        logger.warning("health_check_failed", error=str(e))
        api_healthy = False

    if not api_healthy:
        return JSONResponse(
            {
                "status": "not_ready",
                "reason": "FMP API unreachable",
                "service": s.server_name,
            },
            status_code=503,
        )

    return JSONResponse(
        {
            "status": "ready",
            "service": s.server_name,
            "version": s.server_version,
        }
    )


@mcp.tool(
    annotations={
        "title": "Get Real-Time Quote",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def market_data_get_quote_tool(symbol: str) -> dict[str, Any]:
    """Get full real-time quote with OHLCV data.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')

    Returns:
        Real-time quote with price, change, volume, and market data
    """
    try:
        result = await get_quote(symbol)
        return truncate_response(result)
    except Exception as e:
        log_error(logger, e, context={"tool": "market_data_get_quote_tool", "symbol": symbol})
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Latest Trade Snapshot",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def market_data_get_latest_trade_tool(symbol: str) -> dict[str, Any]:
    """Get fast price snapshot (condensed quote).

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')

    Returns:
        Minimal quote data with current price and volume
    """
    try:
        result = await get_latest_trade(symbol)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger, e, context={"tool": "market_data_get_latest_trade_tool", "symbol": symbol}
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Historical Price Candles",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def market_data_get_candles_tool(
    symbol: str,
    interval: Literal["1min", "5min", "15min", "30min", "1hour", "4hour", "daily"] = "daily",
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> dict[str, Any]:
    """Get historical price candles (OHLCV data), returned oldest-first.

    Daily candles are split- and dividend-adjusted (total-return basis);
    intraday candles are raw prices.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        interval: Time interval (1min, 5min, 15min, 30min, 1hour, 4hour, daily)
        from_date: Start date in YYYY-MM-DD format (optional)
        to_date: End date in YYYY-MM-DD format (optional)
        limit: Maximum number of candles to return (default: 30, max: 130).
            Requests above 130 are clamped; pagination metadata echoes both
            the requested and effective limit.
        offset: Number of candles to skip. Candles are oldest-first, so a
            higher offset pages forward in time.

    Returns:
        Historical OHLCV candle data with pagination metadata
    """
    try:
        result = await get_candles(symbol, interval, from_date, to_date, limit, offset)
        return truncate_response(result)
    except Exception as e:
        log_error(logger, e, context={"tool": "market_data_get_candles_tool", "symbol": symbol})
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Market Movers",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def market_data_get_movers_tool(
    mover_type: Literal["gainers", "losers", "actives"] = "gainers",
    index: Literal["sp500", "nasdaq100", "dowjones"] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Get market movers (top gainers, losers, or most active stocks).

    Args:
        mover_type: Type of movers to retrieve (gainers, losers, or actives)
        index: Scope results to a specific index (sp500, nasdaq100, dowjones).
            nasdaq100 is the Nasdaq-100 index (~100 names), not the full
            Nasdaq exchange or the Nasdaq Composite. When provided,
            batch-quotes all index constituents and returns the top movers
            sorted by change % (or volume for actives). Constituent lists are
            cached 24h. Omit for exchange-wide movers.
        limit: Max results to return for index movers (default 20, ignored
            when index is omitted)

    Returns:
        List of stocks with price change and volume data
    """
    try:
        result = await get_movers(mover_type, index=index, limit=limit)
        return truncate_response(result)
    except Exception as e:
        log_error(logger, e, context={"tool": "market_data_get_movers_tool", "type": mover_type})
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Market Sector Snapshot",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def market_data_get_market_snapshot_tool() -> dict[str, Any]:
    """Get market sector performance overview.

    Returns:
        Sector performance data with price changes
    """
    try:
        result = await get_market_snapshot()
        return truncate_response(result)
    except Exception as e:
        log_error(logger, e, context={"tool": "market_data_get_market_snapshot_tool"})
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Check Market Open Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def market_data_is_market_open_tool() -> dict[str, Any]:
    """Check if the market is currently open.

    Returns:
        Market open status and hours information
    """
    try:
        result = await is_market_open()
        return truncate_response(result)
    except Exception as e:
        log_error(logger, e, context={"tool": "market_data_is_market_open_tool"})
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get After-Hours Quote",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def market_data_get_afterhours_quote_tool(symbol: str) -> dict[str, Any]:
    """Get pre-market and after-hours quote data.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')

    Returns:
        Pre-market and after-hours price data
    """
    try:
        result = await get_afterhours_quote(symbol)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger, e, context={"tool": "market_data_get_afterhours_quote_tool", "symbol": symbol}
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Short Volume Data",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def market_data_get_short_volume_tool(
    symbol: str, limit: int = 20, offset: int = 0
) -> dict[str, Any]:
    """Get historical short sale volume data.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        limit: Maximum number of records to return (default: 50)
        offset: Number of records to skip (for pagination)

    Returns:
        Historical short volume data with short interest ratio
    """
    try:
        result = await get_short_volume(symbol, limit, offset)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger, e, context={"tool": "market_data_get_short_volume_tool", "symbol": symbol}
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Technical Indicators",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def market_data_get_technical_indicators_tool(
    symbol: str,
    indicator_type: Literal["RSI", "SMA", "EMA", "WMA", "DEMA", "TEMA", "ADX"],
    period: int = 10,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    """Get technical indicators for a symbol.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        indicator_type: Type of technical indicator (RSI, SMA, EMA, etc.)
        period: Period for the indicator calculation (default: 10)
        limit: Maximum number of records to return (default: 50)
        offset: Number of records to skip (for pagination)

    Returns:
        Technical indicator data with calculated values
    """
    try:
        result = await get_technical_indicators(symbol, indicator_type, period, limit, offset)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "market_data_get_technical_indicators_tool",
                "symbol": symbol,
                "indicator_type": indicator_type,
            },
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "List Available Commodities",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def market_data_list_commodities_tool() -> dict[str, Any]:
    """List available commodity/futures symbols with display names.

    Returns:
        List of commodity symbols and their names
    """
    try:
        result = await list_commodities()
        return truncate_response(result)
    except Exception as e:
        log_error(logger, e, context={"tool": "market_data_list_commodities_tool"})
        return format_api_error(e, "FMP")


async def main() -> None:
    """Main entry point for the MCP server."""

    try:
        # Load settings at startup (no import-time side effects)
        settings = bootstrap()

        logger.info(
            "starting_mcp_server",
            transport=settings.transport,
            host=settings.host,
            port=settings.port,
        )

        # Add CORS middleware for web-based MCP clients
        cors_middleware = [
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            )
        ]

        await mcp.run_async(
            transport=settings.transport,
            host=settings.host,
            port=settings.port,
            path="/mcp",  # AgentCore expects streamable-HTTP at '/mcp'
            stateless_http=True,  # AgentCore requires stateless streamable-HTTP
            middleware=cors_middleware,
        )
    except Exception as e:
        log_error(logger, e, context={"event": "server_failed"})
        raise


if __name__ == "__main__":
    asyncio.run(main())
