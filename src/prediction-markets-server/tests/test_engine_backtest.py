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


def test_summarize_trades_hold_to_resolution_exit_breakdown_only_resolution() -> None:
    """For pure hold-to-resolution trades, all four reasons surface; only resolution has count."""
    rule = _rule(0.05, 0.15)
    trades = simulate_rule(rule, [_winner_market("0xW"), _loser_market("0xL")])
    summary = summarize_trades(trades)
    breakdown = summary["exit_breakdown"]
    assert set(breakdown.keys()) == {"stop", "take_profit", "expiry", "resolution"}
    assert breakdown["resolution"]["count"] == 2
    assert breakdown["resolution"]["share"] == 1.0
    assert breakdown["resolution"]["win_rate_at_resolution"] == 0.5
    for reason in ("stop", "take_profit", "expiry"):
        assert breakdown[reason]["count"] == 0
        assert breakdown[reason]["share"] == 0.0


def test_simulate_rule_trade_includes_resolution_exit_metadata() -> None:
    """Each hold_to_resolution trade carries exit_reason='resolution' and time_to_exit_days."""
    rule = _rule(0.05, 0.15)
    trades = simulate_rule(rule, [_winner_market("0xW")])
    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "resolution"
    assert trade.time_to_exit_days == pytest.approx(9.0, abs=1e-9)


# ── stop_take_profit walk ──


def _stp_rule(
    *,
    price_min: float = 0.05,
    price_max: float = 0.15,
    stop_price: float | None = None,
    take_profit_price: float | None = None,
    max_hold_days: int | None = None,
):
    exit_rule: dict[str, object] = {"type": "stop_take_profit"}
    if stop_price is not None:
        exit_rule["stop_price"] = stop_price
    if take_profit_price is not None:
        exit_rule["take_profit_price"] = take_profit_price
    if max_hold_days is not None:
        exit_rule["max_hold_days"] = max_hold_days
    return validate_rule(
        {
            "side": "YES",
            "entry": {"price_min": price_min, "price_max": price_max},
            "exit": exit_rule,
        }
    )


def _market_from_prices(
    condition: str,
    prices: list[tuple[int, float]],  # (days_before_end, price)
    *,
    winning_outcome: str = "Yes",
) -> BacktestMarket:
    rows = [_row(f"{condition}-Y", condition, _now() - timedelta(days=d), p) for d, p in prices]
    return BacktestMarket(
        condition_id=condition,
        event_slug=f"event-{condition}",
        end_date=_now(),
        winning_outcome_label=winning_outcome,
        yes_token_rows=rows,
    )


def test_simulate_rule_stop_only_fires_on_first_row_below_stop() -> None:
    """Earliest in-band sample is entry; first subsequent row at-or-below stop wins."""
    market = _market_from_prices(
        "0xS",
        [(20, 0.10), (15, 0.08), (10, 0.03), (5, 0.50), (1, 1.00)],
    )
    rule = _stp_rule(stop_price=0.04)
    trades = simulate_rule(rule, [market])
    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "stop"
    assert trade.exit_price == 0.03
    assert trade.entry_price == 0.10
    assert trade.return_on_cost == pytest.approx((0.03 - 0.10) / 0.10, abs=1e-9)
    assert trade.realized_win is False
    assert trade.time_to_exit_days == pytest.approx(10.0, abs=1e-9)


def test_simulate_rule_take_profit_only_fires_on_first_row_above_target() -> None:
    """Stop unset: a row meeting take-profit fires take_profit, not resolution."""
    market = _market_from_prices(
        "0xT",
        [(20, 0.10), (15, 0.12), (10, 0.55), (1, 1.00)],
    )
    rule = _stp_rule(take_profit_price=0.50)
    trades = simulate_rule(rule, [market])
    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "take_profit"
    assert trade.exit_price == 0.55
    assert trade.realized_win is True


def test_simulate_rule_expiry_fires_at_first_row_past_boundary() -> None:
    """max_hold_days=7: first row past entry+7d triggers expiry, not resolution."""
    market = _market_from_prices(
        "0xE",
        [(20, 0.10), (15, 0.12), (12, 0.14), (1, 1.00)],
        winning_outcome="Yes",
    )
    # entry at day -20; max_hold_days=7 → boundary at day -13. First row at-or-past
    # boundary is (15, 0.12) — exactly 5 days in, which is < 7. Next row (12, 0.14)
    # is 8 days in; first to trigger expiry. (Stop/TP unset.)
    rule = _stp_rule(max_hold_days=7)
    trades = simulate_rule(rule, [market])
    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "expiry"
    assert trade.exit_price == 0.14
    assert trade.time_to_exit_days == pytest.approx(8.0, abs=1e-9)


def test_simulate_rule_stop_take_profit_max_hold_combined_first_match_wins() -> None:
    """Stop fires before take-profit and before expiry — order is stop > tp > expiry."""
    market = _market_from_prices(
        "0xC",
        [(30, 0.10), (25, 0.02), (10, 0.99), (1, 1.00)],
    )
    rule = _stp_rule(stop_price=0.04, take_profit_price=0.40, max_hold_days=14)
    trades = simulate_rule(rule, [market])
    assert len(trades) == 1
    assert trades[0].exit_reason == "stop"
    assert trades[0].exit_price == 0.02


