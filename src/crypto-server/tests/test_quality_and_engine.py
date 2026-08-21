"""Crypto quality, backtest, and artifact tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from src import server
from src.config import Settings
from src.engine import artifact_fingerprint, export_artifact, run_bar_backtest, validate_artifact
from src.engine.metrics import compute_metrics
from src.json_utils import canonical_json
from src.models import Candle
from src.quality import (
    build_candle_source_quality,
    compute_coverage,
    iter_candle_chunks,
    snap_start_to_available,
)
from src.storage import CryptoStore


def _candle(idx: int, close: float | None = None, volume: float = 10.0) -> Candle:
    start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=idx)
    price = close if close is not None else 100.0 + idx
    return Candle(
        product_id="BTC-USD",
        start=start,
        low=price - 1.0,
        high=price + 1.0,
        open=price,
        close=price,
        volume=volume,
    )


def test_iter_candle_chunks_uses_350_bar_limit() -> None:
    """ONE_MINUTE ranges chunk at 350 candles per request."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(minutes=351)

    chunks = list(iter_candle_chunks(start, end, "ONE_MINUTE"))

    assert len(chunks) == 2
    assert chunks[0][1] - chunks[0][0] == 350 * 60
    assert chunks[1][1] - chunks[1][0] == 60


def test_compute_coverage_reports_missing_gap_ranges() -> None:
    """Coverage records expected bars, missing percent, and gap ranges."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=5)
    candles = [_candle(0), _candle(1), _candle(3), _candle(4)]

    coverage = compute_coverage(
        candles,
        requested_start=start,
        requested_end=end,
        granularity="ONE_DAY",
        now=end,
    )

    assert coverage.expected_intervals == 5
    assert coverage.returned_intervals == 4
    assert coverage.missing_intervals == 1
    assert coverage.missing_pct == pytest.approx(0.2)
    assert coverage.gap_ranges == [
        {
            "start": "2026-01-03T00:00:00+00:00",
            "end": "2026-01-04T00:00:00+00:00",
        }
    ]


def test_snap_start_advances_past_leading_gap() -> None:
    """A missing leading candle snaps to the first available candle and clears the gap."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=5)
    candles = [_candle(1), _candle(2), _candle(3), _candle(4)]  # day 0 missing
    coverage = compute_coverage(
        candles, requested_start=start, requested_end=end, granularity="ONE_DAY", now=end
    )
    assert coverage.missing_intervals == 1

    effective_start, snapped = snap_start_to_available(
        candles,
        coverage,
        requested_start=start,
        requested_end=end,
        granularity="ONE_DAY",
        now=end,
    )

    assert effective_start == start + timedelta(days=1)
    assert snapped.missing_intervals == 0
    assert snapped.start == "2026-01-02T00:00:00+00:00"


def test_snap_start_leaves_interior_gap_blocking() -> None:
    """An interior gap is not snappable and is returned unchanged (still blocking)."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=5)
    candles = [_candle(0), _candle(1), _candle(3), _candle(4)]  # day 2 missing (interior)
    coverage = compute_coverage(
        candles, requested_start=start, requested_end=end, granularity="ONE_DAY", now=end
    )

    effective_start, returned = snap_start_to_available(
        candles,
        coverage,
        requested_start=start,
        requested_end=end,
        granularity="ONE_DAY",
        now=end,
    )

    assert effective_start == start
    assert returned.missing_intervals == 1


def test_snap_start_noop_when_complete() -> None:
    """A complete window is returned unchanged."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=3)
    candles = [_candle(0), _candle(1), _candle(2)]
    coverage = compute_coverage(
        candles, requested_start=start, requested_end=end, granularity="ONE_DAY", now=end
    )

    effective_start, returned = snap_start_to_available(
        candles,
        coverage,
        requested_start=start,
        requested_end=end,
        granularity="ONE_DAY",
        now=end,
    )

    assert effective_start == start
    assert returned.missing_intervals == 0


