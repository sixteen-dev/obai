"""Tests for scenario analysis and position risk profiling."""

from src.engine.pricing import bs_price
from src.engine.scenarios import position_pnl_scenarios, position_risk_profile


class TestPnlScenarios:
    """P&L scenario grid tests."""

    @staticmethod
    def _fair_premium() -> float:
        """BS price at the test parameters, used as entry premium."""
        return bs_price(100.0, 100.0, 0.25, 0.045, 0.30, "call")

    def _make_result(self, contract_multiplier: int = 100) -> dict[str, object]:
        return position_pnl_scenarios(
            current_price=100.0,
            strike=100.0,
            expiry_years=0.25,
            option_type="call",
            direction="long",
            quantity=1,
            entry_premium=self._fair_premium(),
            iv=0.30,
            contract_multiplier=contract_multiplier,
        )

    def test_pnl_grid_dimensions(self) -> None:
        """Grid should be 7 spot x 5 vol = 35 cells."""
        result = self._make_result()
        grid = result["pnl_grid"]
        assert isinstance(grid, list)
        assert len(grid) == 7  # 7 spot changes
        for row in grid:
            assert isinstance(row, list)
            assert len(row) == 5  # 5 vol changes

    def test_pnl_at_zero_change(self) -> None:
        """At 0% spot and 0% vol change, P&L should be near zero."""
        result = self._make_result()
        grid = result["pnl_grid"]
        assert isinstance(grid, list)
        # 0% spot is index 3 ([-10, -5, -2, 0, ...])
        # 0% vol is index 2 ([-20, -10, 0, ...])
        pnl_at_zero = grid[3][2]
        # With 100x multiplier, tolerance scales to $50
        assert abs(pnl_at_zero) < 50.0

    def test_long_call_profits_on_upside(self) -> None:
        """Long call should profit when spot rises."""
        result = self._make_result()
        grid = result["pnl_grid"]
        assert isinstance(grid, list)
        # +10% spot (index 6), 0% vol (index 2) should be profitable
        pnl_upside = grid[6][2]
        assert pnl_upside > 0

    def test_long_call_loses_on_downside(self) -> None:
        """Long call should lose when spot drops significantly."""
        result = self._make_result()
        grid = result["pnl_grid"]
        assert isinstance(grid, list)
        # -10% spot (index 0), 0% vol (index 2) should be negative
        pnl_downside = grid[0][2]
        assert pnl_downside < 0

    def test_max_profit_and_loss_present(self) -> None:
        result = self._make_result()
        assert "max_profit" in result
        assert "max_loss" in result
        max_profit = result["max_profit"]
        max_loss = result["max_loss"]
        assert isinstance(max_profit, float)
        assert isinstance(max_loss, float)
        assert max_profit >= max_loss

    def test_contract_multiplier_scales_pnl(self) -> None:
        """Multiplier=100 should give 100x the P&L of multiplier=1."""
        result_1x = self._make_result(contract_multiplier=1)
        result_100x = self._make_result(contract_multiplier=100)

        grid_1x = result_1x["pnl_grid"]
        grid_100x = result_100x["pnl_grid"]
        assert isinstance(grid_1x, list)
        assert isinstance(grid_100x, list)

        # Check several cells: 100x multiplier should give 100x the P&L
        for row_idx in [0, 3, 6]:
            for col_idx in [0, 2, 4]:
                pnl_1x = grid_1x[row_idx][col_idx]
                pnl_100x = grid_100x[row_idx][col_idx]
                assert isinstance(pnl_1x, float)
                assert isinstance(pnl_100x, float)
                # Allow small rounding tolerance
                assert abs(pnl_100x - pnl_1x * 100) < 1.0


