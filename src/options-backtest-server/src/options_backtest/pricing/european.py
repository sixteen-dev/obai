"""Black-76 European pricing and Greeks in float64 (ADR 0002 §4, design §13.1).

Time ``t`` is ACT/365F years from exact instants, ``(expires_at_ns - at_ns) / (365 · 86_400e9)``;
``df`` is the discount factor to expiry; ``forward`` the forward to expiry; the normal CDF is
``statistics.NormalDist``. ``right`` is ``"call"`` or ``"put"`` (``OptionType`` members pass),
so this module needs nothing from the domain model. Values cross into ``Decimal`` only through
``Decimal(float)`` at the caller.
"""

from __future__ import annotations

from dataclasses import dataclass


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

    """
    raise NotImplementedError


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

    """
    raise NotImplementedError


def spot_delta(  # noqa: PLR0913, PLR0917 — signature fixed by ADR 0002 §4
    forward: float, spot: float, strike: float, t: float, df: float, sigma: float, right: str
) -> float:
    """Return the normalized long-option spot delta a selector compares (design §9.1 item 2).

    ``df · (forward/spot) · (N(d1) - [right is put])``: calls in (0, 1), puts in (-1, 0).

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

    """
    raise NotImplementedError
