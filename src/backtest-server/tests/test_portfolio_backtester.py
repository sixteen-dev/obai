"""Tests for portfolio backtester with shared capital pool."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from src.engine.portfolio_backtester import run_portfolio_backtest
from src.models.strategy import PositionSizing, RiskManagement


def _make_signal_df(  # noqa: PLR0913
    prices: list[float],
    entries: list[bool],
    exits: list[bool],
    start_date: date | None = None,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[int] | None = None,
    opens: list[float] | None = None,
    dates: list[date] | None = None,
    atr: list[float | None] | None = None,
) -> pl.DataFrame:
    """Create a DataFrame with signals for testing."""
    n = len(prices)
    base = start_date or date(2023, 1, 2)
    bar_dates = dates or [date(base.year, base.month, base.day + i) for i in range(n)]
    df = pl.DataFrame(
        {
            "date": bar_dates,
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


class TestSingleSymbolBasic:
    """Test basic single-symbol portfolio backtest."""

    def test_entry_exit_produces_trade(self) -> None:
        """One entry and one exit should produce exactly one trade."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        assert len(result.trades) >= 1
        trade = result.trades[0]
        assert trade.symbol == "TEST"
        assert trade.shares > 0
        assert trade.pnl > 0  # Price went up 100 -> 110

    def test_equity_curve_length_matches_dates(self) -> None:
        """Equity curve should have same length as date union."""
        df = _make_signal_df(
            prices=[100.0, 101.0, 102.0, 103.0, 104.0],
            entries=[False, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
        )
        assert len(result.equity_curve) == 5
        assert len(result.daily_position_counts) == 5


class TestCashEquityInvariant:
    """Test that cash + positions = equity at every timestep."""

    def test_cash_plus_positions_equals_equity(self) -> None:
        """Verify the invariant holds: equity = cash + sum(shares * close)."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 108.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, True, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=50.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        # Before any trade: equity should be initial capital
        assert result.equity_curve[0] == pytest.approx(100_000.0)
        # At end (after all positions closed): all in cash
        assert result.equity_curve[-1] > 0


class TestExitFreesCashForEntry:
    """Test that exits process before entries within the same day."""

    def test_exit_frees_cash_for_entry_same_day(self) -> None:
        """Exit symbol A on day N, cash available for entry symbol B on day N."""
        # Symbol A: enter day 1, exit signal on day 2 (triggers exit on day 3)
        df_a = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        # Symbol B: entry signal on day 2 (triggers entry on day 3)
        df_b = _make_signal_df(
            prices=[50.0, 50.0, 52.0, 55.0, 55.0],
            entries=[False, False, True, False, False],
            exits=[False, False, False, False, False],
        )
        # Use max_positions=1 so only 1 can be held at a time
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=1,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"AAA": df_a, "BBB": df_b},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        # Should have 2 trades: one for AAA, one for BBB
        # If exits didn't process before entries, BBB would be skipped
        symbols_traded = {t.symbol for t in result.trades}
        assert "AAA" in symbols_traded
        # BBB entry fires on bar 2, processed on bar 3 (same bar AAA exits)
        assert "BBB" in symbols_traded


class TestSignalSkippedInsufficientCapital:
    """Test signal skipping when capital is exhausted."""

    def test_signals_skipped_insufficient_capital(self) -> None:
        """Three signals but cash for 1: 2 should be skipped."""
        # All three symbols fire entry on bar 0 (execute on bar 1)
        df_a = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        df_b = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        df_c = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=1,  # Only allow 1 position
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"AAA": df_a, "BBB": df_b, "CCC": df_c},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        # Only 1 position allowed, so 2 signals should be skipped
        assert len(result.signals_skipped) == 2
        # 1 trade entered (plus 1 end-of-backtest close)
        entered_symbols = {t.symbol for t in result.trades}
        assert len(entered_symbols) == 1


class TestMaxPositionsRespected:
    """Test that max_positions constraint is never violated."""

    def test_never_exceeds_max_positions(self) -> None:
        """Position count should never exceed max_positions."""
        # Three symbols with overlapping entry signals
        df_a = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False, False],
            exits=[False, False, False, False, True, False],
        )
        df_b = _make_signal_df(
            prices=[50.0, 50.0, 52.0, 55.0, 55.0, 55.0],
            entries=[True, False, False, False, False, False],
            exits=[False, False, False, False, True, False],
        )
        df_c = _make_signal_df(
            prices=[200.0, 200.0, 210.0, 220.0, 220.0, 220.0],
            entries=[True, False, False, False, False, False],
            exits=[False, False, False, False, True, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=50.0,
            max_positions=2,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"AAA": df_a, "BBB": df_b, "CCC": df_c},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        # Daily position counts should never exceed 2
        for count in result.daily_position_counts:
            assert count <= 2


class TestStopLossTriggers:
    """Test stop loss on discrete share positions."""

    def test_stop_loss_triggers(self) -> None:
        """Verify stop loss works on discrete share positions."""
        # Enter at ~100, price drops to 90 (10% loss)
        df = _make_signal_df(
            prices=[100.0, 100.0, 95.0, 90.0, 85.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
            lows=[99.0, 99.0, 93.0, 88.0, 83.0],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            stop_loss_pct=5.0,
        )
        # Should have a stop_loss exit
        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        assert len(stop_trades) >= 1
        assert stop_trades[0].pnl < 0

    def test_percent_stop_and_target_are_read_from_risk_management(self) -> None:
        """A caller passing only ``risk_management`` must not run unprotected."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 95.0, 90.0, 85.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
            lows=[99.0, 99.0, 93.0, 88.0, 83.0],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            risk_management=RiskManagement(stop_loss_pct=5.0),
        )
        assert [t.exit_reason for t in result.trades] == ["stop_loss"]
        assert result.trades[0].exit_price == pytest.approx(95.0)


