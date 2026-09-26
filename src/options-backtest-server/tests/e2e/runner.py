"""E2E golden runner: scenario TOML to artifacts to an exact comparison (ADR 0002 §11).

A scenario is one TOML file under ``scenarios/``, read with ``tomllib.loads(text,
parse_float=Decimal)``:

- ``id``, ``proves``, ``derived_by``, ``checked_by``; ``window = {start, end}``;
- ``strategy = {file, patch}``: a file under ``strategies/`` and an optional RFC 7396 merge
  patch, serialized back to exact JSON for ``load_strategy``;
- ``[market]``: keys replacing those of ``defaults.toml``, which states every ``MarketSpec``
  field; each ``[[market.overrides]]`` table builds the override its snake_case ``kind`` names,
  its other keys being that class's fields (ADR 0002 §17 item 23);
- ``[expected]``: compared by ``compare`` under §17 item 44.

Every unknown key, missing key or mistyped value raises. Decimals are TOML strings (or TOML
numbers, exact through ``parse_float=Decimal``), dates ISO strings or TOML dates. The pipeline is
``load_strategy`` → ``generate`` → ``write_dataset``/``read_dataset`` → ``resolve`` → ``run``.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import CoverageState, UnderlyingField
from options_backtest.data.store import read_dataset, write_dataset
from options_backtest.engine.journal import replay
from options_backtest.engine.simulator import run
from options_backtest.ingest import load_strategy
from options_backtest.models.artifacts import (
    AccountPoint,
    ArtifactBundle,
    CampaignRecord,
    FillDetail,
    NonfillDetail,
    SettlementDetail,
    SimEvent,
    SimEventKind,
)
from options_backtest.models.ledger import LedgerEntry
from options_backtest.models.run import resolve
from options_backtest.money import Price
from options_backtest.reference.calendars import Slot
from options_backtest.synthetic.market import (
    ActivityPin,
    CoverageStatus,
    EarlyClose,
    LateAvailability,
    MarketSpec,
    Override,
    QuoteDrop,
    QuotePin,
    QuoteStale,
    RateDrop,
    SettlementDrop,
    SettlementPin,
    TermsRevision,
    UnderlyingDrop,
    generate,
)

E2E_DIR: Final = Path(__file__).resolve().parent
DEFAULTS_PATH: Final = E2E_DIR / "defaults.toml"
STRATEGIES_DIR: Final = E2E_DIR / "strategies"
SCENARIOS_DIR: Final = E2E_DIR / "scenarios"
MAX_TREE_DEPTH: Final = 32
"""Deepest JSON or TOML nesting the tree helpers follow before raising."""

type Json = None | bool | int | Decimal | str | list[Json] | dict[str, Json]
type Converter = Callable[[object, str], object]
type Row = Mapping[str, object]

_DECIMAL_TEXT: Final = re.compile(r"-?[0-9]+(\.[0-9]+)?")
_JSON_NUMBER: Final = re.compile(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?")
_STRATEGY_FILE: Final = re.compile(r"[a-z0-9_]+\.json")


class _Missing:
    """The value of a key an observed row lacks; equal to nothing expected."""

    def __repr__(self) -> str:
        return "<missing>"


_MISSING: Final = _Missing()


# --- scalar converters: (value, where) -> typed value, raising with the location -----------


def _text(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{where}: expected a string, got {value!r}")
    return value


def _integer(value: object, where: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{where}: expected an integer, got {value!r}")
    return value


def _boolean(value: object, where: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{where}: expected a boolean, got {value!r}")
    return value


def _decimal(value: object, where: str) -> Decimal:
    if isinstance(value, str):
        if _DECIMAL_TEXT.fullmatch(value) is None:
            raise ValueError(f"{where}: {value!r} is not a decimal string")
        return Decimal(value)
    if type(value) is Decimal and value.is_finite():
        return value
    if type(value) is int:
        return Decimal(value)
    raise TypeError(f"{where}: expected a decimal string, got {value!r}")


def _price(value: object, where: str) -> Price:
    return Price(_decimal(value, where))


def _date(value: object, where: str) -> date:
    if type(value) is date:
        return value
    try:
        return date.fromisoformat(_text(value, where))
    except ValueError as e:
        raise ValueError(f"{where}: {value!r} is not an ISO date") from e


def _instant(value: object, where: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as e:
            raise ValueError(f"{where}: {value!r} is not an ISO date-time") from e
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{where}: expected a date-time with a UTC offset, got {value!r}")
    return value


def _member[E: StrEnum](kind: type[E], value: object, where: str) -> E:
    text = _text(value, where)
    try:
        return kind(text)
    except ValueError as e:
        raise ValueError(f"{where}: {text!r} is not a {kind.__name__}") from e


def _slot(value: object, where: str) -> Slot:
    return _member(Slot, value, where)


def _underlying_field(value: object, where: str) -> UnderlyingField:
    return _member(UnderlyingField, value, where)


def _coverage_state(value: object, where: str) -> CoverageState:
    return _member(CoverageState, value, where)


# --- containers ----------------------------------------------------------------------------


def _table(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise TypeError(f"{where}: expected a table, got {value!r}")
    return value


def _array(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{where}: expected an array, got {value!r}")
    return value


def _items[T](value: object, where: str, convert: Callable[[object, str], T]) -> tuple[T, ...]:
    return tuple(convert(item, f"{where}[{i}]") for i, item in enumerate(_array(value, where)))


def _texts(value: object, where: str) -> tuple[str, ...]:
    return _items(value, where, _text)


def _integers(value: object, where: str) -> tuple[int, ...]:
    return _items(value, where, _integer)


def _dates(value: object, where: str) -> tuple[date, ...]:
    return _items(value, where, _date)


def _slots(value: object, where: str) -> tuple[Slot, ...]:
    return _items(value, where, _slot)


def _tuple_of(value: object, where: str, size: int) -> list[object]:
    items = _array(value, where)
    if len(items) != size:
        raise ValueError(f"{where}: expected {size} items, got {items!r}")
    return items


def _rate(value: object, where: str) -> tuple[int, Decimal]:
    tenor, bey = _tuple_of(value, where, 2)
    return _integer(tenor, f"{where}[0]"), _decimal(bey, f"{where}[1]")


def _leg(value: object, where: str) -> tuple[str, int, Decimal]:
    contract, contracts, price = _tuple_of(value, where, 3)
    return (
        _text(contract, f"{where}[0]"),
        _integer(contracts, f"{where}[1]"),
        _decimal(price, f"{where}[2]"),
    )


def _position(value: object, where: str) -> tuple[str, int]:
    contract, quantity = _tuple_of(value, where, 2)
    return _text(contract, f"{where}[0]"), _integer(quantity, f"{where}[1]")


def _legs(value: object, where: str) -> tuple[tuple[str, int, Decimal], ...]:
    return _items(value, where, _leg)


def _positions(value: object, where: str) -> tuple[tuple[str, int], ...]:
    return _items(value, where, _position)


def _check_keys(
    table: Mapping[str, object],
    required: Collection[str],
    where: str,
    optional: Collection[str] = (),
) -> None:
    unknown = sorted(set(table) - set(required) - set(optional))
    missing = sorted(set(required) - set(table))
    if unknown or missing:
        raise ValueError(f"{where}: unknown keys {unknown}, missing keys {missing}")


def _get[T](
    table: Mapping[str, object], key: str, convert: Callable[[object, str], T], where: str
) -> T:
    return convert(table[key], f"{where}.{key}")


# --- exact JSON trees and RFC 7396 --------------------------------------------------------------


def _json_tree(value: object, where: str, depth: int = 0) -> Json:
    """Check a parsed TOML or JSON value is a JSON tree: no floats, dates or non-finite numbers."""
    if depth > MAX_TREE_DEPTH:
        raise ValueError(f"{where}: nested deeper than {MAX_TREE_DEPTH}")
    if isinstance(value, dict):
        return {
            _text(key, where): _json_tree(item, f"{where}.{key}", depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_json_tree(item, f"{where}[{i}]", depth + 1) for i, item in enumerate(value)]
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        return value
    raise TypeError(f"{where}: {value!r} has no exact JSON form")


def _unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    members = dict(pairs)
    if len(members) != len(pairs):
        names = [name for name, _ in pairs]
        raise ValueError(f"duplicate keys in {names}")
    return members


def _reject_constant(name: str) -> object:
    raise ValueError(f"non-finite number {name}")


def read_json_tree(text: str, where: str) -> Json:
    """Parse JSON exactly: fractions as ``Decimal``; duplicate keys and NaN/Infinity rejected.

    Args:
        text: The JSON text.
        where: Name used in error messages.

    Returns:
        The tree.

    Raises:
        ValueError: If the text is not strict JSON.

    """
    try:
        parsed = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_unique_members,
        )
    except ValueError as e:  # json.JSONDecodeError is a ValueError
        raise ValueError(f"{where}: {e}") from e
    return _json_tree(parsed, where)


def merge_patch(target: Json, patch: Json) -> Json:
    """Apply an RFC 7396 JSON merge patch; ``target`` is left unchanged.

    Args:
        target: The document.
        patch: The patch; a null member removes that member.

    Returns:
        The patched document.

    Raises:
        ValueError: If the patch nests deeper than ``MAX_TREE_DEPTH``.

    """
    return _merge(target, patch, 0)


def _merge(target: Json, patch: Json, depth: int) -> Json:
    if depth > MAX_TREE_DEPTH:
        raise ValueError(f"merge patch nested deeper than {MAX_TREE_DEPTH}")
    if not isinstance(patch, dict):
        return patch
    merged: dict[str, Json] = dict(target) if isinstance(target, dict) else {}
    for name, value in patch.items():
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = _merge(merged.get(name), value, depth + 1)
    return merged


def json_bytes(tree: Json) -> bytes:
    """Serialize a tree as compact UTF-8 JSON; a ``Decimal`` is written as its exact number.

    Args:
        tree: The tree.

    Returns:
        The document bytes.

    Raises:
        TypeError: For a value with no JSON form (a float, a date).
        ValueError: For a non-finite decimal or nesting deeper than ``MAX_TREE_DEPTH``.

    """
    return _json_text(tree, 0).encode("utf-8")


def _json_text(value: Json, depth: int) -> str:
    if depth > MAX_TREE_DEPTH:
        raise ValueError(f"JSON tree nested deeper than {MAX_TREE_DEPTH}")
    if isinstance(value, dict):
        members = [
            f"{json.dumps(key)}:{_json_text(item, depth + 1)}" for key, item in value.items()
        ]
        return "{" + ",".join(members) + "}"
    if isinstance(value, list):
        return "[" + ",".join(_json_text(item, depth + 1) for item in value) + "]"
    if isinstance(value, Decimal):
        return _json_number(value)
    if value is None or isinstance(value, bool | int | str):
        return json.dumps(value)
    raise TypeError(f"{value!r} has no exact JSON form")


def _json_number(value: Decimal) -> str:
    text = str(value)
    if not value.is_finite() or _JSON_NUMBER.fullmatch(text) is None:
        raise ValueError(f"{value!r} is not a finite JSON number")
    return text


# --- strategy, market and overrides ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StrategyRef:
    """A strategy file and the merge patch a scenario applies to it.

    Attributes:
        file: File name under ``strategies/``.
        patch: RFC 7396 merge patch (a JSON object).

    """

    file: str
    patch: Json


def strategy_document(ref: StrategyRef, directory: Path = STRATEGIES_DIR) -> bytes:
    """Read a strategy file and return the patched document's bytes for ``load_strategy``.

    Args:
        ref: File and patch.
        directory: Directory holding the strategy files.

    Returns:
        The exact JSON document.

    Raises:
        ValueError: If the file is not strict JSON.

    """
    path = directory / ref.file
    tree = read_json_tree(path.read_text(encoding="utf-8"), ref.file)
    return json_bytes(merge_patch(tree, ref.patch))


_MARKET_KEYS: Final = frozenset(
    {
        "seed", "first_session", "last_session", "holidays", "early_closes", "index_start",
        "daily_drift", "daily_vol", "sigma", "rates", "roots", "weekly_dtes", "strike_step",
        "strikes_each_side", "tick", "half_spread_abs", "half_spread_rel", "bid_size",
        "ask_size", "premium_multiplier", "deliverable_units", "overrides",
    }
)  # fmt: skip


def build_market_spec(table: Mapping[str, object]) -> MarketSpec:
    """Build a ``MarketSpec`` from a table stating every field.

    Args:
        table: The merged ``[market]`` table.

    Returns:
        The market; its constraints are ``generate``'s to check.

    Raises:
        ValueError: If a field is missing or unknown, or a value is malformed.
        TypeError: If a value has the wrong type.

    """
    _check_keys(table, _MARKET_KEYS, "market")
    return MarketSpec(
        seed=_get(table, "seed", _integer, "market"),
        first_session=_get(table, "first_session", _date, "market"),
        last_session=_get(table, "last_session", _date, "market"),
        holidays=_get(table, "holidays", _dates, "market"),
        early_closes=_get(table, "early_closes", _dates, "market"),
        index_start=_get(table, "index_start", _decimal, "market"),
        daily_drift=_get(table, "daily_drift", _decimal, "market"),
        daily_vol=_get(table, "daily_vol", _decimal, "market"),
        sigma=_get(table, "sigma", _decimal, "market"),
        rates=_items(table["rates"], "market.rates", _rate),
        roots=_get(table, "roots", _texts, "market"),
        weekly_dtes=_get(table, "weekly_dtes", _integers, "market"),
        strike_step=_get(table, "strike_step", _decimal, "market"),
        strikes_each_side=_get(table, "strikes_each_side", _integer, "market"),
        tick=_get(table, "tick", _decimal, "market"),
        half_spread_abs=_get(table, "half_spread_abs", _decimal, "market"),
        half_spread_rel=_get(table, "half_spread_rel", _decimal, "market"),
        bid_size=_get(table, "bid_size", _integer, "market"),
        ask_size=_get(table, "ask_size", _integer, "market"),
        premium_multiplier=_get(table, "premium_multiplier", _decimal, "market"),
        deliverable_units=_get(table, "deliverable_units", _decimal, "market"),
        overrides=_items(table["overrides"], "market.overrides", _override_at),
    )


_OVERRIDES: Final[Mapping[str, tuple[Callable[..., Override], Mapping[str, Converter]]]] = (
    MappingProxyType(
        {
            "quote_pin": (QuotePin, {"contract": _text, "session": _date, "slots": _slots,
                "bid": _decimal, "ask": _decimal, "bid_size": _integer, "ask_size": _integer}),
            "quote_drop": (QuoteDrop, {"contract": _text, "session": _date, "slots": _slots}),
            "quote_stale": (QuoteStale, {"contract": _text, "session": _date, "slots": _slots,
                "seconds": _integer}),
            "late_availability": (LateAvailability, {"selector": _text,
                "available_at": _instant}),
            "underlying_drop": (UnderlyingDrop, {"underlying_id": _text,
                "field": _underlying_field, "session": _date, "slots": _slots}),
            "settlement_pin": (SettlementPin, {"series": _text, "session": _date,
                "value": _price}),
            "settlement_drop": (SettlementDrop, {"series": _text, "session": _date}),
            "rate_drop": (RateDrop, {"session": _date, "tenor_days": _integer}),
            "activity_pin": (ActivityPin, {"contract": _text, "session": _date, "slot": _slot,
                "cumulative_volume": _integer}),
            "terms_revision": (TermsRevision, {"contract": _text, "session": _date,
                "deliverable_units": _decimal}),
            "coverage_status": (CoverageStatus, {"table": _text, "session": _date,
                "status": _coverage_state, "note": _text}),
            "early_close": (EarlyClose, {"session": _date}),
        }
    )
)  # fmt: skip
"""Override kind (snake_case class name) to its class and each field's converter."""
_OPTIONAL_OVERRIDE_KEYS: Final = MappingProxyType({"rate_drop": frozenset({"tenor_days"})})
"""Fields TOML cannot set to None: omitting one means None (``RateDrop``: every tenor)."""


