"""MCP server for events, news, and time-sensitive market catalysts."""

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
from .tools import get_dividends, get_earnings, search_market_news

# Server start time for uptime tracking
_server_start_time: float = time.time()

# Configure logging first
configure_logging()
logger = get_logger(__name__)


def bootstrap() -> Settings:
    """Bootstrap server by loading settings from env vars.

    Returns:
        Application settings

    Raises:
        Exception: If bootstrap fails
    """
    logger.info("bootstrap_started", server="events-news-server")

    try:
        logger.info("loading_settings", source="env")
        settings = load_settings()
        logger.info("settings_loaded")
        logger.info("bootstrap_complete")
        return settings

    except Exception as e:
        log_error(logger, e, context={"event": "bootstrap_failed"})
        raise


# Initialize FastMCP server early (without secrets);
# name and version do not require secrets.
mcp = FastMCP("events-news-server", version=__version__)
logger.info("mcp_server_initialized", name="events-news-server", version=__version__)


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
                "service": "events-news-server",
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
        "title": "Search Market News",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    }
)
async def events_news_search_market_news_tool(
    query: str,
    ticker: str | None = None,
    time_range: str | None = "week",
    limit: int = 5,
) -> dict[str, Any]:
    """AI-powered search for financial and market news using Tavily.

    Use this tool to find current news about stocks, markets, companies, or
    financial events. Supports natural language queries - ask questions like
    you would to a research assistant.

    WHEN TO USE:
    - Finding latest news about a specific stock or company
    - Researching market-moving events (earnings, FDA approvals, M&A)
    - Understanding why a stock price moved
    - Getting sector or market-wide news and sentiment
    - Finding analyst opinions or price target changes

    QUERY TIPS:
    - Be specific: "NVIDIA earnings beat" > "NVIDIA news"
    - Include context: "Tesla delivery numbers Q4" > "Tesla deliveries"
    - Ask questions: "why did Apple stock drop today"
    - Combine topics: "AI chip demand semiconductor stocks"

    Args:
        query: Natural language search query describing the news you need.
            Examples: 'earnings beat', 'FDA approval', 'why did stock drop',
            'analyst upgrade', 'supply chain issues', 'market outlook'
        ticker: Stock ticker symbol to focus the search (e.g., 'AAPL', 'TSLA').
            When provided, search results prioritize news about this company.
        time_range: How recent the news should be. Options:
            'day' - last 24 hours (breaking news)
            'week' - last 7 days (default, good for recent developments)
            'month' - last 30 days (for broader context)
            'year' - last 365 days (for historical research)
        limit: Number of articles to return (1-20, default: 5)

    Returns:
        News articles with titles, URLs, content summaries, sources, and
        relevance scores. Articles are ranked by relevance to your query.
    """
    try:
        result = await search_market_news(query, ticker, time_range, limit)  # type: ignore[arg-type]
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "events_news_search_market_news_tool",
                "query": query,
                "ticker": ticker,
                "time_range": time_range,
            },
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Earnings",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def events_news_get_earnings_tool(
    symbol: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Get earnings history and upcoming earnings for a specific ticker.

    Returns earnings sorted with reported results (actual EPS/revenue)
    before estimated/upcoming entries. Use this to find past and future
    earnings announcements for a stock.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL', 'MSFT')
        limit: Maximum number of earnings records to return (default: 5)

    Returns:
        List of earnings records with dates, EPS estimates/actual, and revenue
    """
    try:
        result = await get_earnings(symbol, limit)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "events_news_get_earnings_tool",
                "symbol": symbol,
                "limit": limit,
            },
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Dividends",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def events_news_get_dividends_tool(
    symbol: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Get dividend history for a specific ticker.

    Use this to find past dividend payments for a stock.
    Returns ex-dividend dates, payment dates, dividend amounts, and yield.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL', 'MSFT')
        limit: Maximum number of dividend records to return (default: 10)

    Returns:
        List of dividend records with dates, amounts, yield, and frequency
    """
    try:
        result = await get_dividends(symbol, limit)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "events_news_get_dividends_tool",
                "symbol": symbol,
                "limit": limit,
            },
        )
        return format_api_error(e, "FMP")


async def main() -> None:
    """Main entry point for the MCP server."""

    try:
        # Load settings from env at startup (no import-time side effects)
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
