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
        stop_loss_pct: Optional stop-loss percentage.
        take_profit_pct: Optional take-profit percentage.

    """

    symbol: str
    shares: int
    entry_price: float
    entry_date: date
    cost_basis: float
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None


@dataclass
class PortfolioState:
    """Mutable portfolio state for day-by-day simulation.

    Attributes:
        cash: Available cash balance.
        positions: Map of symbol to open PositionLot.
        equity_history: Daily total equity (cash + positions mark-to-market).

    """

    cash: float
    positions: dict[str, PositionLot] = field(default_factory=dict)
    equity_history: list[float] = field(default_factory=list)

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
) -> list[tuple[str, int, float]]:
    """Allocate available cash across competing entry signals.

    Priority: earliest signal first (lowest signal_fired_at_idx).
    Tiebreak: alphabetical by symbol (deterministic, reproducible).
    Shares are discrete integers (floor division). Remainder stays as cash.

    Args:
        cash: Available cash balance.
        total_equity: Total portfolio equity (cash + positions).
        signals: List of (symbol, price, signal_fired_at_idx) tuples.
        method: Sizing method ("equal_weight" or "fixed_pct").
        max_position_pct: Maximum position size as percentage of equity.
        max_positions: Maximum number of concurrent positions.
        current_position_count: Number of currently held positions.
        commission_pct: Commission as percentage of trade value (e.g. 0.1 for 0.1%).

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

    for symbol, price, _ in sorted_signals[:available_slots]:
        if remaining_cash <= 0 or price <= 0:
            break

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
