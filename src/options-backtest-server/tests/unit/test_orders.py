"""Order vocabulary: purposes, ids, usable quotes, package debit and closing legs (ADR 0002 §9).

Design §10.3: ``q > 0`` buys at the ask, ``q < 0`` sells at the bid, and
``D = Σ premium_usd(natural_i, q_i · n)`` is negative for a credit.
"""

from dataclasses import replace
from datetime import date

import pytest
from data_builders import MON, quote_obs
from engine_builders import (
    CLOSING_LEGS,
    LONG,
    LONG_ID,
    MON_S,
    OPENING_LEGS,
    SHORT,
    SHORT_ID,
    leg,
    order,
    price,
    usd,
)

from options_backtest.engine.orders import (
    ExitTrigger,
    OrderLeg,
    OrderPurpose,
    closing_legs,
    natural_price,
    order_id,
    package_debit,
    usable_quote,
)
from options_backtest.errors import MissingMarkError
from options_backtest.models.market import Quote

QUOTES = {
    SHORT_ID: Quote(price("2.00"), price("2.20")),
    LONG_ID: Quote(price("1.00"), price("1.10")),
}


@pytest.mark.parametrize(
    ("purpose", "opening"),
    [
        (OrderPurpose.ENTRY, True),
        (OrderPurpose.ROLL_OPEN, True),
        (OrderPurpose.EXIT, False),
        (OrderPurpose.ROLL_CLOSE, False),
        (OrderPurpose.FINAL, False),
    ],
)
def test_only_entries_and_replacements_open(purpose: OrderPurpose, opening: bool) -> None:
    assert purpose.opening is opening


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [
        (OrderPurpose.ENTRY, "o:2024-03-04:entry"),
        (OrderPurpose.ROLL_OPEN, "o:2024-03-04:roll_open"),
        (OrderPurpose.EXIT, "o:2024-03-04:exit"),
        (OrderPurpose.ROLL_CLOSE, "o:2024-03-04:roll_close"),
        (OrderPurpose.FINAL, "o:2024-03-04:final"),
    ],
)
def test_order_id_spells_session_and_purpose(purpose: OrderPurpose, expected: str) -> None:
    assert order_id(MON, purpose) == expected


def test_order_id_rejects_wrong_types() -> None:
    with pytest.raises(TypeError, match="session_date"):
        order_id("2024-03-04", OrderPurpose.ENTRY)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="purpose"):
        order_id(MON, "entry")  # type: ignore[arg-type]


# --- usable_quote --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bid", "ask", "buy", "sell"),
    [
        ("2.00", "2.20", True, True),  # VALID
        ("2.10", "2.10", True, True),  # LOCKED
        ("0", "0.05", True, False),  # NO_BID: a zero bid cannot be sold into
        ("2.30", "2.20", False, False),  # CROSSED
        ("0", "0", False, False),  # ZERO_ASK
        ("-0.05", "0.10", False, False),  # NEGATIVE
    ],
)
def test_usable_quote_follows_the_side_the_leg_trades(
    bid: str, ask: str, buy: bool, sell: bool
) -> None:
    observation = quote_obs(SHORT_ID, MON_S, "DEC", bid, ask)

    assert (usable_quote(observation, 1) is not None) is buy
    assert (usable_quote(observation, -2) is not None) is sell


def test_usable_quote_returns_the_observation_prices() -> None:
    observation = quote_obs(SHORT_ID, MON_S, "DEC", "2.00", "2.20")

    assert usable_quote(observation, -1) == Quote(price("2.00"), price("2.20"))


def test_usable_quote_of_no_observation_is_none() -> None:
    assert usable_quote(None, 1) is None


def test_usable_quote_rejects_a_zero_ratio() -> None:
    observation = quote_obs(SHORT_ID, MON_S, "DEC")

    with pytest.raises(ValueError, match="ratio"):
        usable_quote(observation, 0)
    with pytest.raises(TypeError, match="ratio"):
        usable_quote(observation, True)  # noqa: FBT003 — a bool is not a ratio


# --- package_debit -------------------------------------------------------------------------


def test_natural_price_is_the_ask_for_a_buy_and_the_bid_for_a_sell() -> None:
    quote = Quote(price("2.00"), price("2.20"))
    assert (natural_price(quote, 3), natural_price(quote, -1)) == (price("2.20"), price("2.00"))


def test_natural_price_refuses_a_zero_ratio_or_a_non_quote() -> None:
    with pytest.raises(ValueError, match="nonzero"):
        natural_price(Quote(price("2.00"), price("2.20")), 0)
    with pytest.raises(TypeError):
        natural_price((price("2.00"), price("2.20")), 1)  # type: ignore[arg-type]


def test_package_debit_of_a_credit_vertical_is_negative() -> None:
    # Sell 4900 at the bid 2.00, buy 4895 at the ask 1.10: 100·(-1)·2.00 + 100·(+1)·1.10.
    assert package_debit(OPENING_LEGS, 1, QUOTES) == usd("-90.00")


def test_package_debit_scales_by_packages() -> None:
    assert package_debit(OPENING_LEGS, 2, QUOTES) == usd("-180.00")


def test_closing_the_credit_vertical_is_a_debit_at_the_other_naturals() -> None:
    # Buy 4900 at the ask 2.20, sell 4895 at the bid 1.00.
    assert package_debit(CLOSING_LEGS, 1, QUOTES) == usd("120.00")


