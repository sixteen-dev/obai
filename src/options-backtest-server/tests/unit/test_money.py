"""Exact money and price types (ADR 0001 §2, design §8.1)."""

import dataclasses
from decimal import (
    ROUND_HALF_EVEN,
    Clamped,
    Decimal,
    DivisionByZero,
    FloatOperation,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    getcontext,
    localcontext,
)

import pytest

from options_backtest.money import EXACT, Price, Usd


class _DecimalSubclass(Decimal):
    """A Decimal subclass; money types accept exactly ``Decimal``."""


def usd(text: str) -> Usd:
    return Usd(Decimal(text))


def price(text: str) -> Price:
    return Price(Decimal(text))


# --- EXACT context ---------------------------------------------------------------------------


def test_exact_context_has_the_adr_precision_rounding_and_traps() -> None:
    assert EXACT.prec == 50
    assert EXACT.rounding == ROUND_HALF_EVEN
    trapped = {signal for signal, enabled in EXACT.traps.items() if enabled}
    assert trapped == {Inexact, InvalidOperation, Overflow, DivisionByZero}
    assert not {Clamped, FloatOperation, Rounded, Subnormal, Underflow} & trapped


def test_exact_context_raises_inexact_on_one_third() -> None:
    with localcontext(EXACT), pytest.raises(Inexact):
        Decimal(1) / Decimal(3)


def test_exact_context_is_never_installed_as_the_thread_context() -> None:
    before = getcontext().prec

    total = usd("0.10") + usd("0.20")

    assert total == usd("0.30")
    assert getcontext() is not EXACT
    assert getcontext().prec == before


# --- Usd construction guards -----------------------------------------------------------------


@pytest.mark.parametrize("raw", [1, 1.5, "1.00", True, _DecimalSubclass("1")])
def test_usd_requires_exactly_decimal(raw: object) -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Usd(raw)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_usd_rejects_nonfinite_values(text: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        usd(text)


def test_usd_rejects_more_than_nine_decimal_places() -> None:
    with pytest.raises(ValueError, match="9 decimal places"):
        usd("0.0000000001")


def test_usd_rejects_an_extreme_exponent_without_expanding_it() -> None:
    with pytest.raises(ValueError, match="9 decimal places"):
        usd("1E-999999999")
    assert usd("0E-999999999").amount == 0


def test_usd_counts_decimal_places_by_value_not_by_representation() -> None:
    assert usd("1.0000000000") == usd("1")
    assert usd("0.000000001").amount == Decimal("0.000000001")


@pytest.mark.parametrize("text", ["1E+19", "-1E+19", "10000000000000000000.5"])
def test_usd_rejects_magnitudes_outside_decimal_28_9(text: str) -> None:
    with pytest.raises(ValueError, match=r"1e\+19"):
        usd(text)


@pytest.mark.parametrize(
    "text", ["9999999999999999999.999999999", "-9999999999999999999.999999999"]
)
def test_usd_accepts_the_largest_decimal_28_9_magnitude(text: str) -> None:
    assert usd(text).amount == Decimal(text)


@pytest.mark.parametrize("text", ["-0", "-0.00", "-0E+3"])
def test_usd_normalizes_negative_zero(text: str) -> None:
    amount = usd(text).amount

    assert amount == 0
    assert not amount.is_signed()


def test_usd_negative_zero_keeps_its_exponent() -> None:
    assert str(usd("-0.00").amount) == "0.00"


def test_usd_is_frozen_and_slotted() -> None:
    value = usd("1")

    with pytest.raises(dataclasses.FrozenInstanceError):
        value.amount = Decimal("2")  # type: ignore[misc]
    assert not hasattr(value, "__dict__")


# --- Usd value semantics and arithmetic ------------------------------------------------------


def test_usd_equality_and_hash_follow_value_not_representation() -> None:
    assert usd("1.0") == usd("1.00")
    assert hash(usd("1.0")) == hash(usd("1.00"))


def test_usd_is_ordered() -> None:
    values = [usd("2"), usd("-3"), usd("0.5")]

    assert sorted(values) == [usd("-3"), usd("0.5"), usd("2")]
    assert usd("1") < usd("2")
    assert usd("2") >= usd("2.00")


def test_usd_add_sub_and_negate_are_exact() -> None:
    assert usd("0.1") + usd("0.2") == usd("0.3")
    assert usd("10088.00") - usd("10000.00") == usd("88.00")
    assert -usd("90.00") == usd("-90.00")
    assert usd("0.000000001") + usd("1234567890.123456789") == usd("1234567890.123456790")


def test_usd_negating_zero_gives_unsigned_zero() -> None:
    assert not (-usd("0")).amount.is_signed()
    assert not (usd("-0") + usd("-0")).amount.is_signed()


def test_usd_sum_outside_decimal_28_9_raises() -> None:
    with pytest.raises(ValueError, match=r"1e\+19"):
        usd("9000000000000000000") + usd("1000000000000000000")


def test_usd_rejects_non_usd_operands_at_runtime() -> None:
    with pytest.raises(TypeError):
        usd("1") + Decimal("1")  # type: ignore[operator]
    with pytest.raises(TypeError):
        usd("1") - price("1")  # type: ignore[operator]
    with pytest.raises(TypeError):
        usd("1") * Decimal("2")  # type: ignore[operator]


@pytest.mark.parametrize(
    ("amount", "n", "expected"),
    [("1.10", 3, "3.30"), ("110.00", -1, "-110.00"), ("2.00", 0, "0"), ("0.000000001", 7, "7E-9")],
)
def test_usd_scaled_by_integer_is_exact(amount: str, n: int, expected: str) -> None:
    assert usd(amount).scaled_by(n) == usd(expected)


@pytest.mark.parametrize("n", [True, 2.0, Decimal("2")])
def test_usd_scaled_by_requires_an_int(n: object) -> None:
    with pytest.raises(TypeError, match="int"):
        usd("1").scaled_by(n)  # type: ignore[arg-type]


def test_usd_scaled_by_raises_inexact_rather_than_rounding() -> None:
    with pytest.raises(Inexact):
        usd("1234567890123456789.123456789").scaled_by(10**30 + 1)


def test_usd_scaled_by_outside_decimal_28_9_raises() -> None:
    with pytest.raises(ValueError, match=r"1e\+19"):
        usd("5000000000000000000").scaled_by(2)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1.23", True),
        ("100", True),
        ("0", True),
        ("-0.01", True),
        ("1.005", False),
        ("0.000000001", False),
    ],
)
def test_usd_is_cents(text: str, expected: bool) -> None:
    assert usd(text).is_cents() is expected