class TestEntryBarStopBinds:
    """A stop pierced on the bar a lot opens must close that same bar."""

    def test_entry_bar_stop_binds(self) -> None:
        """Lot opened on a bar whose low pierces the stop exits that bar via stop."""
        # Entry signal on bar 0 -> fill at bar 1 open (100). Stop at 95.
        # Bar 1's low (94) pierces the stop on the entry bar itself.
        df = _make_signal_df(
            prices=[100.0, 100.0, 100.0, 100.0],
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            lows=[99.0, 94.0, 99.0, 99.0],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.0,
            stop_loss_pct=5.0,
        )

        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        assert len(stop_trades) == 1
        trade = stop_trades[0]
        # Closed on the entry bar itself, at the stop level not the close.
        assert trade.entry_date == trade.exit_date
        assert trade.exit_price == pytest.approx(95.0)
        assert trade.pnl < 0


class TestCloseRemainingAtEnd:
    """Test that open positions are closed at the final bar."""

    def test_close_remaining_at_end(self) -> None:
        """Open positions should be closed at the final bar."""
        # Enter but never get an exit signal
        df = _make_signal_df(
            prices=[100.0, 100.0, 105.0, 110.0, 115.0],
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
        )
        # Should have exactly 1 trade closed at end of backtest
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "end_of_backtest"
        # Final equity should be all cash (no positions)
        assert result.equity_curve[-1] > 0


