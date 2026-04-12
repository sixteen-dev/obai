"""CLOB API client for Polymarket order book and pricing data.

CLOB (Central Limit Order Book) API provides real-time pricing,
order book depth, spreads, and historical price data.
Base URL: https://clob.polymarket.com
"""

from __future__ import annotations

import asyncio
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


class ClobClient:
    """Client for the Polymarket CLOB API.

    Handles order book data, pricing, spreads, and price history.
    """

    def __init__(self) -> None:
        """Initialize CLOB client with settings."""
        settings = get_settings()
        self._base_url = settings.clob_api_base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=settings.request_timeout)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def get_order_book(self, token_id: str) -> dict[str, Any]:
        """Get the full order book for a token.

        Args:
            token_id: CLOB token ID.

        Returns:
            Order book with bids and asks arrays.

        """
        raw = await self._get("/book", {"token_id": token_id})
        return self._normalize_book(raw, token_id)

    async def get_midpoint(self, token_id: str) -> dict[str, Any]:
        """Get midpoint price for a token.

        Args:
            token_id: CLOB token ID.

        Returns:
            Dict with token_id and midpoint price.

        """
        raw = await self._get("/midpoint", {"token_id": token_id})
        mid = raw.get("mid") if isinstance(raw, dict) else None
        return {
            "token_id": token_id,
            "midpoint": float(mid) if mid is not None else None,
        }

    async def get_spread(self, token_id: str) -> dict[str, Any]:
        """Get spread for a token.

        Args:
            token_id: CLOB token ID.

        Returns:
            Dict with spread value.

        """
        raw = await self._get("/spread", {"token_id": token_id})
        spread = raw.get("spread") if isinstance(raw, dict) else None
        return {
            "token_id": token_id,
            "spread": float(spread) if spread is not None else None,
        }

    async def get_last_trade_price(self, token_id: str) -> dict[str, Any]:
        """Get the last trade price for a token.

        Args:
            token_id: CLOB token ID.

        Returns:
            Dict with last trade price.

        """
        raw = await self._get("/last-trade-price", {"token_id": token_id})
        price = raw.get("price") if isinstance(raw, dict) else None
        return {
            "token_id": token_id,
            "last_trade_price": float(price) if price is not None else None,
        }

    async def get_price_history(
        self,
        token_id: str,
        *,
        interval: str = "1d",
        fidelity: int = 60,
    ) -> dict[str, Any]:
        """Get historical price data for a token.

        Args:
            token_id: CLOB token ID.
            interval: Lookback window (1m, 1h, 6h, 1d, 1w, max, all).
            fidelity: Sampling resolution in minutes. E.g., 1 = one
                point per minute, 60 = one point per hour.

        Returns:
            Dict with token_id and history array of {t, p} points.

        """
        params: dict[str, Any] = {
            "market": token_id,
            "interval": interval,
            "fidelity": fidelity,
        }
        raw = await self._get("/prices-history", params)
        history: list[dict[str, Any]] = []
        raw_history = raw.get("history", []) if isinstance(raw, dict) else []

        if isinstance(raw_history, list):
            for point in raw_history:
                if isinstance(point, dict):
                    history.append(
                        {
                            "timestamp": point.get("t"),
                            "price": float(point["p"]) if "p" in point else None,
                        }
                    )

        return {
            "token_id": token_id,
            "interval": interval,
            "fidelity": fidelity,
            "count": len(history),
            "history": history,
        }

    def _normalize_book(self, raw: Any, token_id: str) -> dict[str, Any]:
        """Normalize order book response.

        The CLOB API returns bids ascending (worst first) and asks
        descending (worst first). We sort bids descending and asks
        ascending so index 0 is always the best price on each side.
        """
        if not isinstance(raw, dict):
            return {"token_id": token_id, "bids": [], "asks": [], "bid_depth": 0, "ask_depth": 0}

        bids = self._parse_book_side(raw.get("bids", []))
        asks = self._parse_book_side(raw.get("asks", []))

        # Sort so best prices are first: bids high→low, asks low→high
        bids.sort(key=lambda x: x["price"], reverse=True)
        asks.sort(key=lambda x: x["price"])

        bid_depth = sum(b["size"] for b in bids[:5])
        ask_depth = sum(a["size"] for a in asks[:5])
        best_bid = bids[0]["price"] if bids else None
        best_ask = asks[0]["price"] if asks else None
        midpoint = (
            round((best_bid + best_ask) / 2, 6)
            if best_bid is not None and best_ask is not None
            else None
        )
        spread = (
            round(best_ask - best_bid, 6) if best_bid is not None and best_ask is not None else None
        )

        return {
            "token_id": token_id,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "midpoint": midpoint,
            "spread": spread,
            "bid_depth_top5": round(bid_depth, 2),
            "ask_depth_top5": round(ask_depth, 2),
            "bids": bids[:10],
            "asks": asks[:10],
        }

    def _parse_book_side(self, raw_side: Any) -> list[dict[str, Any]]:
        """Parse one side of the order book."""
        if not isinstance(raw_side, list):
            return []
        levels: list[dict[str, Any]] = []
        for level in raw_side:
            if isinstance(level, dict):
                try:
                    levels.append(
                        {
                            "price": float(level.get("price", 0)),
                            "size": float(level.get("size", 0)),
                        }
                    )
                except (ValueError, TypeError):
                    continue
        return levels

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET request with retry logic."""
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
