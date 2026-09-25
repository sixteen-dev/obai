"""§9.1 static semantic checks, one test per ADR 0001 §4 table row and code, exact pointers."""

import copy
import json
import sys
from decimal import Context, localcontext
from pathlib import Path

import pytest

from options_backtest import ingest
from options_backtest.errors import ErrorCode, SpecRejected
from options_backtest.ingest import load_strategy, parse_spec
from options_backtest.models.strategy import StrategySpec
from options_backtest.models.strategy_checks import (
    CheckResult,
    PremiumDirection,
    ValidatedStrategy,
    check_strategy,
)
from options_backtest.money import EXACT

EXAMPLE = Path(__file__).resolve().parents[1] / "contracts" / "example-strategy.json"

type Document = dict[str, object]
type Found = list[tuple[ErrorCode, str]]

CREDIT = PremiumDirection.CREDIT
DEBIT = PremiumDirection.DEBIT


def _target(target: int = 45, low: int = 30, high: int = 60) -> Document:
    return {"method": "target_dte", "target_dte": target, "min_dte": low, "max_dte": high}


def _same_as(anchor: str) -> Document:
    return {"method": "same_as", "anchor_leg_id": anchor}


def _delta(value: float) -> Document:
    return {"method": "delta", "target_delta": value, "tolerance": 0.03}


def _offset(anchor: str, units: str) -> Document:
    return {"method": "strike_offset", "anchor_leg_id": anchor, "offset_price_units": units}


def _moneyness(ratio: float) -> Document:
    return {"method": "moneyness", "target_strike_to_spot": ratio, "tolerance": 0.01}


def _leg(leg_id: str, side: str, option_type: str, expiry: Document, strike: Document) -> Document:
    return {
        "leg_id": leg_id,
        "side": side,
        "option_type": option_type,
        "ratio": 1,
        "expiry_selection": expiry,
        "strike_selection": strike,
    }


def _example() -> Document:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _strategy(structure: str, legs: list[Document], basis: str | None) -> Document:
    document = _example()
    document["structure"] = structure
    document["legs"] = legs
    document["exits"] = {
        "take_profit": None if basis is None else {"basis": basis, "fraction": 0.5},
        "stop_loss": None if basis is None else {"basis": basis, "multiple": 2.0},
        "exit_dte": 21,
        "max_holding_sessions": 45,
    }
    return document


def _iron_condor() -> Document:
    legs = [
        _leg("short_put", "sell", "put", _target(), _delta(-0.20)),
        _leg("long_put", "buy", "put", _same_as("short_put"), _offset("short_put", "-5")),
        _leg("short_call", "sell", "call", _same_as("short_put"), _offset("short_put", "50")),
        _leg("long_call", "buy", "call", _same_as("short_put"), _offset("short_call", "5")),
    ]
    return _strategy("iron_condor", legs, "initial_credit")


def _long_straddle() -> Document:
    legs = [
        _leg("long_call", "buy", "call", _target(), _delta(0.50)),
        _leg("long_put", "buy", "put", _same_as("long_call"), _offset("long_call", "0")),
    ]
    return _strategy("long_straddle", legs, "initial_debit")


def _long_strangle() -> Document:
    legs = [
        _leg("long_put", "buy", "put", _target(), _delta(-0.25)),
        _leg("long_call", "buy", "call", _same_as("long_put"), _offset("long_put", "20")),
    ]
    return _strategy("long_strangle", legs, "initial_debit")


def _single_long() -> Document:
    legs = [_leg("long_call", "buy", "call", _target(), _delta(0.30))]
    return _strategy("single_long", legs, "initial_debit")


def _vertical(first: Document, second: Document, basis: str | None) -> Document:
    return _strategy("vertical", [first, second], basis)


def _legs(document: Document) -> list[Document]:
    legs = document["legs"]
    assert isinstance(legs, list)
    return legs


def _section(document: Document, name: str) -> Document:
    section = document[name]
    assert isinstance(section, dict)
    return section


def _spec(document: Document) -> StrategySpec:
    return parse_spec(json.dumps(document).encode())