def build_override(table: Mapping[str, object], where: str = "override") -> Override:
    """Build the override named by ``table["kind"]`` from its other keys.

    Args:
        table: One ``[[market.overrides]]`` table.
        where: Location used in error messages.

    Returns:
        The override.

    Raises:
        ValueError: For an unknown kind, an unknown or missing key or a malformed value.
        TypeError: If a value has the wrong type.

    """
    kind = _text(table.get("kind"), f"{where}.kind")
    if kind not in _OVERRIDES:
        raise ValueError(f"{where}.kind: unknown override {kind!r}; known: {sorted(_OVERRIDES)}")
    factory, converters = _OVERRIDES[kind]
    optional = _OPTIONAL_OVERRIDE_KEYS.get(kind, frozenset())
    _check_keys(table, {"kind", *converters} - optional, where, optional)
    values = {
        key: convert(table[key], f"{where}.{key}") if key in table else None
        for key, convert in converters.items()
    }
    return factory(**values)


def _override_at(value: object, where: str) -> Override:
    return build_override(_table(value, where), where)


# --- scenario and expectations ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Expected:
    """A scenario's ``[expected]`` table, typed.

    Attributes:
        derivation: The hand derivation of every stated number.
        scalars: Stated result values: ``calculation_status`` and ``warning_codes`` always;
            ``headline_eligible``, ``final_equity_usd``, ``open_positions`` and
            ``retired_contracts`` when stated.
        rows: ``fills``, ``nonfills`` and ``settlements`` always (exhaustive, () when not
            stated); ``campaigns`` only when stated (then exhaustive too).
        account: Account rows by session date; only these dates are compared.

    """

    derivation: str
    scalars: Mapping[str, object]
    rows: Mapping[str, tuple[Row, ...]]
    account: Mapping[date, Row]


