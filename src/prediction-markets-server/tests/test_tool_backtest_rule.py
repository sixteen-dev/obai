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
        self.list_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []

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
        self.list_calls.append(
            {
                "end_date_min": end_date_min,
                "end_date_max": end_date_max,
                "tag_slug": tag_slug,
            }
        )
        return list(self._payloads.values())[:limit]

    async def public_search(
        self,
        *,
        query: str,
        limit_per_type: int = 10,
        events_status: str = "",
        events_tag: list[str] | None = None,
    ) -> dict[str, Any]:
        self.search_calls.append({"events_status": events_status, "events_tag": events_tag})
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
async def test_backtest_rule_pushes_category_to_gamma_tag(fixtures) -> None:
    """The structured rule category is a Gamma tag filter, not a local exact match."""
    dl, store = fixtures
    result = await backtest_prediction_rule(
        {
            "side": "YES",
            "entry": {"price_min": 0.05, "price_max": 0.15},
            "exit": {"type": "hold_to_resolution"},
            "filters": {"category": "politics"},
        },
        downloader=dl,
        store=store,
        max_markets=5,
        max_history_points=1000,
        now=_now(),
    )
    assert dl.gamma.list_calls[-1] == {
        "end_date_min": "",
        "end_date_max": "",
        "tag_slug": "politics",
    }
    assert result["selected_condition_ids"] == ["0xA", "0xB"]


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


class _FakeClobTracked:
    """Fake CLOB returning per-token configured price tracks for stop_take_profit tests."""

    def __init__(self, tracks: dict[str, list[tuple[int, float]]]) -> None:
        # tracks: token_id -> list of (unix_timestamp, price) tuples.
        self._tracks = tracks

    async def get_price_history(
        self, token_id: str, *, interval: str = "1d", fidelity: int = 60
    ) -> dict[str, Any]:
        history = [{"timestamp": ts, "price": p} for ts, p in self._tracks[token_id]]
        return {
            "token_id": token_id,
            "interval": interval,
            "fidelity": fidelity,
            "count": len(history),
            "history": history,
        }

    async def close(self) -> None:
        pass


def _stp_fixture(yes_track: list[tuple[int, float]]) -> tuple[HistoryDownloader, PredictionStore]:
    """Build downloader + store with one YES-winner market driving `yes_track`."""
    payload = _payload("0xA", terminal_yes=True)
    # End date must land after the latest yes_track sample so resolution is in
    # the past relative to the walk.
    last_ts = yes_track[-1][0]
    payload["end_date"] = datetime.fromtimestamp(last_ts + 60 * 60, tz=timezone.utc).isoformat()
    payloads = {"0xA": payload}
    store = PredictionStore(manager=PredictionDuckDBManager(db_path=":memory:"))
    store.ensure_connected()
    tracks = {
        "0xA-Y": yes_track,
        # NO leg only needs presence for the downloader, not a realistic track.
        "0xA-N": [(yes_track[0][0], 1.0 - yes_track[0][1])],
    }
    dl = HistoryDownloader(
        gamma=_FakeGamma(payloads),
        clob=_FakeClobTracked(tracks),
        data_client=_FakeData(),
        store=store,
    )
    return dl, store


