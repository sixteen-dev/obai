"""Assumed flat fee schedule (ADR 0001 §5 ``engine/fees.py``, design §11.5).

One line item per assessed leg or lifecycle event is kept, even at a $0 rate, so the entry
records what was assessed; zero amounts post nothing (the posting functions drop them).
"""

from decimal import Decimal
from typing import Any

import pytest

from options_backtest.engine.fees import AssumedFlatFeeSchedule, lifecycle_fees, trade_fees
from options_backtest.models.ledger import FeeEvent, FeeLine, LegFill
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    ExerciseStyle,
    OptionType,
    SettlementType,
)
from options_backtest.money import Price, Usd


def usd(text: str) -> Usd:
    return Usd(Decimal(text))


def terms(strike: str) -> ContractTerms:
    deliverable = Deliverable("SPX-x100", (DeliverableComponent("SPX", Decimal(100)),), usd("0"))
    return ContractTerms(
        contract_id=f"SPXW-P{strike}",
        option_type=OptionType.PUT,
        strike=Price(Decimal(strike)),
        exercise_style=ExerciseStyle.EUROPEAN,
        settlement_type=SettlementType.CASH,
        premium_multiplier=Decimal(100),
        deliverable=deliverable,
        aggregate_exercise_amount=usd(strike).scaled_by(100),
        expires_at_ns=1_000,
    )


def schedule(**overrides: Any) -> AssumedFlatFeeSchedule:
    fields: dict[str, Any] = {
        "schedule_id": "flat",
        "trade_per_contract": usd("0.65"),
        "exercise_assignment_per_contract": usd("0.00"),
        "cash_settlement_per_contract": usd("0.10"),
    }
    fields.update(overrides)
    return AssumedFlatFeeSchedule(**fields)


def test_the_schedule_declares_the_assumed_cost_basis() -> None:
    assert schedule().cost_basis == "assumed_schedule"


def test_trade_fees_assess_each_leg_on_its_absolute_contracts() -> None:
    legs = (
        LegFill(terms("100"), -3, Price(Decimal("2.00"))),
        LegFill(terms("95"), 2, Price(Decimal("1.10"))),
    )

    assert trade_fees(schedule(), legs) == (
        FeeLine("flat:trade", FeeEvent.TRADE, 3, usd("0.65"), usd("1.95")),
        FeeLine("flat:trade", FeeEvent.TRADE, 2, usd("0.65"), usd("1.30")),
    )


def test_a_zero_rate_still_records_its_line() -> None:
    legs = (LegFill(terms("100"), 1, Price(Decimal("2.00"))),)

    lines = trade_fees(schedule(trade_per_contract=usd("0")), legs)

    assert lines == (FeeLine("flat:trade", FeeEvent.TRADE, 1, usd("0"), usd("0")),)


def test_trade_fees_need_at_least_one_leg() -> None:
    with pytest.raises(ValueError, match="at least one leg"):
        trade_fees(schedule(), ())


@pytest.mark.parametrize(
    ("event", "rate"),
    [
        pytest.param(FeeEvent.EXERCISE_ASSIGNMENT, "0.00", id="exercise-assignment"),
        pytest.param(FeeEvent.CASH_SETTLEMENT, "0.10", id="cash-settlement"),
    ],
)
def test_lifecycle_fees_charge_the_events_rate_per_contract(event: FeeEvent, rate: str) -> None:
    assert lifecycle_fees(schedule(), event, 4) == (
        FeeLine(f"flat:{event.value}", event, 4, usd(rate), usd(rate).scaled_by(4)),
    )


def test_lifecycle_fees_reject_a_trade_event() -> None:
    with pytest.raises(ValueError, match="trade_fees"):
        lifecycle_fees(schedule(), FeeEvent.TRADE, 1)


@pytest.mark.parametrize("contracts", [0, -1])
def test_lifecycle_fees_need_a_positive_contract_count(contracts: int) -> None:
    with pytest.raises(ValueError, match="> 0"):
        lifecycle_fees(schedule(), FeeEvent.CASH_SETTLEMENT, contracts)


def test_lifecycle_fees_reject_a_non_int_contract_count() -> None:
    with pytest.raises(TypeError, match="int"):
        lifecycle_fees(schedule(), FeeEvent.CASH_SETTLEMENT, True)


@pytest.mark.parametrize(
    ("overrides", "error", "match"),
    [
        pytest.param({"schedule_id": ""}, ValueError, "non-empty", id="empty-id"),
        pytest.param({"schedule_id": 7}, TypeError, "str", id="id-type"),
        pytest.param({"trade_per_contract": usd("-0.01")}, ValueError, ">= 0", id="negative"),
        pytest.param({"cash_settlement_per_contract": usd("0.005")}, ValueError, "cents", id="sub"),
        pytest.param({"exercise_assignment_per_contract": Decimal(1)}, TypeError, "Usd", id="type"),
    ],
)
def test_schedule_rates_are_whole_non_negative_cents(
    overrides: dict[str, Any], error: type[Exception], match: str
) -> None:
    with pytest.raises(error, match=match):
        schedule(**overrides)


def test_lifecycle_fees_reject_a_non_enum_event() -> None:
    with pytest.raises(TypeError, match="FeeEvent"):
        lifecycle_fees(schedule(), "cash_settlement", 1)  # type: ignore[arg-type]
