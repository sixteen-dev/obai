"""Tests for core backtest loop."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from src.engine.backtester import (
    MAX_SLIPPAGE_PCT,
    MIN_SLIPPAGE_PCT,
    BacktestConfig,
    Trade,
    combine_equity_curves,
    compute_entry_fill,
    compute_exit_fill,
    run_backtest,
    run_multi_symbol_backtest,
)
from src.models.strategy import PositionSizing, RiskManagement


def _make_signal_df(  # noqa: PLR0913
    prices: list[float],
    entries: list[bool],
    exits: list[bool],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[int] | None = None,
) -> pl.DataFrame:
    """Create a DataFrame with signals for testing."""
    n = len(prices)
    dates = [date(2023, 1, i + 2) for i in range(n)]
    return pl.DataFrame(
        {
            "date": dates,
            "open": prices,
            "high": highs if highs else [p + 1.0 for p in prices],
            "low": lows if lows else [p - 1.0 for p in prices],
            "close": prices,
            "volume": volumes if volumes else [1_000_000] * n,
            "entry_signal": entries,
            "exit_signal": exits,
        }
    )


class TestRunBacktest:
    """Test the core backtest loop."""

    def test_no_trades_with_no_signals(self) -> None:
        """No entry signals should produce no trades."""
        df = _make_signal_df(
            prices=[100.0, 101.0, 102.0, 103.0, 104.0],
            entries=[False, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement()

        equity_df, trades = run_backtest(df, sizing, risk)

        assert len(trades) == 0
        assert len(equity_df) == 5
        # Equity should be flat at 100k
        assert equity_df["equity"][0] == 100_000.0
        assert equity_df["equity"][-1] == 100_000.0

    def test_single_trade_entry_exit(self) -> None:
        """Entry then exit signal should produce exactly one trade."""
        df = _make_signal_df(
            prices=[100.0, 102.0, 104.0, 106.0, 108.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement()

        _, trades = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(symbol="TEST", slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].symbol == "TEST"
        assert trades[0].exit_reason == "signal"

    def test_stop_loss_triggers(self) -> None:
        """Stop loss should exit when price drops sufficiently."""
        # Enter at 100, then price drops to 90 (10% loss)
        df = _make_signal_df(
            prices=[100.0, 100.0, 95.0, 90.0, 85.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement(stop_loss_pct=5.0)  # 5% stop loss

        _, trades = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) >= 1
        assert trades[0].exit_reason == "stop_loss"

    def test_take_profit_triggers(self) -> None:
        """Take profit should exit when price rises sufficiently."""
        # Enter at 100, price rises to 120 (20% gain)
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 120.0, 125.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement(take_profit_pct=15.0)  # 15% take profit

        _, trades = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) >= 1
        assert trades[0].exit_reason == "take_profit"

    def test_equity_starts_at_100k(self) -> None:
        """Equity curve should start at 100,000."""
        df = _make_signal_df(
            prices=[100.0, 101.0, 102.0],
            entries=[False, False, False],
            exits=[False, False, False],
        )
        eq_df, _ = run_backtest(df, PositionSizing(), RiskManagement())

        assert eq_df["equity"][0] == 100_000.0

    def test_slippage_reduces_returns(self) -> None:
        """Slippage should reduce trade returns."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement()

        _, trades_no_slip = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )
        _, trades_with_slip = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.5, commission_pct=0.0),
        )

        # With slippage, returns should be worse
        assert trades_with_slip[0].return_pct < trades_no_slip[0].return_pct

    def test_commission_reduces_returns(self) -> None:
        """Commission should reduce trade returns."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement()

        _, trades_no_comm = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )
        _, trades_with_comm = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.5),
        )

        assert trades_with_comm[0].return_pct < trades_no_comm[0].return_pct


class TestEntryBarStopBinds:
    """Stop/TP pierced on the entry bar must bind that same bar."""

    def test_stop_hits_on_entry_bar(self) -> None:
        """Entry-bar low piercing the stop closes that bar at the stop, not close."""
        # Entry signal on bar 0 -> fill at bar 1 open (100). Stop at 95.
        # Bar 1 opens above the stop but its low (94) pierces it intrabar.
        df = _make_signal_df(
            prices=[100.0, 100.0, 100.0, 100.0],
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            lows=[99.0, 94.0, 99.0, 99.0],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement(stop_loss_pct=5.0)

        _, trades = run_backtest(
            df,
            sizing,
            risk,
            config=BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        trade = trades[0]
        assert trade.exit_reason == "stop_loss"
        # Closed on the entry bar itself (same date), at the stop level not close.
        assert trade.entry_date == trade.exit_date
        assert trade.entry_price == pytest.approx(100.0)
        assert trade.exit_price == pytest.approx(95.0)


class TestTradeDataclass:
    """Test Trade serialization."""

    def test_to_dict(self) -> None:
        """Trade should serialize to dict with rounded values."""
        trade = Trade(
            symbol="AAPL",
            entry_date="2023-01-10",
            entry_price=150.123,
            exit_date="2023-02-15",
            exit_price=165.456,
            return_pct=10.2345,
            holding_days=36,
            exit_reason="signal",
        )
        d = trade.to_dict()
        assert d["entry_price"] == 150.12
        assert d["exit_price"] == 165.46
        assert d["return_pct"] == 10.2345


class TestMultiSymbolBacktest:
    """Test multi-symbol backtest."""

    def test_combines_equity_curves(self) -> None:
        """Multi-symbol should produce a single combined equity curve."""
        df1 = _make_signal_df(
            prices=[100.0, 102.0, 104.0],
            entries=[False, False, False],
            exits=[False, False, False],
        )
        df2 = _make_signal_df(
            prices=[50.0, 51.0, 52.0],
            entries=[False, False, False],
            exits=[False, False, False],
        )

        combined_eq, trades = run_multi_symbol_backtest(
            {"AAPL": df1, "MSFT": df2},
            PositionSizing(),
            RiskManagement(),
        )

        assert "equity" in combined_eq.columns
        assert "date" in combined_eq.columns
        assert len(trades) == 0

    def test_empty_symbols_returns_empty(self) -> None:
        """No symbols should return empty results."""
        combined_eq, trades = run_multi_symbol_backtest(
            {},
            PositionSizing(),
            RiskManagement(),
        )

        assert combined_eq.is_empty()
        assert len(trades) == 0

    def test_combine_equity_curves_sorted_and_ffilled(self) -> None:
        """Combined curve is chronological and holds (not re-bases) on gaps.

        Two per-symbol sleeves with mutually-differing date coverage are fed
        so a naive full-join would leave the output unsorted and would drop
        the missing sleeve from the blended mean on dates it lacks a bar.

        Curve A covers [d1, d2, d4]; curve B covers [d2, d3, d4]. Each sleeve
        starts at initial capital (100_000) on its first row. The fix sorts by
        date, forward-fills interior gaps ("hold"), back-fills pre-inception
        dates to the sleeve's initial capital, then averages. On d3 (A has no
        bar) A must carry its d2 equity of 110_000 rather than dropping out.
        """
        d1, d2, d3, d4 = (
            date(2020, 1, 1),
            date(2020, 1, 2),
            date(2020, 1, 3),
            date(2020, 1, 4),
        )
        curve_a = pl.DataFrame(
            {"date": [d1, d2, d4], "equity_AAPL": [100_000.0, 110_000.0, 120_000.0]},
        )
        curve_b = pl.DataFrame(
            {"date": [d2, d3, d4], "equity_MSFT": [100_000.0, 105_000.0, 130_000.0]},
        )

        combined = combine_equity_curves([curve_a, curve_b])

        dates = combined["date"].to_list()
        assert dates == [d1, d2, d3, d4]
        assert dates == sorted(dates)

        equity = dict(zip(dates, combined["equity"].to_list(), strict=True))
        # d1: A=100_000, B back-filled to its initial 100_000 -> mean 100_000.
        assert equity[d1] == pytest.approx(100_000.0)
        # d2: A=110_000, B=100_000 -> mean 105_000.
        assert equity[d2] == pytest.approx(105_000.0)
        # d3: A carried from d2 (110_000), B=105_000 -> mean 107_500 (not 105_000).
        assert equity[d3] == pytest.approx(107_500.0)
        # d4: A=120_000, B=130_000 -> mean 125_000.
        assert equity[d4] == pytest.approx(125_000.0)


class TestVolumeScaledSlippage:
    """Test volume-scaled slippage in fill functions."""

    def test_no_volume_data_falls_back_to_flat(self) -> None:
        """Without volume data, fill matches flat slippage."""
        flat = compute_entry_fill(100.0, 0.1)
        scaled = compute_entry_fill(100.0, 0.1, order_shares=None, bar_volume=None)
        assert flat == scaled

    def test_zero_volume_falls_back_to_flat(self) -> None:
        """Zero bar volume falls back to flat slippage."""
        flat = compute_entry_fill(100.0, 0.1)
        scaled = compute_entry_fill(100.0, 0.1, order_shares=100.0, bar_volume=0)
        assert flat == scaled

    def test_reference_participation_equals_base(self) -> None:
        """At 1% participation (reference rate), effective ≈ base slippage."""
        # 100 shares / 10,000 volume = 1% participation = reference
        fill = compute_entry_fill(100.0, 0.1, order_shares=100.0, bar_volume=10_000)
        expected = 100.0 * (1 + 0.1 / 100)
        assert fill == pytest.approx(expected, rel=1e-6)

    def test_low_participation_reduces_slippage(self) -> None:
        """Below reference rate, effective slippage < base."""
        # 10 shares / 1,000,000 volume = 0.001% participation
        low_fill = compute_entry_fill(100.0, 0.1, order_shares=10.0, bar_volume=1_000_000)
        flat_fill = compute_entry_fill(100.0, 0.1)
        assert low_fill < flat_fill

    def test_high_participation_increases_slippage(self) -> None:
        """Above reference rate, effective slippage > base."""
        # 25,000 shares / 100,000 volume = 25% participation
        high_fill = compute_entry_fill(100.0, 0.1, order_shares=25_000.0, bar_volume=100_000)
        flat_fill = compute_entry_fill(100.0, 0.1)
        assert high_fill > flat_fill

    def test_clamped_at_max(self) -> None:
        """Extreme participation should be capped at MAX_SLIPPAGE_PCT."""
        # 100% participation → sqrt(1/0.01) = 10, so 0.1 * 10 = 1.0
        # Not hit yet, need more extreme: 500% participation
        fill = compute_entry_fill(100.0, 0.1, order_shares=500_000.0, bar_volume=100_000)
        max_fill = 100.0 * (1 + MAX_SLIPPAGE_PCT / 100)
        assert fill == pytest.approx(max_fill, rel=1e-6)

    def test_clamped_at_min(self) -> None:
        """Very low participation should floor at MIN_SLIPPAGE_PCT."""
        fill = compute_entry_fill(100.0, 0.1, order_shares=1.0, bar_volume=100_000_000)
        min_fill = 100.0 * (1 + MIN_SLIPPAGE_PCT / 100)
        assert fill == pytest.approx(min_fill, rel=1e-6)

    def test_entry_fill_always_above_open(self) -> None:
        """Buy fill should always be >= open price."""
        fill = compute_entry_fill(100.0, 0.1, order_shares=500.0, bar_volume=50_000)
        assert fill > 100.0

    def test_exit_signal_fill_below_open(self) -> None:
        """Signal sell fill should be <= open price."""
        fill = compute_exit_fill(
            "signal",
            100.0,
            99.0,
            None,
            None,
            0.1,
            order_shares=500.0,
            bar_volume=50_000,
        )
        assert fill < 100.0

    def test_exit_stop_unaffected_by_volume(self) -> None:
        """Stop loss fills should ignore volume scaling."""
        no_vol = compute_exit_fill("stop_loss", 100.0, 99.0, 95.0, None, 0.1)
        with_vol = compute_exit_fill(
            "stop_loss",
            100.0,
            99.0,
            95.0,
            None,
            0.1,
            order_shares=500.0,
            bar_volume=50_000,
        )
        assert no_vol == with_vol

    def test_zero_slippage_with_scaling(self) -> None:
        """slippage_pct=0 with volume scaling should produce zero slippage."""
        fill = compute_entry_fill(100.0, 0.0, order_shares=500.0, bar_volume=50_000)
        # User explicitly set slippage to 0 — honor that even with scaling enabled
        assert fill == pytest.approx(100.0, rel=1e-10)


class TestSpreadCostInFills:
    """Test spread cost application in fill functions."""

    def test_entry_spread_increases_fill(self) -> None:
        """Spread cost should increase entry fill (buy at ask)."""
        no_spread = compute_entry_fill(100.0, 0.1)
        with_spread = compute_entry_fill(100.0, 0.1, spread_cost=0.005)
        assert with_spread > no_spread

    def test_exit_spread_decreases_fill(self) -> None:
        """Spread cost should decrease exit fill (sell at bid)."""
        no_spread = compute_exit_fill("signal", 100.0, 99.0, None, None, 0.1)
        with_spread = compute_exit_fill(
            "signal",
            100.0,
            99.0,
            None,
            None,
            0.1,
            spread_cost=0.005,
        )
        assert with_spread < no_spread

    def test_spread_cost_magnitude(self) -> None:
        """Half-spread of 0.5% on $100 should add/subtract $0.50."""
        fill = compute_entry_fill(100.0, 0.0, spread_cost=0.005)
        assert fill == pytest.approx(100.5, rel=1e-6)


class TestRegressionFlagsOff:
    """Verify flags off produces identical results to flat-rate behavior."""

    def test_single_symbol_flags_off_matches_default(self) -> None:
        """volume_scaled_slippage=False, spread=None should match original behavior."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement()

        # Default config (no volume scaling)
        default_cfg = BacktestConfig(slippage_pct=0.1, commission_pct=0.1)
        eq_default, trades_default = run_backtest(df, sizing, risk, default_cfg)

        # Explicit flags off
        flags_off = BacktestConfig(
            slippage_pct=0.1,
            commission_pct=0.1,
            volume_scaled_slippage=False,
            spread_estimates=None,
        )
        eq_flags, trades_flags = run_backtest(df, sizing, risk, flags_off)

        assert eq_default["equity"].to_list() == eq_flags["equity"].to_list()
        assert len(trades_default) == len(trades_flags)
        if trades_default:
            assert trades_default[0].return_pct == trades_flags[0].return_pct


