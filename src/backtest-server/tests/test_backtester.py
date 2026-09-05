"""Tests for core backtest loop."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime

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
    atr: list[float | None] | None = None,
    opens: list[float] | None = None,
) -> pl.DataFrame:
    """Create a DataFrame with signals for testing."""
    n = len(prices)
    dates = [date(2023, 1, i + 2) for i in range(n)]
    df = pl.DataFrame(
        {
            "date": dates,
            "open": opens if opens else prices,
            "high": highs if highs else [p + 1.0 for p in prices],
            "low": lows if lows else [p - 1.0 for p in prices],
            "close": prices,
            "volume": volumes if volumes else [1_000_000] * n,
            "entry_signal": entries,
            "exit_signal": exits,
        }
    )
    if atr is None:
        return df
    return df.with_columns(pl.Series("atr", atr, dtype=pl.Float64))


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


def _make_intraday_df(
    prices: list[float],
    entries: list[bool],
    timestamps: list[datetime],
) -> pl.DataFrame:
    """Create an intraday DataFrame with entry signals and no exit signals."""
    n = len(prices)
    return pl.DataFrame(
        {
            "date": timestamps,
            "open": prices,
            "high": [p + 1.0 for p in prices],
            "low": [p - 1.0 for p in prices],
            "close": prices,
            "volume": [1_000_000] * n,
            "entry_signal": entries,
            "exit_signal": [False] * n,
        }
    )


class TestEndOfBacktestCloseOut:
    """A position still open on the last bar must be reported as a trade."""

    def test_open_position_closes_at_the_final_close(self) -> None:
        """The run ends with a closed trade priced at the last available close."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0],
            entries=[True, False, False],
            exits=[False, False, False],
        )

        equity, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=50.0),
            RiskManagement(),
            BacktestConfig(
                symbol="OPEN",
                slippage_pct=0.0,
                commission_pct=0.0,
                initial_capital=1000.0,
            ),
        )

        assert len(trades) == 1
        assert trades[0].exit_reason == "end_of_backtest"
        assert trades[0].exit_date == "2023-01-04"
        assert trades[0].exit_price == pytest.approx(110.0)
        assert trades[0].return_pct == pytest.approx(10.0)
        # Closing at the mark leaves the equity curve unchanged.
        assert equity["equity"][-1] == pytest.approx(1050.0)


