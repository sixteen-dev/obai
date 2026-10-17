"""Implied volatility and the parity-implied forward: guards, reason order, stopping rule.

ADR 0002 §4 and §17 item 26, design §13.1. The QuantLib round trip lives in
``tests/reference/test_pricing_quantlib.py``; these tests use ``black76`` itself as the price
source, so they pin the solver's contract without an oracle.
"""

import math
from decimal import Decimal
from typing import Any, Final

import pytest

from options_backtest.models.market import OptionType
from options_backtest.pricing import iv
from options_backtest.pricing.european import black76, greeks
from options_backtest.pricing.iv import (
    IV_LOWER,
    IV_UPPER,
    VEGA_FLOOR,
    ForwardReason,
    IvReason,
    IvResult,
    ParityPair,
    implied_vol,
    parity_forward,
)

IV_ARGS: Final[dict[str, Any]] = {
    "price": 100.0,
    "forward": 5000.0,
    "strike": 5000.0,
    "t": 0.25,
    "df": 0.99,
    "right": "call",
}
NOT_POSITIVE: Final = (0.0, -1.0, math.nan, math.inf, -math.inf)
NOT_FINITE: Final = (math.nan, math.inf, -math.inf)
NOT_REAL: Final = (True, Decimal("1"), "1", None)
ONE_HOUR: Final = 1 / (365 * 24)
FIRST_MIDPOINT: Final = (IV_LOWER + IV_UPPER) / 2


def with_arg(args: dict[str, Any], name: str, value: object) -> dict[str, Any]:
    return {**args, name: value}


def residual_tolerance(price: float) -> float:
    return max(1e-8, 1e-8 * abs(price))


# --- implied_vol guards -----------------------------------------------------------------------


@pytest.mark.parametrize("value", NOT_POSITIVE)
@pytest.mark.parametrize("name", ["forward", "strike", "df"])
def test_implied_vol_refuses_a_non_positive_forward_strike_or_df(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=name):
        implied_vol(**with_arg(IV_ARGS, name, value))


@pytest.mark.parametrize("value", NOT_FINITE)
@pytest.mark.parametrize("name", ["price", "t"])
def test_implied_vol_refuses_a_non_finite_price_or_time(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=name):
        implied_vol(**with_arg(IV_ARGS, name, value))


def test_implied_vol_refuses_a_negative_price() -> None:
    with pytest.raises(ValueError, match="price"):
        implied_vol(**with_arg(IV_ARGS, "price", -0.01))


@pytest.mark.parametrize("value", NOT_REAL)
@pytest.mark.parametrize("name", ["price", "forward", "strike", "t", "df"])
def test_implied_vol_refuses_a_non_float_input(name: str, value: object) -> None:
    with pytest.raises(TypeError, match=name):
        implied_vol(**with_arg(IV_ARGS, name, value))


@pytest.mark.parametrize("right", ["Put", "p", "", None])
def test_implied_vol_refuses_an_unknown_right(right: object) -> None:
    with pytest.raises(ValueError, match="right"):
        implied_vol(**with_arg(IV_ARGS, "right", right))


def test_implied_vol_validates_before_reporting_expiry() -> None:
    """An invalid input raises even when ``t <= 0`` would otherwise be EXPIRED."""
    with pytest.raises(ValueError, match="forward"):
        implied_vol(**with_arg(with_arg(IV_ARGS, "t", 0.0), "forward", -5000.0))
    with pytest.raises(ValueError, match="right"):
        implied_vol(**with_arg(with_arg(IV_ARGS, "t", -1.0), "right", "c"))


# --- implied_vol reasons, in order ------------------------------------------------------------


def reason(**changes: Any) -> IvReason | None:
    return implied_vol(**{**IV_ARGS, **changes}).reason


@pytest.mark.parametrize("t", [0.0, -0.0, -ONE_HOUR])
@pytest.mark.parametrize("price", [0.0, 1.0, 5000.0, 1e9])
def test_expired_is_checked_first(t: float, price: float) -> None:
    """``t <= 0`` wins over a price at zero, inside the bounds, at and far above the upper."""
    assert implied_vol(**{**IV_ARGS, "t": t, "price": price}) == IvResult(None, IvReason.EXPIRED)