def _check(document: Document) -> CheckResult:
    return check_strategy(_spec(document))


def _found(document: Document) -> Found:
    return [(issue.code, issue.json_pointer) for issue in _check(document).issues]


# Valid structures and their derived facts ------------------------------------------------


@pytest.mark.parametrize(
    ("document", "leg_order", "direction"),
    [
        (_example(), ("short_put", "long_put"), CREDIT),
        (_iron_condor(), ("short_put", "long_put", "short_call", "long_call"), CREDIT),
        (_long_straddle(), ("long_call", "long_put"), DEBIT),
        (_long_strangle(), ("long_put", "long_call"), DEBIT),
        (_single_long(), ("long_call",), DEBIT),
    ],
)
def test_supported_structures_pass_with_their_leg_order_and_premium_direction(
    document: Document, leg_order: tuple[str, ...], direction: PremiumDirection
) -> None:
    assert _check(document) == CheckResult(
        issues=(), leg_order=leg_order, premium_direction=direction
    )


def test_leg_order_resolves_anchors_before_dependents_whatever_the_listing_order() -> None:
    document = _example()
    _legs(document).reverse()

    assert _check(document).leg_order == ("short_put", "long_put")


def test_check_strategy_requires_a_strategy_spec() -> None:
    with pytest.raises(TypeError, match="StrategySpec"):
        check_strategy(_example())  # type: ignore[arg-type]


# Row 1: product roots and the single expiry target ----------------------------------------


def test_spx_root_is_rejected_as_am_settled() -> None:
    document = _example()
    _section(document, "product")["allowed_option_roots"] = ["XSP", "SPX"]

    (issue,) = _check(document).issues

    assert (issue.code, issue.json_pointer) == (
        ErrorCode.UNSUPPORTED_PRODUCT,
        "/product/allowed_option_roots/1",
    )
    assert "AM-settled" in issue.message


def test_root_outside_spxw_and_xsp_is_an_unsupported_product() -> None:
    document = _example()
    _section(document, "product")["allowed_option_roots"] = ["SPY"]

    assert _found(document) == [(ErrorCode.UNSUPPORTED_PRODUCT, "/product/allowed_option_roots/0")]


def test_spxw_and_xsp_roots_are_both_supported() -> None:
    document = _example()
    _section(document, "product")["allowed_option_roots"] = ["SPXW", "XSP"]

    assert _found(document) == []


def test_a_second_target_dte_leg_is_an_invalid_selector() -> None:
    document = _example()
    _legs(document)[1]["expiry_selection"] = _target()

    assert _found(document) == [(ErrorCode.INVALID_SELECTOR, "/legs/1/expiry_selection")]


def test_no_target_dte_leg_is_an_invalid_selector() -> None:
    document = _single_long()
    _legs(document)[0]["expiry_selection"] = _same_as("long_call")

    assert _found(document) == [
        (ErrorCode.INVALID_SELECTOR, "/legs"),
        (ErrorCode.INVALID_SELECTOR, "/legs/0/expiry_selection/anchor_leg_id"),
    ]


# Row 2: signed long-option delta -------------------------------------------------------


def test_put_with_positive_delta_is_an_invalid_selector() -> None:
    document = _example()
    _legs(document)[0]["strike_selection"] = _delta(0.30)

    assert _found(document) == [
        (ErrorCode.INVALID_SELECTOR, "/legs/0/strike_selection/target_delta"),
    ]


def test_call_with_negative_delta_is_an_invalid_selector() -> None:
    document = _single_long()
    _legs(document)[0]["strike_selection"] = _delta(-0.30)

    assert _found(document) == [
        (ErrorCode.INVALID_SELECTOR, "/legs/0/strike_selection/target_delta"),
    ]


# Row 3: leg ids, anchors, leg-level acyclicity, offsetting duplicates -------------------------


def test_duplicate_leg_id_is_an_invalid_selector() -> None:
    document = _iron_condor()
    _legs(document)[3]["leg_id"] = "long_put"

    result = _check(document)

    assert [(i.code, i.json_pointer) for i in result.issues] == [
        (ErrorCode.INVALID_SELECTOR, "/legs/3/leg_id"),
    ]
    assert result.leg_order == ()


