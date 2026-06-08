"""Tests for data.downloader (mocked GammaClient/ClobClient)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.data import HistoryDownloader
from src.storage import PredictionDuckDBManager, PredictionStore


def _now() -> datetime:
    return datetime(2026, 5, 16, tzinfo=timezone.utc)


class _FakeGamma:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[str] = []

    async def get_market(self, identifier: str) -> dict[str, Any]:
        self.calls.append(identifier)
        return self._payload

    async def close(self) -> None:
        pass


class _FakeClob:
    def __init__(self, history: list[dict[str, Any]]) -> None:
        self._history = history
        self.calls: list[tuple[str, str, int]] = []

    async def get_price_history(
        self, token_id: str, *, interval: str = "1d", fidelity: int = 60
    ) -> dict[str, Any]:
        self.calls.append((token_id, interval, fidelity))
        return {
            "token_id": token_id,
            "interval": interval,
            "fidelity": fidelity,
            "count": len(self._history),
            "history": self._history,
        }

    async def close(self) -> None:
        pass


class _FakeData:
    def __init__(self, trades: list[dict[str, Any]] | None = None) -> None:
        self._trades = trades or []

    async def get_trades(self, condition_id: str, *, limit: int = 50) -> dict[str, Any]:
        return {
            "condition_id": condition_id,
            "trade_count": len(self._trades),
            "trades": self._trades,
        }

    async def close(self) -> None:
        pass


def _resolved_payload() -> dict[str, Any]:
    return {
        "condition_id": "0xabc",
        "slug": "x",
        "question": "Will X happen?",
        "clob_token_ids": ["tokYes", "tokNo"],
        "outcomes": ["Yes", "No"],
        "outcome_prices": [1.0, 0.0],
        "closed": True,
        "uma_resolution_status": "resolved",
        "winning_outcome": None,
        "volume": 50000.0,
        "end_date": "2026-04-01T00:00:00Z",
    }


@pytest.fixture
def store() -> PredictionStore:
    s = PredictionStore(manager=PredictionDuckDBManager(db_path=":memory:"))
    s.ensure_connected()
    return s


@pytest.mark.asyncio
async def test_ensure_market_writes_market_and_tokens(store: PredictionStore) -> None:
    """ensure_market upserts both pm_markets and pm_tokens for the payload."""
    gamma = _FakeGamma(_resolved_payload())
    dl = HistoryDownloader(gamma=gamma, clob=_FakeClob([]), data_client=_FakeData(), store=store)
    result = await dl.ensure_market("x", now=_now())
    assert result.market.condition_id == "0xabc"
    assert result.market.resolution_method == "terminal_price_exact"
    assert len(result.tokens) == 2
    n = store.manager.conn.execute("SELECT count(*) FROM pm_tokens").fetchone()
    assert n is not None
    assert n[0] == 2


@pytest.mark.asyncio
async def test_ensure_price_history_writes_and_dedupes(store: PredictionStore) -> None:
    """Two ensure_price_history calls dedupe; second call is fast cached path or fetched."""
    history = [
        {"timestamp": 1700000000, "price": 0.5},
        {"timestamp": 1700001000, "price": 0.6},
    ]
    gamma = _FakeGamma(_resolved_payload())
    clob = _FakeClob(history)
    dl = HistoryDownloader(
        gamma=gamma,
        clob=clob,
        data_client=_FakeData(),
        store=store,
        data_freshness_hours=24,
    )
    await dl.ensure_market("x", now=_now())
    res1 = await dl.ensure_price_history(
        token_id="tokYes",
        condition_id="0xabc",
        fidelity_minutes=60,
        interval="max",
        now=_now(),
        max_history_points=10000,
    )
    assert res1.rows_written == 2
    assert res1.cache_action == "fetched"
    res2 = await dl.ensure_price_history(
        token_id="tokYes",
        condition_id="0xabc",
        fidelity_minutes=60,
        interval="max",
        now=_now(),
        max_history_points=10000,
    )
    assert res2.cache_action == "cached"
    assert res2.rows_written == 0
    n = store.manager.conn.execute(
        "SELECT count(*) FROM pm_price_history WHERE token_id = ?", ["tokYes"]
    ).fetchone()
    assert n is not None
    assert n[0] == 2


@pytest.mark.asyncio
async def test_ensure_market_from_payload_slug_fallback_fetches_tokens(
    store: PredictionStore,
) -> None:
    """Empty token arrays (a public_search mini-payload) trigger the slug-based refetch."""
    mini_payload: dict[str, Any] = {
        "condition_id": "0xabc",
        "slug": "x",
        "question": "Will X happen?",
        "clob_token_ids": [],
        "outcomes": [],
        "outcome_prices": [],
        "closed": True,
        "uma_resolution_status": "resolved",
        "winning_outcome": None,
        "volume": 50000.0,
        "end_date": "2026-04-01T00:00:00Z",
    }
    gamma = _FakeGamma(_resolved_payload())  # get_market returns full payload with tokens
    dl = HistoryDownloader(gamma=gamma, clob=_FakeClob([]), data_client=_FakeData(), store=store)

    result = await dl.ensure_market_from_payload(mini_payload, now=_now())

    assert len(result.tokens) == 2
    assert gamma.calls == ["x"]  # slug-based fallback triggered once
    n = store.manager.conn.execute("SELECT count(*) FROM pm_tokens").fetchone()
    assert n is not None and n[0] == 2


@pytest.mark.asyncio
async def test_ensure_market_from_payload_no_slug_skips_fallback(
    store: PredictionStore,
) -> None:
    """When mini-payload has no slug and no tokens, no Gamma fallback is attempted."""
    mini_payload: dict[str, Any] = {
        "condition_id": "0xdef",
        "slug": "",
        "question": "Will Y happen?",
        "clob_token_ids": [],
        "outcomes": [],
        "outcome_prices": [],
        "closed": True,
        "uma_resolution_status": "resolved",
        "winning_outcome": None,
        "volume": 10000.0,
        "end_date": "2026-04-01T00:00:00Z",
    }
    gamma = _FakeGamma(_resolved_payload())
    dl = HistoryDownloader(gamma=gamma, clob=_FakeClob([]), data_client=_FakeData(), store=store)

    result = await dl.ensure_market_from_payload(mini_payload, now=_now())

    assert len(result.tokens) == 0
    assert gamma.calls == []  # no fallback attempted


@pytest.mark.asyncio
async def test_ensure_price_history_raises_when_over_cap(store: PredictionStore) -> None:
    """When upstream returns more points than max_history_points, raise ValueError."""
    history = [{"timestamp": 1700000000 + i, "price": 0.5} for i in range(50)]
    dl = HistoryDownloader(
        gamma=_FakeGamma(_resolved_payload()),
        clob=_FakeClob(history),
        data_client=_FakeData(),
        store=store,
    )
    with pytest.raises(ValueError, match="exceeding prediction_max_history_points"):
        await dl.ensure_price_history(
            token_id="tokYes",
            condition_id="0xabc",
            fidelity_minutes=60,
            interval="max",
            now=_now(),
            max_history_points=10,
        )
