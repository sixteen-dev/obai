"""Black-76 price, Greeks and spot delta: input guards and closed-form identities.

ADR 0002 §4 and §17 items 2 and 26, design §13.1. Accuracy against an independent oracle lives
in ``tests/reference/test_pricing_quantlib.py``; these tests pin the input contract, the
parity identities and the canonical Greek units by finite differences of ``black76`` itself.
"""

import math
from decimal import Decimal
from typing import Any, Final

import pytest

from options_backtest.models.market import OptionType
from options_backtest.pricing.european import Greeks, black76, greeks, spot_delta

PRICE_ARGS: Final[dict[str, Any]] = {
    "forward": 5000.0,
    "strike": 5050.0,
    "t": 0.25,
    "df": 0.99,
    "sigma": 0.2,
    "right": "call",
}
DELTA_ARGS: Final[dict[str, Any]] = {**PRICE_ARGS, "spot": 4990.0}
GREEK_ARGS: Final[dict[str, Any]] = {**DELTA_ARGS, "multiplier": 100.0}
NOT_POSITIVE: Final = (0.0, -0.0, -1.0, math.nan, math.inf, -math.inf)
NOT_REAL: Final = (True, Decimal("1"), "1", None)
UNKNOWN_RIGHTS: Final = ("Call", "CALL", "c", "", "straddle", None)
RIGHTS: Final = ("call", "put")
STRIKES: Final = (4500.0, 5000.0, 5600.0)


def with_arg(args: dict[str, Any], name: str, value: object) -> dict[str, Any]:
    return {**args, name: value}


# --- guards -----------------------------------------------------------------------------------


@pytest.mark.parametrize("value", NOT_POSITIVE)
@pytest.mark.parametrize("name", ["forward", "strike", "t", "df", "sigma"])
def test_black76_refuses_a_non_positive_or_non_finite_input(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=name):
        black76(**with_arg(PRICE_ARGS, name, value))


@pytest.mark.parametrize("value", NOT_REAL)
@pytest.mark.parametrize("name", ["forward", "strike", "t", "df", "sigma"])
def test_black76_refuses_a_non_float_input(name: str, value: object) -> None:
    with pytest.raises(TypeError, match=name):
        black76(**with_arg(PRICE_ARGS, name, value))


@pytest.mark.parametrize("right", UNKNOWN_RIGHTS)
def test_every_kernel_refuses_an_unknown_right(right: object) -> None:
    with pytest.raises(ValueError, match="right"):
        black76(**with_arg(PRICE_ARGS, "right", right))
    with pytest.raises(ValueError, match="right"):
        spot_delta(**with_arg(DELTA_ARGS, "right", right))
    with pytest.raises(ValueError, match="right"):
        greeks(**with_arg(GREEK_ARGS, "right", right))


@pytest.mark.parametrize("value", NOT_POSITIVE)
@pytest.mark.parametrize("name", ["forward", "spot", "strike", "t", "df", "sigma"])
def test_spot_delta_refuses_a_non_positive_or_non_finite_input(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=name):
        spot_delta(**with_arg(DELTA_ARGS, name, value))


@pytest.mark.parametrize("value", NOT_POSITIVE)
@pytest.mark.parametrize("name", ["forward", "spot", "strike", "t", "df", "sigma", "multiplier"])
def test_greeks_refuse_a_non_positive_or_non_finite_input(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=name):
        greeks(**with_arg(GREEK_ARGS, name, value))


@pytest.mark.parametrize("value", NOT_REAL)
@pytest.mark.parametrize("name", ["spot", "multiplier"])
def test_greeks_refuse_a_non_float_spot_or_multiplier(name: str, value: object) -> None:
    with pytest.raises(TypeError, match=name):
        greeks(**with_arg(GREEK_ARGS, name, value))


def test_integer_inputs_price_like_floats() -> None:
    assert black76(5000, 5050, 1, 1, 0.2, "call") == black76(5000.0, 5050.0, 1.0, 1.0, 0.2, "call")


