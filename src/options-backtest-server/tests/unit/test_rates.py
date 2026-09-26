"""Bill discount factors and log-DF interpolation (ADR 0002 §3, §17 item 16, design §13.1)."""

import math
from decimal import Decimal
from typing import Any

import pytest

from options_backtest.reference.rates import CMT_CURVE_ID, DiscountCurve, bill_df

CURVE = DiscountCurve(((28, 0.996), (91, 0.987), (182, 0.975)))


def test_curve_id_is_the_treasury_cmt() -> None:
    assert CMT_CURVE_ID == "UST_CMT"


@pytest.mark.parametrize("tenor_days", [1, 28, 91, 182])
def test_a_zero_rate_is_exactly_one(tenor_days: int) -> None:
    assert bill_df(Decimal(0), tenor_days) == 1.0
    assert bill_df(Decimal("0.000"), tenor_days) == 1.0


@pytest.mark.parametrize(("bey", "tenor_days"), [("0.0525", 28), ("0.051", 91), ("-0.002", 182)])
def test_bill_df_is_one_over_one_plus_simple_yield(bey: str, tenor_days: int) -> None:
    assert bill_df(Decimal(bey), tenor_days) == 1 / (1 + float(bey) * tenor_days / 365)


@pytest.mark.parametrize(
    ("bey", "tenor_days", "error"),
    [
        ("0.05", 0, ValueError),
        ("0.05", -28, ValueError),
        ("-5", 91, ValueError),
        ("-5", 73, ValueError),
        ("NaN", 28, ValueError),
        ("Infinity", 28, ValueError),
        (0.05, 28, TypeError),
        ("0.05", 28.0, TypeError),
        ("0.05", True, TypeError),
    ],
)
def test_bill_df_refuses_bad_inputs(bey: Any, tenor_days: Any, error: type[Exception]) -> None:
    value = Decimal(bey) if isinstance(bey, str) else bey
    with pytest.raises(error):
        bill_df(value, tenor_days)


def test_df_is_one_at_zero_and_the_tenor_df_at_each_tenor() -> None:
    assert CURVE.df(0) == 1.0
    assert CURVE.df(0.0) == 1.0
    for tenor, df in CURVE.points:
        assert CURVE.df(tenor) == df
        assert CURVE.df(float(tenor)) == df


def test_df_below_the_first_tenor_interpolates_from_one() -> None:
    assert CURVE.df(14) == pytest.approx(math.exp(math.log(0.996) * 14 / 28), rel=1e-15)
    assert CURVE.df(0.5) == pytest.approx(math.exp(math.log(0.996) * 0.5 / 28), rel=1e-15)


def test_df_between_tenors_is_linear_in_log_df() -> None:
    t = 50.25
    weight = (t - 28) / (91 - 28)
    expected = math.exp(math.log(0.996) + weight * (math.log(0.987) - math.log(0.996)))
    assert CURVE.df(t) == pytest.approx(expected, rel=1e-15)
    midpoint = CURVE.df((91 + 182) / 2)
    assert midpoint == pytest.approx(math.sqrt(0.987 * 0.975), rel=1e-15)


def test_df_beyond_the_last_tenor_is_unavailable() -> None:
    assert CURVE.df(182.000001) is None
    assert CURVE.df(365) is None


def test_zero_rates_give_df_one_everywhere() -> None:
    flat = DiscountCurve(tuple((n, bill_df(Decimal(0), n)) for n in (28, 91, 182)))
    for t in (0, 0.25, 13.7, 28, 60.5, 91, 150, 182):
        assert flat.df(t) == 1.0


@pytest.mark.parametrize(
    ("t_days", "error"),
    [
        (-1e-9, ValueError),
        (-1, ValueError),
        (math.nan, ValueError),
        (math.inf, ValueError),
        (True, TypeError),
        (Decimal(1), TypeError),
        ("1", TypeError),
    ],
)
def test_df_refuses_bad_times(t_days: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        CURVE.df(t_days)


@pytest.mark.parametrize(
    ("points", "error"),
    [
        ((), ValueError),
        (((91, 0.99), (28, 0.996)), ValueError),
        (((28, 0.996), (28, 0.995)), ValueError),
        (((0, 1.0),), ValueError),
        (((28, 0.0),), ValueError),
        (((28, -0.5),), ValueError),
        (((28, math.inf),), ValueError),
        (((28, math.nan),), ValueError),
        (((28, 1),), TypeError),
        (((28.0, 0.99),), TypeError),
        (((28, 0.99, 1.0),), ValueError),
        ([(28, 0.99)], TypeError),
        (([28, 0.99],), TypeError),
    ],
)
def test_curve_points_are_validated(points: Any, error: type[Exception]) -> None:
    with pytest.raises(error, match="DiscountCurve"):
        DiscountCurve(points)