def test_missing_anchor_is_an_invalid_selector() -> None:
    document = _example()
    _legs(document)[1]["strike_selection"] = _offset("ghost", "-5")

    assert _found(document) == [
        (ErrorCode.INVALID_SELECTOR, "/legs/1/strike_selection/anchor_leg_id"),
    ]


def test_self_anchor_is_an_invalid_selector() -> None:
    document = _example()
    _legs(document)[1]["expiry_selection"] = _same_as("long_put")

    assert _found(document) == [
        (ErrorCode.INVALID_SELECTOR, "/legs/1/expiry_selection/anchor_leg_id"),
    ]


def test_anchor_cycle_between_two_legs_is_an_invalid_selector_on_each() -> None:
    document = _example()
    _legs(document)[0]["strike_selection"] = _offset("long_put", "5")

    result = _check(document)

    assert [(i.code, i.json_pointer) for i in result.issues] == [
        (ErrorCode.INVALID_SELECTOR, "/legs/0"),
        (ErrorCode.INVALID_SELECTOR, "/legs/1"),
    ]
    assert result.leg_order == ()


def test_cycle_through_expiry_and_strike_anchors_is_a_leg_level_cycle() -> None:
    document = _example()
    short_put, long_put = _legs(document)
    short_put["strike_selection"] = _offset("long_put", "5")
    long_put["strike_selection"] = _delta(-0.20)

    assert _found(document) == [
        (ErrorCode.INVALID_SELECTOR, "/legs/0"),
        (ErrorCode.INVALID_SELECTOR, "/legs/1"),
    ]


def test_opposite_side_same_type_legs_at_zero_offset_are_an_invalid_selector() -> None:
    document = _example()
    _legs(document)[1]["strike_selection"] = _offset("short_put", "0")

    assert _found(document) == [(ErrorCode.INVALID_SELECTOR, "/legs/1/strike_selection")]


def test_offsetting_legs_point_at_the_leg_carrying_the_offset() -> None:
    document = _example()
    short_put, long_put = _legs(document)
    long_put["strike_selection"] = _offset("short_put", "0")
    _legs(document)[:] = [long_put, short_put]  # the offset leg listed first

    assert _found(document) == [(ErrorCode.INVALID_SELECTOR, "/legs/0/strike_selection")]


def test_offsetting_legs_are_detected_through_an_offset_chain() -> None:
    document = _iron_condor()
    _legs(document)[3]["strike_selection"] = _offset("long_put", "55")  # = short_call strike

    assert _found(document) == [
        (ErrorCode.INVALID_SELECTOR, "/legs/3/strike_selection"),
        (ErrorCode.UNSUPPORTED_STRUCTURE, "/legs/3/strike_selection"),
    ]


def test_a_straddle_on_an_anchor_cycle_reports_only_the_cycle() -> None:
    # The call is at offset 0 from the put, so the straddle's anchor is there; the put's expiry
    # anchor back to the call makes a leg-level cycle, and that is the only true issue.
    legs = [
        _leg("long_call", "buy", "call", _target(), _offset("long_put", "0")),
        _leg("long_put", "buy", "put", _same_as("long_call"), _delta(-0.50)),
    ]

    assert _found(_strategy("long_straddle", legs, "initial_debit")) == [
        (ErrorCode.INVALID_SELECTOR, "/legs/0"),
        (ErrorCode.INVALID_SELECTOR, "/legs/1"),
    ]


def test_a_vertical_with_a_broken_anchor_reports_only_the_anchor() -> None:
    # The offset fixes the direction (credit) once the expiry anchor's typo is fixed; the
    # direction is not "undetermined", the graph is just unresolved.
    first = _leg("short_put", "sell", "put", _target(), _delta(-0.30))
    second = _leg("long_put", "buy", "put", _same_as("shortput"), _offset("short_put", "-5"))

    assert _found(_vertical(first, second, None)) == [
        (ErrorCode.INVALID_SELECTOR, "/legs/1/expiry_selection/anchor_leg_id"),
    ]