def test_execution_grade_quality_blocks_when_coinbase_fetch_fails() -> None:
    """Execution-grade quality blocks when required Coinbase candles are missing."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=2)
    coverage = compute_coverage(
        [],
        requested_start=start,
        requested_end=end,
        granularity="ONE_DAY",
        now=end,
    )

    quality = build_candle_source_quality(
        product_id="BTC-USD",
        candles=[],
        coverage=coverage,
        execution_grade_required=True,
        max_missing_pct_execution=0.0,
        fetch_failed=True,
    )

    assert quality.blocking_quality_warning is True
    assert quality.execution_grade is False
    assert quality.warnings == [
        "missing_candles",
        "coinbase_fetch_failed",
        "blocking_missing_candles",
    ]


async def test_partial_bar_refetched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The still-open trailing candle is re-fetched, overwriting a stale partial."""
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = day_start + timedelta(days=1)  # today's bar is still open (start + 1d > now)
    partial = Candle(
        "BTC-USD", day_start, low=99.0, high=101.0, open=100.0, close=100.0, volume=5.0
    )
    completed = Candle(
        "BTC-USD", day_start, low=95.0, high=120.0, open=100.0, close=118.0, volume=50.0
    )

    class _FakeCoinbase:
        """Returns the completed candle for any historical fetch."""

        async def get_historical_candles(
            self,
            product_id: str,
            *,
            start: datetime,
            end: datetime,
            granularity: str,
        ) -> list[Candle]:
            del product_id, start, end, granularity
            return [completed]

    store = CryptoStore(str(tmp_path / "crypto.duckdb"))
    await store.upsert_candles([partial], "ONE_DAY")
    monkeypatch.setattr(server._state, "store", store)
    monkeypatch.setattr(server._state, "coinbase", _FakeCoinbase())
    monkeypatch.setattr(server._state, "settings", Settings())
    try:
        candles, quality = await server._load_candles(
            product_id="BTC-USD",
            timeframe="1d",
            granularity="ONE_DAY",
            start=day_start,
            end=window_end,
            execution_grade_required=False,
        )
    finally:
        store.close()

    assert len(candles) == 1
    assert candles[0].close == pytest.approx(118.0)
    assert candles[0].high == pytest.approx(120.0)
    assert quality.coverage is not None
    assert quality.coverage.expected_intervals == 0  # open bar excluded from expected
    assert quality.coverage.missing_intervals == 0


def test_backtest_normalizes_percent_position_cap() -> None:
    """Execution config accepts 10 as 10%, not 10x leverage."""
    candles = [_candle(idx, close=100.0 + idx) for idx in range(12)]

    result = run_bar_backtest(
        candles,
        strategy_spec={
            "template": "spot_trend_follow",
            "parameters": {"fast_window": 2, "slow_window": 3},
        },
        execution_config={"initial_capital_usd": 100_000, "max_position_pct": 10},
    )

    assert result.result["execution_config"]["max_position_pct"] == pytest.approx(0.1)
    first_buy = next(trade for trade in result.trades if trade["side"] == "BUY")
    assert first_buy["notional"] < 11_000


def test_backtest_caps_fills_by_bar_participation() -> None:
    """Buy size is capped by max_bar_participation_pct times bar volume."""
    candles = [_candle(idx, close=100.0 + idx, volume=10.0) for idx in range(12)]

    result = run_bar_backtest(
        candles,
        strategy_spec={
            "template": "spot_trend_follow",
            "parameters": {"fast_window": 2, "slow_window": 3},
        },
        execution_config={
            "initial_capital_usd": 1_000_000,
            "max_position_pct": 100,
            "max_bar_participation_pct": 5,
            "spread_bps": 0,
            "taker_fee_bps": 0,
        },
    )

    first_buy = next(trade for trade in result.trades if trade["side"] == "BUY")
    assert first_buy["quantity"] == pytest.approx(0.5)
    assert first_buy["participation_pct"] == pytest.approx(0.05)


