"""Comprehensive tests for RiskChecker.

Tests cover: all four risk limit checks, edge cases (zero equity, exact limits),
stateless design (queries Alpaca each time), and configuration via env vars.
"""

import pytest

from lib.alpaca_client import AlpacaClient
from lib.models import AccountInfo, PositionInfo, RiskResult
from lib.risk import RiskChecker

from .conftest import FakeAccount, FakeOrder, FakePosition


def _make_account(
    equity: float = 100000.0,
    cash: float = 25000.0,
    buying_power: float = 25000.0,
    long_market_value: float = 75000.0,
    last_equity: float = 100000.0,
) -> AccountInfo:
    daily_pnl = round(equity - last_equity, 2)
    return AccountInfo(
        equity=equity,
        cash=cash,
        buying_power=buying_power,
        portfolio_value=equity,
        long_market_value=long_market_value,
        short_market_value=0.0,
        last_equity=last_equity,
        daily_pnl=daily_pnl,
        daytrade_count=0,
        pattern_day_trader=False,
    )


def _make_position(symbol: str, market_value: float, current_price: float = 200.0) -> PositionInfo:
    return PositionInfo(
        symbol=symbol,
        qty=market_value / current_price,
        side="long",
        avg_entry_price=current_price * 0.95,
        current_price=current_price,
        market_value=market_value,
        cost_basis=market_value * 0.95,
        unrealized_pl=market_value * 0.05,
        unrealized_pl_pct=5.0,
        unrealized_intraday_pl=0.0,
        change_today_pct=0.0,
    )


class TestRiskCheckerInit:
    """Test risk checker initialization and config."""

    def test_default_limits(self, alpaca_client: AlpacaClient) -> None:
        checker = RiskChecker(alpaca_client)
        assert checker.max_position_pct == 10.0
        assert checker.max_daily_trades == 20
        assert checker.max_daily_loss_pct == 3.0
        assert checker.max_exposure_pct == 90.0

    def test_env_var_overrides(
        self, alpaca_client: AlpacaClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MAX_POSITION_PCT", "15.0")
        monkeypatch.setenv("MAX_DAILY_TRADES", "5")
        monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "1.5")
        monkeypatch.setenv("MAX_EXPOSURE_PCT", "80.0")
        checker = RiskChecker(alpaca_client)
        assert checker.max_position_pct == 15.0
        assert checker.max_daily_trades == 5
        assert checker.max_daily_loss_pct == 1.5
        assert checker.max_exposure_pct == 80.0


class TestCheckOrderAllowed:
    """Test orders that should pass all risk checks."""

    def test_small_buy_within_all_limits(self, alpaca_client: AlpacaClient) -> None:
        """A small buy order with plenty of headroom passes all checks."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 10, limit_price=200.0)

        assert result.allowed is True
        assert result.rejection_reason is None

    def test_sell_within_existing_long_passes_size_checks(
        self, alpaca_client: AlpacaClient
    ) -> None:
        """Selling within an existing long is a pure reduction — no size check."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="90000.00",  # 90% exposure
        )
        alpaca_client._client.get_all_positions.return_value = [
            FakePosition(
                symbol="AAPL", qty="100", market_value="20000.00", current_price="200.00"
            ),
        ]
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # Existing 100 long shares; selling all 100 stays within long quantity.
        result = checker.check_order("AAPL", "sell", 100, limit_price=200.0)

        assert result.allowed is True


class TestDailyTradeLimit:
    """Test daily trade count circuit breaker."""

    def test_rejects_when_at_limit(self, alpaca_client: AlpacaClient) -> None:
        """Order rejected when daily trade count is at max."""
        alpaca_client._client.get_account.return_value = FakeAccount()
        alpaca_client._client.get_all_positions.return_value = []
        # Return 20 filled orders (at the limit)
        alpaca_client._client.get_orders.return_value = [
            FakeOrder(status="filled") for _ in range(20)
        ]

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 5, limit_price=200.0)

        assert result.allowed is False
        assert "Daily trade limit reached" in (result.rejection_reason or "")

    def test_allows_when_under_limit(self, alpaca_client: AlpacaClient) -> None:
        """Order allowed when trade count is under max."""
        alpaca_client._client.get_account.return_value = FakeAccount()
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = [
            FakeOrder(status="filled") for _ in range(19)
        ]

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 5, limit_price=200.0)

        assert result.allowed is True


