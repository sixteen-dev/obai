"""Gamma API client for Polymarket market discovery and metadata.

Gamma API provides market search, event details, categories, and metadata.
Base URL: https://gamma-api.polymarket.com
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from ..config import get_settings
from ..logging_config import get_logger, log_error

logger = get_logger(__name__)

_MAX_RETRIES = 2
_RETRY_DELAYS = (0.5, 1.5)


def _is_retryable(status_code: int) -> bool:
    """Check if HTTP status code warrants a retry."""
    return status_code in {429, 500, 502, 503, 504}


class GammaClient:
    """Client for the Polymarket Gamma API.

    Handles market discovery, search, metadata, and event information.
    """

    def __init__(self) -> None:
        """Initialize Gamma client with settings."""
        settings = get_settings()
        self._base_url = settings.gamma_api_base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=settings.request_timeout)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    _PAGE_SIZE = 100

    async def search_markets(
        self,
        *,
        query: str = "",
        limit: int = 10,
        active: bool = True,
        closed: bool = False,
        order: str = "volume24hr",
        ascending: bool = False,
        end_date_min: str = "",
    ) -> list[dict[str, Any]]:
        """Search for prediction markets.

        Args:
            query: Search text to filter markets by question/title.
            limit: Maximum number of results (1-500).
            active: Include active markets.
            closed: Include closed/resolved markets.
            order: Sort field (volume24hr, liquidity, startDate, endDate).
            ascending: Sort direction.
            end_date_min: ISO date string (YYYY-MM-DD). Exclude markets
                ending before this date. Use today's date to filter expired markets.

        Returns:
            List of market dicts with core metadata and pricing.

        """
        capped_limit = min(limit, 500)
        all_markets: list[dict[str, Any]] = []

        while len(all_markets) < capped_limit:
            page_size = min(self._PAGE_SIZE, capped_limit - len(all_markets))
            params: dict[str, Any] = {
                "limit": page_size,
                "offset": len(all_markets),
                "active": str(active).lower(),
                "closed": str(closed).lower(),
                "order": order,
                "ascending": str(ascending).lower(),
            }
            if query:
                params["_q"] = query
            if end_date_min:
                params["end_date_min"] = end_date_min

            raw_markets = await self._get("/markets", params)
            if not isinstance(raw_markets, list) or len(raw_markets) == 0:
                break

            all_markets.extend(self._normalize_market(m) for m in raw_markets)

            # Fewer results than requested — no more pages
            if len(raw_markets) < page_size:
                break

        # Client-side end_date_min filter as safety net
        if end_date_min:
            all_markets = [
                m for m in all_markets if not m.get("end_date") or m["end_date"] >= end_date_min
            ]

        return all_markets

    async def get_market(self, identifier: str) -> dict[str, Any]:
        """Get a single market by slug, numeric ID, or condition ID.

        The Gamma API /markets/{id} only accepts numeric IDs.
        conditionId is not a supported path or filter param.
        Slugs are the most reliable lookup key.

        Args:
            identifier: Market slug (preferred), numeric ID, or
                condition ID (0x... hex — will search by slug fallback).

        Returns:
            Normalized market dict.

        Raises:
            ValueError: If no market found.

        """
        # Numeric database ID — use path lookup
        if identifier.isdigit():
            raw = await self._get(f"/markets/{identifier}")
            return self._normalize_market(raw)

        # Slug — use slug filter (most reliable)
        if not identifier.startswith("0x"):
            return await self.get_market_by_slug(identifier)

        # conditionId (0x...) — search all markets and match client-side.
        # The Gamma API ignores conditionId as a filter param, so we
        # fetch a broad set and find the match ourselves.
        results = await self._get("/markets", {"limit": 100})
        if isinstance(results, list):
            for m in results:
                if isinstance(m, dict) and m.get("conditionId") == identifier:
                    return self._normalize_market(m)

        msg = f"No market found for identifier: {identifier}"
        raise ValueError(msg)

    async def get_market_by_slug(self, slug: str) -> dict[str, Any]:
        """Get a market by its URL slug.

        Args:
            slug: Market slug (e.g., "will-trump-win-2024").

        Returns:
            Normalized market dict.

        Raises:
            ValueError: If no market found for slug.

        """
        results = await self._get("/markets", {"slug": slug, "limit": 1})
        if not isinstance(results, list) or len(results) == 0:
            msg = f"No market found for slug: {slug}"
            raise ValueError(msg)
        return self._normalize_market(results[0])

    async def get_event(self, event_id: str) -> dict[str, Any]:
        """Get an event with its markets.

        Args:
            event_id: Event identifier.

        Returns:
            Event dict with nested market objects.

        """
        raw = await self._get(f"/events/{event_id}")
        return self._normalize_event(raw)

    async def search_events(
        self,
        *,
        query: str = "",
        limit: int = 10,
        active: bool = True,
    ) -> list[dict[str, Any]]:
        """Search events.

        Args:
            query: Search text.
            limit: Maximum results.
            active: Only active events.

        Returns:
            List of normalized event dicts.

        """
        params: dict[str, Any] = {
            "limit": min(limit, 100),
            "active": str(active).lower(),
        }
        if query:
            params["_q"] = query

        raw = await self._get("/events", params)
        if not isinstance(raw, list):
            return []
        return [self._normalize_event(e) for e in raw]

    async def get_tags(self) -> list[dict[str, Any]]:
        """Get ranked market tags/categories.

        Returns:
            List of tag dicts with label and count.

        """
        raw = await self._get("/tags")
        if not isinstance(raw, list):
            return []
        return raw

    def _normalize_market(self, raw: Any) -> dict[str, Any]:
        """Extract relevant fields from a raw Gamma market response."""
        if not isinstance(raw, dict):
            return {}

        outcomes = self._parse_json_field(raw.get("outcomes", []))
        outcome_prices = self._parse_json_field(raw.get("outcomePrices", []))

        # Parse outcome prices from strings to floats
        parsed_prices: list[float] = []
        for p in outcome_prices:
            try:
                parsed_prices.append(float(p))
            except (ValueError, TypeError):
                parsed_prices.append(0.0)

        # Parse clobTokenIds which may also be a JSON string
        clob_token_ids = self._parse_json_field(raw.get("clobTokenIds", []))

        return {
            "condition_id": raw.get("conditionId", ""),
            "question_id": raw.get("questionID", ""),
            "slug": raw.get("slug", ""),
            "question": raw.get("question", ""),
            "description": raw.get("description", ""),
            "outcomes": outcomes,
            "outcome_prices": parsed_prices,
            "best_bid": raw.get("bestBid"),
            "best_ask": raw.get("bestAsk"),
            "spread": raw.get("spread"),
            "last_trade_price": raw.get("lastTradePrice"),
            "volume": self._safe_float(raw.get("volume")),
            "volume_24h": self._safe_float(raw.get("volume24hr")),
            "volume_1w": self._safe_float(raw.get("volume1wk")),
            "volume_1m": self._safe_float(raw.get("volume1mo")),
            "liquidity": self._safe_float(raw.get("liquidity")),
            "start_date": raw.get("startDate"),
            "end_date": raw.get("endDate"),
            "active": raw.get("active", False),
            "closed": raw.get("closed", False),
            "archived": raw.get("archived", False),
            "neg_risk": raw.get("negRisk", False),
            "clob_token_ids": clob_token_ids,
            "group_item_title": raw.get("groupItemTitle", ""),
            "resolution_source": raw.get("resolutionSource", ""),
            "category": self._extract_category(raw),
            "accepting_orders": raw.get("acceptingOrders", False),
            "order_min_size": raw.get("orderMinSize"),
            "tick_size": raw.get("orderPriceMinTickSize"),
            "one_week_price_change": raw.get("oneWeekPriceChange"),
        }

    @staticmethod
    def _parse_json_field(value: Any) -> list[Any]:
        """Parse a field that may be a JSON-encoded string or a native list.

        The Gamma API sometimes returns array fields (outcomes,
        outcomePrices, clobTokenIds) as JSON strings instead of
        native JSON arrays. This handles both forms.
        """
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
        return []

    @staticmethod
    def _safe_float(value: Any) -> float:
        """Convert a value that may be a string or number to float."""
        if value is None:
            return 0.0
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0.0

    def _normalize_event(self, raw: Any) -> dict[str, Any]:
        """Extract relevant fields from a raw Gamma event response."""
        if not isinstance(raw, dict):
            return {}

        raw_markets = raw.get("markets", [])
        markets = (
            [self._normalize_market(m) for m in raw_markets]
            if isinstance(raw_markets, list)
            else []
        )

        raw_tags = raw.get("tags", [])
        tags: list[str] = []
        if isinstance(raw_tags, list):
            for t in raw_tags:
                if isinstance(t, dict):
                    label = t.get("label") or t.get("slug") or t.get("name")
                    if label:
                        tags.append(str(label))
                elif isinstance(t, str):
                    tags.append(t)

        return {
            "id": raw.get("id", ""),
            "slug": raw.get("slug", ""),
            "title": raw.get("title", ""),
            "description": raw.get("description", ""),
            "category": raw.get("category", ""),
            "start_date": raw.get("startDate"),
            "end_date": raw.get("endDate"),
            "active": raw.get("active", False),
            "closed": raw.get("closed", False),
            "volume": raw.get("volume", 0),
            "liquidity": raw.get("liquidity", 0),
            "open_interest": raw.get("openInterest"),
            "tags": tags,
            "markets": markets,
            "comment_count": raw.get("commentCount", 0),
        }

    def _extract_category(self, raw: dict[str, Any]) -> str:
        """Extract category from market or its parent event."""
        # Direct category field
        if raw.get("category"):
            return str(raw["category"])
        # From nested events
        events = raw.get("events", [])
        if isinstance(events, list):
            for event in events:
                if isinstance(event, dict) and event.get("category"):
                    return str(event["category"])
        return ""

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET request with retry logic.

        Args:
            path: URL path (appended to base URL).
            params: Query parameters.

        Returns:
            Parsed JSON response.

        Raises:
            httpx.HTTPStatusError: On non-retryable HTTP errors.

        """
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if attempt == _MAX_RETRIES or not _is_retryable(exc.response.status_code):
                    log_error(
                        logger,
                        exc,
                        context={"url": url, "status": exc.response.status_code},
                    )
                    raise
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                logger.warning(
                    "retrying_request",
                    url=url,
                    attempt=attempt + 1,
                    delay=delay,
                    status=exc.response.status_code,
                )
                await asyncio.sleep(delay)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt == _MAX_RETRIES:
                    log_error(logger, exc, context={"url": url})
                    raise
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                await asyncio.sleep(delay)

        if last_error is not None:
            raise last_error
        msg = "Unexpected retry loop exit"  # pragma: no cover
        raise RuntimeError(msg)  # pragma: no cover
