"""C25: float64 Black-76 price, Greeks, spot delta, implied vol and parity forward (design §13.1).

Written from design §13.1 and §18.2 C25, ADR 0002 §4 and §17 item 26 and the T0 stubs of
``pricing/european.py`` and ``pricing/iv.py``, before either is implemented; the oracle is
QuantLib (``conftest``), never the code under test.

Tolerances. The C25 row states no numbers of its own ("stated tolerances; low-vega/no-root
returns null; units checked by finite differences"); the one tolerance the design states here is
§13.1's R1 IV price residual ``max(1e-8, 1e-8·|price|)``, asserted as the solver's stopping rule.
Everything else uses the fallback: prices and Greeks within relative 1e-9 of QuantLib with an
absolute floor of 1e-12 (per unit for ``black76`` and ``spot_delta``, per contract of multiplier
100 for ``greeks``) and a round-trip implied volatility within 1e-6 of the true one. The
finite-difference check of QuantLib's Greek units uses relative 1e-5: it separates unit and
convention errors (per year vs per day, per 1% vs per 1.0, forward vs spot delta, ``F`` vs ``q``
held fixed), each off by 0.29% or more on its cases, and is not an accuracy claim.
"""

import math
from dataclasses import replace
from typing import Final

import pytest

from options_backtest.pricing.european import black76, greeks, spot_delta
from options_backtest.pricing.iv import (
    IV_LOWER,
    IV_UPPER,
    VEGA_FLOOR,
    ForwardReason,
    IvReason,
    ParityPair,
    implied_vol,
    parity_forward,
)

from .conftest import (
    MULTIPLIER,
    RIGHTS,
    SECONDS_PER_YEAR,
    PricingCase,
    make_case,
    pricing_grid,
    quantlib_values,
)

REL_TOL: Final = 1e-9
ABS_FLOOR: Final = 1e-12
IV_TOL: Final = 1e-6
IV_RESIDUAL: Final = 1e-8
FD_REL_TOL: Final = 1e-5
ONE_HOUR: Final = 3_600 / SECONDS_PER_YEAR
QUARTER: Final = 0.25
IV_FIRST_MIDPOINT: Final = (IV_LOWER + IV_UPPER) / 2


def case_id(case: PricingCase) -> str:
    """Return the pytest id of a case."""
    return case.label


def iv_residual_tolerance(price: float) -> float:
    """Return design §13.1's R1 IV price residual tolerance for ``price``."""
    return max(IV_RESIDUAL, IV_RESIDUAL * abs(price))


def iv_identifiable(case: PricingCase) -> bool:
    """Return whether the §13.1 residual moves the volatility by at most a tenth of IV_TOL."""
    reference = quantlib_values(case)
    return iv_residual_tolerance(reference.price) <= reference.vega * IV_TOL / 10


GRID: Final = pricing_grid()
IV_GRID: Final = tuple(case for case in GRID if iv_identifiable(case))
FD_GRID: Final = tuple(
    make_case(right, 5000.0, seconds, 0.05, 0.2, z)
    for right in RIGHTS
    for seconds in (30 * 86_400, 182 * 86_400, 730 * 86_400)
    for z in (-1.0, 0.0, 1.0)
)


def assert_close(actual: float, expected: float, what: str) -> None:
    """Assert ``actual`` is within REL_TOL of ``expected``, or within ABS_FLOOR if larger."""
    allowed = max(REL_TOL * abs(expected), ABS_FLOOR)
    assert abs(actual - expected) <= allowed, (
        f"{what}: {actual!r} vs QuantLib {expected!r}, allowed {allowed:.3g}"
    )


def quantlib_price(case: PricingCase, sigma: float) -> float:
    """Return QuantLib's premium per unit of ``case`` at volatility ``sigma``."""
    return quantlib_values(replace(case, sigma=sigma)).price


def iv_case(right: str, forward: float, strike: float, t: float, df: float) -> PricingCase:
    """Return a hand-built implied-vol input (spot = forward; sigma is filled per use)."""
    return PricingCase(right, forward, forward, strike, t, df, IV_FIRST_MIDPOINT, "iv")


def solve(price: float, case: PricingCase) -> tuple[float | None, IvReason | None]:
    """Return ``implied_vol``'s value and reason for ``price`` on ``case``'s inputs."""
    result = implied_vol(price, case.forward, case.strike, case.t, case.df, case.right)
    return result.value, result.reason


# --- Black-76 price, Greeks and spot delta against QuantLib -----------------------------------


@pytest.mark.parametrize("case", GRID, ids=case_id)
def test_black76_matches_quantlib(case: PricingCase) -> None:
    price = black76(case.forward, case.strike, case.t, case.df, case.sigma, case.right)
    assert_close(price, quantlib_values(case).price, "price per unit")


