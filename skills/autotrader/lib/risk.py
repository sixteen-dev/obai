"""Pre-trade risk validation.

Stateless: all daily counters derived from Alpaca API queries on each check
(today's filled orders for trade count, account endpoint for daily P&L).
Server restarts don't reset counters.

Risk limits loaded from environment variables with sensible defaults.
"""

import math
import os
from dataclasses import dataclass

from .alpaca_client import MAX_ORDER_PAGE, AlpacaClient
from .logging_config import get_logger
from .models import (
    REDUCING_SIDES,
    AccountInfo,
    OrderInfo,
    PositionInfo,
    RiskResult,
    RiskStatus,
)

_logger = get_logger("risk")


# Defaults match context.md rules
_DEFAULT_MAX_POSITION_PCT = 10.0
_DEFAULT_MAX_DAILY_TRADES = 20
_DEFAULT_MAX_DAILY_LOSS_PCT = 3.0
_DEFAULT_MAX_EXPOSURE_PCT = 90.0
_DEFAULT_MAX_POSITIONS = 10

_TERMINAL_STATUSES = frozenset({"filled", "canceled", "expired", "rejected", "replaced"})
_POSITION_SIDES = frozenset({"long", "short"})
_ORDER_SIDES = frozenset({"buy", "sell"})


def _env_float(key: str, default: float) -> float:
    """Read a percent-of-equity limit from the environment."""
    val = os.environ.get(key, "")
    if not val:
        return default
    number = float(val)
    if not math.isfinite(number) or not 0 < number <= 100:
        raise ValueError(f"{key} must be finite and in (0, 100]")
    return number


def _env_int(key: str, default: int) -> int:
    """Read a positive integer limit from the environment."""
    val = os.environ.get(key, "")
    if not val:
        return default
    number = int(val)
    if number <= 0:
        raise ValueError(f"{key} must be a positive integer")
    return number


@dataclass(frozen=True)
class _BrokerState:
    """Broker state one risk check is evaluated against."""

    account: AccountInfo
    positions: list[PositionInfo]
    pending: list[OrderInfo]
    daily_trades: int


@dataclass(frozen=True)
class _Reservation:
    """Exposure and cash that outstanding pending orders would consume."""

    exposure: float
    cash: float
    rejection_reason: str | None


