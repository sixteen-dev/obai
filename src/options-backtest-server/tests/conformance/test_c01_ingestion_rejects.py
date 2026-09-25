"""C01: malformed or non-conforming strategies are rejected before any job (design §18.2).

Also pins the strict-mode traps of ADR 0001 §4 step 5, so a Pydantic resolver bump that
starts accepting ``true`` or ``1.0`` for an integer cannot change behavior silently.
"""

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from options_backtest.errors import ErrorCode, SpecRejected
from options_backtest.ingest import load_strategy
from options_backtest.models.strategy import DeltaStrike, StrikeOffset
from options_backtest.models.strategy_checks import PremiumDirection, ValidatedStrategy
from options_backtest.money import Price, Usd
from options_backtest.strict_json import MAX_DEPTH, MAX_DOCUMENT_BYTES

EXAMPLE = Path(__file__).resolve().parents[1] / "contracts" / "example-strategy.json"

type Path_ = tuple[str | int, ...]


def _example() -> dict[str, object]:
    document = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _set(document: object, path: Path_, value: object) -> None:
    *parents, last = path
    for part in parents:
        document = document[part]  # type: ignore[index]
    document[last] = value  # type: ignore[index]


def _mutated(path: Path_, value: object) -> bytes:
    document = copy.deepcopy(_example())
    _set(document, path, value)
    return json.dumps(document).encode()


def _without(path: Path_) -> bytes:
    document = copy.deepcopy(_example())
    *parents, last = path
    parent: object = document
    for part in parents:
        parent = parent[part]  # type: ignore[index]
    del parent[last]  # type: ignore[attr-defined]
    return json.dumps(document).encode()


def _issues(raw: bytes) -> list[tuple[ErrorCode, str]]:
    with pytest.raises(SpecRejected) as caught:
        load_strategy(raw)
    return [(issue.code, issue.json_pointer) for issue in caught.value.issues]


def test_the_vendored_example_strategy_loads() -> None:
    strategy = load_strategy(EXAMPLE.read_bytes())

    assert isinstance(strategy, ValidatedStrategy)
    assert strategy.leg_order == ("short_put", "long_put")
    assert strategy.premium_direction is PremiumDirection.CREDIT
    spec = strategy.spec
    short_put, long_put = spec.legs
    assert isinstance(short_put.strike_selection, DeltaStrike)
    assert str(short_put.strike_selection.target_delta) == "-0.30"
    assert isinstance(long_put.strike_selection, StrikeOffset)
    assert long_put.strike_selection.offset_price_units == Decimal("-5.00")
    assert spec.account.initial_cash_usd == Usd(Decimal("50000.00"))
    assert spec.liquidity.max_absolute_spread_price_units == Price(Decimal("0.50"))
    assert spec.execution.price_allowance_usd == Usd(Decimal("0.00"))


def test_semantic_issues_reject_through_load_strategy() -> None:
    raw = _mutated(("product", "allowed_option_roots"), ["SPX"])

    assert _issues(raw) == [(ErrorCode.UNSUPPORTED_PRODUCT, "/product/allowed_option_roots/0")]


def test_spec_is_frozen() -> None:
    spec = load_strategy(EXAMPLE.read_bytes()).spec

    with pytest.raises(ValueError, match="frozen"):
        spec.name = "changed"  # type: ignore[misc]


def test_spec_dumps_without_serializer_warnings() -> None:
    # pytest turns warnings into errors; decimal-string fields dump in their schema shape.
    spec = load_strategy(EXAMPLE.read_bytes()).spec

    dumped = spec.model_dump()
    json.loads(spec.model_dump_json())

    assert dumped["account"]["initial_cash_usd"] == "50000.00"
    assert dumped["liquidity"]["max_absolute_spread_price_units"] == "0.50"
    assert dumped["legs"][1]["strike_selection"]["offset_price_units"] == "-5.00"


def test_load_strategy_requires_bytes() -> None:
    with pytest.raises(TypeError, match="bytes"):
        load_strategy(EXAMPLE.read_text(encoding="utf-8"))  # type: ignore[arg-type]