@dataclass(frozen=True, slots=True)
class Scenario:
    """One golden scenario.

    Attributes:
        scenario_id: ``G01`` ...; the file name starts with it.
        proves: Claims or conformance ids it proves.
        derived_by: Author of ``expected``.
        checked_by: Independent re-deriver; "" until checked.
        start: First window session.
        end: Final window session.
        strategy: Strategy file and patch.
        market: The synthetic market.
        expected: What the run must produce.

    """

    scenario_id: str
    proves: tuple[str, ...]
    derived_by: str
    checked_by: str
    start: date
    end: date
    strategy: StrategyRef
    market: MarketSpec
    expected: Expected


_SCALAR_KEYS: Final[Mapping[str, Converter]] = MappingProxyType(
    {
        "calculation_status": _text,
        "warning_codes": _texts,
        "headline_eligible": _boolean,
        "final_equity_usd": _decimal,
        "open_positions": _positions,
        "retired_contracts": _texts,
    }
)
_ROW_KEYS: Final[Mapping[str, Mapping[str, Converter]]] = MappingProxyType(
    {
        "fills": {"session": _date, "slot": _text, "purpose": _text, "packages": _integer,
            "legs": _legs, "net_debit": _decimal, "fees": _decimal, "limit": _decimal,
            "campaign_id": _text},
        "nonfills": {"session": _date, "slot": _text, "purpose": _text, "reason": _text,
            "contract": _text, "net_debit": _decimal},
        "settlements": {"session": _date, "series": _text, "observation_id": _text,
            "value": _decimal, "contract_ids": _texts, "net_cash": _decimal, "fees": _decimal,
            "entry_input_refs": _texts},
        "campaigns": {"campaign_id": _text, "generations": _texts, "start_session": _date,
            "end_session": _date, "basis": _decimal, "realized_pnl": _decimal,
            "fees": _decimal, "rolls": _integer, "outcome": _text, "exit_trigger": _text},
    }
)  # fmt: skip
_EXHAUSTIVE_ROWS: Final = ("fills", "nonfills", "settlements")
_ACCOUNT_KEYS: Final[Mapping[str, Converter]] = MappingProxyType(
    dict.fromkeys(
        ("cash", "receivable", "payable", "encumbrance", "headroom", "mid_nlv", "natural_nlv"),
        _decimal,
    )
)
_REQUIRED_EXPECTED: Final = frozenset({"derivation", "calculation_status", "warning_codes"})
_TOP_KEYS: Final = frozenset(
    {"id", "proves", "derived_by", "checked_by", "window", "strategy", "market", "expected"}
)


