"""QuantLib oracle and the deterministic case grid of the pricing references (design C25).

QuantLib 1.43, a dev dependency imported only under ``tests/reference/``, is the independent
oracle of ADR 0002 §13. Its ``BlackCalculator`` takes the forward, ``σ√t`` and the discount
factor directly, so it prices sub-day maturities (this wheel has no intraday dates), and its spot
Greeks are those of Black-Scholes-Merton with flat ``r = -ln(df)/t`` and ``q = r - ln(F/S)/t``
held fixed (ADR 0002 §17 item 26): ``delta(S)``, ``gamma(S)``, ``thetaPerDay(S, t)`` (theta per
calendar day of advancing valuation time), ``vega(t)`` and ``rho(t)`` per 1.0 of volatility and
of rate. ``test_quantlib_greek_units_match_finite_differences`` confirms that reading from
QuantLib's own prices.

The grid crosses calls and puts; spot 5000 (SPX scale) and 500 (XSP scale); t from one hour to
two years in exact seconds over ACT/365F; df exactly 1.0 and ``exp(-0.05·t)``; sigma 0.05 to
1.5; strikes at ``ln(K/F) = z·σ√t`` for z in {-3, 0, 3}. Three standard deviations is deep in
or out of the money at every maturity (z = -3 is a deep ITM call and a deep OTM put) while each
normal probability stays at or above about 1e-5, which float64 ``statistics.NormalDist``
(the CDF ADR 0002 §4 mandates) resolves to about 1e-12 relative. The forward carries a 1.5%
dividend yield, so ``F != S`` and delta, theta and rho see a nonzero ``q``.
"""

import itertools
import math
from dataclasses import dataclass
from typing import Final

import QuantLib as ql  # type: ignore[import-untyped]

SECONDS_PER_YEAR: Final = 365 * 86_400
MULTIPLIER: Final = 100.0
DIVIDEND_YIELD: Final = 0.015
SPOTS: Final = (5000.0, 500.0)
TENOR_SECONDS: Final = (3_600, 86_400, 7 * 86_400, 30 * 86_400, 182 * 86_400, 730 * 86_400)
RATES: Final = (0.0, 0.05)
SIGMAS: Final = (0.05, 0.2, 0.6, 1.5)
MONEYNESS_Z: Final = (-3.0, 0.0, 3.0)
RIGHTS: Final = ("call", "put")


@dataclass(frozen=True, slots=True)
class PricingCase:
    """One pricing input in the stub's argument spelling.

    Attributes:
        right: ``"call"`` or ``"put"``.
        spot: Index value S.
        forward: Forward F to expiry.
        strike: Strike K.
        t: ACT/365F years to expiry.
        df: Discount factor to expiry.
        sigma: Volatility per sqrt(year).
        label: Test id.

    """

    right: str
    spot: float
    forward: float
    strike: float
    t: float
    df: float
    sigma: float
    label: str


@dataclass(frozen=True, slots=True)
class QuantLibValues:
    """QuantLib's Black value and BSM Greeks per unit of underlying (multiplier 1).

    Attributes:
        price: Premium.
        delta: ``∂V/∂S`` with ``q`` fixed.
        gamma: ``∂²V/∂S²``.
        theta: Per calendar day of advancing valuation time.
        vega: Per 1.0 volatility.
        rho: Per 1.0 rate ``r`` with ``q`` fixed.

    """

    price: float
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


def make_case(  # noqa: PLR0913, PLR0917 — one argument per grid axis
    right: str, spot: float, seconds: int, rate: float, sigma: float, z: float
) -> PricingCase:
    """Return the grid case for one parameter combination.

    Args:
        right: ``"call"`` or ``"put"``.
        spot: Index value.
        seconds: Exact seconds to expiry.
        rate: Continuous rate; 0.0 gives df exactly 1.0.
        sigma: Volatility.
        z: ``ln(K/F)`` in standard deviations ``σ√t``.

    Returns:
        The case.

    """
    t = seconds / SECONDS_PER_YEAR
    forward = spot * math.exp((rate - DIVIDEND_YIELD) * t)
    strike = forward * math.exp(z * sigma * math.sqrt(t))
    label = f"{right}-S{spot:g}-{seconds}s-r{rate:g}-vol{sigma:g}-z{z:+g}"
    return PricingCase(right, spot, forward, strike, t, math.exp(-rate * t), sigma, label)


def pricing_grid() -> tuple[PricingCase, ...]:
    """Return every grid case, in a fixed order."""
    axes = itertools.product(RIGHTS, SPOTS, TENOR_SECONDS, RATES, SIGMAS, MONEYNESS_Z)
    return tuple(make_case(*axis) for axis in axes)


def quantlib_values(case: PricingCase) -> QuantLibValues:
    """Return QuantLib's ``BlackCalculator`` value and Greeks for ``case``.

    Args:
        case: The pricing input.

    Returns:
        QuantLib's figures per unit.

    Raises:
        ValueError: If ``case.right`` is not ``"call"`` or ``"put"``.

    """
    if case.right not in RIGHTS:
        raise ValueError(f"unknown right {case.right!r}")
    option_type = ql.Option.Call if case.right == "call" else ql.Option.Put
    payoff = ql.PlainVanillaPayoff(option_type, case.strike)
    calc = ql.BlackCalculator(payoff, case.forward, case.sigma * math.sqrt(case.t), case.df)
    return QuantLibValues(
        price=float(calc.value()),
        delta=float(calc.delta(case.spot)),
        gamma=float(calc.gamma(case.spot)),
        theta=float(calc.thetaPerDay(case.spot, case.t)),
        vega=float(calc.vega(case.t)),
        rho=float(calc.rho(case.t)),
    )
