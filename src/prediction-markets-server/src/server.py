"""FastMCP server for Polymarket prediction market analysis — 12 tools.

Design doc: docs/design/POLYMARKET_ANALYSIS_SYSTEM.md
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .clients.clob_client import ClobClient
from .clients.data_client import DataClient
from .clients.gamma_client import GammaClient
from .config import Settings, get_settings, load_settings
from .data import HistoryDownloader
from .engine import DEFAULT_PRICE_BUCKET_SIZE
from .logging_config import configure_logging, get_logger, log_error
from .response_utils import format_api_error, truncate_response
from .storage import PredictionDuckDBManager, PredictionStore
from .tools import (
    analyze_longshot_bias,
    analyze_prediction_calibration,
    backtest_prediction_rule,
    backtest_prediction_setup,
    compare_prediction_markets,
    ensure_prediction_market_history,
    estimate_empirical_kelly,
    explore_trending_markets,
    get_market_details,
    get_market_snapshot,
    get_price_history,
    get_top_holders,
    get_trade_flow,
    get_trader_leaderboard,
    get_wallet_activity,
    get_wallet_profile,
    monte_carlo_prediction_risk,
    search_prediction_markets,
)

logger = get_logger(__name__)

mcp = FastMCP("prediction-markets-server", version=__version__)

_server_start_time = time.time()
_prediction_store: PredictionStore | None = None


def get_prediction_store() -> PredictionStore:
    """Return the process-wide PredictionStore (must call bootstrap first)."""
    if _prediction_store is None:
        msg = "Prediction store not initialized - call bootstrap() first"
        raise RuntimeError(msg)
    return _prediction_store


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
        "title": "Backtest Prediction Setup (legacy — prefer Backtest Prediction Rule)",
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
    """Legacy event-study over resolved markets — prefer backtest_prediction_rule.

    This tool is kept for backwards compatibility. New historical
    backtests should use ``backtest_prediction_rule`` which validates a
    typed rule schema, applies the §10.4 terminal payoff math, and emits
    a compact ``monte_carlo_input`` suitable for the risk tool.

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


# -- Admin Tool: Ensure Prediction Market History -----------------------------
#
# This tool is registered conditionally in main() — it is exposed as an MCP
# tool only when settings.prediction_enable_admin_tools is True. Definition
# stays at module scope so the conditional registration call has something
# to attach.