def test_nested_duplicate_key_is_rejected_at_its_pointer() -> None:
    text = json.dumps(_example())
    raw = text.replace('"side": "buy"', '"side": "buy", "side": "sell"', 1).encode()

    assert _issues(raw) == [(ErrorCode.DUPLICATE_KEY, "/legs/1/side")]


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_number_is_rejected(literal: str) -> None:
    text = json.dumps(_example())
    raw = text.replace('"target_delta": -0.3', f'"target_delta": {literal}', 1).encode()

    assert _issues(raw) == [
        (ErrorCode.NONFINITE_NUMBER, "/legs/0/strike_selection/target_delta"),
    ]


@pytest.mark.parametrize(
    ("path", "pointer"),
    [
        (("leverage",), "/leverage"),
        (("legs", 0, "notes"), "/legs/0/notes"),
        (("legs", 1, "strike_selection", "round_to"), "/legs/1/strike_selection/round_to"),
        (("roll", "trigger_dte"), "/roll/trigger_dte"),
        (("oneOf:delta",), "/oneOf:delta"),
        (("account", "a/b~c"), "/account/a~1b~0c"),
    ],
)
def test_unknown_field_is_rejected_at_its_pointer(path: Path_, pointer: str) -> None:
    assert _issues(_mutated(path, 1)) == [(ErrorCode.UNKNOWN_FIELD, pointer)]


@pytest.mark.parametrize("literal", ["true", "1.0", "1e0", "1E+0", '"1"'])
def test_ratio_must_be_the_integer_1(literal: str) -> None:
    raw = json.dumps(_example()).replace('"ratio": 1', f'"ratio": {literal}', 1).encode()

    assert _issues(raw) == [(ErrorCode.SCHEMA_VIOLATION, "/legs/0/ratio")]


@pytest.mark.parametrize("value", [True, 1.0, "1"])
def test_fixed_contracts_must_be_an_integer(value: object) -> None:
    assert _issues(_mutated(("sizing", "contracts"), value)) == [
        (ErrorCode.SCHEMA_VIOLATION, "/sizing/contracts"),
    ]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("account", "max_concurrent_campaigns"), True),
        (("account", "max_concurrent_campaigns"), 1.0),
        (("execution", "fill_attempts"), 3.0),
    ],
)
def test_integer_constants_reject_booleans_and_integral_fractions(
    path: Path_, value: object
) -> None:
    pointer = "/" + "/".join(str(part) for part in path)

    assert _issues(_mutated(path, value)) == [(ErrorCode.SCHEMA_VIOLATION, pointer)]


@pytest.mark.parametrize("value", [True, "0.03"])
def test_number_fields_reject_booleans_and_strings(value: object) -> None:
    assert _issues(_mutated(("legs", 0, "strike_selection", "tolerance"), value)) == [
        (ErrorCode.SCHEMA_VIOLATION, "/legs/0/strike_selection/tolerance"),
    ]


@pytest.mark.parametrize(
    ("literal", "found"),
    [
        ("1e1000000000000000000", [(ErrorCode.RESOURCE_LIMIT, "")]),
        ("1e999999999999999999", [(ErrorCode.SCHEMA_VIOLATION, "/exits/stop_loss/multiple")]),
    ],
)
def test_a_number_beyond_the_decimal_exponent_range_is_a_resource_limit(
    literal: str, found: list[tuple[ErrorCode, str]]
) -> None:
    raw = json.dumps(_example()).replace('"multiple": 2.0', f'"multiple": {literal}', 1).encode()

    assert _issues(raw) == found


def test_number_fields_accept_json_integers() -> None:
    strategy = load_strategy(_mutated(("exits", "stop_loss", "multiple"), 2))

    assert strategy.spec.exits.stop_loss is not None
    assert strategy.spec.exits.stop_loss.multiple == Decimal(2)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("account", "initial_cash_usd"), 50000),
        (("account", "initial_cash_usd"), 50000.0),
        (("execution", "price_allowance_usd"), 0),
        (("liquidity", "max_absolute_spread_price_units"), 0.5),
        (("legs", 1, "strike_selection", "offset_price_units"), -5),
    ],
)
def test_decimal_string_fields_reject_numbers(path: Path_, value: object) -> None:
    pointer = "/" + "/".join(str(part) for part in path)

    assert _issues(_mutated(path, value)) == [(ErrorCode.SCHEMA_VIOLATION, pointer)]


