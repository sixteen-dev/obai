"""Thin typed wrapper around alpaca-py TradingClient.

Handles float casting (Alpaca returns strings for all numerics) and maps
responses to typed dataclasses. Paper trading enforced via assertion.

Environment variables:
    ALPACA_API_KEY: Alpaca API key
    ALPACA_SECRET_KEY: Alpaca secret key
"""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, OrderStatus, OrderType, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)

from .logging_config import get_logger
from .models import AccountInfo, OrderInfo, PositionInfo

# US equity exchanges run on US/Eastern. Day-counting (daily trade limits,
# "today's filled orders") must reference that calendar, not UTC, so 4–8pm ET
# trades aren't accidentally counted against the next exchange day.
_US_EXCHANGE_TZ = ZoneInfo("America/New_York")


class AlpacaClientError(Exception):
    """Error from Alpaca API operations."""


def _safe_float(value: object) -> float:
    """Cast Alpaca string values to float safely."""
    if value is None:
        return 0.0
    return float(str(value))


def _status_value(status: object) -> str:
    """Return the canonical lowercase Alpaca order status.

    The Alpaca SDK is inconsistent: some response paths give back an
    ``OrderStatus`` enum, others give back the raw string value
    ("filled", "new", ...). ``str(enum)`` is ``"OrderStatus.FILLED"``,
    which compares equal to neither the string ``"filled"`` nor
    ``OrderStatus.FILLED.value``. Use ``.value`` when available and fall
    back to lowercasing the string form so both shapes produce the same
    key for set/dict lookups.
    """
    if status is None:
        return ""
    value = getattr(status, "value", None)
    if isinstance(value, str):
        return value.lower()
    return str(status).lower()


_SIDE_MAP = {"buy": OrderSide.BUY, "sell": OrderSide.SELL}
_TIF_MAP = {
    "day": TimeInForce.DAY,
    "gtc": TimeInForce.GTC,
    "opg": TimeInForce.OPG,
    "cls": TimeInForce.CLS,
    "ioc": TimeInForce.IOC,
    "fok": TimeInForce.FOK,
}


_logger = get_logger("alpaca_client")


