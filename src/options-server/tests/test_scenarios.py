"""Tests for scenario analysis and position risk profiling."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from src.engine.pricing import bs_greeks, bs_price
from src.engine.scenarios import (
    _payoff_at_expiry,
    position_pnl_scenarios,
    position_risk_profile,
)
from src.server import _years_to_expiry, options_compute_greeks_tool

# A comfortably-future expiry so _years_to_expiry stays well above zero.
_FUTURE_EXPIRY = "2027-01-15"


def _expiry_in_days(days: int) -> str:
    """Expiry date `days` calendar days ahead in US market time (YYYY-MM-DD)."""
    today = datetime.now(tz=ZoneInfo("America/New_York")).date()
    return (today + timedelta(days=days)).isoformat()


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

    def test_grid_honors_range_and_time(self) -> None:
        """Grid must span the requested spot range and show time decay.

        With spot_range_pct=25 the spot axis must reach ±25% (not a fixed
        ±10%), and repricing at a forward horizon must move P&L for the same
        spot/vol cell (theta effect on a long call).
        """
        result = position_pnl_scenarios(
            current_price=100.0,
            strike=100.0,
            expiry_years=0.25,
            option_type="call",
            direction="long",
            quantity=1,
            entry_premium=self._fair_premium(),
            iv=0.30,
            spot_range_pct=25.0,
            days_forward=[30],
        )

        spot_changes = result["spot_changes"]
        assert isinstance(spot_changes, list)
        assert max(spot_changes) == pytest.approx(25.0)
        assert min(spot_changes) == pytest.approx(-25.0)

        grid_t0 = result["pnl_grid"]
        by_day = result["pnl_grid_by_day"]
        assert isinstance(grid_t0, list)
        assert isinstance(by_day, list)
        grid_later = by_day[0]["pnl_grid"]
        # Same ATM cell (0% spot idx 3, 0% vol idx 2): a long call bleeds theta,
        # so its P&L at a later horizon must differ from (and sit below) t0.
        assert grid_later[3][2] != grid_t0[3][2]
        assert grid_later[3][2] < grid_t0[3][2]


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
        """Short put with a low strike has a finite loss.

        The scan runs from a spot of zero, so the whole payoff is captured
        and the deepest loss is the strike less the premium.
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
        assert isinstance(result["max_loss"], float)
        assert result["max_loss"] == pytest.approx(-2990.0)

    def test_long_put_profit_is_bounded_by_a_zero_underlying(self) -> None:
        """A put's upside ends where the underlying does, at a spot of zero.

        The scan used to start at half the spot and treat that arbitrary floor
        as an open boundary, so any put still sloped there was reported as
        "unlimited". Nothing can fall below zero, so the true maximum is the
        strike less the premium.
        """
        contract: dict[str, object] = {
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
        result = position_risk_profile([contract])
        assert result["max_profit"] == pytest.approx(9600.0)
        assert result["max_loss"] == pytest.approx(-400.0)

    def test_short_put_loss_is_bounded_by_a_zero_underlying(self) -> None:
        """The mirror case: a short put's loss is capped, never unlimited."""
        contract: dict[str, object] = {
            "underlying_price": 100.0,
            "strike": 100.0,
            "expiry_years": 0.25,
            "option_type": "put",
            "direction": "short",
            "quantity": 1,
            "entry_premium": 4.0,
            "iv": 0.30,
            "risk_free_rate": 0.045,
        }
        result = position_risk_profile([contract])
        assert result["max_loss"] == pytest.approx(-9600.0)
        assert result["max_profit"] == pytest.approx(400.0)

    def test_long_straddle_keeps_unlimited_upside(self) -> None:
        """A put leg must not mask the call leg's open-ended upside.

        Scanning down to a spot of zero puts the straddle's global maximum on
        the put side, so a rule that asked whether the top of the scan held
        the maximum would report a finite profit for a position that has none.
        The test that matters is whether the payoff is still climbing there.
        """
        call: dict[str, object] = {
            "underlying_price": 100.0,
            "strike": 100.0,
            "expiry_years": 0.25,
            "option_type": "call",
            "direction": "long",
            "quantity": 1,
            "entry_premium": 4.0,
            "iv": 0.30,
            "risk_free_rate": 0.045,
        }
        put = dict(call, option_type="put")
        result = position_risk_profile([call, put])
        assert result["max_profit"] == "unlimited"
        assert result["max_loss"] == pytest.approx(-800.0)  # exact: both legs at the strike

    def test_short_straddle_keeps_unlimited_downside(self) -> None:
        """The mirror: a short call leg leaves the loss open-ended."""
        call: dict[str, object] = {
            "underlying_price": 100.0,
            "strike": 100.0,
            "expiry_years": 0.25,
            "option_type": "call",
            "direction": "short",
            "quantity": 1,
            "entry_premium": 4.0,
            "iv": 0.30,
            "risk_free_rate": 0.045,
        }
        put = dict(call, option_type="put")
        result = position_risk_profile([call, put])
        assert result["max_loss"] == "unlimited"
        assert result["max_profit"] == pytest.approx(800.0)  # exact: both legs at the strike

    def test_uppercase_call_payoff_matches_greeks(self) -> None:
        """Uppercase 'CALL' legs must compute a CALL payoff, not a put payoff.

        Greeks normalize case, so a 'CALL' leg gets call Greeks (positive
        delta). The raw string compare in _payoff_at_expiry saw 'CALL' !=
        'call' and computed the put payoff, contradicting those Greeks. The
        payoff slope above the strike must be POSITIVE (call profile).
        """
        strike = 100.0
        below = _payoff_at_expiry(strike + 5.0, strike, "CALL", "long", 1, 5.0)
        above = _payoff_at_expiry(strike + 15.0, strike, "CALL", "long", 1, 5.0)
        assert above > below  # long-call payoff rises as spot rises past strike

        contract: dict[str, object] = {
            "underlying_price": 100.0,
            "strike": strike,
            "expiry_years": 0.25,
            "option_type": "CALL",
            "direction": "long",
            "quantity": 1,
            "entry_premium": 5.0,
            "iv": 0.30,
            "risk_free_rate": 0.045,
        }
        result = position_risk_profile([contract])
        greeks = result["net_greeks"]
        assert isinstance(greeks, dict)
        assert greeks["delta"] > 0  # call Greeks
        assert result["max_profit"] == "unlimited"  # call upside is unbounded
        breakevens = result["breakevens"]
        assert isinstance(breakevens, list)
        assert breakevens[0] > strike  # call breakeven sits above the strike

    def test_multi_expiry_rejected_or_modeled(self) -> None:
        """A two-expiry calendar must be rejected, not silently collapsed.

        Legs at different expiries cannot be modeled by the single-expiry
        intrinsic payoff curve, so the engine must fail loud rather than
        fabricate max_profit/max_loss/breakevens.
        """
        near_leg: dict[str, object] = {
            "underlying_price": 100.0,
            "strike": 100.0,
            "expiry_years": 0.08,
            "option_type": "call",
            "direction": "short",
            "quantity": 1,
            "entry_premium": 2.0,
            "iv": 0.30,
            "risk_free_rate": 0.045,
        }
        far_leg = {**near_leg, "expiry_years": 0.50, "direction": "long", "entry_premium": 4.0}
        with pytest.raises(ValueError, match="multi-expiry"):
            position_risk_profile([near_leg, far_leg])


class TestComputeGreeksTool:
    """Tool-level tests for options_compute_greeks_tool.

    These exercise the dividend-yield threading and the implied-vol solver
    at the product surface (the engine already supports both; the tool did
    not reach them).
    """

    async def test_dividend_yield_changes_greeks(self) -> None:
        """A nonzero dividend yield must lower a call's delta and price.

        The exp(-qT) discount on spot (and the lower drift in d1) reduces
        both call delta and call price versus q=0. The tool must thread the
        dividend_yield through to bs_price/bs_greeks for this to show up.
        """
        base = await options_compute_greeks_tool(
            underlying_price=100.0,
            strike=100.0,
            expiry_date=_FUTURE_EXPIRY,
            option_type="call",
            volatility=0.30,
        )
        with_div = await options_compute_greeks_tool(
            underlying_price=100.0,
            strike=100.0,
            expiry_date=_FUTURE_EXPIRY,
            option_type="call",
            volatility=0.30,
            dividend_yield=0.05,
        )
        assert with_div["greeks"]["delta"] < base["greeks"]["delta"]
        assert with_div["price"] < base["price"]

    async def test_compute_greeks_solves_iv_from_market_price(self) -> None:
        """Supplying a market_price must yield a SOLVED IV, not the seed vol.

        With a seed vol of 0.20 and a market price well above the seed's BS
        value, the solved implied volatility must sit clearly above the seed;
        without a market price the field falls back to the seed vol.
        """
        seed_vol = 0.20
        echoed = await options_compute_greeks_tool(
            underlying_price=100.0,
            strike=100.0,
            expiry_date=_FUTURE_EXPIRY,
            option_type="call",
            volatility=seed_vol,
        )
        assert echoed["implied_volatility"] == pytest.approx(seed_vol)

        solved = await options_compute_greeks_tool(
            underlying_price=100.0,
            strike=100.0,
            expiry_date=_FUTURE_EXPIRY,
            option_type="call",
            volatility=seed_vol,
            market_price=15.0,
        )
        implied = solved["implied_volatility"]
        assert implied != pytest.approx(seed_vol)
        assert implied > seed_vol + 0.05

    async def test_price_and_greeks_use_the_solved_iv_not_the_seed(self) -> None:
        """The whole payload must describe one volatility: the solved IV.

        Reproduces the captured CORE-OPT-MATH call (spot 100, strike 100,
        60-day call, r=4%, q=3%, seed 30%, premium $5.50), which published
        price 4.8968 / delta 0.526987 — the seed-vol numbers — alongside a
        solved IV of 33.7641%. Priced at the solved IV the payload price is
        the supplied premium and delta sits above the seed-vol delta.
        """
        expiry = _expiry_in_days(60)
        seed_vol = 0.30
        result = await options_compute_greeks_tool(
            underlying_price=100.0,
            strike=100.0,
            expiry_date=expiry,
            option_type="call",
            volatility=seed_vol,
            risk_free_rate=0.04,
            dividend_yield=0.03,
            market_price=5.50,
        )
        seed_delta = bs_greeks(
            100.0, 100.0, _years_to_expiry(expiry), 0.04, seed_vol, "call", 0.03
        )["delta"]

        assert result["price"] == pytest.approx(5.50, abs=1e-3)
        assert result["greeks"]["delta"] != pytest.approx(seed_delta, abs=1e-5)
        assert result["greeks"]["delta"] > seed_delta
        assert result["breakeven"] == pytest.approx(100.0 + result["price"], abs=1e-3)

    async def test_seed_volatility_still_prices_when_no_market_price(self) -> None:
        """Without a market_price the payload stays at the seed, unchanged."""
        expiry = _expiry_in_days(60)
        seed_vol = 0.30
        result = await options_compute_greeks_tool(
            underlying_price=100.0,
            strike=100.0,
            expiry_date=expiry,
            option_type="call",
            volatility=seed_vol,
            risk_free_rate=0.04,
            dividend_yield=0.03,
        )
        time_to_expiry = _years_to_expiry(expiry)
        seed_price = bs_price(100.0, 100.0, time_to_expiry, 0.04, seed_vol, "call", 0.03)
        seed_delta = bs_greeks(100.0, 100.0, time_to_expiry, 0.04, seed_vol, "call", 0.03)["delta"]

        assert result["price"] == pytest.approx(seed_price, abs=1e-3)
        assert result["greeks"]["delta"] == pytest.approx(seed_delta, abs=1e-5)
        assert result["implied_volatility"] == pytest.approx(seed_vol)

    async def test_volatility_used_names_the_pricing_volatility(self) -> None:
        """volatility_used pins what priced the payload: solved IV, else seed."""
        expiry = _expiry_in_days(60)
        seed_vol = 0.30
        solved = await options_compute_greeks_tool(
            underlying_price=100.0,
            strike=100.0,
            expiry_date=expiry,
            option_type="call",
            volatility=seed_vol,
            risk_free_rate=0.04,
            dividend_yield=0.03,
            market_price=5.50,
        )
        echoed = await options_compute_greeks_tool(
            underlying_price=100.0,
            strike=100.0,
            expiry_date=expiry,
            option_type="call",
            volatility=seed_vol,
            risk_free_rate=0.04,
            dividend_yield=0.03,
        )

        assert solved["volatility_used"] == solved["implied_volatility"]
        assert solved["volatility_used"] != pytest.approx(seed_vol)
        assert echoed["volatility_used"] == pytest.approx(seed_vol)

    async def test_unsolvable_market_price_declares_the_seed_fallback(self) -> None:
        """An unreachable premium must not be laundered into a solved IV.

        market_price above the no-arbitrage bound leaves the solver with no
        root, so the payload reports no implied volatility, prices at the
        seed, and says which happened.
        """
        result = await options_compute_greeks_tool(
            underlying_price=100.0,
            strike=200.0,
            expiry_date=_expiry_in_days(60),
            option_type="call",
            volatility=0.30,
            market_price=150.0,
        )

        assert result["implied_volatility"] is None
        assert result["volatility_used"] == pytest.approx(0.30)
        assert "iv_solve_error" in result
