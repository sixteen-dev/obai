"""Generic causality conformance: a future bar must not change an earlier decision.

Every result the engine produces for bars ``0..k-1`` must be identical whether
the frame it ran on ended at bar ``k`` or at bar ``n``. Bar ``k`` itself is
excluded from every comparison: a truncated run force-closes its open position
there (``end_of_backtest``), and ``is_last_bar_of_session`` peeks at bar ``k+1``
to decide the intraday session boundary, so the last bar of a prefix is
legitimately different from the same bar inside a longer run.

Within-bar information is out of scope. Volume-scaled slippage reads the full
bar's volume and the Corwin-Schultz spread window is already backward-looking;
both are documented ex-post modeling inputs, not leaks this suite polices.
"""

from __future__ import annotations

from typing import Any

import polars as pl
import pytest

from src.engine.backtester import BacktestConfig, Trade, run_backtest
from src.engine.indicators import compute_indicators
from src.engine.portfolio_backtester import (
    PortfolioBacktestResult,
    PortfolioTradeRecord,
    run_portfolio_backtest,
)
from src.engine.signals import generate_signals
from src.engine.utils import date_to_str
from src.models.strategy import (
    Condition,
    IndicatorConfig,
    Operand,
    PositionSizing,
    RiskManagement,
    RuleSet,
)
from tests.conformance_fixtures import random_walk_intraday, random_walk_ohlcv

CUT_POINTS: tuple[int, ...] = (30, 60, 90)
INTRADAY_CUT_POINTS: tuple[int, ...] = (13, 27, 45)
COMPARED_COLUMNS: tuple[str, ...] = ("fast", "slow", "rsi", "entry_signal", "exit_signal")

_INDICATORS: list[IndicatorConfig] = [
    IndicatorConfig(id="fast", type="SMA", params={"length": 5}),
    IndicatorConfig(id="slow", type="SMA", params={"length": 12}),
    IndicatorConfig(id="rsi", type="RSI", params={"length": 7}),
]
_ENTRY_RULES = RuleSet(
    logic="AND",
    conditions=[
        Condition(
            left=Operand(indicator="fast"),
            operator="crosses_above",
            right=Operand(indicator="slow"),
        )
    ],
)
_EXIT_RULES = RuleSet(
    logic="OR",
    conditions=[
        Condition(
            left=Operand(indicator="fast"),
            operator="crosses_below",
            right=Operand(indicator="slow"),
        ),
        Condition(
            left=Operand(indicator="rsi"),
            operator="greater_than",
            right=Operand(constant=75.0),
        ),
    ],
)


# Session-anchored columns: an opening range published only once its interval
# closes, and a VWAP accumulating from a date fixed in advance. Both read whole
# sessions, so a truncated frame is where a leak would show.
_SESSION_INDICATORS: list[IndicatorConfig] = [
    IndicatorConfig(id="orb", type="OPENING_RANGE", params={"minutes": 15}),
    IndicatorConfig(id="anchored", type="AVWAP", params={"anchor_date": "2024-01-04"}),
]
SESSION_COLUMNS: tuple[str, ...] = ("orb_high", "orb_low", "anchored")


def _pipeline(df: pl.DataFrame) -> pl.DataFrame:
    """Run the production enrich-then-signal pipeline over one OHLCV frame."""
    enriched, warnings = compute_indicators(df, _INDICATORS)
    assert warnings == []
    return generate_signals(enriched, _ENTRY_RULES, _EXIT_RULES)


def _session_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Enrich an intraday frame with the session-anchored indicator columns."""
    enriched, warnings = compute_indicators(df, _SESSION_INDICATORS, timeframe="5min")
    assert warnings == []
    return enriched


def _cutoff(df: pl.DataFrame, k: int) -> str:
    """Return the last timestamp a prefix run cut at ``k`` may settle a trade on."""
    return date_to_str(df["date"][k - 1])


def _run_single(signals: pl.DataFrame) -> tuple[pl.DataFrame, list[Trade]]:
    """Run the single-symbol engine with the suite's fixed configuration."""
    return run_backtest(
        signals,
        PositionSizing(max_position_pct=60.0),
        RiskManagement(stop_loss_pct=4.0, take_profit_pct=6.0),
        BacktestConfig(symbol="X", slippage_pct=0.1, commission_pct=0.1, initial_capital=10_000.0),
    )