def test_option_type_members_pass_as_the_right() -> None:
    for member in OptionType:
        assert black76(**with_arg(PRICE_ARGS, "right", member)) == black76(
            **with_arg(PRICE_ARGS, "right", member.value)
        )
        assert spot_delta(**with_arg(DELTA_ARGS, "right", member)) == spot_delta(
            **with_arg(DELTA_ARGS, "right", member.value)
        )
        assert greeks(**with_arg(GREEK_ARGS, "right", member)) == greeks(
            **with_arg(GREEK_ARGS, "right", member.value)
        )


# --- black76 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("right", RIGHTS)
def test_black76_at_the_money_is_the_closed_form(right: str) -> None:
    """F = K: both rights are ``df·F·(2N(σ√t/2) - 1) = df·F·erf(σ√t/(2√2))``."""
    price = black76(100.0, 100.0, 1.0, 0.95, 0.2, right)
    assert math.isclose(price, 0.95 * 100.0 * math.erf(0.1 / math.sqrt(2)), rel_tol=1e-14)


@pytest.mark.parametrize("df", [0.9, 1.0, 1.001])
@pytest.mark.parametrize("strike", STRIKES)
def test_black76_satisfies_put_call_parity(strike: float, df: float) -> None:
    """``C - P = df·(F - K)``; a DF above 1 (a negative bill yield) prices like any other."""
    call = black76(5000.0, strike, 0.5, df, 0.25, "call")
    put = black76(5000.0, strike, 0.5, df, 0.25, "put")
    assert math.isclose(call - put, df * (5000.0 - strike), rel_tol=1e-12, abs_tol=1e-9)


@pytest.mark.parametrize(("right", "strike"), [("call", 4000.0), ("put", 6000.0)])
def test_black76_of_a_deep_in_the_money_option_tends_to_discounted_intrinsic(
    right: str, strike: float
) -> None:
    price = black76(5000.0, strike, 0.25, 0.98, 0.01, right)
    assert price == pytest.approx(0.98 * abs(5000.0 - strike), rel=1e-15)


@pytest.mark.parametrize(("right", "strike"), [("call", 6000.0), ("put", 4000.0)])
def test_black76_of_a_far_out_of_the_money_option_underflows_to_zero(
    right: str, strike: float
) -> None:
    """One hour, |d| above 300: both normal tails underflow and the premium is exactly 0.0."""
    assert black76(5000.0, strike, 1 / (365 * 24), 1.0, 0.05, right) == 0.0


# --- spot delta and Greeks --------------------------------------------------------------------


@pytest.mark.parametrize("strike", STRIKES)
def test_spot_delta_is_signed_and_call_minus_put_is_df_forward_over_spot(strike: float) -> None:
    args = with_arg(DELTA_ARGS, "strike", strike)
    call = spot_delta(**with_arg(args, "right", "call"))
    put = spot_delta(**with_arg(args, "right", "put"))
    assert 0.0 < call < 1.0
    assert -1.0 < put < 0.0
    assert math.isclose(call - put, 0.99 * 5000.0 / 4990.0, rel_tol=1e-15)


@pytest.mark.parametrize("right", RIGHTS)
@pytest.mark.parametrize("strike", STRIKES)
def test_greeks_are_per_contract_and_delta_is_the_spot_delta(right: str, strike: float) -> None:
    args = with_arg(with_arg(GREEK_ARGS, "strike", strike), "right", right)
    per_contract = greeks(**args)
    per_unit = greeks(**with_arg(args, "multiplier", 1.0))
    for field in ("delta", "gamma", "theta", "vega", "rho"):
        scaled = 100.0 * getattr(per_unit, field)
        assert math.isclose(getattr(per_contract, field), scaled, rel_tol=1e-15), field
    unit_delta = spot_delta(**{key: args[key] for key in DELTA_ARGS})
    assert math.isclose(per_unit.delta, unit_delta, rel_tol=1e-15)


def bsm_inputs(spot: float, rate: float, carry: float, t: float) -> tuple[float, float]:
    """Return (forward, df) of flat ``r = rate`` and ``q = rate - carry`` (§17 item 26)."""
    return spot * math.exp(carry * t), math.exp(-rate * t)


