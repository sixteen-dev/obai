"""Pre-trade risk validation.

Stateless: all daily counters derived from Alpaca API queries on each check
(today's filled orders for trade count, account endpoint for daily P&L).
Server restarts don't reset counters.

Risk limits loaded from environment variables with sensible defaults.
"""

import math
import os

from .alpaca_client import AlpacaClient
from .logging_config import get_logger
from .models import RiskResult, RiskStatus

_logger = get_logger("risk")


# Defaults match context.md rules
_DEFAULT_MAX_POSITION_PCT = 10.0
_DEFAULT_MAX_DAILY_TRADES = 20
_DEFAULT_MAX_DAILY_LOSS_PCT = 3.0
_DEFAULT_MAX_EXPOSURE_PCT = 90.0


def _env_float(key: str, default: float) -> float:
    """Read a float from environment variable."""
    val = os.environ.get(key, "")
    if not val:
        return default
    return float(val)


def _env_int(key: str, default: int) -> int:
    """Read an int from environment variable."""
    val = os.environ.get(key, "")
    if not val:
        return default
    return int(val)


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
        qty_reason = _validate_qty(qty)
        if qty_reason:
            _logger.warning(
                "risk_check_rejected",
                symbol=symbol,
                side=side,
                qty=qty,
                reason=qty_reason,
            )
            return RiskResult(allowed=False, rejection_reason=qty_reason)

        account = self._client.get_account()
        positions = self._client.get_positions()
        todays_orders = self._client.get_todays_filled_orders()

        daily_trades = len(todays_orders)
        daily_pnl_pct = (account.daily_pnl / account.equity * 100) if account.equity > 0 else 0.0
        total_exposure = abs(account.long_market_value) + abs(account.short_market_value)

        # Check 1: Daily trade count
        if daily_trades >= self.max_daily_trades:
            reason = f"Daily trade limit reached ({daily_trades}/{self.max_daily_trades})"
            _logger.warning(
                "risk_check_rejected",
                symbol=symbol,
                side=side,
                qty=qty,
                reason=reason,
            )
            return RiskResult(allowed=False, rejection_reason=reason)

        # Check 2: Daily loss circuit breaker
        if daily_pnl_pct < -self.max_daily_loss_pct:
            reason = (
                f"Daily loss limit breached ({daily_pnl_pct:.1f}% vs "
                f"-{self.max_daily_loss_pct}% max)"
            )
            _logger.warning(
                "risk_check_rejected",
                symbol=symbol,
                side=side,
                qty=qty,
                reason=reason,
            )
            return RiskResult(allowed=False, rejection_reason=reason)

        if account.equity <= 0:
            _logger.info("risk_check_passed", symbol=symbol, side=side, qty=qty)
            return RiskResult(allowed=True, rejection_reason=None)

        existing = next(
            (p for p in positions if p.symbol.upper() == symbol.upper()),
            None,
        )
        sizing = _sized_order(side, qty, limit_price, existing)
        if sizing.rejection_reason:
            _logger.warning(
                "risk_check_rejected",
                symbol=symbol,
                side=side,
                qty=qty,
                reason=sizing.rejection_reason,
            )
            return RiskResult(allowed=False, rejection_reason=sizing.rejection_reason)

        # Pure reductions (sell within existing long, buy-to-cover within
        # existing short) do not grow exposure; skip size/exposure checks.
        if sizing.new_position_notional <= 0:
            _logger.info("risk_check_passed", symbol=symbol, side=side, qty=qty)
            return RiskResult(allowed=True, rejection_reason=None)

        # Check 3: Position size — applies to long and short alike.
        position_pct = sizing.new_position_notional / account.equity * 100
        if position_pct > self.max_position_pct:
            reason = (
                f"Position would be {position_pct:.1f}% of equity "
                f"(max {self.max_position_pct}%)"
            )
            _logger.warning(
                "risk_check_rejected",
                symbol=symbol,
                side=side,
                qty=qty,
                reason=reason,
            )
            return RiskResult(allowed=False, rejection_reason=reason)

        # Check 4: Portfolio exposure
        new_exposure_pct = (total_exposure + sizing.added_exposure) / account.equity * 100
        if new_exposure_pct > self.max_exposure_pct:
            reason = f"Exposure would be {new_exposure_pct:.1f}% (max {self.max_exposure_pct}%)"
            _logger.warning(
                "risk_check_rejected",
                symbol=symbol,
                side=side,
                qty=qty,
                reason=reason,
            )
            return RiskResult(allowed=False, rejection_reason=reason)

        _logger.info("risk_check_passed", symbol=symbol, side=side, qty=qty)
        return RiskResult(allowed=True, rejection_reason=None)


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


def _validate_qty(qty: float) -> str | None:
    """Return a rejection reason if qty is not a positive finite number."""
    if not math.isfinite(qty):
        return "Order qty must be a finite number"
    if qty <= 0:
        return "Order qty must be greater than zero"
    return None


def _sized_order(
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
        if price <= 0:
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
        new_position_notional = abs(existing_value) + added_exposure if existing_long else added_exposure
        return _OrderSizing(new_position_notional, added_exposure, None)

    if side_lc == "sell":
        new_short_qty = qty - existing_long
        if new_short_qty <= 0:
            return _OrderSizing(0.0, 0.0, None)
        price = limit_price if limit_price and limit_price > 0 else current_price
        if price <= 0:
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
        new_position_notional = abs(existing_value) + added_exposure if existing_short else added_exposure
        return _OrderSizing(new_position_notional, added_exposure, None)

    return _OrderSizing(0.0, 0.0, f"Unsupported order side: {side}")
