"""End-to-end tests for tools.historical.ensure_prediction_market_history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.data import HistoryDownloader
from src.storage import PredictionDuckDBManager, PredictionStore
from src.tools.historical import ensure_prediction_market_history


def _now() -> datetime:
    return datetime(2026, 5, 16, tzinfo=timezone.utc)


class _FakeGamma:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self._payloads = payloads

    async def get_market(self, identifier: str) -> dict[str, Any]:
        if identifier not in self._payloads:
            msg = f"unknown market {identifier}"
            raise ValueError(msg)
        return self._payloads[identifier]

    async def close(self) -> None:
        pass


class _FakeClob:
    async def get_price_history(
        self, token_id: str, *, interval: str = "1d", fidelity: int = 60
    ) -> dict[str, Any]:
        return {
            "token_id": token_id,
            "interval": interval,
            "fidelity": fidelity,
            "count": 2,
            "history": [
                {"timestamp": 1700000000, "price": 0.5},
                {"timestamp": 1700001000, "price": 0.6},
            ],
        }

    async def close(self) -> None:
        pass


class _FakeData:
    async def get_trades(self, condition_id: str, *, limit: int = 50) -> dict[str, Any]:
        return {"condition_id": condition_id, "trade_count": 0, "trades": []}

    async def close(self) -> None:
        pass


def _payload(condition_id: str) -> dict[str, Any]:
    return {
        "condition_id": condition_id,
        "slug": condition_id.replace("0x", "slug-"),
        "question": "Will X happen?",
        "clob_token_ids": [f"{condition_id}-Y", f"{condition_id}-N"],
        "outcomes": ["Yes", "No"],
        "outcome_prices": [1.0, 0.0],
        "closed": True,
        "uma_resolution_status": "resolved",
        "winning_outcome": None,
        "volume": 50000.0,
        "end_date": "2026-04-01T00:00:00Z",
    }


@pytest.fixture
def downloader() -> HistoryDownloader:
    store = PredictionStore(manager=PredictionDuckDBManager(db_path=":memory:"))
    store.ensure_connected()
    return HistoryDownloader(
        gamma=_FakeGamma({"0xA": _payload("0xA"), "0xB": _payload("0xB")}),
        clob=_FakeClob(),
        data_client=_FakeData(),
        store=store,
    )


@pytest.mark.asyncio
async def test_ensure_response_has_contract_shape(downloader: HistoryDownloader) -> None:
    """Response must include all §15 top-level fields."""
    result = await ensure_prediction_market_history(
        ["0xA", "0xB"],
        downloader=downloader,
        max_history_points=1000,
        now=_now(),
    )
    for key in (
        "tool",
        "universe",
        "selected_condition_ids",
        "filters",
        "cache_actions",
        "data_coverage",
        "sample_size",
        "examples",
        "limitations",
        "quality_flags",
    ):
        assert key in result, f"missing {key}"
    assert result["sample_size"] == 2
    assert set(result["selected_condition_ids"]) == {"0xA", "0xB"}
    assert "markets" in result["cache_actions"]
    assert "price_history" in result["cache_actions"]


@pytest.mark.asyncio
async def test_ensure_repeated_call_is_idempotent(downloader: HistoryDownloader) -> None:
    """A second call with the same identifiers should report cached price_history."""
    first = await ensure_prediction_market_history(
        ["0xA"], downloader=downloader, max_history_points=1000, now=_now()
    )
    assert first["sample_size"] == 1
    second = await ensure_prediction_market_history(
        ["0xA"], downloader=downloader, max_history_points=1000, now=_now()
    )
    actions = set()
    for entry in second["cache_actions"]["price_history"].values():
        actions.add(entry["action"])
    assert actions == {"cached"}


@pytest.mark.asyncio
async def test_ensure_records_skipped_when_market_lookup_fails(
    downloader: HistoryDownloader,
) -> None:
    """A bad identifier should appear in skipped_reasons, not crash the batch."""
    result = await ensure_prediction_market_history(
        ["0xA", "doesnotexist"],
        downloader=downloader,
        max_history_points=1000,
        now=_now(),
    )
    assert result["sample_size"] == 1
    skipped = result["data_coverage"]["skipped_reasons"]
    assert skipped.get("market_lookup_failed") == 1


@pytest.mark.asyncio
async def test_ensure_empty_identifiers_returns_empty_shape() -> None:
    """No identifiers → contract-shaped empty response, not a crash."""
    result = await ensure_prediction_market_history(
        [],
        downloader=None,  # not used in empty path
        max_history_points=0,
    )
    assert result["sample_size"] == 0
    assert result["selected_condition_ids"] == []