def parse_defaults(text: str) -> Mapping[str, object]:
    """Parse ``defaults.toml``: one ``[market]`` table stating every ``MarketSpec`` field.

    Args:
        text: The TOML text.

    Returns:
        The default market table.

    Raises:
        ValueError: If a table or field is missing or unknown.

    """
    document = tomllib.loads(text, parse_float=Decimal)
    _check_keys(document, {"market"}, "defaults")
    market = _table(document["market"], "defaults.market")
    _check_keys(market, _MARKET_KEYS, "defaults.market")
    return MappingProxyType(dict(market))


def parse_scenario(text: str, *, defaults: Mapping[str, object]) -> Scenario:
    """Parse a scenario's TOML over the default market.

    Args:
        text: The scenario's TOML text.
        defaults: ``parse_defaults`` of ``defaults.toml``.

    Returns:
        The scenario.

    Raises:
        ValueError: For an unknown or missing key or a malformed value.
        TypeError: If a value has the wrong type.

    """
    document = tomllib.loads(text, parse_float=Decimal)
    _check_keys(document, _TOP_KEYS, "scenario")
    window = _table(document["window"], "window")
    _check_keys(window, {"start", "end"}, "window")
    market = _table(document["market"], "market")
    return Scenario(
        scenario_id=_text(document["id"], "id"),
        proves=_texts(document["proves"], "proves"),
        derived_by=_text(document["derived_by"], "derived_by"),
        checked_by=_text(document["checked_by"], "checked_by"),
        start=_date(window["start"], "window.start"),
        end=_date(window["end"], "window.end"),
        strategy=_strategy_ref(document["strategy"]),
        market=build_market_spec({**defaults, **market}),
        expected=_expected(document["expected"]),
    )