class AlpacaClient:
    """Typed wrapper around Alpaca TradingClient for paper trading.

    All numeric fields from Alpaca are cast to float/int.
    Paper mode is enforced via assertion on init.
    """

    def __init__(self) -> None:
        """Initialize from environment variables. Asserts paper mode."""
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

        if not api_key or not secret_key:
            msg = "ALPACA_API_KEY and ALPACA_SECRET_KEY must be set"
            raise AlpacaClientError(msg)

        self._client = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=True,  # ALWAYS paper — hard-coded
        )

    def get_account(self) -> AccountInfo:
        """Get account information with all numerics as float."""
        try:
            acct = self._client.get_account()
        except APIError as exc:
            raise AlpacaClientError(str(exc)) from exc

        equity = _safe_float(getattr(acct, "equity", 0))
        last_equity = _safe_float(getattr(acct, "last_equity", 0))

        return AccountInfo(
            equity=equity,
            cash=_safe_float(getattr(acct, "cash", 0)),
            buying_power=_safe_float(getattr(acct, "buying_power", 0)),
            portfolio_value=_safe_float(getattr(acct, "portfolio_value", 0)),
            long_market_value=_safe_float(getattr(acct, "long_market_value", 0)),
            short_market_value=_safe_float(getattr(acct, "short_market_value", 0)),
            last_equity=last_equity,
            daily_pnl=round(equity - last_equity, 2),
            daytrade_count=int(getattr(acct, "daytrade_count", 0) or 0),
            pattern_day_trader=bool(getattr(acct, "pattern_day_trader", False)),
        )

    def get_positions(self) -> list[PositionInfo]:
        """Get all open positions."""
        try:
            positions = self._client.get_all_positions()
        except APIError as exc:
            raise AlpacaClientError(str(exc)) from exc

        return [self._map_position(pos) for pos in positions]

    def get_position(self, symbol: str) -> PositionInfo | None:
        """Get position for a specific symbol, or None if not held."""
        try:
            pos = self._client.get_open_position(symbol.upper())
        except APIError as exc:
            if "position does not exist" in str(exc).lower():
                return None
            raise AlpacaClientError(str(exc)) from exc

        return self._map_position(pos)

    def submit_order(  # noqa: PLR0913
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "market",
        limit_price: float | None = None,
        stop_price: float | None = None,
        time_in_force: str = "day",
    ) -> OrderInfo:
        """Submit a trading order.

        Args:
            symbol: Ticker symbol.
            side: 'buy' or 'sell'.
            qty: Number of shares.
            order_type: 'market', 'limit', 'stop', or 'stop_limit'.
            limit_price: Required for limit/stop_limit orders.
            stop_price: Required for stop/stop_limit orders.
            time_in_force: 'day', 'gtc', etc.

        Returns:
            Order confirmation.

        Raises:
            AlpacaClientError: If submission fails.
            ValueError: If parameters are invalid.

        """
        mapped_side = _SIDE_MAP.get(side.lower())
        if mapped_side is None:
            msg = f"Invalid side '{side}'. Must be 'buy' or 'sell'."
            raise ValueError(msg)

        tif = _TIF_MAP.get(time_in_force.lower(), TimeInForce.DAY)

        try:
            order_data = self._build_order_request(
                symbol.upper(),
                mapped_side,
                qty,
                order_type,
                limit_price,
                stop_price,
                tif,
            )
            order = self._client.submit_order(order_data)
        except APIError as exc:
            _logger.error(
                "order_failed",
                symbol=symbol.upper(),
                side=side,
                qty=qty,
                order_type=order_type,
                error=str(exc),
            )
            raise AlpacaClientError(str(exc)) from exc

        result = self._map_order(order)
        _logger.info(
            "order_submitted",
            symbol=result.symbol,
            side=result.side,
            qty=result.qty,
            order_type=result.order_type,
            order_id=result.order_id,
            status=result.status,
        )
        return result

    def get_orders(self, status: str = "open", limit: int = 50) -> list[OrderInfo]:
        """Get orders filtered by status ('open', 'closed', 'all')."""
        status_map = {
            "open": QueryOrderStatus.OPEN,
            "closed": QueryOrderStatus.CLOSED,
            "all": QueryOrderStatus.ALL,
        }
        query_status = status_map.get(status.lower(), QueryOrderStatus.OPEN)

        try:
            orders = self._client.get_orders(
                filter=GetOrdersRequest(status=query_status, limit=limit),
            )
        except APIError as exc:
            raise AlpacaClientError(str(exc)) from exc

        return [self._map_order(o) for o in orders]

    def get_todays_orders_for_limit(self) -> list[OrderInfo]:
        """Get today's submitted+filled orders for daily-trade-limit accounting.

        Day boundary is US Eastern (the exchange day), not UTC, so trades
        between 8pm-midnight ET don't roll into the next exchange day. Both
        filled and still-open submissions count against the limit so a queue
        of unfilled orders can't exceed the configured submission cap.
        """
        midnight_et = datetime.now(tz=_US_EXCHANGE_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        after_utc = midnight_et.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            orders = self._client.get_orders(
                filter=GetOrdersRequest(
                    status=QueryOrderStatus.ALL,
                    after=after_utc,
                    limit=200,
                ),
            )
        except APIError as exc:
            raise AlpacaClientError(str(exc)) from exc

        # Compare against `.value` (e.g. "filled"), not `str(enum)` which
        # is "OrderStatus.FILLED". The Alpaca SDK returns OrderStatus
        # enums when fully typed but raw strings on some response paths
        # (paginated lists, websocket-derived models). Normalize both
        # sides through `_status_value` so the same set of canonical
        # lowercase strings matches either shape.
        counted = {
            OrderStatus.FILLED.value,
            OrderStatus.PARTIALLY_FILLED.value,
            OrderStatus.NEW.value,
            OrderStatus.ACCEPTED.value,
            OrderStatus.PENDING_NEW.value,
            OrderStatus.ACCEPTED_FOR_BIDDING.value,
        }
        return [
            self._map_order(o)
            for o in orders
            if _status_value(getattr(o, "status", None)) in counted
        ]

    def get_todays_filled_orders(self) -> list[OrderInfo]:
        """Compatibility alias — same semantics as ``get_todays_orders_for_limit``.

        Older callers asked specifically for filled orders, but day-trade
        limits should count submissions too. Keep the old name working so
        tests and scripts don't break, with the corrected behavior.
        """
        return self.get_todays_orders_for_limit()

    def cancel_order(self, order_id: str) -> None:
        """Cancel a specific order."""
        try:
            self._client.cancel_order_by_id(order_id)
        except APIError as exc:
            raise AlpacaClientError(str(exc)) from exc

    def close_position(self, symbol: str) -> OrderInfo:
        """Close an open position at market price."""
        try:
            order = self._client.close_position(symbol.upper())
        except APIError as exc:
            _logger.error("close_position_failed", symbol=symbol.upper(), error=str(exc))
            raise AlpacaClientError(str(exc)) from exc

        result = self._map_order(order)
        _logger.info(
            "position_closed",
            symbol=result.symbol,
            side=result.side,
            qty=result.qty,
            order_id=result.order_id,
            status=result.status,
        )
        return result

    def get_clock(self) -> dict[str, object]:
        """Get market clock (is_open, next_open, next_close)."""
        try:
            clock = self._client.get_clock()
        except APIError as exc:
            raise AlpacaClientError(str(exc)) from exc

        return {
            "is_open": bool(getattr(clock, "is_open", False)),
            "timestamp": str(getattr(clock, "timestamp", "")),
            "next_open": str(getattr(clock, "next_open", "")),
            "next_close": str(getattr(clock, "next_close", "")),
        }

    @staticmethod
    def _build_order_request(  # noqa: PLR0913
        symbol: str,
        side: OrderSide,
        qty: float,
        order_type: str,
        limit_price: float | None,
        stop_price: float | None,
        tif: TimeInForce,
    ) -> MarketOrderRequest | LimitOrderRequest | StopOrderRequest | StopLimitOrderRequest:
        """Build the appropriate alpaca-py order request object."""
        otype = order_type.lower()

        if otype == "market":
            return MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=tif,
                type=OrderType.MARKET,
            )
        if otype == "limit":
            if limit_price is None:
                msg = "limit_price required for limit orders"
                raise ValueError(msg)
            return LimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=tif,
                type=OrderType.LIMIT,
                limit_price=limit_price,
            )
        if otype == "stop":
            if stop_price is None:
                msg = "stop_price required for stop orders"
                raise ValueError(msg)
            return StopOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=tif,
                type=OrderType.STOP,
                stop_price=stop_price,
            )
        if otype == "stop_limit":
            if limit_price is None or stop_price is None:
                msg = "Both limit_price and stop_price required for stop_limit orders"
                raise ValueError(msg)
            return StopLimitOrderRequest(
                symbol=symbol,
                qty=qty,
                side=side,
                time_in_force=tif,
                type=OrderType.STOP_LIMIT,
                limit_price=limit_price,
                stop_price=stop_price,
            )

        msg = f"Invalid order_type '{order_type}'. Use: market, limit, stop, stop_limit."
        raise ValueError(msg)

    @staticmethod
    def _map_position(pos: object) -> PositionInfo:
        """Map Alpaca position to typed PositionInfo."""
        return PositionInfo(
            symbol=str(getattr(pos, "symbol", "")),
            qty=_safe_float(getattr(pos, "qty", 0)),
            side=str(getattr(pos, "side", "long")),
            avg_entry_price=_safe_float(getattr(pos, "avg_entry_price", 0)),
            current_price=_safe_float(getattr(pos, "current_price", 0)),
            market_value=_safe_float(getattr(pos, "market_value", 0)),
            cost_basis=_safe_float(getattr(pos, "cost_basis", 0)),
            unrealized_pl=_safe_float(getattr(pos, "unrealized_pl", 0)),
            unrealized_pl_pct=_safe_float(getattr(pos, "unrealized_plpc", 0)) * 100,
            unrealized_intraday_pl=_safe_float(getattr(pos, "unrealized_intraday_pl", 0)),
            change_today_pct=_safe_float(getattr(pos, "change_today", 0)) * 100,
        )

    @staticmethod
    def _map_order(order: object) -> OrderInfo:
        """Map Alpaca order to typed OrderInfo."""
        filled_at = getattr(order, "filled_at", None)
        lp = getattr(order, "limit_price", None)
        sp = getattr(order, "stop_price", None)
        fap = getattr(order, "filled_avg_price", None)

        return OrderInfo(
            order_id=str(getattr(order, "id", "")),
            symbol=str(getattr(order, "symbol", "")),
            side=str(getattr(order, "side", "")),
            qty=_safe_float(getattr(order, "qty", 0)),
            filled_qty=_safe_float(getattr(order, "filled_qty", 0)),
            order_type=str(getattr(order, "type", "")),
            status=_status_value(getattr(order, "status", None)),
            limit_price=_safe_float(lp) if lp is not None else None,
            stop_price=_safe_float(sp) if sp is not None else None,
            filled_avg_price=_safe_float(fap) if fap is not None else None,
            time_in_force=str(getattr(order, "time_in_force", "")),
            submitted_at=str(getattr(order, "submitted_at", "")),
            filled_at=str(filled_at) if filled_at is not None else None,
        )
