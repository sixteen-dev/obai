"""Schema parity: our syntactic verdict equals JSON Schema 2020-12 on a mutated corpus.

The oracle is ``jsonschema``'s Draft 2020-12 validator fed the same bytes, parsed exactly
(``Decimal``), with the specification's integer rule: any number with a zero fractional part.
The one whitelisted disagreement is ADR 0001 §11's: an integral number written with a
fraction or exponent (``1.0``) is an integer to JSON Schema and is rejected here.

Excluded from the corpus, because they are not schema questions: duplicate keys, non-finite
numbers and lone surrogates (strict JSON, C01), and strings ending in a newline, which
Python's ``re.search`` lets ``$`` match although ECMA-262 regular expressions do not.
"""

import copy
import json
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.protocols import Validator  # type: ignore[import-untyped]
from jsonschema.validators import extend  # type: ignore[import-untyped]

from options_backtest.errors import ErrorCode, Issue, SpecRejected
from options_backtest.ingest import parse_spec
from options_backtest.strict_json import json_pointer

CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"

type Document = dict[str, object]
type JsonPath = tuple[str | int, ...]

PALETTE: tuple[object, ...] = (
    None, True, False,
    0, 1, -1, 2, 3, 5, 6, 7, 10, 11, 12, 13, 20, 21, 252, 253, 365, 366, 756, 757, 10**30,
    1.0, 3.0, 7.0, -0.0,
    0.5, 0.25, 0.2500000001, 1e-9, -0.3, 0.3, -1.0, 0.999999999, -0.999999999, 100.5,
    "", "x", "0", "0.00", "1", "5.00", "-5.00", "-0", "05", "1e3", "5.", "0.0000000001",
    "1234567890123456", "123456789012345.123456789",
    "XSP", "SPX", "BRK.B", "xsp", "buy", "delta", "a" * 41, "A" * 21, "p" * 161, "É",
    [], {}, ["XSP"], ["XSP", "XSP"], [1],
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class Case:
    """One mutated document and what is needed to judge a disagreement on it."""

    label: str
    raw: bytes
    pointer: str
    integral_fraction: bool


def _is_integer(_checker: object, instance: object) -> bool:
    if isinstance(instance, bool):
        return False
    if isinstance(instance, int):
        return True
    return isinstance(instance, Decimal) and instance == instance.to_integral_value()


@pytest.fixture(scope="module")
def oracle() -> Validator:
    schema = json.loads(
        (CONTRACTS / "strategy.schema.json").read_text("utf-8"), parse_float=Decimal
    )
    Draft202012Validator.check_schema(schema)
    type_checker = Draft202012Validator.TYPE_CHECKER.redefine("integer", _is_integer)
    return extend(Draft202012Validator, type_checker=type_checker)(schema)


def _example() -> Document:
    document = json.loads((CONTRACTS / "example-strategy.json").read_text("utf-8"))
    assert isinstance(document, dict)
    return document


def _leg(leg_id: str, side: str, option_type: str, expiry: Document, strike: Document) -> Document:
    return {
        "leg_id": leg_id,
        "side": side,
        "option_type": option_type,
        "ratio": 1,
        "expiry_selection": expiry,
        "strike_selection": strike,
    }


def _condor_base() -> Document:
    """Iron condor over the branches the example leaves out: moneyness, weekly, risk budget."""
    target: Document = {"method": "target_dte", "target_dte": 45, "min_dte": 30, "max_dte": 60}
    same: Document = {"method": "same_as", "anchor_leg_id": "short_put"}
    document = _example()
    document["structure"] = "iron_condor"
    document["legs"] = [
        _leg("short_put", "sell", "put", target, {
            "method": "moneyness", "target_strike_to_spot": 0.95, "tolerance": 0,
        }),
        _leg("long_put", "buy", "put", same, {
            "method": "strike_offset", "anchor_leg_id": "short_put", "offset_price_units": "-5",
        }),
        _leg("short_call", "sell", "call", same, {
            "method": "delta", "target_delta": 0.2, "tolerance": 0.25,
        }),
        _leg("long_call", "buy", "call", same, {
            "method": "strike_offset", "anchor_leg_id": "short_call", "offset_price_units": "5",
        }),
    ]  # fmt: skip
    document["entry"] = {
        "schedule": {
            "frequency": "weekly",
            "weekday": 3,
            "holiday_policy": "next_session_same_week",
        },
        "all_conditions": [
            {"feature": "options.atm30_iv_rank_252s", "operator": "gte", "value": 30}
        ],
    }
    document["exits"] = {
        "take_profit": None,
        "stop_loss": {"basis": "initial_credit", "multiple": 2},
        "exit_dte": 0,
        "max_holding_sessions": 252,
    }
    document["roll"] = {
        "mode": "sequential",
        "trigger_dte": 30,
        "max_rolls": 2,
        "max_campaign_sessions": 120,
    }
    document["sizing"] = {"method": "risk_budget", "max_contracts": 4}
    document["end_policy"] = "mark_open_positions"
    return document


def _single_long_base() -> Document:
    """Single long call with a daily schedule, several conditions and no stop loss."""
    target: Document = {"method": "target_dte", "target_dte": 7, "min_dte": 7, "max_dte": 365}
    document = _example()
    document["structure"] = "single_long"
    document["legs"] = [
        _leg(
            "call",
            "buy",
            "call",
            target,
            {"method": "delta", "target_delta": 0.5, "tolerance": 0.1},
        )
    ]
    document["entry"] = {
        "schedule": {"frequency": "daily"},
        "all_conditions": [
            {"feature": "underlying.return_20s", "operator": "lt", "value": -0.05},
            {"feature": "underlying.close_to_sma_50s", "operator": "gt", "value": 0},
        ],
    }
    document["exits"] = {
        "take_profit": {"basis": "initial_debit", "fraction": 10},
        "stop_loss": None,
        "exit_dte": 3,
        "max_holding_sessions": 1,
    }
    return document


def _bases() -> dict[str, Document]:
    return {"example": _example(), "condor": _condor_base(), "single_long": _single_long_base()}


def _paths(node: object, prefix: JsonPath = ()) -> Iterator[tuple[JsonPath, object]]:
    yield prefix, node
    if isinstance(node, dict):
        for key, child in node.items():
            yield from _paths(child, (*prefix, key))
    if isinstance(node, list):
        for index, child in enumerate(node):
            yield from _paths(child, (*prefix, index))


def _replaced(base: Document, path: JsonPath, value: object) -> object:
    if not path:
        return value
    document = copy.deepcopy(base)
    parent: object = document
    for part in path[:-1]:
        parent = parent[part]  # type: ignore[index]
    parent[path[-1]] = value  # type: ignore[index]
    return document


def _variants(node: object) -> Iterator[tuple[str, object, bool]]:
    """Yield (description, replacement, is an integral fraction) for one node."""
    for value in PALETTE:
        yield repr(value), value, isinstance(value, float) and value.is_integer()
    if isinstance(node, dict):
        yield "unknown member", {**node, "zz_unknown": 1}, False
        for key in node:
            yield f"without {key}", {k: v for k, v in node.items() if k != key}, False
    if isinstance(node, list) and node:
        yield "last item repeated", [*node, node[-1]], False


def _corpus() -> list[Case]:
    return [
        Case(
            label=f"{name}:{json_pointer(path)} <- {description}",
            raw=json.dumps(_replaced(base, path, value)).encode(),
            pointer=json_pointer(path),
            integral_fraction=integral_fraction,
        )
        for name, base in _bases().items()
        for path, node in _paths(base)
        for description, value, integral_fraction in _variants(node)
    ]


def _our_issues(raw: bytes) -> tuple[Issue, ...]:
    try:
        parse_spec(raw)
    except SpecRejected as rejected:
        return rejected.issues
    return ()


def _is_documented_delta(case: Case, ours: tuple[Issue, ...], theirs: bool) -> bool:
    """Whether the disagreement is exactly the whitelisted integral-fraction one."""
    integer_codes = {ErrorCode.SCHEMA_VIOLATION, ErrorCode.UNSUPPORTED_SCHEMA_VERSION}
    return (
        case.integral_fraction
        and theirs
        and [(issue.code in integer_codes, issue.json_pointer) for issue in ours]
        == [(True, case.pointer)]
    )


def test_every_base_document_is_valid_for_both_validators(oracle: Validator) -> None:
    for name, base in _bases().items():
        raw = json.dumps(base).encode()
        assert _our_issues(raw) == (), name
        assert oracle.is_valid(json.loads(raw, parse_float=Decimal)), name


def test_verdicts_agree_on_the_mutated_corpus_except_integral_fractions(oracle: Validator) -> None:
    corpus = _corpus()
    verdicts = [
        (case, _our_issues(case.raw), oracle.is_valid(json.loads(case.raw, parse_float=Decimal)))
        for case in corpus
    ]
    disagreements = [
        (case, ours, theirs) for case, ours, theirs in verdicts if (not ours) != theirs
    ]
    documented = [
        case.label
        for case, ours, theirs in disagreements
        if _is_documented_delta(case, ours, theirs)
    ]
    undocumented = [
        f"{case.label}: ours={[i.code.value for i in ours] or 'valid'} jsonschema={theirs}"
        for case, ours, theirs in disagreements
        if not _is_documented_delta(case, ours, theirs)
    ]

    assert undocumented == []
    assert len(corpus) > 10_000
    assert sum(theirs for _, _, theirs in verdicts) > 500  # the corpus exercises acceptance too
    assert any("/schema_version <- 1.0" in label for label in documented)
    assert any("/ratio <- 1.0" in label for label in documented)