def test_backtest_realized_pnl_includes_entry_fee() -> None:
    """Round-trip realized P&L reconciles both entry and exit fees."""
    candles = [
        _candle(0, close=100.0, volume=1_000.0),
        _candle(1, close=110.0, volume=1_000.0),
        Candle("BTC-USD", datetime(2026, 1, 3, tzinfo=UTC), 89, 101, 100, 90, 1_000),
        Candle("BTC-USD", datetime(2026, 1, 4, tzinfo=UTC), 109, 111, 110, 110, 1_000),
        _candle(4, close=110.0, volume=1_000.0),
    ]

    result = run_bar_backtest(
        candles,
        strategy_spec={
            "template": "spot_trend_follow",
            "parameters": {"fast_window": 1, "slow_window": 2},
        },
        execution_config={
            "initial_capital_usd": 1_000,
            "max_position_pct": 100,
            "max_bar_participation_pct": 100,
            "spread_bps": 0,
            "taker_fee_bps": 100,
        },
    )

    buy = result.trades[0]
    sell = result.trades[1]
    assert buy["side"] == "BUY"
    assert sell["side"] == "SELL"
    expected_realized = (sell["price"] - buy["price"]) * sell["quantity"]
    expected_realized -= buy["fee"] + sell["fee"]
    assert sell["realized_pnl"] == pytest.approx(expected_realized)
    assert sell["realized_pnl"] == pytest.approx(78.21782178217822)


def test_backtest_no_same_bar_lookahead() -> None:
    """Signal on bar t fills at t+1 open, never the same bar."""
    candles = [
        Candle("BTC-USD", datetime(2026, 1, 1, tzinfo=UTC), 99, 101, 100, 100, 1_000),
        Candle("BTC-USD", datetime(2026, 1, 2, tzinfo=UTC), 99, 201, 100, 200, 1_000),
        Candle("BTC-USD", datetime(2026, 1, 3, tzinfo=UTC), 999, 1001, 1_000, 1_000, 1_000),
        Candle("BTC-USD", datetime(2026, 1, 4, tzinfo=UTC), 999, 1001, 1_000, 1_000, 1_000),
        Candle("BTC-USD", datetime(2026, 1, 5, tzinfo=UTC), 999, 1001, 1_000, 1_000, 1_000),
    ]

    result = run_bar_backtest(
        candles,
        strategy_spec={
            "template": "spot_trend_follow",
            "parameters": {"fast_window": 1, "slow_window": 2},
        },
        execution_config={
            "initial_capital_usd": 10_000,
            "max_position_pct": 100,
            "max_bar_participation_pct": 100,
            "spread_bps": 0,
            "taker_fee_bps": 0,
        },
    )

    buy = next(trade for trade in result.trades if trade["side"] == "BUY")
    assert buy["timestamp"] == "2026-01-03T00:00:00+00:00"
    assert buy["price"] == pytest.approx(1_000)


def test_metric_golden_fixture() -> None:
    """Hand-calculated metric fixture guards crypto annualization and ratios."""
    equity_curve = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "equity": 100.0},
        {"timestamp": "2026-01-02T00:00:00+00:00", "equity": 110.0},
        {"timestamp": "2026-01-03T00:00:00+00:00", "equity": 105.0},
        {"timestamp": "2026-01-04T00:00:00+00:00", "equity": 120.0},
    ]
    trades = [
        {"realized_pnl": 8.0, "notional": 100.0},
        {"realized_pnl": -3.0, "notional": 110.0},
    ]

    result = compute_metrics(equity_curve, trades)
    metrics = result.metrics

    assert metrics["total_return"] == pytest.approx(0.2)
    assert metrics["cagr"] == pytest.approx(4366352090.482958)
    assert metrics["volatility"] == pytest.approx(1.8863663980577492)
    assert metrics["sharpe"] == pytest.approx(12.740511467983099)
    assert metrics["sortino"] == pytest.approx(47.91875982177696)
    assert metrics["max_drawdown"] == pytest.approx(-0.045454545454545414)
    assert metrics["calmar"] == pytest.approx(96059745990.62515)
    assert metrics["profit_factor"] == pytest.approx(8 / 3)
    assert metrics["turnover"] == pytest.approx(1.9310344827586208)


def test_hit_rate_counts_closed_trades_only() -> None:
    """Hit rate and trade count use closed SELL legs, not every BUY+SELL leg."""
    equity_curve = [
        {"timestamp": "2026-01-01T00:00:00+00:00", "equity": 100.0},
        {"timestamp": "2026-01-02T00:00:00+00:00", "equity": 110.0},
    ]
    trades = [
        {"side": "BUY", "realized_pnl": 0.0, "notional": 100.0},
        {"side": "SELL", "realized_pnl": 10.0, "notional": 110.0},
        {"side": "BUY", "realized_pnl": 0.0, "notional": 110.0},
        {"side": "SELL", "realized_pnl": -5.0, "notional": 105.0},
    ]

    result = compute_metrics(equity_curve, trades)
    metrics = result.metrics

    assert metrics["trade_count"] == 2
    assert metrics["hit_rate"] == pytest.approx(0.5)


