"""Contract terms, deliverables and quotes (ADR 0001 §3, design §8.2, §12.2, fixture F05)."""

import dataclasses
import json
import re
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from options_backtest.errors import MissingMarkError
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    ExerciseStyle,
    OptionType,
    Quote,
    SettlementType,
    require_id,
    require_int,
    require_non_negative_usd,
    require_type,
)
from options_backtest.money import Price, Usd

LEDGER_FIXTURES = Path(__file__).resolve().parents[1] / "contracts" / "ledger-fixtures.json"
EXPIRES_AT_NS = 1_700_000_000_000_000_000


def usd(text: str) -> Usd:
    return Usd(Decimal(text))


def price(text: str) -> Price:
    return Price(Decimal(text))


def deliverable(asset_id: str = "SPX", units: str = "100", cash: str = "0") -> Deliverable:
    component = DeliverableComponent(asset_id, Decimal(units))
    return Deliverable(f"{asset_id}-x{units}", (component,), usd(cash))


def standard_terms(option_type: OptionType, strike: str, **overrides: Any) -> ContractTerms:
    """Build a 100-unit contract with aggregate exercise amount 100*K, as in design §12.2."""
    fields: dict[str, Any] = {
        "contract_id": f"SPXW-{option_type.value}-{strike}",
        "option_type": option_type,
        "strike": price(strike),
        "exercise_style": ExerciseStyle.EUROPEAN,
        "settlement_type": SettlementType.CASH,
        "premium_multiplier": Decimal("100"),
        "deliverable": deliverable(),
        "aggregate_exercise_amount": usd(strike).scaled_by(100),
        "expires_at_ns": EXPIRES_AT_NS,
    }
    fields.update(overrides)
    return ContractTerms(**fields)


def f05_fixture() -> dict[str, Any]:
    fixtures = json.loads(LEDGER_FIXTURES.read_text(encoding="utf-8"))["fixtures"]
    matches = [fixture for fixture in fixtures if fixture["id"] == "F05"]
    assert len(matches) == 1
    return dict(matches[0])


def f05_terms(phase: dict[str, str], option_type: str, strike: str) -> ContractTerms:
    component = DeliverableComponent("XYZ", Decimal(phase["deliverable_shares"]))
    return ContractTerms(
        contract_id=f"XYZ-F05-{phase['deliverable_shares']}",
        option_type=OptionType(option_type),
        strike=price(strike),
        exercise_style=ExerciseStyle.AMERICAN,
        settlement_type=SettlementType.PHYSICAL,
        premium_multiplier=Decimal(phase["premium_multiplier"]),
        deliverable=Deliverable(f"XYZ-x{phase['deliverable_shares']}", (component,), usd("0")),
        aggregate_exercise_amount=usd(phase["aggregate_exercise_amount_usd"]),
        expires_at_ns=EXPIRES_AT_NS,
    )


# --- enums -----------------------------------------------------------------------------------


def test_enum_values() -> None:
    assert [member.value for member in OptionType] == ["call", "put"]
    assert [member.value for member in ExerciseStyle] == ["european", "american"]
    assert [member.value for member in SettlementType] == ["cash", "physical"]


def test_option_type_payoff_sign_is_the_design_e() -> None:
    assert OptionType.CALL.payoff_sign == 1
    assert OptionType.PUT.payoff_sign == -1


# --- DeliverableComponent / Deliverable ------------------------------------------------------


@pytest.mark.parametrize("units", ["0", "-100", "NaN", "Infinity"])
def test_deliverable_component_units_must_be_finite_and_positive(units: str) -> None:
    with pytest.raises(ValueError, match="units"):
        DeliverableComponent("XYZ", Decimal(units))


def test_deliverable_component_units_must_be_exactly_decimal() -> None:
    with pytest.raises(TypeError, match="units"):
        DeliverableComponent("XYZ", 100)  # type: ignore[arg-type]


