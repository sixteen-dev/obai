"""Implied volatility and the parity-implied forward, float64 (ADR 0002 §4, design §13.1).

A failed solve is a typed result with a reason, never a clamped or seeded value.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

IV_LOWER: Final = 1e-4
IV_UPPER: Final = 5.0
IV_MAX_ITERATIONS: Final = 200
VEGA_FLOOR: Final = 1e-10
"""Black-76 vega per unit per 1.0 volatility below which a volatility is not identified."""
PARITY_STRIKES: Final = 5
PARITY_MIN_PAIRS: Final = 3
PARITY_MAX_IQR_FRACTION: Final = 0.005


class IvReason(StrEnum):
    """Why an implied volatility is unavailable."""

    EXPIRED = "EXPIRED"
    BELOW_LOWER_BOUND = "BELOW_LOWER_BOUND"
    ABOVE_UPPER_BOUND = "ABOVE_UPPER_BOUND"
    NO_ROOT = "NO_ROOT"
    VEGA_TOO_SMALL = "VEGA_TOO_SMALL"


@dataclass(frozen=True, slots=True)
class IvResult:
    """An implied volatility or the reason there is none.

    Attributes:
        value: Volatility per sqrt(year); None when unavailable.
        reason: None when ``value`` is present, else why not.

    """

    value: float | None
    reason: IvReason | None


def implied_vol(  # noqa: PLR0913, PLR0917 — signature fixed by ADR 0002 §4
    price: float, forward: float, strike: float, t: float, df: float, right: str
) -> IvResult:
    """Solve Black-76 for the volatility that reproduces ``price``.

    In order: ``t <= 0`` is EXPIRED; ``price`` below the lower bound ``df·max(F-K, 0)`` (call)
    or ``df·max(K-F, 0)`` (put) is BELOW_LOWER_BOUND; at or above the upper bound ``df·F``
    (call) or ``df·K`` (put) is ABOVE_UPPER_BOUND; a price outside
    ``[black76(IV_LOWER), black76(IV_UPPER)]`` is NO_ROOT. Otherwise bisection on
    ``[IV_LOWER, IV_UPPER]`` stops at the first midpoint whose price residual is at most
    ``max(1e-8, 1e-8·|price|)``; no such midpoint within ``IV_MAX_ITERATIONS`` is NO_ROOT, and a
    solution whose Black-76 vega per unit is below ``VEGA_FLOOR`` is VEGA_TOO_SMALL.

    Args:
        price: Observed premium per unit (a quote mid), >= 0.
        forward: Forward, > 0.
        strike: Strike, > 0.
        t: Years to expiry.
        df: Discount factor, > 0.
        right: ``"call"`` or ``"put"``.

    Returns:
        The volatility or the reason it is unavailable.

    Raises:
        ValueError: If an input is not finite, ``forward``/``strike``/``df`` is not positive or
            ``right`` is unknown.

    """
    raise NotImplementedError


class ForwardReason(StrEnum):
    """Why a parity-implied forward is unavailable."""

    TOO_FEW_PAIRS = "TOO_FEW_PAIRS"
    NONPOSITIVE_FORWARD = "NONPOSITIVE_FORWARD"
    DISPERSION = "DISPERSION"


@dataclass(frozen=True, slots=True)
class ParityPair:
    """Call and put mids at one strike, both from VALID quotes.

    Attributes:
        strike: Strike.
        call_mid: Call quote mid per unit.
        put_mid: Put quote mid per unit.

    """

    strike: float
    call_mid: float
    put_mid: float


@dataclass(frozen=True, slots=True)
class ForwardResult:
    """A parity-implied forward or the reason there is none.

    Attributes:
        value: Forward; None when unavailable.
        reason: None when ``value`` is present, else why not.
        pairs_used: Strikes of the pairs used, ascending.

    """

    value: float | None
    reason: ForwardReason | None
    pairs_used: tuple[float, ...]


def parity_forward(pairs: Sequence[ParityPair], df: float, spot: float) -> ForwardResult:
    """Return the median put-call-parity forward of the pairs nearest spot.

    Uses the ``PARITY_STRIKES`` pairs nearest ``spot`` by ``|strike - spot|`` (ties: lower
    strike first); fewer than ``PARITY_MIN_PAIRS`` is TOO_FEW_PAIRS. Each gives
    ``F_j = K_j + (call_mid - put_mid)/df``; the value is ``statistics.median`` of them. A median
    <= 0 is NONPOSITIVE_FORWARD; an inclusive-quantile interquartile range
    (``statistics.quantiles(n=4, method="inclusive")``) above ``PARITY_MAX_IQR_FRACTION × F`` is
    DISPERSION.

    Args:
        pairs: Every strike of one expiry whose call and put quotes are both VALID; distinct
            strikes.
        df: Discount factor to the expiry, > 0.
        spot: Contemporaneous index value, > 0.

    Returns:
        The forward or the reason it is unavailable.

    Raises:
        ValueError: On repeated strikes, a non-finite input or a non-positive ``df``/``spot``.

    """
    raise NotImplementedError
