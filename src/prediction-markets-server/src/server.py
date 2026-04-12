"""FastMCP server for Polymarket prediction market analysis — 12 tools.

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
    explore_trending_markets,
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
) -> dict[str, Any]:
    """Search Polymarket events and markets by keyword.

    Returns events grouped by topic, each containing nested markets
    with pricing, slugs, and condition IDs. For browsing by
    category or volume without a keyword, use explore_trending_markets.

    Args:
        query: Search text (e.g., "election", "bitcoin", "fed rate").
        limit: Max events (1-50). Default 10.

    Returns:
        Events with nested markets, outcome prices, and identifiers
        (slug, condition_id, event_url).

    """
    try:
        result = await search_prediction_markets(query, limit=limit)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "search_prediction_markets", "query": query})
        return format_api_error(exc, "Polymarket")


# -- Tool 2: Explore Trending Markets -----------------------------------------


@mcp.tool(
    annotations={
        "title": "Explore Trending Markets",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def explore_trending_markets_tool(
    tag_slug: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """Browse trending Polymarket events ranked by 24-hour volume.

    Returns events with nested markets and pricing. Use for broad
    discovery without a keyword: "what's trending", "top crypto
    bets", "active politics markets".

    Args:
        tag_slug: Filter by tag (e.g., "politics", "crypto",
            "sports", "bitcoin", "elections", "nba", "soccer",
            "economy", "technology", "us-election"). Empty for all.
        limit: Max events (1-20). Default 10.

    Returns:
        Events ranked by volume with nested markets and event URLs.

    """
    try:
        result = await explore_trending_markets(tag_slug=tag_slug, limit=limit)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "explore_trending_markets", "tag_slug": tag_slug})
        return format_api_error(exc, "Polymarket")


# -- Tool 3: Market Details ---------------------------------------------------


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
    """Get full metadata for a single Polymarket market.

    Returns question, outcomes, resolution criteria, timing, status,
    category, pricing, and volume. Use slug from prior tool results
    or a Polymarket URL — do not fabricate slugs from titles.

    Args:
        condition_id: Market condition ID (0x hex). Fallback.
        slug: Market URL slug from tool data or Polymarket URL.

    Returns:
        Complete market metadata with resolution info and identifiers.

    """
    try:
        result = await get_market_details(condition_id=condition_id, slug=slug)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_market_details", "condition_id": condition_id})
        return format_api_error(exc, "Polymarket")


# -- Tool 4: Market Snapshot ---------------------------------------------------


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
    """Get live order-book state with per-outcome bid/ask and depth.

    Returns executable pricing from the CLOB order book — not just
    midpoint. Includes per-outcome top-of-book depth for assessing
    fill quality on YES or NO sides.

    Args:
        condition_id: Market condition ID (0x hex). Fallback.
        slug: Market URL slug from tool data or Polymarket URL.
            Do not fabricate from a title.

    Returns:
        Per-outcome bid, ask, midpoint, spread, depth, volume,
        liquidity, and identifiers.

    """
    try:
        result = await get_market_snapshot(condition_id, slug=slug)
        return truncate_response(result)
    except Exception as exc:
        log_error(
            logger, exc, context={"tool": "get_market_snapshot", "condition_id": condition_id}
        )
        return format_api_error(exc, "Polymarket")


# -- Tool 5: Price History ---------------------------------------------------


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
        condition_id: Market condition ID (0x hex). Fallback.
        slug: Market URL slug from tool data or Polymarket URL.
            Do not fabricate from a title.
        interval: Lookback window (1m, 1h, 6h, 1d, 1w, max, all).
        fidelity: Sampling resolution in minutes. 1 = per-minute,
            60 = hourly (default), 1440 = daily.

    Returns:
        YES/NO price arrays with timestamps.

    """
    try:
        result = await get_price_history(
            condition_id, slug=slug, interval=interval, fidelity=fidelity
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_price_history", "condition_id": condition_id})
        return format_api_error(exc, "Polymarket")


# -- Tool 6: Compare Markets --------------------------------------------------


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
    """Compare 2-5 markets side by side on odds, depth, and volume.

    Args:
        identifiers: 2-5 market slugs or condition IDs from tool data.
            Slugs preferred. Do not fabricate from titles.

    Returns:
        Side-by-side comparison with per-outcome spread/depth,
        liquidity, volume, and time to resolution.

    """
    try:
        result = await compare_prediction_markets(identifiers)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "compare_prediction_markets"})
        return format_api_error(exc, "Polymarket")


