"""FastMCP server for Polymarket prediction market analysis — 11 tools.

Design doc: docs/design/POLYMARKET_ANALYSIS_SYSTEM.md
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .config import Settings, get_settings, load_settings
from .logging_config import configure_logging, get_logger, log_error
from .response_utils import format_api_error, truncate_response
from .tools import (
    backtest_prediction_setup,
    compare_prediction_markets,
    get_market_details,
    get_market_snapshot,
    get_price_history,
    get_top_holders,
    get_trade_flow,
    get_trader_leaderboard,
    get_wallet_activity,
    get_wallet_profile,
    search_prediction_markets,
)

logger = get_logger(__name__)

mcp = FastMCP("prediction-markets-server", version=__version__)

_server_start_time = time.time()


# -- Tool 1: Search Markets ---------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Search Prediction Markets",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def search_prediction_markets_tool(
    query: str = "",
    limit: int = 10,
    active: bool = True,
    closed: bool = False,
    order: str = "volume24hr",
    end_date_min: str = "",
) -> dict[str, Any]:
    """Search and rank Polymarket prediction markets.

    Find markets by topic, keyword, or category. Returns markets with
    current pricing, volume, and liquidity data.

    Args:
        query: Search text (e.g., "election", "bitcoin", "fed rate").
        limit: Max results (1-50). Default 10.
        active: Include active markets. Default true.
        closed: Include resolved markets. Default false.
        order: Sort by volume24hr, liquidity, or endDate.
        end_date_min: ISO date (YYYY-MM-DD). Exclude markets ending
            before this date. Defaults to today when searching active
            markets, so expired markets are automatically filtered out.
            Pass "none" to disable and include expired markets.

    Returns:
        Ranked list of matching markets with pricing snapshots.

    """
    try:
        result = await search_prediction_markets(
            query,
            limit=limit,
            active=active,
            closed=closed,
            order=order,
            end_date_min=end_date_min,
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "search_prediction_markets", "query": query})
        return format_api_error(exc, "Polymarket")


# -- Tool 2: Market Details ---------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Get Market Details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def get_market_details_tool(
    condition_id: str = "",
    slug: str = "",
) -> dict[str, Any]:
    """Get full details for a Polymarket market.

    Returns question, outcomes, resolution criteria, timing, status,
    category, and current pricing.

    Args:
        condition_id: Market condition ID (hex). Preferred.
        slug: Market URL slug (e.g., "will-trump-win-2024"). Fallback.

    Returns:
        Complete market details including resolution info.

    """
    try:
        result = await get_market_details(condition_id=condition_id, slug=slug)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_market_details", "condition_id": condition_id})
        return format_api_error(exc, "Polymarket")


# -- Tool 3: Market Snapshot ---------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Get Market Snapshot",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def get_market_snapshot_tool(
    condition_id: str = "",
    slug: str = "",
) -> dict[str, Any]:
    """Get executable outcome-aware market state with live bid/ask and depth.

    Returns the actual executable pricing from the order book,
    not just the midpoint. Includes per-outcome top-of-book depth
    so a manual trader can assess fill quality for YES or NO.

    Args:
        condition_id: Market condition ID (fallback).
        slug: Market URL slug (preferred — fast and reliable).

    Returns:
        Per-outcome bid, ask, midpoint, spread, depth, volume, liquidity.

    """
    try:
        result = await get_market_snapshot(condition_id, slug=slug)
        return truncate_response(result)
    except Exception as exc:
        log_error(
            logger, exc, context={"tool": "get_market_snapshot", "condition_id": condition_id}
        )
        return format_api_error(exc, "Polymarket")


# -- Tool 4: Price History ---------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Get Price History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def get_price_history_tool(
    condition_id: str = "",
    slug: str = "",
    interval: str = "1d",
    fidelity: int = 60,
) -> dict[str, Any]:
    """Get historical price timeseries for a market's outcomes.

    Args:
        condition_id: Market condition ID (fallback).
        slug: Market URL slug (preferred — fast and reliable).
        interval: Time interval (1m, 5m, 1h, 6h, 1d, 1w, max).
        fidelity: Number of data points. Default 60.

    Returns:
        YES/NO price history arrays with timestamps.

    """
    try:
        result = await get_price_history(
            condition_id, slug=slug, interval=interval, fidelity=fidelity
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_price_history", "condition_id": condition_id})
        return format_api_error(exc, "Polymarket")


# -- Tool 5: Compare Markets --------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Compare Prediction Markets",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def compare_prediction_markets_tool(
    identifiers: list[str],
) -> dict[str, Any]:
    """Compare 2-5 markets side by side.

    Compares displayed odds, per-outcome executable spread/depth,
    liquidity, volume, and time to resolution across multiple markets.

    Args:
        identifiers: List of 2-5 market slugs or condition IDs.
            Slugs preferred for reliable lookups.

    Returns:
        Side-by-side comparison table.

    """
    try:
        result = await compare_prediction_markets(identifiers)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "compare_prediction_markets"})
        return format_api_error(exc, "Polymarket")


# -- Tool 6: Trade Flow -------------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Get Trade Flow",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def get_trade_flow_tool(
    condition_id: str,
    limit: int = 50,
) -> dict[str, Any]:
    """Summarize recent buy/sell flow and notable large trades.

    Shows recent trade direction, size distribution, and large prints.
    Includes caveat that flow is not proof of edge.

    Args:
        condition_id: Market condition ID.
        limit: Max trades to analyze (1-100). Default 50.

    Returns:
        Flow summary with buy/sell counts, large trades, recent prints.

    """
    try:
        result = await get_trade_flow(condition_id, limit=limit)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_trade_flow", "condition_id": condition_id})
        return format_api_error(exc, "Polymarket")


# -- Tool 7: Top Holders ------------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Get Top Holders",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def get_top_holders_tool(
    condition_id: str,
    limit: int = 20,
) -> dict[str, Any]:
    """Show holder concentration and risk for a market.

    Args:
        condition_id: Market condition ID.
        limit: Max holders (1-50). Default 20.

    Returns:
        Top holders, concentration metrics, and risk level.

    """
    try:
        result = await get_top_holders(condition_id, limit=limit)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_top_holders", "condition_id": condition_id})
        return format_api_error(exc, "Polymarket")


# -- Tool 8: Trader Leaderboard -----------------------------------------------


@mcp.tool(
    annotations={
        "title": "Get Trader Leaderboard",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def get_trader_leaderboard_tool(
    period: str = "all",
    limit: int = 20,
) -> dict[str, Any]:
    """Discover top Polymarket traders from the official leaderboard.

    Args:
        period: Time window — daily, weekly, monthly, or all (default).
        limit: Max traders (1-50). Default 20.

    Returns:
        Ranked trader list with volume, PnL, and win metrics.

    """
    try:
        result = await get_trader_leaderboard(period=period, limit=limit)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_trader_leaderboard"})
        return format_api_error(exc, "Polymarket")


# -- Tool 9: Wallet Activity --------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Get Wallet Activity",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def get_wallet_activity_tool(
    wallet_address: str,
    limit: int = 50,
) -> dict[str, Any]:
    """Show a wallet's recent trades and active markets.

    Args:
        wallet_address: Ethereum wallet address (0x...).
        limit: Max activity entries (1-100). Default 50.

    Returns:
        Recent trades, open positions, and directional behavior.

    """
    try:
        result = await get_wallet_activity(wallet_address, limit=limit)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_wallet_activity", "wallet": wallet_address})
        return format_api_error(exc, "Polymarket")


# -- Tool 10: Wallet Profile --------------------------------------------------


@mcp.tool(
    annotations={
        "title": "Get Wallet Profile",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def get_wallet_profile_tool(
    wallet_address: str,
) -> dict[str, Any]:
    """Descriptive summary of a wallet's trading profile.

    Shows preferred categories, activity level, and directional tendency.
    Does not claim durable alpha without proper historical controls.

    Args:
        wallet_address: Ethereum wallet address (0x...).

    Returns:
        Descriptive wallet summary (not a performance claim).

    """
    try:
        result = await get_wallet_profile(wallet_address)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_wallet_profile", "wallet": wallet_address})
        return format_api_error(exc, "Polymarket")


# -- Tool 11: Backtest Prediction Setup ----------------------------------------


@mcp.tool(
    annotations={
        "title": "Backtest Prediction Setup",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def backtest_prediction_setup_tool(
    setup_description: str,
    category: str = "",
    min_volume: float = 1000,
    min_liquidity: float = 500,
    price_threshold_min: float = 0.0,
    price_threshold_max: float = 1.0,
    forward_windows: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Run a descriptive event-study over resolved markets.

    V1 evaluates explicit filters only:
    - category
    - minimum volume / liquidity
    - YES entry price band
    - forward windows over historical YES price series

    The free-text setup description is preserved for context, but is
    not parsed into arbitrary rule logic. Does NOT use end-of-history
    wallet rankings or assume historical book depth.

    Args:
        setup_description: What setup to test (human description).
        category: Category filter (politics, crypto, etc.).
        min_volume: Minimum volume filter. Default 1000.
        min_liquidity: Minimum liquidity filter. Default 500.
        price_threshold_min: Min YES price at entry (0-1).
        price_threshold_max: Max YES price at entry (0-1).
        forward_windows: Evaluation windows (e.g., ["24h", "72h"]).
        limit: Max resolved markets to scan. Default 100.

    Returns:
        Sample size, forward-window stats, examples, and limitations.

    """
    try:
        result = await backtest_prediction_setup(
            setup_description,
            category=category,
            min_volume=min_volume,
            min_liquidity=min_liquidity,
            price_threshold_min=price_threshold_min,
            price_threshold_max=price_threshold_max,
            forward_windows=forward_windows,
            limit=limit,
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "backtest_prediction_setup"})
        return format_api_error(exc, "Polymarket")


