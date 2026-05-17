"""Tests for engine.backtest — simulator + summary + monte_carlo_input."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.engine.backtest import (
    BacktestMarket,
    build_monte_carlo_input,
    simulate_rule,
    summarize_trades,
)
from src.engine.rules import validate_rule
from src.storage import PriceRow


def _now() -> datetime:
    return datetime(2026, 5, 16, tzinfo=timezone.utc)


def _row(token: str, condition: str, ts: datetime, price: float) -> PriceRow:
    return PriceRow(
        token_id=token,
        condition_id=condition,
        timestamp=ts,
        price=price,
        fidelity_minutes=60,
        source="clob_prices_history",
        fetched_at=ts,
    )


def _rule(price_min: float = 0.05, price_max: float = 0.15):
    return validate_rule(
        {
            "side": "YES",
            "entry": {"price_min": price_min, "price_max": price_max},
            "exit": {"type": "hold_to_resolution"},
        }
    )


def _winner_market(condition: str) -> BacktestMarket:
    rows = [
        _row(f"{condition}-Y", condition, _now() - timedelta(days=10), 0.10),
        _row(f"{condition}-Y", condition, _now() - timedelta(days=5), 0.30),
        _row(f"{condition}-Y", condition, _now() - timedelta(days=1), 1.00),
    ]
    return BacktestMarket(
        condition_id=condition,
        event_slug="event-x",
        end_date=_now(),
        winning_outcome_label="Yes",
        yes_token_rows=rows,
    )


def _loser_market(condition: str) -> BacktestMarket:
    rows = [
        _row(f"{condition}-Y", condition, _now() - timedelta(days=10), 0.10),
        _row(f"{condition}-Y", condition, _now() - timedelta(days=5), 0.05),
        _row(f"{condition}-Y", condition, _now() - timedelta(days=1), 0.0),
    ]
    return BacktestMarket(
        condition_id=condition,
        event_slug="event-y",
        end_date=_now(),
        winning_outcome_label="No",
        yes_token_rows=rows,
    )


def test_simulate_rule_yields_one_trade_per_market_with_eligible_entry() -> None:
    """One winner + one loser at p=0.10 entry → two trades."""
    rule = _rule(0.05, 0.15)
    markets = [_winner_market("0xW"), _loser_market("0xL")]
    trades = simulate_rule(rule, markets)
    assert len(trades) == 2
    assert {t.condition_id for t in trades} == {"0xW", "0xL"}


def test_simulate_rule_terminal_payoff_math() -> None:
    """Win at p=0.10: return_on_cost = 9, pnl = 0.9. Lose: return_on_cost = -1, pnl = -0.10."""
    rule = _rule(0.05, 0.15)
    trades = simulate_rule(rule, [_winner_market("0xW"), _loser_market("0xL")])
    by_id = {t.condition_id: t for t in trades}
    winner = by_id["0xW"]
    loser = by_id["0xL"]
    assert winner.realized_win is True
    assert winner.entry_price == 0.10
    assert winner.return_on_cost == pytest.approx(9.0, abs=1e-9)
    assert winner.pnl_per_contract == pytest.approx(0.90, abs=1e-9)
    assert loser.realized_win is False
    assert loser.return_on_cost == -1.0
    assert loser.pnl_per_contract == pytest.approx(-0.10, abs=1e-9)


def test_simulate_rule_skips_markets_outside_entry_band() -> None:
    """No row inside [0.50, 0.60] in either market → 0 trades, not a crash."""
    rule = _rule(0.50, 0.60)
    trades = simulate_rule(rule, [_winner_market("0xW")])
    assert trades == []


def test_summarize_trades_distribution_fields() -> None:
    """Two trades (one win, one loss) → win_rate=0.5, win_count=1, loss_count=1."""
    rule = _rule(0.05, 0.15)
    trades = simulate_rule(rule, [_winner_market("0xW"), _loser_market("0xL")])
    summary = summarize_trades(trades)
    assert summary["sample_size"] == 2
    assert summary["win_count"] == 1
    assert summary["loss_count"] == 1
    assert summary["win_rate"] == 0.5
    assert "best_trade" in summary and "worst_trade" in summary


def test_summarize_trades_returns_stable_zero_shape_on_empty() -> None:
    """No trades → all fields present, zeros, no exception."""
    summary = summarize_trades([])
    assert summary["sample_size"] == 0
    assert summary["best_trade"] is None
    for key in ("win_rate", "avg_return_on_cost", "median_return_on_cost"):
        assert summary[key] == 0.0


def test_simulate_rule_deterministic_across_runs() -> None:
    """Same inputs → identical trade lists (same order, same values)."""
    rule = _rule(0.05, 0.15)
    markets = [_winner_market("0xA"), _loser_market("0xB"), _winner_market("0xC")]
    trades_1 = simulate_rule(rule, markets)
    trades_2 = simulate_rule(rule, markets)
    assert trades_1 == trades_2


def test_monte_carlo_input_shape() -> None:
    """Compact §10.5 payload should include returns + fingerprint + condition_ids."""
    rule = _rule(0.05, 0.15)
    trades = simulate_rule(rule, [_winner_market("0xW"), _loser_market("0xL")])
    mc = build_monte_carlo_input(
        trades=trades,
        seed=42,
        source_backtest_fingerprint="abcd1234",
        limitations=["test_limitation"],
    )
    assert mc["return_type"] == "return_on_cost"
    assert mc["seed"] == 42
    assert mc["source_backtest_fingerprint"] == "abcd1234"
    assert len(mc["returns"]) == 2
    assert mc["condition_ids"] == ["0xW", "0xL"]
    assert mc["limitations"] == ["test_limitation"]


def test_simulate_rule_ttr_filter_skips_only_late_entries() -> None:
    """Regression: TTR filter must evaluate at entry time, not invocation time.

    The earliest in-band sample is 10 days before resolution; a rule with
    min_days_to_resolution=5 should KEEP this market (10 >= 5), not skip
    every closed market as the original wall-clock check did.
    """
    rule = validate_rule(
        {
            "side": "YES",
            "entry": {"price_min": 0.05, "price_max": 0.15},
            "exit": {"type": "hold_to_resolution"},
            "filters": {"min_days_to_resolution": 5},
        }
    )
    skipped: dict[str, int] = {}
    trades = simulate_rule(rule, [_winner_market("0xW")], out_skipped=skipped)
    assert len(trades) == 1
    assert skipped.get("ttr_min_unmet", 0) == 0


def test_simulate_rule_ttr_filter_drops_late_entries_and_counts_them() -> None:
    """Same market, but min_days_to_resolution=15 means the 10-day entry is too late."""
    rule = validate_rule(
        {
            "side": "YES",
            "entry": {"price_min": 0.05, "price_max": 0.15},
            "exit": {"type": "hold_to_resolution"},
            "filters": {"min_days_to_resolution": 15},
        }
    )
    skipped: dict[str, int] = {}
    trades = simulate_rule(rule, [_winner_market("0xW")], out_skipped=skipped)
    assert trades == []
    assert skipped.get("ttr_min_unmet") == 1