async def ensure_prediction_market_history_tool(
    identifiers: list[str],
    interval: str = "max",
    fidelity: int = 60,
    include_trades: bool = False,
) -> dict[str, Any]:
    """Backfill prediction-market cache (admin/debug — exposes raw cache state).

    Hidden from the MCP tool list by default. Enable by setting
    PREDICTION_ENABLE_ADMIN_TOOLS=true. The normal analysis tools call the
    same backfill path internally and report cache_actions in their own
    responses.

    Args:
        identifiers: Slugs, condition_ids, or numeric market IDs.
        interval: CLOB price-history interval ("max" by default).
        fidelity: Sampling resolution in minutes (60 by default).
        include_trades: When True, also backfill recent Data API trades.

    Returns:
        Dict matching the §15 response contract (selected_condition_ids,
        cache_actions, data_coverage, quality_flags, limitations).

    """
    settings = get_settings()
    store = get_prediction_store()
    gamma = GammaClient()
    clob = ClobClient()
    data_client = DataClient()
    try:
        downloader = HistoryDownloader(
            gamma=gamma,
            clob=clob,
            data_client=data_client,
            store=store,
            data_freshness_hours=settings.prediction_data_freshness_hours,
        )
        result = await ensure_prediction_market_history(
            identifiers,
            downloader=downloader,
            interval=interval,
            fidelity=fidelity,
            include_trades=include_trades,
            max_history_points=settings.prediction_max_history_points,
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(
            logger,
            exc,
            context={
                "tool": "ensure_prediction_market_history",
                "identifiers": identifiers,
            },
        )
        return format_api_error(exc, "Polymarket")
    finally:
        await gamma.close()
        await clob.close()
        await data_client.close()


# -- Historical Tool: Calibration --------------------------------------------
#
# Registered conditionally in main() — gated by settings.prediction_enable_historical_tools.


async def analyze_prediction_calibration_tool(
    query: str = "",
    category: str = "",
    start_date: str = "",
    end_date: str = "",
    price_bucket_size: float = DEFAULT_PRICE_BUCKET_SIZE,
    min_lifetime_volume: float | None = None,
    max_markets: int = 100,
    fidelity: int = 60,
    sampling_mode: str = "market_bucket_once",
) -> dict[str, Any]:
    """Calibration of Polymarket implied probabilities vs realized outcomes.

    Args:
        query: Optional free-text topic (e.g. "election").
        category: Optional category match (case-insensitive).
        start_date: Optional ISO-8601 lower bound on market end_date.
        end_date: Optional ISO-8601 upper bound on market end_date.
        price_bucket_size: Width of price buckets (default 0.05).
        min_lifetime_volume: Optional static lifetime-volume filter
            (contamination is named in response limitations when used).
        max_markets: Hard cap on selected market universe (default 100).
        fidelity: Sampled-price resolution in minutes (default 60).
        sampling_mode: ``market_bucket_once`` (default), ``sample_weighted``,
            or ``both``.

    Returns:
        Dict matching the §15 response contract with per-bucket calibration
        metrics, sample sizes, quality flags, and a reliability_label.

    """
    settings = get_settings()
    store = get_prediction_store()
    gamma = GammaClient()
    clob = ClobClient()
    data_client = DataClient()
    try:
        downloader = HistoryDownloader(
            gamma=gamma,
            clob=clob,
            data_client=data_client,
            store=store,
            data_freshness_hours=settings.prediction_data_freshness_hours,
        )
        result = await analyze_prediction_calibration(
            downloader=downloader,
            store=store,
            query=query,
            category=category,
            start_date=_parse_iso_or_none(start_date),
            end_date=_parse_iso_or_none(end_date),
            price_bucket_size=price_bucket_size,
            min_lifetime_volume=min_lifetime_volume,
            max_markets=max_markets,
            fidelity=fidelity,
            sampling_mode=sampling_mode,  # type: ignore[arg-type]
            max_history_points=settings.prediction_max_history_points,
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(
            logger,
            exc,
            context={"tool": "analyze_prediction_calibration", "query": query},
        )
        return format_api_error(exc, "Polymarket")
    finally:
        await gamma.close()
        await clob.close()
        await data_client.close()


# -- Historical Tool: Longshot Bias ------------------------------------------


async def analyze_longshot_bias_tool(
    query: str = "",
    category: str = "",
    start_date: str = "",
    end_date: str = "",
    longshot_max_price: float = 0.10,
    favorite_min_price: float = 0.90,
    side: str = "yes",
    min_lifetime_volume: float | None = None,
    max_markets: int = 100,
    fidelity: int = 60,
    price_bucket_size: float = DEFAULT_PRICE_BUCKET_SIZE,
) -> dict[str, Any]:
    """Longshot vs favorite bias over resolved Polymarket markets.

    Args:
        query: Optional free-text topic.
        category: Optional category match.
        start_date: Optional ISO-8601 lower bound on end_date.
        end_date: Optional ISO-8601 upper bound on end_date.
        longshot_max_price: Strict upper bound for the longshot tail
            (default 0.10).
        favorite_min_price: Inclusive lower bound for the favorite tail
            (default 0.90).
        side: "yes" (default), "no", or "both" — carried into the
            response only in V1.
        min_lifetime_volume: Optional static lifetime-volume filter.
        max_markets: Hard cap on selected universe (default 100).
        fidelity: Sampled-price resolution in minutes.
        price_bucket_size: Width for the per-bucket breakdown.

    Returns:
        Dict matching the §15 response contract with longshot/favorite
        win rates, excess return, bucket detail, and reliability_label.

    """
    settings = get_settings()
    store = get_prediction_store()
    gamma = GammaClient()
    clob = ClobClient()
    data_client = DataClient()
    try:
        downloader = HistoryDownloader(
            gamma=gamma,
            clob=clob,
            data_client=data_client,
            store=store,
            data_freshness_hours=settings.prediction_data_freshness_hours,
        )
        result = await analyze_longshot_bias(
            downloader=downloader,
            store=store,
            query=query,
            category=category,
            start_date=_parse_iso_or_none(start_date),
            end_date=_parse_iso_or_none(end_date),
            longshot_max_price=longshot_max_price,
            favorite_min_price=favorite_min_price,
            side=side,  # type: ignore[arg-type]
            min_lifetime_volume=min_lifetime_volume,
            max_markets=max_markets,
            fidelity=fidelity,
            price_bucket_size=price_bucket_size,
            max_history_points=settings.prediction_max_history_points,
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "analyze_longshot_bias", "query": query})
        return format_api_error(exc, "Polymarket")
    finally:
        await gamma.close()
        await clob.close()
        await data_client.close()


def _parse_iso_or_none(value: str) -> datetime | None:
    """Parse an ISO-8601 string into an aware UTC datetime; empty input → None.

    A non-empty but unparseable input is a caller mistake (the agent passed
    something the tool cannot use), so surface it loudly via ValueError —
    the silent ``None`` fallback used to make the agent narrate "the tool
    rejected the date" while really the date was being dropped.
    """
    if not value:
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        msg = f"Invalid ISO-8601 date {value!r}; expected YYYY-MM-DD or YYYY-MM-DDTHH:MM:SSZ"
        raise ValueError(msg) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


# -- Historical Tool: Backtest Prediction Rule -------------------------------
#
# Registered conditionally under settings.prediction_enable_historical_tools.
# Inputs accept a dict rule payload (§10.4 V1 schema); the tool validates it
# via engine.rules.validate_rule before any DB/API work.


async def backtest_prediction_rule_tool(
    rule_json: str,
    query: str = "",
    max_markets: int = 100,
    fidelity: int = 60,
    seed: int = 12345,
) -> dict[str, Any]:
    """Backtest a structured prediction-market rule (§10.4 V1 schema).

    Args:
        rule_json: JSON-encoded rule payload. V1 supports {"side": "YES",
            "entry": {"price_min": float, "price_max": float}, "exit":
            {"type": "hold_to_resolution"}, "filters": {...}}. Unsupported
            fields are rejected loudly. Passed as a string (not an object)
            so the tool input schema stays strict-mode compatible.
        query: Optional free-text discovery topic.
        max_markets: Hard cap on selected market universe (default 100).
        fidelity: Sampled-price resolution in minutes (default 60).
        seed: Seed echoed into ``monte_carlo_input`` for downstream
            reproducibility of the risk tool.

    Returns:
        Dict matching the §15 response contract with sample_size,
        win_rate, distribution stats, examples, monte_carlo_input,
        limitations, quality_flags, and reliability_label.

    """
    try:
        rule = json.loads(rule_json)
    except json.JSONDecodeError as exc:
        return {"isError": True, "error": f"Invalid rule JSON: {exc}"}
    if not isinstance(rule, dict):
        return {"isError": True, "error": "rule_json must decode to an object."}
    settings = get_settings()
    store = get_prediction_store()
    gamma = GammaClient()
    clob = ClobClient()
    data_client = DataClient()
    try:
        downloader = HistoryDownloader(
            gamma=gamma,
            clob=clob,
            data_client=data_client,
            store=store,
            data_freshness_hours=settings.prediction_data_freshness_hours,
        )
        result = await backtest_prediction_rule(
            rule,
            downloader=downloader,
            store=store,
            query=query,
            max_markets=max_markets,
            fidelity=fidelity,
            seed=seed,
            max_history_points=settings.prediction_max_history_points,
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "backtest_prediction_rule"})
        return format_api_error(exc, "Polymarket")
    finally:
        await gamma.close()
        await clob.close()
        await data_client.close()


