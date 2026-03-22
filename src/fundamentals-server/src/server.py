"""MCP server for company fundamentals with AI-enhanced educational context."""

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
    get_analyst_outlook,
    get_company_profile,
    get_fundamentals,
    get_insider_trades,
    get_insider_trading_statistics,
    get_revenue_segments,
    get_sec_filings,
    get_valuation_metrics,
    search_fundamentals,
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
    logger.info("bootstrap_started", server="fundamentals-server")

    try:
        logger.info("loading_settings", source="env")
        settings = load_settings()
        logger.info("settings_loaded", qdrant_url=settings.qdrant_url)

        logger.info("bootstrap_complete")
        return settings

    except Exception as e:
        log_error(logger, e, context={"event": "bootstrap_failed"})
        raise


# Initialize FastMCP server early (without secrets/auth);
# name and version do not require secrets.
mcp = FastMCP("fundamentals-server", version=__version__)
logger.info("mcp_server_initialized", name="fundamentals-server", version=__version__)


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
                "service": "fundamentals-server",
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
    client = FMPClient()
    try:
        api_healthy = await client.health_check()
    except Exception as e:
        logger.warning("health_check_failed", error=str(e))
        api_healthy = False
    finally:
        await client.close()

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
        "title": "Get Financial Statements",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fundamentals_get_statement_tool(
    symbol: str,
    statement_type: Literal["income", "balance", "cashflow"],
    period: Literal["annual", "quarter"] = "annual",
    limit: int = 4,
    include_context: bool = True,
) -> dict[str, Any]:
    """Get financial statements (income statement, balance sheet, or cash flow).

    Use for detailed financial statement data like revenue, expenses, assets,
    liabilities, and cash flows.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        statement_type: Type of financial statement (income/balance/cashflow)
        period: Reporting period (annual or quarter)
        limit: Number of periods to return
        include_context: Include educational PDF context explaining metrics

    Returns:
        Financial statement data with optional educational context from PDFs
    """
    try:
        result = await get_fundamentals(symbol, statement_type, period, limit, include_context)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "fundamentals_get_statement_tool",
                "symbol": symbol,
                "statement_type": statement_type,
            },
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Company Profile",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fundamentals_get_company_profile_tool(
    symbol: str, include_context: bool = False
) -> dict[str, Any]:
    """Get company profile and basic information.

    Returns sector, industry, CEO, market cap, beta, and other company metadata.
    Use for company overview questions.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        include_context: Include industry/sector educational context from PDFs

    Returns:
        Company profile data (sector, industry, description, CEO, etc.)
    """
    try:
        result = await get_company_profile(symbol, include_context)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger, e, context={"tool": "fundamentals_get_company_profile_tool", "symbol": symbol}
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Valuation Metrics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fundamentals_get_valuation_metrics_tool(
    symbol: str,
    period: Literal["annual", "quarter"] = "annual",
    limit: int = 1,
) -> dict[str, Any]:
    """Get comprehensive valuation metrics and financial ratios.

    THIS IS THE PRIMARY TOOL for valuation questions. Includes:
    - Valuation ratios: P/E, P/B, P/S, EV/EBITDA, EV/Sales
    - Profitability: ROE, ROA, ROIC, gross/operating/net margins
    - Efficiency: asset turnover, inventory turnover
    - Leverage: debt/equity, debt/assets, interest coverage
    - Per-share: revenue, earnings, book value, free cash flow

    Use this for ANY question about P/E ratio, valuation, margins, ROE, or debt ratios.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        period: Reporting period ('annual' or 'quarter')
        limit: Number of periods to return (default: 1 for latest)

    Returns:
        Combined valuation metrics and financial ratios data
    """
    try:
        result = await get_valuation_metrics(symbol, period, limit)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger, e, context={"tool": "fundamentals_get_valuation_metrics_tool", "symbol": symbol}
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Analyst Outlook",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fundamentals_get_analyst_outlook_tool(
    symbol: str,
    estimates_limit: int = 2,
) -> dict[str, Any]:
    """Get comprehensive analyst outlook including estimates, price targets, and ratings.

    THIS IS THE PRIMARY TOOL for analyst-related questions. Combines:
    - Analyst estimates: EPS and revenue forecasts
    - Price target: consensus high/low/average from analysts
    - Company rating: buy/hold/sell recommendation and score

    Use this for questions about analyst opinions, price targets, EPS estimates,
    or buy/sell recommendations.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        estimates_limit: Number of estimate periods to return (default: 2)

    Returns:
        Combined analyst data with estimates, price targets, and rating
    """
    try:
        result = await get_analyst_outlook(symbol, estimates_limit)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={"tool": "fundamentals_get_analyst_outlook_tool", "symbol": symbol},
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Search Financial Education Content",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fundamentals_search_education_tool(
    query: str,
    top_k: int = 5,
    source_filter: str | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """Search educational content about financial fundamentals from processed PDFs.

    Use this to learn about financial concepts, investment strategies, and market fundamentals.
    The vector database contains 19 financial PDFs including guides on options, ETFs, bonds,
    ratios, asset allocation, and more.

    Args:
        query: Natural language query (e.g., "What are option strategies for volatility?")
        top_k: Number of results to return per page
        source_filter: Optional filter by specific PDF source name
        offset: Number of results to skip (for pagination)

    Returns:
        Relevant educational content chunks with source citations and pagination metadata
    """
    try:
        result = await search_fundamentals(query, top_k, source_filter, offset)
        return truncate_response(result)
    except Exception as e:
        log_error(logger, e, context={"tool": "fundamentals_search_education_tool", "query": query})
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get SEC Filings",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fundamentals_get_sec_filings_tool(
    symbol: str,
    limit: int = 5,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict[str, Any]:
    """Get SEC filings for a company (10-K, 10-Q, 8-K, etc.).

    Retrieves official SEC filings including annual reports, quarterly reports,
    and material event disclosures. Useful for deep fundamental research and
    regulatory compliance analysis.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        limit: Number of filings to return (default: 10)
        from_date: Start date (YYYY-MM-DD), defaults to 3 months ago
        to_date: End date (YYYY-MM-DD), defaults to today

    Returns:
        SEC filings with links to official documents
    """
    try:
        result = await get_sec_filings(symbol, limit, from_date, to_date)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={"tool": "fundamentals_get_sec_filings_tool", "symbol": symbol},
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Insider Trades",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fundamentals_get_insider_trades_tool(
    symbol: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Get individual insider transactions (who bought/sold, how many shares, at what price).

    Use when you need to know WHICH insiders traded and the details of each transaction.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        limit: Number of trades to return (default: 10)

    Returns:
        Individual trades with insider name, role, transaction type, shares, and price
    """
    try:
        result = await get_insider_trades(symbol, limit)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={"tool": "fundamentals_get_insider_trades_tool", "symbol": symbol},
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Insider Trading Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fundamentals_get_insider_trading_statistics_tool(
    symbol: str,
    limit: int = 4,
) -> dict[str, Any]:
    """Get quarterly insider buy/sell ratios and volume totals.

    Use when you need the overall insider sentiment trend, not individual trades.
    Key signal: acquiredDisposedRatio > 1 = insiders are net buyers (bullish).

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        limit: Number of most recent quarters (default: 4, max recommended: 8)

    Returns:
        Per-quarter aggregates: transaction counts, share volumes, and buy/sell ratio
    """
    try:
        result = await get_insider_trading_statistics(symbol, limit)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={
                "tool": "fundamentals_get_insider_trading_statistics_tool",
                "symbol": symbol,
            },
        )
        return format_api_error(e, "FMP")


@mcp.tool(
    annotations={
        "title": "Get Revenue Segments",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    }
)
async def fundamentals_get_revenue_segments_tool(
    symbol: str,
    period: Literal["annual", "quarter"] = "annual",
) -> dict[str, Any]:
    """Get revenue breakdown by product/business segment.

    Shows how revenue is distributed across different product lines or business units.
    Useful for understanding business diversification and which products drive earnings.
    Example: Apple's breakdown by iPhone, Mac, iPad, Services, Wearables.

    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL')
        period: Reporting period ('annual' or 'quarter')

    Returns:
        Revenue segmentation data by product/business line
    """
    try:
        result = await get_revenue_segments(symbol, period)
        return truncate_response(result)
    except Exception as e:
        log_error(
            logger,
            e,
            context={"tool": "fundamentals_get_revenue_segments_tool", "symbol": symbol},
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
