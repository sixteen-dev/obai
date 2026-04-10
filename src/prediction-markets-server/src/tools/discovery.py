"""Market discovery and detail tools."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..clients.gamma_client import GammaClient
from ..logging_config import get_logger

logger = get_logger(__name__)


async def search_prediction_markets(
    query: str = "",
    *,
    limit: int = 10,
    active: bool = True,
    closed: bool = False,
    order: str = "volume24hr",
    end_date_min: str = "",
) -> dict[str, Any]:
    """Search and rank relevant Polymarket markets.

    Args:
        query: Search text to filter markets.
        limit: Maximum results (1-50).
        active: Include active markets.
        closed: Include resolved markets.
        order: Sort by (volume24hr, liquidity, endDate).
        end_date_min: ISO date (YYYY-MM-DD). Exclude markets ending
            before this date. Defaults to today when searching active
            (non-closed) markets so expired markets are filtered out.
            Pass "none" to disable.

    Returns:
        Dict with ranked matching markets, pricing snapshot, and volume.

    """
    # Default: exclude expired markets when searching for active markets.
    # The Gamma API marks markets as active even after their end_date,
    # so active=true alone is not sufficient.
    effective_end_date_min = end_date_min
    if not effective_end_date_min and active and not closed:
        effective_end_date_min = datetime.now(UTC).strftime("%Y-%m-%d")
    if effective_end_date_min == "none":
        effective_end_date_min = ""

    client = GammaClient()
    try:
        markets = await client.search_markets(
            query=query,
            limit=min(limit, 50),
            active=active,
            closed=closed,
            order=order,
            end_date_min=effective_end_date_min,
        )

        results = []
        for m in markets:
            results.append(
                {
                    "condition_id": m["condition_id"],
                    "question": m["question"],
                    "slug": m["slug"],
                    "outcomes": m["outcomes"],
                    "outcome_prices": m["outcome_prices"],
                    "best_bid": m["best_bid"],
                    "best_ask": m["best_ask"],
                    "spread": m["spread"],
                    "volume_24h": m["volume_24h"],
                    "volume": m["volume"],
                    "liquidity": m["liquidity"],
                    "end_date": m["end_date"],
                    "active": m["active"],
                    "closed": m["closed"],
                    "category": m["category"],
                }
            )

        return {
            "tool": "search_prediction_markets",
            "query": query,
            "count": len(results),
            "markets": results,
        }
    finally:
        await client.close()


async def get_market_details(
    condition_id: str = "",
    slug: str = "",
) -> dict[str, Any]:
    """Get full details for a specific market.

    Args:
        condition_id: Market condition ID. Preferred identifier.
        slug: Market URL slug. Used if condition_id not provided.

    Returns:
        Dict with question, outcomes, resolution summary, timing,
        status, tags, and category.

    Raises:
        ValueError: If neither condition_id nor slug provided.

    """
    if not condition_id and not slug:
        msg = "Provide either condition_id or slug"
        raise ValueError(msg)

    client = GammaClient()
    try:
        if condition_id:
            market = await client.get_market(condition_id)
        else:
            market = await client.get_market_by_slug(slug)

        return {
            "tool": "get_market_details",
            "condition_id": market["condition_id"],
            "question_id": market["question_id"],
            "slug": market["slug"],
            "question": market["question"],
            "description": market["description"],
            "outcomes": market["outcomes"],
            "outcome_prices": market["outcome_prices"],
            "resolution_source": market["resolution_source"],
            "start_date": market["start_date"],
            "end_date": market["end_date"],
            "active": market["active"],
            "closed": market["closed"],
            "accepting_orders": market["accepting_orders"],
            "neg_risk": market["neg_risk"],
            "category": market["category"],
            "group_item_title": market["group_item_title"],
            "clob_token_ids": market["clob_token_ids"],
            "volume": market["volume"],
            "volume_24h": market["volume_24h"],
            "liquidity": market["liquidity"],
            "best_bid": market["best_bid"],
            "best_ask": market["best_ask"],
            "spread": market["spread"],
            "last_trade_price": market["last_trade_price"],
            "tick_size": market["tick_size"],
            "order_min_size": market["order_min_size"],
        }
    finally:
        await client.close()
