"""Capital allocation and portfolio position tracking for shared-capital backtesting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class PositionLot:
    """A single open position with discrete share tracking.

    Attributes:
        symbol: Ticker symbol.
        shares: Number of shares held (discrete integer).
        entry_price: Fill price per share at entry.
        entry_date: Date the position was opened.
        cost_basis: Total cost (shares * entry_price + commission).
        last_mark: Most recent observed price per share. Starts at the entry
            price and carries forward on dates the symbol has no bar.
        stop_level: Stop price frozen at the fill, whether it came from a
            percentage of that fill or a multiple of the signal bar's ATR.
            None when the lot carries no stop.
        take_profit_pct: Optional take-profit percentage.
        trail_level: Trailing stop level, ratcheted at the end of every bar
            the lot survives. None when the lot carries no trailing stop.
        high_water_mark: Highest high the lot has printed since its fill.

    """

    symbol: str
    shares: int
    entry_price: float
    entry_date: date
    cost_basis: float
    last_mark: float
    stop_level: float | None = None
    take_profit_pct: float | None = None
    trail_level: float | None = None
    high_water_mark: float = 0.0


@dataclass
class PortfolioState:
    """Mutable portfolio state for day-by-day simulation.

    Attributes:
        cash: Available cash balance.
        positions: Map of symbol to open PositionLot.
        equity_history: Daily total equity (cash + positions mark-to-market).
        last_exit_bar_idx: Bar index of each symbol's most recent exit, in
            that symbol's own bars. Read by the re-entry cooldown.

    """

    cash: float
    positions: dict[str, PositionLot] = field(default_factory=dict)
    equity_history: list[float] = field(default_factory=list)
    last_exit_bar_idx: dict[str, int] = field(default_factory=dict)

    @property
    def position_count(self) -> int:
        """Return number of currently held positions."""
        return len(self.positions)


def allocate_capital(  # noqa: PLR0913
    cash: float,
    total_equity: float,
    signals: list[tuple[str, float, int]],
    method: str,
    max_position_pct: float,
    max_positions: int,
    current_position_count: int,
    commission_pct: float = 0.0,
    risk_budget: float | None = None,
    stop_distances: dict[str, float] | None = None,
) -> list[tuple[str, int, float]]:
    """Allocate available cash across competing entry signals.

    Priority: earliest signal first (lowest signal_fired_at_idx).
    Tiebreak: alphabetical by symbol (deterministic, reproducible).
    Shares are discrete integers (floor division). Remainder stays as cash.

    Args:
        cash: Available cash balance.
        total_equity: Total portfolio equity (cash + positions).
        signals: List of (symbol, price, signal_fired_at_idx) tuples.
        method: Sizing method ("equal_weight", "fixed_pct" or "atr_risk").
        max_position_pct: Maximum position size as percentage of equity.
        max_positions: Maximum number of concurrent positions.
        current_position_count: Number of currently held positions.
        commission_pct: Commission as percentage of trade value (e.g. 0.1 for 0.1%).
        risk_budget: Dollars of loss the ``atr_risk`` method budgets per
            position, measured at the stop. Ignored by the other methods.
        stop_distances: Per-symbol ATR stop distance in price units, read by
            the ``atr_risk`` method. Ignored by the other methods.

    Returns:
        List of (symbol, shares, total_cost) tuples for allocations made.

    """
    available_slots = max_positions - current_position_count
    if available_slots <= 0 or cash <= 0:
        return []

    sorted_signals = sorted(signals, key=lambda s: (s[2], s[0]))

    allocations: list[tuple[str, int, float]] = []
    remaining_cash = cash
    commission_mult = 1 + commission_pct / 100
    distances = stop_distances or {}

    for symbol, price, _ in sorted_signals[:available_slots]:
        if remaining_cash <= 0 or price <= 0:
            break

        if method == "atr_risk":
            shares = _atr_risk_shares(
                risk_budget=risk_budget,
                stop_distance=distances.get(symbol),
                price=price,
                max_per_position=total_equity * max_position_pct / 100,
                remaining_cash=remaining_cash,
                commission_pct=commission_pct,
            )
        else:
            dollar_alloc = _compute_dollar_allocation(
                method=method,
                remaining_cash=remaining_cash,
                total_equity=total_equity,
                max_position_pct=max_position_pct,
                slots_remaining=available_slots - len(allocations),
            )
            shares = int(dollar_alloc // price)
        if shares <= 0:
            continue

        total_cost = shares * price * commission_mult
        if total_cost > remaining_cash:
            shares = _adjust_shares_for_cost(remaining_cash, price, commission_pct)
            if shares <= 0:
                continue
            total_cost = shares * price * commission_mult

        allocations.append((symbol, shares, total_cost))
        remaining_cash -= total_cost

    return allocations


def _atr_risk_shares(  # noqa: PLR0913
    risk_budget: float | None,
    stop_distance: float | None,
    price: float,
    max_per_position: float,
    remaining_cash: float,
    commission_pct: float,
) -> int:
    """Return the whole shares whose loss at the ATR stop equals the budget.

    The budget bounds the price loss down to the stop only; commission and
    slippage sit on top of it, and a gap through the stop can exceed it.
    Exposure and cash can only reduce the count the budget sets.

    Args:
        risk_budget: Dollars of loss budgeted for this position.
        stop_distance: ATR stop distance in price units for this symbol.
        price: Fill price per share.
        max_per_position: Dollar value the exposure cap allows.
        remaining_cash: Cash still unallocated on this bar.
        commission_pct: Commission as percentage of trade value.

    Returns:
        The share count, or 0 when the budget, exposure cap or cash leaves no
        whole share, or when the stop distance cannot be priced.

    """
    if risk_budget is None or stop_distance is None or stop_distance <= 0 or price <= 0:
        return 0
    effective_price = price * (1 + commission_pct / 100)
    if effective_price <= 0:
        return 0
    return min(
        int(risk_budget // stop_distance),
        int(max_per_position // price),
        int(remaining_cash // effective_price),
    )


def _compute_dollar_allocation(
    method: str,
    remaining_cash: float,
    total_equity: float,
    max_position_pct: float,
    slots_remaining: int,
) -> float:
    """Compute dollar allocation for a single position.

    Args:
        method: Sizing method ("equal_weight" or "fixed_pct").
        remaining_cash: Cash available.
        total_equity: Total portfolio equity.
        max_position_pct: Max position as percentage of equity.
        slots_remaining: Number of allocation slots remaining.

    Returns:
        Dollar amount to allocate.

    """
    max_per_position = total_equity * max_position_pct / 100

    if method == "equal_weight":
        per_slot = remaining_cash / max(1, slots_remaining)
        return min(per_slot, max_per_position, remaining_cash)
    # fixed_pct
    return min(remaining_cash, max_per_position)


def _adjust_shares_for_cost(
    remaining_cash: float,
    price: float,
    commission_pct: float,
) -> int:
    """Reduce share count to fit within remaining cash including commission.

    Args:
        remaining_cash: Cash available.
        price: Price per share.
        commission_pct: Commission as percentage of trade value.

    Returns:
        Adjusted number of shares (may be 0).

    """
    effective_price = price * (1 + commission_pct / 100)
    if effective_price <= 0:
        return 0
    return int(remaining_cash // effective_price)