# -- Historical Tool: Monte Carlo Prediction Risk ----------------------------


def monte_carlo_prediction_risk_tool(
    monte_carlo_input_json: str | None = None,
    returns: list[float] | None = None,
    num_paths: int = 1000,
    starting_bankroll: float = 1.0,
    position_fraction: float = 1.0,
    max_drawdown_limit: float = 0.30,
    seed: int = 12345,
) -> dict[str, Any]:
    """IID bootstrap Monte Carlo over a return series (§13.2).

    Accepts either a JSON-encoded ``monte_carlo_input`` from
    ``backtest_prediction_rule`` or an inline ``returns`` list — exactly
    one of the two must be supplied. The response always includes the
    IID limitation language and an ``iid_monte_carlo_assumption`` quality
    flag.

    Args:
        monte_carlo_input_json: JSON string of the compact dict returned
            by backtest_prediction_rule. Passed as a string so the tool
            input schema stays strict-mode compatible.
        returns: Inline return-on-cost list (use only when not chaining
            from a backtest).
        num_paths: Number of synthetic paths (≤ 10,000).
        starting_bankroll: Initial bankroll per path.
        position_fraction: Fraction of bankroll risked per step in (0, 1].
        max_drawdown_limit: Threshold used for prob_exceeds_drawdown_limit.
        seed: PRNG seed — same inputs must produce identical output.

    Returns:
        Dict with sampling_method, terminal-wealth percentiles, drawdown
        percentiles, ruin_probability, limitations, quality_flags.

    """
    monte_carlo_input: dict[str, Any] | None
    if monte_carlo_input_json is None:
        monte_carlo_input = None
    else:
        try:
            monte_carlo_input = json.loads(monte_carlo_input_json)
        except json.JSONDecodeError as exc:
            return {"isError": True, "error": f"Invalid monte_carlo_input JSON: {exc}"}
        if not isinstance(monte_carlo_input, dict):
            return {
                "isError": True,
                "error": "monte_carlo_input_json must decode to an object.",
            }
    try:
        result = monte_carlo_prediction_risk(
            monte_carlo_input=monte_carlo_input,
            returns=returns,
            num_paths=num_paths,
            starting_bankroll=starting_bankroll,
            position_fraction=position_fraction,
            max_drawdown_limit=max_drawdown_limit,
            seed=seed,
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "monte_carlo_prediction_risk"})
        return format_api_error(exc, "Polymarket")


