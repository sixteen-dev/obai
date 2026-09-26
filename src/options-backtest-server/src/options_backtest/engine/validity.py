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

from options_backtest.data.records import CoveragePartition
from options_backtest.errors import Issue
from options_backtest.models.market import ContractTerms
from options_backtest.models.result import CalculationStatus
from options_backtest.money import Usd


@dataclass(frozen=True, slots=True)
class RunStatus:
    """A run's calculation status and the issues that set it.

    Attributes:
        status: Current status.
        reasons: The one issue that moved it off VALID; () while VALID.

    """

    status: CalculationStatus
    reasons: tuple[Issue, ...]

    @classmethod
    def valid(cls) -> RunStatus:
        """Return the initial status: VALID, no reasons."""
        raise NotImplementedError

    def invalidate(self, issue: Issue) -> RunStatus:
        """Return INVALID with ``issue`` as the reason.

        Args:
            issue: Why (MISSING_VALUATION, MISSING_SETTLEMENT, DATA_COVERAGE_GAP or
                UNSUPPORTED_CORPORATE_ACTION).

        Returns:
            The new status.

        Raises:
            ValueError: If the status is not VALID (it never changes twice).

        """
        raise NotImplementedError

    def mark_incomplete(self, issue: Issue) -> RunStatus:
        """Return INCOMPLETE with ``issue`` as the reason.

        Args:
            issue: Why the run could not finish its end policy.

        Returns:
            The new status.

        Raises:
            ValueError: If the status is not VALID.

        """
        raise NotImplementedError


class CoverageVerdict(StrEnum):
    """What a session's chain partition means for the run (ADR 0002 §10)."""

    COMPLETE = "complete"
    GAP = "gap"
    INVALID = "invalid"


def coverage_verdict(partition: CoveragePartition | None) -> CoverageVerdict:
    """Return the verdict of a session's ``"quotes"`` partition, read at DEC phase 2.

    COMPLETE proceeds; GAP makes a due entry or replacement a disclosed missed opportunity
    (held positions are decided as usual); UNKNOWN or an absent partition invalidates the run
    with DATA_COVERAGE_GAP.

    Args:
        partition: The partition, or None when absent.

    Returns:
        The verdict.

    """
    raise NotImplementedError


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

    """
    raise NotImplementedError


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

    """
    raise NotImplementedError
