"""Portfolio backtester with shared capital pool and discrete share tracking.

Simulates a multi-symbol portfolio where all symbols compete for the same
capital pool. Processes exits before entries each day so freed cash is
immediately available for new positions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
import polars as pl

from ..models.strategy import PositionSizing
from .accounting import PortfolioState, PositionLot, allocate_capital
from .backtester import (
    check_intrabar_stop,
    check_intrabar_tp,
    compute_entry_fill,
    compute_exit_fill,
)
from .utils import date_to_str


@dataclass
class PortfolioTradeRecord:
    """A single completed trade from the portfolio backtester.

    Attributes:
        symbol: Ticker symbol.
        entry_date: ISO date string of entry.
        entry_price: Fill price at entry.
        exit_date: ISO date string of exit.
        exit_price: Fill price at exit.
        shares: Number of shares traded.
        return_pct: Percentage return on the trade.
        pnl: Dollar profit/loss.
        exit_reason: Why the trade was closed.
        holding_days: Number of days held.

    """

    symbol: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    shares: int
    return_pct: float
    pnl: float
    exit_reason: str
    holding_days: int

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON output."""
        return {
            "symbol": self.symbol,
            "entry_date": self.entry_date,
            "entry_price": round(self.entry_price, 2),
            "exit_date": self.exit_date,
            "exit_price": round(self.exit_price, 2),
            "shares": self.shares,
            "return_pct": round(self.return_pct, 4),
            "pnl": round(self.pnl, 2),
            "exit_reason": self.exit_reason,
            "holding_days": self.holding_days,
        }


@dataclass
class PortfolioBacktestResult:
    """Results from a portfolio backtest run.

    Attributes:
        equity_curve: Daily total equity values.
        trades: Completed trade records.
        signals_skipped: Entries that fired but couldn't be filled.
        daily_position_counts: Number of held positions each day.

    """

    equity_curve: list[float]
    trades: list[PortfolioTradeRecord]
    signals_skipped: list[dict[str, Any]]
    daily_position_counts: list[int]


@dataclass
class _SymbolArrays:
    """Pre-extracted numpy arrays for a single symbol's data."""

    dates: list[Any]
    opens: np.ndarray[Any, np.dtype[np.float64]]
    highs: np.ndarray[Any, np.dtype[np.float64]]
    lows: np.ndarray[Any, np.dtype[np.float64]]
    closes: np.ndarray[Any, np.dtype[np.float64]]
    volumes: np.ndarray[Any, np.dtype[np.int64]]
    entries: np.ndarray[Any, np.dtype[Any]]
    exits: np.ndarray[Any, np.dtype[Any]]
    date_to_idx: dict[date, int]