def test_package_debit_uses_integer_ratios_exactly() -> None:
    legs = (leg(SHORT, -2), leg(LONG, 1))

    assert package_debit(legs, 3, QUOTES) == usd("-870.00")  # 100·(-6)·2.00 + 100·3·1.10


def test_package_debit_ignores_other_quotes() -> None:
    quotes = {**QUOTES, "SPXW:2024-03-06:C:5100": Quote(price("0"), price("0.05"))}

    assert package_debit(OPENING_LEGS, 1, quotes) == usd("-90.00")


def test_package_debit_never_prices_a_missing_leg_at_zero() -> None:
    with pytest.raises(MissingMarkError) as caught:
        package_debit(OPENING_LEGS, 1, {SHORT_ID: QUOTES[SHORT_ID]})

    assert caught.value.instrument_ids == (LONG_ID,)


@pytest.mark.parametrize("packages", [0, -1])
def test_package_debit_needs_a_package(packages: int) -> None:
    with pytest.raises(ValueError, match="packages"):
        package_debit(OPENING_LEGS, packages, QUOTES)


def test_package_debit_needs_a_leg() -> None:
    with pytest.raises(ValueError, match="leg"):
        package_debit((), 1, QUOTES)


# --- closing_legs --------------------------------------------------------------------------


def test_closing_legs_keep_terms_versions_and_order_with_negated_ratios() -> None:
    opening = (leg(SHORT, -1), OrderLeg(LONG, f"{LONG_ID}@v2", 2))

    assert closing_legs(opening) == (leg(SHORT, 1), OrderLeg(LONG, f"{LONG_ID}@v2", -2))


def test_closing_legs_need_a_leg() -> None:
    with pytest.raises(ValueError, match="leg"):
        closing_legs(())


# --- OrderLeg and Order guards -------------------------------------------------------------


def test_order_leg_rejects_a_zero_or_non_int_ratio() -> None:
    with pytest.raises(ValueError, match="ratio"):
        OrderLeg(SHORT, f"{SHORT_ID}@v1", 0)
    with pytest.raises(TypeError, match="ratio"):
        OrderLeg(SHORT, f"{SHORT_ID}@v1", 1.0)  # type: ignore[arg-type]


def test_order_leg_version_must_be_the_contracts() -> None:
    with pytest.raises(ValueError, match="version_id"):
        OrderLeg(SHORT, f"{LONG_ID}@v1", -1)


def test_a_final_order_has_no_limit_and_every_other_order_has_one() -> None:
    assert order(OrderPurpose.FINAL, limit=None).limit_usd is None
    with pytest.raises(ValueError, match="limit"):
        order(OrderPurpose.FINAL, limit="120")
    with pytest.raises(ValueError, match="limit"):
        order(OrderPurpose.EXIT, limit=None)


def test_order_needs_whole_packages_and_distinct_legs() -> None:
    with pytest.raises(ValueError, match="packages"):
        order(packages=0)
    with pytest.raises(ValueError, match="legs"):
        order(legs=())
    with pytest.raises(ValueError, match="twice"):
        order(legs=(leg(SHORT, -1), leg(SHORT, 1)))


@pytest.mark.parametrize(
    ("purpose", "trigger"),
    [
        (OrderPurpose.ENTRY, ExitTrigger.TIME_EXIT),
        (OrderPurpose.ROLL_CLOSE, ExitTrigger.ROLL_CAP),
        (OrderPurpose.EXIT, None),
        (OrderPurpose.EXIT, ExitTrigger.FINAL_LIQUIDATION),
        (OrderPurpose.EXIT, ExitTrigger.SETTLEMENT),
        (OrderPurpose.FINAL, ExitTrigger.TIME_EXIT),
    ],
)
def test_order_trigger_fits_its_purpose(purpose: OrderPurpose, trigger: ExitTrigger | None) -> None:
    limit = None if purpose is OrderPurpose.FINAL else "100"
    valid = order(purpose, limit=limit)

    with pytest.raises(ValueError, match="trigger"):
        replace(valid, trigger=trigger)


@pytest.mark.parametrize(
    "trigger",
    [
        ExitTrigger.TIME_EXIT,
        ExitTrigger.TAKE_PROFIT,
        ExitTrigger.STOP_LOSS,
        ExitTrigger.CAMPAIGN_CAP,
        ExitTrigger.ROLL_CAP,
    ],
)
def test_an_exit_carries_any_exit_trigger(trigger: ExitTrigger) -> None:
    assert replace(order(OrderPurpose.EXIT, limit="100"), trigger=trigger).trigger is trigger


def test_order_rejects_wrong_field_types() -> None:
    valid = order()
    with pytest.raises(TypeError, match="purpose"):
        replace(valid, purpose="entry")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="limit_usd"):
        replace(valid, limit_usd="-90")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="session_date"):
        replace(valid, session_date="2024-03-04")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="submitted_at_ns"):
        replace(valid, submitted_at_ns=1.0)  # type: ignore[arg-type]


def test_order_id_must_spell_the_orders_session_and_purpose() -> None:
    valid = order()
    with pytest.raises(ValueError, match="order_id"):
        replace(valid, order_id="")
    with pytest.raises(ValueError, match="order_id"):
        replace(valid, session_date=date(2024, 3, 5))
    with pytest.raises(ValueError, match="order_id"):
        replace(valid, purpose=OrderPurpose.ROLL_OPEN)
    with pytest.raises(ValueError, match="campaign_id"):
        replace(valid, campaign_id="")