# -- Health Endpoints ----------------------------------------------------------


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request: Request) -> JSONResponse:
    """Liveness probe — is the server running."""
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
        return JSONResponse(
            {
                "status": "starting",
                "service": "prediction-markets-server",
                "version": __version__,
            }
        )


@mcp.custom_route("/health/ready", methods=["GET"])
async def health_check_ready(_request: Request) -> JSONResponse:
    """Readiness probe — can the server handle requests."""
    try:
        s = get_settings()
    except RuntimeError:
        return JSONResponse(
            {"status": "not_ready", "reason": "Settings not loaded"},
            status_code=503,
        )

    # No API keys required — Polymarket APIs are public
    return JSONResponse({"status": "ready", "service": s.server_name})


# -- Bootstrap & Main ----------------------------------------------------------


def bootstrap() -> Settings:
    """Bootstrap server by loading settings."""
    logger.info("bootstrap_started", server="prediction-markets-server")
    try:
        settings = load_settings()
        logger.info(
            "bootstrap_complete",
            port=settings.port,
            gamma_url=settings.gamma_api_base_url,
            clob_url=settings.clob_api_base_url,
        )
        return settings
    except Exception as exc:
        log_error(logger, exc, context={"event": "bootstrap_failed"})
        raise


async def main() -> None:
    """Start the MCP server."""
    settings = bootstrap()
    configure_logging(settings.log_level)

    cors_middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]

    await mcp.run_async(
        transport=settings.transport,
        host=settings.host,
        port=settings.port,
        path="/mcp",
        stateless_http=True,
        middleware=cors_middleware,
    )


if __name__ == "__main__":
    asyncio.run(main())