def run_portfolio_backtest(  # noqa: PLR0913
    signal_dfs: dict[str, pl.DataFrame],
    initial_capital: float,
    position_sizing: PositionSizing,
    slippage_pct: float = 0.001,
    commission_pct: float = 0.1,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    close_eod: bool = False,
    timeframe: str = "daily",
    volume_scaled_slippage: bool = False,
    spread_estimates: dict[str, np.ndarray[Any, np.dtype[np.float64]]] | None = None,
) -> PortfolioBacktestResult:
    """Run a portfolio backtest with shared capital across multiple symbols.

    Day-by-day loop: mark-to-market, check exits (all before entries),
    collect entry signals, allocate capital, execute entries.

    Args:
        signal_dfs: Dict of symbol to DataFrame with entry_signal/exit_signal.
        initial_capital: Starting cash amount.
        position_sizing: Position sizing configuration.
        slippage_pct: Slippage percentage (e.g. 0.1 for 0.1%).
        commission_pct: Commission as percentage of trade value (e.g. 0.1 for 0.1%).
        stop_loss_pct: Optional stop-loss percentage.
        take_profit_pct: Optional take-profit percentage.
        close_eod: Whether to force close at end of day (unused for daily).
        timeframe: Bar timeframe (for holding period computation).
        volume_scaled_slippage: Scale slippage by participation rate.
        spread_estimates: Per-symbol spread estimate arrays (fraction of price).

    Returns:
        PortfolioBacktestResult with equity curve, trades, and diagnostics.

    """
    symbol_arrays = _prepare_symbol_arrays(signal_dfs)
    all_dates = _build_date_union(symbol_arrays)

    if not all_dates:
        return PortfolioBacktestResult([], [], [], [])

    state = PortfolioState(cash=float(initial_capital))
    trades: list[PortfolioTradeRecord] = []
    signals_skipped: list[dict[str, Any]] = []
    daily_counts: list[int] = []
    signal_fired_at: dict[str, int] = {}

    for day_idx, current_date in enumerate(all_dates):
        _process_day(
            day_idx=day_idx,
            current_date=current_date,
            all_dates=all_dates,
            symbol_arrays=symbol_arrays,
            state=state,
            trades=trades,
            signals_skipped=signals_skipped,
            signal_fired_at=signal_fired_at,
            position_sizing=position_sizing,
            slippage_pct=slippage_pct,
            commission_pct=commission_pct,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            volume_scaled_slippage=volume_scaled_slippage,
            spread_estimates=spread_estimates,
        )
        equity = _compute_equity_from_state(state, current_date, symbol_arrays)
        state.equity_history.append(float(equity))
        daily_counts.append(state.position_count)

    # Close remaining positions at final bar's close
    if all_dates:
        _close_remaining_positions(
            state=state,
            trades=trades,
            final_date=all_dates[-1],
            symbol_arrays=symbol_arrays,
            slippage_pct=slippage_pct,
            commission_pct=commission_pct,
            volume_scaled_slippage=volume_scaled_slippage,
            spread_estimates=spread_estimates,
        )
        # Recompute final equity after closing
        if state.equity_history:
            state.equity_history[-1] = float(state.cash)

    return PortfolioBacktestResult(
        equity_curve=state.equity_history,
        trades=trades,
        signals_skipped=signals_skipped,
        daily_position_counts=daily_counts,
    )


def _process_day(  # noqa: PLR0913
    day_idx: int,
    current_date: date,
    all_dates: list[date],
    symbol_arrays: dict[str, _SymbolArrays],
    state: PortfolioState,
    trades: list[PortfolioTradeRecord],
    signals_skipped: list[dict[str, Any]],
    signal_fired_at: dict[str, int],
    position_sizing: PositionSizing,
    slippage_pct: float,
    commission_pct: float,
    stop_loss_pct: float | None,
    take_profit_pct: float | None,
    volume_scaled_slippage: bool = False,
    spread_estimates: dict[str, np.ndarray[Any, np.dtype[np.float64]]] | None = None,
) -> None:
    """Process a single day: exits first, then entries.

    Args:
        day_idx: Index of current day in all_dates.
        current_date: The current date being processed.
        all_dates: Full sorted date list.
        symbol_arrays: Pre-extracted per-symbol arrays.
        state: Mutable portfolio state.
        trades: Trade record accumulator.
        signals_skipped: Skipped signal accumulator.
        signal_fired_at: Tracks when each symbol's signal first fired.
        position_sizing: Position sizing config.
        slippage_pct: Slippage percentage.
        commission_pct: Commission as percentage of trade value.
        stop_loss_pct: Stop-loss percentage.
        take_profit_pct: Take-profit percentage.
        volume_scaled_slippage: Scale slippage by participation rate.
        spread_estimates: Per-symbol spread estimate arrays.

    """
    # Step 1: Check exits for all held positions
    _check_all_exits(
        current_date=current_date,
        symbol_arrays=symbol_arrays,
        state=state,
        trades=trades,
        slippage_pct=slippage_pct,
        commission_pct=commission_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        volume_scaled_slippage=volume_scaled_slippage,
        spread_estimates=spread_estimates,
    )

    # Step 2: Collect entry signals and allocate capital
    _collect_and_execute_entries(
        day_idx=day_idx,
        current_date=current_date,
        all_dates=all_dates,
        symbol_arrays=symbol_arrays,
        state=state,
        signals_skipped=signals_skipped,
        signal_fired_at=signal_fired_at,
        position_sizing=position_sizing,
        slippage_pct=slippage_pct,
        commission_pct=commission_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        volume_scaled_slippage=volume_scaled_slippage,
        spread_estimates=spread_estimates,
    )

    # Step 3: Re-check exits so a stop/TP pierced on the entry bar binds that
    # same bar. Freshly-opened lots were not exit-checked in Step 1 (they did
    # not exist yet). Only newly-opened lots can close here, and for those
    # _check_position_exit considers price levels alone — the prior bar's exit
    # signal is what Step 1 already acted on.
    _check_all_exits(
        current_date=current_date,
        symbol_arrays=symbol_arrays,
        state=state,
        trades=trades,
        slippage_pct=slippage_pct,
        commission_pct=commission_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        volume_scaled_slippage=volume_scaled_slippage,
        spread_estimates=spread_estimates,
    )


