"""Pricing discount factors from Treasury bill CMT points (ADR 0002 §3, design §13.1).

The 1-, 3- and 6-month CMT points are bill-based zero-coupon yields quoted bond-equivalent, so
each converts exactly as ``DF = 1 / (1 + y·n/365)``. Log discount factors are interpolated
linearly between brackets, from ``DF(0) = 1`` below the first tenor; beyond the last tenor the
input is unavailable. This module is a float64 leaf: it imports nothing from the package.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass
from decimal import Decimal
from itertools import pairwise
from typing import Final

CMT_CURVE_ID: Final = "UST_CMT"
"""Curve id of the Treasury bill CMT points; the only curve R1 reads."""


def bill_df(bey: Decimal, tenor_days: int) -> float:
    """Return a bill point's discount factor, ``1 / (1 + bey · tenor_days / 365)``, in float64.

    Args:
        bey: Bond-equivalent yield as a decimal ratio; ``1 + bey·n/365`` must be > 0.
        tenor_days: Tenor in calendar days, > 0.

    Returns:
        The discount factor; exactly 1.0 when ``bey`` is 0.

    Raises:
        TypeError: If ``bey`` is not exactly ``Decimal`` or ``tenor_days`` not exactly ``int``.
        ValueError: If ``bey`` is not finite, ``tenor_days <= 0`` or the denominator is not
            positive and finite.

    """
    if type(bey) is not Decimal:
        raise TypeError(f"bill_df bey must be exactly Decimal, got {type(bey).__name__}")
    if not bey.is_finite():
        raise ValueError(f"bill_df bey must be finite, got {bey}")
    if type(tenor_days) is not int:
        raise TypeError(f"bill_df tenor_days must be int, got {type(tenor_days).__name__}")
    if tenor_days <= 0:
        raise ValueError(f"bill_df tenor_days must be > 0, got {tenor_days}")
    denominator = 1.0 + float(bey) * tenor_days / 365
    if not (math.isfinite(denominator) and denominator > 0):
        raise ValueError(f"bill_df 1 + bey*n/365 must be positive, got {denominator}")
    return 1.0 / denominator


@dataclass(frozen=True, slots=True)
class DiscountCurve:
    """Discount factors at bill tenors, interpolated in log DF.

    Attributes:
        points: (tenor_days, df) pairs: tenors strictly ascending and > 0, each df > 0.

    """

    points: tuple[tuple[int, float], ...]

    def __post_init__(self) -> None:
        """Require a non-empty tuple of (int tenor > 0, finite float df > 0), tenors ascending."""
        if not isinstance(self.points, tuple):
            raise TypeError(
                f"DiscountCurve.points must be a tuple, got {type(self.points).__name__}"
            )
        if not self.points:
            raise ValueError("DiscountCurve.points must not be empty")
        for point in self.points:
            _require_point(point)
        for (lower, _), (upper, _) in pairwise(self.points):
            if upper <= lower:
                raise ValueError(
                    f"DiscountCurve tenors must ascend strictly, got {lower} then {upper}"
                )

    def df(self, t_days: float) -> float | None:
        """Return the discount factor ``t_days`` calendar days ahead.

        Linear in ``log(df)`` between the bracketing points, with (0, 1.0) the bracket below
        the first tenor; a tenor's own df at that tenor exactly.

        Args:
            t_days: Time to the cash flow in (fractional) calendar days, ``(expires_at_ns -
                at_ns) / 86_400e9``.

        Returns:
            The discount factor; None when ``t_days`` exceeds the last tenor.

        Raises:
            TypeError: If ``t_days`` is not an ``int`` or ``float`` (``bool`` excluded).
            ValueError: If ``t_days`` is negative or not finite.

        """
        if isinstance(t_days, bool) or not isinstance(t_days, int | float):
            raise TypeError(
                f"DiscountCurve.df t_days must be a number, got {type(t_days).__name__}"
            )
        if not (math.isfinite(t_days) and t_days >= 0):
            raise ValueError(f"DiscountCurve.df t_days must be finite and >= 0, got {t_days}")
        index = bisect_left(self.points, t_days, key=lambda point: point[0])
        if index == len(self.points):
            return None
        upper_tenor, upper_df = self.points[index]
        if upper_tenor == t_days:
            return upper_df
        lower_tenor, lower_df = (0, 1.0) if index == 0 else self.points[index - 1]
        weight = (t_days - lower_tenor) / (upper_tenor - lower_tenor)
        log_lower = math.log(lower_df)
        return math.exp(log_lower + weight * (math.log(upper_df) - log_lower))


def _require_point(point: object) -> None:
    if not isinstance(point, tuple):
        raise TypeError(f"DiscountCurve.points items must be tuples, got {type(point).__name__}")
    if len(point) != 2:  # noqa: PLR2004 — a (tenor_days, df) pair
        raise ValueError(f"DiscountCurve.points items must be (tenor_days, df), got {point!r}")
    tenor, df = point
    if type(tenor) is not int or type(df) is not float:
        raise TypeError(f"DiscountCurve point must be (int, float), got {point!r}")
    if tenor <= 0 or not (math.isfinite(df) and df > 0):
        raise ValueError(f"DiscountCurve point needs tenor > 0 and a finite df > 0, got {point!r}")
