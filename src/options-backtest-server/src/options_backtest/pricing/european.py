"""Black-76 European pricing and Greeks in float64 (ADR 0002 §4, design §13.1).

Time ``t`` is ACT/365F years from exact instants, ``(expires_at_ns - at_ns) / (365 · 86_400e9)``;
``df`` is the discount factor to expiry; ``forward`` the forward to expiry; the normal CDF is
``statistics.NormalDist``. ``right`` is ``"call"`` or ``"put"`` (``OptionType`` members pass),
so this module needs nothing from the domain model. Values cross into ``Decimal`` only through
``Decimal(float)`` at the caller.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from statistics import NormalDist
from typing import Final

_STANDARD_NORMAL: Final = NormalDist()
_DAYS_PER_YEAR: Final = 365.0
"""ACT/365F: theta is per calendar day of a 365-day year."""


@dataclass(frozen=True, slots=True)
class Greeks:
    """Canonical Greeks of one long contract (design §13.1), float64.

    Those of the Black-Scholes-Merton model with flat continuous rate ``r = -ln(df)/t`` and flat
    yield ``q = r - ln(forward/spot)/t``, each times ``multiplier``; QuantLib's analytic
    European engine on such a process is the reference.

    Attributes:
        delta: Dollars per 1.0 of spot, ``∂V/∂S`` with ``q`` fixed.
        gamma: Dollars per spot unit squared.
        theta: Dollars per calendar day of advancing valuation time, ``-(∂V/∂t)/365``.
        vega: Dollars per 1.0 absolute volatility.
        rho: Dollars per 1.0 absolute rate ``r``, with ``q`` fixed.

    """

    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


def black76(  # noqa: PLR0913, PLR0917 — signature fixed by ADR 0002 §4
    forward: float, strike: float, t: float, df: float, sigma: float, right: str
) -> float:
    """Return the Black-76 premium per unit of underlying.

    ``df·(F·N(d1) - K·N(d2))`` for a call and ``df·(K·N(-d2) - F·N(-d1))`` for a put, with
    ``d1 = (ln(F/K) + σ²t/2)/(σ√t)`` and ``d2 = d1 - σ√t``.

    Args:
        forward: Forward, > 0.
        strike: Strike, > 0.
        t: Years to expiry, > 0.
        df: Discount factor to expiry, > 0.
        sigma: Volatility per sqrt(year), > 0.
        right: ``"call"`` or ``"put"``.

    Returns:
        The premium per unit.

    Raises:
        ValueError: If an input is not finite, not positive, or ``right`` is unknown.
        TypeError: If a numeric input is not an ``int`` or ``float`` (``bool`` excluded).

    """
    is_call = _is_call(right)
    _require_positive({"forward": forward, "strike": strike, "t": t, "df": df, "sigma": sigma})
    d1, d2 = _d1_d2(forward, strike, t, sigma)
    cdf = _STANDARD_NORMAL.cdf
    if is_call:
        return df * (forward * cdf(d1) - strike * cdf(d2))
    return df * (strike * cdf(-d2) - forward * cdf(-d1))


def greeks(  # noqa: PLR0913, PLR0917 — signature fixed by ADR 0002 §4
    forward: float,
    spot: float,
    strike: float,
    t: float,
    df: float,
    sigma: float,
    right: str,
    multiplier: float,
) -> Greeks:
    """Return the canonical Greeks of one long contract.

    With ``e^(-qt) = df·F/S``, ``φ = sign`` (+1 call, -1 put) and ``n`` the normal density:
    delta ``φ·e^(-qt)·N(φ·d1)``, gamma ``e^(-qt)·n(d1)/(S·σ√t)``, vega ``df·F·n(d1)·√t``, rho
    ``φ·t·df·K·N(φ·d2)`` and theta ``(-df·F·n(d1)·σ/(2√t) - φ·r·df·K·N(φ·d2) +
    φ·q·df·F·N(φ·d1))/365``, each per unit times ``multiplier``.

    Args:
        forward: Forward, > 0.
        spot: Spot, > 0.
        strike: Strike, > 0.
        t: Years to expiry, > 0.
        df: Discount factor to expiry, > 0.
        sigma: Volatility, > 0.
        right: ``"call"`` or ``"put"``.
        multiplier: Premium multiplier of the contract, > 0.

    Returns:
        The Greeks.

    Raises:
        ValueError: As ``black76``, or for a non-positive spot or multiplier.
        TypeError: As ``black76``, also for ``spot`` and ``multiplier``.

    """
    sign = 1.0 if _is_call(right) else -1.0
    _require_positive(
        {
            "forward": forward,
            "spot": spot,
            "strike": strike,
            "t": t,
            "df": df,
            "sigma": sigma,
            "multiplier": multiplier,
        }
    )
    d1, d2 = _d1_d2(forward, strike, t, sigma)
    sqrt_t = math.sqrt(t)
    carry_df = df * forward / spot
    density = _STANDARD_NORMAL.pdf(d1)
    cdf_d1 = _STANDARD_NORMAL.cdf(sign * d1)
    cdf_d2 = _STANDARD_NORMAL.cdf(sign * d2)
    rate = -math.log(df) / t
    dividend_yield = rate - math.log(forward / spot) / t
    theta_per_year = (
        -df * forward * density * sigma / (2.0 * sqrt_t)
        - sign * rate * df * strike * cdf_d2
        + sign * dividend_yield * df * forward * cdf_d1
    )
    return Greeks(
        delta=multiplier * (sign * carry_df * cdf_d1),
        gamma=multiplier * (carry_df * density / (spot * sigma * sqrt_t)),
        theta=multiplier * (theta_per_year / _DAYS_PER_YEAR),
        vega=multiplier * (df * forward * density * sqrt_t),
        rho=multiplier * (sign * t * df * strike * cdf_d2),
    )


def spot_delta(  # noqa: PLR0913, PLR0917 — signature fixed by ADR 0002 §4
    forward: float, spot: float, strike: float, t: float, df: float, sigma: float, right: str
) -> float:
    """Return the normalized long-option spot delta a selector compares (design §9.1 item 2).

    ``df · (forward/spot) · (N(d1) - [right is put])``: calls in (0, 1), puts in (-1, 0). The
    put's ``N(d1) - 1`` is evaluated as ``-N(-d1)``, the same value without cancellation.

    Args:
        forward: Forward, > 0.
        spot: Spot, > 0.
        strike: Strike, > 0.
        t: Years to expiry, > 0.
        df: Discount factor to expiry, > 0.
        sigma: Volatility, > 0.
        right: ``"call"`` or ``"put"``.

    Returns:
        The signed delta per unit of underlying.

    Raises:
        ValueError: As ``black76``, or for a non-positive spot.
        TypeError: As ``black76``, also for ``spot``.

    """
    sign = 1.0 if _is_call(right) else -1.0
    _require_positive(
        {"forward": forward, "spot": spot, "strike": strike, "t": t, "df": df, "sigma": sigma}
    )
    d1, _ = _d1_d2(forward, strike, t, sigma)
    return sign * (df * forward / spot) * _STANDARD_NORMAL.cdf(sign * d1)


def _is_call(right: object) -> bool:
    if right == "call":
        return True
    if right == "put":
        return False
    raise ValueError(f"right must be 'call' or 'put', got {right!r}")


def _require_positive(values: Mapping[str, object]) -> None:
    for field, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError(f"{field} must be a float, got {type(value).__name__}")
        if not (math.isfinite(value) and value > 0):
            raise ValueError(f"{field} must be finite and > 0, got {value!r}")


def _d1_d2(forward: float, strike: float, t: float, sigma: float) -> tuple[float, float]:
    std_dev = sigma * math.sqrt(t)
    d1 = math.log(forward / strike) / std_dev + std_dev / 2.0
    return d1, d1 - std_dev
