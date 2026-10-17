"""Exact money and price values (ADR 0001 §2, design §8.1).

Every arithmetic operation runs under ``EXACT``, whose traps turn any rounding into
``decimal.Inexact``. ``EXACT`` is only ever entered through ``localcontext``; the thread's
default context (precision 28, silent rounding) is never used or replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Final, cast

EXACT: Final = Context(
    prec=50,
    rounding=ROUND_HALF_EVEN,
    traps=[Inexact, InvalidOperation, Overflow, DivisionByZero],
)

_MAX_PLACES: Final = 9
_CENT_PLACES: Final = 2
_USD_LIMIT: Final = Decimal("1e19")  # DECIMAL(28,9)
_PRICE_LIMIT: Final = Decimal("1e15")  # DECIMAL(24,9)


def _fits_places(value: Decimal, places: int) -> bool:
    """Return whether finite ``value`` is a whole multiple of ``10**-places``.

    Judged by value, not representation (``1.0000000000`` fits 9 places), using only the
    digit tuple so an extreme exponent cannot force a huge integer computation.
    """
    _, digits, exponent = value.as_tuple()
    excess = -places - cast(int, exponent)  # a finite Decimal always has an int exponent
    return excess <= 0 or not any(digits[-excess:])


def _checked_decimal(value: object, owner: str, limit: Decimal) -> Decimal:
    """Validate an exact, finite, at most 9-place decimal below ``limit`` in magnitude.

    Args:
        value: Candidate value; must be exactly ``Decimal`` (no subclass, int, float or str).
        owner: Type name used in error messages.
        limit: Exclusive bound on the absolute value.

    Returns:
        The value, with negative zero replaced by positive zero of the same exponent.

    Raises:
        TypeError: If ``value`` is not exactly ``Decimal``.
        ValueError: If it is not finite, too large or has more than 9 decimal places.

    """
    if type(value) is not Decimal:
        raise TypeError(f"{owner} requires exactly Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise ValueError(f"{owner} must be finite, got {value}")
    if value.copy_abs() >= limit:
        raise ValueError(f"{owner} magnitude must be < {limit:e}, got {value}")
    if not _fits_places(value, _MAX_PLACES):
        raise ValueError(f"{owner} allows at most 9 decimal places, got {value}")
    if value.is_zero():
        return value.copy_abs()
    return value


@dataclass(frozen=True, slots=True, order=True)
class Usd:
    """An exact signed US-dollar amount within DECIMAL(28,9).

    There is no ``Usd * Decimal``: the only price-to-money conversions are
    ``ContractTerms.premium_usd`` and ``Deliverable.value_usd``.

    Attributes:
        amount: Exactly ``Decimal``, finite, at most 9 decimal places, ``|amount| < 1e19``.

    """

    amount: Decimal

    def __post_init__(self) -> None:
        """Validate the amount and normalize negative zero."""
        object.__setattr__(self, "amount", _checked_decimal(self.amount, "Usd", _USD_LIMIT))

    def __add__(self, other: Usd) -> Usd:
        """Return the exact sum; raises ``ValueError`` if it leaves DECIMAL(28,9)."""
        if not isinstance(other, Usd):
            return NotImplemented
        with localcontext(EXACT):
            return Usd(self.amount + other.amount)

    def __sub__(self, other: Usd) -> Usd:
        """Return the exact difference; raises ``ValueError`` if it leaves DECIMAL(28,9)."""
        if not isinstance(other, Usd):
            return NotImplemented
        with localcontext(EXACT):
            return Usd(self.amount - other.amount)

    def __neg__(self) -> Usd:
        """Return the exact negation."""
        with localcontext(EXACT):
            return Usd(-self.amount)

    def scaled_by(self, n: int) -> Usd:
        """Return the amount times an integer count, exactly.

        Args:
            n: Integer multiplier (not ``bool``).

        Returns:
            ``amount * n``.

        Raises:
            TypeError: If ``n`` is not exactly ``int``.
            decimal.Inexact: If the product needs more than 50 significant digits.
            ValueError: If the product leaves DECIMAL(28,9).

        """
        if type(n) is not int:
            raise TypeError(f"Usd.scaled_by requires an int, got {type(n).__name__}")
        with localcontext(EXACT):
            return Usd(self.amount * n)

    def is_cents(self) -> bool:
        """Return whether the amount is a whole number of cents."""
        return _fits_places(self.amount, _CENT_PLACES)


ZERO_USD: Final = Usd(Decimal(0))


@dataclass(frozen=True, slots=True, order=True)
class Price:
    """An exact non-negative price within DECIMAL(24,9), in the instrument's quote units.

    A price has no arithmetic; it becomes money only through ``ContractTerms.premium_usd``
    or ``Deliverable.value_usd``.

    Attributes:
        value: Exactly ``Decimal``, finite, ``0 <= value < 1e15``, at most 9 decimal places.

    """

    value: Decimal

    def __post_init__(self) -> None:
        """Validate the value and normalize negative zero."""
        value = _checked_decimal(self.value, "Price", _PRICE_LIMIT)
        if value < 0:
            raise ValueError(f"Price must be >= 0, got {value}")
        object.__setattr__(self, "value", value)

    @staticmethod
    def mid(bid: Price, ask: Price) -> Price:
        """Return the exact midpoint of a bid and an ask.

        Args:
            bid: Bid price.
            ask: Ask price, at least ``bid``.

        Returns:
            ``(bid + ask) / 2``; a half-cent midpoint is kept exactly.

        Raises:
            ValueError: If ``bid > ask`` or the midpoint needs more than 9 decimal places.

        """
        if bid > ask:
            raise ValueError(f"mid requires bid <= ask, got bid {bid.value} > ask {ask.value}")
        with localcontext(EXACT):
            return Price((bid.value + ask.value) / 2)