@pytest.mark.parametrize(
    ("right", "forward", "strike", "df", "lower"),
    [("call", 5000.0, 4000.0, 0.5, 500.0), ("put", 400.0, 500.0, 0.8, 80.0)],
)
def test_below_the_lower_bound_is_strict(
    right: str, forward: float, strike: float, df: float, lower: float
) -> None:
    """``df·max(±(F - K), 0)``: just below is BELOW_LOWER_BOUND, exactly at it is not."""
    inputs = {"right": right, "forward": forward, "strike": strike, "df": df}
    below = math.nextafter(lower, 0.0)
    assert black76(forward, strike, 0.25, df, IV_LOWER, right) > below
    assert reason(**inputs, price=below) is IvReason.BELOW_LOWER_BOUND
    assert reason(**inputs, price=lower) is not IvReason.BELOW_LOWER_BOUND


@pytest.mark.parametrize(
    ("right", "forward", "strike", "df", "upper"),
    [("call", 5000.0, 4000.0, 0.5, 2500.0), ("put", 400.0, 500.0, 0.8, 400.0)],
)
def test_at_or_above_the_upper_bound_is_above_upper_bound(
    right: str, forward: float, strike: float, df: float, upper: float
) -> None:
    """``df·F`` for a call, ``df·K`` for a put; the bound itself is already out."""
    inputs = {"right": right, "forward": forward, "strike": strike, "df": df}
    assert reason(**inputs, price=upper) is IvReason.ABOVE_UPPER_BOUND
    assert reason(**inputs, price=upper + 1.0) is IvReason.ABOVE_UPPER_BOUND
    assert reason(**inputs, price=math.nextafter(upper, 0.0)) is not IvReason.ABOVE_UPPER_BOUND


@pytest.mark.parametrize(
    ("strike", "t", "price"),
    [(5000.0, ONE_HOUR, 1e-3), (5000.0, 2.0, 4999.0), (6500.0, ONE_HOUR, 1.0)],
)
def test_a_price_outside_the_solver_bracket_is_no_root(
    strike: float, t: float, price: float
) -> None:
    """Inside the price bounds, outside ``[black76(IV_LOWER), black76(IV_UPPER)]``."""
    low = black76(5000.0, strike, t, 1.0, IV_LOWER, "call")
    high = black76(5000.0, strike, t, 1.0, IV_UPPER, "call")
    assert max(5000.0 - strike, 0.0) <= price < 5000.0
    assert not low <= price <= high
    assert reason(strike=strike, t=t, df=1.0, price=price) is IvReason.NO_ROOT


def test_bisection_without_a_converged_midpoint_within_the_cap_is_no_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The iteration cap ends the solve with NO_ROOT, never the last midpoint."""
    price = black76(5000.0, 5000.0, 0.25, 0.99, 0.2, "call")
    assert reason(price=price) is None
    monkeypatch.setattr(iv, "IV_MAX_ITERATIONS", 3)
    assert implied_vol(**with_arg(IV_ARGS, "price", price)) == IvResult(None, IvReason.NO_ROOT)


@pytest.mark.parametrize(("right", "strike"), [("call", 6500.0), ("put", 3800.0)])
def test_a_solution_with_vega_below_the_floor_is_vega_too_small(right: str, strike: float) -> None:
    """One hour, far OTM, price 1e-9: bracketed and met at the first midpoint, vega ~1e-19."""
    args = {"forward": 5000.0, "strike": strike, "t": ONE_HOUR, "df": 1.0, "right": right}
    assert black76(**args, sigma=IV_LOWER) <= 1e-9 <= black76(**args, sigma=IV_UPPER)
    assert abs(black76(**args, sigma=FIRST_MIDPOINT) - 1e-9) <= residual_tolerance(1e-9)
    vega = greeks(5000.0, 5000.0, strike, ONE_HOUR, 1.0, FIRST_MIDPOINT, right, 1.0).vega
    assert vega < VEGA_FLOOR
    assert implied_vol(1e-9, **args) == IvResult(None, IvReason.VEGA_TOO_SMALL)


# --- implied_vol values -----------------------------------------------------------------------


def test_bisection_stops_at_the_first_midpoint_within_the_residual() -> None:
    """A price exactly at the first midpoint's premium returns that midpoint, unrefined."""
    price = black76(5000.0, 5000.0, 0.25, 0.99, FIRST_MIDPOINT, "put")
    result = implied_vol(**{**IV_ARGS, "price": price, "right": "put"})
    assert result == IvResult(FIRST_MIDPOINT, None)