def test_simulate_rule_expiry_wins_over_take_profit_when_row_is_past_boundary() -> None:
    """When a single sampled row both crosses TP and sits past max_hold, label it expiry.

    Without this rule, exit_breakdown would attribute max-hold exits to
    whatever price trigger happened to be in the same sampling bucket and
    over-count take_profit / stop on long-horizon rules.
    """
    market = _market_from_prices(
        "0xMH",
        # entry at day -20, max_hold_days=7 → boundary day -13
        # row at day -10 is 10d past entry (past boundary) AND price 0.95 ≥ TP 0.90
        [(20, 0.10), (15, 0.12), (10, 0.95), (1, 1.00)],
    )
    rule = _stp_rule(take_profit_price=0.90, max_hold_days=7)
    trades = simulate_rule(rule, [market])
    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "expiry"
    assert trade.exit_price == 0.95
    assert trade.time_to_exit_days == pytest.approx(10.0, abs=1e-9)


def test_simulate_rule_falls_back_to_resolution_when_no_trigger_fires() -> None:
    """Stop set low, no TP, no max-hold: nothing triggers → resolution payoff."""
    market = _market_from_prices(
        "0xR",
        [(20, 0.10), (15, 0.11), (10, 0.12), (1, 0.04)],
        winning_outcome="No",
    )
    # stop_price below every sampled price after entry → stop never fires.
    rule = _stp_rule(stop_price=0.01)
    trades = simulate_rule(rule, [market])
    assert len(trades) == 1
    trade = trades[0]
    assert trade.exit_reason == "resolution"
    # hold-to-resolution math: realized_win driven by winning_outcome_label, not last-sample sign
    assert trade.realized_win is False
    assert trade.return_on_cost == -1.0


def test_simulate_rule_falls_back_to_resolution_when_max_hold_covers_full_lifetime() -> None:
    """max_hold_days > market lifetime → boundary never hit; fall back to resolution."""
    market = _market_from_prices("0xL", [(5, 0.10), (1, 1.0)])
    rule = _stp_rule(max_hold_days=30)
    trades = simulate_rule(rule, [market])
    assert len(trades) == 1
    assert trades[0].exit_reason == "resolution"


def test_simulate_rule_skips_when_max_hold_violated_and_no_row_past_boundary() -> None:
    """Data gap: resolution falls past entry+max_hold but no sample straddles boundary."""
    # Entry at day -30 (price 0.10). Last YES sample at day -25 (5 days into hold).
    # max_hold_days = 7 → boundary at day -23. No row at-or-after day -23 exists.
    # market.end_date is _now() (day 0), well past day -23 → skip.
    market = BacktestMarket(
        condition_id="0xG",
        event_slug="event-gap",
        end_date=_now(),
        winning_outcome_label="Yes",
        yes_token_rows=[
            _row("0xG-Y", "0xG", _now() - timedelta(days=30), 0.10),
            _row("0xG-Y", "0xG", _now() - timedelta(days=25), 0.11),
        ],
    )
    rule = _stp_rule(max_hold_days=7)
    skipped: dict[str, int] = {}
    trades = simulate_rule(rule, [market], out_skipped=skipped)
    assert trades == []
    assert skipped.get("no_exit_price_for_max_hold") == 1


def test_simulate_rule_skips_entry_when_no_row_in_band() -> None:
    """stop_take_profit must still record no_eligible_entry when entry band is empty."""
    market = _market_from_prices("0xN", [(10, 0.50), (5, 0.55)])
    rule = _stp_rule(stop_price=0.04)
    skipped: dict[str, int] = {}
    trades = simulate_rule(rule, [market], out_skipped=skipped)
    assert trades == []
    assert skipped.get("no_eligible_entry") == 1


def test_summarize_trades_exit_breakdown_share_sums_to_one() -> None:
    """Mixed exit reasons across markets: share fractions sum to 1.0."""
    # Stop fires on one market; another resolves (terminal sample < TP, no max-hold).
    stop_market = _market_from_prices(
        "0xS1", [(20, 0.10), (10, 0.02), (1, 0.0)], winning_outcome="No"
    )
    res_market = _market_from_prices(
        "0xR1", [(20, 0.10), (10, 0.11), (1, 0.20)], winning_outcome="Yes"
    )
    # take_profit at 0.95 — neither market reaches it; stop at 0.04 fires on res_market's
    # entry? No — entry validator rejects stop >= price_min. stop_market entry is 0.10,
    # walk sees 0.02 → stop fires. res_market walks 0.11, 0.20 → no stop → resolution.
    rule = _stp_rule(stop_price=0.04, take_profit_price=0.95)
    trades = simulate_rule(rule, [stop_market, res_market])
    assert len(trades) == 2
    summary = summarize_trades(trades)
    breakdown = summary["exit_breakdown"]
    total_share = sum(
        breakdown[reason]["share"] for reason in ("stop", "take_profit", "expiry", "resolution")
    )
    assert total_share == pytest.approx(1.0, abs=1e-9)
    assert breakdown["stop"]["count"] == 1
    assert breakdown["resolution"]["count"] == 1
    assert breakdown["resolution"]["win_rate_at_resolution"] == 1.0