class TestCloseEod:
    """close_eod must force-close positions at session end, entry bar included."""

    def test_entry_on_last_bar_of_session_closes_same_session(self) -> None:
        """A position opened on the session's last bar cannot survive overnight."""
        df = _make_intraday_df(
            prices=[100.0, 100.0, 105.0, 110.0],
            entries=[True, False, False, False],
            timestamps=[
                datetime(2024, 1, 2, 15, 50),
                datetime(2024, 1, 2, 15, 55),
                datetime(2024, 1, 3, 9, 30),
                datetime(2024, 1, 3, 15, 55),
            ],
        )

        _, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(close_eod=True),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0, timeframe="5min"),
        )

        assert len(trades) == 1
        assert trades[0].exit_reason == "eod_close"
        assert trades[0].exit_date == "2024-01-02T15:55:00"
        assert trades[0].holding_minutes == 0

    def test_entry_on_interior_bar_holds_until_session_end(self) -> None:
        """An interior-bar entry is not force-closed until the session's last bar."""
        df = _make_intraday_df(
            prices=[100.0, 100.0, 105.0],
            entries=[True, False, False],
            timestamps=[
                datetime(2024, 1, 2, 9, 30),
                datetime(2024, 1, 2, 15, 50),
                datetime(2024, 1, 2, 15, 55),
            ],
        )

        _, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(close_eod=True),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0, timeframe="5min"),
        )

        assert len(trades) == 1
        assert trades[0].exit_reason == "eod_close"
        assert trades[0].exit_date == "2024-01-02T15:55:00"
        assert trades[0].holding_minutes == 5

    def test_daily_bars_close_on_the_entry_bar(self) -> None:
        """Every daily bar ends a session, so close_eod exits at that day's close."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0],
            entries=[True, False, False, False],
            exits=[False, False, False, False],
        )

        equity, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(close_eod=True),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].exit_reason == "eod_close"
        assert trades[0].entry_date == trades[0].exit_date == "2023-01-03"
        assert trades[0].holding_days == 0
        assert equity["equity"].n_unique() == 1  # entered and exited at 100, flat curve

    def test_early_close_session_detected_by_next_bar_date_change(self) -> None:
        """A short session ends at its last bar, whatever the wall-clock time."""
        df = _make_intraday_df(
            prices=[100.0, 100.0, 105.0],
            entries=[True, False, False],
            timestamps=[
                datetime(2024, 7, 3, 12, 55),
                datetime(2024, 7, 3, 13, 0),
                datetime(2024, 7, 5, 9, 30),
            ],
        )

        _, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(close_eod=True),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0, timeframe="5min"),
        )

        assert len(trades) == 1
        assert trades[0].exit_reason == "eod_close"
        assert trades[0].exit_date == "2024-07-03T13:00:00"


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


class TestEntrySkipReasons:
    """An entry signal that never becomes a fill must say why."""

    def test_entries_that_fire_while_held_are_counted(self) -> None:
        """Repeat signals during an open position are counted, not discarded."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 100.0, 100.0, 100.0],
            entries=[True, True, True, False, False],
            exits=[False, False, False, False, False],
        )
        skipped: Counter[str] = Counter()

        _, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
            skipped=skipped,
        )

        assert dict(skipped) == {"in_position": 2}
        assert len(trades) == 1
        assert trades[0].exit_reason == "end_of_backtest"

    def test_no_entry_after_skips_are_counted(self) -> None:
        """Signals blocked by the late-session cutoff are counted, not silent."""
        df = _make_intraday_df(
            prices=[100.0, 100.0, 100.0, 100.0],
            entries=[True, True, False, False],
            timestamps=[
                datetime(2024, 1, 2, 15, 25),
                datetime(2024, 1, 2, 15, 35),
                datetime(2024, 1, 2, 15, 40),
                datetime(2024, 1, 2, 15, 45),
            ],
        )
        skipped: Counter[str] = Counter()

        _, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(no_entry_after="15:30"),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0, timeframe="5min"),
            skipped=skipped,
        )

        assert skipped["no_entry_after"] == 2
        assert trades == []


_ATR_STOP_PRICES: list[float] = [50.0, 50.0, 52.0, 54.0]
_ATR_STOP_HIGHS: list[float] = [51.0, 51.0, 53.0, 55.0]
_ATR_STOP_LOWS: list[float] = [49.0, 49.0, 44.0, 53.0]


class TestAtrStop:
    """A stop placed in the asset's own volatility units."""

    def test_stop_is_frozen_from_the_signal_bars_atr(self) -> None:
        """The stop uses the ATR that was readable when the signal fired."""
        df = _make_signal_df(
            prices=_ATR_STOP_PRICES,
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            highs=_ATR_STOP_HIGHS,
            lows=_ATR_STOP_LOWS,
            atr=[2.5, 3.0, 3.5, 3.5],
        )

        equity_df, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(atr_indicator="atr", stop_atr_multiple=2.0),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        # 50 - 2 * 2.5 from the signal bar; the entry bar's ATR would give 44.0,
        # which the same bar's low also pierces.
        assert trades[0].exit_reason == "stop_loss"
        assert trades[0].entry_date == "2023-01-03"
        assert trades[0].exit_date == "2023-01-04"
        assert trades[0].exit_price == 45.0
        assert trades[0].return_pct == pytest.approx(-10.0)
        assert equity_df["equity"][-1] == pytest.approx(90_000.0)

    def test_undefined_atr_skips_the_entry(self) -> None:
        """No stop distance means no position, booked before any cost."""
        df = _make_signal_df(
            prices=_ATR_STOP_PRICES,
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            highs=_ATR_STOP_HIGHS,
            lows=_ATR_STOP_LOWS,
            atr=[None, 3.0, 3.5, 3.5],
        )
        skipped: Counter[str] = Counter()

        equity_df, trades = run_backtest(
            df,
            PositionSizing(max_position_pct=100.0),
            RiskManagement(atr_indicator="atr", stop_atr_multiple=2.0),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
            skipped=skipped,
        )

        assert trades == []
        assert dict(skipped) == {"atr_undefined": 1}
        assert equity_df["equity"].to_list() == [100_000.0] * 4

    def test_missing_atr_column_raises(self) -> None:
        """A silently dropped ATR column must fail the run, not the stop."""
        df = _make_signal_df(
            prices=_ATR_STOP_PRICES,
            entries=[True, False, False, False],
            exits=[False, False, False, False],
        )

        with pytest.raises(ValueError, match="atr_14"):
            run_backtest(
                df,
                PositionSizing(max_position_pct=100.0),
                RiskManagement(atr_indicator="atr_14", stop_atr_multiple=2.0),
                BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
            )