# Row 4: leg count, sides, types and strike order per structure -------------------------------


def test_leg_count_must_match_the_structure() -> None:
    document = _example()
    del _legs(document)[1]

    assert _found(document) == [(ErrorCode.UNSUPPORTED_STRUCTURE, "/legs")]


def test_single_long_requires_a_buy_leg() -> None:
    document = _single_long()
    _legs(document)[0]["side"] = "sell"

    assert _found(document) == [(ErrorCode.UNSUPPORTED_STRUCTURE, "/legs/0/side")]


def test_both_sell_vertical_is_an_unsupported_structure() -> None:
    document = _example()
    _legs(document)[1]["side"] = "sell"

    result = _check(document)

    assert [(i.code, i.json_pointer) for i in result.issues] == [
        (ErrorCode.UNSUPPORTED_STRUCTURE, "/legs/1/side"),
    ]
    assert result.premium_direction is None


def test_vertical_legs_must_share_an_option_type() -> None:
    document = _example()
    _legs(document)[1]["option_type"] = "call"

    assert _found(document) == [(ErrorCode.UNSUPPORTED_STRUCTURE, "/legs/1/option_type")]


def test_misordered_condor_is_an_unsupported_structure() -> None:
    document = _iron_condor()
    _legs(document)[3]["strike_selection"] = _offset("short_call", "-5")  # long call below

    assert _found(document) == [(ErrorCode.UNSUPPORTED_STRUCTURE, "/legs/3/strike_selection")]


def test_misordered_condor_points_at_the_leg_carrying_the_offset() -> None:
    document = _iron_condor()
    _legs(document)[1]["strike_selection"] = _offset("short_put", "5")  # long put above

    assert _found(document) == [(ErrorCode.UNSUPPORTED_STRUCTURE, "/legs/1/strike_selection")]


def test_condor_needs_one_long_and_one_short_of_each_type() -> None:
    document = _iron_condor()
    _legs(document)[2]["side"] = "buy"

    assert _found(document) == [(ErrorCode.UNSUPPORTED_STRUCTURE, "/legs")]


def test_straddle_without_a_zero_offset_anchor_is_an_unsupported_structure() -> None:
    document = _long_straddle()
    _legs(document)[1]["strike_selection"] = _delta(-0.50)

    assert _found(document) == [(ErrorCode.UNSUPPORTED_STRUCTURE, "/legs")]


def test_straddle_with_a_nonzero_offset_is_an_unsupported_structure() -> None:
    document = _long_straddle()
    _legs(document)[1]["strike_selection"] = _offset("long_call", "5")

    assert _found(document) == [(ErrorCode.UNSUPPORTED_STRUCTURE, "/legs")]


def test_straddle_needs_a_call_and_a_put() -> None:
    document = _long_straddle()
    _legs(document)[1]["option_type"] = "call"

    assert _found(document) == [(ErrorCode.UNSUPPORTED_STRUCTURE, "/legs")]


@pytest.mark.parametrize("units", ["-20", "0"])
def test_strangle_needs_put_strike_below_call_strike(units: str) -> None:
    document = _long_strangle()
    _legs(document)[1]["strike_selection"] = _offset("long_put", units)

    assert _found(document) == [(ErrorCode.UNSUPPORTED_STRUCTURE, "/legs/1/strike_selection")]


def test_misordered_strangle_points_at_the_leg_carrying_the_offset() -> None:
    legs = [
        _leg("long_call", "buy", "call", _target(), _delta(0.25)),
        _leg("long_put", "buy", "put", _same_as("long_call"), _offset("long_call", "10")),
    ]

    assert _found(_strategy("long_strangle", legs, "initial_debit")) == [
        (ErrorCode.UNSUPPORTED_STRUCTURE, "/legs/1/strike_selection"),
    ]


def test_strangle_with_independent_strikes_is_left_to_selection() -> None:
    document = _long_strangle()
    _legs(document)[1]["strike_selection"] = _delta(0.25)

    assert _found(document) == []