def test_canonical_json_pins_float_formatting() -> None:
    """Int-valued floats hash the same as ints; unsupported types fail loud."""
    assert canonical_json({"risk": {"max_position_pct": 10.0}}) == (
        '{"risk":{"max_position_pct":10}}'
    )
    assert artifact_fingerprint({"risk": {"max_position_pct": 10.0}}) == artifact_fingerprint(
        {"risk": {"max_position_pct": 10}}
    )
    with pytest.raises(TypeError):
        canonical_json({"bad": datetime(2026, 1, 1, tzinfo=UTC)})


def test_backtest_rejects_unsupported_cross_asset_template() -> None:
    """V1 engine is single-product Coinbase spot, not cross-asset."""
    candles = [_candle(idx, close=100.0 + idx) for idx in range(12)]

    with pytest.raises(ValueError, match="Unsupported v1 crypto strategy template"):
        run_bar_backtest(
            candles,
            strategy_spec={"template": "cross_asset_momentum", "parameters": {}},
            execution_config={},
        )


def test_artifact_fingerprint_blocks_load_bearing_mutation() -> None:
    """Artifact validation fails when load-bearing fields change."""
    backtest_result = {
        "data_config": {
            "product_id": "BTC-USD",
            "timeframe": "1d",
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-10T00:00:00+00:00",
        },
        "quality_status": "execution_grade",
        "source_quality_fingerprint": "abc123",
    }
    artifact = export_artifact(
        job_id="crypto_bt_test",
        strategy_spec={"template": "spot_trend_follow", "parameters": {"fast_window": 2}},
        risk_config={"max_position_pct": 10},
        execution_profile={"paper_backend": "internal_coinbase_paper_ledger"},
        backtest_result=backtest_result,
    )

    assert validate_artifact(artifact)["valid"] is True
    artifact["venues"] = ["coinbase", "binance"]
    validation = validate_artifact(artifact)

    assert validation["valid"] is False
    assert "venues must be ['coinbase'] for v1" in validation["errors"]
    assert "fingerprint does not match load-bearing fields" in validation["errors"]


def test_unknown_execution_config_key_is_rejected_not_defaulted() -> None:
    """A misspelled key silently ran the wrong backtest.

    A live handoff sent ``initial_capital: 60000``; the engine reads
    ``initial_capital_usd`` and fell back to its $100,000 default, so every
    dollar metric described an account the caller never asked for.
    """
    candles = [_candle(idx, close=100.0 + idx) for idx in range(12)]

    with pytest.raises(ValueError, match="initial_capital"):
        run_bar_backtest(
            candles,
            strategy_spec={"template": "spot_trend_follow", "parameters": {}},
            execution_config={"initial_capital": 60_000},
        )


def test_unknown_strategy_spec_key_is_rejected_not_defaulted() -> None:
    """The same handoff sent its rules under ``signal``, which was ignored.

    The engine reads ``parameters``, so it ran template defaults and echoed
    ``parameters: {}`` while the caller believed a 50/200 crossover ran.
    """
    candles = [_candle(idx, close=100.0 + idx) for idx in range(12)]

    with pytest.raises(ValueError, match="signal"):
        run_bar_backtest(
            candles,
            strategy_spec={
                "template": "spot_trend_follow",
                "signal": {"fast_sma": 50, "slow_sma": 200},
            },
            execution_config={"initial_capital_usd": 60_000},
        )


def test_coverage_reports_the_window_it_actually_evaluated() -> None:
    """Coverage echoed the request back, hiding a truncated assessment.

    Requesting through a future close clamps the evaluated window to the
    last closed bar, but ``start``/``end`` still echoed the request, so
    callers could not tell the two apart.
    """
    now = datetime(2026, 1, 6, 12, tzinfo=UTC)
    requested_end = datetime(2026, 1, 8, tzinfo=UTC)
    candles = [_candle(idx) for idx in range(5)]

    coverage = compute_coverage(
        candles,
        requested_start=datetime(2026, 1, 1, tzinfo=UTC),
        requested_end=requested_end,
        granularity="ONE_DAY",
        now=now,
    )

    assert coverage.end == requested_end.isoformat()
    assert coverage.evaluated_end < coverage.end


