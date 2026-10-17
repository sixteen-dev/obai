"""Strict JSON boundary: limits, encoding, duplicate keys, non-finite numbers (ADR 0001 §4 1-3)."""

from collections.abc import Mapping
from decimal import Context, Decimal, localcontext

import pytest

from options_backtest.errors import ErrorCode, Issue, SpecRejected
from options_backtest.strict_json import (
    MAX_DEPTH,
    MAX_DOCUMENT_BYTES,
    freeze,
    json_pointer,
    parse,
)


def _rejection(raw: bytes) -> tuple[Issue, ...]:
    with pytest.raises(SpecRejected) as caught:
        parse(raw)
    return caught.value.issues


def _codes_at(raw: bytes) -> list[tuple[ErrorCode, str]]:
    return [(issue.code, issue.json_pointer) for issue in _rejection(raw)]


def _nested_arrays(depth: int) -> bytes:
    return b"[" * depth + b"]" * depth


def test_arrays_become_tuples_and_objects_read_only_mappings() -> None:
    tree = parse(b'{"legs": [{"side": "buy"}, [1, 2]], "empty_object": {}, "empty_array": []}')

    assert isinstance(tree, Mapping)
    assert tree["legs"] == ({"side": "buy"}, (1, 2))
    assert isinstance(tree["legs"], tuple)
    assert isinstance(tree["empty_object"], Mapping)
    assert tree["empty_array"] == ()


def test_numbers_are_exact_and_integers_stay_int() -> None:
    tree = parse(b'{"delta": -0.30, "big": 1E+400, "count": 7, "flag": true, "none": null}')

    assert isinstance(tree, Mapping)
    assert tree["delta"] == Decimal("-0.30")
    assert str(tree["delta"]) == "-0.30"
    assert tree["big"] == Decimal("1E+400")
    assert type(tree["count"]) is int
    assert tree["flag"] is True
    assert tree["none"] is None


def test_negative_zero_becomes_positive_zero() -> None:
    tree = parse(b'{"a": -0.0, "b": -0}')

    assert isinstance(tree, Mapping)
    zero = tree["a"]
    assert isinstance(zero, Decimal)
    assert zero == 0
    assert not zero.is_signed()
    assert tree["b"] == 0


def test_duplicate_key_is_reported_at_its_nested_pointer() -> None:
    raw = b'{"legs": [{"side": "buy"}, {"side": "buy", "side": "sell"}]}'

    assert _codes_at(raw) == [(ErrorCode.DUPLICATE_KEY, "/legs/1/side")]


def test_a_key_repeated_three_times_is_reported_once() -> None:
    assert _codes_at(b'{"a": 1, "a": 2, "a": 3}') == [(ErrorCode.DUPLICATE_KEY, "/a")]


def test_duplicate_key_pointer_escapes_tilde_and_slash() -> None:
    assert _codes_at(b'{"a/b~c": 1, "a/b~c": 2}') == [(ErrorCode.DUPLICATE_KEY, "/a~1b~0c")]


def test_every_non_finite_number_is_reported_sorted_by_pointer() -> None:
    raw = b'{"z": NaN, "b": [Infinity, -Infinity]}'

    assert _codes_at(raw) == [
        (ErrorCode.NONFINITE_NUMBER, "/b/0"),
        (ErrorCode.NONFINITE_NUMBER, "/b/1"),
        (ErrorCode.NONFINITE_NUMBER, "/z"),
    ]


def test_issues_of_different_kinds_are_all_reported_sorted_by_pointer_then_code() -> None:
    raw = b'{"b": NaN, "a": 1, "a": Infinity}'

    assert _codes_at(raw) == [
        (ErrorCode.DUPLICATE_KEY, "/a"),
        (ErrorCode.NONFINITE_NUMBER, "/a"),
        (ErrorCode.NONFINITE_NUMBER, "/b"),
    ]


def test_document_of_exactly_the_size_limit_is_parsed() -> None:
    prefix, suffix = b'{"name": "', b'"}'
    raw = prefix + b"x" * (MAX_DOCUMENT_BYTES - len(prefix) - len(suffix)) + suffix

    assert len(raw) == MAX_DOCUMENT_BYTES == 64 * 1024
    assert isinstance(parse(raw), Mapping)


def test_document_over_the_size_limit_is_a_resource_limit() -> None:
    raw = b" " * MAX_DOCUMENT_BYTES + b"{}"

    assert _codes_at(raw) == [(ErrorCode.RESOURCE_LIMIT, "")]


def test_byte_order_mark_is_malformed_json() -> None:
    assert _codes_at(b"\xef\xbb\xbf{}") == [(ErrorCode.MALFORMED_JSON, "")]


def test_invalid_utf8_is_malformed_json() -> None:
    assert _codes_at(b'{"name": "\xff"}') == [(ErrorCode.MALFORMED_JSON, "")]