class TestVolumeScaledBacktest:
    """Integration: volume-scaled slippage in a full backtest."""

    def test_liquid_stock_cheaper_than_illiquid(self) -> None:
        """Same strategy on high-volume vs low-volume should produce different costs."""
        entries = [True, False, False, False, False]
        exits = [False, False, True, False, False]
        prices = [100.0, 100.0, 110.0, 110.0, 110.0]

        # High volume (liquid) → lower slippage
        df_liquid = _make_signal_df(
            prices=prices, entries=entries, exits=exits, volumes=[10_000_000] * 5
        )
        # Low volume (illiquid) → higher slippage
        df_illiquid = _make_signal_df(
            prices=prices, entries=entries, exits=exits, volumes=[10_000] * 5
        )

        sizing = PositionSizing(max_position_pct=100.0)
        risk = RiskManagement()
        cfg = BacktestConfig(slippage_pct=0.1, commission_pct=0.0, volume_scaled_slippage=True)

        _, trades_liquid = run_backtest(df_liquid, sizing, risk, cfg)
        _, trades_illiquid = run_backtest(df_illiquid, sizing, risk, cfg)

        assert len(trades_liquid) == 1
        assert len(trades_illiquid) == 1
        # Liquid stock should have higher return (less slippage eaten)
        assert trades_liquid[0].return_pct > trades_illiquid[0].return_pct
