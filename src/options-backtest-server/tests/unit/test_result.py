"""``SimulationResult`` carries only what WP3 can defend (ADR 0002 §6, §7 α, §17 items 4, 29, 32).

The constructor refuses a headline unless the run is valid and flat, a final equity unless it
is valid, a valid run ending with positions under ``liquidate_at_final_session``
(``OpenAtEndIsNotValid``), and a SYNTHETIC_FIXTURE result whose first warning is not
SYNTHETIC_FIXTURE_NOT_HISTORICAL.
"""

from datetime import date
from typing import Any

import pytest
from selection_builders import MON, TUE, WED, flat_result, usd

from options_backtest.data.records import FidelityClass
from options_backtest.errors import ErrorCode, Issue
from options_backtest.models.result import (
    CalculationStatus,
    RunWarning,
    SimulationResult,
    WarningCode,
)

MISSING_VALUATION = Issue(
    ErrorCode.MISSING_VALUATION,
    "held leg SPXW:2024-03-15:P:4900 has no CLOSE mark",
    "",
    affected_interval="2024-03-05",
)
OPEN_VERTICAL = (("SPXW:2024-03-15:P:4895", 1), ("SPXW:2024-03-15:P:4900", -1))
NOT_VALID = [CalculationStatus.INVALID, CalculationStatus.INCOMPLETE]
DEFERRED = RunWarning(WarningCode.EXIT_DEFERRED, MON, "no valid decision quote", ("x",))


def _not_valid(status: CalculationStatus, **changes: Any) -> SimulationResult:
    return flat_result(
        **{
            "calculation_status": status,
            "invalid_reasons": (MISSING_VALUATION,),
            "headline_eligible": False,
            "final_equity_usd": None,
            **changes,
        }
    )


def test_a_valid_flat_run_carries_its_headline_and_final_equity() -> None:
    result = flat_result()

    assert result.headline_eligible
    assert result.final_equity_usd == usd("10006.00")


@pytest.mark.parametrize("status", NOT_VALID)
def test_a_run_that_is_not_valid_is_reported_without_headline_or_equity(
    status: CalculationStatus,
) -> None:
    result = _not_valid(status)

    assert not result.headline_eligible
    assert result.final_equity_usd is None


@pytest.mark.parametrize("status", NOT_VALID)
def test_a_headline_is_refused_unless_the_run_is_valid(status: CalculationStatus) -> None:
    with pytest.raises(ValueError, match="headline_eligible"):
        _not_valid(status, headline_eligible=True)


def test_a_headline_is_refused_when_the_run_ends_holding_positions() -> None:
    with pytest.raises(ValueError, match="headline_eligible"):
        flat_result(open_positions=OPEN_VERTICAL, end_policy="mark_open_positions")


def test_a_valid_flat_run_is_headline_eligible() -> None:
    with pytest.raises(ValueError, match="headline_eligible"):
        flat_result(headline_eligible=False)


def test_a_valid_run_with_open_positions_keeps_its_final_equity_without_a_headline() -> None:
    result = flat_result(
        open_positions=OPEN_VERTICAL, end_policy="mark_open_positions", headline_eligible=False
    )

    assert result.final_equity_usd == usd("10006.00")


def test_a_liquidating_run_that_ends_holding_positions_is_not_valid() -> None:
    with pytest.raises(ValueError, match="open_positions"):
        flat_result(open_positions=OPEN_VERTICAL, headline_eligible=False)


def test_an_incomplete_liquidation_keeps_its_open_positions() -> None:
    result = _not_valid(CalculationStatus.INCOMPLETE, open_positions=OPEN_VERTICAL)

    assert result.open_positions == OPEN_VERTICAL


@pytest.mark.parametrize("status", NOT_VALID)
def test_a_final_equity_is_refused_unless_the_run_is_valid(status: CalculationStatus) -> None:
    with pytest.raises(ValueError, match="final_equity_usd"):
        _not_valid(status, final_equity_usd=usd("9983.00"))


def test_a_valid_run_states_its_final_equity() -> None:
    with pytest.raises(ValueError, match="final_equity_usd"):
        flat_result(final_equity_usd=None)