class TestPortfolioCommissionApplied:
    """Test that commission is actually applied in portfolio mode."""

    def test_commission_reduces_portfolio_returns(self) -> None:
        """Portfolio with commission should have lower final equity than without."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        result_no_comm = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.0,
        )
        result_with_comm = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.5,
        )
        # Commission should reduce final equity
        assert result_with_comm.equity_curve[-1] < result_no_comm.equity_curve[-1]


class TestPortfolioVolumeScaledSlippage:
    """Test volume-scaled slippage in portfolio backtest."""

    def test_flags_off_matches_default(self) -> None:
        """Explicit flags off should produce same result as default."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        default = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.1,
        )
        flags_off = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.1,
            volume_scaled_slippage=False,
            spread_estimates=None,
        )
        assert default.equity_curve == flags_off.equity_curve

    def test_volume_scaling_changes_results(self) -> None:
        """Enabling volume scaling should change results vs flat."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 110.0, 110.0, 110.0],
            entries=[True, False, False, False, False],
            exits=[False, False, True, False, False],
            volumes=[50_000] * 5,  # Moderate volume
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )
        flat = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.1,
            volume_scaled_slippage=False,
        )
        scaled = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.1,
            volume_scaled_slippage=True,
        )
        # Results should differ when volume scaling is on
        assert flat.equity_curve != scaled.equity_curve


class TestCoFiringEntryAndExit:
    """A bar whose entry and exit both fire must not open and close in one bar."""

    def test_co_firing_signals_do_not_wash_the_position(self) -> None:
        """The prior bar's exit belongs to the position it closed, not the new one.

        Entries fill on the bar after the signal, so a lot opened here was
        opened BY the prior bar's entry. Re-reading that same prior bar's exit
        flag closed it immediately: a same-day round trip with zero holding
        days and two commissions charged, repeated for every bar the two
        signals co-fire.
        """
        df = _make_signal_df(
            prices=[100.0, 100.0, 100.0, 100.0, 100.0],
            entries=[True, True, True, False, False],
            exits=[True, True, True, False, False],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs={"AAA": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            commission_pct=0.1,
        )

        same_bar = [t for t in result.trades if t.entry_date == t.exit_date]
        assert same_bar == [], f"opened and closed on one bar: {same_bar}"

    def test_stop_loss_still_binds_on_the_entry_bar(self) -> None:
        """The entry-bar recheck exists for price levels; that must still work.

        A stop pierced by the entry bar's own low has to close that bar, or
        the backtest reports a loss the strategy would never have taken.
        """
        df = _make_signal_df(
            prices=[100.0, 100.0, 100.0],
            entries=[True, False, False],
            exits=[False, False, False],
            lows=[100.0, 80.0, 100.0],
            highs=[101.0, 101.0, 101.0],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=5,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs={"AAA": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            commission_pct=0.1,
            stop_loss_pct=5.0,
        )

        assert [t.exit_reason for t in result.trades] == ["stop_loss"]
        assert result.trades[0].entry_date == result.trades[0].exit_date


class TestIntrabarProceedsCannotFundOpeningEntry:
    """Cash released inside a bar is not available to that bar's opening orders."""

    def test_intraday_target_proceeds_do_not_fund_a_same_day_entry(self) -> None:
        """AAA's $110 target prints after the open BBB would have filled at.

        Running stops and targets in the opening phase handed those proceeds
        to BBB's opening purchase, buying 11 shares with money the portfolio
        did not hold when the order was placed — and with a slot that was
        still occupied at the open.
        """
        df_a = _make_signal_df(
            prices=[100.0, 100.0, 100.0, 100.0],
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            highs=[100.0, 100.0, 120.0, 100.0],
            lows=[100.0, 100.0, 100.0, 100.0],
        )
        df_b = _make_signal_df(
            prices=[100.0, 100.0, 100.0, 100.0],
            entries=[False, True, False, False],
            exits=[False, False, False, False],
            highs=[100.0, 100.0, 100.0, 100.0],
            lows=[100.0, 100.0, 100.0, 100.0],
        )
        sizing = PositionSizing(
            method="fixed_pct",
            max_position_pct=100.0,
            max_positions=1,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs={"AAA": df_a, "BBB": df_b},
            initial_capital=1000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.0,
            take_profit_pct=10.0,
        )

        assert [t.symbol for t in result.trades] == ["AAA"]
        assert result.trades[0].exit_reason == "take_profit"
        assert result.trades[0].exit_price == pytest.approx(110.0)
        assert [s["symbol"] for s in result.signals_skipped] == ["BBB"]


class TestOpeningAllocationMarksAtTheOpen:
    """Opening purchases are sized on information available at that open."""

    @pytest.mark.parametrize("later_close", [100.0, 20.0])
    def test_a_later_close_does_not_change_the_opening_share_count(
        self,
        later_close: float,
    ) -> None:
        """Every price through the open is identical; only AAA's close moves.

        Marking held positions at the close let a price printed hours after
        the fill shrink BBB's opening purchase from 5 shares to 3.
        """
        df_a = _make_signal_df(
            prices=[100.0, 100.0, later_close, 100.0],
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            highs=[100.0, 100.0, 100.0, 100.0],
            lows=[100.0, 100.0, min(100.0, later_close), 100.0],
            opens=[100.0, 100.0, 100.0, 100.0],
        )
        df_b = _make_signal_df(
            prices=[100.0, 100.0, 100.0, 100.0],
            entries=[False, True, False, False],
            exits=[False, False, False, False],
            highs=[100.0, 100.0, 100.0, 100.0],
            lows=[100.0, 100.0, 100.0, 100.0],
        )
        sizing = PositionSizing(
            method="fixed_pct",
            max_position_pct=50.0,
            max_positions=2,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs={"AAA": df_a, "BBB": df_b},
            initial_capital=1000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.0,
        )

        assert [t.shares for t in result.trades if t.symbol == "BBB"] == [5]


