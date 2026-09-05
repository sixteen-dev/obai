"""Portfolio backtester with shared capital pool and discrete share tracking.

Simulates a multi-symbol portfolio where all symbols compete for the same
capital pool. Processes exits before entries each day so freed cash is
immediately available for new positions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Literal

import numpy as np
import polars as pl

from ..models.strategy import PositionSizing, RiskManagement
from .accounting import PortfolioState, PositionLot, allocate_capital
from .backtester import (
    atr_column,
    atr_value,
    check_atr_risk_inputs,
    check_intrabar_tp,
    compute_entry_fill,
    compute_exit_fill,
    needs_entry_atr,
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
        exit_reason: Why the trade was closed — ``signal``, ``stop_loss``,
            ``trailing_stop``, ``take_profit``, ``time_stop`` or
            ``end_of_backtest``.
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
        entries_skipped_by_reason: Count of unfilled entry signals per reason.

    """

    equity_curve: list[float]
    trades: list[PortfolioTradeRecord]
    signals_skipped: list[dict[str, Any]]
    daily_position_counts: list[int]
    entries_skipped_by_reason: dict[str, int] = field(default_factory=dict)


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
    # ATR in price units, present only when a risk rule reads it.
    atr: np.ndarray[Any, np.dtype[np.float64]] | None = None


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
    risk_management: RiskManagement | None = None,
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
        risk_management: Risk rules the lots are opened under. Defaults to no
            rules; ``stop_loss_pct`` and ``take_profit_pct`` override its
            percent stop and target only when given.

    Returns:
        PortfolioBacktestResult with equity curve, trades, and diagnostics.

    """
    risk = _resolve_risk(risk_management, stop_loss_pct, take_profit_pct)
    check_atr_risk_inputs(position_sizing, risk)
    symbol_arrays = _prepare_symbol_arrays(signal_dfs, risk.atr_indicator)
    all_dates = _build_date_union(symbol_arrays)

    if not all_dates:
        return PortfolioBacktestResult([], [], [], [])

    state = PortfolioState(cash=float(initial_capital))
    trades: list[PortfolioTradeRecord] = []
    signals_skipped: list[dict[str, Any]] = []
    daily_counts: list[int] = []
    signal_fired_at: dict[str, int] = {}
    skipped: Counter[str] = Counter()

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
            skipped=skipped,
            risk=risk,
            position_sizing=position_sizing,
            slippage_pct=slippage_pct,
            commission_pct=commission_pct,
            stop_loss_pct=risk.stop_loss_pct,
            take_profit_pct=risk.take_profit_pct,
            volume_scaled_slippage=volume_scaled_slippage,
            spread_estimates=spread_estimates,
        )
        _mark_day_close(state, current_date, symbol_arrays, risk, daily_counts)

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
        entries_skipped_by_reason=dict(skipped),
    )


def _resolve_risk(
    risk_management: RiskManagement | None,
    stop_loss_pct: float | None,
    take_profit_pct: float | None,
) -> RiskManagement:
    """Merge the positional percent stop/target into one set of risk rules.

    Args:
        risk_management: Caller's risk rules, or None for no rules.
        stop_loss_pct: Positional percent stop; overrides the rules' value when given.
        take_profit_pct: Positional percent target; overrides likewise.

    Returns:
        Risk rules with the percent stop and target resolved.

    """
    risk = risk_management or RiskManagement()
    return replace(
        risk,
        stop_loss_pct=stop_loss_pct if stop_loss_pct is not None else risk.stop_loss_pct,
        take_profit_pct=take_profit_pct if take_profit_pct is not None else risk.take_profit_pct,
    )


def _mark_day_close(
    state: PortfolioState,
    current_date: date,
    symbol_arrays: dict[str, _SymbolArrays],
    risk: RiskManagement,
    daily_counts: list[int],
) -> None:
    """Ratchet trails, mark held lots at the close, and record the day's equity.

    Args:
        state: Portfolio state; equity history and lots are mutated in place.
        current_date: The day being closed.
        symbol_arrays: Symbol data for the price lookup.
        risk: Risk rules (trailing-stop settings).
        daily_counts: Per-day open-position counts, appended in place.

    """
    _update_trails(state, current_date, symbol_arrays, risk)
    _refresh_marks(state, current_date, symbol_arrays, mark="close")
    state.equity_history.append(float(_compute_equity_from_state(state)))
    daily_counts.append(state.position_count)


def _process_day(  # noqa: PLR0913
    day_idx: int,
    current_date: date,
    all_dates: list[date],
    symbol_arrays: dict[str, _SymbolArrays],
    state: PortfolioState,
    trades: list[PortfolioTradeRecord],
    signals_skipped: list[dict[str, Any]],
    signal_fired_at: dict[str, int],
    skipped: Counter[str],
    risk: RiskManagement,
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
        skipped: Counter of unfilled entry signals by reason.
        risk: Risk rules the lots are opened under.
        position_sizing: Position sizing config.
        slippage_pct: Slippage percentage.
        commission_pct: Commission as percentage of trade value.
        stop_loss_pct: Stop-loss percentage.
        take_profit_pct: Take-profit percentage.
        volume_scaled_slippage: Scale slippage by participation rate.
        spread_estimates: Per-symbol spread estimate arrays.

    """
    # Step 1: opening phase. An exit signal scheduled by the previous close
    # fills at this open, before anything the bar prints later.
    _check_all_exits(
        current_date=current_date,
        symbol_arrays=symbol_arrays,
        state=state,
        trades=trades,
        slippage_pct=slippage_pct,
        commission_pct=commission_pct,
        take_profit_pct=take_profit_pct,
        risk=risk,
        phase="open",
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
        skipped=skipped,
        risk=risk,
        position_sizing=position_sizing,
        slippage_pct=slippage_pct,
        commission_pct=commission_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        volume_scaled_slippage=volume_scaled_slippage,
        spread_estimates=spread_estimates,
    )

    # Steps 3 and 4: the rest of the bar, in the order it prints. Stops and
    # targets bind intrabar for every held lot, including the ones just
    # opened, and their proceeds land after this bar's opening orders — money
    # released inside the bar cannot fund a fill at its open. Whatever is
    # still held at the close is then closed by the holding cap. Signals are
    # not re-read here; Step 1 already spent them.
    late_phases: tuple[Literal["intrabar", "close"], ...] = ("intrabar", "close")
    for phase in late_phases:
        _check_all_exits(
            current_date=current_date,
            symbol_arrays=symbol_arrays,
            state=state,
            trades=trades,
            slippage_pct=slippage_pct,
            commission_pct=commission_pct,
            take_profit_pct=take_profit_pct,
            risk=risk,
            phase=phase,
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
    take_profit_pct: float | None,
    risk: RiskManagement,
    phase: Literal["open", "intrabar", "close"],
    volume_scaled_slippage: bool = False,
    spread_estimates: dict[str, np.ndarray[Any, np.dtype[np.float64]]] | None = None,
) -> None:
    """Check exit conditions for all held positions in one execution phase.

    Args:
        current_date: The current date.
        symbol_arrays: Pre-extracted per-symbol arrays.
        state: Mutable portfolio state.
        trades: Trade record accumulator.
        slippage_pct: Slippage percentage.
        commission_pct: Commission as percentage of trade value.
        take_profit_pct: Take-profit percentage.
        risk: Risk rules the lots are held under.
        phase: "open" for signal exits scheduled by the previous close,
            "intrabar" for stop and take-profit levels pierced inside the bar,
            "close" for the holding cap spent at the bar's close.
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
            take_profit_pct=take_profit_pct,
            risk=risk,
            phase=phase,
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


def _check_position_exit(  # noqa: PLR0913
    lot: PositionLot,
    arrays: _SymbolArrays,
    bar_idx: int,
    take_profit_pct: float | None,
    risk: RiskManagement,
    phase: Literal["open", "intrabar", "close"],
) -> str:
    """Check if a position should exit on this bar, in the given phase.

    Args:
        lot: The open position, carrying the stop level frozen at its fill.
        arrays: Symbol's market data arrays.
        bar_idx: Index into the arrays for current bar.
        take_profit_pct: Take-profit percentage.
        risk: Risk rules the lot is held under.
        phase: "open" checks the signal scheduled by the previous close;
            "intrabar" checks the stop and take-profit levels; "close" checks
            the holding cap against the symbol's own bar count.

    Returns:
        Exit reason string, or empty string if no exit.

    """
    if phase == "open":
        return _open_phase_exit(lot, arrays, bar_idx)
    if phase == "close":
        return _close_phase_exit(lot, arrays, bar_idx, risk)
    return _intrabar_phase_exit(lot, arrays, bar_idx, take_profit_pct)


def _open_phase_exit(lot: PositionLot, arrays: _SymbolArrays, bar_idx: int) -> str:
    """Return the signal exit the previous close scheduled for this open.

    A lot opened on this bar was opened BY that same prior bar, so its exit
    flag has already been spent and reading it again would close the position
    on the bar it opened. Step 1 runs before entries, so this only bites if
    the phases are ever reordered.

    Args:
        lot: The open position.
        arrays: Symbol's market data arrays.
        bar_idx: Index into the arrays for current bar.

    Returns:
        ``signal``, or an empty string when no exit was scheduled.

    """
    if arrays.date_to_idx.get(lot.entry_date) == bar_idx:
        return ""
    if bar_idx > 0 and arrays.exits[bar_idx - 1]:
        return "signal"
    return ""


def _close_phase_exit(
    lot: PositionLot,
    arrays: _SymbolArrays,
    bar_idx: int,
    risk: RiskManagement,
) -> str:
    """Return the holding-cap exit spent at this bar's close.

    Bars are counted in the symbol's own series, so a portfolio date it never
    printed does not spend the cap, and the entry bar is the first bar held.

    Args:
        lot: The open position.
        arrays: Symbol's market data arrays.
        bar_idx: Index into the arrays for current bar.
        risk: Risk rules the lot is held under.

    Returns:
        ``time_stop``, or an empty string when the cap has bars left.

    """
    bars_held = bar_idx - arrays.date_to_idx[lot.entry_date] + 1
    if risk.max_holding_bars and bars_held >= risk.max_holding_bars:
        return "time_stop"
    return ""


def _intrabar_phase_exit(
    lot: PositionLot,
    arrays: _SymbolArrays,
    bar_idx: int,
    take_profit_pct: float | None,
) -> str:
    """Return the stop or target level this bar pierced, stop winning ties.

    The frozen stop and the trail are one level: whichever sits higher is the
    one that binds, and the trail moved only at earlier closes.

    Args:
        lot: The open position, carrying the levels it was opened with.
        arrays: Symbol's market data arrays.
        bar_idx: Index into the arrays for current bar.
        take_profit_pct: Take-profit percentage.

    Returns:
        ``stop_loss``, ``trailing_stop`` or ``take_profit``, or an empty
        string when neither level was reached.

    """
    effective_stop = _effective_lot_stop(lot)
    if effective_stop is not None and float(arrays.lows[bar_idx]) <= effective_stop:
        return _lot_stop_reason(lot)

    tp_pct = take_profit_pct or lot.take_profit_pct
    if tp_pct:
        hit, _ = check_intrabar_tp(lot.entry_price, float(arrays.highs[bar_idx]), tp_pct)
        if hit:
            return "take_profit"

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
    tp_pct = lot.take_profit_pct
    stop_level = _effective_lot_stop(lot)
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
    state.last_exit_bar_idx[symbol] = bar_idx

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


def _lot_stop_level(
    fill_price: float,
    stop_loss_pct: float | None,
    stop_distance: float | None,
) -> float | None:
    """Return the stop level frozen at this fill.

    Args:
        fill_price: Entry fill price per share.
        stop_loss_pct: Percent stop, when one is configured.
        stop_distance: ATR stop distance in price units, when one was priced
            from the signal bar.

    Returns:
        The frozen level, or None when the lot carries no stop.

    """
    if stop_loss_pct:
        return fill_price * (1 - stop_loss_pct / 100)
    if stop_distance is not None:
        return fill_price - stop_distance
    return None


def _trail_distance(
    risk: RiskManagement,
    high_water_mark: float,
    atr_at: float | None,
) -> float | None:
    """Return how far below the high water mark a trailing stop sits.

    Args:
        risk: Risk rules the lot is held under.
        high_water_mark: Highest high the lot has printed, or its fill price
            when the trail is being placed.
        atr_at: ATR on the bar the distance is measured from, or None.

    Returns:
        The distance, or None when no trail is configured or the ATR variant
        has no value to read on that bar.

    """
    if risk.trailing_stop_pct is not None:
        return risk.trailing_stop_pct / 100 * high_water_mark
    if risk.trailing_stop_atr_multiple is not None and atr_at is not None:
        return risk.trailing_stop_atr_multiple * atr_at
    return None


def _effective_lot_stop(lot: PositionLot) -> float | None:
    """Return the highest stop level protecting a lot.

    Args:
        lot: The open position.

    Returns:
        The tighter of the frozen stop and the trail, or None when the lot
        carries neither.

    """
    levels = [level for level in (lot.stop_level, lot.trail_level) if level is not None]
    return max(levels) if levels else None


def _lot_stop_reason(lot: PositionLot) -> str:
    """Name which stop a lot's effective level belongs to.

    Args:
        lot: The open position.

    Returns:
        ``trailing_stop`` only when the trail sits strictly above the frozen
        stop; a tie belongs to the fixed stop, which the trail did not improve.

    """
    if lot.trail_level is not None and (lot.stop_level is None or lot.trail_level > lot.stop_level):
        return "trailing_stop"
    return "stop_loss"


def _update_trails(
    state: PortfolioState,
    current_date: date,
    symbol_arrays: dict[str, _SymbolArrays],
    risk: RiskManagement,
) -> None:
    """Ratchet every held lot's trail with the bar that just completed.

    Runs after the day's exits, so a high printed inside a bar can only
    tighten the level checked on the next one. The level never falls, a lot
    whose symbol has no bar that date is untouched, and an undefined ATR
    leaves the level where it was.

    Args:
        state: Portfolio state; held lots are mutated in place.
        current_date: Date of the bar that just completed.
        symbol_arrays: Pre-extracted per-symbol arrays.
        risk: Risk rules the lots are held under.

    """
    if risk.trailing_stop_pct is None and risk.trailing_stop_atr_multiple is None:
        return
    for symbol, lot in state.positions.items():
        arrays = symbol_arrays.get(symbol)
        bar_idx = arrays.date_to_idx.get(current_date) if arrays is not None else None
        if arrays is None or bar_idx is None:
            continue
        lot.high_water_mark = max(lot.high_water_mark, float(arrays.highs[bar_idx]))
        distance = _trail_distance(risk, lot.high_water_mark, atr_value(arrays.atr, bar_idx))
        if distance is None:
            continue
        candidate = lot.high_water_mark - distance
        if lot.trail_level is None or candidate > lot.trail_level:
            lot.trail_level = candidate


def _in_reentry_cooldown(
    risk: RiskManagement,
    last_exit_bar_idx: dict[str, int],
    symbol: str,
    bar_idx: int,
) -> bool:
    """Report whether this symbol's last exit is still too recent to re-enter.

    The distance is counted in the symbol's own bars, so a portfolio date it
    never printed does not shorten the wait. An exit at the open and a
    re-entry at that same open are zero bars apart, which any cooldown blocks.

    Args:
        risk: Risk rules the run is under.
        last_exit_bar_idx: Bar index of each symbol's most recent exit.
        symbol: Symbol the entry would fill in.
        bar_idx: Bar the entry would fill on.

    Returns:
        True when a cooldown is configured and has not elapsed.

    """
    if not risk.reentry_cooldown_bars:
        return False
    last_exit = last_exit_bar_idx.get(symbol)
    if last_exit is None:
        return False
    return bar_idx - last_exit <= risk.reentry_cooldown_bars


def _collect_pending_signals(  # noqa: PLR0913
    day_idx: int,
    current_date: date,
    symbol_arrays: dict[str, _SymbolArrays],
    state: PortfolioState,
    signals_skipped: list[dict[str, Any]],
    signal_fired_at: dict[str, int],
    skipped: Counter[str],
    risk: RiskManagement,
    sizing_method: str,
    slippage_pct: float,
) -> tuple[list[tuple[str, float, int]], dict[str, float], dict[str, float]]:
    """Collect the entries eligible to compete for capital on this bar's open.

    Signals on the previous bar's close are read here; the guards run in the
    order an unfilled signal should be attributed: already held, then still
    inside the re-entry cooldown, then an ATR that neither a stop nor a share
    count can be priced from.

    Args:
        day_idx: Index of current day in the date union.
        current_date: The current date.
        symbol_arrays: Pre-extracted per-symbol arrays.
        state: Portfolio state, read for the symbols already held.
        signals_skipped: Skipped signal accumulator.
        signal_fired_at: Tracks when each symbol's signal first fired.
        skipped: Counter of unfilled entry signals by reason.
        risk: Risk rules the lots would be opened under.
        sizing_method: Configured position sizing method.
        slippage_pct: Slippage percentage.

    Returns:
        Tuple of (pending ``(symbol, fill_price, first_fired_day)`` signals,
        per-symbol ATR stop distance in price units, per-symbol ATR printed on
        the signal bar).

    """
    pending_signals: list[tuple[str, float, int]] = []
    stop_distances: dict[str, float] = {}
    entry_atr: dict[str, float] = {}

    for symbol, arrays in symbol_arrays.items():
        bar_idx = arrays.date_to_idx.get(current_date)
        if bar_idx is None or bar_idx == 0:
            continue
        # Entry signal from previous bar
        if not arrays.entries[bar_idx - 1]:
            continue
        # A signal on a symbol already held is a decision this run made, so it
        # is counted rather than dropped: "fires but cannot be acted on" is a
        # different diagnosis from "never fires".
        if symbol in state.positions:
            skipped["in_position"] += 1
            continue
        # A blocked signal never competes for capital, so it also never
        # registers the earlier fired-at index that would jump the queue once
        # the cooldown elapses.
        if _in_reentry_cooldown(risk, state.last_exit_bar_idx, symbol, bar_idx):
            signals_skipped.append(
                {
                    "symbol": symbol,
                    "date": date_to_str(current_date),
                    "reason": "cooldown",
                }
            )
            skipped["cooldown"] += 1
            continue
        atr_prev = atr_value(arrays.atr, bar_idx - 1)
        needs_atr = needs_entry_atr(
            risk.stop_atr_multiple, risk.trailing_stop_atr_multiple, sizing_method
        )
        if needs_atr and atr_prev is None:
            signals_skipped.append(
                {
                    "symbol": symbol,
                    "date": date_to_str(current_date),
                    "reason": "atr_undefined",
                }
            )
            skipped["atr_undefined"] += 1
            continue
        if atr_prev is not None:
            entry_atr[symbol] = atr_prev
        if risk.stop_atr_multiple is not None and atr_prev is not None:
            stop_distances[symbol] = risk.stop_atr_multiple * atr_prev
        if symbol not in signal_fired_at:
            signal_fired_at[symbol] = day_idx
        fill_price = compute_entry_fill(float(arrays.opens[bar_idx]), slippage_pct)
        pending_signals.append((symbol, fill_price, signal_fired_at[symbol]))

    return pending_signals, stop_distances, entry_atr


def _collect_and_execute_entries(  # noqa: PLR0913, PLR0912
    day_idx: int,
    current_date: date,
    all_dates: list[date],
    symbol_arrays: dict[str, _SymbolArrays],
    state: PortfolioState,
    signals_skipped: list[dict[str, Any]],
    signal_fired_at: dict[str, int],
    skipped: Counter[str],
    risk: RiskManagement,
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
        skipped: Counter of unfilled entry signals by reason.
        risk: Risk rules the lots are opened under.
        position_sizing: Position sizing config.
        slippage_pct: Slippage percentage.
        commission_pct: Commission as percentage of trade value.
        stop_loss_pct: Stop-loss percentage.
        take_profit_pct: Take-profit percentage.
        volume_scaled_slippage: Scale slippage by participation rate.
        spread_estimates: Per-symbol spread estimate arrays.

    """
    pending_signals, stop_distances, entry_atr = _collect_pending_signals(
        day_idx=day_idx,
        current_date=current_date,
        symbol_arrays=symbol_arrays,
        state=state,
        signals_skipped=signals_skipped,
        signal_fired_at=signal_fired_at,
        skipped=skipped,
        risk=risk,
        sizing_method=position_sizing.method,
        slippage_pct=slippage_pct,
    )

    if not pending_signals:
        return

    # Size against equity marked at this open: a close printed later in the bar
    # is not known when these orders are placed.
    _refresh_marks(state, current_date, symbol_arrays, mark="open")
    total_equity = _compute_equity_from_state(state)
    risk_budget = _risk_budget(position_sizing, total_equity)
    allocations = allocate_capital(
        cash=state.cash,
        total_equity=total_equity,
        signals=pending_signals,
        method=position_sizing.method,
        max_position_pct=position_sizing.max_position_pct,
        max_positions=position_sizing.max_positions,
        current_position_count=state.position_count,
        commission_pct=commission_pct,
        risk_budget=risk_budget,
        stop_distances=stop_distances,
    )

    _execute_allocations(
        allocations=allocations,
        current_date=current_date,
        symbol_arrays=symbol_arrays,
        state=state,
        signal_fired_at=signal_fired_at,
        stop_distances=stop_distances,
        entry_atr=entry_atr,
        risk=risk,
        slippage_pct=slippage_pct,
        commission_pct=commission_pct,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        volume_scaled_slippage=volume_scaled_slippage,
        spread_estimates=spread_estimates,
    )

    _record_unallocated(
        pending_signals=pending_signals,
        allocated_symbols={a[0] for a in allocations},
        current_date=current_date,
        signals_skipped=signals_skipped,
        skipped=skipped,
        method=position_sizing.method,
        risk_budget=risk_budget,
        stop_distances=stop_distances,
    )