class RiskChecker:
    """Pre-trade risk validation engine.

    Validates orders against configurable limits before submission.
    All state is derived from Alpaca API calls.
    """

    def __init__(self, client: AlpacaClient) -> None:
        """Initialize risk checker.

        Args:
            client: Alpaca client for state queries.

        """
        self._client = client
        self.max_position_pct = _env_float("MAX_POSITION_PCT", _DEFAULT_MAX_POSITION_PCT)
        self.max_daily_trades = _env_int("MAX_DAILY_TRADES", _DEFAULT_MAX_DAILY_TRADES)
        self.max_daily_loss_pct = _env_float("MAX_DAILY_LOSS_PCT", _DEFAULT_MAX_DAILY_LOSS_PCT)
        self.max_exposure_pct = _env_float("MAX_EXPOSURE_PCT", _DEFAULT_MAX_EXPOSURE_PCT)
        self.max_positions = _env_int("MAX_POSITIONS", _DEFAULT_MAX_POSITIONS)

    def get_risk_status(self) -> RiskStatus:
        """Get current risk utilization without validating a specific order."""
        account = self._client.get_account()
        todays_orders = self._client.get_todays_filled_orders()

        daily_trades = len(todays_orders)
        daily_pnl_pct = (account.daily_pnl / account.equity * 100) if account.equity > 0 else 0.0
        total_exposure = abs(account.long_market_value) + abs(account.short_market_value)
        exposure_pct = (total_exposure / account.equity * 100) if account.equity > 0 else 0.0

        status = RiskStatus(
            daily_trades_used=daily_trades,
            daily_trades_limit=self.max_daily_trades,
            daily_pnl_pct=round(daily_pnl_pct, 2),
            daily_loss_limit_pct=self.max_daily_loss_pct,
            current_exposure_pct=round(exposure_pct, 2),
            max_exposure_pct=self.max_exposure_pct,
            max_position_pct=self.max_position_pct,
            max_positions=self.max_positions,
        )
        _logger.info(
            "risk_status",
            daily_trades=f"{daily_trades}/{self.max_daily_trades}",
            daily_pnl_pct=round(daily_pnl_pct, 2),
            exposure_pct=round(exposure_pct, 2),
        )
        return status

    def check_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        limit_price: float | None = None,
    ) -> RiskResult:
        """Validate an order against all risk limits.

        Args:
            symbol: Ticker symbol.
            side: 'buy' or 'sell'.
            qty: Number of shares (must be > 0 and finite).
            limit_price: Price estimate for position size calculation.
                Required for buy orders on new positions and for sells that
                exceed the existing long quantity (which open or grow a
                short). For existing same-direction positions, falls back
                to ``current_price`` if omitted.

        Returns:
            RiskResult with allowed status and rejection reason.

        Raises:
            AlpacaClientError: If Alpaca API calls fail.

        """
        reason = _validate_qty(qty) or _validate_price(limit_price)
        if reason:
            return _reject(symbol, side, qty, reason)

        open_orders = self._client.get_orders(status="open", limit=MAX_ORDER_PAGE)
        if len(open_orders) >= MAX_ORDER_PAGE:
            return _reject(
                symbol, side, qty, "Open-order response may be truncated; reconcile before trading"
            )
        state = _BrokerState(
            account=self._client.get_account(),
            positions=self._client.get_positions(),
            pending=[o for o in open_orders if o.status not in _TERMINAL_STATUSES],
            daily_trades=len(self._client.get_todays_filled_orders()),
        )

        reason = _pre_trade_reason(symbol, state)
        if reason:
            return _reject(symbol, side, qty, reason)

        existing = next((p for p in state.positions if p.symbol.upper() == symbol.upper()), None)
        sizing = _sized_order(side, qty, limit_price, existing)
        # Entry circuit breakers must not prevent an otherwise valid reduction.
        if sizing.rejection_reason is None and sizing.new_position_notional <= 0:
            return _allow(symbol, side, qty)

        reason = self._new_exposure_reason(symbol, side, state, sizing)
        if reason:
            return _reject(symbol, side, qty, reason)
        return _allow(symbol, side, qty)

    def _new_exposure_reason(
        self,
        symbol: str,
        side: str,
        state: _BrokerState,
        sizing: "_OrderSizing",
    ) -> str | None:
        """Return why this order may not add exposure, or None if it may."""
        reason = self._state_reason(symbol, state, sizing)
        if reason:
            return reason
        reserved = _reserved(state.pending, state.positions)
        return reserved.rejection_reason or self._limit_reason(side, state, sizing, reserved)

    def _state_reason(
        self,
        symbol: str,
        state: _BrokerState,
        sizing: "_OrderSizing",
    ) -> str | None:
        """Check account validity, the daily circuit breakers and position count."""
        account = state.account
        balances = (
            account.equity,
            account.cash,
            account.buying_power,
            account.daily_pnl,
            account.long_market_value,
            account.short_market_value,
        )
        if account.equity <= 0 or not all(math.isfinite(value) for value in balances):
            return "Invalid account equity or balances; new exposure blocked"
        if state.daily_trades >= self.max_daily_trades:
            return f"Daily trade limit reached ({state.daily_trades}/{self.max_daily_trades})"
        daily_pnl_pct = account.daily_pnl / account.equity * 100
        if daily_pnl_pct <= -self.max_daily_loss_pct:
            return (
                f"Daily loss limit breached ({daily_pnl_pct:.1f}% vs "
                f"-{self.max_daily_loss_pct}% max)"
            )
        if sizing.rejection_reason:
            return sizing.rejection_reason
        occupied = {p.symbol.upper() for p in state.positions} | {
            o.symbol.upper() for o in state.pending
        }
        if symbol.upper() not in occupied and len(occupied) >= self.max_positions:
            return f"Maximum positions reached ({self.max_positions})"
        return None

    def _limit_reason(
        self,
        side: str,
        state: _BrokerState,
        sizing: "_OrderSizing",
        reserved: _Reservation,
    ) -> str | None:
        """Check position size, portfolio exposure and buying power."""
        account = state.account
        position_pct = sizing.new_position_notional / account.equity * 100
        if position_pct > self.max_position_pct:
            return f"Position would be {position_pct:.1f}% of equity (max {self.max_position_pct}%)"

        held_exposure = abs(account.long_market_value) + abs(account.short_market_value)
        new_exposure_pct = (
            (held_exposure + reserved.exposure + sizing.added_exposure) / account.equity * 100
        )
        if new_exposure_pct > self.max_exposure_pct:
            return f"Exposure would be {new_exposure_pct:.1f}% (max {self.max_exposure_pct}%)"

        # Broker-side rules will still bounce an underfunded buy, but rejecting
        # locally keeps the daily-trade counter honest and gives a clearer error.
        available = max(0.0, min(account.cash - reserved.cash, account.buying_power))
        if side.lower() == "buy" and sizing.added_exposure > available:
            return (
                f"Insufficient buying power: order needs "
                f"${sizing.added_exposure:,.0f} but ${available:,.0f} cash/buying power available"
            )
        return None


