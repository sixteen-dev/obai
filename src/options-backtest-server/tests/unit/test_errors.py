"""Typed error vocabulary: §15.2 shape, codes and fail-loud guards."""

import dataclasses

import pytest

from options_backtest.errors import (
    ErrorCode,
    Issue,
    LedgerInvariantError,
    MissingMarkError,
    SimulationInvariantError,
    SpecRejected,
    UnsupportedLifecycle,
    sorted_issues,
)

SECTION_15_2_CODES = {
    "UNSUPPORTED_STRUCTURE",
    "UNSUPPORTED_PRODUCT",
    "UNSUPPORTED_ACCOUNT_STATE",
    "INVALID_SELECTOR",
    "DATA_ENTITLEMENT_MISSING",
    "DATA_COVERAGE_GAP",
    "QUOTE_SEMANTICS_UNKNOWN",
    "MISSING_SETTLEMENT",
    "UNSUPPORTED_CORPORATE_ACTION",
    "PRICING_INPUT_UNAVAILABLE",
    "INSUFFICIENT_CAPITAL",
    "HOLDOUT_LOCKED",
    "SEARCH_BUDGET_EXCEEDED",
    "IDEMPOTENCY_CONFLICT",
    "RESOURCE_LIMIT",
    "ARTIFACT_COMMIT_FAILED",
}
ADR_0001_ADDED_CODES = {
    "MALFORMED_JSON",
    "DUPLICATE_KEY",
    "NONFINITE_NUMBER",
    "UNKNOWN_FIELD",
    "SCHEMA_VIOLATION",
    "UNSUPPORTED_SCHEMA_VERSION",
    "INVALID_STRATEGY_RULE",
}
ADR_0002_ADDED_CODES = {"SELECTION_BUDGET_EXCEEDED", "MISSING_VALUATION"}


def _issue(pointer: str = "/legs/0/side") -> Issue:
    return Issue(ErrorCode.SCHEMA_VIOLATION, "side must be buy or sell", pointer)


def test_error_codes_are_section_15_2_plus_adr_additions_with_value_equal_to_name() -> None:
    assert {code.value for code in ErrorCode} == (
        SECTION_15_2_CODES | ADR_0001_ADDED_CODES | ADR_0002_ADDED_CODES
    )
    assert all(code.name == code.value for code in ErrorCode)


def test_issue_has_the_section_15_2_shape_and_defaults() -> None:
    issue = _issue()

    assert [field.name for field in dataclasses.fields(Issue)] == [
        "code",
        "message",
        "json_pointer",
        "retriable",
        "missing_capability",
        "affected_interval",
        "remediation",
    ]
    assert issue.retriable is False
    assert issue.missing_capability is None
    assert issue.affected_interval is None
    assert issue.remediation is None


def test_issue_is_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        _issue().message = "changed"  # type: ignore[misc]


def test_issue_accepts_the_whole_document_pointer() -> None:
    assert _issue(pointer="").json_pointer == ""


def test_issue_rejects_a_pointer_that_is_not_rfc_6901() -> None:
    with pytest.raises(ValueError, match="json_pointer"):
        _issue(pointer="legs/0")


def test_issue_rejects_an_empty_message() -> None:
    with pytest.raises(ValueError, match="message"):
        Issue(ErrorCode.SCHEMA_VIOLATION, "", "/name")


def test_issue_rejects_a_code_that_is_not_an_error_code() -> None:
    with pytest.raises(TypeError, match="ErrorCode"):
        Issue("SCHEMA_VIOLATION", "bad", "/name")  # type: ignore[arg-type]


def test_spec_rejected_carries_every_issue_in_order() -> None:
    first = _issue(pointer="/legs/0/side")
    second = Issue(ErrorCode.UNKNOWN_FIELD, "unknown field", "/extra")

    error = SpecRejected([first, second])

    assert error.issues == (first, second)
    assert "SCHEMA_VIOLATION at /legs/0/side" in str(error)
    assert "UNKNOWN_FIELD at /extra" in str(error)


def test_spec_rejected_requires_at_least_one_issue() -> None:
    with pytest.raises(ValueError, match="at least one issue"):
        SpecRejected([])


def test_sorted_issues_orders_by_pointer_then_code() -> None:
    late = Issue(ErrorCode.UNKNOWN_FIELD, "unknown field", "/z")
    code_b = Issue(ErrorCode.UNKNOWN_FIELD, "unknown field", "/a")
    code_a = Issue(ErrorCode.SCHEMA_VIOLATION, "bad value", "/a")

    assert sorted_issues([late, code_b, code_a]) == (code_a, code_b, late)


def test_sorted_issues_rejects_a_non_issue() -> None:
    with pytest.raises(TypeError, match="Issue"):
        sorted_issues([("/a", ErrorCode.SCHEMA_VIOLATION)])  # type: ignore[list-item]


def test_unsupported_lifecycle_carries_its_code() -> None:
    error = UnsupportedLifecycle(ErrorCode.UNSUPPORTED_ACCOUNT_STATE, "short stock needs a borrow")

    assert error.code is ErrorCode.UNSUPPORTED_ACCOUNT_STATE
    assert str(error) == "UNSUPPORTED_ACCOUNT_STATE: short stock needs a borrow"


def test_unsupported_lifecycle_rejects_a_plain_string_code() -> None:
    with pytest.raises(TypeError, match="ErrorCode"):
        UnsupportedLifecycle("UNSUPPORTED_ACCOUNT_STATE", "x")  # type: ignore[arg-type]


def test_unsupported_lifecycle_rejects_an_empty_message() -> None:
    with pytest.raises(ValueError, match="message"):
        UnsupportedLifecycle(ErrorCode.UNSUPPORTED_ACCOUNT_STATE, "")


def test_missing_mark_error_names_every_instrument() -> None:
    error = MissingMarkError(["SPXW-P100", "SPXW-P95"])

    assert error.instrument_ids == ("SPXW-P100", "SPXW-P95")
    assert "SPXW-P100" in str(error)
    assert "SPXW-P95" in str(error)


def test_missing_mark_error_requires_an_instrument() -> None:
    with pytest.raises(ValueError, match="at least one instrument"):
        MissingMarkError([])


def test_missing_mark_error_rejects_a_bare_string() -> None:
    with pytest.raises(TypeError, match="sequence of instrument ids"):
        MissingMarkError("SPXW-P100")


def test_ledger_invariant_error_is_an_exception() -> None:
    with pytest.raises(LedgerInvariantError, match="unbalanced"):
        raise LedgerInvariantError("unbalanced entry")


def test_simulation_invariant_error_is_a_job_failure_not_an_invalid_run_or_request_error() -> None:
    # ADR 0002 §1, §10: a broken engine invariant fails the job; it never becomes an invalid run.
    error = SimulationInvariantError("funding headroom -1.00 after commit")

    assert str(error) == "funding headroom -1.00 after commit"
    assert not isinstance(error, UnsupportedLifecycle | MissingMarkError | SpecRejected)