def _check_all_exits(  # noqa: PLR0913
    current_date: date,
    symbol_arrays: dict[str, _SymbolArrays],
    state: PortfolioState,
    trades: list[PortfolioTradeRecord],
    slippage_pct: float,
    commission_pct: float,
    stop_loss_pct: float | None,
    take_profit_pct: float | None,
    volume_scaled_slippage: bool = False,
    spread_estimates: dict[str, np.ndarray[Any, np.dtype[np.float64]]] | None = None,
) -> None:
    """Check exit conditions for all held positions.

    Args:
        current_date: The current date.
        symbol_arrays: Pre-extracted per-symbol arrays.
        state: Mutable portfolio state.
        trades: Trade record accumulator.
        slippage_pct: Slippage percentage.
        commission_pct: Commission as percentage of trade value.
        stop_loss_pct: Stop-loss percentage.
        take_profit_pct: Take-profit percentage.
        volume_scaled_slippage: Scale slippage by participation rate.
        spread_estimates: Per-symbol spread estimate arrays.

    """
    to_close: list[str] = []

    for symbol, lot in state.positions.items():
        arrays = symbol_arrays.get(symbol)
        if arrays is None:
            continue
        bar_idx = arrays.date_to_idx.get(current_date)
        if bar_idx is None:
            continue

        exit_reason = _check_position_exit(
            lot=lot,
            arrays=arrays,
            bar_idx=bar_idx,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )
        if exit_reason:
            _close_position(
                state=state,
                trades=trades,
                symbol=symbol,
                lot=lot,
                arrays=arrays,
                bar_idx=bar_idx,
                exit_reason=exit_reason,
                slippage_pct=slippage_pct,
                commission_pct=commission_pct,
                volume_scaled_slippage=volume_scaled_slippage,
                spread_estimates=spread_estimates,
            )
            to_close.append(symbol)

    for symbol in to_close:
        del state.positions[symbol]


def _check_position_exit(
    lot: PositionLot,
    arrays: _SymbolArrays,
    bar_idx: int,
    stop_loss_pct: float | None,
    take_profit_pct: float | None,
) -> str:
    """Check if a position should exit on this bar.

    Args:
        lot: The open position.
        arrays: Symbol's market data arrays.
        bar_idx: Index into the arrays for current bar.
        stop_loss_pct: Stop-loss percentage.
        take_profit_pct: Take-profit percentage.

    Returns:
        Exit reason string, or empty string if no exit.

    """
    sl_pct = stop_loss_pct or lot.stop_loss_pct
    tp_pct = take_profit_pct or lot.take_profit_pct

    if sl_pct:
        hit, _ = check_intrabar_stop(lot.entry_price, float(arrays.lows[bar_idx]), sl_pct)
        if hit:
            return "stop_loss"

    if tp_pct:
        hit, _ = check_intrabar_tp(lot.entry_price, float(arrays.highs[bar_idx]), tp_pct)
        if hit:
            return "take_profit"

    # Signal-based exit: check previous bar's exit signal. A lot opened on this
    # bar was opened BY that same prior bar, so its exit flag has already been
    # spent and reading it again would close the position on the bar it opened.
    # Price levels above still bind, which is what the entry-bar recheck is for.
    if arrays.date_to_idx.get(lot.entry_date) == bar_idx:
        return ""
    if bar_idx > 0 and arrays.exits[bar_idx - 1]:
        return "signal"

    return ""