@pytest.mark.parametrize(
    "value", ["050000.00", "5e4", "50000.0000000001", "-50000", "50000.", "50000.00\n", " 1"]
)
def test_unsigned_decimal_strings_must_fullmatch_the_schema_pattern(value: str) -> None:
    assert _issues(_mutated(("account", "initial_cash_usd"), value)) == [
        (ErrorCode.SCHEMA_VIOLATION, "/account/initial_cash_usd"),
    ]


@pytest.mark.parametrize(
    "value",
    [2, 0, "1", True, 1.0, None, [1]],
)
def test_unsupported_schema_version_is_the_only_issue(value: object) -> None:
    document = copy.deepcopy(_example())
    document["schema_version"] = value
    document["leverage"] = 2  # would be UNKNOWN_FIELD past the version gate

    assert _issues(json.dumps(document).encode()) == [
        (ErrorCode.UNSUPPORTED_SCHEMA_VERSION, "/schema_version"),
    ]


def test_missing_schema_version_is_unsupported() -> None:
    assert _issues(_without(("schema_version",))) == [
        (ErrorCode.UNSUPPORTED_SCHEMA_VERSION, "/schema_version"),
    ]


def test_document_that_is_not_an_object_is_a_schema_violation() -> None:
    assert _issues(b"[]") == [(ErrorCode.SCHEMA_VIOLATION, "")]


def test_document_over_64_kib_is_a_resource_limit() -> None:
    raw = EXAMPLE.read_bytes() + b" " * MAX_DOCUMENT_BYTES

    assert _issues(raw) == [(ErrorCode.RESOURCE_LIMIT, "")]


def test_nesting_beyond_16_containers_is_a_resource_limit() -> None:
    deep: object = 1
    for _ in range(MAX_DEPTH):  # the document object itself is container 1
        deep = [deep]

    assert _issues(_mutated(("name",), deep)) == [
        (ErrorCode.RESOURCE_LIMIT, "/name" + "/0" * (MAX_DEPTH - 1)),
    ]


def test_byte_order_mark_is_malformed_json() -> None:
    assert _issues(b"\xef\xbb\xbf" + EXAMPLE.read_bytes()) == [(ErrorCode.MALFORMED_JSON, "")]


def test_invalid_utf8_is_malformed_json() -> None:
    raw = EXAMPLE.read_bytes().replace(b"Illustrative", b"Illustr\xc3\x28tive", 1)

    assert _issues(raw) == [(ErrorCode.MALFORMED_JSON, "")]


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("structure",), "custom", ErrorCode.UNSUPPORTED_STRUCTURE),
        (("product", "family"), "us_american_equity", ErrorCode.UNSUPPORTED_PRODUCT),
        (("account", "policy"), "reg_t_margin_v1", ErrorCode.UNSUPPORTED_ACCOUNT_STATE),
        (("account", "currency"), "EUR", ErrorCode.SCHEMA_VIOLATION),
        (("clock_profile",), "intraday_v1", ErrorCode.SCHEMA_VIOLATION),
    ],
)
def test_unsupported_enumerated_value_has_its_specific_code(
    path: Path_, value: str, code: ErrorCode
) -> None:
    pointer = "/" + "/".join(str(part) for part in path)

    assert _issues(_mutated(path, value)) == [(code, pointer)]


@pytest.mark.parametrize(
    ("path", "value", "pointer"),
    [
        (("legs", 0, "strike_selection", "method"), "nearest", "/legs/0/strike_selection/method"),
        (("legs", 0, "strike_selection", "method"), 7, "/legs/0/strike_selection/method"),
        (("legs", 1, "expiry_selection", "method"), "any", "/legs/1/expiry_selection/method"),
        (("roll", "mode"), "calendar", "/roll/mode"),
        (("entry", "schedule", "frequency"), "hourly", "/entry/schedule/frequency"),
        (("sizing", "method"), "kelly", "/sizing/method"),
        (("sizing",), 5, "/sizing"),
    ],
)
def test_one_of_tag_errors_point_at_the_discriminating_member(
    path: Path_, value: object, pointer: str
) -> None:
    assert _issues(_mutated(path, value)) == [(ErrorCode.SCHEMA_VIOLATION, pointer)]