def load_scenario(path: Path) -> Scenario:
    """Read ``defaults.toml`` and a scenario file and parse them.

    Args:
        path: The scenario file; its name starts with the scenario id and ``_``.

    Returns:
        The scenario.

    Raises:
        ValueError: As ``parse_scenario``, or if the id does not start the file name.

    """
    defaults = parse_defaults(DEFAULTS_PATH.read_text(encoding="utf-8"))
    scenario = parse_scenario(path.read_text(encoding="utf-8"), defaults=defaults)
    if path.stem.split("_", 1)[0] != scenario.scenario_id:
        raise ValueError(f"{path.name}: id {scenario.scenario_id!r} does not start the file name")
    return scenario


def _strategy_ref(value: object) -> StrategyRef:
    table = _table(value, "strategy")
    _check_keys(table, {"file"}, "strategy", optional={"patch"})
    file = _text(table["file"], "strategy.file")
    if _STRATEGY_FILE.fullmatch(file) is None:
        raise ValueError(f"strategy.file: {file!r} is not a file name under strategies/")
    patch = _json_tree(table.get("patch", {}), "strategy.patch")
    if not isinstance(patch, dict):
        raise TypeError(f"strategy.patch: expected a table, got {patch!r}")
    return StrategyRef(file=file, patch=patch)