@pytest.mark.parametrize("case", GRID, ids=case_id)
def test_greeks_match_quantlib_per_long_contract(case: PricingCase) -> None:
    reference = quantlib_values(case)
    result = greeks(
        case.forward, case.spot, case.strike, case.t, case.df, case.sigma, case.right, MULTIPLIER
    )
    assert_close(result.delta, MULTIPLIER * reference.delta, "delta $/$")
    assert_close(result.gamma, MULTIPLIER * reference.gamma, "gamma $/$^2")
    assert_close(result.theta, MULTIPLIER * reference.theta, "theta $/calendar day")
    assert_close(result.vega, MULTIPLIER * reference.vega, "vega $/1.0 vol")
    assert_close(result.rho, MULTIPLIER * reference.rho, "rho $/1.0 rate")


@pytest.mark.parametrize("case", GRID, ids=case_id)
def test_spot_delta_matches_quantlib_delta(case: PricingCase) -> None:
    """``df·(F/S)·(N(d1) - [put])`` is QuantLib's BSM spot delta per unit, q fixed."""
    delta = spot_delta(
        case.forward, case.spot, case.strike, case.t, case.df, case.sigma, case.right
    )
    assert_close(delta, quantlib_values(case).delta, "spot delta")


def bumped_price(case: PricingCase, variable: str, step: float) -> float:
    """Return QuantLib's BSM price with ``variable`` (spot, rate, t or sigma) moved, q fixed.

    ``r = -ln(df)/t`` and ``q = r - ln(F/S)/t`` (ADR 0002 §17 item 26); the moved point is
    repriced at ``F = S·exp((r - q)·t)`` and ``df = exp(-r·t)``.
    """
    rate = -math.log(case.df) / case.t
    point = {"spot": case.spot, "rate": rate, "t": case.t, "sigma": case.sigma}
    point[variable] += step
    carry = point["rate"] - (rate - math.log(case.forward / case.spot) / case.t)
    moved = replace(
        case,
        spot=point["spot"],
        forward=point["spot"] * math.exp(carry * point["t"]),
        t=point["t"],
        df=math.exp(-point["rate"] * point["t"]),
        sigma=point["sigma"],
    )
    return quantlib_values(moved).price


def central_difference(case: PricingCase, variable: str, step: float) -> float:
    """Return the central difference of QuantLib's BSM price in ``variable``."""
    up = bumped_price(case, variable, step)
    down = bumped_price(case, variable, -step)
    return (up - down) / (2 * step)


@pytest.mark.parametrize("case", FD_GRID, ids=case_id)
def test_quantlib_greek_units_match_finite_differences(case: PricingCase) -> None:
    """C25 units: the oracle's Greeks are the BSM derivatives in the canonical units.

    Delta and gamma per 1.0 of spot, vega per 1.0 of volatility, rho per 1.0 of ``r`` with ``q``
    fixed, theta per calendar day of advancing valuation time (time to expiry falling).
    """
    reference = quantlib_values(case)
    h_spot = 1e-3 * case.spot * case.sigma * math.sqrt(case.t)
    base = bumped_price(case, "spot", 0.0)
    up = bumped_price(case, "spot", h_spot)
    down = bumped_price(case, "spot", -h_spot)
    differences = {
        "delta": (reference.delta, central_difference(case, "spot", h_spot)),
        "gamma": (reference.gamma, (up - 2 * base + down) / h_spot**2),
        "vega": (reference.vega, central_difference(case, "sigma", 1e-4 * case.sigma)),
        "rho": (reference.rho, central_difference(case, "rate", 1e-4)),
        "theta": (reference.theta, -central_difference(case, "t", 1e-4 * case.t) / 365),
    }
    for name, (greek, difference) in differences.items():
        assert abs(greek - difference) <= FD_REL_TOL * abs(greek), (name, greek, difference)


# --- implied_vol: round trip and every IvReason path, in order (§17 item 26) ------------------


def test_iv_grid_keeps_both_rights_scales_and_tenors() -> None:
    """Only low-vega corners leave the round-trip grid; every axis stays represented."""
    assert {case.right for case in IV_GRID} == set(RIGHTS)
    assert {case.spot for case in IV_GRID} == {5000.0, 500.0}
    assert {round(case.t * SECONDS_PER_YEAR) for case in IV_GRID} == {
        round(case.t * SECONDS_PER_YEAR) for case in GRID
    }


@pytest.mark.parametrize("case", IV_GRID, ids=case_id)
def test_implied_vol_recovers_sigma_from_quantlib_price(case: PricingCase) -> None:
    price = quantlib_values(case).price
    value, reason = solve(price, case)
    assert reason is None
    assert value is not None
    assert abs(value - case.sigma) <= IV_TOL
    repriced = black76(case.forward, case.strike, case.t, case.df, value, case.right)
    assert abs(repriced - price) <= iv_residual_tolerance(price)