# -- Tool 7: Trade Flow -------------------------------------------------------


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
    """Summarize recent buy/sell flow and large trades for a market.

    Requires condition_id. If you only have a slug, resolve it first
    with get_market_details or get_market_snapshot.

    Args:
        condition_id: Market condition ID (0x hex) from tool data.
        limit: Max trades to analyze (1-100). Default 50.

    Returns:
        Buy/sell counts, size distribution, large prints.

    """
    try:
        result = await get_trade_flow(condition_id, limit=limit)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_trade_flow", "condition_id": condition_id})
        return format_api_error(exc, "Polymarket")


# -- Tool 8: Top Holders ------------------------------------------------------


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
    """Show holder concentration and whale risk for a market.

    Requires condition_id. If you only have a slug, resolve it first
    with get_market_details or get_market_snapshot.

    Args:
        condition_id: Market condition ID (0x hex) from tool data.
        limit: Max holders (1-50). Default 20.

    Returns:
        Top holders, concentration metrics, risk assessment.

    """
    try:
        result = await get_top_holders(condition_id, limit=limit)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_top_holders", "condition_id": condition_id})
        return format_api_error(exc, "Polymarket")


# -- Tool 9: Trader Leaderboard -----------------------------------------------


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
    time_period: str = "ALL",
    order_by: str = "PNL",
    limit: int = 20,
) -> dict[str, Any]:
    """Top Polymarket traders ranked by PnL or volume.

    Args:
        time_period: DAY, WEEK, MONTH, or ALL (default).
        order_by: PNL (default) or VOL.
        limit: Max traders (1-50). Default 20.

    Returns:
        Ranked traders with volume, PnL, and profile info.

    """
    try:
        result = await get_trader_leaderboard(
            time_period=time_period,
            order_by=order_by,
            limit=limit,
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_trader_leaderboard"})
        return format_api_error(exc, "Polymarket")


# -- Tool 10: Wallet Activity --------------------------------------------------


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
    """Recent trades and open positions for a wallet.

    Args:
        wallet_address: Ethereum address (0x...).
        limit: Max activity entries (1-100). Default 50.

    Returns:
        Recent trades, open positions, directional behavior.

    """
    try:
        result = await get_wallet_activity(wallet_address, limit=limit)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_wallet_activity", "wallet": wallet_address})
        return format_api_error(exc, "Polymarket")


# -- Tool 11: Wallet Profile --------------------------------------------------


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
    """Descriptive summary of a wallet's trading behavior.

    Args:
        wallet_address: Ethereum address (0x...).

    Returns:
        Category preferences, activity level, directional tendency.
        Descriptive only — not a performance claim.

    """
    try:
        result = await get_wallet_profile(wallet_address)
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "get_wallet_profile", "wallet": wallet_address})
        return format_api_error(exc, "Polymarket")


# -- Tool 12: Backtest Prediction Setup ----------------------------------------


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
    min_volume: float = 1000,
    min_liquidity: float = 500,
    price_threshold_min: float = 0.0,
    price_threshold_max: float = 1.0,
    forward_windows: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Event-study over resolved markets with structured filters.

    Scans closed markets and evaluates price movement after entry
    points matching the filter criteria. Does not parse free-text
    into rules — only the structured filters below are applied.

    Args:
        setup_description: What setup to test (context only).
        min_volume: Minimum lifetime volume. Default 1000.
        min_liquidity: Minimum liquidity (documentation only — not
            applied to closed markets whose books are gone).
        price_threshold_min: Min YES price at entry (0-1).
        price_threshold_max: Max YES price at entry (0-1).
        forward_windows: Windows to measure (e.g., ["24h", "72h",
            "to_resolution"]). Default ["24h", "72h", "to_resolution"].
        limit: Max resolved markets to scan. Default 100.

    Returns:
        Sample size, per-window stats, examples, and limitations.

    """
    try:
        result = await backtest_prediction_setup(
            setup_description,
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