class TestRiskProfile:
    """Position risk profile tests."""

    @staticmethod
    def _long_call_contract() -> dict[str, object]:
        return {
            "underlying_price": 100.0,
            "strike": 100.0,
            "expiry_years": 0.25,
            "option_type": "call",
            "direction": "long",
            "quantity": 1,
            "entry_premium": 5.0,
            "iv": 0.30,
            "risk_free_rate": 0.045,
        }

    @staticmethod
    def _long_put_contract() -> dict[str, object]:
        return {
            "underlying_price": 100.0,
            "strike": 100.0,
            "expiry_years": 0.25,
            "option_type": "put",
            "direction": "long",
            "quantity": 1,
            "entry_premium": 4.0,
            "iv": 0.30,
            "risk_free_rate": 0.045,
        }

    @staticmethod
    def _short_call_contract() -> dict[str, object]:
        return {
            "underlying_price": 100.0,
            "strike": 105.0,
            "expiry_years": 0.25,
            "option_type": "call",
            "direction": "short",
            "quantity": 1,
            "entry_premium": 3.0,
            "iv": 0.30,
            "risk_free_rate": 0.045,
        }

    @staticmethod
    def _short_put_contract() -> dict[str, object]:
        return {
            "underlying_price": 100.0,
            "strike": 95.0,
            "expiry_years": 0.25,
            "option_type": "put",
            "direction": "short",
            "quantity": 1,
            "entry_premium": 2.0,
            "iv": 0.30,
            "risk_free_rate": 0.045,
        }

    def test_risk_profile_long_call(self) -> None:
        """Long call: positive delta, negative theta (scaled by 100x multiplier)."""
        result = position_risk_profile([self._long_call_contract()])
        greeks = result["net_greeks"]
        assert isinstance(greeks, dict)
        assert greeks["delta"] > 0
        assert greeks["theta"] < 0
        # With 100x multiplier, delta should be much larger than raw per-share delta
        assert greeks["delta"] > 1.0  # Raw delta ~0.5-0.6, times 100 = 50+

    def test_risk_profile_straddle(self) -> None:
        """Long straddle (call + put, same strike): delta near 0, gamma > 0."""
        contracts = [self._long_call_contract(), self._long_put_contract()]
        result = position_risk_profile(contracts)
        greeks = result["net_greeks"]
        assert isinstance(greeks, dict)
        # With 100x multiplier, tolerance scales up
        assert abs(greeks["delta"]) < 15.0  # Near zero (drift from r > 0)
        assert greeks["gamma"] > 0

    def test_breakeven_detection(self) -> None:
        """A long call should have at least one breakeven point."""
        result = position_risk_profile([self._long_call_contract()])
        breakevens = result["breakevens"]
        assert isinstance(breakevens, list)
        assert len(breakevens) >= 1
        # Breakeven should be above the strike (call)
        assert breakevens[0] > 100.0

    def test_straddle_two_breakevens(self) -> None:
        """A long straddle should have two breakevens."""
        contracts = [self._long_call_contract(), self._long_put_contract()]
        result = position_risk_profile(contracts)
        breakevens = result["breakevens"]
        assert isinstance(breakevens, list)
        assert len(breakevens) == 2
        # Lower breakeven < strike < upper breakeven
        assert breakevens[0] < 100.0
        assert breakevens[1] > 100.0

    def test_long_call_unlimited_profit(self) -> None:
        """Long call max_profit should be 'unlimited'."""
        result = position_risk_profile([self._long_call_contract()])
        assert result["max_profit"] == "unlimited"

    def test_risk_profile_returns_all_keys(self) -> None:
        result = position_risk_profile([self._long_call_contract()])
        assert "net_greeks" in result
        assert "max_profit" in result
        assert "max_loss" in result
        assert "breakevens" in result

    def test_short_call_unlimited_loss(self) -> None:
        """Short call max_loss should be 'unlimited' (loss grows as price rises)."""
        result = position_risk_profile([self._short_call_contract()])
        assert result["max_loss"] == "unlimited"

    def test_short_put_finite_loss(self) -> None:
        """Short put with low strike has finite loss within the scan range.

        A short put at strike=30 with underlying=100 means the scan range
        [50, 150] fully captures the payoff. At spot=50 the put is far OTM
        so the payoff curve has flattened — max_loss is detected as finite
        (not 'unlimited').
        """
        contract: dict[str, object] = {
            "underlying_price": 100.0,
            "strike": 30.0,
            "expiry_years": 0.25,
            "option_type": "put",
            "direction": "short",
            "quantity": 1,
            "entry_premium": 0.10,
            "iv": 0.30,
            "risk_free_rate": 0.045,
        }
        result = position_risk_profile([contract])
        # Strike=30 is far below the scan floor (50), so max loss is finite
        assert isinstance(result["max_loss"], float)
        assert result["max_loss"] != "unlimited"