def _expected(value: object) -> Expected:
    table = _table(value, "expected")
    optional = {*_SCALAR_KEYS, *_ROW_KEYS, "account"} - _REQUIRED_EXPECTED
    _check_keys(table, _REQUIRED_EXPECTED, "expected", optional)
    return Expected(
        derivation=_text(table["derivation"], "expected.derivation"),
        scalars={
            key: convert(table[key], f"expected.{key}")
            for key, convert in _SCALAR_KEYS.items()
            if key in table
        },
        rows={
            section: _items(table.get(section, []), f"expected.{section}", _row_parser(section))
            for section in _ROW_KEYS
            if section in _EXHAUSTIVE_ROWS or section in table
        },
        account={
            _date(day, f"expected.account.{day}"): _row(row, _ACCOUNT_KEYS, f"account.{day}")
            for day, row in _table(table.get("account", {}), "expected.account").items()
        },
    )


def _row(value: object, converters: Mapping[str, Converter], where: str) -> Row:
    table = _table(value, where)
    _check_keys(table, (), where, optional=converters)
    return {key: converters[key](item, f"{where}.{key}") for key, item in table.items()}


def _row_parser(section: str) -> Callable[[object, str], Row]:
    converters = _ROW_KEYS[section]
    return lambda value, where: _row(value, converters, where)


