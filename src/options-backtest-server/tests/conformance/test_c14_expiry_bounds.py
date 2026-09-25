"""C14: exact expiry bounds, including tails that strike-only evaluation misses.

Design §11.2 (evaluate every breakpoint plus the slope as S -> infinity; a negative upper-tail
slope is unbounded loss); ADR 0001 §5 (``expiry_bounds``; reserves of an unbounded campaign
raise). Payoff(S) = h·S + Σ q_i x intrinsic_i(S) with intrinsic from deliverable and AEA,
breakpoints {0} ∪ {(AEA_i - cash_i) / units_i}, upper_slope = h + Σ_calls q_i x units_i; every
bound below includes ``entry_cash``. The numbers are hand-computed in each test.
"""

from decimal import Decimal

import pytest
from builders import (
    DAY_NS,
    EXPIRES_AT_NS,
    INDEX_ASSET,
    ZERO,
    fixture_fee_schedule,
    index_option,
    moment,
    price,
    settle_day,
    stock_option,
    usd,
)

from options_backtest.engine.fees import trade_fees
from options_backtest.engine.funding import campaign_encumbrances, expiry_bounds
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.errors import ErrorCode, UnsupportedLifecycle
from options_backtest.models.ledger import LedgerState, LegFill
from options_backtest.models.market import OptionType


def test_naked_short_call_has_no_minimum_although_its_breakpoints_look_finite() -> None:
    call = index_option(OptionType.CALL, "100")
    premium = usd("300.00")

    bounds = expiry_bounds(((call, -1),), 0, premium)

    # Evaluating only {0, K} finds a worst payoff of 0, a finite "max loss": what C14 rejects.
    strike_only = min(
        call.intrinsic_usd({INDEX_ASSET: point}).scaled_by(-1) for point in bounds.breakpoints
    )
    assert bounds.breakpoints == (price("0"), price("100"))
    assert strike_only == ZERO
    assert bounds.upper_slope == Decimal(-100)
    assert bounds.min_value is None
    assert bounds.max_value == premium


def test_reserve_for_a_naked_short_call_campaign_raises() -> None:
    call = index_option(OptionType.CALL, "100")
    schedule = fixture_fee_schedule()
    funded = apply_entry(
        LedgerState.empty(),
        book_deposit(event_id="c14-deposit", at_ns=moment(0), cash=usd("10000.00")),
    )
    legs = (LegFill(call, -1, price("3.00")),)
    entry = book_option_trade(
        funded,
        event_id="c14-naked-call",
        at_ns=moment(0, 1),
        campaign_id="naked",
        legs=legs,
        fees=trade_fees(schedule, legs),
        settles_on=settle_day(1),
    )

    with pytest.raises(UnsupportedLifecycle) as caught:
        campaign_encumbrances(apply_entry(funded, entry), schedule)
    assert caught.value.code is ErrorCode.UNSUPPORTED_ACCOUNT_STATE


def test_covered_call_is_bounded_on_both_sides() -> None:
    # 100·S - max(100·S - 10,000, 0) + 300: 300 at S = 0, 10,300 from S = 100 on; slope 0.
    call = stock_option(OptionType.CALL, "100.00", shares="100", aggregate="10000.00")

    bounds = expiry_bounds(((call, -1),), 100, usd("300.00"))

    assert bounds.breakpoints == (price("0"), price("100"))
    assert bounds.upper_slope == 0
    assert bounds.min_value == usd("300.00")
    assert bounds.max_value == usd("10300.00")


def test_stock_plus_short_put_has_its_minimum_at_zero_and_no_maximum() -> None:
    # 100·S - max(10,000 - 100·S, 0) + 200: -9,800 at S = 0, rising with slope 100 forever.
    put = stock_option(OptionType.PUT, "100.00", shares="100", aggregate="10000.00")

    bounds = expiry_bounds(((put, -1),), 100, usd("200.00"))

    assert bounds.breakpoints == (price("0"), price("100"))
    assert bounds.upper_slope == 100
    assert bounds.min_value == usd("-9800.00")
    assert bounds.max_value is None


def test_iron_condor_is_bounded_by_its_wings() -> None:
    # Long 90 put, short 95 put, short 105 call, long 110 call, credit 150: both wings lose
    # 5 x 100 = 500, so the range is [-350, 150] with a flat upper tail.
    holdings = (
        (index_option(OptionType.PUT, "90"), 1),
        (index_option(OptionType.PUT, "95"), -1),
        (index_option(OptionType.CALL, "105"), -1),
        (index_option(OptionType.CALL, "110"), 1),
    )

    bounds = expiry_bounds(holdings, 0, usd("150.00"))

    assert bounds.breakpoints == tuple(price(p) for p in ("0", "90", "95", "105", "110"))
    assert bounds.upper_slope == 0
    assert bounds.min_value == usd("-350.00")
    assert bounds.max_value == usd("150.00")


def test_breakpoints_come_from_deliverable_and_aea_not_the_listed_strike() -> None:
    # F05 after the reverse split: listed strike 60, but 50 shares for AEA 6,000 put the kink at
    # S = 120. Long put bought for 1,000: max(6,000 - 50·S, 0) - 1,000 spans [-1,000, 5,000].
    put = stock_option(OptionType.PUT, "60.00", shares="50", aggregate="6000.00")

    bounds = expiry_bounds(((put, 1),), 0, usd("-1000.00"))

    assert bounds.breakpoints == (price("0"), price("120"))
    assert bounds.upper_slope == 0
    assert bounds.min_value == usd("-1000.00")
    assert bounds.max_value == usd("5000.00")


def test_mixed_expiries_raise() -> None:
    near = index_option(OptionType.PUT, "100")
    far = index_option(OptionType.PUT, "95", expires_at_ns=EXPIRES_AT_NS + DAY_NS)

    with pytest.raises(UnsupportedLifecycle) as caught:
        expiry_bounds(((near, -1), (far, 1)), 0, usd("90.00"))
    assert caught.value.code is ErrorCode.UNSUPPORTED_ACCOUNT_STATE
