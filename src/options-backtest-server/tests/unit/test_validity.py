"""Run status, coverage verdicts, mark-range findings and the end status (ADR 0002 §7, §10).

``RunStatus`` refines ``R1Campaign.calc``: VALID moves at most once, to INVALID or INCOMPLETE,
and never back (``CalcMonotone``).
"""

from dataclasses import replace
from datetime import date

import pytest
from data_builders import MON, WED, coverage, option_terms
from engine_builders import LONG, SHORT, usd

from options_backtest.data.records import CoverageState
from options_backtest.engine.validity import (
    CoverageVerdict,
    RunStatus,
    coverage_verdict,
    end_status,
    package_mark_in_range,
)
from options_backtest.errors import ErrorCode, Issue
from options_backtest.models.market import OptionType
from options_backtest.models.result import CalculationStatus

FINAL = date(2024, 3, 6)
INVALIDATING = (
    ErrorCode.MISSING_VALUATION,
    ErrorCode.MISSING_SETTLEMENT,
    ErrorCode.DATA_COVERAGE_GAP,
    ErrorCode.UNSUPPORTED_CORPORATE_ACTION,
)


def issue(code: ErrorCode = ErrorCode.MISSING_VALUATION) -> Issue:
    return Issue(code, f"{code.value} on 2024-03-05", "", affected_interval="2024-03-05")


def incomplete_issue() -> Issue:
    return Issue(
        ErrorCode.UNSUPPORTED_ACCOUNT_STATE,
        "incomplete_liquidation: held",
        "",
        affected_interval="2024-03-06",
    )


# --- RunStatus -----------------------------------------------------------------------------


def test_a_run_starts_valid_without_reasons() -> None:
    assert RunStatus.valid() == RunStatus(CalculationStatus.VALID, ())


@pytest.mark.parametrize("code", INVALIDATING)
def test_valid_moves_to_invalid_with_its_one_reason(code: ErrorCode) -> None:
    status = RunStatus.valid().invalidate(issue(code))

    assert status == RunStatus(CalculationStatus.INVALID, (issue(code),))


def test_valid_moves_to_incomplete_with_its_one_reason() -> None:
    status = RunStatus.valid().mark_incomplete(incomplete_issue())

    assert status == RunStatus(CalculationStatus.INCOMPLETE, (incomplete_issue(),))


def test_the_status_never_changes_twice() -> None:
    invalid = RunStatus.valid().invalidate(issue())
    incomplete = RunStatus.valid().mark_incomplete(incomplete_issue())

    for moved in (invalid, incomplete):
        with pytest.raises(ValueError, match="valid"):
            moved.invalidate(issue(ErrorCode.MISSING_SETTLEMENT))
        with pytest.raises(ValueError, match="valid"):
            moved.mark_incomplete(incomplete_issue())


@pytest.mark.parametrize(
    "code", [ErrorCode.INSUFFICIENT_CAPITAL, ErrorCode.UNSUPPORTED_ACCOUNT_STATE]
)
def test_only_the_adr_10_conditions_invalidate(code: ErrorCode) -> None:
    with pytest.raises(ValueError, match="code"):
        RunStatus.valid().invalidate(issue(code))


@pytest.mark.parametrize(
    ("pointer", "interval"),
    [
        ("/account", "2024-03-05"),  # a spec pointer: not a run-level issue
        ("", None),  # undated
        ("", "2024-03"),  # not a session date
        ("", "20240305"),  # not the ISO spelling
    ],
)
def test_a_status_moves_only_on_a_run_level_issue_dated_by_its_session(
    pointer: str, interval: str | None
) -> None:
    # ADR 0002 §17 items 30 and 43: json_pointer "" and the session's ISO date.
    invalidating = Issue(
        ErrorCode.MISSING_VALUATION, "missing", pointer, affected_interval=interval
    )
    incomplete = replace(invalidating, code=ErrorCode.UNSUPPORTED_ACCOUNT_STATE)

    with pytest.raises(ValueError, match="run-level"):
        RunStatus.valid().invalidate(invalidating)
    with pytest.raises(ValueError, match="run-level"):
        RunStatus.valid().mark_incomplete(incomplete)


def test_incompleteness_is_the_unsupported_account_state_code() -> None:
    with pytest.raises(ValueError, match="code"):
        RunStatus.valid().mark_incomplete(issue(ErrorCode.MISSING_VALUATION))