class TestMissingBarValuation:
    """A held symbol with no bar today keeps its last observed mark."""

    def test_a_missing_bar_does_not_revalue_the_lot_at_its_entry_price(self) -> None:
        """AAA has no Jan 5 bar, so its Jan 4 mark of $120 has to carry.

        Falling back to the entry price invented a $200 loss on the gap date
        and an equal recovery the next day, inflating volatility and drawdown.
        """
        df_a = _make_signal_df(
            prices=[100.0, 100.0, 120.0, 120.0],
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            dates=[date(2023, 1, 2), date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 6)],
        )
        df_b = _make_signal_df(
            prices=[100.0, 100.0, 100.0, 100.0, 100.0],
            entries=[False, False, False, False, False],
            exits=[False, False, False, False, False],
        )
        sizing = PositionSizing(
            method="fixed_pct",
            max_position_pct=100.0,
            max_positions=1,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs={"AAA": df_a, "BBB": df_b},
            initial_capital=1000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.0,
        )

        assert result.equity_curve == pytest.approx([1000.0, 1000.0, 1200.0, 1200.0, 1200.0])


class TestEntrySkipReasons:
    """A shared-capital run must say why a fired signal never filled."""

    def test_capital_and_in_position_skips_are_counted(self) -> None:
        """One slot: the loser is capital-blocked, the winner re-signals held."""
        frames = {
            sym: _make_signal_df(
                prices=[100.0, 100.0, 100.0],
                entries=[True, True, False],
                exits=[False, False, False],
            )
            for sym in ("A", "B")
        }
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=1,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs=frames,
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.0,
        )

        assert result.entries_skipped_by_reason == {"in_position": 1, "insufficient_capital": 2}


def _atr_stop_frame(atr: list[float | None]) -> pl.DataFrame:
    """Build the shared ATR-stop fixture: entry on bar 1, stop pierced on bar 2."""
    return _make_signal_df(
        prices=[50.0, 50.0, 52.0, 54.0],
        entries=[True, False, False, False],
        exits=[False, False, False, False],
        highs=[51.0, 51.0, 53.0, 55.0],
        lows=[49.0, 49.0, 44.0, 53.0],
        atr=atr,
    )


def _atr_stop_sizing() -> PositionSizing:
    """Position sizing for the ATR-stop fixture: one fully funded slot.

    ``equal_weight`` splits cash across the open slots, so a single fully
    funded position needs exactly one slot.
    """
    return PositionSizing(
        method="equal_weight",
        max_position_pct=100.0,
        max_positions=1,
        allocation_mode="portfolio",
    )


class TestAtrStop:
    """The lot carries the level it was opened with, not a percentage."""

    def test_lot_carries_the_frozen_atr_level(self) -> None:
        """The stop from the signal bar's ATR fills at its level."""
        result = run_portfolio_backtest(
            signal_dfs={"TEST": _atr_stop_frame([2.5, 3.0, 3.5, 3.5])},
            initial_capital=100_000.0,
            position_sizing=_atr_stop_sizing(),
            slippage_pct=0.0,
            commission_pct=0.0,
            risk_management=RiskManagement(atr_indicator="atr", stop_atr_multiple=2.0),
        )

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.shares == 2000
        assert trade.exit_reason == "stop_loss"
        assert trade.exit_price == pytest.approx(45.0)
        assert trade.pnl == pytest.approx(-10_000.0)
        assert result.equity_curve[-1] == pytest.approx(90_000.0)

    def test_undefined_atr_is_recorded_in_signals_skipped(self) -> None:
        """An unpriced stop distance blocks the fill and says so by symbol and date."""
        result = run_portfolio_backtest(
            signal_dfs={"TEST": _atr_stop_frame([None, 3.0, 3.5, 3.5])},
            initial_capital=100_000.0,
            position_sizing=_atr_stop_sizing(),
            slippage_pct=0.0,
            commission_pct=0.0,
            risk_management=RiskManagement(atr_indicator="atr", stop_atr_multiple=2.0),
        )

        assert result.trades == []
        assert result.entries_skipped_by_reason == {"atr_undefined": 1}
        assert result.signals_skipped == [
            {"symbol": "TEST", "date": "2023-01-03", "reason": "atr_undefined"}
        ]


def _atr_risk_frame() -> pl.DataFrame:
    """Entry on bar 1 at 50, signal exit on bar 3 at 54, ATR 2.5 on the signal bar."""
    return _make_signal_df(
        prices=[50.0, 50.0, 52.0, 54.0],
        entries=[True, False, False, False],
        exits=[False, False, True, False],
        atr=[2.5, 3.0, 3.5, 3.5],
    )