def _risk_budget(position_sizing: PositionSizing, total_equity: float) -> float | None:
    """Return the dollars of loss one ``atr_risk`` position may budget.

    Args:
        position_sizing: Sizing configuration for the run.
        total_equity: Equity marked at the open the orders are placed on.

    Returns:
        The budget, or None for a method that does not size to a stop.

    """
    if position_sizing.method != "atr_risk" or position_sizing.risk_pct is None:
        return None
    return total_equity * position_sizing.risk_pct / 100


def _execute_allocations(  # noqa: PLR0913
    allocations: list[tuple[str, int, float]],
    current_date: date,
    symbol_arrays: dict[str, _SymbolArrays],
    state: PortfolioState,
    signal_fired_at: dict[str, int],
    stop_distances: dict[str, float],
    entry_atr: dict[str, float],
    risk: RiskManagement,
    slippage_pct: float,
    commission_pct: float,
    stop_loss_pct: float | None,
    take_profit_pct: float | None,
    volume_scaled_slippage: bool = False,
    spread_estimates: dict[str, np.ndarray[Any, np.dtype[np.float64]]] | None = None,
) -> None:
    """Fill each allocation at this bar's open and open the lot it buys.

    Args:
        allocations: ``(symbol, shares, total_cost)`` triples to fill.
        current_date: The current date.
        symbol_arrays: Pre-extracted per-symbol arrays.
        state: Mutable portfolio state; cash and positions are mutated.
        signal_fired_at: Tracks when each symbol's signal first fired.
        stop_distances: Per-symbol ATR stop distance in price units.
        entry_atr: Per-symbol ATR printed on the signal bar.
        risk: Risk rules the lots are opened under.
        slippage_pct: Slippage percentage.
        commission_pct: Commission as percentage of trade value.
        stop_loss_pct: Stop-loss percentage.
        take_profit_pct: Take-profit percentage.
        volume_scaled_slippage: Scale slippage by participation rate.
        spread_estimates: Per-symbol spread estimate arrays.

    """
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

        trail_distance = _trail_distance(risk, fill_price, entry_atr.get(symbol))
        state.cash -= actual_cost
        state.positions[symbol] = PositionLot(
            symbol=symbol,
            shares=shares,
            entry_price=fill_price,
            entry_date=current_date,
            cost_basis=actual_cost,
            last_mark=fill_price,
            stop_level=_lot_stop_level(fill_price, stop_loss_pct, stop_distances.get(symbol)),
            take_profit_pct=take_profit_pct,
            trail_level=None if trail_distance is None else fill_price - trail_distance,
            high_water_mark=fill_price,
        )
        # Clear signal tracking once entered
        signal_fired_at.pop(symbol, None)


