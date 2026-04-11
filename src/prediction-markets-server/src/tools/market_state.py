"""Market state, pricing, and comparison tools."""

from __future__ import annotations

import asyncio
from typing import Any

from ..clients.clob_client import ClobClient
from ..clients.gamma_client import GammaClient
from ..logging_config import get_logger

logger = get_logger(__name__)


async def _resolve_market(
    gamma: GammaClient,
    *,
    slug: str = "",
    condition_id: str = "",
) -> dict[str, Any]:
    """Resolve a market by slug (preferred) or condition_id.

    Slug lookups are single-request and reliable.
    ConditionId lookups may require a broad search fallback.

    Args:
        gamma: GammaClient instance.
        slug: Market URL slug (preferred).
        condition_id: Market condition ID (fallback).

    Returns:
        Normalized market dict.

    Raises:
        ValueError: If neither identifier is provided.

    """
    if slug:
        return await gamma.get_market_by_slug(slug)
    if condition_id:
        return await gamma.get_market(condition_id)
    msg = "Provide either slug or condition_id"
    raise ValueError(msg)


async def _load_outcome_books(
    market: dict[str, Any],
    clob: ClobClient,
) -> list[dict[str, Any]]:
    """Fetch executable book state for each outcome token."""
    token_ids = market.get("clob_token_ids", [])
    outcomes = market.get("outcomes", [])
    outcome_prices = market.get("outcome_prices", [])

    if not token_ids:
        return []

    books = await asyncio.gather(
        *[clob.get_order_book(token_id) for token_id in token_ids],
        return_exceptions=True,
    )

    outcome_books: list[dict[str, Any]] = []
    for idx, token_id in enumerate(token_ids):
        outcome_label = outcomes[idx] if idx < len(outcomes) else f"outcome_{idx}"
        displayed_price = outcome_prices[idx] if idx < len(outcome_prices) else None
        raw_book = books[idx]

        if isinstance(raw_book, BaseException):
            outcome_books.append(
                {
                    "outcome": outcome_label,
                    "token_id": token_id,
                    "displayed_price": displayed_price,
                    "error": str(raw_book),
                }
            )
            continue

        outcome_books.append(
            {
                "outcome": outcome_label,
                "token_id": token_id,
                "displayed_price": displayed_price,
                "best_bid": raw_book.get("best_bid"),
                "best_ask": raw_book.get("best_ask"),
                "midpoint": raw_book.get("midpoint"),
                "spread": raw_book.get("spread"),
                "bid_depth_top5": raw_book.get("bid_depth_top5", 0),
                "ask_depth_top5": raw_book.get("ask_depth_top5", 0),
            }
        )

    return outcome_books


async def get_market_snapshot(
    condition_id: str = "",
    *,
    slug: str = "",
) -> dict[str, Any]:
    """Get executable market state with per-outcome bid/ask/spread/depth.

    Args:
        condition_id: Market condition ID (fallback).
        slug: Market URL slug (preferred — reliable single-request lookup).

    Returns:
        Dict with per-outcome executable books, last trade,
        volume, and liquidity.

    """
    gamma = GammaClient()
    clob = ClobClient()
    try:
        market = await _resolve_market(gamma, slug=slug, condition_id=condition_id)
        resolved_cid = market.get("condition_id", condition_id)

        if not market.get("clob_token_ids"):
            return {
                "tool": "get_market_snapshot",
                "condition_id": resolved_cid,
                "slug": market.get("slug", slug),
                "error": "No CLOB token IDs found for this market",
            }

        outcome_books = await _load_outcome_books(market, clob)

        return {
            "tool": "get_market_snapshot",
            "condition_id": resolved_cid,
            "slug": market.get("slug", slug),
            "question": market["question"],
            "outcomes": market["outcomes"],
            "outcome_prices": market["outcome_prices"],
            "outcome_books": outcome_books,
            "last_trade_price": market["last_trade_price"],
            "volume": market["volume"],
            "volume_24h": market["volume_24h"],
            "liquidity": market["liquidity"],
            "active": market["active"],
            "end_date": market["end_date"],
            "accepting_orders": market["accepting_orders"],
            "tick_size": market["tick_size"],
            "order_min_size": market["order_min_size"],
            "note": (
                "Executable pricing is outcome-specific. "
                "Use the bid/ask for the YES or NO token you intend to trade; "
                "midpoint is not a fillable price."
            ),
        }
    finally:
        await gamma.close()
        await clob.close()