def _atr_risk_sizing(risk_pct: float, max_position_pct: float = 20.0) -> PositionSizing:
    """Portfolio sizing that budgets ``risk_pct`` of equity to the ATR stop."""
    return PositionSizing(
        method="atr_risk",
        risk_pct=risk_pct,
        max_position_pct=max_position_pct,
        max_positions=5,
        allocation_mode="portfolio",
    )


class TestAtrRiskSizing:
    """Shared capital buys whole shares sized by the budgeted loss."""

    def test_allocates_whole_shares_from_the_risk_budget(self) -> None:
        """1% of 100k over a 2 x 2.5 stop distance buys 200 shares."""
        result = run_portfolio_backtest(
            signal_dfs={"TEST": _atr_risk_frame()},
            initial_capital=100_000.0,
            position_sizing=_atr_risk_sizing(risk_pct=1.0),
            slippage_pct=0.0,
            commission_pct=0.0,
            risk_management=RiskManagement(atr_indicator="atr", stop_atr_multiple=2.0),
        )

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.shares == 200
        assert trade.exit_reason == "signal"
        assert trade.pnl == pytest.approx(800.0)
        assert result.equity_curve[-1] == pytest.approx(100_800.0)

    def test_zero_shares_is_named_not_insufficient_capital(self) -> None:
        """A budget too small to carry one share is not a cash shortage."""
        result = run_portfolio_backtest(
            signal_dfs={"TEST": _atr_risk_frame()},
            initial_capital=100_000.0,
            position_sizing=_atr_risk_sizing(risk_pct=0.001),
            slippage_pct=0.0,
            commission_pct=0.0,
            risk_management=RiskManagement(atr_indicator="atr", stop_atr_multiple=2.0),
        )

        assert result.trades == []
        assert result.entries_skipped_by_reason == {"zero_shares": 1}
        assert result.signals_skipped == [
            {"symbol": "TEST", "date": "2023-01-03", "reason": "zero_shares"}
        ]


class TestTrailingStop:
    """The lot's trail ratchets at the close and binds on the next bar."""

    def test_lot_trail_is_updated_at_the_close_and_checked_next_day(self) -> None:
        """Bar 2's high sets the level bar 3 is checked against."""
        df = _make_signal_df(
            prices=[100.0, 100.0, 118.0, 108.0],
            entries=[True, False, False, False],
            exits=[False, False, False, False],
            highs=[100.0, 100.0, 120.0, 111.0],
            lows=[100.0, 100.0, 95.0, 105.0],
            opens=[100.0, 100.0, 100.0, 110.0],
        )
        sizing = PositionSizing(
            method="equal_weight",
            max_position_pct=100.0,
            max_positions=1,
            allocation_mode="portfolio",
        )

        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=sizing,
            slippage_pct=0.0,
            commission_pct=0.0,
            risk_management=RiskManagement(trailing_stop_pct=10.0),
        )

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.shares == 1000
        assert trade.exit_date == "2023-01-05"
        assert trade.exit_reason == "trailing_stop"
        assert trade.exit_price == pytest.approx(108.0)
        assert trade.pnl == pytest.approx(8_000.0)


_TIME_STOP_PRICES: list[float] = [100.0, 100.0, 103.0, 110.0, 120.0]


def _one_slot_sizing() -> PositionSizing:
    """Position sizing for the time-stop fixtures: one fully funded slot."""
    return PositionSizing(
        method="equal_weight",
        max_position_pct=100.0,
        max_positions=1,
        allocation_mode="portfolio",
    )


