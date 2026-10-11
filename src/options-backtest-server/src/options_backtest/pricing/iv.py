"""Implied volatility and the parity-implied forward, float64 (ADR 0002 §4, design §13.1).

A failed solve is a typed result with a reason, never a clamped or seeded value.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from options_backtest.pricing.european import black76, greeks

IV_LOWER: Final = 1e-4
IV_UPPER: Final = 5.0
IV_MAX_ITERATIONS: Final = 200
VEGA_FLOOR: Final = 1e-10
"""Black-76 vega per unit per 1.0 volatility below which a volatility is not identified."""
PARITY_STRIKES: Final = 5
PARITY_MIN_PAIRS: Final = 3
PARITY_MAX_IQR_FRACTION: Final = 0.005
_IV_RESIDUAL: Final = 1e-8
"""Design §13.1's R1 IV price residual: ``max(1e-8, 1e-8·|price|)`` premium units."""
_RIGHTS: Final = ("call", "put")


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
        ValueError: If an input is not finite, ``price`` is negative, ``forward``/``strike``/``df``
            is not positive or ``right`` is unknown; checked before any reason.
        TypeError: If a numeric input is not an ``int`` or ``float`` (``bool`` excluded).

    """
    _require_real({"price": price, "t": t}, positive=False)
    _require_real({"forward": forward, "strike": strike, "df": df}, positive=True)
    if price < 0:
        raise ValueError(f"price must be >= 0, got {price!r}")
    if right not in _RIGHTS:
        raise ValueError(f"right must be 'call' or 'put', got {right!r}")
    if t <= 0:
        return IvResult(None, IvReason.EXPIRED)
    lower, upper = _price_bounds(forward, strike, df, right)
    if price < lower:
        return IvResult(None, IvReason.BELOW_LOWER_BOUND)
    if price >= upper:
        return IvResult(None, IvReason.ABOVE_UPPER_BOUND)

    def residual(sigma: float) -> float:
        return black76(forward, strike, t, df, sigma, right) - price

    value = _bisect(residual, max(_IV_RESIDUAL, _IV_RESIDUAL * abs(price)))
    if value is None:
        return IvResult(None, IvReason.NO_ROOT)
    # Black-76 vega per unit is df·F·n(d1)·√t: the BSM vega, whatever the spot.
    if greeks(forward, forward, strike, t, df, value, right, 1.0).vega < VEGA_FLOOR:
        return IvResult(None, IvReason.VEGA_TOO_SMALL)
    return IvResult(value, None)


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
    DISPERSION. ``pairs_used`` holds the nearest pairs' strikes whatever the outcome, also the
    fewer than ``PARITY_MIN_PAIRS`` of TOO_FEW_PAIRS.

    Args:
        pairs: Every strike of one expiry whose call and put quotes are both VALID; distinct
            strikes.
        df: Discount factor to the expiry, > 0.
        spot: Contemporaneous index value, > 0.

    Returns:
        The forward or the reason it is unavailable.

    Raises:
        ValueError: On repeated strikes, a non-finite input or a non-positive ``df``/``spot``.
        TypeError: If a numeric input is not an ``int`` or ``float`` (``bool`` excluded).

    """
    _require_real({"df": df, "spot": spot}, positive=True)
    for index, pair in enumerate(pairs):
        fields = {"strike": pair.strike, "call_mid": pair.call_mid, "put_mid": pair.put_mid}
        _require_real({f"pairs[{index}].{k}": v for k, v in fields.items()}, positive=False)
    strikes = sorted(pair.strike for pair in pairs)
    if len(set(strikes)) != len(strikes):
        raise ValueError(f"pairs must have distinct strikes, got {strikes}")
    nearest = sorted(pairs, key=lambda pair: (abs(pair.strike - spot), pair.strike))
    nearest = nearest[:PARITY_STRIKES]
    used = tuple(sorted(pair.strike for pair in nearest))
    if len(nearest) < PARITY_MIN_PAIRS:
        return ForwardResult(None, ForwardReason.TOO_FEW_PAIRS, used)
    forwards = [pair.strike + (pair.call_mid - pair.put_mid) / df for pair in nearest]
    value = statistics.median(forwards)
    if value <= 0:
        return ForwardResult(None, ForwardReason.NONPOSITIVE_FORWARD, used)
    first_quartile, _, third_quartile = statistics.quantiles(forwards, n=4, method="inclusive")
    if third_quartile - first_quartile > PARITY_MAX_IQR_FRACTION * value:
        return ForwardResult(None, ForwardReason.DISPERSION, used)
    return ForwardResult(value, None, used)


def _price_bounds(forward: float, strike: float, df: float, right: str) -> tuple[float, float]:
    """Return the no-arbitrage (lower, upper) premium bounds of a European option."""
    if right == "call":
        return df * max(forward - strike, 0.0), df * forward
    return df * max(strike - forward, 0.0), df * strike


def _bisect(residual: Callable[[float], float], tolerance: float) -> float | None:
    """Return the first midpoint of ``[IV_LOWER, IV_UPPER]`` with ``|residual| <= tolerance``.

    ``residual`` increases with volatility. None when the interval does not bracket its root
    (``residual(IV_LOWER) <= 0 <= residual(IV_UPPER)`` fails) or when none of at most
    ``IV_MAX_ITERATIONS`` midpoints meets the tolerance.
    """
    if not residual(IV_LOWER) <= 0.0 <= residual(IV_UPPER):
        return None
    low, high = IV_LOWER, IV_UPPER
    for _ in range(IV_MAX_ITERATIONS):
        middle = (low + high) / 2.0
        miss = residual(middle)
        if abs(miss) <= tolerance:
            return middle
        if miss < 0.0:
            low = middle
        else:
            high = middle
    return None


def _require_real(values: Mapping[str, object], *, positive: bool) -> None:
    for field, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError(f"{field} must be a float, got {type(value).__name__}")
        if not math.isfinite(value):
            raise ValueError(f"{field} must be finite, got {value!r}")
        if positive and value <= 0:
            raise ValueError(f"{field} must be > 0, got {value!r}")