def _run_intraday(signals: pl.DataFrame) -> tuple[pl.DataFrame, list[Trade]]:
    """Run the single-symbol engine in 5-minute close-at-session-end mode."""
    return run_backtest(
        signals,
        PositionSizing(max_position_pct=100.0),
        RiskManagement(stop_loss_pct=1.0, close_eod=True),
        BacktestConfig(
            symbol="X",
            slippage_pct=0.1,
            commission_pct=0.1,
            timeframe="5min",
            initial_capital=10_000.0,
        ),
    )


def _run_portfolio(frames: dict[str, pl.DataFrame]) -> PortfolioBacktestResult:
    """Run the shared-capital engine with the suite's fixed configuration."""
    return run_portfolio_backtest(
        signal_dfs=frames,
        initial_capital=10_000.0,
        position_sizing=PositionSizing(
            method="equal_weight",
            max_position_pct=50.0,
            max_positions=2,
            allocation_mode="portfolio",
        ),
        slippage_pct=0.1,
        commission_pct=0.1,
        stop_loss_pct=4.0,
        take_profit_pct=6.0,
    )


def _settled(trades: list[Any], cutoff: str) -> list[Any]:
    """Return the trades that had already closed at or before ``cutoff``."""
    return [trade for trade in trades if trade.exit_date <= cutoff]


def _trade_key(trade: Trade) -> tuple[Any, ...]:
    """Reduce a single-symbol trade to the fields a prefix run must reproduce."""
    return (
        trade.entry_date,
        trade.exit_date,
        round(trade.entry_price, 9),
        round(trade.exit_price, 9),
        trade.exit_reason,
        None if trade.pnl is None else round(trade.pnl, 6),
    )


def _portfolio_trade_key(trade: PortfolioTradeRecord) -> tuple[Any, ...]:
    """Reduce a portfolio trade to the fields a prefix run must reproduce."""
    return (
        trade.symbol,
        trade.entry_date,
        trade.exit_date,
        trade.shares,
        round(trade.exit_price, 9),
        trade.exit_reason,
        round(trade.pnl, 6),
    )


def _assert_float_column_matches(name: str, left: list[Any], right: list[Any]) -> None:
    """Assert two float columns hold nulls in the same places and equal values."""
    assert [value is None for value in left] == [value is None for value in right], name
    defined = [(a, b) for a, b in zip(left, right, strict=True) if a is not None]
    assert [a for a, _ in defined] == pytest.approx([b for _, b in defined], abs=1e-9), name


def _assert_columns_are_prefix_invariant(prefix: pl.DataFrame, full_head: pl.DataFrame) -> None:
    """Assert every compared indicator and signal column agrees bar for bar."""
    for column in COMPARED_COLUMNS:
        left, right = prefix[column].to_list(), full_head[column].to_list()
        if prefix.schema[column] == pl.Boolean:
            assert left == right, column
        else:
            _assert_float_column_matches(column, left, right)


def _assert_equity_prefix_matches(prefix: pl.Series, full: pl.Series, k: int) -> None:
    """Assert the first ``k`` equity points agree; bar ``k`` is excluded by design."""
    assert prefix[:k].to_list() == pytest.approx(full[:k].to_list(), abs=1e-9)