@pytest.mark.parametrize("t", [0.0, -ONE_HOUR])
@pytest.mark.parametrize("price", [1.0, 600.0, 2500.0])
def test_implied_vol_expired_is_checked_first(t: float, price: float) -> None:
    """``t <= 0`` wins over a price below the lower bound (1), inside (600) or at the upper."""
    case = iv_case("call", 5000.0, 4000.0, t, 0.5)
    assert solve(price, case) == (None, IvReason.EXPIRED)


@pytest.mark.parametrize(
    ("right", "forward", "strike", "df", "price"),
    [("call", 5000.0, 4000.0, 0.5, 499.0), ("put", 500.0, 600.0, 1.0, 99.5)],
)
def test_implied_vol_below_lower_bound_precedes_no_root(
    right: str, forward: float, strike: float, df: float, price: float
) -> None:
    """Below ``df·max(±(F-K), 0)``; also below the IV_LOWER price, so NO_ROOT must not win."""
    case = iv_case(right, forward, strike, QUARTER, df)
    assert price < quantlib_price(case, IV_LOWER)
    assert solve(price, case) == (None, IvReason.BELOW_LOWER_BOUND)


def test_implied_vol_price_at_lower_bound_is_not_below_it() -> None:
    """The lower bound is strict: a price of exactly ``df·(F-K)`` is not BELOW_LOWER_BOUND."""
    case = iv_case("call", 5000.0, 4000.0, QUARTER, 0.5)
    _, reason = solve(500.0, case)
    assert reason is not IvReason.BELOW_LOWER_BOUND


@pytest.mark.parametrize(
    ("right", "forward", "strike", "price"),
    [
        ("call", 5000.0, 4000.0, 2500.0),
        ("call", 5000.0, 4000.0, 2600.0),
        ("put", 500.0, 600.0, 300.0),
        ("put", 500.0, 600.0, 450.0),
    ],
)
def test_implied_vol_at_or_above_upper_bound_precedes_no_root(
    right: str, forward: float, strike: float, price: float
) -> None:
    """At or above ``df·F`` (call) or ``df·K`` (put), df 0.5; above the IV_UPPER price too."""
    case = iv_case(right, forward, strike, QUARTER, 0.5)
    assert price > quantlib_price(case, IV_UPPER)
    assert solve(price, case) == (None, IvReason.ABOVE_UPPER_BOUND)


@pytest.mark.parametrize(
    ("strike", "t", "price"),
    [(5000.0, ONE_HOUR, 1e-3), (5000.0, 2.0, 4999.0), (6500.0, ONE_HOUR, 1.0)],
)
def test_implied_vol_outside_solver_bracket_is_no_root(
    strike: float, t: float, price: float
) -> None:
    """Inside the bounds but below the IV_LOWER or above the IV_UPPER Black-76 price."""
    case = iv_case("call", 5000.0, strike, t, 1.0)
    below = price < quantlib_price(case, IV_LOWER)
    above = price > quantlib_price(case, IV_UPPER)
    assert 0.0 < price < case.forward
    assert below or above
    assert solve(price, case) == (None, IvReason.NO_ROOT)


@pytest.mark.parametrize(("right", "strike"), [("call", 6500.0), ("put", 3800.0)])
def test_implied_vol_flat_price_is_vega_too_small(right: str, strike: float) -> None:
    """One hour, far OTM, price 1e-9: bracketed, solved at the first midpoint, vega under 1e-10.

    Every midpoint's price is below the 1e-8 residual, so bisection stops at the first one,
    ``(IV_LOWER + IV_UPPER)/2``, whose vega per unit is far below VEGA_FLOOR.
    """
    price = 1e-9
    case = iv_case(right, 5000.0, strike, ONE_HOUR, 1.0)
    assert quantlib_price(case, IV_LOWER) <= price <= quantlib_price(case, IV_UPPER)
    assert abs(quantlib_price(case, IV_FIRST_MIDPOINT) - price) <= iv_residual_tolerance(price)
    assert quantlib_values(case).vega < VEGA_FLOOR
    assert solve(price, case) == (None, IvReason.VEGA_TOO_SMALL)


# --- parity_forward: five nearest, >= 3 pairs, median, inclusive IQR, F > 0 -------------------


def pair(strike: float, forward: float) -> ParityPair:
    """Return a pair at ``strike`` whose parity forward at df 1 is exactly ``forward``."""
    gap = forward - strike
    return ParityPair(strike=strike, call_mid=20.0 + max(gap, 0.0), put_mid=20.0 + max(-gap, 0.0))