# --- running and observing -------------------------------------------------------------------


def stored_dataset(scenario: Scenario, directory: Path) -> FrozenDataset:
    """Generate the scenario's market, write it to ``directory`` and read it back.

    Args:
        scenario: The scenario.
        directory: An existing, empty directory for the dataset files.

    Returns:
        The dataset as read from disk.

    """
    write_dataset(generate(scenario.market), directory)
    return read_dataset(directory)


def run_scenario(scenario: Scenario, directory: Path) -> ArtifactBundle:
    """Run a scenario end to end, through the dataset's on-disk form.

    Args:
        scenario: The scenario.
        directory: An existing, empty directory for the dataset files.

    Returns:
        The run's artifacts.

    """
    strategy = load_strategy(strategy_document(scenario.strategy))
    dataset = stored_dataset(scenario, directory)
    resolved = resolve(
        strategy,
        start_date=scenario.start,
        end_date=scenario.end,
        manifest_id=dataset.manifest.manifest_id,
    )
    return run(resolved, dataset)


@dataclass(frozen=True, slots=True)
class Observed:
    """A run's artifacts in the typed shape of ``Expected``.

    Attributes:
        scalars: Every ``Expected.scalars`` key.
        rows: ``fills``, ``nonfills``, ``settlements`` and ``campaigns``, in artifact order.
        account: Account rows by session date.

    """

    scalars: Mapping[str, object]
    rows: Mapping[str, tuple[Row, ...]]
    account: Mapping[date, Row]


def observe(bundle: ArtifactBundle) -> Observed:
    """Read the compared values out of a run's artifacts.

    Args:
        bundle: The artifacts.

    Returns:
        The observed values; ``retired_contracts`` comes from replaying the journal.

    Raises:
        TypeError: If an event carries the wrong detail for its kind.
        ValueError: If an account date repeats or a settlement has no journal entry.
        LedgerInvariantError: If the journal does not replay.

    """
    result = bundle.result
    final_equity = result.final_equity_usd
    entries = {entry.event_id: entry for entry in bundle.journal}
    scalars = {
        "calculation_status": result.calculation_status.value,
        "warning_codes": tuple(warning.code.value for warning in result.warnings),
        "headline_eligible": result.headline_eligible,
        "final_equity_usd": None if final_equity is None else final_equity.amount,
        "open_positions": result.open_positions,
        "retired_contracts": tuple(sorted(replay(bundle.journal).retired)),
    }
    rows = {
        "fills": tuple(_fill_row(e) for e in _events(bundle, SimEventKind.FILLED)),
        "nonfills": tuple(_nonfill_row(e) for e in _events(bundle, SimEventKind.NOT_FILLED)),
        "settlements": tuple(
            _settlement_row(e, entries) for e in _events(bundle, SimEventKind.SETTLED)
        ),
        "campaigns": tuple(_campaign_row(record) for record in bundle.campaigns),
    }
    return Observed(scalars=scalars, rows=rows, account=_account_rows(bundle.account_curve))


def _events(bundle: ArtifactBundle, kind: SimEventKind) -> tuple[SimEvent, ...]:
    return tuple(event for event in bundle.events if event.kind is kind)


def _detail[D](event: SimEvent, kind: type[D]) -> D:
    detail = event.detail
    if not isinstance(detail, kind):
        raise TypeError(f"{event.event_id}: {event.kind} carries {type(detail).__name__}")
    return detail


def _fill_row(event: SimEvent) -> Row:
    detail = _detail(event, FillDetail)
    limit = detail.limit_usd
    return {
        "session": event.session_date,
        "slot": event.slot.value,
        "purpose": detail.purpose.value,
        "packages": detail.packages,
        "legs": tuple((leg.contract_id, leg.contracts, leg.price.value) for leg in detail.legs),
        "net_debit": detail.net_debit.amount,
        "fees": detail.fees.amount,
        "limit": None if limit is None else limit.amount,
        "campaign_id": event.campaign_id,
    }