class TestTimeStop:
    """A holding-period cap counted in the symbol's own bars, not calendar days."""

    def test_close_phase_exits_after_n_symbol_bars(self) -> None:
        """The lot opened on bar 1 is closed at bar 2's close."""
        df = _make_signal_df(
            prices=_TIME_STOP_PRICES,
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
            opens=_TIME_STOP_PRICES,
        )

        result = run_portfolio_backtest(
            signal_dfs={"TEST": df},
            initial_capital=100_000.0,
            position_sizing=_one_slot_sizing(),
            slippage_pct=0.0,
            commission_pct=0.0,
            risk_management=RiskManagement(max_holding_bars=2),
        )

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.shares == 1000
        assert trade.entry_date == "2023-01-03"
        assert trade.exit_date == "2023-01-04"
        assert trade.exit_price == pytest.approx(103.0)
        assert trade.exit_reason == "time_stop"
        assert trade.pnl == pytest.approx(3_000.0)
        assert result.equity_curve[-1] == pytest.approx(103_000.0)

    def test_missing_bar_does_not_advance_the_count(self) -> None:
        """A portfolio date the symbol never printed is not a bar it held."""
        held = _make_signal_df(
            prices=_TIME_STOP_PRICES,
            entries=[True, False, False, False, False],
            exits=[False, False, False, False, False],
            opens=_TIME_STOP_PRICES,
            dates=[
                date(2023, 1, 2),
                date(2023, 1, 3),
                date(2023, 1, 5),
                date(2023, 1, 6),
                date(2023, 1, 7),
            ],
        )
        quiet = _make_signal_df(
            prices=[50.0] * 6,
            entries=[False] * 6,
            exits=[False] * 6,
        )

        result = run_portfolio_backtest(
            signal_dfs={"TEST": held, "QUIET": quiet},
            initial_capital=100_000.0,
            position_sizing=_one_slot_sizing(),
            slippage_pct=0.0,
            commission_pct=0.0,
            risk_management=RiskManagement(max_holding_bars=2),
        )

        assert [t.exit_date for t in result.trades] == ["2023-01-05"]
        assert result.trades[0].exit_reason == "time_stop"


def _cooldown_df() -> pl.DataFrame:
    """Build the shared cooldown fixture: exit on bar 2, entries every bar."""
    return _make_signal_df(
        prices=[100.0] * 7,
        entries=[True, True, True, True, True, False, False],
        exits=[False, True, False, False, False, False, False],
    )


class TestReentryCooldown:
    """A symbol is barred from re-entering for a fixed number of its own bars."""

    def test_same_open_flip_is_blocked_and_recorded(self) -> None:
        """An exit and a re-entry at the same open are zero bars apart."""
        result = run_portfolio_backtest(
            signal_dfs={"TEST": _cooldown_df()},
            initial_capital=100_000.0,
            position_sizing=_one_slot_sizing(),
            slippage_pct=0.0,
            commission_pct=0.0,
            risk_management=RiskManagement(reentry_cooldown_bars=2),
        )

        assert [(t.entry_date, t.exit_date, t.exit_reason) for t in result.trades] == [
            ("2023-01-03", "2023-01-04", "signal"),
            ("2023-01-07", "2023-01-08", "end_of_backtest"),
        ]
        assert result.entries_skipped_by_reason == {"cooldown": 3}
        assert result.signals_skipped == [
            {"symbol": "TEST", "date": "2023-01-04", "reason": "cooldown"},
            {"symbol": "TEST", "date": "2023-01-05", "reason": "cooldown"},
            {"symbol": "TEST", "date": "2023-01-06", "reason": "cooldown"},
        ]

    def test_without_cooldown_the_same_open_flip_stands(self) -> None:
        """Control: with no cooldown the freed lot is bought back at that open."""
        result = run_portfolio_backtest(
            signal_dfs={"TEST": _cooldown_df()},
            initial_capital=100_000.0,
            position_sizing=_one_slot_sizing(),
            slippage_pct=0.0,
            commission_pct=0.0,
        )

        assert [t.entry_date for t in result.trades] == ["2023-01-03", "2023-01-04"]

    def test_cooldown_skips_do_not_gain_priority(self) -> None:
        """A blocked signal must not queue up a claim on the next free slot."""
        frames = {
            "ZZZ": _make_signal_df(
                prices=[100.0] * 7,
                entries=[True, False, True, True, True, False, False],
                exits=[False, True, False, False, False, False, False],
            ),
            "AAA": _make_signal_df(
                prices=[100.0] * 7,
                entries=[False, False, False, False, True, False, False],
                exits=[False] * 7,
            ),
        }

        result = run_portfolio_backtest(
            signal_dfs=frames,
            initial_capital=100_000.0,
            position_sizing=_one_slot_sizing(),
            slippage_pct=0.0,
            commission_pct=0.0,
            risk_management=RiskManagement(reentry_cooldown_bars=2),
        )

        # Both signals first fire on 2023-01-06 for a 2023-01-07 fill, so the
        # one slot goes to AAA on the alphabetical tiebreak. ZZZ would win it
        # if its blocked bars had registered as earlier signals.
        assert [(t.symbol, t.entry_date) for t in result.trades] == [
            ("ZZZ", "2023-01-03"),
            ("AAA", "2023-01-07"),
        ]
        assert result.entries_skipped_by_reason == {"cooldown": 2, "insufficient_capital": 1}