@pytest.mark.parametrize("right", ["call", "put"])
@pytest.mark.parametrize("z", [-1.5, -0.5, 0.0, 0.5, 1.5])
@pytest.mark.parametrize(("t", "sigma"), [(7 / 365, 0.12), (0.25, 0.2), (2.0, 0.9)])
def test_implied_vol_round_trips_black76(right: str, z: float, t: float, sigma: float) -> None:
    """Strikes at ``ln(K/F) = z·σ√t``, where vega makes the volatility identifiable."""
    strike = 5000.0 * math.exp(z * sigma * math.sqrt(t))
    price = black76(5000.0, strike, t, 0.97, sigma, right)
    result = implied_vol(price, 5000.0, strike, t, 0.97, right)
    assert result.reason is None
    assert result.value is not None
    assert abs(result.value - sigma) <= 1e-6
    repriced = black76(5000.0, strike, t, 0.97, result.value, right)
    assert abs(repriced - price) <= residual_tolerance(price)


def test_implied_vol_accepts_option_type_members() -> None:
    for member in OptionType:
        by_member = implied_vol(**with_arg(IV_ARGS, "right", member))
        assert by_member == implied_vol(**with_arg(IV_ARGS, "right", member.value))
        assert by_member.value is not None


# --- parity_forward ---------------------------------------------------------------------------


def pair(strike: float, forward: float, df: float = 1.0) -> ParityPair:
    """Return a pair whose parity forward ``K + (C - P)/df`` is ``forward`` (exact here)."""
    gap = (forward - strike) * df
    return ParityPair(strike=strike, call_mid=10.0 + max(gap, 0.0), put_mid=10.0 + max(-gap, 0.0))


def outcome(pairs: list[ParityPair], df: float, spot: float) -> tuple[float | None, Any, Any]:
    result = parity_forward(pairs, df, spot)
    return result.value, result.reason, result.pairs_used


def test_parity_forward_uses_the_five_nearest_with_the_lower_strike_on_a_tie() -> None:
    """Spot 5000: 4985 and 5015 tie at distance 15 for fifth; 4985 wins (with 5015: 5000)."""
    forwards = {4985.0: 5001.0, 4990.0: 5000.0, 4995.0: 5000.0, 5000.0: 5002.0}
    forwards |= {5005.0: 5001.0, 5015.0: 4000.0}
    pairs = [pair(strike, forward) for strike, forward in forwards.items()]
    expected = (5001.0, None, (4985.0, 4990.0, 4995.0, 5000.0, 5005.0))
    assert outcome(pairs, 1.0, 5000.0) == expected
    shuffled = [pairs[i] for i in (3, 5, 0, 4, 1, 2)]
    assert outcome(shuffled, 1.0, 5000.0) == expected


def test_parity_forward_divides_the_mid_difference_by_df() -> None:
    pairs = [pair(4990.0, 5004.0, 0.5), pair(5000.0, 5004.0, 0.5), pair(5010.0, 5004.0, 0.5)]
    assert outcome(pairs, 0.5, 5000.0) == (5004.0, None, (4990.0, 5000.0, 5010.0))


def test_parity_forward_of_an_even_count_is_the_mean_of_the_middle_two() -> None:
    pairs = [pair(4990.0, 5000.0), pair(4995.0, 5001.0), pair(5005.0, 5004.0)]
    pairs.append(pair(5010.0, 5006.0))
    assert outcome(pairs, 1.0, 5000.0) == (5002.5, None, (4990.0, 4995.0, 5005.0, 5010.0))


