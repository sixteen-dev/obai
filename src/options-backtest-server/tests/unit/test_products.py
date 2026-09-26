"""R1 product rules, contract ids, terms and settlement rounding (ADR 0002 §3, §17 items 5, 15)."""

from dataclasses import replace
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pytest
from data_builders import local_ns

from options_backtest.models.market import (
    Deliverable,
    DeliverableComponent,
    ExerciseStyle,
    OptionType,
    SettlementType,
)
from options_backtest.money import ZERO_USD, Price, Usd
from options_backtest.reference.products import (
    PRODUCT_RULES_VERSION,
    R1_FAMILY,
    SPXW_RULES,
    XSP_RULES,
    ProductRules,
    contract_id,
    product_rules,
    settlement_price,
    strike_text,
)

EXPIRY = date(2024, 4, 19)
EXPIRES_AT_NS = local_ns(EXPIRY, 16)


def price(text: str) -> Price:
    return Price(Decimal(text))


def test_template_rules_are_the_adr_values() -> None:
    assert PRODUCT_RULES_VERSION == "cboe_template_unverified_v1"
    for rules, underlying, series, divisor in (
        (SPXW_RULES, "SPX", "SPX_PM", 1),
        (XSP_RULES, "XSP", "XSP_PM", 10),
    ):
        assert (rules.underlying_id, rules.settlement_series) == (underlying, series)
        assert rules.settlement_divisor == Decimal(divisor)
        assert (rules.settlement_places, rules.settlement_rounding) == (2, ROUND_HALF_UP)
        assert (rules.premium_multiplier, rules.deliverable_units) == (Decimal(100), Decimal(100))
        assert (rules.family, rules.last_trade_local) == (R1_FAMILY, time(16, 0))
        assert rules.status == "template_unverified"


def test_product_rules_knows_only_the_r1_roots() -> None:
    assert product_rules("SPXW") is SPXW_RULES
    assert product_rules("XSP") is XSP_RULES
    for root in ("SPX", "spxw", ""):
        with pytest.raises(ValueError, match="root"):
            product_rules(root)


@pytest.mark.parametrize(
    ("strike", "text"),
    [
        ("4900.00", "4900"),
        ("4900", "4900"),
        ("4.9E+3", "4900"),
        ("451.50", "451.5"),
        ("0.5", "0.5"),
    ],
)
def test_strike_text_is_normalized_without_exponent(strike: str, text: str) -> None:
    assert strike_text(price(strike)) == text


def test_contract_id_spells_root_expiry_right_and_strike() -> None:
    assert contract_id("SPXW", EXPIRY, OptionType.PUT, price("4900.00")) == "SPXW:2024-04-19:P:4900"
    assert contract_id("XSP", EXPIRY, OptionType.CALL, price("451.50")) == "XSP:2024-04-19:C:451.5"


@pytest.mark.parametrize(
    "arguments",
    [
        ("", EXPIRY, OptionType.PUT, Price(Decimal(1))),
        ("SPXW", datetime(2024, 4, 19), OptionType.PUT, Price(Decimal(1))),
        ("SPXW", EXPIRY, "put", Price(Decimal(1))),
        ("SPXW", EXPIRY, OptionType.PUT, Decimal(1)),
    ],
)
def test_contract_id_checks_its_arguments(arguments: tuple[Any, ...]) -> None:
    with pytest.raises((TypeError, ValueError)):
        contract_id(*arguments)


def test_spxw_terms_are_european_cash_with_aea_100_k() -> None:
    terms = SPXW_RULES.terms(price("4900"), OptionType.PUT, EXPIRES_AT_NS, expiry=EXPIRY)
    assert terms.contract_id == "SPXW:2024-04-19:P:4900"
    assert (terms.option_type, terms.strike) == (OptionType.PUT, price("4900"))
    assert (terms.exercise_style, terms.settlement_type) == (
        ExerciseStyle.EUROPEAN,
        SettlementType.CASH,
    )
    assert terms.premium_multiplier == Decimal(100)
    assert terms.deliverable == Deliverable(
        "SPX:100", (DeliverableComponent("SPX", Decimal(100)),), ZERO_USD
    )
    assert terms.aggregate_exercise_amount == Usd(Decimal(490000))
    assert terms.expires_at_ns == EXPIRES_AT_NS


def test_xsp_terms_deliver_xsp_units() -> None:
    terms = XSP_RULES.terms(price("451.5"), OptionType.CALL, EXPIRES_AT_NS, expiry=EXPIRY)
    assert terms.contract_id == "XSP:2024-04-19:C:451.5"
    assert terms.deliverable.deliverable_id == "XSP:100"
    assert terms.aggregate_exercise_amount == Usd(Decimal(45150))
    assert terms.intrinsic_usd({"XSP": price("452.5")}) == Usd(Decimal(100))


def test_replaced_template_builds_tla_scale_terms() -> None:
    rules = replace(SPXW_RULES, premium_multiplier=Decimal(1), deliverable_units=Decimal(1))
    terms = rules.terms(price("100"), OptionType.PUT, EXPIRES_AT_NS, expiry=EXPIRY)
    assert terms.premium_multiplier == Decimal(1)
    assert terms.deliverable.deliverable_id == "SPX:1"
    assert terms.aggregate_exercise_amount == Usd(Decimal(100))


def test_terms_refuse_a_datetime_expiry() -> None:
    with pytest.raises(TypeError, match="expiry"):
        SPXW_RULES.terms(price("4900"), OptionType.PUT, EXPIRES_AT_NS, expiry=datetime(2024, 4, 19))


@pytest.mark.parametrize(
    ("rules", "official", "settlement"),
    [
        (SPXW_RULES, "4897", "4897.00"),
        (SPXW_RULES, "4897.125", "4897.13"),
        (XSP_RULES, "4512.37", "451.24"),
        (XSP_RULES, "4512.25", "451.23"),
        (XSP_RULES, "4512.24", "451.22"),
        (XSP_RULES, "4500", "450.00"),
    ],
)
def test_settlement_price_divides_and_rounds_half_up(
    rules: ProductRules, official: str, settlement: str
) -> None:
    value = settlement_price(rules, price(official))
    assert str(value.value) == settlement


def test_settlement_price_refuses_a_non_price() -> None:
    with pytest.raises(TypeError):
        settlement_price(XSP_RULES, Decimal("4512.37"))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"root": ""}, ValueError),
        ({"underlying_id": None}, TypeError),
        ({"settlement_divisor": Decimal(0)}, ValueError),
        ({"settlement_divisor": 10}, TypeError),
        ({"settlement_places": -1}, ValueError),
        ({"settlement_places": 10}, ValueError),
        ({"settlement_places": 2.0}, TypeError),
        ({"settlement_rounding": "HALF_UP"}, ValueError),
        ({"premium_multiplier": 100}, TypeError),
        ({"deliverable_units": Decimal("-100")}, ValueError),
        ({"deliverable_units": Decimal("Infinity")}, ValueError),
        ({"last_trade_local": "16:00"}, TypeError),
        ({"status": ""}, ValueError),
    ],
)
def test_product_rules_are_validated(changes: dict[str, Any], error: type[Exception]) -> None:
    field = next(iter(changes))
    with pytest.raises(error, match=rf"ProductRules\.{field}"):
        replace(SPXW_RULES, **changes)