# Rows 5 and 9: entry DTE window ----------------------------------------------------------


@pytest.mark.parametrize(("target", "low", "high"), [(70, 30, 60), (20, 30, 60), (45, 60, 30)])
def test_target_dte_outside_its_window_is_an_invalid_rule(target: int, low: int, high: int) -> None:
    document = _example()
    _legs(document)[0]["expiry_selection"] = _target(target, low, high)

    assert _found(document) == [
        (ErrorCode.INVALID_STRATEGY_RULE, "/legs/0/expiry_selection/target_dte"),
    ]


@pytest.mark.parametrize(("target", "low", "high"), [(30, 30, 60), (60, 30, 60)])
def test_target_dte_on_a_window_edge_is_allowed(target: int, low: int, high: int) -> None:
    document = _example()
    _legs(document)[0]["expiry_selection"] = _target(target, low, high)

    assert _found(document) == []


def test_window_that_never_clears_exit_dte_is_an_invalid_rule() -> None:
    document = _example()
    _section(document, "exits")["exit_dte"] = 60

    assert _found(document) == [
        (ErrorCode.INVALID_STRATEGY_RULE, "/legs/0/expiry_selection/max_dte"),
    ]


def test_window_that_never_clears_the_roll_trigger_is_an_invalid_rule() -> None:
    document = _example()
    document["roll"] = {
        "mode": "sequential",
        "trigger_dte": 60,
        "max_rolls": 2,
        "max_campaign_sessions": 90,
    }

    assert _found(document) == [
        (ErrorCode.INVALID_STRATEGY_RULE, "/legs/0/expiry_selection/max_dte"),
    ]


def test_partly_qualifying_window_is_left_per_candidate() -> None:
    document = _example()
    _legs(document)[0]["expiry_selection"] = _target(20, 14, 60)  # target itself <= exit_dte

    assert _found(document) == []


# Row 7: premium direction and exit bases ---------------------------------------------------


@pytest.mark.parametrize(
    ("first", "second", "direction"),
    [
        # offset sign: a lower-strike put is the cheaper one
        (
            _leg("short_put", "sell", "put", _target(), _delta(-0.30)),
            _leg("long_put", "buy", "put", _same_as("short_put"), _offset("short_put", "-5")),
            CREDIT,
        ),
        # offset sign: a higher-strike call is the cheaper one
        (
            _leg("long_call", "buy", "call", _target(), _delta(0.50)),
            _leg("short_call", "sell", "call", _same_as("long_call"), _offset("long_call", "10")),
            DEBIT,
        ),
        # same-method selectors: the larger |delta| is the pricier leg
        (
            _leg("short_put", "sell", "put", _target(), _delta(-0.30)),
            _leg("long_put", "buy", "put", _same_as("short_put"), _delta(-0.20)),
            CREDIT,
        ),
        (
            _leg("short_call", "sell", "call", _target(), _delta(0.30)),
            _leg("long_call", "buy", "call", _same_as("short_call"), _delta(0.50)),
            DEBIT,
        ),
        # same-method selectors: higher strike/spot is pricier for puts, lower for calls
        (
            _leg("short_put", "sell", "put", _target(), _moneyness(0.95)),
            _leg("long_put", "buy", "put", _same_as("short_put"), _moneyness(0.90)),
            CREDIT,
        ),
        (
            _leg("long_call", "buy", "call", _target(), _moneyness(1.00)),
            _leg("short_call", "sell", "call", _same_as("long_call"), _moneyness(1.05)),
            DEBIT,
        ),
    ],
)
def test_vertical_direction_is_derived_from_the_selectors(
    first: Document, second: Document, direction: PremiumDirection
) -> None:
    result = _check(_vertical(first, second, None))

    assert result.issues == ()
    assert result.premium_direction is direction