def test_parity_forward_takes_five_nearest_with_lower_strike_on_ties() -> None:
    """Spot 5002.5: 4990 and 5015 tie at 12.5 for the fifth place and 4990 wins.

    With 4990 the forwards are 4998..5002 (median 5000); 5015 would give median 5001, and the
    far pairs at 4000 and 6000 would widen the IQR past 0.5%.
    """
    pairs = [
        pair(6000.0, 1000.0),
        pair(5015.0, 5010.0),
        pair(5000.0, 5000.0),
        pair(4990.0, 4998.0),
        pair(5010.0, 5002.0),
        pair(4000.0, 9000.0),
        pair(4995.0, 4999.0),
        pair(5005.0, 5001.0),
    ]
    result = parity_forward(pairs, 1.0, 5002.5)
    assert (result.value, result.reason) == (5000.0, None)
    assert result.pairs_used == (4990.0, 4995.0, 5000.0, 5005.0, 5010.0)


def test_parity_forward_needs_three_pairs() -> None:
    two = [pair(4995.0, 5000.0), pair(5005.0, 5001.0)]
    too_few = parity_forward(two, 1.0, 5000.0)
    assert (too_few.value, too_few.reason) == (None, ForwardReason.TOO_FEW_PAIRS)
    three = parity_forward([*two, pair(5000.0, 5003.0)], 1.0, 5000.0)
    assert (three.value, three.reason) == (5001.0, None)
    assert three.pairs_used == (4995.0, 5000.0, 5005.0)


def test_parity_forward_median_of_four_and_inclusive_iqr() -> None:
    """Forwards 4970, 5000, 5000, 5030: median 5000; inclusive IQR 15 <= 25 accepts.

    The default exclusive method would give an IQR of 45 and reject.
    """
    pairs = [pair(4990.0, 4970.0), pair(4995.0, 5000.0), pair(5005.0, 5000.0), pair(5010.0, 5030.0)]
    result = parity_forward(pairs, 1.0, 5000.0)
    assert (result.value, result.reason) == (5000.0, None)
    assert result.pairs_used == (4990.0, 4995.0, 5005.0, 5010.0)


@pytest.mark.parametrize(
    ("high_quartile", "value", "reason"),
    [(5012.5, 5000.0, None), (5012.625, None, ForwardReason.DISPERSION)],
)
def test_parity_forward_rejects_iqr_above_half_percent(
    high_quartile: float, value: float | None, reason: ForwardReason | None
) -> None:
    """Five forwards, median 5000: IQR = Q3 - Q1 = 25 = 0.5% of F passes, 25.125 fails."""
    forwards = [4980.0, 4987.5, 5000.0, high_quartile, 5020.0]
    strikes = [4990.0, 4995.0, 5000.0, 5005.0, 5010.0]
    pairs = [pair(strike, forward) for strike, forward in zip(strikes, forwards, strict=True)]
    result = parity_forward(pairs, 1.0, 5000.0)
    assert (result.value, result.reason) == (value, reason)
    assert result.pairs_used == tuple(strikes)


@pytest.mark.parametrize("forwards", [(-30.0, -10.0, 5.0), (-1.0, 0.0, 1.0)])
def test_parity_forward_nonpositive_precedes_dispersion(forwards: tuple[float, ...]) -> None:
    """A median of -10 or exactly 0 is NONPOSITIVE_FORWARD although its IQR also exceeds 0.5%."""
    pairs = [
        pair(strike, forward) for strike, forward in zip((5.0, 10.0, 15.0), forwards, strict=True)
    ]
    result = parity_forward(pairs, 1.0, 10.0)
    assert (result.value, result.reason) == (None, ForwardReason.NONPOSITIVE_FORWARD)
    assert result.pairs_used == (5.0, 10.0, 15.0)


def quantlib_pair(strike: float, forward: float, df: float) -> ParityPair:
    """Return QuantLib's call and put premiums at ``strike`` as a pair (sigma 0.2, t 0.25)."""
    call = PricingCase("call", 5000.0, forward, strike, QUARTER, df, 0.2, "parity")
    put = replace(call, right="put")
    return ParityPair(strike, quantlib_values(call).price, quantlib_values(put).price)


def test_parity_forward_recovers_quantlib_forward_under_discounting() -> None:
    """QuantLib mids at F = 5000·exp(0.035/4), df = exp(-0.05/4): each ``K + (C - P)/df`` is F."""
    forward = 5000.0 * math.exp(0.035 * QUARTER)
    df = math.exp(-0.05 * QUARTER)
    pairs = [quantlib_pair(4900.0 + 25.0 * i, forward, df) for i in range(9)]
    result = parity_forward(pairs, df, 5000.0)
    assert result.reason is None
    assert result.value is not None
    assert_close(result.value, forward, "parity forward")
    assert result.pairs_used == (4950.0, 4975.0, 5000.0, 5025.0, 5050.0)
