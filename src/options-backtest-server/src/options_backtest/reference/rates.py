"""Pricing discount factors from Treasury bill CMT points (ADR 0002 §3, design §13.1).

The 1-, 3- and 6-month CMT points are bill-based zero-coupon yields quoted bond-equivalent, so
each converts exactly as ``DF = 1 / (1 + y·n/365)``. Log discount factors are interpolated
linearly between brackets, from ``DF(0) = 1`` below the first tenor; beyond the last tenor the
input is unavailable. This module is a float64 leaf: it imports nothing from the package.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
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
        ValueError: If ``tenor_days <= 0`` or the denominator is not positive.

    """
    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class DiscountCurve:
    """Discount factors at bill tenors, interpolated in log DF.

    Attributes:
        points: (tenor_days, df) pairs: tenors strictly ascending and > 0, each df > 0.

    """

    points: tuple[tuple[int, float], ...]

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
            ValueError: If ``t_days`` is negative or not finite.

        """
        raise NotImplementedError