@pytest.mark.parametrize(
    ("basis", "direction"), [("initial_debit", DEBIT), ("initial_credit", CREDIT)]
)
def test_vertical_direction_falls_back_to_the_exit_bases(
    basis: str, direction: PremiumDirection
) -> None:
    first = _leg("long_call", "buy", "call", _target(), _delta(0.50))
    second = _leg("short_call", "sell", "call", _same_as("long_call"), _moneyness(1.05))

    result = _check(_vertical(first, second, basis))

    assert result.issues == ()
    assert result.premium_direction is direction


TWENTY_NINE_DIGIT_DELTA = "-0.30000000000000000000000000001"
FIFTY_ONE_DIGIT_DELTA = "-0.3" + "0" * 49 + "1"


def _sold_leg_delta(document: Document, target_delta: str) -> bytes:
    """Return the document with the first leg's target_delta written as exact decimal text."""
    text = json.dumps(document)
    assert '"target_delta": -0.3,' in text
    return text.replace('"target_delta": -0.3,', f'"target_delta": {target_delta},', 1).encode()


@pytest.mark.parametrize(
    ("context", "sold_delta"),
    [
        pytest.param(Context(), TWENTY_NINE_DIGIT_DELTA, id="default-context"),
        pytest.param(Context(prec=40), TWENTY_NINE_DIGIT_DELTA, id="prec-40"),
        pytest.param(EXACT, FIFTY_ONE_DIGIT_DELTA, id="exact"),
    ],
)
def test_vertical_direction_from_delta_is_the_same_in_every_decimal_context(
    context: Context, sold_delta: str
) -> None:
    # The sold put's |delta| is larger by 1e-29 (or 1e-51): it is the pricier leg, a credit.
    first = _leg("short_put", "sell", "put", _target(), _delta(-0.3))
    second = _leg("long_put", "buy", "put", _same_as("short_put"), _delta(-0.3))

    with localcontext(context):
        undeclared = check_strategy(
            parse_spec(_sold_leg_delta(_vertical(first, second, None), sold_delta))
        )
        debit = check_strategy(
            parse_spec(_sold_leg_delta(_vertical(first, second, "initial_debit"), sold_delta))
        )

    assert (undeclared.issues, undeclared.premium_direction) == ((), CREDIT)
    assert [(i.code, i.json_pointer) for i in debit.issues] == [
        (ErrorCode.INVALID_STRATEGY_RULE, "/exits/stop_loss/basis"),
        (ErrorCode.INVALID_STRATEGY_RULE, "/exits/take_profit/basis"),
    ]


def test_undetermined_vertical_direction_is_an_invalid_rule() -> None:
    first = _leg("short_put", "sell", "put", _target(), _delta(-0.30))
    second = _leg("long_put", "buy", "put", _same_as("short_put"), _delta(-0.30))

    result = _check(_vertical(first, second, None))

    assert [(i.code, i.json_pointer) for i in result.issues] == [
        (ErrorCode.INVALID_STRATEGY_RULE, "/legs"),
    ]
    assert result.premium_direction is None


def test_conflicting_exit_bases_do_not_determine_a_vertical() -> None:
    first = _leg("short_put", "sell", "put", _target(), _delta(-0.30))
    second = _leg("long_put", "buy", "put", _same_as("short_put"), _moneyness(0.9))
    document = _vertical(first, second, "initial_credit")
    stop_loss = _section(document, "exits")["stop_loss"]
    assert isinstance(stop_loss, dict)
    stop_loss["basis"] = "initial_debit"

    assert _found(document) == [(ErrorCode.INVALID_STRATEGY_RULE, "/legs")]


def test_exit_basis_mismatching_a_credit_vertical_is_an_invalid_rule() -> None:
    document = _example()
    take_profit = _section(document, "exits")["take_profit"]
    assert isinstance(take_profit, dict)
    take_profit["basis"] = "initial_debit"

    assert _found(document) == [(ErrorCode.INVALID_STRATEGY_RULE, "/exits/take_profit/basis")]