def _record_unallocated(  # noqa: PLR0913
    pending_signals: list[tuple[str, float, int]],
    allocated_symbols: set[str],
    current_date: date,
    signals_skipped: list[dict[str, Any]],
    skipped: Counter[str],
    method: str,
    risk_budget: float | None,
    stop_distances: dict[str, float],
) -> None:
    """Name why each fired signal that won no capital never filled.

    Args:
        pending_signals: The signals that competed for capital.
        allocated_symbols: Symbols that won an allocation.
        current_date: The current date.
        signals_skipped: Skipped signal accumulator.
        skipped: Counter of unfilled entry signals by reason.
        method: Configured position sizing method.
        risk_budget: Dollars of loss budgeted per ``atr_risk`` position.
        stop_distances: Per-symbol ATR stop distance in price units.

    """
    for symbol, _price, _ in pending_signals:
        if symbol in allocated_symbols:
            continue
        reason = _unallocated_reason(method, risk_budget, stop_distances.get(symbol))
        signals_skipped.append(
            {
                "symbol": symbol,
                "date": date_to_str(current_date),
                "reason": reason,
            }
        )
        skipped[reason] += 1
        # Keep signal_fired_at for priority on next day


def _unallocated_reason(
    method: str,
    risk_budget: float | None,
    stop_distance: float | None,
) -> str:
    """Separate a budget too small to carry a share from a cash shortage.

    Args:
        method: Configured position sizing method.
        risk_budget: Dollars of loss budgeted per ``atr_risk`` position.
        stop_distance: This symbol's ATR stop distance in price units.

    Returns:
        ``zero_shares`` when the risk budget alone buys nothing, otherwise
        ``insufficient_capital``.

    """
    if method != "atr_risk" or risk_budget is None or stop_distance is None:
        return "insufficient_capital"
    if int(risk_budget // stop_distance) == 0:
        return "zero_shares"
    return "insufficient_capital"


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
    atr_indicator: str | None = None,
) -> dict[str, _SymbolArrays]:
    """Convert Polars DataFrames to numpy arrays for fast iteration.

    Args:
        signal_dfs: Dict of symbol to signal DataFrames.
        atr_indicator: Id of the declared ATR indicator a risk rule reads,
            or None when none does.

    Returns:
        Dict of symbol to pre-extracted arrays.

    Raises:
        ValueError: If the named ATR indicator produced no column.

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
            atr=atr_column(df, atr_indicator),
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


def _refresh_marks(
    state: PortfolioState,
    current_date: date,
    symbol_arrays: dict[str, _SymbolArrays],
    mark: Literal["open", "close"],
) -> None:
    """Update each held lot's ``last_mark`` from the bar printed on ``current_date``.

    A symbol with no bar on that date keeps its carried mark: falling back to
    the entry price would invent a loss on the gap date and an equal recovery
    once the symbol prints again.

    Args:
        state: Portfolio state; held lots are mutated in place.
        current_date: Date for the price lookup.
        symbol_arrays: Symbol data for the price lookup.
        mark: Which price of the current bar to mark held positions at.

    """
    for symbol, lot in state.positions.items():
        arrays = symbol_arrays.get(symbol)
        bar_idx = arrays.date_to_idx.get(current_date) if arrays is not None else None
        if arrays is None or bar_idx is None:
            continue
        prices = arrays.opens if mark == "open" else arrays.closes
        lot.last_mark = float(prices[bar_idx])


def _compute_equity_from_state(state: PortfolioState) -> float:
    """Compute total equity as cash plus every held lot at its ``last_mark``.

    Args:
        state: Portfolio state, marked beforehand via ``_refresh_marks``.

    Returns:
        Total portfolio equity.

    """
    equity = float(state.cash)
    for lot in state.positions.values():
        equity += lot.shares * lot.last_mark
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
