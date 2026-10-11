"""Strict JSON boundary for strategy documents (ADR 0001 §4 steps 1-3, design §9.1).

``parse`` turns raw bytes into an immutable tree or raises ``SpecRejected`` with every issue
found. Numbers are exact: JSON fractions and exponents become ``Decimal``, integers stay
``int``. Arrays become tuples. Objects are plain ``dict`` at runtime because strict Pydantic
validation rejects every other mapping type; they are typed as read-only ``Mapping``.
"""

from __future__ import annotations

import codecs
import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from typing import Final

from options_backtest.errors import ErrorCode, Issue, SpecRejected, sorted_issues

MAX_DOCUMENT_BYTES: Final = 64 * 1024
MAX_DEPTH: Final = 16  # containers; the document's outermost container is depth 1

type JsonValue = None | bool | int | Decimal | str | tuple[JsonValue, ...] | Mapping[str, JsonValue]


@dataclass(frozen=True, slots=True)
class _ParsedObject:
    """One JSON object as parsed: every key/value pair in order, repeated keys kept."""

    pairs: list[tuple[str, object]]


@dataclass(frozen=True, slots=True)
class _NonFinite:
    """A ``NaN``, ``Infinity`` or ``-Infinity`` literal, kept so the walk can point at it."""

    literal: str


def parse(raw: bytes) -> JsonValue:
    """Parse a strategy document into an immutable, exact tree.

    Args:
        raw: The document bytes: strict UTF-8, no byte order mark, at most 64 KiB.

    Returns:
        The tree: objects as read-only mappings, arrays as tuples, exact numbers.

    Raises:
        TypeError: If ``raw`` is not ``bytes``.
        SpecRejected: ``RESOURCE_LIMIT``, ``MALFORMED_JSON``, ``DUPLICATE_KEY`` or
            ``NONFINITE_NUMBER`` issues, all of them, sorted by (pointer, code).

    """
    if not isinstance(raw, bytes):
        raise TypeError(f"parse requires bytes, got {type(raw).__name__}")
    text = _decode(raw)
    try:
        parsed = json.loads(
            text,
            parse_float=_decimal,
            parse_constant=_NonFinite,
            object_pairs_hook=_ParsedObject,
        )
    except json.JSONDecodeError as e:
        message = f"{e.msg} at line {e.lineno} column {e.colno}"
        raise _rejected(ErrorCode.MALFORMED_JSON, message) from e
    except RecursionError as e:
        message = "JSON nesting exceeds the parser's recursion limit"
        raise _rejected(ErrorCode.RESOURCE_LIMIT, message) from e
    except ValueError as e:  # an integer literal beyond the interpreter's digit limit
        raise _rejected(ErrorCode.RESOURCE_LIMIT, f"number exceeds a parser limit: {e}") from e
    except InvalidOperation as e:  # an exponent beyond the decimal module's range
        message = "number exponent exceeds the decimal module's range"
        raise _rejected(ErrorCode.RESOURCE_LIMIT, message) from e
    return freeze(parsed)


def freeze(parsed: object) -> JsonValue:
    """Walk the strict parser's output into an immutable tree, collecting every issue.

    Args:
        parsed: What ``json.loads`` returned under ``parse``'s hooks.

    Returns:
        The immutable tree.

    Raises:
        TypeError: If ``parsed`` holds a value the strict parser never produces.
        SpecRejected: ``RESOURCE_LIMIT`` (depth > 16), ``DUPLICATE_KEY``,
            ``NONFINITE_NUMBER`` or ``MALFORMED_JSON`` (lone surrogate) issues, sorted.

    """
    issues: list[Issue] = []
    tree = _freeze(parsed, "", 1, issues)
    if issues:
        raise SpecRejected(sorted_issues(issues))
    return tree


