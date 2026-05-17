"""End-to-end test for tools.backtest_rule.backtest_prediction_rule."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from src.data import HistoryDownloader
from src.storage import PredictionDuckDBManager, PredictionStore
from src.tools.backtest_rule import backtest_prediction_rule


def _now() -> datetime:
    return datetime(2026, 5, 16, tzinfo=timezone.utc)


def _payload(condition_id: str, terminal_yes: bool = True) -> dict[str, Any]:
    return {
        "condition_id": condition_id,
        "slug": condition_id.replace("0x", "slug-"),
        "question": "Will X happen?",
        "category": "politics",
        "clob_token_ids": [f"{condition_id}-Y", f"{condition_id}-N"],
        "outcomes": ["Yes", "No"],
        "outcome_prices": [1.0, 0.0] if terminal_yes else [0.0, 1.0],
        "closed": True,
        "uma_resolution_status": "resolved",
        "winning_outcome": None,
        "volume": 50000.0,
        "end_date": "2026-04-01T00:00:00Z",
        "event_slug": "event-x",
    }


class _FakeGamma:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
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
    ) -> list[dict[str, Any]]:
        return list(self._payloads.values())[:limit]

    async def public_search(
        self,
        *,
        query: str,
        limit_per_type: int = 10,
        events_status: str = "",
        events_tag: list[str] | None = None,
    ) -> dict[str, Any]:
        return {"events": [{"markets": list(self._payloads.values())}], "pagination": {}}

    async def get_market(self, identifier: str) -> dict[str, Any]:
        return self._payloads[identifier]

    async def close(self) -> None:
        pass


class _FakeClob:
    async def get_price_history(
        self, token_id: str, *, interval: str = "1d", fidelity: int = 60
    ) -> dict[str, Any]:
        # Provide a cheap entry-band sample at 0.10 + a terminal sample
        return {
            "token_id": token_id,
            "interval": interval,
            "fidelity": fidelity,
            "count": 2,
            "history": [
                {"timestamp": 1700000000, "price": 0.10},
                {"timestamp": 1700100000, "price": 1.0 if "-Y" in token_id else 0.0},
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
def fixtures():
    payloads = {
        "0xA": _payload("0xA", terminal_yes=True),
        "0xB": _payload("0xB", terminal_yes=False),
    }
    store = PredictionStore(manager=PredictionDuckDBManager(db_path=":memory:"))
    store.ensure_connected()
    dl = HistoryDownloader(
        gamma=_FakeGamma(payloads),
        clob=_FakeClob(),
        data_client=_FakeData(),
        store=store,
    )
    return dl, store


@pytest.mark.asyncio
async def test_backtest_rule_response_contract_shape(fixtures) -> None:
    dl, store = fixtures
    result = await backtest_prediction_rule(
        {
            "side": "YES",
            "entry": {"price_min": 0.05, "price_max": 0.15},
            "exit": {"type": "hold_to_resolution"},
        },
        downloader=dl,
        store=store,
        max_markets=5,
        max_history_points=1000,
        now=_now(),
    )
    for key in (
        "tool",
        "selected_condition_ids",
        "filters",
        "cache_actions",
        "data_coverage",
        "sample_size",
        "metrics",
        "monte_carlo_input",
        "examples",
        "limitations",
        "quality_flags",
        "reliability_label",
        "data_fingerprint",
    ):
        assert key in result, f"missing {key}"
    assert result["tool"] == "backtest_prediction_rule"
    assert result["monte_carlo_input"]["return_type"] == "return_on_cost"


@pytest.mark.asyncio
async def test_backtest_rule_lifetime_volume_limitation_present(fixtures) -> None:
    """When volume_filter_mode='lifetime_static', the limitation must surface."""
    dl, store = fixtures
    result = await backtest_prediction_rule(
        {
            "side": "YES",
            "entry": {"price_min": 0.05, "price_max": 0.15},
            "exit": {"type": "hold_to_resolution"},
            "filters": {"min_lifetime_volume": 1000, "volume_filter_mode": "lifetime_static"},
        },
        downloader=dl,
        store=store,
        max_markets=5,
        max_history_points=1000,
        now=_now(),
    )
    text = " ".join(result["limitations"])
    assert "lifetime" in text
    assert result["filters"]["volume_filter_mode"] == "lifetime_static"


@pytest.mark.asyncio
async def test_backtest_rule_propagates_validation_errors(fixtures) -> None:
    """Bad rule payload → pydantic.ValidationError, not silent default."""
    dl, store = fixtures
    with pytest.raises(ValidationError):
        await backtest_prediction_rule(
            {
                "side": "MAYBE",
                "entry": {"price_min": 0.1, "price_max": 0.2},
                "exit": {"type": "hold_to_resolution"},
            },
            downloader=dl,
            store=store,
            max_markets=5,
            max_history_points=1000,
            now=_now(),
        )


@pytest.mark.asyncio
async def test_backtest_data_fingerprint_changes_when_winner_flips(fixtures) -> None:
    """Regression: data_fingerprint must change when a market's winning_outcome flips.

    Previously resolution_fingerprints was populated with condition_ids,
    so a UMA dispute that flipped the winner left data_fingerprint
    unchanged — defeating cache-invalidation. Here we flip the upstream
    payload between runs so the backfill rewrites the resolution fields
    (a direct UPDATE would be overwritten by ensure_market).
    """
    dl, store = fixtures
    result_a = await backtest_prediction_rule(
        {
            "side": "YES",
            "entry": {"price_min": 0.05, "price_max": 0.15},
            "exit": {"type": "hold_to_resolution"},
        },
        downloader=dl,
        store=store,
        max_markets=5,
        max_history_points=1000,
        now=_now(),
    )
    # Flip the terminal outcome_prices for "0xA" in the fake Gamma so the
    # next ensure_market refresh infers the opposite winning_outcome —
    # mirrors what a UMA dispute re-resolution looks like to the cache.
    flipped = _payload("0xA", terminal_yes=False)
    dl.gamma._payloads["0xA"] = flipped  # noqa: SLF001 — testing fingerprint plumbing
    result_b = await backtest_prediction_rule(
        {
            "side": "YES",
            "entry": {"price_min": 0.05, "price_max": 0.15},
            "exit": {"type": "hold_to_resolution"},
        },
        downloader=dl,
        store=store,
        max_markets=5,
        max_history_points=1000,
        now=_now(),
    )
    assert result_a["data_fingerprint"] != result_b["data_fingerprint"]
    assert (
        result_a["monte_carlo_input"]["source_backtest_fingerprint"]
        != result_b["monte_carlo_input"]["source_backtest_fingerprint"]
    )
