"""Contract lifecycle at OPEN and CUT: re-versioning and PM cash settlement (ADR 0002 §7).

OPEN phase 1 transfers due cash with WP1's ``book_settle_due(through=session)`` directly; this
module adds what needs the as-of view. CUT phase 6 settles an expiring held package in one
``book_cash_settlement`` entry at the final official value (``R1Campaign.Settle``), T+1.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from options_backtest.data.asof import AsOfView
from options_backtest.data.records import TradingSession
from options_backtest.engine.fees import AssumedFlatFeeSchedule
from options_backtest.models.ledger import LedgerEntry, LedgerState


def revised_contracts(view: AsOfView, held_versions: Mapping[str, str]) -> tuple[str, ...]:
    """Return held contracts re-versioned since they were traded (checked at OPEN).

    Args:
        view: As-of view at the session's OPEN.
        held_versions: Held contract id to the ``version_id`` its lots were traded under.

    Returns:
        Contract ids, sorted, whose ``view.contract`` is None or has another ``version_id``;
        each invalidates the run with UNSUPPORTED_CORPORATE_ACTION.

    """
    raise NotImplementedError


def expiring_contracts(state: LedgerState, session: TradingSession) -> tuple[str, ...]:
    """Return held contracts expiring in the session: ``open_ns <= expires_at_ns <= cutoff_ns``.

    Args:
        state: Ledger state.
        session: Session.

    Returns:
        Contract ids, sorted.

    """
    raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ExpirySettlement:
    """The CUT outcome for one expiring held package.

    Attributes:
        generation_id: Campaign generation holding the package.
        contract_ids: Its contracts, sorted.
        series: Their settlement series.
        observation_id: Final settlement observation used; None when missing.
        entry: The ``book_cash_settlement`` entry to commit; None when missing (the run is then
            invalid with MISSING_SETTLEMENT).

    """

    generation_id: str
    contract_ids: tuple[str, ...]
    series: str
    observation_id: str | None
    entry: LedgerEntry | None


def settle_expiring(  # noqa: PLR0913 — explicit event coordinates, as WP1 posting functions
    state: LedgerState,
    view: AsOfView,
    schedule: AssumedFlatFeeSchedule,
    *,
    event_id: str,
    session: TradingSession,
    settles_on: date,
) -> ExpirySettlement | None:
    """Settle the held package expiring in ``session`` at CUT.

    The series is the contracts' ``ContractVersion.settlement_series`` (one per package); the
    value ``view.settlement(series, session_date)``; the entry is
    ``book_cash_settlement(contract_ids, {asset_id: value}, lifecycle_fees(CASH_SETTLEMENT,
    Σ|q|), settles_on, settlement_ref=observation_id)`` at ``session.cutoff_ns``, with
    ``asset_id`` the contracts' single deliverable component.

    Args:
        state: Ledger state after the session's fills.
        view: As-of view at the session's CUT.
        schedule: Fee schedule.
        event_id: Event id of the SETTLED event.
        session: Session.
        settles_on: The next table session.

    Returns:
        The outcome; None when nothing held expires in the session.

    Raises:
        SimulationInvariantError: If expiring contracts span generations or series.

    """
    raise NotImplementedError
