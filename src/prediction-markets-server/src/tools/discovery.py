"""Market discovery and detail tools."""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from ..clients.gamma_client import GammaClient
from ..logging_config import get_logger

logger = get_logger(__name__)

# Minimum markets to fetch for client-side relevance filtering.
# The Gamma API text search (_q) is unreliable, so we rely on
# client-side filtering over a broad set sorted by 24h volume.
_SEARCH_FETCH_LIMIT = 100

_GENERIC_QUERY_WORDS = {
    "a",
    "about",
    "active",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "best",
    "bet",
    "bets",
    "by",
    "current",
    "edge",
    "execute",
    "executable",
    "execution",
    "find",
    "for",
    "from",
    "give",
    "gains",
    "i",
    "in",
    "is",
    "it",
    "liquid",
    "liquidity",
    "manual",
    "market",
    "markets",
    "me",
    "no",
    "of",
    "odds",
    "on",
    "or",
    "polymarket",
    "prediction",
    "profit",
    "profitable",
    "pricing",
    "reward",
    "risk",
    "show",
    "spread",
    "the",
    "today",
    "to",
    "top",
    "trade",
    "trades",
    "volume",
    "what",
    "which",
    "will",
    "win",
    "yes",
}


def _tokenize_search_text(value: str) -> set[str]:
    """Normalize text into tokens useful for client-side relevance checks."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    tokens = set(re.findall(r"[a-z0-9]+", ascii_text))
    return {t for t in tokens if len(t) >= 3 and t not in _GENERIC_QUERY_WORDS}


def _prefix_match_count(query_tokens: set[str], market_tokens: set[str]) -> int:
    """Count query tokens that prefix-match any market token.

    "rate" matches "rates", "cut" matches "cutting", "btc" matches "btc".
    Minimum prefix length of 3 prevents false positives from short stems.
    """
    score = 0
    for qt in query_tokens:
        for mt in market_tokens:
            if mt.startswith(qt) or qt.startswith(mt):
                score += 1
                break
    return score


def _market_relevance_score(query_tokens: set[str], market: dict[str, Any]) -> int:
    """Count distinctive query tokens present in a market record."""
    searchable = " ".join(
        str(part)
        for part in (
            market.get("question", ""),
            market.get("slug", ""),
            " ".join(str(o) for o in market.get("outcomes", [])),
            market.get("category", ""),
            market.get("group_item_title", ""),
        )
        if part
    )
    market_tokens = _tokenize_search_text(searchable)
    return _prefix_match_count(query_tokens, market_tokens)


def _filter_relevant_markets(
    query: str,
    markets: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], set[str]]:
    """Remove unrelated fallback results for specific search queries.

    Gamma search can return high-volume unrelated markets for narrow
    queries. For specific queries, require enough distinctive token
    overlap to avoid telling the agent that irrelevant markets are matches.
    Generic discovery queries are left unfiltered.
    """
    query_tokens = _tokenize_search_text(query)
    if not query_tokens:
        return markets, query_tokens

    # One- or two-token searches are often broad ("Iran peace", "Fed"),
    # so one distinctive match is enough. Longer queries should carry at
    # least two matching distinctive tokens.
    min_score = 1 if len(query_tokens) <= 2 else 2
    return (
        [m for m in markets if _market_relevance_score(query_tokens, m) >= min_score],
        query_tokens,
    )


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

    # Fetch a broad set for client-side filtering, then trim to
    # the requested limit after relevance scoring.  The Gamma API
    # text search (_q) is unreliable so we omit it and rely on
    # client-side matching over top-volume markets instead.
    fetch_limit = max(_SEARCH_FETCH_LIMIT, limit)

    client = GammaClient()
    try:
        markets = await client.search_markets(
            limit=min(fetch_limit, 500),
            active=active,
            closed=closed,
            order=order,
            end_date_min=effective_end_date_min,
        )
        relevant_markets, query_tokens = _filter_relevant_markets(query, markets)

        results = []
        for m in relevant_markets[:limit]:
            results.append(
                {
                    "condition_id": m["condition_id"],
                    "question": m["question"],
                    "slug": m["slug"],
                    "market_url": m["market_url"],
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

        response: dict[str, Any] = {
            "tool": "search_prediction_markets",
            "query": query,
            "count": len(results),
            "raw_count": len(markets),
            "relevance_filter_applied": bool(query_tokens),
            "markets": results,
        }
        if query_tokens and markets and not results:
            response["note"] = (
                "Search returned raw markets, but none matched enough distinctive "
                "query terms. Treat this as no relevant market found; do not infer "
                "that unrelated raw results are matches."
            )
        return response
    finally:
        await client.close()


_TAG_SLUG_KEYWORDS: dict[str, list[str]] = {
    "bitcoin": ["bitcoin", "btc"],
    "crypto": ["crypto", "ethereum", "eth", "solana", "altcoin", "defi"],
    "politics": ["politics", "president", "election", "congress", "senate"],
    "economy": ["economy", "fed", "rate", "gdp", "inflation", "tariff"],
    "geopolitics": ["geopolitics", "war", "ceasefire", "sanctions", "iran", "ukraine"],
    "sports": ["sports", "nfl", "mlb", "nhl", "golf", "tennis"],
    "nba": ["nba", "lakers", "celtics", "nuggets"],
    "soccer": ["soccer", "football", "fifa", "champions", "league", "premier"],
    "esports": ["esports", "counter-strike", "league-of-legends"],
}


def _infer_tag_slug(query_tokens: set[str]) -> str:
    """Map query tokens to a Gamma tag_slug for event filtering."""
    for slug, keywords in _TAG_SLUG_KEYWORDS.items():
        if query_tokens & set(keywords):
            return slug
    return ""


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
            market_summaries = []
            for m in event.get("markets", []):
                market_summaries.append(
                    {
                        "condition_id": m.get("condition_id", ""),
                        "slug": m.get("slug", ""),
                        "market_url": event_url,
                        "question": m.get("question", ""),
                        "outcomes": m.get("outcomes", []),
                        "outcome_prices": m.get("outcome_prices", []),
                        "volume_24h": m.get("volume_24h", 0),
                        "liquidity": m.get("liquidity", 0),
                    }
                )
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
