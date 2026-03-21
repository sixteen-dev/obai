"""Comprehensive tests for AlpacaClient wrapper.

Tests cover: float casting, all method paths, error handling, paper mode enforcement,
and edge cases (None values, missing positions, API failures).
"""

from unittest.mock import MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

from lib.alpaca_client import AlpacaClient, AlpacaClientError

from .conftest import FakeAccount, FakeClock, FakeOrder


class TestAlpacaClientInit:
    """Test client initialization and paper mode enforcement."""

    def test_init_requires_api_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(AlpacaClientError, match="ALPACA_API_KEY"):
            AlpacaClient()

    def test_init_requires_secret_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ALPACA_API_KEY", "key")
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        with pytest.raises(AlpacaClientError, match="ALPACA_SECRET_KEY"):
            AlpacaClient()

    def test_init_creates_paper_client(
        self, mock_env: None, mock_trading_client: MagicMock
    ) -> None:
        with patch("lib.alpaca_client.TradingClient", return_value=mock_trading_client) as ctor:
            AlpacaClient()
            ctor.assert_called_once_with(
                api_key="test-api-key",
                secret_key="test-secret-key",
                paper=True,
            )


class TestGetAccount:
    """Test account info retrieval and float casting."""

    def test_returns_typed_account(self, alpaca_client: AlpacaClient) -> None:
        account = alpaca_client.get_account()
        assert account.equity == 100000.0
        assert account.cash == 25000.0
        assert account.buying_power == 25000.0
        assert account.portfolio_value == 100000.0
        assert account.long_market_value == 75000.0
        assert account.short_market_value == 0.0
        assert account.last_equity == 99800.0
        assert account.daily_pnl == 200.0  # 100000 - 99800
        assert account.daytrade_count == 0
        assert account.pattern_day_trader is False

    def test_daily_pnl_calculation(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.get_account.return_value = FakeAccount(
            equity="95000.00",
            last_equity="98000.00",
        )
        account = alpaca_client.get_account()
        assert account.daily_pnl == -3000.0

    def test_handles_none_values(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        """Alpaca sometimes returns None for fields — should default to 0."""
        fake = FakeAccount()
        fake.equity = None  # type: ignore[assignment]
        fake.cash = None  # type: ignore[assignment]
        fake.daytrade_count = None  # type: ignore[assignment]
        mock_trading_client.get_account.return_value = fake
        account = alpaca_client.get_account()
        assert account.equity == 0.0
        assert account.cash == 0.0
        assert account.daytrade_count == 0

    def test_api_error_raises_client_error(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.get_account.side_effect = APIError("auth failed")
        with pytest.raises(AlpacaClientError, match="auth failed"):
            alpaca_client.get_account()

    def test_to_dict_serialization(self, alpaca_client: AlpacaClient) -> None:
        account = alpaca_client.get_account()
        d = account.to_dict()
        assert isinstance(d, dict)
        assert d["equity"] == 100000.0
        assert d["daily_pnl"] == 200.0


class TestGetPositions:
    """Test position retrieval with float casting and edge cases."""

    def test_returns_all_positions(self, alpaca_client: AlpacaClient) -> None:
        positions = alpaca_client.get_positions()
        assert len(positions) == 2
        assert positions[0].symbol == "AAPL"
        assert positions[1].symbol == "NVDA"

    def test_float_casting_on_positions(self, alpaca_client: AlpacaClient) -> None:
        positions = alpaca_client.get_positions()
        aapl = positions[0]
        assert aapl.qty == 25.0
        assert aapl.avg_entry_price == 195.20
        assert aapl.current_price == 205.80
        assert aapl.market_value == 5145.0
        assert aapl.unrealized_pl == 265.0
        # unrealized_plpc is multiplied by 100 to get percentage
        assert abs(aapl.unrealized_pl_pct - 5.43) < 0.1

    def test_empty_positions(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.get_all_positions.return_value = []
        positions = alpaca_client.get_positions()
        assert positions == []

    def test_api_error_raises_client_error(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.get_all_positions.side_effect = APIError("timeout")
        with pytest.raises(AlpacaClientError, match="timeout"):
            alpaca_client.get_positions()


class TestGetPosition:
    """Test single position lookup."""

    def test_returns_position_when_exists(self, alpaca_client: AlpacaClient) -> None:
        pos = alpaca_client.get_position("AAPL")
        assert pos is not None
        assert pos.symbol == "AAPL"

    def test_returns_none_when_not_found(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.get_open_position.side_effect = APIError("position does not exist")
        pos = alpaca_client.get_position("XYZ")
        assert pos is None

    def test_uppercases_symbol(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        alpaca_client.get_position("aapl")
        mock_trading_client.get_open_position.assert_called_with("AAPL")

    def test_non_404_error_raises(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.get_open_position.side_effect = APIError("server error")
        with pytest.raises(AlpacaClientError, match="server error"):
            alpaca_client.get_position("AAPL")


class TestSubmitOrder:
    """Test order submission with all order types and error paths."""

    def test_market_order(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        order = alpaca_client.submit_order("AAPL", "buy", 10.0)
        assert order.order_id == "order-uuid-123"
        assert order.symbol == "AAPL"
        assert order.status == "accepted"
        mock_trading_client.submit_order.assert_called_once()

    def test_limit_order_requires_price(self, alpaca_client: AlpacaClient) -> None:
        with pytest.raises(ValueError, match="limit_price required"):
            alpaca_client.submit_order("AAPL", "buy", 10.0, order_type="limit")

    def test_limit_order_with_price(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        order = alpaca_client.submit_order(
            "AAPL",
            "buy",
            10.0,
            order_type="limit",
            limit_price=195.0,
        )
        assert order.order_id == "order-uuid-123"

    def test_stop_order_requires_price(self, alpaca_client: AlpacaClient) -> None:
        with pytest.raises(ValueError, match="stop_price required"):
            alpaca_client.submit_order("AAPL", "sell", 10.0, order_type="stop")

    def test_stop_limit_requires_both_prices(self, alpaca_client: AlpacaClient) -> None:
        with pytest.raises(ValueError, match="Both limit_price and stop_price"):
            alpaca_client.submit_order(
                "AAPL",
                "sell",
                10.0,
                order_type="stop_limit",
                limit_price=190.0,
            )

    def test_invalid_side_raises(self, alpaca_client: AlpacaClient) -> None:
        with pytest.raises(ValueError, match="Invalid side"):
            alpaca_client.submit_order("AAPL", "hold", 10.0)

    def test_invalid_order_type_raises(self, alpaca_client: AlpacaClient) -> None:
        with pytest.raises(ValueError, match="Invalid order_type"):
            alpaca_client.submit_order("AAPL", "buy", 10.0, order_type="bracket")

    def test_uppercases_symbol(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        alpaca_client.submit_order("aapl", "buy", 10.0)
        args = mock_trading_client.submit_order.call_args[0][0]
        assert args.symbol == "AAPL"

    def test_api_error_raises_client_error(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.submit_order.side_effect = APIError("insufficient buying power")
        with pytest.raises(AlpacaClientError, match="insufficient buying power"):
            alpaca_client.submit_order("AAPL", "buy", 10000.0)

    def test_order_info_maps_optional_prices(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.submit_order.return_value = FakeOrder(
            limit_price="195.00",
            stop_price=None,
            filled_avg_price=None,
            filled_at=None,
        )
        order = alpaca_client.submit_order(
            "AAPL",
            "buy",
            10.0,
            order_type="limit",
            limit_price=195.0,
        )
        assert order.limit_price == 195.0
        assert order.stop_price is None
        assert order.filled_avg_price is None
        assert order.filled_at is None


class TestGetOrders:
    """Test order listing with status filters."""

    def test_empty_orders(self, alpaca_client: AlpacaClient) -> None:
        orders = alpaca_client.get_orders()
        assert orders == []

    def test_returns_orders_list(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.get_orders.return_value = [
            FakeOrder(id="o1", symbol="AAPL"),
            FakeOrder(id="o2", symbol="NVDA"),
        ]
        orders = alpaca_client.get_orders(status="all")
        assert len(orders) == 2
        assert orders[0].order_id == "o1"
        assert orders[1].symbol == "NVDA"

    def test_defaults_to_open_status(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        alpaca_client.get_orders()
        call_args = mock_trading_client.get_orders.call_args
        from alpaca.trading.enums import QueryOrderStatus

        assert call_args.kwargs["filter"].status == QueryOrderStatus.OPEN


class TestClosePosition:
    """Test position closing."""

    def test_close_returns_order(self, alpaca_client: AlpacaClient) -> None:
        order = alpaca_client.close_position("AAPL")
        assert order.side == "sell"
        assert order.symbol == "AAPL"

    def test_uppercases_symbol(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        alpaca_client.close_position("aapl")
        mock_trading_client.close_position.assert_called_with("AAPL")

    def test_api_error_raises_client_error(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.close_position.side_effect = APIError("no position")
        with pytest.raises(AlpacaClientError, match="no position"):
            alpaca_client.close_position("XYZ")


class TestGetClock:
    """Test market clock retrieval."""

    def test_returns_clock_dict(self, alpaca_client: AlpacaClient) -> None:
        clock = alpaca_client.get_clock()
        assert clock["is_open"] is True
        assert "next_close" in clock
        assert "next_open" in clock

    def test_market_closed(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.get_clock.return_value = FakeClock(is_open=False)
        clock = alpaca_client.get_clock()
        assert clock["is_open"] is False


class TestCancelOrder:
    """Test order cancellation."""

    def test_cancel_calls_api(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        alpaca_client.cancel_order("order-123")
        mock_trading_client.cancel_order_by_id.assert_called_with("order-123")

    def test_api_error_raises(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.cancel_order_by_id.side_effect = APIError("not found")
        with pytest.raises(AlpacaClientError, match="not found"):
            alpaca_client.cancel_order("bad-id")


class TestTodaysFilledOrders:
    """Test today's filled order retrieval for risk tracking."""

    def test_empty_when_no_fills(self, alpaca_client: AlpacaClient) -> None:
        orders = alpaca_client.get_todays_filled_orders()
        assert orders == []

    def test_filters_to_filled_only(
        self, alpaca_client: AlpacaClient, mock_trading_client: MagicMock
    ) -> None:
        mock_trading_client.get_orders.return_value = [
            FakeOrder(id="o1", status="filled"),
            FakeOrder(id="o2", status="canceled"),
            FakeOrder(id="o3", status="filled"),
        ]
        orders = alpaca_client.get_todays_filled_orders()
        assert len(orders) == 2
        assert all(o.status == "filled" for o in orders)