def _close_position(  # noqa: PLR0913
    state: PortfolioState,
    trades: list[PortfolioTradeRecord],
    symbol: str,
    lot: PositionLot,
    arrays: _SymbolArrays,
    bar_idx: int,
    exit_reason: str,
    slippage_pct: float,
    commission_pct: float,
    volume_scaled_slippage: bool = False,
    spread_estimates: dict[str, np.ndarray[Any, np.dtype[np.float64]]] | None = None,
) -> None:
    """Close a position and record the trade.

    Args:
        state: Mutable portfolio state.
        trades: Trade record accumulator.
        symbol: Ticker symbol.
        lot: The open position to close.
        arrays: Symbol's market data arrays.
        bar_idx: Index into the arrays for current bar.
        exit_reason: Why the position is closing.
        slippage_pct: Slippage percentage.
        commission_pct: Commission as percentage of trade value.
        volume_scaled_slippage: Scale slippage by participation rate.
        spread_estimates: Per-symbol spread estimate arrays.

    """
    sl_pct = lot.stop_loss_pct
    tp_pct = lot.take_profit_pct
    stop_level = lot.entry_price * (1 - sl_pct / 100) if sl_pct else None
    tp_level = lot.entry_price * (1 + tp_pct / 100) if tp_pct else None

    order_shares: float | None = None
    bar_volume: int | None = None
    spread_cost = 0.0
    if volume_scaled_slippage:
        order_shares = float(lot.shares)
        bar_volume = int(arrays.volumes[bar_idx])
    if spread_estimates and symbol in spread_estimates:
        sv = spread_estimates[symbol]
        if bar_idx < len(sv) and not np.isnan(sv[bar_idx]):
            spread_cost = float(sv[bar_idx]) / 2

    exit_price = compute_exit_fill(
        reason=exit_reason,
        open_price=float(arrays.opens[bar_idx]),
        close_price=float(arrays.closes[bar_idx]),
        stop_level=stop_level,
        tp_level=tp_level,
        slippage_pct=slippage_pct,
        order_shares=order_shares,
        bar_volume=bar_volume,
        spread_cost=spread_cost,
    )

    proceeds = lot.shares * exit_price * (1 - commission_pct / 100)
    state.cash += proceeds

    pnl = proceeds - lot.cost_basis
    return_pct = (exit_price - lot.entry_price) / lot.entry_price * 100 - commission_pct * 2
    exit_date = arrays.dates[bar_idx]
    holding_days = _compute_holding_days(lot.entry_date, exit_date)

    trades.append(
        PortfolioTradeRecord(
            symbol=symbol,
            entry_date=date_to_str(lot.entry_date),
            entry_price=lot.entry_price,
            exit_date=date_to_str(exit_date),
            exit_price=exit_price,
            shares=lot.shares,
            return_pct=return_pct,
            pnl=pnl,
            exit_reason=exit_reason,
            holding_days=holding_days,
        )
    )


