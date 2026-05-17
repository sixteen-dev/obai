"""End-to-end tests for trading scripts.

Tests run each script's main() function with mocked Alpaca client,
verifying JSON output, exit codes, and error handling.
"""

import json
import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from alpaca.common.exceptions import APIError

from .conftest import FakeAccount, FakeClock, FakeOrder, FakePosition


def _capture_stdout(func, args=None):
    """Run a function and capture its stdout output."""
    old_stdout = sys.stdout
    sys.stdout = StringIO()
    try:
        if args:
            with patch("sys.argv", args):
                func()
        else:
            func()
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return output


def _make_mock_client():
    """Create a standard mock TradingClient for script tests."""
    client = MagicMock()
    client.get_account.return_value = FakeAccount()
    client.get_all_positions.return_value = [
        FakePosition(symbol="AAPL"),
        FakePosition(symbol="NVDA", qty="10", market_value="8900.00"),
    ]
    client.get_clock.return_value = FakeClock()
    client.get_orders.return_value = []
    client.submit_order.return_value = FakeOrder()
    client.close_position.return_value = FakeOrder(side="sell")
    return client


class TestMarketHoursScript:
    """Test scripts/market_hours.py end-to-end."""

    def test_outputs_json_when_open(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.market_hours import main

            output = _capture_stdout(main)

        data = json.loads(output)
        assert data["is_open"] is True
        assert "next_close" in data

    def test_outputs_json_when_closed(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        mock_client.get_clock.return_value = FakeClock(is_open=False)
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.market_hours import main

            output = _capture_stdout(main)

        data = json.loads(output)
        assert data["is_open"] is False

    def test_exit_1_on_api_error(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        mock_client.get_clock.side_effect = APIError("connection refused")
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.market_hours import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


class TestGetPortfolioScript:
    """Test scripts/get_portfolio.py end-to-end."""

    def test_outputs_account_positions_risk(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.get_portfolio import main

            output = _capture_stdout(main)

        data = json.loads(output)
        assert "account" in data
        assert "positions" in data
        assert "risk" in data
        assert data["account"]["equity"] == 100000.0
        assert data["position_count"] == 2
        assert data["positions"][0]["symbol"] == "AAPL"

    def test_risk_status_included(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.get_portfolio import main

            output = _capture_stdout(main)

        data = json.loads(output)
        risk = data["risk"]
        assert "daily_trades_used" in risk
        assert "current_exposure_pct" in risk
        assert "max_position_pct" in risk

    def test_empty_portfolio(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        mock_client.get_all_positions.return_value = []
        mock_client.get_account.return_value = FakeAccount(
            equity="100000.00",
            long_market_value="0.00",
        )
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.get_portfolio import main

            output = _capture_stdout(main)

        data = json.loads(output)
        assert data["position_count"] == 0
        assert data["positions"] == []

    def test_exit_1_on_api_error(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        mock_client.get_account.side_effect = APIError("auth failed")
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.get_portfolio import main

            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1


class TestExecuteTradeScript:
    """Test scripts/execute_trade.py end-to-end."""

    def test_market_buy_succeeds(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        # Low exposure so risk check passes
        mock_client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        mock_client.get_all_positions.return_value = []
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.execute_trade import main

            output = _capture_stdout(
                main,
                args=[
                    "execute_trade.py",
                    "--symbol",
                    "AAPL",
                    "--side",
                    "buy",
                    "--qty",
                    "10",
                    "--limit-price",
                    "200.00",
                ],
            )

        data = json.loads(output)
        assert data["order_id"] == "order-uuid-123"
        assert data["status"] == "accepted"

    def test_market_buy_new_position_no_price_rejected(self, mock_env: None) -> None:
        """Market buy on new stock without --limit-price is rejected by risk checker."""
        mock_client = _make_mock_client()
        mock_client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        mock_client.get_all_positions.return_value = []
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.execute_trade import main

            with pytest.raises(SystemExit) as exc_info:
                _capture_stdout(
                    main,
                    args=["execute_trade.py", "--symbol", "AAPL", "--side", "buy", "--qty", "10"],
                )
            assert exc_info.value.code == 1

    def test_risk_rejection_outputs_error(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        # At daily trade limit
        mock_client.get_account.return_value = FakeAccount()
        mock_client.get_all_positions.return_value = []
        mock_client.get_orders.return_value = [FakeOrder(status="filled") for _ in range(20)]
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.execute_trade import main

            with pytest.raises(SystemExit) as exc_info:
                _capture_stdout(
                    main,
                    args=["execute_trade.py", "--symbol", "AAPL", "--side", "buy", "--qty", "10"],
                )
            assert exc_info.value.code == 1

    def test_limit_order_with_price(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        mock_client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        mock_client.get_all_positions.return_value = []
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.execute_trade import main

            output = _capture_stdout(
                main,
                args=[
                    "execute_trade.py",
                    "--symbol",
                    "AAPL",
                    "--side",
                    "buy",
                    "--qty",
                    "10",
                    "--order-type",
                    "limit",
                    "--limit-price",
                    "195.00",
                ],
            )

        data = json.loads(output)
        assert data["status"] == "accepted"

    def test_sell_within_existing_long_skips_position_checks(self, mock_env: None) -> None:
        """A sell that reduces an existing long is a pure reduction and
        does not need a limit price or exposure recheck."""
        mock_client = _make_mock_client()
        mock_client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="90000.00",
        )
        mock_client.get_all_positions.return_value = [
            FakePosition(symbol="AAPL", qty="100", side="long", current_price="195.00"),
        ]
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.execute_trade import main

            output = _capture_stdout(
                main,
                args=["execute_trade.py", "--symbol", "AAPL", "--side", "sell", "--qty", "10"],
            )

        data = json.loads(output)
        assert data["status"] == "accepted"

    def test_sell_opening_short_without_limit_price_rejected(
        self, mock_env: None
    ) -> None:
        """A sell that opens or grows a short must include --limit-price so
        the risk engine can size the resulting short position. Without a
        price, the order is rejected (see lib/risk.py:_sized_order)."""
        mock_client = _make_mock_client()
        mock_client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="0.00",
        )
        mock_client.get_all_positions.return_value = []
        with (
            patch("lib.alpaca_client.TradingClient", return_value=mock_client),
            pytest.raises(SystemExit) as exc_info,
        ):
            from scripts.execute_trade import main

            _capture_stdout(
                main,
                args=["execute_trade.py", "--symbol", "AAPL", "--side", "sell", "--qty", "10"],
            )
        assert exc_info.value.code == 1


class TestClosePositionScript:
    """Test scripts/close_position.py end-to-end."""

    def test_close_outputs_order(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.close_position import main

            output = _capture_stdout(
                main,
                args=["close_position.py", "--symbol", "AAPL"],
            )

        data = json.loads(output)
        assert data["side"] == "sell"
        assert data["order_id"] == "order-uuid-123"

    def test_close_nonexistent_position(self, mock_env: None) -> None:
        mock_client = _make_mock_client()
        mock_client.close_position.side_effect = APIError("no position for XYZ")
        with patch("lib.alpaca_client.TradingClient", return_value=mock_client):
            from scripts.close_position import main

            with pytest.raises(SystemExit) as exc_info:
                _capture_stdout(
                    main,
                    args=["close_position.py", "--symbol", "XYZ"],
                )
            assert exc_info.value.code == 1


class TestModels:
    """Test data model serialization and edge cases."""

    def test_account_info_to_dict(self) -> None:
        from lib.models import AccountInfo

        acct = AccountInfo(
            equity=100000.0,
            cash=25000.0,
            buying_power=25000.0,
            portfolio_value=100000.0,
            long_market_value=75000.0,
            short_market_value=0.0,
            last_equity=99800.0,
            daily_pnl=200.0,
            daytrade_count=0,
            pattern_day_trader=False,
        )
        d = acct.to_dict()
        assert d["equity"] == 100000.0
        assert d["daily_pnl"] == 200.0
        assert isinstance(d, dict)

    def test_position_info_to_dict(self) -> None:
        from lib.models import PositionInfo

        pos = PositionInfo(
            symbol="AAPL",
            qty=25.0,
            side="long",
            avg_entry_price=195.20,
            current_price=205.80,
            market_value=5145.0,
            cost_basis=4880.0,
            unrealized_pl=265.0,
            unrealized_pl_pct=5.43,
            unrealized_intraday_pl=50.0,
            change_today_pct=0.98,
        )
        d = pos.to_dict()
        assert d["symbol"] == "AAPL"
        assert d["unrealized_pl"] == 265.0

    def test_order_info_to_dict_with_nulls(self) -> None:
        from lib.models import OrderInfo

        order = OrderInfo(
            order_id="abc",
            symbol="AAPL",
            side="buy",
            qty=10.0,
            filled_qty=0.0,
            order_type="limit",
            status="new",
            limit_price=195.0,
            stop_price=None,
            filled_avg_price=None,
            time_in_force="day",
            submitted_at="2026-03-21T10:00:00Z",
            filled_at=None,
        )
        d = order.to_dict()
        assert d["limit_price"] == 195.0
        assert d["stop_price"] is None
        assert d["filled_at"] is None

    def test_risk_result_to_dict(self) -> None:
        from lib.models import RiskResult

        r = RiskResult(allowed=False, rejection_reason="Too big")
        d = r.to_dict()
        assert d["allowed"] is False
        assert d["rejection_reason"] == "Too big"

    def test_risk_status_to_dict(self) -> None:
        from lib.models import RiskStatus

        s = RiskStatus(
            daily_trades_used=3,
            daily_trades_limit=20,
            daily_pnl_pct=-0.5,
            daily_loss_limit_pct=3.0,
            current_exposure_pct=72.0,
            max_exposure_pct=90.0,
            max_position_pct=10.0,
        )
        d = s.to_dict()
        assert d["daily_trades_used"] == 3
        assert d["max_exposure_pct"] == 90.0