class TestDailyLossLimit:
    """Test daily loss circuit breaker."""

    def test_rejects_when_loss_exceeds_limit(self, alpaca_client: AlpacaClient) -> None:
        """Stop trading when daily loss exceeds threshold."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="96500.00",
            last_equity="100000.00",  # -3.5% daily loss
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 5, limit_price=200.0)

        assert result.allowed is False
        assert "Daily loss limit breached" in (result.rejection_reason or "")

    def test_allows_when_loss_within_limit(self, alpaca_client: AlpacaClient) -> None:
        """Trading allowed when loss is within threshold."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="97500.00",
            last_equity="100000.00",  # -2.5% daily loss
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 5, limit_price=200.0)

        assert result.allowed is True

    def test_allows_when_profitable(self, alpaca_client: AlpacaClient) -> None:
        """No issues when account is up on the day."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="102000.00",
            last_equity="100000.00",  # +2% daily gain
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 5, limit_price=200.0)

        assert result.allowed is True


class TestPositionSizeLimit:
    """Test single position size as % of equity."""

    def test_rejects_oversized_new_position(self, alpaca_client: AlpacaClient) -> None:
        """New position that would exceed max_position_pct is rejected."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # 60 shares at $200 = $12,000 = 12% of equity (max is 10%)
        result = checker.check_order("AAPL", "buy", 60, limit_price=200.0)

        assert result.allowed is False
        assert "Position would be" in (result.rejection_reason or "")

    def test_allows_within_position_limit(self, alpaca_client: AlpacaClient) -> None:
        """New position within limit passes."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # 25 shares at $200 = $5,000 = 5% of equity (within 10% max)
        result = checker.check_order("AAPL", "buy", 25, limit_price=200.0)

        assert result.allowed is True

    def test_includes_existing_position_in_calculation(self, alpaca_client: AlpacaClient) -> None:
        """Adding to an existing position counts the total, not just the new order."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        # Already hold $8000 of AAPL
        alpaca_client._client.get_all_positions.return_value = [
            FakePosition(symbol="AAPL", market_value="8000.00", current_price="200.00"),
        ]
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # Adding 15 shares at $200 = $3000 more → total $11,000 = 11% (> 10%)
        result = checker.check_order("AAPL", "buy", 15, limit_price=200.0)

        assert result.allowed is False
        assert "Position would be" in (result.rejection_reason or "")


class TestExposureLimit:
    """Test total portfolio exposure as % of equity."""

    def test_rejects_when_exposure_exceeds_limit(self, alpaca_client: AlpacaClient) -> None:
        """Buy rejected when it would push exposure over max."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="88000.00",  # Already at 88%
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # $5000 more would push to 93% (> 90% max)
        result = checker.check_order("XYZ", "buy", 25, limit_price=200.0)

        assert result.allowed is False
        assert "Exposure would be" in (result.rejection_reason or "")

    def test_allows_when_exposure_within_limit(self, alpaca_client: AlpacaClient) -> None:
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="80000.00",  # 80%
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # $5000 more = 85% (< 90% max)
        result = checker.check_order("XYZ", "buy", 25, limit_price=200.0)

        assert result.allowed is True


class TestGetRiskStatus:
    """Test risk status snapshot (no order validation)."""

    def test_returns_current_utilization(self, alpaca_client: AlpacaClient) -> None:
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="99500.00",
            long_market_value="72000.00",
        )
        alpaca_client._client.get_orders.return_value = [
            FakeOrder(status="filled") for _ in range(3)
        ]

        checker = RiskChecker(alpaca_client)
        status = checker.get_risk_status()

        assert status.daily_trades_used == 3
        assert status.daily_trades_limit == 20
        assert status.daily_pnl_pct == 0.5  # (100000-99500)/100000*100
        assert status.daily_loss_limit_pct == 3.0
        assert status.current_exposure_pct == 72.0
        assert status.max_exposure_pct == 90.0

    def test_handles_zero_equity(self, alpaca_client: AlpacaClient) -> None:
        """No division by zero when equity is 0."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="0.00",
            last_equity="0.00",
            long_market_value="0.00",
        )
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        status = checker.get_risk_status()

        assert status.daily_pnl_pct == 0.0
        assert status.current_exposure_pct == 0.0

    def test_status_to_dict(self, alpaca_client: AlpacaClient) -> None:
        alpaca_client._client.get_account.return_value = FakeAccount()
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        status = checker.get_risk_status()
        d = status.to_dict()

        assert isinstance(d, dict)
        assert "daily_trades_used" in d
        assert "current_exposure_pct" in d


