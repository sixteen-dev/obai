"""Pre-trade risk validation.

Stateless: all daily counters derived from Alpaca API queries on each check
(today's filled orders for trade count, account endpoint for daily P&L).
Server restarts don't reset counters.

Risk limits loaded from environment variables with sensible defaults.
"""

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
            qty: Number of shares.
            limit_price: Price estimate for position size calculation.
                Required for buy orders on new positions (no existing holding).
                For existing positions, falls back to current_price if omitted.

        Returns:
            RiskResult with allowed status and rejection reason.

        Raises:
            AlpacaClientError: If Alpaca API calls fail.

        """
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

        # Checks 3 & 4 only apply to buy orders
        if side.lower() == "buy" and account.equity > 0:
            # Estimate order cost
            existing = next(
                (p for p in positions if p.symbol.upper() == symbol.upper()),
                None,
            )
            price_est = limit_price or (existing.current_price if existing else 0.0)

            if price_est <= 0:
                reason = (
                    "Cannot estimate position size: no limit_price provided "
                    "and no existing position to infer price from. "
                    "Pass --limit-price for market orders on new positions."
                )
                _logger.warning(
                    "risk_check_rejected",
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    reason=reason,
                )
                return RiskResult(allowed=False, rejection_reason=reason)

            order_cost = qty * price_est
            existing_value = existing.market_value if existing else 0.0
            total_position = existing_value + order_cost

            # Check 3: Position size
            position_pct = total_position / account.equity * 100
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
            new_exposure_pct = (total_exposure + order_cost) / account.equity * 100
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
