"""End-to-end test for analyze_prediction_calibration with mocked clients."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.data import HistoryDownloader
from src.storage import PredictionDuckDBManager, PredictionStore
from src.tools.calibration import analyze_prediction_calibration


def _now() -> datetime:
    return datetime(2026, 5, 16, tzinfo=timezone.utc)


def _payload(condition_id: str, *, end_date: str = "2026-04-01T00:00:00Z") -> dict[str, Any]:
    return {
        "condition_id": condition_id,
        "slug": condition_id.replace("0x", "slug-"),
        "question": f"Will {condition_id} happen?",
        "category": "politics",
        "clob_token_ids": [f"{condition_id}-Y", f"{condition_id}-N"],
        "outcomes": ["Yes", "No"],
        "outcome_prices": [1.0, 0.0],
        "closed": True,
        "uma_resolution_status": "resolved",
        "winning_outcome": None,
        "volume": 50000.0,
        "end_date": end_date,
        "resolution_status": "resolved",
    }


class _FakeGamma:
    def __init__(
        self, candidates: list[dict[str, Any]], payloads: dict[str, dict[str, Any]]
    ) -> None:
        self._candidates = candidates
        self._payloads = payloads

    async def list_markets(  # noqa: PLR0913 — must match production GammaClient.list_markets signature
        self,
        *,
        limit: int = 10,
        active: bool = True,
        closed: bool = False,
        order: str = "endDate",
        ascending: bool = False,
        end_date_min: str = "",
        end_date_max: str = "",
        tag_slug: str = "",
    ) -> list[dict[str, Any]]:
        return self._candidates[:limit]

    async def public_search(
        self,
        *,
        query: str,
        limit_per_type: int = 10,
        events_status: str = "",
        events_tag: list[str] | None = None,
    ) -> dict[str, Any]:
        return {"events": [{"markets": self._candidates}], "pagination": {}}

    async def get_market(self, identifier: str) -> dict[str, Any]:
        if identifier in self._payloads:
            return self._payloads[identifier]
        msg = f"unknown {identifier}"
        raise ValueError(msg)

    async def close(self) -> None:
        pass


class _FakeClob:
    async def get_price_history(
        self, token_id: str, *, interval: str = "1d", fidelity: int = 60
    ) -> dict[str, Any]:
        # Two cheap and two pricey samples so observations land in 2 buckets
        return {
            "token_id": token_id,
            "interval": interval,
            "fidelity": fidelity,
            "count": 2,
            "history": [
                {"timestamp": 1700000000, "price": 0.10},
                {"timestamp": 1700001000, "price": 0.90},
            ],
        }

    async def close(self) -> None:
        pass


class _FakeData:
    async def get_trades(self, condition_id: str, *, limit: int = 50) -> dict[str, Any]:
        return {"condition_id": condition_id, "trade_count": 0, "trades": []}

    async def close(self) -> None:
        pass


@pytest.fixture
def downloader_and_store():
    payloads = {f"0x{i}": _payload(f"0x{i}") for i in range(5)}
    candidates = list(payloads.values())
    store = PredictionStore(manager=PredictionDuckDBManager(db_path=":memory:"))
    store.ensure_connected()
    dl = HistoryDownloader(
        gamma=_FakeGamma(candidates, payloads),
        clob=_FakeClob(),
        data_client=_FakeData(),
        store=store,
    )
    return dl, store


@pytest.mark.asyncio
async def test_calibration_response_has_contract_shape(downloader_and_store) -> None:
    """Top-level keys match §15."""
    dl, store = downloader_and_store
    result = await analyze_prediction_calibration(
        downloader=dl,
        store=store,
        max_markets=5,
        fidelity=60,
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
        "metrics",
        "examples",
        "limitations",
        "quality_flags",
        "reliability_label",
    ):
        assert key in result, f"missing {key}"
    assert result["tool"] == "analyze_prediction_calibration"
    assert "market_bucket_once" in result["metrics"]


@pytest.mark.asyncio
async def test_calibration_limitations_name_lifetime_volume_contamination(
    downloader_and_store,
) -> None:
    """When min_lifetime_volume is set, the limitation must appear in the response."""
    dl, store = downloader_and_store
    result = await analyze_prediction_calibration(
        downloader=dl,
        store=store,
        min_lifetime_volume=1000.0,
        max_markets=5,
        fidelity=60,
        max_history_points=1000,
        now=_now(),
    )
    text = " ".join(result["limitations"])
    assert "lifetime" in text
    assert result["filters"]["volume_filter_mode"] == "lifetime_static"


@pytest.mark.asyncio
async def test_calibration_both_mode_returns_both_summaries(downloader_and_store) -> None:
    """sampling_mode='both' should emit market_bucket_once AND sample_weighted summaries."""
    dl, store = downloader_and_store
    result = await analyze_prediction_calibration(
        downloader=dl,
        store=store,
        sampling_mode="both",
        max_markets=5,
        fidelity=60,
        max_history_points=1000,
        now=_now(),
    )
    assert "market_bucket_once" in result["metrics"]
    assert "sample_weighted" in result["metrics"]


@pytest.mark.asyncio
async def test_calibration_rejects_unknown_sampling_mode(downloader_and_store) -> None:
    """Bad sampling_mode is a programming error — raise, not silent fall-through."""
    dl, store = downloader_and_store
    with pytest.raises(ValueError, match="sampling_mode"):
        await analyze_prediction_calibration(
            downloader=dl,
            store=store,
            sampling_mode="not_a_mode",  # type: ignore[arg-type]
            max_markets=5,
            max_history_points=1000,
            now=_now(),
        )