class TestRiskCheckPriorityOrder:
    """Test that risk checks are evaluated in the correct order."""

    def test_daily_trades_checked_before_position_size(self, alpaca_client: AlpacaClient) -> None:
        """If both trade limit and position limit would fail, trade limit is the rejection."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = [
            FakeOrder(status="filled") for _ in range(20)
        ]

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 1000, limit_price=200.0)

        assert result.allowed is False
        assert "Daily trade limit" in (result.rejection_reason or "")

    def test_daily_loss_checked_before_position_size(self, alpaca_client: AlpacaClient) -> None:
        """If both loss limit and position limit would fail, loss limit is the rejection."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="96000.00",
            last_equity="100000.00",  # -4%
            long_market_value="50000.00",
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 1000, limit_price=200.0)

        assert result.allowed is False
        assert "Daily loss limit" in (result.rejection_reason or "")


class TestMarketOrderNoPriceEstimate:
    """Test that market buy orders for new positions require a price estimate.

    This is the critical gotcha: without a limit_price and no existing position,
    the risk checker has no way to estimate position size. It must reject rather
    than silently pass with price_est=0.
    """

    def test_rejects_market_buy_new_position_no_price(
        self,
        alpaca_client: AlpacaClient,
    ) -> None:
        """Market buy of new stock with no limit_price must be rejected."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 10000)

        assert result.allowed is False
        assert "Cannot estimate position size" in (result.rejection_reason or "")

    def test_allows_market_buy_existing_position_no_price(
        self,
        alpaca_client: AlpacaClient,
    ) -> None:
        """Market buy adding to existing position uses current_price as fallback."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        alpaca_client._client.get_all_positions.return_value = [
            FakePosition(symbol="AAPL", market_value="3000.00", current_price="200.00"),
        ]
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # 10 shares at ~$200 = $2000 more → total $5000 = 5% (within 10% max)
        result = checker.check_order("AAPL", "buy", 10)

        assert result.allowed is True

    def test_allows_market_buy_new_position_with_limit_price(
        self,
        alpaca_client: AlpacaClient,
    ) -> None:
        """Market buy with explicit limit_price (as price estimate) passes normally."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="50000.00",
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 25, limit_price=200.0)

        assert result.allowed is True

    def test_sell_within_existing_long_no_price_estimate(
        self,
        alpaca_client: AlpacaClient,
    ) -> None:
        """Sell within an existing long needs no price estimate (pure reduction)."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
        )
        alpaca_client._client.get_all_positions.return_value = [
            FakePosition(
                symbol="AAPL", qty="100", market_value="20000.00", current_price="200.00"
            ),
        ]
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # Existing 100 long; selling 50 is a reduction, no short side opens.
        result = checker.check_order("AAPL", "sell", 50)

        assert result.allowed is True

    def test_rejects_sell_without_position_and_without_price(
        self,
        alpaca_client: AlpacaClient,
    ) -> None:
        """A sell with no existing long *and* no price would open an unbounded short."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "sell", 10000)

        assert result.allowed is False
        assert "short-position" in (result.rejection_reason or "")


class TestAlpacaStringCasting:
    """Test that all Alpaca string-to-float conversions work correctly.

    Alpaca returns strings for ALL numeric fields. This verifies the
    risk checker works correctly with the float-casted values from AlpacaClient.
    """

    def test_string_equity_values_cast_correctly(
        self,
        alpaca_client: AlpacaClient,
    ) -> None:
        """Verify risk calculations use float-casted values, not raw strings."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="99999.99",
            last_equity="100000.00",
            long_market_value="75000.50",
        )
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        status = checker.get_risk_status()

        # daily_pnl_pct should be a proper float calculation, not string comparison
        assert isinstance(status.daily_pnl_pct, float)
        assert status.daily_pnl_pct <= 0  # small loss (rounds to -0.0)
        assert isinstance(status.current_exposure_pct, float)
        assert status.current_exposure_pct > 0


