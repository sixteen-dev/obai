"""Run validity, coverage verdicts and mark-range findings (ADR 0002 §7, §10; design §8.4).

``RunStatus`` refines ``R1Campaign.calc``: it starts VALID and moves at most once, to INVALID
(missing valuation, settlement or coverage, a re-versioned held contract) or to INCOMPLETE
(a final liquidation still held after the final CUT); ``CalcMonotone`` holds by construction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from options_backtest.data.records import CoveragePartition, CoverageState
from options_backtest.engine.funding import expiry_bounds
from options_backtest.errors import ErrorCode, Issue
from options_backtest.models.market import ContractTerms, require_type
from options_backtest.models.result import CalculationStatus
from options_backtest.money import ZERO_USD, Usd

_INVALIDATING: Final = frozenset(
    {
        ErrorCode.MISSING_VALUATION,
        ErrorCode.MISSING_SETTLEMENT,
        ErrorCode.DATA_COVERAGE_GAP,
        ErrorCode.UNSUPPORTED_CORPORATE_ACTION,
    }
)
"""The codes of ADR 0002 §10's invalid outcomes."""
_INCOMPLETE_REMEDIATION: Final = "extend the window or set end_policy to mark_open_positions"


@dataclass(frozen=True, slots=True)
class RunStatus:
    """A run's calculation status and the issues that set it.

    Attributes:
        status: Current status.
        reasons: The one issue that moved it off VALID; () while VALID.

    """

    status: CalculationStatus
    reasons: tuple[Issue, ...]

    def __post_init__(self) -> None:
        """Require no reason while VALID and exactly one otherwise."""
        require_type(self.status, CalculationStatus, "RunStatus.status")
        if not isinstance(self.reasons, tuple):
            raise TypeError(f"RunStatus.reasons must be a tuple, got {type(self.reasons).__name__}")
        for reason in self.reasons:
            require_type(reason, Issue, "RunStatus.reasons item")
        expected = 0 if self.status is CalculationStatus.VALID else 1
        if len(self.reasons) != expected:
            raise ValueError(
                f"RunStatus.reasons: a {self.status.value} run carries {expected} reason(s), "
                f"got {len(self.reasons)}"
            )

    @classmethod
    def valid(cls) -> RunStatus:
        """Return the initial status: VALID, no reasons."""
        return cls(CalculationStatus.VALID, ())

    def invalidate(self, issue: Issue) -> RunStatus:
        """Return INVALID with ``issue`` as the reason.

        Args:
            issue: Why (MISSING_VALUATION, MISSING_SETTLEMENT, DATA_COVERAGE_GAP or
                UNSUPPORTED_CORPORATE_ACTION).

        Returns:
            The new status.

        Raises:
            TypeError: If ``issue`` is not an ``Issue``.
            ValueError: If the status is not VALID (it never changes twice) or the issue's code
                is not one of the four.

        """
        self._require_move(issue)
        if issue.code not in _INVALIDATING:
            raise ValueError(f"issue code {issue.code} does not invalidate a run (ADR 0002 §10)")
        return RunStatus(CalculationStatus.INVALID, (issue,))

    def mark_incomplete(self, issue: Issue) -> RunStatus:
        """Return INCOMPLETE with ``issue`` as the reason.

        Args:
            issue: Why the run could not finish its end policy; code UNSUPPORTED_ACCOUNT_STATE
                (ADR 0002 §17 item 43).

        Returns:
            The new status.

        Raises:
            TypeError: If ``issue`` is not an ``Issue``.
            ValueError: If the status is not VALID or the issue's code is another.

        """
        self._require_move(issue)
        if issue.code is not ErrorCode.UNSUPPORTED_ACCOUNT_STATE:
            raise ValueError(
                f"an incomplete run's issue code is UNSUPPORTED_ACCOUNT_STATE: {issue.code}"
            )
        return RunStatus(CalculationStatus.INCOMPLETE, (issue,))

    def _require_move(self, issue: Issue) -> None:
        """Require a VALID status (it moves once, ``CalcMonotone``) and a run-level ``Issue``.

        Run-level (ADR 0002 §17 items 30, 43): ``json_pointer`` "" and ``affected_interval``
        the ISO date of the session that moved the status.
        """
        require_type(issue, Issue, "RunStatus issue")
        if self.status is not CalculationStatus.VALID:
            raise ValueError(f"the status moves only from valid, once; it is {self.status.value}")
        interval = issue.affected_interval
        if issue.json_pointer != "" or interval is None:
            raise ValueError(f"a run-level issue has json_pointer '' and a session date: {issue}")
        try:
            dated = date.fromisoformat(interval).isoformat() == interval
        except ValueError as e:
            raise ValueError(f"a run-level issue's affected_interval {interval!r}: {e}") from e
        if not dated:
            raise ValueError(f"a run-level issue's affected_interval {interval!r} is not ISO")