def test_missing_discriminator_points_at_it() -> None:
    assert _issues(_without(("roll", "mode"))) == [(ErrorCode.SCHEMA_VIOLATION, "/roll/mode")]


def test_errors_inside_a_one_of_branch_drop_the_branch_tag_from_the_pointer() -> None:
    raw = _mutated(("legs", 0, "strike_selection", "tolerance"), 0.5)

    assert _issues(raw) == [(ErrorCode.SCHEMA_VIOLATION, "/legs/0/strike_selection/tolerance")]


def test_empty_object_is_not_an_empty_array() -> None:
    assert _issues(_mutated(("entry", "all_conditions"), {})) == [
        (ErrorCode.SCHEMA_VIOLATION, "/entry/all_conditions"),
    ]


def test_repeated_option_roots_violate_unique_items() -> None:
    assert _issues(_mutated(("product", "allowed_option_roots"), ["XSP", "XSP"])) == [
        (ErrorCode.SCHEMA_VIOLATION, "/product/allowed_option_roots"),
    ]


def test_unique_items_is_judged_only_when_every_item_is_valid() -> None:
    # ADR 0001 §11 (WP1 review): item errors hide a uniqueItems violation of the same array.
    assert _issues(_mutated(("product", "allowed_option_roots"), ["XSP", "XSP", "bad"])) == [
        (ErrorCode.SCHEMA_VIOLATION, "/product/allowed_option_roots/2"),
    ]


@pytest.mark.parametrize("value", [0, 0.0, -0.0])
def test_zero_target_delta_is_rejected(value: float) -> None:
    assert _issues(_mutated(("legs", 0, "strike_selection", "target_delta"), value)) == [
        (ErrorCode.SCHEMA_VIOLATION, "/legs/0/strike_selection/target_delta"),
    ]


def test_every_issue_is_reported_sorted_by_pointer_then_code() -> None:
    document = copy.deepcopy(_example())
    document["zzz"] = 1
    _set(document, ("legs", 1, "ratio"), True)
    _set(document, ("legs", 0, "side"), "short")
    _set(document, ("name",), "")
    _set(document, ("structure",), "butterfly")

    assert _issues(json.dumps(document).encode()) == [
        (ErrorCode.SCHEMA_VIOLATION, "/legs/0/side"),
        (ErrorCode.SCHEMA_VIOLATION, "/legs/1/ratio"),
        (ErrorCode.SCHEMA_VIOLATION, "/name"),
        (ErrorCode.UNSUPPORTED_STRUCTURE, "/structure"),
        (ErrorCode.UNKNOWN_FIELD, "/zzz"),
    ]


@pytest.mark.parametrize(
    ("path", "count", "pointer"),
    [
        (("legs",), 5, "/legs"),
        (("legs",), 0, "/legs"),
        (("product", "allowed_option_roots"), 5, "/product/allowed_option_roots"),
        (("entry", "all_conditions"), 9, "/entry/all_conditions"),
    ],
)
def test_item_counts_are_judged_on_the_input_even_when_items_are_invalid(
    path: Path_, count: int, pointer: str
) -> None:
    # ADR 0001 §11 (WP1 review): a count violation suppresses that array's item errors.
    invalid_item = {"bogus": True}

    assert _issues(_mutated(path, [invalid_item] * count)) == [
        (ErrorCode.SCHEMA_VIOLATION, pointer),
    ]


def test_invalid_items_do_not_make_a_long_enough_array_too_short() -> None:
    assert _issues(_mutated(("product", "allowed_option_roots"), ["xsp", "spxw"])) == [
        (ErrorCode.SCHEMA_VIOLATION, "/product/allowed_option_roots/0"),
        (ErrorCode.SCHEMA_VIOLATION, "/product/allowed_option_roots/1"),
    ]


def test_null_exit_rules_are_accepted_and_empty_ones_are_not() -> None:
    assert load_strategy(_mutated(("exits", "take_profit"), None)).spec.exits.take_profit is None
    assert _issues(_mutated(("exits", "take_profit"), {})) == [
        (ErrorCode.SCHEMA_VIOLATION, "/exits/take_profit/basis"),
        (ErrorCode.SCHEMA_VIOLATION, "/exits/take_profit/fraction"),
    ]
