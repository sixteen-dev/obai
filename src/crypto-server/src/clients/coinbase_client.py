"""Public Coinbase Advanced Trade market-data client."""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from datetime import datetime
from typing import Any, cast

import httpx

from ..logging_config import get_logger
from ..models import Candle, Product
from ..quality import iter_candle_chunks

logger = get_logger(__name__)

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class LocalTokenBucket:
    """Sliding-window request limiter for Coinbase public endpoints."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        """Initialize limiter with a request count and window size."""
        if limit <= 0 or window_seconds <= 0:
            msg = "Token bucket limit and window_seconds must be positive"
            raise ValueError(msg)
        self.limit = limit
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a local request token is available."""
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._timestamps and now - self._timestamps[0] >= self.window_seconds:
                    self._timestamps.popleft()
                if len(self._timestamps) < self.limit:
                    self._timestamps.append(now)
                    return
                sleep_for = self.window_seconds - (now - self._timestamps[0])
            # Each pass sleeps until the oldest token expires; contention may add passes.
            await asyncio.sleep(max(0.01, sleep_for))


class CoinbaseClient:
    """Coinbase public Advanced Trade market-data adapter.

    V1 uses only public `/api/v3/brokerage/market/...` endpoints. No API key or
    Authorization header is accepted by this client.
    """

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        local_safety_limit: int,
        rate_window_seconds: float,
        max_concurrent_requests: int,
        max_retries: int,
        backoff_base_seconds: float,
        backoff_max_seconds: float,
    ) -> None:
        """Initialize public Coinbase client and local limiter policy."""
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_max_seconds = backoff_max_seconds
        self._limiter = LocalTokenBucket(local_safety_limit, rate_window_seconds)
        self._semaphore = asyncio.Semaphore(max_concurrent_requests)
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)
        self._product_cache: dict[str, Product] = {}
        self._ticker_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._cache_ttl_seconds = 1.0

    async def close(self) -> None:
        """Close HTTP resources."""
        await self._client.aclose()

    async def list_products(self, product_type: str = "SPOT", limit: int = 250) -> list[Product]:
        """List public Coinbase products, following pagination deterministically."""
        products: list[Product] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"product_type": product_type, "limit": limit}
            if cursor:
                params["cursor"] = cursor
            payload = await self._request("GET", "/products", params=params)
            for raw in payload.get("products", []):
                product = Product.from_coinbase(raw)
                products.append(product)
                self._product_cache[product.product_id] = product
            pagination = payload.get("pagination") or {}
            if not pagination.get("has_next"):
                break
            next_cursor = pagination.get("next_cursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)
        return products

    async def get_product(self, product_id: str) -> Product:
        """Get one public Coinbase product."""
        normalized = product_id.upper()
        if normalized in self._product_cache:
            return self._product_cache[normalized]
        payload = await self._request("GET", f"/products/{normalized}")
        raw = payload.get("product") if isinstance(payload.get("product"), dict) else payload
        if not isinstance(raw, dict):
            msg = f"Coinbase product payload was not an object for {normalized}"
            raise ValueError(msg)
        product = Product.from_coinbase(raw)
        self._product_cache[product.product_id] = product
        return product

    async def get_candles(
        self,
        product_id: str,
        *,
        start_ts: int,
        end_ts: int,
        granularity: str,
    ) -> list[Candle]:
        """Fetch one Coinbase candle page."""
        payload = await self._request(
            "GET",
            f"/products/{product_id.upper()}/candles",
            params={
                "start": start_ts,
                "end": end_ts,
                "granularity": granularity,
                "limit": 350,
            },
        )
        return [Candle.from_coinbase(product_id, raw) for raw in payload.get("candles", [])]

    async def get_historical_candles(
        self,
        product_id: str,
        *,
        start: datetime,
        end: datetime,
        granularity: str,
    ) -> list[Candle]:
        """Fetch a complete candle range in deterministic 350-candle chunks."""
        candles_by_start: dict[int, Candle] = {}
        for chunk_start, chunk_end in iter_candle_chunks(start, end, granularity):
            chunk = await self.get_candles(
                product_id,
                start_ts=chunk_start,
                end_ts=chunk_end,
                granularity=granularity,
            )
            for candle in chunk:
                candles_by_start[candle.start_ts] = candle
        return [candles_by_start[key] for key in sorted(candles_by_start)]

    async def get_product_book(self, product_id: str, depth: int = 50) -> dict[str, Any]:
        """Get current public product book."""
        return await self._request(
            "GET",
            "/product_book",
            params={"product_id": product_id.upper(), "limit": depth},
            no_cache=True,
        )

    async def get_ticker(self, product_id: str, *, limit: int = 100) -> dict[str, Any]:
        """Get public market trades plus best bid/ask, using a 1-second local cache."""
        normalized = product_id.upper()
        now = time.monotonic()
        cached = self._ticker_cache.get(normalized)
        if cached and now - cached[0] <= self._cache_ttl_seconds:
            return cached[1]
        payload = await self._request(
            "GET",
            f"/products/{normalized}/ticker",
            params={"limit": limit},
            no_cache=True,
        )
        self._ticker_cache[normalized] = (now, payload)
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        no_cache: bool = False,
    ) -> dict[str, Any]:
        """Make a bounded, rate-limited request."""
        attempt = 0
        while True:
            await self._limiter.acquire()
            async with self._semaphore:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    headers={"Cache-Control": "no-cache"} if no_cache else None,
                )
            if response.status_code not in RETRYABLE_STATUSES:
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    return cast(dict[str, Any], payload)
                return {"data": payload}
            if attempt >= self.max_retries:
                response.raise_for_status()
            retry_after = _retry_after_seconds(response)
            delay = retry_after if retry_after is not None else self._backoff_delay(attempt)
            logger.warning(
                "coinbase_retry",
                path=path,
                status=response.status_code,
                attempt=attempt + 1,
                delay_seconds=round(delay, 3),
            )
            await asyncio.sleep(delay)
            attempt += 1

    def _backoff_delay(self, attempt: int) -> float:
        base = min(self.backoff_max_seconds, self.backoff_base_seconds * (2**attempt))
        return float(base + random.uniform(0.0, base * 0.25))


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