def _collect_and_execute_entries(  # noqa: PLR0913, PLR0912
    day_idx: int,
    current_date: date,
    all_dates: list[date],
    symbol_arrays: dict[str, _SymbolArrays],
    state: PortfolioState,
    signals_skipped: list[dict[str, Any]],
    signal_fired_at: dict[str, int],
    position_sizing: PositionSizing,
    slippage_pct: float,
    commission_pct: float,
    stop_loss_pct: float | None,
    take_profit_pct: float | None,
    volume_scaled_slippage: bool = False,
    spread_estimates: dict[str, np.ndarray[Any, np.dtype[np.float64]]] | None = None,
) -> None:
    """Collect entry signals and execute allocations.

    Signals on previous bar's close → trade on current bar's open.

    Args:
        day_idx: Index of current day in all_dates.
        current_date: The current date.
        all_dates: Full sorted date list.
        symbol_arrays: Pre-extracted per-symbol arrays.
        state: Mutable portfolio state.
        signals_skipped: Skipped signal accumulator.
        signal_fired_at: Tracks when each symbol's signal first fired.
        position_sizing: Position sizing config.
        slippage_pct: Slippage percentage.
        commission_pct: Commission as percentage of trade value.
        stop_loss_pct: Stop-loss percentage.
        take_profit_pct: Take-profit percentage.
        volume_scaled_slippage: Scale slippage by participation rate.
        spread_estimates: Per-symbol spread estimate arrays.

    """
    pending_signals: list[tuple[str, float, int]] = []

    for symbol, arrays in symbol_arrays.items():
        if symbol in state.positions:
            continue
        bar_idx = arrays.date_to_idx.get(current_date)
        if bar_idx is None or bar_idx == 0:
            continue
        # Entry signal from previous bar
        if arrays.entries[bar_idx - 1]:
            if symbol not in signal_fired_at:
                signal_fired_at[symbol] = day_idx
            fill_price = compute_entry_fill(float(arrays.opens[bar_idx]), slippage_pct)
            pending_signals.append((symbol, fill_price, signal_fired_at[symbol]))

    if not pending_signals:
        return

    total_equity = _compute_equity_from_state(state, current_date, symbol_arrays)
    allocations = allocate_capital(
        cash=state.cash,
        total_equity=total_equity,
        signals=pending_signals,
        method=position_sizing.method,
        max_position_pct=position_sizing.max_position_pct,
        max_positions=position_sizing.max_positions,
        current_position_count=state.position_count,
        commission_pct=commission_pct,
    )

    allocated_symbols = {a[0] for a in allocations}

    # Execute allocations
    for symbol, shares, _total_cost in allocations:
        arrays = symbol_arrays[symbol]
        bar_idx = arrays.date_to_idx[current_date]

        order_shares: float | None = None
        bar_volume: int | None = None
        spread_cost = 0.0
        if volume_scaled_slippage:
            order_shares = float(shares)
            bar_volume = int(arrays.volumes[bar_idx])
        if spread_estimates and symbol in spread_estimates:
            sv = spread_estimates[symbol]
            if bar_idx < len(sv) and not np.isnan(sv[bar_idx]):
                spread_cost = float(sv[bar_idx]) / 2

        fill_price = compute_entry_fill(
            float(arrays.opens[bar_idx]),
            slippage_pct,
            order_shares,
            bar_volume,
            spread_cost,
        )

        # Recompute cost from actual fill — volume scaling may change the price
        commission_mult = 1 + commission_pct / 100
        actual_cost = shares * fill_price * commission_mult

        # Guard: if adjusted fill exceeds available cash, reduce shares
        if actual_cost > state.cash and fill_price > 0:
            shares = int(state.cash / (fill_price * commission_mult))  # noqa: PLW2901
            if shares <= 0:
                continue
            actual_cost = shares * fill_price * commission_mult

        state.cash -= actual_cost
        state.positions[symbol] = PositionLot(
            symbol=symbol,
            shares=shares,
            entry_price=fill_price,
            entry_date=current_date,
            cost_basis=actual_cost,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
        )
        # Clear signal tracking once entered
        signal_fired_at.pop(symbol, None)

    # Record skipped signals
    for symbol, _price, _ in pending_signals:
        if symbol not in allocated_symbols:
            signals_skipped.append(
                {
                    "symbol": symbol,
                    "date": date_to_str(current_date),
                    "reason": "insufficient_capital",
                }
            )
            # Keep signal_fired_at for priority on next day