@pytest.mark.parametrize(
    ("document", "basis"),
    [
        (_iron_condor(), "initial_debit"),
        (_single_long(), "initial_credit"),
        (_long_straddle(), "initial_credit"),
        (_long_strangle(), "initial_credit"),
    ],
)
def test_exit_basis_must_match_the_fixed_structure_direction(
    document: Document, basis: str
) -> None:
    stop_loss = _section(document, "exits")["stop_loss"]
    assert isinstance(stop_loss, dict)
    stop_loss["basis"] = basis

    assert _found(document) == [(ErrorCode.INVALID_STRATEGY_RULE, "/exits/stop_loss/basis")]


# Rows 8 and 9: account, sizing and liquidity feasibility ---------------------------------------


def test_zero_initial_cash_is_an_invalid_rule() -> None:
    document = _example()
    _section(document, "account")["initial_cash_usd"] = "0.00"

    assert _found(document) == [(ErrorCode.INVALID_STRATEGY_RULE, "/account/initial_cash_usd")]


@pytest.mark.parametrize(
    ("cash", "found"),
    [
        ("50000.005", [(ErrorCode.INVALID_STRATEGY_RULE, "/account/initial_cash_usd")]),
        ("0.001", [(ErrorCode.INVALID_STRATEGY_RULE, "/account/initial_cash_usd")]),
        ("0.01", []),
    ],
)
def test_initial_cash_must_be_whole_cents(cash: str, found: Found) -> None:
    # The opening deposit posts initial cash to CASH, which takes whole cents only (ADR §2).
    document = _example()
    _section(document, "account")["initial_cash_usd"] = cash

    assert _found(document) == found


def test_campaign_risk_fraction_above_total_is_an_invalid_rule() -> None:
    document = _example()
    _section(document, "account")["max_campaign_risk_fraction"] = 0.25

    assert _found(document) == [
        (ErrorCode.INVALID_STRATEGY_RULE, "/account/max_campaign_risk_fraction"),
    ]


def test_campaign_risk_fraction_equal_to_total_is_allowed() -> None:
    document = _example()
    _section(document, "account")["max_campaign_risk_fraction"] = 0.2

    assert _found(document) == []


def test_fixed_contracts_above_the_order_cap_is_an_invalid_rule() -> None:
    document = _example()
    document["sizing"] = {"method": "fixed_contracts", "contracts": 3}
    _section(document, "execution")["max_contracts_per_order"] = 2

    assert _found(document) == [(ErrorCode.INVALID_STRATEGY_RULE, "/sizing/contracts")]


def test_fixed_contracts_equal_to_the_order_cap_is_allowed() -> None:
    document = _example()
    document["sizing"] = {"method": "fixed_contracts", "contracts": 2}
    _section(document, "execution")["max_contracts_per_order"] = 2

    assert _found(document) == []


def test_risk_budget_above_the_order_cap_is_not_an_error() -> None:
    document = _example()
    document["sizing"] = {"method": "risk_budget", "max_contracts": 10}
    _section(document, "execution")["max_contracts_per_order"] = 2

    assert _found(document) == []


def test_zero_absolute_spread_cap_is_an_invalid_rule() -> None:
    document = _example()
    _section(document, "liquidity")["max_absolute_spread_price_units"] = "0"

    assert _found(document) == [
        (ErrorCode.INVALID_STRATEGY_RULE, "/liquidity/max_absolute_spread_price_units"),
    ]


# Row 10: open interest entitlement and roll reachability --------------------------------------


def test_min_open_interest_1_is_a_missing_data_entitlement() -> None:
    document = _example()
    _section(document, "liquidity")["min_open_interest"] = 1

    (issue,) = _check(document).issues

    assert (issue.code, issue.json_pointer) == (
        ErrorCode.DATA_ENTITLEMENT_MISSING,
        "/liquidity/min_open_interest",
    )
    assert issue.missing_capability == "historical_open_interest"


@pytest.mark.parametrize("trigger_dte", [21, 14])
def test_roll_trigger_at_or_below_exit_dte_is_an_invalid_rule(trigger_dte: int) -> None:
    document = _example()
    document["roll"] = {
        "mode": "sequential",
        "trigger_dte": trigger_dte,
        "max_rolls": 2,
        "max_campaign_sessions": 90,
    }

    assert _found(document) == [(ErrorCode.INVALID_STRATEGY_RULE, "/roll/trigger_dte")]