# -- Historical Tool: Estimate Empirical Kelly -------------------------------


def estimate_empirical_kelly_tool(
    monte_carlo_input_json: str | None = None,
    returns: list[float] | None = None,
    starting_bankroll: float | None = None,
    max_drawdown_limit: float | None = None,
    confidence_haircut: float = 0.5,
    win_prob: float | None = None,
    payoff_odds: float | None = None,
    seed: int = 4242,
) -> dict[str, Any]:
    """Empirical Kelly + drawdown-constrained sizing (§10.6).

    When ``starting_bankroll`` and ``max_drawdown_limit`` are both
    supplied, returns numerical fractions. When either is missing, returns
    qualitative guidance only (per §13.3 — precise sizing requires
    constraints).

    Args:
        monte_carlo_input_json: JSON string of the compact dict from
            backtest_prediction_rule. Passed as a string so the tool
            input schema stays strict-mode compatible.
        returns: Inline return-on-cost list (alternative to monte_carlo_input).
        starting_bankroll: Optional bankroll; absent → qualitative output.
        max_drawdown_limit: Optional drawdown cap; absent → qualitative output.
        confidence_haircut: Multiplier in [0, 1] applied to half-Kelly.
        win_prob: Optional closed-form Kelly input.
        payoff_odds: Optional closed-form Kelly input.
        seed: PRNG seed for the inner drawdown Monte Carlo.

    Returns:
        Dict with sample_size, source_backtest_fingerprint, metrics (or
        None when qualitative), limitations, quality_flags.

    """
    monte_carlo_input: dict[str, Any] | None
    if monte_carlo_input_json is None:
        monte_carlo_input = None
    else:
        try:
            monte_carlo_input = json.loads(monte_carlo_input_json)
        except json.JSONDecodeError as exc:
            return {"isError": True, "error": f"Invalid monte_carlo_input JSON: {exc}"}
        if not isinstance(monte_carlo_input, dict):
            return {
                "isError": True,
                "error": "monte_carlo_input_json must decode to an object.",
            }
    try:
        result = estimate_empirical_kelly(
            monte_carlo_input=monte_carlo_input,
            returns=returns,
            starting_bankroll=starting_bankroll,
            max_drawdown_limit=max_drawdown_limit,
            confidence_haircut=confidence_haircut,
            win_prob=win_prob,
            payoff_odds=payoff_odds,
            seed=seed,
        )
        return truncate_response(result)
    except Exception as exc:
        log_error(logger, exc, context={"tool": "estimate_empirical_kelly"})
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
    """Readiness probe — verifies Gamma upstream is reachable.

    Settings alone aren't a useful readiness signal because Polymarket APIs
    are public (no keys to check). Probe Gamma for a 1-row response with a
    tight timeout — if that fails, every prediction-market tool will also
    fail and the service should report not-ready.
    """
    try:
        s = get_settings()
    except RuntimeError:
        return JSONResponse(
            {"status": "not_ready", "reason": "Settings not loaded"},
            status_code=503,
        )

    probe_url = f"{s.gamma_api_base_url.rstrip('/')}/events?limit=1"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(probe_url)
            response.raise_for_status()
    except Exception as exc:
        return JSONResponse(
            {
                "status": "not_ready",
                "reason": f"Gamma upstream unreachable: {type(exc).__name__}",
                "service": s.server_name,
            },
            status_code=503,
        )

    return JSONResponse({"status": "ready", "service": s.server_name})


