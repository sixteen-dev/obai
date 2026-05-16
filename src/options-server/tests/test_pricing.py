"""Tests for Black-Scholes pricing engine."""

import math

import pytest

from src.engine.pricing import breakeven_at_expiry, bs_greeks, bs_price, implied_vol


class TestPutCallParity:
    """Verify put-call parity: C - P = S - K*exp(-rT)."""

    @pytest.mark.parametrize(
        ("spot", "strike", "time", "rate", "sigma"),
        [
            (100.0, 100.0, 1.0, 0.05, 0.20),
            (150.0, 140.0, 0.5, 0.03, 0.30),
            (50.0, 55.0, 0.25, 0.08, 0.40),
            (200.0, 200.0, 2.0, 0.045, 0.15),
        ],
    )
    def test_put_call_parity(
        self, spot: float, strike: float, time: float, rate: float, sigma: float
    ) -> None:
        call = bs_price(spot, strike, time, rate, sigma, "call")
        put = bs_price(spot, strike, time, rate, sigma, "put")
        expected = spot - strike * math.exp(-rate * time)

        assert call - put == pytest.approx(expected, abs=1e-8)


class TestDelta:
    """Delta behavior tests."""

    def test_atm_call_delta_approximately_half(self) -> None:
        # With r=0, ATM delta should be very close to 0.5
        greeks = bs_greeks(100.0, 100.0, 1.0, 0.0, 0.20, "call")
        assert greeks["delta"] == pytest.approx(0.5, abs=0.05)

    def test_deep_itm_call_delta_near_one(self) -> None:
        greeks = bs_greeks(200.0, 100.0, 1.0, 0.05, 0.20, "call")
        assert greeks["delta"] > 0.95

    def test_deep_otm_call_delta_near_zero(self) -> None:
        greeks = bs_greeks(50.0, 100.0, 1.0, 0.05, 0.20, "call")
        assert greeks["delta"] < 0.05


class TestGamma:
    """Gamma behavior tests."""

    def test_gamma_peaks_at_atm(self) -> None:
        gamma_atm = bs_greeks(100.0, 100.0, 0.25, 0.05, 0.20, "call")["gamma"]
        gamma_itm = bs_greeks(120.0, 100.0, 0.25, 0.05, 0.20, "call")["gamma"]
        gamma_otm = bs_greeks(80.0, 100.0, 0.25, 0.05, 0.20, "call")["gamma"]

        assert gamma_atm > gamma_itm
        assert gamma_atm > gamma_otm

    def test_gamma_positive(self) -> None:
        for opt_type in ("call", "put"):
            greeks = bs_greeks(100.0, 100.0, 1.0, 0.05, 0.20, opt_type)
            assert greeks["gamma"] > 0


class TestTheta:
    """Theta behavior tests."""

    def test_theta_negative_for_long_call(self) -> None:
        greeks = bs_greeks(100.0, 100.0, 1.0, 0.05, 0.20, "call")
        assert greeks["theta"] < 0

    def test_theta_negative_for_long_put(self) -> None:
        # For ATM puts, theta is generally negative (time decay)
        greeks = bs_greeks(100.0, 100.0, 1.0, 0.05, 0.20, "put")
        assert greeks["theta"] < 0


class TestVega:
    """Vega behavior tests."""

    def test_vega_positive(self) -> None:
        for opt_type in ("call", "put"):
            greeks = bs_greeks(100.0, 100.0, 1.0, 0.05, 0.20, opt_type)
            assert greeks["vega"] > 0

    def test_vega_same_for_call_and_put(self) -> None:
        call_greeks = bs_greeks(100.0, 100.0, 1.0, 0.05, 0.20, "call")
        put_greeks = bs_greeks(100.0, 100.0, 1.0, 0.05, 0.20, "put")
        assert call_greeks["vega"] == pytest.approx(put_greeks["vega"], abs=1e-10)