def test_future_requested_window_blocks_execution_grade() -> None:
    """Fail-closed was unenforceable: a future end could never report missing.

    ``expected_intervals`` is clamped to the last closed bar, so a window
    extending past available data reported 0 missing and execution_grade
    true. The caller asked to fail closed and got a green light instead.
    """
    now = datetime(2026, 1, 6, 12, tzinfo=UTC)
    candles = [_candle(idx) for idx in range(5)]
    coverage = compute_coverage(
        candles,
        requested_start=datetime(2026, 1, 1, tzinfo=UTC),
        requested_end=datetime(2026, 1, 8, tzinfo=UTC),
        granularity="ONE_DAY",
        now=now,
    )

    quality = build_candle_source_quality(
        product_id="BTC-USD",
        candles=candles,
        coverage=coverage,
        execution_grade_required=True,
        max_missing_pct_execution=0.01,
        fetch_failed=False,
    )

    assert quality.blocking_quality_warning is True
    assert quality.execution_grade is False
    assert "requested_window_not_yet_closed" in quality.warnings


def test_closed_requested_window_stays_execution_grade() -> None:
    """The block must not fire when the request ends at a closed bar."""
    now = datetime(2026, 1, 6, 12, tzinfo=UTC)
    candles = [_candle(idx) for idx in range(5)]
    coverage = compute_coverage(
        candles,
        requested_start=datetime(2026, 1, 1, tzinfo=UTC),
        requested_end=datetime(2026, 1, 6, tzinfo=UTC),
        granularity="ONE_DAY",
        now=now,
    )

    quality = build_candle_source_quality(
        product_id="BTC-USD",
        candles=candles,
        coverage=coverage,
        execution_grade_required=True,
        max_missing_pct_execution=0.01,
        fetch_failed=False,
    )

    assert quality.blocking_quality_warning is False
    assert quality.execution_grade is True


@pytest.mark.asyncio
async def test_backtest_through_future_close_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The trace scenario end to end: a window past the last closed bar.

    Requesting through tomorrow's UTC close previously returned
    ``quality_status: execution_grade`` with ``missing_pct: 0.0``, because
    the unclosed bars were dropped from ``expected`` as well as from
    ``returned``. The caller asked to fail closed and got a green light.
    """
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=10)
    tomorrow_close = today + timedelta(days=2)
    closed = [
        Candle(
            "BTC-USD",
            start + timedelta(days=idx),
            low=99.0,
            high=101.0,
            open=100.0,
            close=100.0 + idx,
            volume=50.0,
        )
        for idx in range(10)
    ]

    class _FakeCoinbase:
        """Serves only bars that have actually closed."""

        async def get_historical_candles(self, product_id: str, **_kwargs: object) -> list[Candle]:
            del product_id
            return closed

    store = CryptoStore(str(tmp_path / "crypto.duckdb"))
    await store.upsert_candles(closed, "ONE_DAY")
    monkeypatch.setattr(server._state, "store", store)
    monkeypatch.setattr(server._state, "coinbase", _FakeCoinbase())
    monkeypatch.setattr(server._state, "settings", Settings())
    try:
        response = await server.crypto_backtest_run_strategy(
            strategy_spec=json.dumps({"template": "spot_trend_follow", "parameters": {}}),
            data_config=json.dumps(
                {
                    "product_id": "BTC-USD",
                    "timeframe": "1d",
                    "start": start.isoformat(),
                    "end": tomorrow_close.isoformat(),
                }
            ),
            execution_config=json.dumps({"initial_capital_usd": 60_000}),
        )
    finally:
        store.close()

    assert response["isError"] is True
    assert "incomplete" in response["error"].lower()
    assert response["source_quality"]["blocking_quality_warning"] is True
    assert "requested_window_not_yet_closed" in response["source_quality"]["warnings"]


@pytest.mark.asyncio
async def test_backtest_rejects_unread_fail_closed_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Flags the server never read must not be silently accepted.

    ``require_complete_coverage`` and ``fail_closed`` appeared nowhere in the
    server, so a caller believed it had opted into a guarantee that was never
    enforced. Coverage is now always fail-closed, and the flags are refused.
    """
    monkeypatch.setattr(server._state, "store", CryptoStore(str(tmp_path / "crypto.duckdb")))
    monkeypatch.setattr(server._state, "settings", Settings())

    response = await server.crypto_backtest_run_strategy(
        strategy_spec=json.dumps({"template": "spot_trend_follow"}),
        data_config=json.dumps(
            {
                "product_id": "BTC-USD",
                "timeframe": "1d",
                "start": "2026-01-01T00:00:00Z",
                "end": "2026-01-10T00:00:00Z",
                "require_complete_coverage": True,
                "fail_closed": True,
            }
        ),
        execution_config=json.dumps({"initial_capital_usd": 60_000}),
    )

    assert response["isError"] is True
    assert "fail_closed" in response["error"]
    assert "require_complete_coverage" in response["error"]