def _close_remaining_positions(  # noqa: PLR0913
    state: PortfolioState,
    trades: list[PortfolioTradeRecord],
    final_date: date,
    symbol_arrays: dict[str, _SymbolArrays],
    slippage_pct: float,
    commission_pct: float,
    volume_scaled_slippage: bool = False,
    spread_estimates: dict[str, np.ndarray[Any, np.dtype[np.float64]]] | None = None,
) -> None:
    """Close all remaining open positions at final bar's close.

    Args:
        state: Mutable portfolio state.
        trades: Trade record accumulator.
        final_date: The last date in the backtest.
        symbol_arrays: Pre-extracted per-symbol arrays.
        slippage_pct: Slippage percentage.
        commission_pct: Commission as percentage of trade value.
        volume_scaled_slippage: Scale slippage by participation rate.
        spread_estimates: Per-symbol spread estimate arrays.

    """
    to_close = list(state.positions.keys())
    for symbol in to_close:
        lot = state.positions[symbol]
        arrays = symbol_arrays.get(symbol)
        if arrays is None:
            continue
        bar_idx = arrays.date_to_idx.get(final_date)
        if bar_idx is None:
            # Use the last available bar for this symbol
            if arrays.dates:
                bar_idx = len(arrays.dates) - 1
            else:
                continue

        _close_position(
            state=state,
            trades=trades,
            symbol=symbol,
            lot=lot,
            arrays=arrays,
            bar_idx=bar_idx,
            exit_reason="end_of_backtest",
            slippage_pct=slippage_pct,
            commission_pct=commission_pct,
            volume_scaled_slippage=volume_scaled_slippage,
            spread_estimates=spread_estimates,
        )
    state.positions.clear()


# --- Data preparation helpers ---


def _prepare_symbol_arrays(
    signal_dfs: dict[str, pl.DataFrame],
) -> dict[str, _SymbolArrays]:
    """Convert Polars DataFrames to numpy arrays for fast iteration.

    Args:
        signal_dfs: Dict of symbol to signal DataFrames.

    Returns:
        Dict of symbol to pre-extracted arrays.

    """
    result: dict[str, _SymbolArrays] = {}
    for symbol, df in signal_dfs.items():
        dates_raw = df["date"].to_list()
        dates_as_dates = [_extract_date(d) for d in dates_raw]
        date_to_idx = {d: i for i, d in enumerate(dates_as_dates)}

        result[symbol] = _SymbolArrays(
            dates=dates_raw,
            opens=df["open"].to_numpy().astype(np.float64),
            highs=df["high"].to_numpy().astype(np.float64),
            lows=df["low"].to_numpy().astype(np.float64),
            closes=df["close"].to_numpy().astype(np.float64),
            volumes=df["volume"].to_numpy().astype(np.int64),
            entries=df["entry_signal"].to_numpy(),
            exits=df["exit_signal"].to_numpy(),
            date_to_idx=date_to_idx,
        )
    return result


def _build_date_union(
    symbol_arrays: dict[str, _SymbolArrays],
) -> list[date]:
    """Build a sorted union of all dates across symbols.

    Args:
        symbol_arrays: Pre-extracted per-symbol arrays.

    Returns:
        Sorted list of unique dates.

    """
    all_dates: set[date] = set()
    for arrays in symbol_arrays.values():
        all_dates.update(arrays.date_to_idx.keys())
    return sorted(all_dates)


def _compute_equity_from_state(
    state: PortfolioState,
    current_date: date,
    symbol_arrays: dict[str, _SymbolArrays],
) -> float:
    """Compute total equity from state.

    Args:
        state: Portfolio state.
        current_date: Date for close price lookup.
        symbol_arrays: Symbol data for close price lookup.

    Returns:
        Total portfolio equity.

    """
    equity = float(state.cash)
    for symbol, lot in state.positions.items():
        arrays = symbol_arrays.get(symbol)
        if arrays is None:
            equity += lot.shares * lot.entry_price
            continue
        bar_idx = arrays.date_to_idx.get(current_date)
        if bar_idx is not None:
            equity += lot.shares * float(arrays.closes[bar_idx])
        else:
            equity += lot.shares * lot.entry_price
    return equity


def _extract_date(val: Any) -> date:
    """Extract a date from a date or datetime value.

    Args:
        val: Date or datetime value.

    Returns:
        Extracted date.

    """
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    return date.min


def _compute_holding_days(entry_date: date, exit_date: Any) -> int:
    """Compute holding period in days.

    Args:
        entry_date: Entry date.
        exit_date: Exit date (date or datetime).

    Returns:
        Number of days held.

    """
    if isinstance(exit_date, datetime):
        exit_d = exit_date.date()
    elif isinstance(exit_date, date):
        exit_d = exit_date
    else:
        return 0
    return (exit_d - entry_date).days