def json_pointer(parts: Iterable[str | int]) -> str:
    """Build an RFC 6901 JSON pointer from path segments.

    Args:
        parts: Object member names and array indices, outermost first.

    Returns:
        The pointer; ``""`` for the whole document.

    Raises:
        TypeError: If ``parts`` is a bare string.

    """
    if isinstance(parts, str):
        raise TypeError("json_pointer needs a sequence of segments, not a str")
    return "".join(f"/{_escape(str(part))}" for part in parts)


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _decimal(text: str) -> Decimal:
    """Return a JSON fraction or exponent as an exact ``Decimal``, whatever the caller's context.

    The conversion itself is exact; the context only decides whether an exponent beyond the
    decimal module's range raises ``InvalidOperation`` or silently becomes ``NaN``, so it traps.
    """
    with localcontext() as context:
        context.traps[InvalidOperation] = True
        return Decimal(text)


def _rejected(code: ErrorCode, message: str) -> SpecRejected:
    return SpecRejected([Issue(code, message, "")])


def _decode(raw: bytes) -> str:
    if len(raw) > MAX_DOCUMENT_BYTES:
        message = f"document is {len(raw)} bytes; the limit is {MAX_DOCUMENT_BYTES}"
        raise _rejected(ErrorCode.RESOURCE_LIMIT, message)
    if raw.startswith(codecs.BOM_UTF8):
        raise _rejected(ErrorCode.MALFORMED_JSON, "a byte order mark is not allowed")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        message = f"invalid UTF-8 at byte {e.start}: {e.reason}"
        raise _rejected(ErrorCode.MALFORMED_JSON, message) from e


def _freeze(node: object, pointer: str, depth: int, issues: list[Issue]) -> JsonValue:
    if isinstance(node, _ParsedObject | list) and depth > MAX_DEPTH:
        message = f"nesting deeper than {MAX_DEPTH} containers"
        issues.append(Issue(ErrorCode.RESOURCE_LIMIT, message, pointer))
        return None
    if isinstance(node, _ParsedObject):
        return _freeze_object(node, pointer, depth, issues)
    if isinstance(node, list):
        return tuple(
            _freeze(item, f"{pointer}/{index}", depth + 1, issues)
            for index, item in enumerate(node)
        )
    return _freeze_scalar(node, pointer, issues)


def _freeze_object(
    node: _ParsedObject, pointer: str, depth: int, issues: list[Issue]
) -> Mapping[str, JsonValue]:
    frozen: dict[str, JsonValue] = {}
    for key, value in node.pairs:
        member = f"{pointer}/{_pointer_segment(key)}"
        _check_text(key, member, issues)
        frozen[key] = _freeze(value, member, depth + 1, issues)
    for key, count in Counter(key for key, _ in node.pairs).items():
        if count > 1:
            message = f"key {key!r} appears {count} times"
            member = f"{pointer}/{_pointer_segment(key)}"
            issues.append(Issue(ErrorCode.DUPLICATE_KEY, message, member))
    return frozen


def _pointer_segment(key: str) -> str:
    """Return ``key`` as an escaped pointer segment, each lone surrogate replaced by U+FFFD.

    A pointer must be valid Unicode so the rejection itself can be emitted as UTF-8; the lone
    surrogate is reported as MALFORMED_JSON at this segment.
    """
    return _escape("".join("\ufffd" if "\ud800" <= ch <= "\udfff" else ch for ch in key))


def _freeze_scalar(node: object, pointer: str, issues: list[Issue]) -> JsonValue:
    if isinstance(node, _NonFinite):
        message = f"{node.literal} is not a finite number"
        issues.append(Issue(ErrorCode.NONFINITE_NUMBER, message, pointer))
        return None
    if isinstance(node, str):
        _check_text(node, pointer, issues)
        return node
    if isinstance(node, Decimal) and node.is_finite():
        return node.copy_abs() if node.is_zero() else node
    if node is None or isinstance(node, bool | int):
        return node
    raise TypeError(
        f"freeze accepts only the strict parser's output, got {type(node).__name__} at {pointer!r}"
    )


def _check_text(text: str, pointer: str, issues: list[Issue]) -> None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as e:
        message = f"string has a lone surrogate escape at index {e.start}; not valid Unicode"
        issues.append(Issue(ErrorCode.MALFORMED_JSON, message, pointer))
