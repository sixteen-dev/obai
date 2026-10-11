"""Strategy ingestion: raw bytes to a ``ValidatedStrategy`` (ADR 0001 §4, design §9.1).

Each stage reports every issue it finds and stops the pipeline:

1-3. ``strict_json.parse``: size, encoding, syntax, duplicate keys, non-finite numbers, depth.
4.   Version gate: ``schema_version`` must be exactly the integer 1, else one issue, alone.
5-6. ``StrategySpec`` validation; each Pydantic error becomes an RFC 6901 pointer and a code.
7.   ``check_strategy``: the static semantic checks.

Issues are sorted by (pointer, code).
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from options_backtest.errors import ErrorCode, Issue, SpecRejected, sorted_issues
from options_backtest.models.strategy import ONE_OF_TAG_ERROR, ONE_OF_TAG_PREFIX, StrategySpec
from options_backtest.models.strategy_checks import ValidatedStrategy, check_strategy
from options_backtest.strict_json import JsonValue, json_pointer, parse

SUPPORTED_SCHEMA_VERSION: Final = 1

_LITERAL_CODES: Final = MappingProxyType(
    {
        "/structure": ErrorCode.UNSUPPORTED_STRUCTURE,
        "/product/family": ErrorCode.UNSUPPORTED_PRODUCT,
        "/account/policy": ErrorCode.UNSUPPORTED_ACCOUNT_STATE,
    }
)


def load_strategy(raw: bytes) -> ValidatedStrategy:
    """Ingest a strategy document: strict parse, schema validation and semantic checks.

    Args:
        raw: The JSON document bytes.

    Returns:
        The strategy with its leg resolution order and premium direction.

    Raises:
        TypeError: If ``raw`` is not ``bytes``.
        SpecRejected: With every issue of the first failing stage, sorted by (pointer, code).

    """
    spec = parse_spec(raw)
    result = check_strategy(spec)
    if result.issues:
        raise SpecRejected(result.issues)
    direction = result.premium_direction
    if direction is None:  # a raise, not an assert, so ``python -O`` keeps it
        raise AssertionError("check_strategy passed a spec without a premium direction")
    return ValidatedStrategy(spec, result.leg_order, direction)


def parse_spec(raw: bytes) -> StrategySpec:
    """Run the syntactic stages only (1-6): a schema-valid spec, not yet semantically checked.

    Args:
        raw: The JSON document bytes.

    Returns:
        The strategy, valid against ``strategy.schema.json`` (integers written ``1.0`` aside).

    Raises:
        TypeError: If ``raw`` is not ``bytes``.
        SpecRejected: With every issue of the first failing stage, sorted by (pointer, code).

    """
    if not isinstance(raw, bytes):
        raise TypeError(f"a strategy document must be bytes, got {type(raw).__name__}")
    tree = parse(raw)
    _check_schema_version(tree)
    try:
        return StrategySpec.model_validate(tree)
    except ValidationError as e:
        issues = [_issue(error) for error in e.errors()]
        raise SpecRejected(sorted_issues(issues)) from e


def _check_schema_version(tree: JsonValue) -> None:
    if not isinstance(tree, Mapping):
        return  # not an object: StrategySpec validation reports it at ""
    version = tree.get("schema_version")
    if type(version) is int and version == SUPPORTED_SCHEMA_VERSION:
        return
    found = f"got {version!r}" if "schema_version" in tree else "it is missing"
    message = f"schema_version must be the integer {SUPPORTED_SCHEMA_VERSION}; {found}"
    raise SpecRejected([Issue(ErrorCode.UNSUPPORTED_SCHEMA_VERSION, message, "/schema_version")])


def _issue(error: ErrorDetails) -> Issue:
    pointer = json_pointer(_json_path(error))
    return Issue(_code(error["type"], pointer), error["msg"], pointer)


def _json_path(error: ErrorDetails) -> list[str | int]:
    """Return the document path of an error: tags dropped, a tag error's key appended.

    A ``oneOf`` tag never ends a location; an unknown member named like one does, and is kept.
    """
    loc = error["loc"]
    path = [part for part in loc[:-1] if not _is_one_of_tag(part)] + list(loc[-1:])
    if error["type"] == ONE_OF_TAG_ERROR and isinstance(error["input"], Mapping):
        path.append(error["ctx"]["key"])
    return path


def _is_one_of_tag(part: str | int) -> bool:
    return isinstance(part, str) and part.startswith(ONE_OF_TAG_PREFIX)


def _code(error_type: str, pointer: str) -> ErrorCode:
    if error_type == "extra_forbidden":
        return ErrorCode.UNKNOWN_FIELD
    if error_type == "literal_error":
        return _LITERAL_CODES.get(pointer, ErrorCode.SCHEMA_VIOLATION)
    return ErrorCode.SCHEMA_VIOLATION