def _sample_artifact() -> dict[str, Any]:
    """Build a valid exported artifact for storage and retrieval tests."""
    return export_artifact(
        job_id="crypto_bt_test",
        strategy_spec={"template": "spot_trend_follow", "parameters": {"fast_window": 2}},
        risk_config={"max_position_pct": 10},
        execution_profile={"paper_backend": "internal_coinbase_paper_ledger"},
        backtest_result={
            "data_config": {
                "product_id": "BTC-USD",
                "timeframe": "1d",
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-10T00:00:00+00:00",
            },
            "quality_status": "execution_grade",
            "source_quality_fingerprint": "abc123",
        },
    )


async def test_store_reads_back_exported_artifact(tmp_path: Path) -> None:
    """Exported artifacts must be retrievable.

    The ``artifacts`` table was write-only — no SELECT existed anywhere — so a
    validation turn could never load what an export turn had produced.
    """
    store = CryptoStore(str(tmp_path / "crypto.duckdb"))
    artifact = _sample_artifact()
    await store.store_artifact(artifact["fingerprint"], artifact)

    stored = await store.get_artifact(artifact["fingerprint"])

    assert stored is not None
    assert stored["fingerprint"] == artifact["fingerprint"]
    assert stored["artifact"] == artifact
    assert await store.get_artifact("no-such-fingerprint") is None


def test_validate_artifact_binds_to_external_storage_key() -> None:
    """A fingerprint carried inside the payload cannot certify the payload.

    ``validate_artifact`` recomputes the fingerprint from the same dict that
    carries it, so a self-consistent artifact always validates — including one
    whose risk limits were swapped. Binding to the key the row was filed under
    is what makes retrieval validation falsifiable.
    """
    artifact = _sample_artifact()
    assert validate_artifact(artifact, storage_key=artifact["fingerprint"])["valid"] is True

    tampered = {**artifact, "risk": {"max_position_pct": 99}}
    tampered["fingerprint"] = artifact_fingerprint(tampered)

    assert validate_artifact(tampered)["valid"] is True

    stale = validate_artifact(tampered, storage_key=artifact["fingerprint"])
    assert stale["valid"] is False
    assert "fingerprint does not match storage key" in stale["errors"]


