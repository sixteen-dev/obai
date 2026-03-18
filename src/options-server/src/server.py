"""MCP server for real-time options data via Massive API."""

import asyncio
import time
from typing import Any

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .clients.massive_client import MassiveClient
from .config import Settings, get_settings, load_settings
from .logging_config import configure_logging, get_logger, log_error
from .response_utils import format_api_error, truncate_response
from .tools import (
    get_latest_option_quote,
    get_latest_option_trade,
    get_option_aggregates,
    get_option_chain_snapshot,
    get_option_contract_snapshot,
    get_option_quotes_history,
    get_option_trades_history,
    list_option_contracts,
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
    logger.info("bootstrap_started", server="options-server")

    try:
        logger.info("loading_settings", source="env")
        settings = load_settings()
        logger.info("settings_loaded")

        logger.info("bootstrap_complete")
        return settings

    except Exception as e:
        log_error(logger, e, context={"event": "bootstrap_failed"})
        raise


# Initialize FastMCP server early (without secrets)
mcp = FastMCP("options-server", version=__version__)
logger.info("mcp_server_initialized", name="options-server", version=__version__)


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
                "service": "options-server",
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

    # Check Massive API connectivity
    try:
        async with MassiveClient(s) as client:
            api_healthy = await client.health_check()
    except Exception as e:
        logger.warning("health_check_failed", error=str(e))
        api_healthy = False

    if not api_healthy:
        return JSONResponse(
            {
                "status": "not_ready",
                "reason": "Massive API unreachable",
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


# =============================================================================
# MVP Tools - Critical Real-Time Options Endpoints
# =============================================================================


@mcp.tool(
    annotations={
        "title": "Get Option Chain Snapshot",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def options_get_chain_snapshot_tool(
    underlying_asset: str,
    expiration_date: str | None = None,
    strike_price: float | None = None,
    contract_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get real-time snapshot of option chain for an underlying asset.

    Returns pricing, Greeks, implied volatility, open interest, and underlying
    price context for all matching option contracts.

    Args:
        underlying_asset: Stock ticker symbol (e.g., 'AAPL')
        expiration_date: Filter by expiration date (YYYY-MM-DD)
        strike_price: Filter by exact strike price
        contract_type: Filter by 'call' or 'put'
        limit: Maximum results (max 250)

    Returns:
        Option chain snapshot with contracts and metadata
    """
    try:
        result = await get_option_chain_snapshot(
            underlying_asset=underlying_asset,
            expiration_date=expiration_date,
            strike_price=strike_price,
            contract_type=contract_type,
            limit=limit,
        )
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "options_get_chain_snapshot_tool",
                "underlying_asset": underlying_asset,
            },
        )
        return format_api_error(e, "Massive")


@mcp.tool(
    annotations={
        "title": "Get Option Contract Snapshot",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def options_get_contract_snapshot_tool(
    underlying_asset: str,
    option_contract: str,
) -> dict[str, Any]:
    """Get real-time snapshot for a single option contract.

    Provides complete details including last trade, last quote, Greeks,
    implied volatility, and open interest.

    Args:
        underlying_asset: Stock ticker symbol (e.g., 'AAPL')
        option_contract: Option symbol (e.g., 'O:AAPL240119C00125000')

    Returns:
        Complete contract snapshot with pricing and Greeks
    """
    try:
        result = await get_option_contract_snapshot(
            underlying_asset=underlying_asset,
            option_contract=option_contract,
        )
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "options_get_contract_snapshot_tool",
                "underlying_asset": underlying_asset,
                "option_contract": option_contract,
            },
        )
        return format_api_error(e, "Massive")


@mcp.tool(
    annotations={
        "title": "Get Latest Option Trade",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def options_get_latest_trade_tool(options_ticker: str) -> dict[str, Any]:
    """Get the most recent trade for an option contract.

    Args:
        options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')

    Returns:
        Latest trade with price, size, exchange, and timestamp
    """
    try:
        result = await get_latest_option_trade(options_ticker=options_ticker)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "options_get_latest_trade_tool",
                "options_ticker": options_ticker,
            },
        )
        return format_api_error(e, "Massive")


@mcp.tool(
    annotations={
        "title": "Get Latest Option Quote",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def options_get_latest_quote_tool(options_ticker: str) -> dict[str, Any]:
    """Get the most recent NBBO quote for an option contract.

    Args:
        options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')

    Returns:
        Latest NBBO with bid/ask prices and sizes
    """
    try:
        result = await get_latest_option_quote(options_ticker=options_ticker)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "options_get_latest_quote_tool",
                "options_ticker": options_ticker,
            },
        )
        return format_api_error(e, "Massive")


# =============================================================================
# Optional Tools - Reference and Historical Data
# =============================================================================


@mcp.tool(
    annotations={
        "title": "List Option Contracts",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def options_list_contracts_tool(
    underlying_ticker: str | None = None,
    expiration_date: str | None = None,
    contract_type: str | None = None,
    expired: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """List all option contracts with optional filters.

    Useful for discovering available contracts before querying snapshots.

    Args:
        underlying_ticker: Filter by underlying stock
        expiration_date: Filter by expiration date (YYYY-MM-DD)
        contract_type: Filter by 'call' or 'put'
        expired: Include expired contracts
        limit: Maximum results per page

    Returns:
        List of option contract references
    """
    try:
        result = await list_option_contracts(
            underlying_ticker=underlying_ticker,
            expiration_date=expiration_date,
            contract_type=contract_type,
            expired=expired,
            limit=limit,
        )
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "options_list_contracts_tool",
                "underlying_ticker": underlying_ticker,
            },
        )
        return format_api_error(e, "Massive")


@mcp.tool(
    annotations={
        "title": "Get Option Trades History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def options_get_trades_history_tool(
    options_ticker: str,
    timestamp_from: str | None = None,
    timestamp_to: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get historical tick-level trades for an option contract.

    Args:
        options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')
        timestamp_from: Start timestamp (nanoseconds or RFC3339)
        timestamp_to: End timestamp (nanoseconds or RFC3339)
        limit: Maximum results

    Returns:
        Historical trades data
    """
    try:
        result = await get_option_trades_history(
            options_ticker=options_ticker,
            timestamp_from=timestamp_from,
            timestamp_to=timestamp_to,
            limit=limit,
        )
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "options_get_trades_history_tool",
                "options_ticker": options_ticker,
            },
        )
        return format_api_error(e, "Massive")


@mcp.tool(
    annotations={
        "title": "Get Option Quotes History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def options_get_quotes_history_tool(
    options_ticker: str,
    timestamp_from: str | None = None,
    timestamp_to: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get historical NBBO quotes for an option contract.

    Args:
        options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')
        timestamp_from: Start timestamp (nanoseconds or RFC3339)
        timestamp_to: End timestamp (nanoseconds or RFC3339)
        limit: Maximum results

    Returns:
        Historical quotes data
    """
    try:
        result = await get_option_quotes_history(
            options_ticker=options_ticker,
            timestamp_from=timestamp_from,
            timestamp_to=timestamp_to,
            limit=limit,
        )
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "options_get_quotes_history_tool",
                "options_ticker": options_ticker,
            },
        )
        return format_api_error(e, "Massive")


@mcp.tool(
    annotations={
        "title": "Get Option Aggregates",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def options_get_aggregates_tool(
    options_ticker: str,
    multiplier: int,
    timespan: str,
    from_date: str,
    to_date: str,
    adjusted: bool = True,
) -> dict[str, Any]:
    """Get OHLCV aggregate bars for an option contract.

    Args:
        options_ticker: Option symbol (e.g., 'O:AAPL240119C00125000')
        multiplier: Size of timespan multiplier (e.g., 1, 5, 15)
        timespan: Timespan unit (minute, hour, day, week, month)
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        adjusted: Adjust for splits

    Returns:
        OHLCV bars data
    """
    try:
        result = await get_option_aggregates(
            options_ticker=options_ticker,
            multiplier=multiplier,
            timespan=timespan,
            from_date=from_date,
            to_date=to_date,
            adjusted=adjusted,
        )
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "options_get_aggregates_tool",
                "options_ticker": options_ticker,
            },
        )
        return format_api_error(e, "Massive")


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