@pytest.mark.parametrize("strike", STRIKES)
def test_call_minus_put_greeks_follow_parity(strike: float) -> None:
    """Differentiating ``C - P = S·e^(-qt) - K·e^(-rt)`` gives each Greek difference."""
    spot, rate, carry, t = 5000.0, 0.04, 0.025, 0.5
    forward, df = bsm_inputs(spot, rate, carry, t)
    call = greeks(forward, spot, strike, t, df, 0.25, "call", 100.0)
    put = greeks(forward, spot, strike, t, df, 0.25, "put", 100.0)
    dividend_yield = rate - carry
    assert math.isclose(call.delta - put.delta, 100.0 * df * forward / spot, rel_tol=1e-12)
    assert math.isclose(call.gamma, put.gamma, rel_tol=1e-12)
    assert math.isclose(call.vega, put.vega, rel_tol=1e-12)
    assert math.isclose(call.rho - put.rho, 100.0 * t * df * strike, rel_tol=1e-12)
    theta_gap = 100.0 * (dividend_yield * df * forward - rate * df * strike) / 365
    assert math.isclose(call.theta - put.theta, theta_gap, rel_tol=1e-9, abs_tol=1e-9)


def bsm_price(  # noqa: PLR0913, PLR0917 — one argument per BSM input
    right: str, spot: float, rate: float, carry: float, t: float, sigma: float, strike: float
) -> float:
    """Return ``black76`` of the BSM market with spot, rate and carry ``r - q`` held flat."""
    forward, df = bsm_inputs(spot, rate, carry, t)
    return black76(forward, strike, t, df, sigma, right)


def finite_difference_greeks(right: str, strike: float) -> Greeks:
    """Return per-contract Greeks (multiplier 100) by central differences of ``bsm_price``.

    Delta and gamma per 1.0 of spot, vega per 1.0 of volatility, rho per 1.0 of ``r`` with
    ``q`` fixed (so the carry moves with the rate), theta per calendar day of advancing
    valuation time (time to expiry falling).
    """
    spot, rate, carry, t, sigma = 5000.0, 0.04, 0.025, 0.5, 0.25
    base = bsm_price(right, spot, rate, carry, t, sigma, strike)
    up = bsm_price(right, spot + 1.0, rate, carry, t, sigma, strike)
    down = bsm_price(right, spot - 1.0, rate, carry, t, sigma, strike)
    h = 1e-5
    vega = bsm_price(right, spot, rate, carry, t, sigma + h, strike) - bsm_price(
        right, spot, rate, carry, t, sigma - h, strike
    )
    rho = bsm_price(right, spot, rate + h, carry + h, t, sigma, strike) - bsm_price(
        right, spot, rate - h, carry - h, t, sigma, strike
    )
    theta = bsm_price(right, spot, rate, carry, t - h, sigma, strike) - bsm_price(
        right, spot, rate, carry, t + h, sigma, strike
    )
    return Greeks(
        delta=100.0 * (up - down) / 2.0,
        gamma=100.0 * (up - 2.0 * base + down),
        theta=100.0 * theta / (2 * h) / 365,
        vega=100.0 * vega / (2 * h),
        rho=100.0 * rho / (2 * h),
    )


@pytest.mark.parametrize("right", RIGHTS)
@pytest.mark.parametrize("strike", STRIKES)
def test_greeks_have_the_canonical_units_by_finite_differences(right: str, strike: float) -> None:
    """Each Greek matches its derivative within 1e-5 relative; a unit slip is off by far more."""
    forward, df = bsm_inputs(5000.0, 0.04, 0.025, 0.5)
    analytic = greeks(forward, 5000.0, strike, 0.5, df, 0.25, right, 100.0)
    numeric = finite_difference_greeks(right, strike)
    for field in ("delta", "gamma", "theta", "vega", "rho"):
        expected = getattr(numeric, field)
        assert math.isclose(getattr(analytic, field), expected, rel_tol=1e-5), field