async def test_export_returns_artifact_id_that_get_artifact_resolves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The export -> validate handoff needs a named, resolvable identifier."""
    store = CryptoStore(str(tmp_path / "crypto.duckdb"))
    monkeypatch.setattr(server._state, "store", store)
    monkeypatch.setattr(server._state, "settings", Settings())
    await store.store_job(
        "crypto_bt_test",
        status="completed",
        result={
            "data_config": {
                "product_id": "BTC-USD",
                "timeframe": "1d",
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-10T00:00:00+00:00",
            },
            "quality_status": "execution_grade",
            "source_quality_fingerprint": "abc123",
            "source_quality": {"blocking_quality_warning": None},
        },
    )

    exported = await server.crypto_strategy_export_artifact(
        job_id="crypto_bt_test",
        strategy_spec=json.dumps(
            {"template": "spot_trend_follow", "parameters": {"fast_window": 2}}
        ),
        risk_config=json.dumps({"max_position_pct": 10}),
        execution_profile=json.dumps({"paper_backend": "internal_coinbase_paper_ledger"}),
    )

    assert exported["artifact_id"] == exported["artifact"]["fingerprint"]

    fetched = await server.crypto_strategy_get_artifact(exported["artifact_id"])

    assert fetched["artifact"] == exported["artifact"]
    assert fetched["validation"]["valid"] is True
    assert (await server.crypto_strategy_get_artifact("no-such-fingerprint"))["isError"] is True


def test_displayed_identity_is_covered_by_the_fingerprint() -> None:
    """A stored artifact must not name one product while describing another.

    ``strategy_id`` and ``human_name`` are what a specialist surfaces to the
    user, so leaving them outside the fingerprint let a row display "ETH-USD"
    while its load-bearing ``symbols`` said BTC-USD, and still validate.
    """
    artifact = _sample_artifact()

    for field, value in (
        ("strategy_id", "eth_usd_totally_different_v1"),
        ("human_name", "ETH-USD Coinbase Spot Strategy"),
        ("market", "equity"),
        ("version", "9.9"),
    ):
        mutated = {**artifact, field: value}
        assert validate_artifact(mutated)["valid"] is False, field


async def test_unbound_validation_declares_its_own_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Validating a loose payload proves internal consistency only, and says so."""
    monkeypatch.setattr(server._state, "store", CryptoStore(str(tmp_path / "crypto.duckdb")))
    monkeypatch.setattr(server._state, "settings", Settings())
    artifact = _sample_artifact()

    loose = await server.crypto_strategy_validate_artifact(json.dumps(artifact))
    bound = await server.crypto_strategy_validate_artifact(
        json.dumps(artifact), artifact_id="not-the-key"
    )

    assert loose["scope"] == "internal_consistency_only"
    assert loose["valid"] is True
    assert bound["scope"] == "storage_bound"
    assert bound["valid"] is False
    assert "fingerprint does not match storage key" in bound["errors"]


def _trend_spec(**params: Any) -> dict[str, Any]:
    """Build a trend-follow spec with caller-supplied parameters."""
    return {"template": "spot_trend_follow", "parameters": params}


def test_misspelled_parameter_is_rejected_not_defaulted() -> None:
    """A typo inside parameters ran a different strategy and reported success.

    Unknown keys are rejected on strategy_spec itself, so the promise is
    already made; it just stopped one level above the values that decide
    what the backtest does. slow_widow silently became slow_window=50.
    """
    candles = [_candle(idx, close=100.0 + idx) for idx in range(12)]

    with pytest.raises(ValueError, match="slow_widow"):
        run_bar_backtest(
            candles=candles,
            strategy_spec=_trend_spec(fast_window=2, slow_widow=5),
            execution_config={"initial_capital_usd": 100_000},
        )


def test_correctly_spelled_parameters_still_run() -> None:
    """The accepted set must not reject the keys the template documents."""
    candles = [_candle(idx, close=100.0 + idx) for idx in range(12)]

    windowed = run_bar_backtest(
        candles=candles,
        strategy_spec=_trend_spec(fast_window=2, slow_window=5),
        execution_config={"initial_capital_usd": 100_000},
    )
    defaulted = run_bar_backtest(
        candles=candles,
        strategy_spec=_trend_spec(),
        execution_config={"initial_capital_usd": 100_000},
    )

    # The windows have to reach the signal: with slow_window defaulting to 50
    # against twelve candles the strategy never trades at all, which is what
    # the misspelled key silently produced.
    assert windowed.trades
    assert not defaulted.trades


def test_parameters_are_validated_per_template() -> None:
    """Each template accepts only its own parameters, not the other's."""
    candles = [_candle(idx, close=100.0 + idx) for idx in range(12)]

    with pytest.raises(ValueError, match="fast_window"):
        run_bar_backtest(
            candles=candles,
            strategy_spec={
                "template": "spot_mean_reversion",
                "parameters": {"window": 5, "fast_window": 2},
            },
            execution_config={"initial_capital_usd": 100_000},
        )


def test_unsupported_template_is_still_rejected() -> None:
    """Validating parameters must not swallow an unknown template."""
    candles = [_candle(idx, close=100.0 + idx) for idx in range(12)]

    with pytest.raises(ValueError, match="Unsupported v1 crypto strategy template"):
        run_bar_backtest(
            candles=candles,
            strategy_spec={"template": "spot_momentum", "parameters": {}},
            execution_config={"initial_capital_usd": 100_000},
        )