async def get_price_history(
    condition_id: str = "",
    *,
    slug: str = "",
    interval: str = "1d",
    fidelity: int = 60,
) -> dict[str, Any]:
    """Get historical price timeseries for a market.

    Args:
        condition_id: Market condition ID (fallback).
        slug: Market URL slug (preferred — reliable single-request lookup).
        interval: Time interval (1m, 5m, 1h, 6h, 1d, 1w, max).
        fidelity: Number of data points.

    Returns:
        Dict with YES/NO price history arrays.

    """
    gamma = GammaClient()
    clob = ClobClient()
    try:
        market = await _resolve_market(gamma, slug=slug, condition_id=condition_id)
        token_ids = market.get("clob_token_ids", [])

        resolved_cid = market.get("condition_id", condition_id)

        if not token_ids:
            return {
                "tool": "get_price_history",
                "condition_id": resolved_cid,
                "slug": market.get("slug", slug),
                "error": "No CLOB token IDs found",
            }

        # Fetch history for all outcome tokens in parallel
        tasks = [
            clob.get_price_history(tid, interval=interval, fidelity=fidelity) for tid in token_ids
        ]
        histories = await asyncio.gather(*tasks, return_exceptions=True)

        outcomes = market.get("outcomes", [])
        outcome_history: dict[str, Any] = {}
        for i, hist in enumerate(histories):
            outcome_label = outcomes[i] if i < len(outcomes) else f"outcome_{i}"
            if isinstance(hist, dict):
                outcome_history[outcome_label] = {
                    "count": hist["count"],
                    "history": hist["history"],
                }
            elif isinstance(hist, BaseException):
                outcome_history[outcome_label] = {
                    "count": 0,
                    "history": [],
                    "error": str(hist),
                }

        return {
            "tool": "get_price_history",
            "condition_id": resolved_cid,
            "slug": market.get("slug", slug),
            "question": market["question"],
            "interval": interval,
            "fidelity": fidelity,
            "outcomes": outcome_history,
        }
    finally:
        await gamma.close()
        await clob.close()


async def compare_prediction_markets(
    identifiers: list[str],
) -> dict[str, Any]:
    """Compare 2-5 markets side by side.

    Args:
        identifiers: List of 2-5 market slugs or condition IDs.
            Slugs are preferred for reliable lookups.

    Returns:
        Dict with side-by-side comparison on displayed odds,
        per-outcome executable depth/spread, liquidity, volume,
        and time to resolution.

    """
    if len(identifiers) < 2:  # noqa: PLR2004
        msg = "Need at least 2 markets to compare"
        raise ValueError(msg)
    if len(identifiers) > 5:  # noqa: PLR2004
        msg = "Maximum 5 markets for comparison"
        raise ValueError(msg)

    gamma = GammaClient()
    clob = ClobClient()
    try:
        tasks = [gamma.get_market(mid) for mid in identifiers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        comparisons: list[dict[str, Any]] = []
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                comparisons.append(
                    {
                        "identifier": identifiers[i],
                        "error": str(result),
                    }
                )
                continue

            market = result
            outcome_books = await _load_outcome_books(market, clob)
            comparisons.append(
                {
                    "condition_id": market["condition_id"],
                    "slug": market.get("slug", identifiers[i]),
                    "question": market["question"],
                    "outcomes": market["outcomes"],
                    "outcome_prices": market["outcome_prices"],
                    "outcome_books": outcome_books,
                    "volume": market["volume"],
                    "volume_24h": market["volume_24h"],
                    "liquidity": market["liquidity"],
                    "end_date": market["end_date"],
                    "active": market["active"],
                    "category": market["category"],
                }
            )

        return {
            "tool": "compare_prediction_markets",
            "count": len(comparisons),
            "markets": comparisons,
        }
    finally:
        await gamma.close()
        await clob.close()