@pytest.mark.parametrize("count", [0, 1, 2])
def test_parity_forward_needs_three_pairs(count: int) -> None:
    pairs = [pair(5000.0 + 5.0 * i, 5000.0) for i in range(count)]
    strikes = tuple(5000.0 + 5.0 * i for i in range(count))
    assert outcome(pairs, 1.0, 5000.0) == (None, ForwardReason.TOO_FEW_PAIRS, strikes)


@pytest.mark.parametrize("median", [-2.0, 0.0])
def test_a_non_positive_median_is_nonpositive_forward(median: float) -> None:
    pairs = [pair(5.0, median - 1.0), pair(10.0, median), pair(15.0, median + 1.0)]
    expected = (None, ForwardReason.NONPOSITIVE_FORWARD, (5.0, 10.0, 15.0))
    assert outcome(pairs, 1.0, 10.0) == expected


@pytest.mark.parametrize(
    ("forwards", "value", "why"),
    [
        ((4990.0, 4995.0, 5000.0, 5020.0, 5030.0), 5000.0, None),
        ((4990.0, 4995.0, 5000.0, 5020.5, 5030.0), None, ForwardReason.DISPERSION),
        ((4900.0, 5000.0, 5100.0, None, None), None, ForwardReason.DISPERSION),
    ],
)
def test_an_inclusive_iqr_above_half_a_percent_is_dispersion(
    forwards: tuple[float | None, ...], value: float | None, why: ForwardReason | None
) -> None:
    """Inclusive quartiles: 4995 and 5020 give an IQR of 25 = 0.5% of 5000, which passes."""
    strikes = (4990.0, 4995.0, 5000.0, 5005.0, 5010.0)
    used = [(k, f) for k, f in zip(strikes, forwards, strict=True) if f is not None]
    pairs = [pair(strike, forward) for strike, forward in used]
    assert outcome(pairs, 1.0, 5000.0) == (value, why, tuple(k for k, _ in used))


def test_parity_forward_accepts_a_tuple_of_pairs() -> None:
    pairs = (pair(4995.0, 5000.0), pair(5000.0, 5000.0), pair(5005.0, 5000.0))
    assert parity_forward(pairs, 1.0, 5000.0).value == 5000.0


# --- parity_forward guards --------------------------------------------------------------------


GOOD_PAIRS: Final = (pair(4995.0, 5000.0), pair(5000.0, 5000.0), pair(5005.0, 5000.0))


def test_parity_forward_refuses_repeated_strikes() -> None:
    with pytest.raises(ValueError, match="strike"):
        parity_forward([*GOOD_PAIRS, pair(5000.0, 5001.0)], 1.0, 5000.0)


@pytest.mark.parametrize("value", NOT_POSITIVE)
def test_parity_forward_refuses_a_non_positive_or_non_finite_df_or_spot(value: float) -> None:
    with pytest.raises(ValueError, match="df"):
        parity_forward(GOOD_PAIRS, value, 5000.0)
    with pytest.raises(ValueError, match="spot"):
        parity_forward(GOOD_PAIRS, 1.0, value)


@pytest.mark.parametrize("value", NOT_FINITE)
@pytest.mark.parametrize("field", ["strike", "call_mid", "put_mid"])
def test_parity_forward_refuses_a_non_finite_pair_field(field: str, value: float) -> None:
    fields = {"strike": 5010.0, "call_mid": 10.0, "put_mid": 20.0, field: value}
    with pytest.raises(ValueError, match=field):
        parity_forward([*GOOD_PAIRS, ParityPair(**fields)], 1.0, 5000.0)


@pytest.mark.parametrize("value", NOT_REAL)
def test_parity_forward_refuses_a_non_float_input(value: Any) -> None:
    with pytest.raises(TypeError, match="call_mid"):
        parity_forward([*GOOD_PAIRS, ParityPair(5010.0, value, 20.0)], 1.0, 5000.0)
    with pytest.raises(TypeError, match="spot"):
        parity_forward(GOOD_PAIRS, 1.0, value)