_ATR_RISK_PRICES: list[float] = [50.0, 50.0, 52.0, 54.0]


def _atr_risk_df() -> pl.DataFrame:
    """Entry on bar 1 at 50, signal exit on bar 3 at 54, ATR 2.5 on the signal bar."""
    return _make_signal_df(
        prices=_ATR_RISK_PRICES,
        entries=[True, False, False, False],
        exits=[False, False, True, False],
        atr=[2.5, 3.0, 3.5, 3.5],
    )


class TestAtrRiskSizing:
    """Shares come from the loss the ATR stop would realize, not from notional."""

    def test_shares_come_from_the_signal_bars_atr(self) -> None:
        """1% of 100k over a 2 x 2.5 stop distance buys 200 shares."""
        equity_df, trades = run_backtest(
            _atr_risk_df(),
            PositionSizing(method="atr_risk", risk_pct=1.0, max_position_pct=20.0),
            RiskManagement(atr_indicator="atr", stop_atr_multiple=2.0),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].entry_price == 50.0
        assert trades[0].exit_price == 54.0
        assert trades[0].exit_reason == "signal"
        assert trades[0].pnl == pytest.approx(800.0)
        assert equity_df["equity"].to_list() == pytest.approx(
            [100_000.0, 100_000.0, 100_400.0, 100_800.0]
        )

    def test_max_position_pct_caps_the_share_count(self) -> None:
        """The exposure cap binds below the risk budget's 200 shares."""
        _, trades = run_backtest(
            _atr_risk_df(),
            PositionSizing(method="atr_risk", risk_pct=1.0, max_position_pct=5.0),
            RiskManagement(atr_indicator="atr", stop_atr_multiple=2.0),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].pnl == pytest.approx(400.0)

    def test_zero_shares_skips_and_books_nothing(self) -> None:
        """A budget smaller than one share's risk is a skip, not a rounded-up fill."""
        skipped: Counter[str] = Counter()

        equity_df, trades = run_backtest(
            _atr_risk_df(),
            PositionSizing(method="atr_risk", risk_pct=0.001, max_position_pct=20.0),
            RiskManagement(atr_indicator="atr", stop_atr_multiple=2.0),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
            skipped=skipped,
        )

        assert trades == []
        assert dict(skipped) == {"zero_shares": 1}
        assert equity_df["equity"].to_list() == [100_000.0] * 4


_TRAIL_OPENS: list[float] = [100.0, 100.0, 100.0, 110.0]
_TRAIL_HIGHS: list[float] = [100.0, 100.0, 120.0, 111.0]
_TRAIL_LOWS: list[float] = [100.0, 100.0, 95.0, 105.0]
_TRAIL_CLOSES: list[float] = [100.0, 100.0, 118.0, 108.0]


def _trailing_df(
    opens: list[float] | None = None,
    lows: list[float] | None = None,
    atr: list[float | None] | None = None,
) -> pl.DataFrame:
    """Entry on bar 1 at 100; bar 2 prints the high that ratchets the trail."""
    return _make_signal_df(
        prices=_TRAIL_CLOSES,
        entries=[True, False, False, False],
        exits=[False, False, False, False],
        highs=_TRAIL_HIGHS,
        lows=lows or _TRAIL_LOWS,
        atr=atr,
        opens=opens or _TRAIL_OPENS,
    )


