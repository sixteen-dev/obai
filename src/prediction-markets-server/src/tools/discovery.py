"""Market discovery and detail tools."""

from __future__ import annotations

from typing import Any

from ..clients.gamma_client import GammaClient
from ..logging_config import get_logger

logger = get_logger(__name__)


def _build_market_summaries(
    event: dict[str, Any],
    event_url: str,
) -> list[dict[str, Any]]:
    """Build market summaries with actionability metadata."""
    summaries: list[dict[str, Any]] = []
    for m in event.get("markets", []):
        summaries.append(
            {
                "condition_id": m.get("condition_id", ""),
                "slug": m.get("slug", ""),
                "market_url": event_url,
                "question": m.get("question", ""),
                "outcomes": m.get("outcomes", []),
                "outcome_prices": m.get("outcome_prices", []),
                "best_bid": m.get("best_bid"),
                "best_ask": m.get("best_ask"),
                "spread": m.get("spread"),
                "volume_24h": m.get("volume_24h", 0),
                "liquidity": m.get("liquidity", 0),
                "active": m.get("active", False),
                "closed": m.get("closed", False),
                "accepting_orders": m.get("accepting_orders", False),
                "enable_order_book": m.get("enable_order_book"),
                "clob_token_ids": m.get("clob_token_ids", []),
                "end_date": m.get("end_date"),
            }
        )
    return summaries


async def search_prediction_markets(
    query: str = "",
    *,
    limit: int = 10,
) -> dict[str, Any]:
    """Search Polymarket events and markets by keyword.

    Uses the ``/public-search`` endpoint for text-based discovery.
    Returns events with nested markets grouped by topic.

    Args:
        query: Search text (e.g., "election", "bitcoin", "fed rate").
        limit: Max events to return (1-50).

    Returns:
        Dict with matching events, each containing nested markets
        with pricing and identifiers.

    """
    client = GammaClient()
    try:
        result = await client.public_search(query, limit_per_type=min(limit, 50))
        events = result.get("events", [])

        formatted_events: list[dict[str, Any]] = []
        for event in events[:limit]:
            event_url = f"https://polymarket.com/event/{event['slug']}" if event.get("slug") else ""
            market_summaries = _build_market_summaries(event, event_url)

            formatted_events.append(
                {
                    "title": event.get("title", ""),
                    "slug": event.get("slug", ""),
                    "event_url": event_url,
                    "active": event.get("active", False),
                    "volume": event.get("volume", 0),
                    "liquidity": event.get("liquidity", 0),
                    "tags": event.get("tags", []),
                    "market_count": len(market_summaries),
                    "markets": market_summaries,
                }
            )

        pagination = result.get("pagination", {})
        return {
            "tool": "search_prediction_markets",
            "query": query,
            "count": len(formatted_events),
            "has_more": pagination.get("hasMore", False),
            "total_results": pagination.get("totalResults", 0),
            "events": formatted_events,
        }
    finally:
        await client.close()


async def explore_trending_markets(
    *,
    tag_slug: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """Browse trending Polymarket events by 24-hour volume.

    Returns events (topic groups) ranked by recent trading
    activity.  Each event includes its nested markets with
    pricing.  Use ``tag_slug`` to narrow by category.

    Args:
        tag_slug: Filter by category tag (e.g., "bitcoin",
            "politics", "sports", "economy", "geopolitics",
            "nba", "soccer", "esports").  Empty for all.
        limit: Maximum events to return (1-20).

    Returns:
        Dict with ranked events, each containing nested markets.

    """
    capped_limit = min(max(limit, 1), 20)

    client = GammaClient()
    try:
        events = await client.search_events(
            limit=capped_limit,
            active=True,
            order="volume24hr",
            ascending=False,
            tag_slug=tag_slug,
        )

        results = []
        for event in events:
            event_url = f"https://polymarket.com/event/{event['slug']}" if event.get("slug") else ""
            market_summaries = _build_market_summaries(event, event_url)
            results.append(
                {
                    "id": event.get("id", ""),
                    "title": event.get("title", ""),
                    "slug": event.get("slug", ""),
                    "event_url": event_url,
                    "volume_24h": event.get("volume", 0),
                    "liquidity": event.get("liquidity", 0),
                    "tags": event.get("tags", []),
                    "market_count": len(market_summaries),
                    "markets": market_summaries[:10],
                }
            )

        return {
            "tool": "explore_trending_markets",
            "tag_slug": tag_slug,
            "count": len(results),
            "events": results,
        }
    finally:
        await client.close()


async def get_market_details(
    condition_id: str = "",
    slug: str = "",
) -> dict[str, Any]:
    """Get full details for a specific market.

    Args:
        condition_id: Market condition ID. Fallback identifier.
        slug: Market URL slug. Preferred identifier.

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
        if slug:
            market = await client.get_market_by_slug(slug)
        else:
            market = await client.get_market(condition_id)

        return {
            "tool": "get_market_details",
            "condition_id": market["condition_id"],
            "question_id": market["question_id"],
            "slug": market["slug"],
            "market_url": market["market_url"],
            "question": market["question"],
            "description": market["description"],
            "outcomes": market["outcomes"],
            "outcome_prices": market["outcome_prices"],
            "resolution_source": market["resolution_source"],
            "start_date": market["start_date"],
            "end_date": market["end_date"],
            "closed_time": market["closed_time"],
            "active": market["active"],
            "closed": market["closed"],
            "enable_order_book": market["enable_order_book"],
            "restricted": market["restricted"],
            "accepting_orders": market["accepting_orders"],
            "neg_risk": market["neg_risk"],
            "category": market["category"],
            "event_title": market["event_title"],
            "event_slug": market["event_slug"],
            "event_tags": market["event_tags"],
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