def test_a_synthetic_fixture_result_opens_with_the_not_historical_warning() -> None:
    synthetic = flat_result().warnings[0]

    with pytest.raises(ValueError, match="SYNTHETIC_FIXTURE_NOT_HISTORICAL"):
        flat_result(warnings=(DEFERRED,))
    with pytest.raises(ValueError, match="SYNTHETIC_FIXTURE_NOT_HISTORICAL"):
        flat_result(warnings=())
    with pytest.raises(ValueError, match="SYNTHETIC_FIXTURE_NOT_HISTORICAL"):
        flat_result(warnings=(DEFERRED, synthetic))
    assert flat_result(warnings=(synthetic, DEFERRED)).warnings[1] == DEFERRED


def test_historical_fidelity_needs_no_synthetic_warning() -> None:
    result = flat_result(data_fidelity=FidelityClass.HISTORICAL_SNAPSHOT, warnings=())

    assert result.warnings == ()


def test_invalid_reasons_are_present_exactly_when_the_run_is_not_valid() -> None:
    with pytest.raises(ValueError, match="invalid_reasons"):
        flat_result(invalid_reasons=(MISSING_VALUATION,))
    with pytest.raises(ValueError, match="invalid_reasons"):
        _not_valid(CalculationStatus.INVALID, invalid_reasons=())


def test_an_invalid_run_may_stop_before_the_end_of_its_window() -> None:
    result = _not_valid(CalculationStatus.INVALID, window_simulated=(MON, TUE))

    assert result.window_simulated == (MON, TUE)


@pytest.mark.parametrize(
    ("status", "simulated"),
    [
        (CalculationStatus.VALID, (MON, TUE)),  # a valid run simulates its whole window
        (CalculationStatus.INCOMPLETE, (MON, TUE)),  # incomplete is decided after the last CUT
        (CalculationStatus.INVALID, (TUE, WED)),  # every run starts at its first session
        (CalculationStatus.INVALID, (MON, date(2024, 3, 7))),  # and never runs past its end
    ],
)
def test_the_simulated_window_lies_within_the_requested_one(
    status: CalculationStatus, simulated: tuple[date, date]
) -> None:
    changes: dict[str, Any] = {"calculation_status": status, "window_simulated": simulated}
    if status is not CalculationStatus.VALID:
        changes |= {"invalid_reasons": (MISSING_VALUATION,), "headline_eligible": False}
        changes |= {"final_equity_usd": None}

    with pytest.raises(ValueError, match="window_simulated"):
        flat_result(**changes)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("execution_basis", "assumed_improvement"),
        ("calibration_status", "calibrated"),
        ("cost_basis", "historical_schedule"),
        ("assignment_basis", "scenario"),
    ],
)
def test_the_wp3_bases_are_fixed(field: str, value: str) -> None:
    with pytest.raises(ValueError, match=field):
        flat_result(**{field: value})


def test_status_fidelity_and_headline_must_have_their_types() -> None:
    with pytest.raises(TypeError, match="calculation_status"):
        flat_result(calculation_status="valid")
    with pytest.raises(TypeError, match="data_fidelity"):
        flat_result(data_fidelity="synthetic_fixture")
    with pytest.raises(TypeError, match="headline_eligible"):
        flat_result(headline_eligible=1)


def test_open_positions_are_sorted_distinct_contracts_with_nonzero_quantities() -> None:
    with pytest.raises(ValueError, match="open_positions"):
        flat_result(
            open_positions=tuple(reversed(OPEN_VERTICAL)),
            end_policy="mark_open_positions",
            headline_eligible=False,
        )
    with pytest.raises(ValueError, match="open_positions"):
        flat_result(
            open_positions=(("SPXW:2024-03-15:P:4895", 0),),
            end_policy="mark_open_positions",
            headline_eligible=False,
        )


def test_a_run_warning_needs_a_code_and_a_message() -> None:
    with pytest.raises(TypeError, match="code"):
        RunWarning("EXIT_DEFERRED", MON, "deferred", ())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="message"):
        RunWarning(WarningCode.EXIT_DEFERRED, MON, "", ())