def test_deliverable_component_needs_an_asset_id() -> None:
    with pytest.raises(ValueError, match="asset_id"):
        DeliverableComponent("", Decimal("100"))


def test_deliverable_needs_an_id() -> None:
    with pytest.raises(ValueError, match="deliverable_id"):
        Deliverable("", (DeliverableComponent("XYZ", Decimal("100")),), usd("0"))


def test_deliverable_needs_a_tuple_of_components() -> None:
    component = DeliverableComponent("XYZ", Decimal("100"))
    with pytest.raises(TypeError, match="components"):
        Deliverable("d", [component], usd("0"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="components"):
        Deliverable("d", ("XYZ",), usd("0"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="at least one component"):
        Deliverable("d", (), usd("0"))


def test_deliverable_rejects_duplicate_assets() -> None:
    component = DeliverableComponent("XYZ", Decimal("100"))
    with pytest.raises(ValueError, match="duplicate"):
        Deliverable("d", (component, component), usd("0"))


def test_deliverable_cash_must_be_non_negative_usd() -> None:
    with pytest.raises(ValueError, match="cash"):
        deliverable(cash="-1")
    with pytest.raises(TypeError, match="cash"):
        Deliverable("d", (DeliverableComponent("XYZ", Decimal("1")),), Decimal("0"))  # type: ignore[arg-type]


def test_deliverable_value_is_units_times_price_plus_cash() -> None:
    components = (
        DeliverableComponent("AAA", Decimal("10")),
        DeliverableComponent("BBB", Decimal("2.5")),
    )
    basket = Deliverable("basket", components, usd("25.50"))
    prices = {"AAA": price("50.01"), "BBB": price("3.333"), "UNUSED": price("1")}

    assert basket.value_usd(prices) == usd("533.9325")


def test_deliverable_value_names_every_unpriced_asset() -> None:
    components = (
        DeliverableComponent("AAA", Decimal("10")),
        DeliverableComponent("BBB", Decimal("1")),
        DeliverableComponent("CCC", Decimal("1")),
    )
    basket = Deliverable("basket", components, usd("0"))

    with pytest.raises(MissingMarkError) as caught:
        basket.value_usd({"BBB": price("1")})
    assert caught.value.instrument_ids == ("AAA", "CCC")


def test_deliverable_value_raises_instead_of_rounding() -> None:
    half_unit = deliverable(asset_id="XYZ", units="0.5")

    with pytest.raises(ValueError, match="9 decimal places"):
        half_unit.value_usd({"XYZ": price("0.000000001")})


# --- ContractTerms ---------------------------------------------------------------------------


def test_contract_terms_are_frozen_and_compare_by_value() -> None:
    terms = standard_terms(OptionType.PUT, "100")

    assert terms == standard_terms(OptionType.PUT, "100")
    assert hash(terms) == hash(standard_terms(OptionType.PUT, "100"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        terms.strike = price("95")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "bad", "error"),
    [
        ("contract_id", "", ValueError),
        ("contract_id", 7, TypeError),
        ("option_type", "put", TypeError),
        ("strike", Decimal("100"), TypeError),
        ("exercise_style", "european", TypeError),
        ("settlement_type", "cash", TypeError),
        ("premium_multiplier", 100, TypeError),
        ("premium_multiplier", Decimal("0"), ValueError),
        ("premium_multiplier", Decimal("-100"), ValueError),
        ("deliverable", "SPX", TypeError),
        ("aggregate_exercise_amount", Decimal("10000"), TypeError),
        ("aggregate_exercise_amount", Usd(Decimal("-0.01")), ValueError),
        ("expires_at_ns", True, TypeError),
        ("expires_at_ns", 1.7e18, TypeError),
    ],
)
def test_contract_terms_guards(field: str, bad: object, error: type[Exception]) -> None:
    with pytest.raises(error, match=field):
        standard_terms(OptionType.PUT, "100", **{field: bad})


def test_contract_terms_do_not_assume_aea_equals_units_times_strike() -> None:
    f05 = f05_fixture()
    after = f05_terms(f05["after"], f05["option_type"], f05["after"]["listed_strike"])

    assert after.deliverable.components[0].units * after.strike.value != Decimal("6000")
    assert after.aggregate_exercise_amount == usd("6000.00")


@pytest.mark.parametrize(
    ("mark", "contracts", "expected"),
    [("2.00", -1, "-200.00"), ("1.10", 1, "110.00"), ("1.005", 3, "301.500"), ("2.00", 0, "0")],
)
def test_premium_is_contracts_times_multiplier_times_price(
    mark: str, contracts: int, expected: str
) -> None:
    terms = standard_terms(OptionType.PUT, "100")

    assert terms.premium_usd(price(mark), contracts) == usd(expected)


def test_premium_uses_the_multiplier_not_the_deliverable() -> None:
    terms = standard_terms(OptionType.PUT, "100", deliverable=deliverable(units="50"))

    assert terms.premium_usd(price("10.00"), 1) == usd("1000.00")


def test_premium_guards() -> None:
    terms = standard_terms(OptionType.PUT, "100")

    with pytest.raises(TypeError, match="contracts"):
        terms.premium_usd(price("1"), True)
    with pytest.raises(TypeError, match="contracts"):
        terms.premium_usd(price("1"), 1.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="price"):
        terms.premium_usd(Decimal("1"), 1)  # type: ignore[arg-type]


# Design §12.2, one standard contract (deliverable 100 units, AEA 100*K), K = 100:
# (option type, position sign s, stock change, cash change).
SECTION_12_2_ROWS = [
    pytest.param(OptionType.CALL, 1, Decimal("100"), Decimal("-10000"), id="long call exercise"),
    pytest.param(
        OptionType.CALL, -1, Decimal("-100"), Decimal("10000"), id="short call assignment"
    ),
    pytest.param(OptionType.PUT, 1, Decimal("-100"), Decimal("10000"), id="long put exercise"),
    pytest.param(OptionType.PUT, -1, Decimal("100"), Decimal("-10000"), id="short put assignment"),
]


@pytest.mark.parametrize(("option_type", "sign", "stock_change", "cash_change"), SECTION_12_2_ROWS)
@pytest.mark.parametrize("spot", ["90", "97.25", "110", "123.45"])
def test_intrinsic_equals_the_section_12_2_transition_value_when_in_the_money(
    option_type: OptionType, sign: int, stock_change: Decimal, cash_change: Decimal, spot: str
) -> None:
    terms = standard_terms(option_type, "100")
    prices = {"SPX": price(spot)}
    transition_value = stock_change * Decimal(spot) + cash_change
    in_the_money = transition_value * sign > 0

    position_value = terms.intrinsic_usd(prices).scaled_by(sign)

    assert position_value == (Usd(transition_value) if in_the_money else usd("0"))


@pytest.mark.parametrize(
    ("option_type", "spot", "expected"),
    [
        (OptionType.CALL, "103.50", "350.00"),
        (OptionType.CALL, "100", "0"),
        (OptionType.CALL, "99.99", "0"),
        (OptionType.PUT, "97", "300.00"),
        (OptionType.PUT, "100", "0"),
        (OptionType.PUT, "100.01", "0"),
        (OptionType.PUT, "0", "10000.00"),
    ],
)
def test_intrinsic_of_a_standard_contract(
    option_type: OptionType, spot: str, expected: str
) -> None:
    terms = standard_terms(option_type, "100")

    assert terms.intrinsic_usd({"SPX": price(spot)}) == usd(expected)


def test_intrinsic_includes_deliverable_cash() -> None:
    terms = standard_terms(
        OptionType.CALL,
        "100",
        deliverable=deliverable(units="50", cash="1000"),
        aggregate_exercise_amount=usd("6000"),
    )

    assert terms.intrinsic_usd({"SPX": price("110")}) == usd("500")


def test_intrinsic_requires_a_price_for_the_deliverable() -> None:
    terms = standard_terms(OptionType.PUT, "100")

    with pytest.raises(MissingMarkError) as caught:
        terms.intrinsic_usd({"XSP": price("10")})
    assert caught.value.instrument_ids == ("SPX",)


@pytest.mark.parametrize("phase", ["before", "after"])
def test_f05_intrinsic_and_market_value_follow_deliverable_and_aea(phase: str) -> None:
    f05 = f05_fixture()
    terms_data, expected = f05[phase], f05["expected"]
    terms = f05_terms(terms_data, f05["option_type"], terms_data["listed_strike"])
    prices = {"XYZ": price(terms_data["stock_mark"])}
    mark = price(terms_data["option_premium_mark"])

    assert terms.deliverable.value_usd(prices) == usd(expected[f"{phase}_deliverable_value_usd"])
    assert terms.intrinsic_usd(prices) == usd(expected[f"{phase}_put_intrinsic_usd"])
    assert terms.premium_usd(mark, f05["position_quantity"]) == usd(
        expected[f"{phase}_option_market_value_usd"]
    )


def test_f05_intrinsic_does_not_depend_on_the_listed_strike() -> None:
    f05 = f05_fixture()
    listed = f05_terms(f05["after"], f05["option_type"], f05["after"]["listed_strike"])
    other_strike = f05_terms(f05["after"], f05["option_type"], "1")
    prices = {"XYZ": price(f05["after"]["stock_mark"])}

    assert other_strike.intrinsic_usd(prices) == listed.intrinsic_usd(prices) == usd("1000.00")


# --- Quote -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("bid", "ask"), [("2.00", "2.20"), ("0", "0.05"), ("1.10", "1.10")])
def test_quote_accepts_ordered_zero_bid_and_locked_markets(bid: str, ask: str) -> None:
    quote = Quote(price(bid), price(ask))

    assert (quote.bid, quote.ask) == (price(bid), price(ask))


def test_quote_rejects_a_crossed_market() -> None:
    with pytest.raises(ValueError, match="bid"):
        Quote(price("2.21"), price("2.20"))


def test_quote_rejects_a_zero_ask() -> None:
    with pytest.raises(ValueError, match="ask"):
        Quote(price("0"), price("0"))


def test_quote_sides_must_be_prices() -> None:
    with pytest.raises(TypeError, match="bid"):
        Quote(Decimal("1"), price("2"))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ask"):
        Quote(price("1"), Decimal("2"))  # type: ignore[arg-type]


# --- Field guards shared with models.ledger --------------------------------------------------


@pytest.mark.parametrize(
    ("guard", "error", "message"),
    [
        (lambda: require_type(usd("1"), Price, "f"), TypeError, "f must be Price, got Usd"),
        (lambda: require_id(7, "f"), TypeError, "f must be str, got int"),
        (lambda: require_id("", "f"), ValueError, "f must be non-empty"),
        (lambda: require_int(True, "f"), TypeError, "f must be int, got bool"),
        (
            lambda: require_non_negative_usd(Decimal(1), "f"),
            TypeError,
            "f must be Usd, got Decimal",
        ),
        (
            lambda: require_non_negative_usd(usd("-0.01"), "f"),
            ValueError,
            "f must be >= 0, got -0.01",
        ),
    ],
)
def test_field_guards_name_the_field_in_one_message_per_rule(
    guard: Callable[[], None], error: type[Exception], message: str
) -> None:
    with pytest.raises(error, match=f"^{re.escape(message)}$"):
        guard()


def test_field_guards_accept_valid_values() -> None:
    require_type(price("1"), Price, "f")
    require_id("SPXW", "f")
    require_int(0, "f")
    require_non_negative_usd(usd("0"), "f")