class CoverageVerdict(StrEnum):
    """What a session's chain partition means for the run (ADR 0002 §10)."""

    COMPLETE = "complete"
    GAP = "gap"
    INVALID = "invalid"


_VERDICTS: Final = MappingProxyType(
    {
        CoverageState.COMPLETE: CoverageVerdict.COMPLETE,
        CoverageState.GAP: CoverageVerdict.GAP,
        CoverageState.UNKNOWN: CoverageVerdict.INVALID,
    }
)


def coverage_verdict(partition: CoveragePartition | None) -> CoverageVerdict:
    """Return the verdict of a session's ``"quotes"`` partition, read at DEC phase 2.

    COMPLETE proceeds; GAP makes a due entry or replacement a disclosed missed opportunity
    (held positions are decided as usual); UNKNOWN or an absent partition invalidates the run
    with DATA_COVERAGE_GAP.

    Args:
        partition: The partition, or None when absent.

    Returns:
        The verdict.

    Raises:
        TypeError: If ``partition`` is not a ``CoveragePartition``.
        ValueError: If it is not the ``"quotes"`` table's.

    """
    if partition is None:
        return CoverageVerdict.INVALID
    require_type(partition, CoveragePartition, "coverage_verdict partition")
    if partition.table != "quotes":
        raise ValueError(f"coverage_verdict reads the quotes partition, got {partition.table!r}")
    return _VERDICTS[partition.status]


def package_mark_in_range(holdings: Sequence[tuple[ContractTerms, int]], mid_value: Usd) -> bool:
    """Return whether a held package's mid value lies in its no-arbitrage range (design §11.2).

    The range is ``[min_value, max_value]`` of ``expiry_bounds(holdings, 0, ZERO_USD)``, a None
    bound being unbounded: ``[0, W·m·n]`` for a debit vertical, ``[-W·m·n, 0]`` for a credit one.
    Outside it the CLOSE mark is a MARK_OUT_OF_RANGE finding, never clamped.

    Args:
        holdings: The package's contracts and signed quantities.
        mid_value: ``Σ premium_usd(mid_i, q_i)`` at the CLOSE quotes.

    Returns:
        Whether the value is within the range, bounds inclusive.

    Raises:
        TypeError: If ``mid_value`` is not ``Usd``.
        ValueError: As ``expiry_bounds`` for malformed holdings.
        UnsupportedLifecycle: As ``expiry_bounds`` for several expiries or deliverables.

    """
    require_type(mid_value, Usd, "package_mark_in_range mid_value")
    bounds = expiry_bounds(holdings, 0, ZERO_USD)
    above_min = bounds.min_value is None or bounds.min_value <= mid_value
    below_max = bounds.max_value is None or mid_value <= bounds.max_value
    return above_min and below_max


def end_status(
    status: RunStatus, *, liquidate_at_final: bool, held: bool, final_session: date
) -> RunStatus:
    """Return the status after the final window session's CUT (``R1Campaign.EndRun``).

    A VALID run that still holds a position under ``liquidate_at_final_session`` becomes
    INCOMPLETE with ``Issue(UNSUPPORTED_ACCOUNT_STATE, message beginning
    "incomplete_liquidation", json_pointer "", affected_interval=final_session.isoformat(),
    remediation="extend the window or set end_policy to mark_open_positions")`` (no new
    code, ADR 0002 §1 and §17 item 43); ``mark_open_positions`` stays VALID with the exposure
    listed; any other status is returned unchanged.

    Args:
        status: Status after the final CUT.
        liquidate_at_final: Whether the end policy is ``liquidate_at_final_session``.
        held: Whether any option lot is held.
        final_session: Final window session, for the issue's interval.

    Returns:
        The final status.

    Raises:
        TypeError: If an argument has the wrong type.

    """
    require_type(status, RunStatus, "end_status status")
    if type(liquidate_at_final) is not bool or type(held) is not bool:
        raise TypeError("end_status liquidate_at_final and held must be bool")
    if type(final_session) is not date:
        raise TypeError(f"end_status final_session must be a date, got {final_session!r}")
    if status.status is not CalculationStatus.VALID or not (liquidate_at_final and held):
        return status
    day = final_session.isoformat()
    issue = Issue(
        code=ErrorCode.UNSUPPORTED_ACCOUNT_STATE,
        message=(
            f"incomplete_liquidation: a position is still held after the final session {day} "
            "under liquidate_at_final_session"
        ),
        json_pointer="",
        affected_interval=day,
        remediation=_INCOMPLETE_REMEDIATION,
    )
    return status.mark_incomplete(issue)