class TestPrefixInvariance:
    """Truncating the future must not move any decision already taken."""

    def test_indicator_and_signal_columns_are_prefix_invariant(self) -> None:
        """Indicators and rule evaluation must not read forward."""
        df = random_walk_ohlcv(0)
        full = _pipeline(df)

        for k in CUT_POINTS:
            prefix = _pipeline(df.head(k + 1))
            _assert_columns_are_prefix_invariant(prefix, full.head(k + 1))

    def test_single_symbol_equity_and_trades_are_prefix_invariant(self) -> None:
        """The single-symbol ledger for bars before the cut must be unchanged."""
        df = random_walk_ohlcv(0)
        equity_full, trades_full = _run_single(_pipeline(df))

        assert [trade.exit_date for trade in trades_full] == [
            "2024-02-20",
            "2024-02-29",
            "2024-03-07",
            "2024-03-24",
        ]
        assert {trade.exit_reason for trade in trades_full} == {"signal"}
        assert equity_full["equity"][59] == pytest.approx(9891.852499946257, abs=1e-6)
        assert equity_full["equity"][89] == pytest.approx(10004.283959843453, abs=1e-6)
        assert len(_settled(trades_full, _cutoff(df, 90))) == 4

        for k in CUT_POINTS:
            equity_prefix, trades_prefix = _run_single(_pipeline(df.head(k + 1)))
            cutoff = _cutoff(df, k)
            _assert_equity_prefix_matches(equity_prefix["equity"], equity_full["equity"], k)
            assert [_trade_key(t) for t in _settled(trades_prefix, cutoff)] == [
                _trade_key(t) for t in _settled(trades_full, cutoff)
            ]

    def test_portfolio_equity_and_trades_are_prefix_invariant(self) -> None:
        """Shared capital must not be allocated on knowledge of later bars."""
        frames = {"A": random_walk_ohlcv(0), "B": random_walk_ohlcv(100)}
        signals = {symbol: _pipeline(df) for symbol, df in frames.items()}
        full = _run_portfolio(signals)

        assert len(full.trades) == 8
        assert {trade.exit_reason for trade in full.trades} == {
            "signal",
            "stop_loss",
            "take_profit",
        }
        assert full.equity_curve[89] == pytest.approx(9699.285533357965, abs=1e-6)
        assert len(_settled(full.trades, _cutoff(frames["A"], 90))) == 7

        for k in CUT_POINTS:
            prefix_signals = {symbol: _pipeline(df.head(k + 1)) for symbol, df in frames.items()}
            prefix = _run_portfolio(prefix_signals)
            cutoff = _cutoff(frames["A"], k)
            assert prefix.equity_curve[:k] == pytest.approx(full.equity_curve[:k], abs=1e-9)
            assert [_portfolio_trade_key(t) for t in _settled(prefix.trades, cutoff)] == [
                _portfolio_trade_key(t) for t in _settled(full.trades, cutoff)
            ]

    def test_session_anchored_columns_are_prefix_invariant(self) -> None:
        """A session-wide aggregate must publish only what the bar could know.

        Both columns aggregate over a whole session or from a fixed date, so
        the guard against reading forward is the availability rule rather than
        the window: the opening range is null until its interval has closed,
        and the anchored average is null before its anchor.
        """
        df = random_walk_intraday(0)
        full = _session_columns(df)

        # Three of every session's ten bars fall inside the opening interval,
        # and the anchor falls on the third of the six sessions.
        assert full["orb_high"].null_count() == 18
        assert full["anchored"].null_count() == 20

        for k in INTRADAY_CUT_POINTS:
            prefix = _session_columns(df.head(k + 1))
            head = full.head(k + 1)
            for column in SESSION_COLUMNS:
                _assert_float_column_matches(
                    column, prefix[column].to_list(), head[column].to_list()
                )

    def test_intraday_close_eod_prefix_invariance_survives_a_mid_session_cut(self) -> None:
        """Session handling must stay causal when the frame ends mid-session.

        Every cut point falls inside a session, which is the hard case:
        ``is_last_bar_of_session`` decides on the next bar's date, so a prefix
        ending mid-session force-closes at its final bar. That final bar is
        outside the comparison; everything before it must still match.
        """
        df = random_walk_intraday(0)
        equity_full, trades_full = _run_intraday(df)

        assert len(trades_full) == 6
        assert {trade.exit_reason for trade in trades_full} == {
            "signal",
            "eod_close",
            "stop_loss",
        }
        assert equity_full["equity"][44] == pytest.approx(10149.638421134176, abs=1e-6)
        assert _cutoff(df, 45) == "2024-01-06T09:50:00"
        assert len(_settled(trades_full, _cutoff(df, 45))) == 4

        for k in INTRADAY_CUT_POINTS:
            equity_prefix, trades_prefix = _run_intraday(df.head(k + 1))
            cutoff = _cutoff(df, k)
            _assert_equity_prefix_matches(equity_prefix["equity"], equity_full["equity"], k)
            assert [_trade_key(t) for t in _settled(trades_prefix, cutoff)] == [
                _trade_key(t) for t in _settled(trades_full, cutoff)
            ]