# --- Price -----------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [1, 1.5, "1.00", False, _DecimalSubclass("1")])
def test_price_requires_exactly_decimal(raw: object) -> None:
    with pytest.raises(TypeError, match="Decimal"):
        Price(raw)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["NaN", "Infinity"])
def test_price_rejects_nonfinite_values(text: str) -> None:
    with pytest.raises(ValueError, match="finite"):
        price(text)


def test_price_rejects_negative_values() -> None:
    with pytest.raises(ValueError, match=">= 0"):
        price("-0.01")


def test_price_normalizes_negative_zero() -> None:
    value = price("-0.00").value

    assert value == 0
    assert not value.is_signed()


def test_price_rejects_more_than_nine_decimal_places() -> None:
    with pytest.raises(ValueError, match="9 decimal places"):
        price("1.0000000001")


def test_price_rejects_magnitudes_outside_decimal_24_9() -> None:
    with pytest.raises(ValueError, match=r"1e\+15"):
        price("1E+15")
    assert price("999999999999999.999999999").value == Decimal("999999999999999.999999999")


def test_price_is_frozen_and_ordered() -> None:
    value = price("1.10")

    with pytest.raises(dataclasses.FrozenInstanceError):
        value.value = Decimal("2")  # type: ignore[misc]
    assert price("0.40") < price("1.10")
    assert price("1.1") == price("1.10")


def test_price_has_no_arithmetic_at_runtime() -> None:
    with pytest.raises(TypeError):
        price("1") + price("1")  # type: ignore[operator]
    with pytest.raises(TypeError):
        price("1") * Decimal("100")  # type: ignore[operator]


@pytest.mark.parametrize(
    ("bid", "ask", "expected"),
    [("2.00", "2.20", "2.10"), ("1.00", "1.01", "1.005"), ("0", "0.05", "0.025"), ("3", "3", "3")],
)
def test_price_mid_is_exact(bid: str, ask: str, expected: str) -> None:
    assert Price.mid(price(bid), price(ask)) == price(expected)


def test_price_mid_keeps_a_half_cent() -> None:
    assert Price.mid(price("1.00"), price("1.01")).value == Decimal("1.005")


def test_price_mid_raises_instead_of_rounding_past_nine_places() -> None:
    with pytest.raises(ValueError, match="9 decimal places"):
        Price.mid(price("0.000000001"), price("0.000000002"))


def test_price_mid_rejects_a_bid_above_the_ask() -> None:
    with pytest.raises(ValueError, match="bid"):
        Price.mid(price("2.20"), price("2.00"))
