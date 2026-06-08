"""Coinbase client contract tests."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.clients import CoinbaseClient


def _client() -> CoinbaseClient:
    return CoinbaseClient(
        base_url="https://api.coinbase.com/api/v3/brokerage/market",
        timeout=5.0,
        local_safety_limit=10,
        rate_window_seconds=1.0,
        max_concurrent_requests=2,
        max_retries=2,
        backoff_base_seconds=0.01,
        backoff_max_seconds=0.01,
    )


@pytest.mark.asyncio()
async def test_public_requests_do_not_send_auth_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V1 public market-data calls must remain key-less."""
    seen_headers: list[dict[str, str]] = []

    async def fake_request(
        _self: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        del params
        seen_headers.append(headers or {})
        return httpx.Response(
            200,
            json={
                "products": [
                    {
                        "product_id": "BTC-USD",
                        "product_type": "SPOT",
                        "base_currency_id": "BTC",
                        "quote_currency_id": "USD",
                        "status": "online",
                        "price_increment": "0.01",
                        "base_increment": "0.00000001",
                        "quote_increment": "0.01",
                        "base_min_size": "0.00000001",
                        "quote_min_size": "1",
                        "base_max_size": "3400",
                        "quote_max_size": "150000000",
                        "trading_disabled": False,
                        "is_disabled": False,
                        "cancel_only": False,
                        "limit_only": False,
                        "post_only": False,
                        "auction_mode": False,
                        "view_only": False,
                    }
                ],
                "pagination": {"has_next": False},
            },
            request=httpx.Request(method, path),
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = _client()
    try:
        products = await client.list_products()
    finally:
        await client.close()

    assert products[0].product_id == "BTC-USD"
    assert products[0].status == "online"
    assert products[0].base_min_size == "0.00000001"
    assert products[0].quote_min_size == "1"
    assert products[0].base_increment == "0.00000001"
    assert products[0].price_increment == "0.01"
    assert products[0].to_dict()["trading_disabled"] is False
    assert seen_headers == [{}]


@pytest.mark.asyncio()
async def test_ticker_uses_one_second_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Latest trade and quote can share one ticker call/cache entry."""
    calls = 0

    async def fake_request(
        _self: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        nonlocal calls
        del params, headers
        calls += 1
        return httpx.Response(
            200,
            json={
                "best_bid": "100.00",
                "best_ask": "100.01",
                "trades": [{"trade_id": "1", "price": "100.00", "time": "2026-06-03T00:00:00Z"}],
            },
            request=httpx.Request(method, path),
        )

    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = _client()
    try:
        first = await client.get_ticker("BTC-USD")
        second = await client.get_ticker("BTC-USD")
    finally:
        await client.close()

    assert first == second
    assert calls == 1


@pytest.mark.asyncio()
async def test_retries_429_then_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """429 responses are retried with bounded backoff."""
    statuses = [429, 200]
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async def fake_request(
        _self: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        del params, headers
        status = statuses.pop(0)
        if status == 429:
            return httpx.Response(
                status,
                headers={"Retry-After": "0.25"},
                request=httpx.Request(method, path),
            )
        return httpx.Response(
            200,
            json={"product_id": "BTC-USD", "product_type": "SPOT"},
            request=httpx.Request(method, path),
        )

    monkeypatch.setattr("src.clients.coinbase_client.asyncio.sleep", fake_sleep)
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    client = _client()
    try:
        product = await client.get_product("BTC-USD")
    finally:
        await client.close()

    assert product.product_id == "BTC-USD"
    assert sleeps == [0.25]