def _nonfill_row(event: SimEvent) -> Row:
    detail = _detail(event, NonfillDetail)
    net_debit = detail.net_debit
    return {
        "session": event.session_date,
        "slot": event.slot.value,
        "purpose": detail.purpose.value,
        "reason": detail.reason.value,
        "contract": detail.contract_id,
        "net_debit": None if net_debit is None else net_debit.amount,
    }


def _settlement_row(event: SimEvent, entries: Mapping[str, LedgerEntry]) -> Row:
    detail = _detail(event, SettlementDetail)
    entry = entries.get(event.event_id)
    if entry is None:
        raise ValueError(f"{event.event_id}: SETTLED event without a journal entry")
    return {
        "session": event.session_date,
        "series": detail.series,
        "observation_id": detail.observation_id,
        "value": detail.value.value,
        "contract_ids": detail.contract_ids,
        "net_cash": detail.net_cash.amount,
        "fees": detail.fees.amount,
        "entry_input_refs": entry.input_refs,
    }


def _campaign_row(record: CampaignRecord) -> Row:
    trigger = record.exit_trigger
    return {
        "campaign_id": record.campaign_id,
        "generations": record.generations,
        "start_session": record.start_session,
        "end_session": record.end_session,
        "basis": record.basis.amount,
        "realized_pnl": record.realized_pnl.amount,
        "fees": record.fees.amount,
        "rolls": record.rolls,
        "outcome": record.outcome.value,
        "exit_trigger": None if trigger is None else trigger.value,
    }


def _account_rows(curve: Sequence[AccountPoint]) -> dict[date, Row]:
    rows: dict[date, Row] = {}
    for point in curve:
        if point.session_date in rows:
            raise ValueError(f"account curve repeats {point.session_date}")
        mid, natural = point.mid_nlv, point.natural_nlv
        rows[point.session_date] = {
            "cash": point.cash.amount,
            "receivable": point.receivable.amount,
            "payable": point.payable.amount,
            "encumbrance": point.encumbrance.amount,
            "headroom": point.headroom.amount,
            "mid_nlv": None if mid is None else mid.amount,
            "natural_nlv": None if natural is None else natural.amount,
        }
    return rows


# --- comparison (§17 item 44) ---------------------------------------------------------------


def compare(expected: Expected, observed: Observed) -> tuple[str, ...]:
    """Compare every stated value exactly; ``Decimal`` values compare by value.

    Scalars and account rows compare only what is stated; row lists compare their length and,
    row by row, only the keys each expected row states.

    Args:
        expected: The scenario's expectations.
        observed: The run's values.

    Returns:
        One message per mismatch; () when the run matches.

    """
    mismatches: list[str] = []
    for key, value in expected.scalars.items():
        mismatches += _mismatch(key, value, observed.scalars.get(key, _MISSING))
    for section, rows in expected.rows.items():
        mismatches += _compare_rows(section, rows, observed.rows.get(section, ()))
    for day, row in expected.account.items():
        mismatches += _compare_account(day, row, observed.account)
    return tuple(mismatches)


def _mismatch(where: str, expected: object, actual: object) -> list[str]:
    if expected == actual:
        return []
    return [f"{where}: expected {expected!r}, got {actual!r}"]


def _compare_rows(section: str, expected: Sequence[Row], actual: Sequence[Row]) -> list[str]:
    mismatches: list[str] = []
    if len(expected) != len(actual):
        mismatches.append(f"{section}: expected {len(expected)} rows, got {len(actual)}")
    for index, (want, got) in enumerate(zip(expected, actual, strict=False)):
        for key, value in want.items():
            mismatches += _mismatch(f"{section}[{index}].{key}", value, got.get(key, _MISSING))
    return mismatches


def _compare_account(day: date, row: Row, account: Mapping[date, Row]) -> list[str]:
    point = account.get(day)
    if point is None:
        return [f"account[{day}]: no account point"]
    return [
        message
        for key, value in row.items()
        for message in _mismatch(f"account[{day}].{key}", value, point.get(key, _MISSING))
    ]