def test_lone_surrogate_escapes_are_malformed_json_at_their_pointer() -> None:
    # A pointer must itself be valid Unicode: a lone surrogate in a key becomes U+FFFD there.
    raw = b'{"name": "a\\ud800", "\\udfff": 1}'

    assert _codes_at(raw) == [
        (ErrorCode.MALFORMED_JSON, "/name"),
        (ErrorCode.MALFORMED_JSON, "/\ufffd"),
    ]


def test_every_pointer_under_a_lone_surrogate_key_is_valid_utf8() -> None:
    raw = b'{"\\ud800": {"a": NaN, "b": 1, "b": 2}}'

    assert _codes_at(raw) == [
        (ErrorCode.MALFORMED_JSON, "/\ufffd"),
        (ErrorCode.NONFINITE_NUMBER, "/\ufffd/a"),
        (ErrorCode.DUPLICATE_KEY, "/\ufffd/b"),
    ]
    for issue in _rejection(raw):
        issue.json_pointer.encode("utf-8")


def test_surrogate_pair_escape_is_a_valid_character() -> None:
    assert parse(b'{"name": "\\ud83d\\ude00"}') == {"name": "\U0001f600"}


def test_syntax_error_is_malformed_json_with_line_and_column() -> None:
    (issue,) = _rejection(b'{\n  "a": 1,\n}')

    assert issue.code is ErrorCode.MALFORMED_JSON
    assert issue.json_pointer == ""
    assert "line 2 column 9" in issue.message  # the trailing comma


def test_empty_input_is_malformed_json() -> None:
    assert _codes_at(b"") == [(ErrorCode.MALFORMED_JSON, "")]


def test_nesting_at_the_depth_limit_is_parsed() -> None:
    tree = parse(_nested_arrays(MAX_DEPTH))

    assert MAX_DEPTH == 16
    assert isinstance(tree, tuple)


def test_nesting_beyond_the_depth_limit_is_a_resource_limit_at_the_deep_container() -> None:
    too_deep_pointer = "/0" * MAX_DEPTH

    assert _codes_at(_nested_arrays(MAX_DEPTH + 1)) == [
        (ErrorCode.RESOURCE_LIMIT, too_deep_pointer)
    ]


def test_object_nesting_counts_toward_the_depth_limit() -> None:
    raw = b'{"a": ' * (MAX_DEPTH + 1) + b"1" + b"}" * (MAX_DEPTH + 1)

    assert _codes_at(raw) == [(ErrorCode.RESOURCE_LIMIT, "/a" * MAX_DEPTH)]


def test_parser_recursion_overflow_is_a_resource_limit() -> None:
    raw = _nested_arrays(30_000)

    assert len(raw) <= MAX_DOCUMENT_BYTES
    assert _codes_at(raw) == [(ErrorCode.RESOURCE_LIMIT, "")]


def test_integer_beyond_the_parser_digit_limit_is_a_resource_limit() -> None:
    assert _codes_at(b'{"n": ' + b"9" * 5000 + b"}") == [(ErrorCode.RESOURCE_LIMIT, "")]


@pytest.mark.parametrize(
    "raw",
    [b"1e1000000000000000000", b"0e99999999999999999999999", b"[-1e-99999999999999999999999999]"],
)
def test_an_exponent_beyond_the_decimal_range_is_a_resource_limit(raw: bytes) -> None:
    assert _codes_at(raw) == [(ErrorCode.RESOURCE_LIMIT, "")]


def test_the_exponent_limit_does_not_depend_on_the_callers_decimal_context() -> None:
    # A context that does not trap InvalidOperation would turn the literal into NaN.
    with localcontext(Context(traps=[])):
        assert _codes_at(b"1e1000000000000000000") == [(ErrorCode.RESOURCE_LIMIT, "")]


def test_an_exponent_inside_the_decimal_range_parses_exactly() -> None:
    assert parse(b"1e999999999999999999") == Decimal("1e999999999999999999")


def test_parse_requires_bytes() -> None:
    with pytest.raises(TypeError, match="bytes"):
        parse('{"a": 1}')  # type: ignore[arg-type]


@pytest.mark.parametrize("foreign", [{"a": 1}, 1.5, Decimal("NaN"), object()])
def test_freeze_rejects_values_the_strict_parser_never_produces(foreign: object) -> None:
    with pytest.raises(TypeError, match="strict parser"):
        freeze(foreign)


def test_json_pointer_escapes_each_token_per_rfc_6901() -> None:
    assert json_pointer(["a/b", "m~n", 0, "~1"]) == "/a~1b/m~0n/0/~01"
    assert json_pointer([]) == ""


def test_json_pointer_rejects_a_bare_string() -> None:
    with pytest.raises(TypeError, match="not a str"):
        json_pointer("legs")