@pytest.mark.asyncio
async def test_backtest_rule_emits_exit_breakdown_and_per_trade_fields() -> None:
    """stop_take_profit must surface exit_breakdown + per-trade exit_reason/time_to_exit_days."""
    base_ts = 1_700_000_000
    # Track: entry at 0.10, then crosses TP at 0.55, ending high.
    yes_track = [(base_ts, 0.10), (base_ts + 86_400 * 5, 0.55), (base_ts + 86_400 * 10, 1.0)]
    dl, store = _stp_fixture(yes_track)
    result = await backtest_prediction_rule(
        {
            "side": "YES",
            "entry": {"price_min": 0.05, "price_max": 0.15},
            "exit": {
                "type": "stop_take_profit",
                "stop_price": 0.04,
                "take_profit_price": 0.50,
            },
        },
        downloader=dl,
        store=store,
        max_markets=5,
        max_history_points=1000,
        now=_now(),
    )
    metrics = result["metrics"]
    assert "exit_breakdown" in metrics
    breakdown = metrics["exit_breakdown"]
    assert set(breakdown.keys()) == {"stop", "take_profit", "expiry", "resolution"}
    # One market, walk crosses 0.55 first → take_profit fires.
    assert breakdown["take_profit"]["count"] == 1
    assert breakdown["stop"]["count"] == 0
    assert breakdown["resolution"]["count"] == 0
    # Per-trade examples must carry the new fields.
    for example in result["examples"]:
        assert example["exit_reason"] in {"stop", "take_profit", "expiry", "resolution"}
        assert isinstance(example["time_to_exit_days"], (int, float))


@pytest.mark.asyncio
async def test_backtest_rule_emits_stop_take_profit_limitations() -> None:
    """stop_take_profit must append the three fidelity caveats to limitations."""
    base_ts = 1_700_000_000
    yes_track = [(base_ts, 0.10), (base_ts + 86_400 * 3, 0.02), (base_ts + 86_400 * 7, 0.0)]
    dl, store = _stp_fixture(yes_track)
    result = await backtest_prediction_rule(
        {
            "side": "YES",
            "entry": {"price_min": 0.05, "price_max": 0.15},
            "exit": {"type": "stop_take_profit", "stop_price": 0.04},
        },
        downloader=dl,
        store=store,
        max_markets=5,
        max_history_points=1000,
        now=_now(),
    )
    text = " ".join(result["limitations"])
    assert "Intra-bucket price paths are unobserved" in text
    assert "Exit price is the sampled row price at trigger" in text
    assert "spread, depth, and market impact" in text


@pytest.mark.asyncio
async def test_backtest_rule_hold_to_resolution_limitations_unchanged(fixtures) -> None:
    """hold_to_resolution path must NOT carry the stop_take_profit caveats."""
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
    text = " ".join(result["limitations"])
    assert "Intra-bucket" not in text
    assert "trigger level" not in text


@pytest.mark.asyncio
async def test_backtest_rule_no_exit_price_for_max_hold_surfaces_in_resolution_breakdown() -> None:
    """Data gap straddling the max-hold boundary → skip counted under no_exit_price_for_max_hold."""
    # Entry sample at t0; only one more sample at t0+1day. max_hold_days=30 → boundary is
    # ~29 days past the last sample, and end_date sits a year later → skip the trade.
    base_ts = 1_700_000_000
    yes_track = [(base_ts, 0.10), (base_ts + 86_400, 0.11)]
    dl, store = _stp_fixture(yes_track)
    # Push end_date well past the boundary.
    dl.gamma._payloads["0xA"]["end_date"] = (  # noqa: SLF001
        datetime.fromtimestamp(base_ts + 86_400 * 365, tz=timezone.utc).isoformat()
    )
    result = await backtest_prediction_rule(
        {
            "side": "YES",
            "entry": {"price_min": 0.05, "price_max": 0.15},
            "exit": {"type": "stop_take_profit", "max_hold_days": 30},
        },
        downloader=dl,
        store=store,
        max_markets=5,
        max_history_points=1000,
        now=_now(),
    )
    assert result["sample_size"] == 0
    breakdown_reasons = result["resolution_breakdown"]
    assert breakdown_reasons.get("no_exit_price_for_max_hold") == 1


@pytest.mark.asyncio
async def test_backtest_rule_rejects_overlap_of_entry_band_and_stop(fixtures) -> None:
    """Schema-level disjointness: stop_price >= entry.price_min must reject."""
    dl, store = fixtures
    with pytest.raises(ValidationError, match="stop_price"):
        await backtest_prediction_rule(
            {
                "side": "YES",
                "entry": {"price_min": 0.05, "price_max": 0.15},
                "exit": {"type": "stop_take_profit", "stop_price": 0.05},
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