class _OrderSizing:
    """Resolved sizing for an order: notional, added exposure, or rejection.

    `new_position_notional` is the dollar value of the resulting position on
    the side that grows (long for buys-on-flat-or-long, short for sells past
    existing long). `added_exposure` is the increment to gross exposure.
    `rejection_reason` carries any pricing-failure message so the caller can
    emit a uniform reject.
    """

    __slots__ = ("new_position_notional", "added_exposure", "rejection_reason")

    def __init__(
        self,
        new_position_notional: float,
        added_exposure: float,
        rejection_reason: str | None,
    ) -> None:
        self.new_position_notional = new_position_notional
        self.added_exposure = added_exposure
        self.rejection_reason = rejection_reason


def _reject(symbol: str, side: str, qty: float, reason: str) -> RiskResult:
    """Log and return a rejection so every gate leaves the same audit record."""
    _logger.warning("risk_check_rejected", symbol=symbol, side=side, qty=qty, reason=reason)
    return RiskResult(allowed=False, rejection_reason=reason)


def _allow(symbol: str, side: str, qty: float) -> RiskResult:
    """Log and return an approval."""
    _logger.info("risk_check_passed", symbol=symbol, side=side, qty=qty)
    return RiskResult(allowed=True, rejection_reason=None)


def _validate_qty(qty: float) -> str | None:
    """Return a rejection reason if qty is not a positive finite number."""
    if not math.isfinite(qty):
        return "Order qty must be a finite number"
    if qty <= 0:
        return "Order qty must be greater than zero"
    return None


def _validate_price(limit_price: float | None) -> str | None:
    """Return a rejection reason if a supplied price estimate is unusable."""
    if limit_price is not None and (not math.isfinite(limit_price) or limit_price <= 0):
        return "Price must be positive and finite"
    return None


def _pre_trade_reason(symbol: str, state: _BrokerState) -> str | None:
    """Reject before sizing when broker state is duplicated or invalid."""
    if any(o.symbol.upper() == symbol.upper() for o in state.pending):
        return "Pending order for this symbol; reconcile before another order"
    invalid_position = any(
        not math.isfinite(p.qty) or p.qty <= 0 or p.side not in _POSITION_SIDES
        for p in state.positions
    )
    if invalid_position:
        return "Invalid position state; reconcile before trading"
    invalid_pending = any(
        not math.isfinite(o.qty)
        or not math.isfinite(o.filled_qty)
        or o.qty <= 0
        or not 0 <= o.filled_qty <= o.qty
        or o.side not in _ORDER_SIDES
        for o in state.pending
    )
    if invalid_pending:
        return "Invalid pending order state"
    return None