# -- Bootstrap & Main ----------------------------------------------------------


def bootstrap() -> Settings:
    """Bootstrap server by loading settings and opening the prediction store."""
    global _prediction_store  # noqa: PLW0603
    logger.info("bootstrap_started", server="prediction-markets-server")
    try:
        settings = load_settings()
        manager = PredictionDuckDBManager(
            db_path=settings.prediction_duckdb_path,
            memory_limit=settings.prediction_duckdb_memory_limit,
        )
        _prediction_store = PredictionStore(manager=manager)
        _prediction_store.ensure_connected()
        logger.info(
            "bootstrap_complete",
            port=settings.port,
            gamma_url=settings.gamma_api_base_url,
            clob_url=settings.clob_api_base_url,
            duckdb_path=settings.prediction_duckdb_path,
            admin_tools=settings.prediction_enable_admin_tools,
        )
        return settings
    except Exception as exc:
        log_error(logger, exc, context={"event": "bootstrap_failed"})
        raise


async def main() -> None:
    """Start the MCP server."""
    settings = bootstrap()
    configure_logging(settings.log_level)

    if settings.prediction_enable_admin_tools:
        mcp.tool(
            annotations={
                "title": "Ensure Prediction Market History (admin)",
                "readOnlyHint": False,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )(ensure_prediction_market_history_tool)
        logger.info("admin_tool_registered", tool="ensure_prediction_market_history")

    if settings.prediction_enable_historical_tools:
        mcp.tool(
            annotations={
                "title": "Analyze Prediction Calibration",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )(analyze_prediction_calibration_tool)
        mcp.tool(
            annotations={
                "title": "Analyze Longshot Bias",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )(analyze_longshot_bias_tool)
        mcp.tool(
            annotations={
                "title": "Backtest Prediction Rule",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )(backtest_prediction_rule_tool)
        mcp.tool(
            annotations={
                "title": "Monte Carlo Prediction Risk",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )(monte_carlo_prediction_risk_tool)
        mcp.tool(
            annotations={
                "title": "Estimate Empirical Kelly",
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": True,
            },
        )(estimate_empirical_kelly_tool)
        logger.info(
            "historical_tools_registered",
            tools=[
                "analyze_prediction_calibration",
                "analyze_longshot_bias",
                "backtest_prediction_rule",
                "monte_carlo_prediction_risk",
                "estimate_empirical_kelly",
            ],
        )

    cors_middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]

    try:
        await mcp.run_async(
            transport=settings.transport,
            host=settings.host,
            port=settings.port,
            path="/mcp",
            stateless_http=True,
            middleware=cors_middleware,
        )
    finally:
        if _prediction_store is not None:
            _prediction_store.close()


if __name__ == "__main__":
    asyncio.run(main())