class TestShortSideRiskChecks:
    """Sells that exceed the existing long quantity open or grow a short — the
    same size and exposure ceilings must apply on that side."""

    def test_rejects_short_that_breaches_position_limit(
        self,
        alpaca_client: AlpacaClient,
    ) -> None:
        """A sell with no existing long is a new short — position limit applies."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="0.00",
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # 60 * $200 = $12k = 12% short notional, breaches 10% position limit.
        result = checker.check_order("AAPL", "sell", 60, limit_price=200.0)

        assert result.allowed is False
        assert "Position would be" in (result.rejection_reason or "")

    def test_rejects_short_that_breaches_exposure_limit(
        self,
        alpaca_client: AlpacaClient,
    ) -> None:
        """Sell adding short exposure beyond max_exposure_pct is rejected."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="88000.00",  # already 88% gross
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # 25 * $200 = $5k short would push gross to 93% > 90%.
        result = checker.check_order("XYZ", "sell", 25, limit_price=200.0)

        assert result.allowed is False
        assert "Exposure would be" in (result.rejection_reason or "")

    def test_sell_exceeding_existing_long_only_checks_excess_short(
        self,
        alpaca_client: AlpacaClient,
    ) -> None:
        """Selling 150 of 100 held flips to a 50-share short — only excess
        contributes to new short risk."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="100000.00",
            last_equity="100000.00",
            long_market_value="20000.00",
        )
        alpaca_client._client.get_all_positions.return_value = [
            FakePosition(
                symbol="AAPL", qty="100", market_value="20000.00", current_price="200.00"
            ),
        ]
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # 50 excess * $200 = $10k = 10% — exactly at max_position_pct, passes.
        result = checker.check_order("AAPL", "sell", 150, limit_price=200.0)

        assert result.allowed is True


class TestQtyValidation:
    """The risk layer must reject non-positive and non-finite quantities."""

    def test_rejects_zero_qty(self, alpaca_client: AlpacaClient) -> None:
        alpaca_client._client.get_account.return_value = FakeAccount()
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", 0, limit_price=200.0)

        assert result.allowed is False
        assert "greater than zero" in (result.rejection_reason or "")

    def test_rejects_negative_qty(self, alpaca_client: AlpacaClient) -> None:
        alpaca_client._client.get_account.return_value = FakeAccount()
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", -5, limit_price=200.0)

        assert result.allowed is False
        assert "greater than zero" in (result.rejection_reason or "")

    def test_rejects_non_finite_qty(self, alpaca_client: AlpacaClient) -> None:
        alpaca_client._client.get_account.return_value = FakeAccount()
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        result = checker.check_order("AAPL", "buy", float("nan"), limit_price=200.0)

        assert result.allowed is False
        assert "finite" in (result.rejection_reason or "")


class TestZeroEquityEdgeCase:
    """Test behavior when account equity is zero (no division by zero)."""

    def test_check_order_with_zero_equity(
        self,
        alpaca_client: AlpacaClient,
    ) -> None:
        """Buy order with zero equity should not crash."""
        alpaca_client._client.get_account.return_value = FakeAccount(
            equity="0.00",
            last_equity="0.00",
            long_market_value="0.00",
        )
        alpaca_client._client.get_all_positions.return_value = []
        alpaca_client._client.get_orders.return_value = []

        checker = RiskChecker(alpaca_client)
        # Should not raise ZeroDivisionError
        result = checker.check_order("AAPL", "buy", 10, limit_price=200.0)

        # With zero equity, daily_pnl_pct = 0, passes loss check
        # But position size / exposure checks need equity > 0 guard
        assert isinstance(result, RiskResult)