def _pending_price(order: OrderInfo, held: PositionInfo | None) -> float | None:
    """Price a pending order conservatively, or None when no price is usable.

    A limit or stop price is an estimate, not a fill guarantee, so the highest
    usable candidate is reserved. Zero and non-finite fields are discarded
    rather than poisoning an otherwise usable estimate.
    """
    candidates = [
        price
        for price in (order.limit_price, order.stop_price, held.current_price if held else None)
        if price is not None and math.isfinite(price) and price > 0
    ]
    return max(candidates) if candidates else None


def _reserved(pending: list[OrderInfo], positions: list[PositionInfo]) -> _Reservation:
    """Reserve the exposure and cash outstanding pending orders would consume.

    An order that only reduces a position already counted in the account's
    market value adds no gross exposure, so it is not double-counted and needs
    no price. A pending buy always consumes cash, including a buy-to-cover.

    Args:
        pending: Non-terminal open orders for this account.
        positions: Currently held positions.

    Returns:
        The reservation, or one carrying a rejection reason when an order that
        would add exposure or spend cash cannot be priced.
    """
    exposure = cash = 0.0
    for order in pending:
        remaining = order.qty - order.filled_qty
        if remaining == 0:
            continue
        held = next((p for p in positions if p.symbol.upper() == order.symbol.upper()), None)
        reducible = held.qty if held and (held.side, order.side) in REDUCING_SIDES else 0.0
        adding = max(0.0, remaining - reducible)
        if adding == 0 and order.side != "buy":
            continue
        price = _pending_price(order, held)
        if price is None:
            return _Reservation(
                0.0, 0.0, f"Cannot price pending {order.symbol} order; reconcile orders first"
            )
        exposure += adding * price
        if order.side == "buy":
            cash += remaining * price
    return _Reservation(exposure, cash, None)


def _sized_order(  # noqa: PLR0911 -- separate long/short/reduction outcomes
    side: str,
    qty: float,
    limit_price: float | None,
    existing: object,
) -> _OrderSizing:
    """Compute the new-position notional and added exposure for an order.

    A buy grows long exposure (or reduces a short). A sell reduces existing
    long up to the held quantity; any excess opens or grows a short and is
    treated as new risk-checked exposure on the short side.
    """
    side_lc = side.lower()
    existing_qty = float(getattr(existing, "qty", 0.0) or 0.0)
    existing_side = str(getattr(existing, "side", "long") or "long").lower()
    existing_long = existing_qty if existing_side == "long" else 0.0
    existing_short = existing_qty if existing_side == "short" else 0.0
    existing_value = float(getattr(existing, "market_value", 0.0) or 0.0)
    current_price = float(getattr(existing, "current_price", 0.0) or 0.0)

    if side_lc == "buy":
        new_long_qty = qty - existing_short
        if new_long_qty <= 0:
            return _OrderSizing(0.0, 0.0, None)
        price = limit_price if limit_price and limit_price > 0 else current_price
        if not math.isfinite(price) or price <= 0 or not math.isfinite(existing_value):
            return _OrderSizing(
                0.0,
                0.0,
                (
                    "Cannot estimate position size: no limit_price provided "
                    "and no existing position to infer price from. "
                    "Pass --limit-price for market orders on new positions."
                ),
            )
        added_exposure = new_long_qty * price
        new_position_notional = (
            abs(existing_value) + added_exposure if existing_long else added_exposure
        )
        return _OrderSizing(new_position_notional, added_exposure, None)

    if side_lc == "sell":
        new_short_qty = qty - existing_long
        if new_short_qty <= 0:
            return _OrderSizing(0.0, 0.0, None)
        price = limit_price if limit_price and limit_price > 0 else current_price
        if not math.isfinite(price) or price <= 0 or not math.isfinite(existing_value):
            return _OrderSizing(
                0.0,
                0.0,
                (
                    "Cannot estimate short-position size: no limit_price "
                    "provided and no existing position to infer price from. "
                    "Pass --limit-price for sells that exceed the existing "
                    "long quantity (these open or grow a short)."
                ),
            )
        added_exposure = new_short_qty * price
        new_position_notional = (
            abs(existing_value) + added_exposure if existing_short else added_exposure
        )
        return _OrderSizing(new_position_notional, added_exposure, None)

    return _OrderSizing(0.0, 0.0, f"Unsupported order side: {side}")