def test_reachable_sequential_roll_is_accepted() -> None:
    document = _example()
    document["roll"] = {
        "mode": "sequential",
        "trigger_dte": 30,
        "max_rolls": 2,
        "max_campaign_sessions": 90,
    }

    assert _found(document) == []


# Reporting ---------------------------------------------------------------------------


def test_every_issue_is_reported_sorted_by_pointer_then_code() -> None:
    document = _example()
    _section(document, "liquidity")["min_open_interest"] = 5
    _section(document, "account")["initial_cash_usd"] = "0"
    _section(document, "product")["allowed_option_roots"] = ["SPX"]
    _legs(document)[0]["strike_selection"] = _delta(0.30)

    assert _found(document) == [
        (ErrorCode.INVALID_STRATEGY_RULE, "/account/initial_cash_usd"),
        (ErrorCode.INVALID_SELECTOR, "/legs/0/strike_selection/target_delta"),
        (ErrorCode.DATA_ENTITLEMENT_MISSING, "/liquidity/min_open_interest"),
        (ErrorCode.UNSUPPORTED_PRODUCT, "/product/allowed_option_roots/0"),
    ]


# ValidatedStrategy: an unchecked spec cannot exist as this type -------------------------------


def test_validated_strategy_accepts_exactly_the_checked_facts() -> None:
    spec = _spec(_example())

    strategy = ValidatedStrategy(spec, ("short_put", "long_put"), CREDIT)

    assert strategy.spec is spec


def test_validated_strategy_reruns_the_checks() -> None:
    document = _example()
    _section(document, "liquidity")["min_open_interest"] = 1

    with pytest.raises(SpecRejected) as caught:
        ValidatedStrategy(_spec(document), ("short_put", "long_put"), CREDIT)

    assert [issue.code for issue in caught.value.issues] == [ErrorCode.DATA_ENTITLEMENT_MISSING]


@pytest.mark.parametrize(
    ("leg_order", "direction"),
    [(("long_put", "short_put"), CREDIT), (("short_put", "long_put"), DEBIT), ((), None)],
)
def test_validated_strategy_rejects_facts_the_checks_did_not_derive(
    leg_order: tuple[str, ...], direction: PremiumDirection | None
) -> None:
    with pytest.raises(ValueError, match="check_strategy"):
        ValidatedStrategy(_spec(_example()), leg_order, direction)  # type: ignore[arg-type]


def test_spec_models_refuse_unvalidated_updates() -> None:
    # pydantic applies model_copy(update=...) without validation; copy.replace goes through it.
    spec = _spec(_example())

    with pytest.raises(TypeError, match="validation"):
        spec.model_copy(update={"schema_version": 99})
    with pytest.raises(TypeError, match="validation"):
        spec.exits.model_copy(update={"exit_dte": -5})
    if sys.version_info >= (3, 13):  # copy.replace is new in Python 3.13
        with pytest.raises(TypeError, match="validation"):
            copy.replace(spec, name="")


def test_spec_models_copy_unchanged() -> None:
    spec = _spec(_example())

    assert spec.model_copy() == spec
    assert spec.model_copy(update={}, deep=True) == spec


def test_load_strategy_refuses_a_passing_check_without_a_premium_direction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Unreachable by the real checks (row 7 rejects an undetermined direction): a check that
    # passed with no direction is a bug, never a ValidatedStrategy without one.
    def passing_without_direction(spec: StrategySpec) -> CheckResult:
        return CheckResult((), ("short_put", "long_put"), None)

    monkeypatch.setattr(ingest, "check_strategy", passing_without_direction)

    with pytest.raises(AssertionError, match="premium direction"):
        load_strategy(EXAMPLE.read_bytes())


def test_validated_strategy_requires_a_strategy_spec() -> None:
    with pytest.raises(TypeError, match="StrategySpec"):
        ValidatedStrategy(_example(), ("short_put", "long_put"), CREDIT)  # type: ignore[arg-type]