def test_a_status_carries_exactly_the_reason_that_moved_it() -> None:
    with pytest.raises(ValueError, match="reasons"):
        RunStatus(CalculationStatus.VALID, (issue(),))
    with pytest.raises(ValueError, match="reasons"):
        RunStatus(CalculationStatus.INVALID, ())
    with pytest.raises(ValueError, match="reasons"):
        RunStatus(CalculationStatus.INCOMPLETE, (incomplete_issue(), incomplete_issue()))
    with pytest.raises(TypeError, match="Issue"):
        RunStatus.valid().invalidate("MISSING_VALUATION")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="status"):
        replace(RunStatus.valid(), status="valid")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="reasons"):
        RunStatus(CalculationStatus.INVALID, [issue()])  # type: ignore[arg-type]


# --- coverage_verdict ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "verdict"),
    [
        (CoverageState.COMPLETE, CoverageVerdict.COMPLETE),
        (CoverageState.GAP, CoverageVerdict.GAP),
        (CoverageState.UNKNOWN, CoverageVerdict.INVALID),
    ],
)
def test_coverage_verdict_of_a_quotes_partition(
    state: CoverageState, verdict: CoverageVerdict
) -> None:
    assert coverage_verdict(coverage(MON, state)) is verdict


def test_an_absent_partition_invalidates() -> None:
    assert coverage_verdict(None) is CoverageVerdict.INVALID


def test_coverage_verdict_reads_only_the_quotes_table() -> None:
    with pytest.raises(ValueError, match="quotes"):
        coverage_verdict(coverage(MON, table="settlements"))


# --- package_mark_in_range -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("mid", "inside"),
    [
        ("-90.00", True),
        ("-500.00", True),  # -W·m·n, inclusive
        ("0", True),  # inclusive
        ("-500.01", False),
        ("0.01", False),
    ],
)
def test_a_credit_vertical_marks_within_minus_width_and_zero(mid: str, inside: bool) -> None:
    holdings = ((SHORT, -1), (LONG, 1))

    assert package_mark_in_range(holdings, usd(mid)) is inside


@pytest.mark.parametrize(
    ("mid", "inside"),
    [("0", True), ("1000.00", True), ("1000.01", False), ("-0.01", False)],
)
def test_a_debit_vertical_of_two_packages_marks_within_zero_and_w_m_n(
    mid: str, inside: bool
) -> None:
    holdings = ((SHORT, 2), (LONG, -2))

    assert package_mark_in_range(holdings, usd(mid)) is inside


def test_an_unbounded_bound_is_open() -> None:
    long_call = ((option_terms(WED, OptionType.CALL, "5100"), 1),)

    assert package_mark_in_range(long_call, usd("99999999.00")) is True
    assert package_mark_in_range(long_call, usd("-0.01")) is False


def test_package_mark_in_range_needs_usd() -> None:
    with pytest.raises(TypeError, match="mid_value"):
        package_mark_in_range(((SHORT, -1), (LONG, 1)), "-90")  # type: ignore[arg-type]


# --- end_status ----------------------------------------------------------------------------


def test_a_liquidation_still_held_after_the_final_cut_is_incomplete() -> None:
    status = end_status(RunStatus.valid(), liquidate_at_final=True, held=True, final_session=FINAL)

    assert status.status is CalculationStatus.INCOMPLETE
    (reason,) = status.reasons
    assert reason.code is ErrorCode.UNSUPPORTED_ACCOUNT_STATE
    assert reason.message.startswith("incomplete_liquidation")
    assert reason.json_pointer == ""
    assert reason.affected_interval == "2024-03-06"
    assert reason.remediation == "extend the window or set end_policy to mark_open_positions"


@pytest.mark.parametrize(("liquidate", "held"), [(True, False), (False, True), (False, False)])
def test_a_flat_run_or_marked_open_positions_stay_valid(liquidate: bool, held: bool) -> None:
    status = end_status(
        RunStatus.valid(), liquidate_at_final=liquidate, held=held, final_session=FINAL
    )

    assert status == RunStatus.valid()


def test_end_status_leaves_an_invalid_run_invalid() -> None:
    invalid = RunStatus.valid().invalidate(issue())

    assert end_status(invalid, liquidate_at_final=True, held=True, final_session=FINAL) == invalid


def test_end_status_rejects_wrong_types() -> None:
    valid = RunStatus.valid()
    with pytest.raises(TypeError, match="bool"):
        end_status(valid, liquidate_at_final=1, held=True, final_session=FINAL)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="final_session"):
        end_status(valid, liquidate_at_final=True, held=True, final_session="2024-03-06")  # type: ignore[arg-type]