class TestImpliedVol:
    """Implied volatility solver tests."""

    @pytest.mark.parametrize(
        ("spot", "strike", "time", "rate", "sigma", "opt_type"),
        [
            (100.0, 100.0, 1.0, 0.05, 0.20, "call"),
            (100.0, 100.0, 1.0, 0.05, 0.20, "put"),
            (150.0, 140.0, 0.5, 0.03, 0.35, "call"),
            (50.0, 55.0, 0.25, 0.08, 0.50, "put"),
        ],
    )
    def test_iv_round_trip(
        self, spot: float, strike: float, time: float, rate: float, sigma: float, opt_type: str
    ) -> None:
        """price -> IV -> price should match within tolerance."""
        price = bs_price(spot, strike, time, rate, sigma, opt_type)
        recovered_vol = implied_vol(price, spot, strike, time, rate, opt_type)
        assert recovered_vol == pytest.approx(sigma, abs=1e-4)

    def test_iv_unreachable_price_returns_nan(self) -> None:
        """A market price above the no-arbitrage upper bound (~spot) is
        unreachable for any non-negative IV, so the bisection fallback
        cannot bracket a root and the solver returns NaN."""
        result = implied_vol(
            market_price=150.0,  # > spot=100, impossible for a call
            spot=100.0,
            strike=200.0,
            time=0.01,
            rate=0.05,
            option_type="call",
        )
        assert math.isnan(result)

    def test_iv_deep_otm_short_expiry_converges(self) -> None:
        """A deep-OTM short-expiry option with a tiny premium has a real
        (high) IV — the bisection fallback finds it even when Newton
        cannot. Regression test for the audit fix that added bracketing.
        """
        result = implied_vol(
            market_price=0.0001,
            spot=100.0,
            strike=200.0,
            time=0.01,
            rate=0.05,
            option_type="call",
        )
        assert not math.isnan(result)
        assert 0.5 < result < 5.0

    def test_iv_expired_returns_nan(self) -> None:
        result = implied_vol(5.0, 100.0, 100.0, 0.0, 0.05, "call")
        assert math.isnan(result)

    def test_iv_with_dividend_yield(self) -> None:
        """IV solver round-trips when a dividend yield is supplied."""
        sigma = 0.25
        spot, strike, time, rate, q = 100.0, 100.0, 0.5, 0.04, 0.02
        price = bs_price(spot, strike, time, rate, sigma, "call", dividend_yield=q)
        recovered = implied_vol(
            price, spot, strike, time, rate, "call", dividend_yield=q
        )
        assert recovered == pytest.approx(sigma, abs=1e-4)


class TestExpiredOption:
    """Expired option (T=0) behavior."""

    def test_expired_call_itm(self) -> None:
        price = bs_price(110.0, 100.0, 0.0, 0.05, 0.20, "call")
        assert price == pytest.approx(10.0, abs=1e-10)

    def test_expired_call_otm(self) -> None:
        price = bs_price(90.0, 100.0, 0.0, 0.05, 0.20, "call")
        assert price == pytest.approx(0.0, abs=1e-10)

    def test_expired_put_itm(self) -> None:
        price = bs_price(90.0, 100.0, 0.0, 0.05, 0.20, "put")
        assert price == pytest.approx(10.0, abs=1e-10)

    def test_expired_put_otm(self) -> None:
        price = bs_price(110.0, 100.0, 0.0, 0.05, 0.20, "put")
        assert price == pytest.approx(0.0, abs=1e-10)


class TestBreakeven:
    """Breakeven at expiry tests."""

    def test_breakeven_long_call(self) -> None:
        be = breakeven_at_expiry(100.0, 5.0, "call", "long")
        assert be == pytest.approx(105.0)

    def test_breakeven_long_put(self) -> None:
        be = breakeven_at_expiry(100.0, 5.0, "put", "long")
        assert be == pytest.approx(95.0)

    def test_breakeven_short_call(self) -> None:
        be = breakeven_at_expiry(100.0, 3.0, "call", "short")
        assert be == pytest.approx(103.0)

    def test_breakeven_short_put(self) -> None:
        be = breakeven_at_expiry(100.0, 3.0, "put", "short")
        assert be == pytest.approx(97.0)
