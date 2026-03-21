"""Shared test fixtures — mock Alpaca SDK objects that behave like the real thing."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Fake Alpaca SDK objects (mimic the real attribute access patterns)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class FakeAccount:
    """Mimics alpaca TradeAccount — returns strings like the real API."""

    equity: str = "100000.00"
    cash: str = "25000.00"
    buying_power: str = "25000.00"
    portfolio_value: str = "100000.00"
    long_market_value: str = "75000.00"
    short_market_value: str = "0.00"
    last_equity: str = "99800.00"
    daytrade_count: int = 0
    pattern_day_trader: bool = False


@dataclass
class FakePosition:
    """Mimics alpaca Position — returns strings like the real API."""

    symbol: str = "AAPL"
    qty: str = "25"
    side: str = "long"
    avg_entry_price: str = "195.20"
    current_price: str = "205.80"
    market_value: str = "5145.00"
    cost_basis: str = "4880.00"
    unrealized_pl: str = "265.00"
    unrealized_plpc: str = "0.0543"
    unrealized_intraday_pl: str = "50.00"
    change_today: str = "0.0098"


@dataclass
class FakeOrder:
    """Mimics alpaca Order — returns strings like the real API."""

    id: str = "order-uuid-123"
    symbol: str = "AAPL"
    side: str = "buy"
    qty: str = "10"
    filled_qty: str = "10"
    type: str = "market"
    status: str = "accepted"
    limit_price: str | None = None
    stop_price: str | None = None
    filled_avg_price: str | None = "205.50"
    time_in_force: str = "day"
    submitted_at: str = "2026-03-21T10:05:00Z"
    filled_at: str | None = "2026-03-21T10:05:01Z"


@dataclass
class FakeClock:
    """Mimics alpaca Clock."""

    is_open: bool = True
    timestamp: str = "2026-03-21T10:30:00-04:00"
    next_open: str = "2026-03-22T09:30:00-04:00"
    next_close: str = "2026-03-21T16:00:00-04:00"


def make_filled_order(symbol: str = "AAPL", side: str = "buy", qty: str = "10") -> FakeOrder:
    """Create a filled order for today (used in risk checker tests)."""
    return FakeOrder(
        id=f"order-{symbol}-{side}",
        symbol=symbol,
        side=side,
        qty=qty,
        filled_qty=qty,
        status="filled",
        filled_at=datetime.now(tz=timezone.utc).isoformat(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required environment variables for Alpaca client."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-api-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret-key")


@pytest.fixture()
def mock_trading_client() -> MagicMock:
    """Create a mock TradingClient with default responses."""
    client = MagicMock()
    client.get_account.return_value = FakeAccount()
    client.get_all_positions.return_value = [
        FakePosition(symbol="AAPL", qty="25", market_value="5145.00"),
        FakePosition(
            symbol="NVDA",
            qty="10",
            market_value="8900.00",
            avg_entry_price="890.00",
            current_price="878.50",
            unrealized_pl="-115.00",
            unrealized_plpc="-0.013",
        ),
    ]
    client.get_open_position.return_value = FakePosition()
    client.get_clock.return_value = FakeClock()
    client.get_orders.return_value = []
    client.submit_order.return_value = FakeOrder()
    client.close_position.return_value = FakeOrder(side="sell", symbol="AAPL")
    client.cancel_order_by_id.return_value = None
    client.cancel_orders.return_value = []
    client.close_all_positions.return_value = []
    return client


@pytest.fixture()
def alpaca_client(mock_env: None, mock_trading_client: MagicMock) -> Any:
    """Create an AlpacaClient with mocked TradingClient."""
    with patch("lib.alpaca_client.TradingClient", return_value=mock_trading_client):
        from lib.alpaca_client import AlpacaClient

        client = AlpacaClient()
        client._client = mock_trading_client
        return client
