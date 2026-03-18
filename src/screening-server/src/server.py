"""MCP server for stock screening and ticker discovery via FMP API."""

import asyncio
import time
from typing import Any

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
    list_available_industries,
    list_available_sectors,
    screen_stocks,
    search_company_by_name,
    search_company_by_symbol,
)

# Server start time for uptime tracking
_server_start_time: float = time.time()

# Configure logging first
configure_logging()
logger = get_logger(__name__)


def bootstrap() -> Settings:
    """Bootstrap server by loading settings from environment variables.

    Returns:
        Application settings

    Raises:
        Exception: If bootstrap fails
    """
    logger.info("bootstrap_started", server="screening-server")

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
mcp = FastMCP("screening-server", version=__version__)
logger.info("mcp_server_initialized", name="screening-server", version=__version__)


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
                "service": "screening-server",
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


# =============================================================================
# Screening Tools - Stock Discovery and Ticker Resolution
# =============================================================================


@mcp.tool(
    annotations={
        "title": "Screen Stocks",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def screening_screen_stocks_tool(
    market_cap_more_than: int | None = None,
    market_cap_lower_than: int | None = None,
    price_more_than: float | None = None,
    price_lower_than: float | None = None,
    volume_more_than: int | None = None,
    beta_more_than: float | None = None,
    beta_lower_than: float | None = None,
    sector: str | None = None,
    industry: str | None = None,
    country: str | None = None,
    exchange: str | None = None,
    is_etf: bool | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Screen stocks with various filters for idea generation.

    Market-wide stock screening to find candidates matching specific criteria.
    Use this tool when you need to discover stocks without knowing specific symbols.

    Common use cases:
    - "Find tech stocks under $50B market cap"
    - "Show me high-volume stocks with low beta"
    - "Screen for healthcare stocks in the US"

    Args:
        market_cap_more_than: Minimum market cap (e.g., 10000000000 for $10B)
        market_cap_lower_than: Maximum market cap
        price_more_than: Minimum stock price
        price_lower_than: Maximum stock price
        volume_more_than: Minimum daily volume
        beta_more_than: Minimum beta (volatility relative to market)
        beta_lower_than: Maximum beta
        sector: Sector filter (Technology, Healthcare, Financial Services, etc.)
        industry: Industry filter (more specific than sector)
        country: Country code (US, CN, GB, etc.)
        exchange: Exchange (NASDAQ, NYSE, AMEX)
        is_etf: True to filter for ETFs only
        limit: Maximum results (default: 25, max: 100)

    Returns:
        Screening results with metadata and filtered stock list
    """
    try:
        result = await screen_stocks(
            market_cap_more_than=market_cap_more_than,
            market_cap_lower_than=market_cap_lower_than,
            price_more_than=price_more_than,
            price_lower_than=price_lower_than,
            volume_more_than=volume_more_than,
            beta_more_than=beta_more_than,
            beta_lower_than=beta_lower_than,
            sector=sector,
            industry=industry,
            country=country,
            exchange=exchange,
            is_etf=is_etf,
            limit=limit,
        )
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "screening_screen_stocks_tool",
                "sector": sector,
                "country": country,
            },
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "List Available Sectors",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def screening_list_available_sectors_tool() -> dict[str, Any]:
    """List all valid sector values accepted by the stock screener.

    Call this tool BEFORE using the screen_stocks tool with a sector
    filter. It returns the exact sector strings the API accepts,
    preventing mismatches from incorrect naming conventions.

    Returns:
        Sorted list of all valid sector names
    """
    try:
        result = await list_available_sectors()
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={"tool": "screening_list_available_sectors_tool"},
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "List Available Industries",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
async def screening_list_available_industries_tool() -> dict[str, Any]:
    """List all valid industry values accepted by the stock screener.

    Call this tool BEFORE using the screen_stocks tool with an industry
    filter. It returns the exact industry strings the API accepts,
    preventing mismatches from typos or incorrect formatting.

    Returns:
        Sorted list of all valid industry names
    """
    try:
        result = await list_available_industries()
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={"tool": "screening_list_available_industries_tool"},
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Search Company by Name",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def screening_search_by_name_tool(
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Search companies by name to resolve ticker symbols.

    Use this tool when a user mentions a company name and you need to find
    the corresponding ticker symbol.

    Common use cases:
    - "What's the ticker for Palantir?"
    - "Find the symbol for Apple"
    - "Look up Tesla's ticker"

    Args:
        query: Company name or partial name (e.g., "Palantir", "Apple Inc")
        limit: Maximum results (default: 10, max: 20)

    Returns:
        Search results with matching companies and their symbols
    """
    try:
        result = await search_company_by_name(
            query=query,
            limit=limit,
        )
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "screening_search_by_name_tool",
                "query": query,
            },
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Search Company by Symbol",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def screening_search_by_symbol_tool(
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Search companies by symbol to resolve partial or typo tickers.

    Use this tool when you need to validate a ticker symbol or find
    similar symbols. Handles typos, partial symbols, and variations.

    Common use cases:
    - Validate "AAPL" is correct for Apple
    - Find what "AAP" might refer to
    - Handle typos like "AAPLL"

    Args:
        query: Ticker or partial ticker (e.g., "AAPL", "AAP")
        limit: Maximum results (default: 10, max: 20)

    Returns:
        Search results with matching companies and their symbols
    """
    try:
        result = await search_company_by_symbol(
            query=query,
            limit=limit,
        )
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "screening_search_by_symbol_tool",
                "query": query,
            },
        )
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
