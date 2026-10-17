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
from options_backtest.engine.fees import AssumedFlatFeeSchedule, lifecycle_fees
from options_backtest.engine.settlement import book_cash_settlement
from options_backtest.errors import LedgerInvariantError, SimulationInvariantError
from options_backtest.models.ledger import FeeEvent, LedgerEntry, LedgerState
from options_backtest.models.market import require_id, require_type
from options_backtest.reference.calendars import next_session


def revised_contracts(view: AsOfView, held_versions: Mapping[str, str]) -> tuple[str, ...]:
    """Return held contracts re-versioned since they were traded (checked at OPEN).

    Args:
        view: As-of view at the session's OPEN.
        held_versions: Held contract id to the ``version_id`` its lots were traded under.

    Returns:
        Contract ids, sorted, whose ``view.contract`` is None or has another ``version_id``;
        each invalidates the run with UNSUPPORTED_CORPORATE_ACTION.

    Raises:
        TypeError: If ``view`` is not an ``AsOfView`` or an id is not a str.
        ValueError: If an id is empty.

    """
    require_type(view, AsOfView, "revised_contracts view")
    revised = []
    for contract_id, version_id in held_versions.items():
        require_id(version_id, "revised_contracts version_id")
        current = view.contract(contract_id)
        if current is None or current.version_id != version_id:
            revised.append(contract_id)
    return tuple(sorted(revised))


def expiring_contracts(state: LedgerState, session: TradingSession) -> tuple[str, ...]:
    """Return held contracts expiring in the session: ``open_ns <= expires_at_ns <= cutoff_ns``.

    Args:
        state: Ledger state.
        session: Session.

    Returns:
        Contract ids, sorted.

    Raises:
        TypeError: If an argument has the wrong type.

    """
    require_type(state, LedgerState, "expiring_contracts state")
    require_type(session, TradingSession, "expiring_contracts session")
    return tuple(
        sorted(
            contract_id
            for contract_id in state.lots  # held instruments; deposited stock has no terms
            if contract_id in state.contracts
            and session.open_ns <= state.contracts[contract_id].expires_at_ns <= session.cutoff_ns
        )
    )


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

    def __post_init__(self) -> None:
        """Require an entry exactly when a settlement observation was used, and citing it."""
        if self.entry is None:
            cited = self.observation_id is None
        else:
            cited = self.entry.input_refs == (self.observation_id,)
        if not cited:
            raise ValueError(
                f"ExpirySettlement of {self.generation_id}: an entry must exist exactly when an "
                f"observation settles it and cite it, got observation {self.observation_id!r}"
            )


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
        TypeError: If an argument has the wrong type.
        ValueError: If the view is not at the session's cutoff or ``settles_on`` is not the
            next table session.
        SimulationInvariantError: If expiring contracts span generations, series or
            deliverable assets, have no version at the cutoff, or the ledger rejects the
            engine's own settlement.

    """
    require_type(state, LedgerState, "settle_expiring state")
    require_type(schedule, AssumedFlatFeeSchedule, "settle_expiring schedule")
    require_id(event_id, "settle_expiring event_id")
    _require_cut(view, session, settles_on)
    contract_ids = expiring_contracts(state, session)
    if not contract_ids:
        return None
    generation_id = _generation(state, contract_ids)
    series = _series(view, contract_ids)
    asset_id = _deliverable_asset(state, contract_ids)
    observation = view.settlement(series, session.session_date)
    if observation is None:
        return ExpirySettlement(generation_id, contract_ids, series, None, None)
    contracts = sum(abs(lot.quantity) for cid in contract_ids for lot in state.lots[cid])
    try:
        entry = book_cash_settlement(
            state,
            event_id=event_id,
            at_ns=session.cutoff_ns,
            contract_ids=contract_ids,
            settlement={asset_id: observation.value},
            fees=lifecycle_fees(schedule, FeeEvent.CASH_SETTLEMENT, contracts),
            settles_on=settles_on,
            settlement_ref=observation.observation_id,
        )
    except LedgerInvariantError as e:
        raise SimulationInvariantError(
            f"the ledger rejected the engine's own settlement of {list(contract_ids)}: {e}"
        ) from e
    return ExpirySettlement(generation_id, contract_ids, series, observation.observation_id, entry)


def _require_cut(view: AsOfView, session: TradingSession, settles_on: date) -> None:
    """Require the view at the session's cutoff and T+1 on the next table session."""
    require_type(view, AsOfView, "settle_expiring view")
    require_type(session, TradingSession, "settle_expiring session")
    if view.at_ns != session.cutoff_ns or view.session != session:
        raise ValueError(
            f"settle_expiring runs at the cutoff {session.cutoff_ns} of {session.session_date}, "
            f"not at {view.at_ns}"
        )
    following = next_session(view.dataset.sessions, session.session_date)
    if following is None or settles_on != following.session_date:
        raise ValueError(
            f"settles_on {settles_on!r} must be the table session after {session.session_date}"
        )


def _generation(state: LedgerState, contract_ids: tuple[str, ...]) -> str:
    """Return the one generation whose lots hold every expiring contract."""
    generations = {lot.campaign_id for cid in contract_ids for lot in state.lots[cid]}
    generation = next(iter(generations)) if len(generations) == 1 else None
    if generation is None:
        raise SimulationInvariantError(
            f"expiring contracts {list(contract_ids)} must be held by one generation, "
            f"got {sorted(generations, key=str)}"
        )
    return generation


def _series(view: AsOfView, contract_ids: tuple[str, ...]) -> str:
    """Return the one settlement series of the contracts' versions at the cutoff."""
    versions = [view.contract(cid) for cid in contract_ids]
    unversioned = [cid for cid, v in zip(contract_ids, versions, strict=True) if v is None]
    if unversioned:
        raise SimulationInvariantError(f"held contracts {unversioned} have no version at CUT")
    series = {version.settlement_series for version in versions if version is not None}
    if len(series) != 1:
        raise SimulationInvariantError(
            f"expiring contracts {list(contract_ids)} span settlement series {sorted(series)}"
        )
    return series.pop()


def _deliverable_asset(state: LedgerState, contract_ids: tuple[str, ...]) -> str:
    """Return the one asset every contract's deliverable consists of (its settlement key)."""
    assets = {
        component.asset_id
        for cid in contract_ids
        for component in state.contracts[cid].deliverable.components
    }
    if len(assets) != 1:
        raise SimulationInvariantError(
            f"expiring contracts {list(contract_ids)} settle on one deliverable asset, "
            f"got {sorted(assets)}"
        )
    return assets.pop()
