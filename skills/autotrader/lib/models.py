"""Typed data models for Alpaca trading responses.

All monetary/numeric values are floats (cast from Alpaca's string responses).
"""

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AccountInfo:
    """Alpaca trading account information."""

    equity: float
    cash: float
    buying_power: float
    portfolio_value: float
    long_market_value: float
    short_market_value: float
    last_equity: float
    daily_pnl: float
    daytrade_count: int
    pattern_day_trader: bool

    def to_dict(self) -> dict[str, object]:
        """Convert to JSON-serializable dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class PositionInfo:
    """A single open position."""

    symbol: str
    qty: float
    side: str
    avg_entry_price: float
    current_price: float
    market_value: float
    cost_basis: float
    unrealized_pl: float
    unrealized_pl_pct: float
    unrealized_intraday_pl: float
    change_today_pct: float

    def to_dict(self) -> dict[str, object]:
        """Convert to JSON-serializable dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class OrderInfo:
    """Information about a submitted or existing order."""

    order_id: str
    symbol: str
    side: str
    qty: float
    filled_qty: float
    order_type: str
    status: str
    limit_price: float | None
    stop_price: float | None
    filled_avg_price: float | None
    time_in_force: str
    submitted_at: str
    filled_at: str | None

    def to_dict(self) -> dict[str, object]:
        """Convert to JSON-serializable dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class RiskStatus:
    """Current risk utilization snapshot."""

    daily_trades_used: int
    daily_trades_limit: int
    daily_pnl_pct: float
    daily_loss_limit_pct: float
    current_exposure_pct: float
    max_exposure_pct: float
    max_position_pct: float

    def to_dict(self) -> dict[str, object]:
        """Convert to JSON-serializable dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class RiskResult:
    """Result of a pre-trade risk check."""

    allowed: bool
    rejection_reason: str | None

    def to_dict(self) -> dict[str, object]:
        """Convert to JSON-serializable dictionary."""
        return asdict(self)