class TestTrailingStop:
    """A stop that ratchets up with the highest high the position has survived."""

    def test_trail_uses_the_previous_bars_high_not_the_current_one(self) -> None:
        """Bar 2's own high cannot tighten the stop bar 2 is checked against."""
        equity_df, trades = run_backtest(
            _trailing_df(),
            PositionSizing(max_position_pct=100.0),
            RiskManagement(trailing_stop_pct=10.0),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        # Bar 2 (low 95) survives a trail still set at 90 from bar 1's high.
        assert trades[0].exit_date == "2023-01-05"
        assert trades[0].exit_reason == "trailing_stop"
        assert trades[0].exit_price == pytest.approx(108.0)
        assert trades[0].return_pct == pytest.approx(8.0)
        assert equity_df["equity"].to_list() == pytest.approx(
            [100_000.0, 100_000.0, 118_000.0, 108_000.0]
        )

    def test_gap_through_fills_at_the_open(self) -> None:
        """An open below the trail is the fill, not the level it skipped."""
        _, trades = run_backtest(
            _trailing_df(opens=[100.0, 100.0, 100.0, 100.0]),
            PositionSizing(max_position_pct=100.0),
            RiskManagement(trailing_stop_pct=10.0),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].exit_reason == "trailing_stop"
        assert trades[0].exit_price == pytest.approx(100.0)

    def test_atr_distance_ratchets_from_the_completed_bar(self) -> None:
        """The ATR variant trails by a multiple of the bar that just closed."""
        _, trades = run_backtest(
            _trailing_df(atr=[4.0, 4.0, 4.0, 4.0]),
            PositionSizing(max_position_pct=100.0),
            RiskManagement(atr_indicator="atr", trailing_stop_atr_multiple=2.0),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        # 120 - 2 * 4 = 112 after bar 2; bar 3's low 105 pierces it.
        assert trades[0].exit_date == "2023-01-05"
        assert trades[0].exit_reason == "trailing_stop"
        assert trades[0].exit_price == pytest.approx(110.0)

    def test_equal_levels_are_labelled_stop_loss(self) -> None:
        """A tie belongs to the fixed stop: the trail added nothing."""
        _, trades = run_backtest(
            _trailing_df(lows=[100.0, 90.0, 95.0, 105.0], atr=[4.0, 4.0, 4.0, 4.0]),
            PositionSizing(max_position_pct=100.0),
            RiskManagement(
                atr_indicator="atr",
                stop_atr_multiple=2.0,
                trailing_stop_atr_multiple=2.0,
            ),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].exit_date == "2023-01-03"
        assert trades[0].exit_reason == "stop_loss"
        assert trades[0].exit_price == pytest.approx(92.0)

    def test_compute_exit_fill_treats_trailing_like_a_stop(self) -> None:
        """The trail fills at its level, or at a worse open."""
        at_level = compute_exit_fill(
            reason="trailing_stop",
            open_price=110.0,
            close_price=108.0,
            stop_level=108.0,
            tp_level=None,
            slippage_pct=0.0,
        )
        gapped = compute_exit_fill(
            reason="trailing_stop",
            open_price=100.0,
            close_price=108.0,
            stop_level=108.0,
            tp_level=None,
            slippage_pct=0.0,
        )

        assert at_level == pytest.approx(108.0)
        assert gapped == pytest.approx(100.0)


_TIME_STOP_PRICES: list[float] = [100.0, 100.0, 103.0, 110.0, 120.0]


def _time_stop_df(
    entries: list[bool] | None = None,
    exits: list[bool] | None = None,
    lows: list[float] | None = None,
) -> pl.DataFrame:
    """Build the shared time-stop fixture: open == close, entry fills on bar 1."""
    return _make_signal_df(
        prices=_TIME_STOP_PRICES,
        entries=entries or [True, False, False, False, False],
        exits=exits or [False, False, False, False, False],
        lows=lows,
        opens=_TIME_STOP_PRICES,
    )


class TestTimeStop:
    """A holding-period cap closes the position at the Nth held bar's close."""

    def test_exits_at_the_close_of_the_nth_held_bar(self) -> None:
        """The entry bar counts as the first bar held."""
        equity_df, trades = run_backtest(
            _time_stop_df(),
            PositionSizing(max_position_pct=100.0),
            RiskManagement(max_holding_bars=2),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].entry_date == "2023-01-03"
        assert trades[0].entry_price == pytest.approx(100.0)
        assert trades[0].exit_date == "2023-01-04"
        assert trades[0].exit_price == pytest.approx(103.0)
        assert trades[0].exit_reason == "time_stop"
        assert trades[0].holding_days == 1
        assert trades[0].return_pct == pytest.approx(3.0)
        assert equity_df["equity"][-1] == pytest.approx(103_000.0)

    def test_one_bar_limit_closes_on_the_entry_bar(self) -> None:
        """A one-bar cap is spent by the fill itself, so the entry bar closes it."""
        _, trades = run_backtest(
            _time_stop_df(),
            PositionSizing(max_position_pct=100.0),
            RiskManagement(max_holding_bars=1),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].entry_date == "2023-01-03"
        assert trades[0].exit_date == "2023-01-03"
        assert trades[0].exit_price == pytest.approx(100.0)
        assert trades[0].return_pct == pytest.approx(0.0)
        assert trades[0].exit_reason == "time_stop"

    def test_intrabar_stop_outranks_the_time_stop(self) -> None:
        """A level pierced inside the bar binds before that bar's close does."""
        _, trades = run_backtest(
            _time_stop_df(lows=[99.0, 99.0, 90.0, 109.0, 119.0]),
            PositionSizing(max_position_pct=100.0),
            RiskManagement(stop_loss_pct=5.0, max_holding_bars=2),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].exit_date == "2023-01-04"
        assert trades[0].exit_reason == "stop_loss"
        assert trades[0].exit_price == pytest.approx(95.0)

    def test_signal_exit_at_the_open_outranks_the_time_stop(self) -> None:
        """The open is settled before the close, so the signal keeps the label."""
        _, trades = run_backtest(
            _time_stop_df(exits=[False, True, False, False, False]),
            PositionSizing(max_position_pct=100.0),
            RiskManagement(max_holding_bars=2),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert len(trades) == 1
        assert trades[0].exit_date == "2023-01-04"
        assert trades[0].exit_reason == "signal"
        assert trades[0].exit_price == pytest.approx(103.0)


def _cooldown_df() -> pl.DataFrame:
    """Build the shared cooldown fixture: exit on bar 2, entries every bar after."""
    return _make_signal_df(
        prices=[100.0] * 7,
        entries=[True, False, True, True, True, False, False],
        exits=[False, True, False, False, False, False, False],
    )


class TestReentryCooldown:
    """A symbol is barred from re-entering for a fixed number of bars."""

    def test_entries_within_n_bars_of_an_exit_are_skipped(self) -> None:
        """The two bars after the exit bar are blocked; the third fills."""
        skipped: Counter[str] = Counter()

        _, trades = run_backtest(
            _cooldown_df(),
            PositionSizing(max_position_pct=100.0),
            RiskManagement(reentry_cooldown_bars=2),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
            skipped=skipped,
        )

        assert [(t.entry_date, t.exit_date, t.exit_reason) for t in trades] == [
            ("2023-01-03", "2023-01-04", "signal"),
            ("2023-01-07", "2023-01-08", "end_of_backtest"),
        ]
        assert dict(skipped) == {"cooldown": 2}

    def test_without_cooldown_the_engine_reenters_the_next_bar(self) -> None:
        """Control: the same frame re-enters on the bar after the exit."""
        _, trades = run_backtest(
            _cooldown_df(),
            PositionSizing(max_position_pct=100.0),
            RiskManagement(),
            BacktestConfig(slippage_pct=0.0, commission_pct=0.0),
        )

        assert [t.entry_date for t in trades] == ["2023-01-03", "2023-01-05"]
