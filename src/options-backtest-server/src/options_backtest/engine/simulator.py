"""The R1 event loop: one fully funded account, one campaign at a time (ADR 0002 §7, design §10).

The only module that joins selection and execution. No formula lives here: each step calls the
module that owns it, and the loop orders the calls, books the entries and records the events.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Final

from options_backtest.data.asof import AsOfView
from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import FidelityClass, QuoteObservation, TradingSession
from options_backtest.engine.campaign import (
    CampaignState,
    FlatAction,
    HeldAction,
    HeldDecision,
    HeldPosition,
    campaign_record,
    close_filled,
    decide_flat,
    decide_held,
    ended,
    evaluate_triggers,
    generation_pnl,
    liquidation,
    open_filled,
    opening_campaign_id,
    settled,
)
from options_backtest.engine.clock import (
    QUOTE_MAX_AGE_NS,
    Phase,
    RunCalendar,
    event_id,
    run_calendar,
)
from options_backtest.engine.fills import CapacityBook, Fill, FillContext, Nonfill, try_fill
from options_backtest.engine.funding import campaign_encumbrances, funding_headroom
from options_backtest.engine.journal import Journal
from options_backtest.engine.lifecycle import revised_contracts, settle_expiring
from options_backtest.engine.orders import (
    ExitTrigger,
    NonfillReason,
    Order,
    OrderLeg,
    OrderPurpose,
    closing_legs,
    order_id,
    package_debit,
    usable_quote,
)
from options_backtest.engine.selector import SelectionContext, select
from options_backtest.engine.settlement import book_settle_due
from options_backtest.engine.trades import book_deposit
from options_backtest.engine.validity import (
    CoverageVerdict,
    RunStatus,
    coverage_verdict,
    end_status,
    package_mark_in_range,
)
from options_backtest.engine.valuation import MarkBasis, value_account
from options_backtest.errors import (
    ErrorCode,
    Issue,
    LedgerInvariantError,
    MissingMarkError,
    SimulationInvariantError,
)
from options_backtest.models.artifacts import (
    AccountPoint,
    ArtifactBundle,
    CampaignOutcome,
    CampaignRecord,
    CandidateDecision,
    DecisionDetail,
    DecisionReason,
    EventDetail,
    EventSummary,
    ExpirySkip,
    FillDetail,
    FilledLeg,
    NonfillDetail,
    OrderSnapshot,
    PositionRow,
    QualityCode,
    QualityFinding,
    SettlementDetail,
    SimEvent,
    SimEventKind,
)
from options_backtest.models.ledger import (
    CASH_KINDS,
    DATED_KINDS,
    AccountKind,
    FeeLine,
    LedgerEntry,
    LedgerState,
)
from options_backtest.models.market import Quote
from options_backtest.models.result import (
    CalculationStatus,
    ResultProvenance,
    RunWarning,
    SimulationResult,
    WarningCode,
)
from options_backtest.models.run import ResolvedRun
from options_backtest.money import ZERO_USD, Price, Usd
from options_backtest.reference.calendars import FILL_SLOTS, Slot, scheduled, slot_instant

_CLOSING_PURPOSES: Final = MappingProxyType(
    {
        HeldAction.FINAL: OrderPurpose.FINAL,
        HeldAction.EXIT: OrderPurpose.EXIT,
        HeldAction.ROLL_CLOSE: OrderPurpose.ROLL_CLOSE,
    }
)
"""The closing order each submitting ``HeldAction`` sends."""
_MARK_RATIO: Final = 1
"""A buy's ``usable_quote`` statuses (VALID, LOCKED, NO_BID) are exactly a CLOSE mark's."""


@dataclass(slots=True)
class _Run:
    """The mutable state of one ``run`` call, created there and never shared.

    Attributes:
        resolved: The resolved run.
        dataset: Its dataset.
        calendar: The window and the settle-only sessions.
        index: Position of each table session date in ``dataset.sessions``.
        last_session: Last window session entered.
        journal: The run's ledger.
        campaign: Campaign bookkeeping.
        status: Run status.
        capacity: Displayed size consumed by committed fills.
        order: The live order; None when none.
        refused: Orders already disclosed with INSUFFICIENT_CAPITAL.
        roll_closed_on: Session of the last ROLL_CLOSE fill.
        close_quotes: This session's CLOSE marks of the held contracts.
        seqs: Last ``seq`` used per (session, slot, phase).
        events: Events so far.
        warnings: Warnings so far.
        positions: Position rows so far.
        curve: Account points so far.
        campaigns: Campaign records so far.
        decisions: Candidate decisions so far.
        quality: Quality findings so far.

    """

    resolved: ResolvedRun
    dataset: FrozenDataset
    calendar: RunCalendar
    index: Mapping[date, int]
    last_session: date
    journal: Journal = field(default_factory=Journal)
    campaign: CampaignState = field(default_factory=CampaignState.initial)
    status: RunStatus = field(default_factory=RunStatus.valid)
    capacity: CapacityBook = field(default_factory=CapacityBook.empty)
    order: Order | None = None
    refused: set[str] = field(default_factory=set)
    roll_closed_on: date | None = None
    close_quotes: dict[str, Quote] = field(default_factory=dict)
    seqs: dict[tuple[date, Slot, Phase], int] = field(default_factory=dict)
    events: list[SimEvent] = field(default_factory=list)
    warnings: list[RunWarning] = field(default_factory=list)
    positions: list[PositionRow] = field(default_factory=list)
    curve: list[AccountPoint] = field(default_factory=list)
    campaigns: list[CampaignRecord] = field(default_factory=list)
    decisions: list[CandidateDecision] = field(default_factory=list)
    quality: list[QualityFinding] = field(default_factory=list)

    @property
    def invalid(self) -> bool:
        """Return whether an INVALIDATED event stopped the run."""
        return self.status.status is CalculationStatus.INVALID


def run(resolved: ResolvedRun, dataset: FrozenDataset) -> ArtifactBundle:
    """Simulate a resolved run on a frozen dataset.

    Setup: ``run_calendar(dataset.sessions, start_date, end_date)``; a ``Journal``; the DEPOSIT
    event ``{start}:OPEN:1:1`` books ``book_deposit(cash=account.initial_cash_usd)`` at the
    first session's ``open_ns``; a SYNTHETIC_FIXTURE dataset emits the
    SYNTHETIC_FIXTURE_NOT_HISTORICAL warning first, dated ``start_date``. Every window session
    ``d``, in order, with ``settles_on`` the next table session and every view
    ``AsOfView(dataset, slot instant)``; within one (session, slot, phase) ``seq`` follows the
    order events are listed here:

    - OPEN 1: ``book_settle_due(through=d)`` → SETTLE_DUE when not None; then a held contract
      in ``revised_contracts`` → INVALIDATED (UNSUPPORTED_CORPORATE_ACTION).
    - DEC 2: ``coverage_verdict(coverage("quotes", d))`` INVALID → INVALIDATED
      (DATA_COVERAGE_GAP). DEC 3, when held and every held leg has a ``usable_quote`` for its
      closing side (at most 120 s old; ``decide_held``'s ``quote_ok``): MARKED with the
      ``liquidation`` P&L at those natural marks and its components; when any leg lacks one,
      nothing (DEC 5 then defers, never MISSING_VALUATION).
      ``funding_headroom >= 0`` is asserted every DEC. DEC 5, held: ``evaluate_triggers`` then
      ``decide_held`` → ORDER_SUBMITTED (closing legs; limit = close ``D`` at DEC + allowance,
      None for FINAL, whose ``input_refs`` are the DEC observation ids that exist) or
      EXIT_DEFERRED (+ warning; detail purpose EXIT with its trigger when one holds, else
      ROLL_CLOSE) or nothing. Flat: ``decide_flat`` → for ENTRY/ROLL_OPEN, a GAP verdict gives
      ENTRY_SKIPPED/CAMPAIGN_ENDED (DATA_COVERAGE_GAP, + warning), else ``select`` (one
      ``CandidateDecision`` per call) → ORDER_SUBMITTED or ENTRY_SKIPPED/CAMPAIGN_ENDED with
      its reason (+ its warnings); END_CAMPAIGN → CAMPAIGN_ENDED; SKIP → ENTRY_SKIPPED; IDLE →
      nothing. DEC 5 emits at most one event.
    - F1, F2, F3 4, while an order is live: ``try_fill`` → FILLED (commit, consume capacity,
      campaign transition, assert ``funding_headroom >= 0`` else ``SimulationInvariantError``)
      or NOT_FILLED; an F3 nonfill adds ORDER_CANCELLED (+ EXIT_UNFILLED for a closing order),
      then, for a cancelled ROLL_OPEN, CAMPAIGN_ENDED (ROLL_OPEN_CANCELLED).
    - CLOSE 3, when unexpired legs are held (expiry date > d): each needs a quote at most 120 s
      old with status VALID, LOCKED or NO_BID, else INVALIDATED (MISSING_VALUATION); else
      MARKED, plus a MARK_OUT_OF_RANGE finding when ``package_mark_in_range`` is false.
    - CUT 6: ``settle_expiring`` → SETTLED (commit), or INVALIDATED (MISSING_SETTLEMENT) when
      the final value is not available by the cutoff. CUT 7: SNAPSHOT with the AccountPoint
      and PositionRows (marks: the CLOSE quotes).

    An INVALIDATED event stops the loop at once (no later event; the curve so far is kept).
    After the final window session's CUT, ``end_status`` applies (liquidation still held →
    INCOMPLETE, the issue of ADR 0002 §17 item 43). Unless invalid, each ``after`` session runs
    while any RECEIVABLE or PAYABLE is outstanding: OPEN 1 SETTLE_DUE and CUT 7 SNAPSHOT (NLVs
    None if a position is held); anything still due after them raises
    ``SimulationInvariantError``.

    A position reaches CUT 6 only through a close that failed: on its expiry session
    ``TimeExit`` holds (``dte = 0 <= exit_dte``), and on the final session under
    ``liquidate_at_final_session`` FINAL is submitted, so settlement always follows an
    EXIT_DEFERRED, EXIT_UNFILLED or INSUFFICIENT_CAPITAL disclosure (ADR 0002 §17 item 40).

    Args:
        resolved: The resolved run.
        dataset: The dataset its ``manifest_id`` names.

    Returns:
        The artifacts; ``result.final_equity_usd`` is the final window session's mid NLV when
        VALID.

    Raises:
        ValueError: If ``resolved.manifest_id`` is not the dataset's, or ``run_calendar``
            rejects the window.
        SimulationInvariantError: On an engine invariant breach (negative headroom after a
            commit, a ledger error on the engine's own entry or selection preview, a fill
            raising mid NLV, dues outstanding after the settle-only sessions).

    """
    _require_inputs(resolved, dataset)
    calendar = run_calendar(dataset.sessions, resolved.start_date, resolved.end_date)
    index = {session.session_date: i for i, session in enumerate(dataset.sessions)}
    sim = _Run(resolved, dataset, calendar, MappingProxyType(index), resolved.start_date)
    _deposit(sim)
    for session in calendar.window:
        _run_session(sim, session)
        if sim.invalid:
            break
    _finish_window(sim)
    _settle_after_window(sim)
    return _bundle(sim)


def _require_inputs(resolved: ResolvedRun, dataset: FrozenDataset) -> None:
    """Require a resolved run and the dataset its ``manifest_id`` names."""
    if not isinstance(resolved, ResolvedRun):
        raise TypeError(f"run needs a ResolvedRun, got {type(resolved).__name__}")
    if not isinstance(dataset, FrozenDataset):
        raise TypeError(f"run needs a FrozenDataset, got {type(dataset).__name__}")
    if resolved.manifest_id != dataset.manifest.manifest_id:
        raise ValueError(
            f"the run resolves manifest {resolved.manifest_id}, "
            f"the dataset is manifest {dataset.manifest.manifest_id}"
        )


# --- the session program ---------------------------------------------------------------------


def _deposit(sim: _Run) -> None:
    """Book the deposit at the first session's open and disclose synthetic data first."""
    session = sim.calendar.window[0]
    cash = sim.resolved.strategy.spec.account.initial_cash_usd
    entry_id = _peek(sim, session, Slot.OPEN, Phase.SETTLE_DUE)
    _commit(sim, book_deposit(event_id=entry_id, at_ns=session.open_ns, cash=cash))
    _check_invariants(sim)
    _emit(sim, session, Slot.OPEN, Phase.SETTLE_DUE, SimEventKind.DEPOSIT)
    manifest = sim.dataset.manifest
    if manifest.fidelity is FidelityClass.SYNTHETIC_FIXTURE:
        message = "the dataset is a synthetic fixture, not historical market data"
        _warn(
            sim,
            WarningCode.SYNTHETIC_FIXTURE_NOT_HISTORICAL,
            session,
            message,
            (manifest.manifest_id,),
        )


def _run_session(sim: _Run, session: TradingSession) -> None:
    """Run one window session's slots in order, stopping at an INVALIDATED event."""
    sim.last_session = session.session_date
    sim.close_quotes = {}
    steps: tuple[Callable[[_Run, TradingSession], None], ...] = (
        _open,
        _decide,
        _fill_slots,
        _close,
        _settle_at_cut,
        _snapshot_at_cut,
    )
    for step in steps:
        step(sim, session)
        if sim.invalid:
            return


def _open(sim: _Run, session: TradingSession) -> None:
    """OPEN 1: settle what is due, then invalidate on a re-versioned held contract."""
    _settle_due(sim, session)
    held = sim.campaign.held
    if held is None:
        return
    view = AsOfView(sim.dataset, session.open_ns)
    revised = revised_contracts(view, {leg.terms.contract_id: leg.version_id for leg in held.legs})
    if revised:
        message = f"held contracts {list(revised)} were re-versioned by {session.session_date}"
        code = ErrorCode.UNSUPPORTED_CORPORATE_ACTION
        _invalidate(sim, session, Slot.OPEN, Phase.SETTLE_DUE, code=code, message=message)


def _settle_due(sim: _Run, session: TradingSession) -> None:
    """OPEN 1: move every RECEIVABLE and PAYABLE dated on or before the session into CASH."""
    entry_id = _peek(sim, session, Slot.OPEN, Phase.SETTLE_DUE)
    entry = book_settle_due(
        sim.journal.state, event_id=entry_id, at_ns=session.open_ns, through=session.session_date
    )
    if entry is None:
        return
    _commit(sim, entry)
    _check_invariants(sim)
    _emit(sim, session, Slot.OPEN, Phase.SETTLE_DUE, SimEventKind.SETTLE_DUE)


def _decide(sim: _Run, session: TradingSession) -> None:
    """DEC: coverage verdict (phase 2), then the held or flat decision (phases 3 and 5)."""
    day = session.session_date
    view = AsOfView(sim.dataset, slot_instant(session, Slot.DEC))
    verdict = coverage_verdict(view.coverage("quotes", day))
    if verdict is CoverageVerdict.INVALID:
        message = f"the quotes coverage partition of {day} is unknown or absent"
        code = ErrorCode.DATA_COVERAGE_GAP
        _invalidate(sim, session, Slot.DEC, Phase.PUBLISH, code=code, message=message)
        return
    _check_funded(sim)
    held = sim.campaign.held
    if held is None:
        _decide_flat(sim, session, view, verdict)
        return
    _decide_held(sim, session, view, held)


# --- decisions while held --------------------------------------------------------------------


def _decide_held(sim: _Run, session: TradingSession, view: AsOfView, held: HeldPosition) -> None:
    """DEC 3 MARKED when every closing side has a usable quote; DEC 5 ``decide_held``."""
    day = session.session_date
    spec = sim.resolved.strategy.spec
    legs = closing_legs(held.legs)
    observed = _observe(view, legs)
    usable = _usable(observed, {leg.terms.contract_id: leg.ratio for leg in legs})
    quote_ok = len(usable) == len(legs)
    if quote_ok:
        refs = tuple(sorted(_observation_ids(observed)))
        _emit(
            sim,
            session,
            Slot.DEC,
            Phase.MARK,
            SimEventKind.MARKED,
            campaign_id=held.generation_id,
            input_refs=refs,
            detail=liquidation(sim.campaign, sim.resolved.fee_schedule, usable),
        )
    triggers = evaluate_triggers(
        sim.campaign,
        spec,
        sim.resolved.fee_schedule,
        session_date=day,
        held_sessions=_count(sim, held.fill_session, day),
        campaign_sessions=_count(sim, sim.campaign.start_session, day),
        close_quotes=usable if quote_ok else None,
    )
    decision = decide_held(
        triggers,
        final_session=day == sim.resolved.end_date,
        liquidate_at_final=spec.end_policy == "liquidate_at_final_session",
        quote_ok=quote_ok,
    )
    if decision.action is HeldAction.DEFER:
        _defer(sim, session, held, decision, legs=legs, usable=usable)
        return
    if decision.action is not HeldAction.HOLD:
        _submit_closing(sim, session, held, decision, legs=legs, observed=observed, usable=usable)


def _defer(  # noqa: PLR0913 — the decision's inputs, all explicit
    sim: _Run,
    session: TradingSession,
    held: HeldPosition,
    decision: HeldDecision,
    *,
    legs: tuple[OrderLeg, ...],
    usable: Mapping[str, Quote],
) -> None:
    """DEC 5 EXIT_DEFERRED: a due exit or roll close without a usable decision quote."""
    missing = tuple(leg.terms.contract_id for leg in legs if leg.terms.contract_id not in usable)
    message = (
        f"a due close of {held.generation_id} has no usable decision quote for {list(missing)}"
    )
    _warn(sim, WarningCode.EXIT_DEFERRED, session, message, missing)
    purpose = OrderPurpose.ROLL_CLOSE if decision.trigger is None else OrderPurpose.EXIT
    detail = DecisionDetail(purpose, DecisionReason.DECISION_QUOTE_INVALID, decision.trigger)
    _emit(
        sim,
        session,
        Slot.DEC,
        Phase.DECIDE,
        SimEventKind.EXIT_DEFERRED,
        campaign_id=held.generation_id,
        detail=detail,
    )


def _submit_closing(  # noqa: PLR0913 — the decision's inputs, all explicit
    sim: _Run,
    session: TradingSession,
    held: HeldPosition,
    decision: HeldDecision,
    *,
    legs: tuple[OrderLeg, ...],
    observed: Mapping[str, QuoteObservation | None],
    usable: Mapping[str, Quote],
) -> None:
    """DEC 5 ORDER_SUBMITTED for FINAL (no limit), EXIT or ROLL_CLOSE (close ``D`` + allowance)."""
    day = session.session_date
    purpose = _CLOSING_PURPOSES[decision.action]
    limit = None
    if purpose is not OrderPurpose.FINAL:
        allowance = sim.resolved.strategy.spec.execution.price_allowance_usd
        limit = package_debit(legs, held.packages, usable) + allowance
    order = Order(
        order_id=order_id(day, purpose),
        campaign_id=held.generation_id,
        purpose=purpose,
        legs=legs,
        packages=held.packages,
        limit_usd=limit,
        trigger=decision.trigger,
        submitted_at_ns=slot_instant(session, Slot.DEC),
        session_date=day,
    )
    _submit(sim, session, order, _observation_ids(observed))


def _submit(sim: _Run, session: TradingSession, order: Order, input_refs: tuple[str, ...]) -> None:
    """DEC 5 ORDER_SUBMITTED: the order is live until it fills or its F3 attempt fails."""
    sim.order = order
    _emit(
        sim,
        session,
        Slot.DEC,
        Phase.DECIDE,
        SimEventKind.ORDER_SUBMITTED,
        campaign_id=order.campaign_id,
        input_refs=input_refs,
        detail=DecisionDetail(order.purpose, None, order.trigger),
    )


# --- decisions while flat --------------------------------------------------------------------


def _decide_flat(
    sim: _Run, session: TradingSession, view: AsOfView, verdict: CoverageVerdict
) -> None:
    """DEC 5 ``decide_flat``: an entry or a due replacement is attempted, skipped or ended."""
    campaign = sim.campaign
    day = session.session_date
    spec = sim.resolved.strategy.spec
    due = campaign.roll_open_due
    decision = decide_flat(
        campaign,
        spec,
        scheduled=scheduled(spec.entry.schedule, session, sim.dataset.sessions),
        final_session=day == sim.resolved.end_date,
        campaign_sessions=_count(sim, campaign.start_session, day) if due else 0,
    )
    match decision.action:
        case FlatAction.ENTRY:
            _attempt_opening(sim, session, view, verdict, OrderPurpose.ENTRY)
        case FlatAction.ROLL_OPEN:
            _attempt_opening(sim, session, view, verdict, OrderPurpose.ROLL_OPEN)
        case FlatAction.SKIP:
            _skip_opening(sim, session, OrderPurpose.ENTRY, decision.reason)
        case FlatAction.END_CAMPAIGN:
            _skip_opening(sim, session, OrderPurpose.ROLL_OPEN, decision.reason)
        case FlatAction.IDLE:
            return


def _attempt_opening(
    sim: _Run,
    session: TradingSession,
    view: AsOfView,
    verdict: CoverageVerdict,
    purpose: OrderPurpose,
) -> None:
    """Skip on a GAP partition, else ``select`` once and submit its order or skip."""
    day = session.session_date
    if verdict is CoverageVerdict.GAP:
        message = f"the quotes partition of {day} has a gap; the due {purpose.value} is missed"
        _warn(sim, WarningCode.DATA_COVERAGE_GAP, session, message, ())
        _skip_opening(sim, session, purpose, DecisionReason.DATA_COVERAGE_GAP)
        return
    ctx = SelectionContext(
        decision_id=_peek(sim, session, Slot.DEC, Phase.DECIDE),
        session=session,
        prior_session=_prior(sim, day),
        settles_on=_following(sim, day),
        purpose=purpose,
        campaign_id=opening_campaign_id(sim.campaign, purpose),
        state=sim.journal.state,
        schedule=sim.resolved.fee_schedule,
    )
    outcome = select(sim.resolved.strategy, view, ctx)
    sim.decisions.append(outcome.decision)
    _selection_warnings(sim, session, outcome.decision)
    if outcome.order is None:
        _skip_opening(sim, session, purpose, outcome.decision.reason)
        return
    _submit(sim, session, outcome.order, _observation_ids(_observe(view, outcome.order.legs)))


def _selection_warnings(sim: _Run, session: TradingSession, decision: CandidateDecision) -> None:
    """Disclose unknown features, skipped expiries and an exhausted budget of one decision."""
    unknown = tuple(check.feature_id for check in decision.conditions if check.value is None)
    if unknown:
        message = f"decision {decision.decision_id} read unknown features {list(unknown)}"
        _warn(sim, WarningCode.FEATURE_UNAVAILABLE, session, message, unknown)
    skipped = tuple(
        f"{expiry.root}:{expiry.expiry.isoformat()}"
        for expiry in decision.expiries
        if expiry.skip_reason is ExpirySkip.PRICING_INPUT_UNAVAILABLE
    )
    if skipped:
        message = f"decision {decision.decision_id} skipped {list(skipped)} for pricing inputs"
        _warn(sim, WarningCode.PRICING_INPUT_UNAVAILABLE, session, message, skipped)
    if decision.budget_exceeded:
        message = f"decision {decision.decision_id} reached the package evaluation budget"
        _warn(sim, WarningCode.SELECTION_BUDGET_EXCEEDED, session, message, (decision.decision_id,))


def _skip_opening(
    sim: _Run, session: TradingSession, purpose: OrderPurpose, reason: DecisionReason | None
) -> None:
    """DEC 5 ENTRY_SKIPPED for an entry; a replacement not opened ends the campaign."""
    if reason is None:
        raise SimulationInvariantError(f"a skipped {purpose.value} needs a reason")
    if purpose is OrderPurpose.ROLL_OPEN:
        _end_unopened(sim, session, Slot.DEC, Phase.DECIDE, reason)
        return
    detail = DecisionDetail(OrderPurpose.ENTRY, reason, None)
    _emit(sim, session, Slot.DEC, Phase.DECIDE, SimEventKind.ENTRY_SKIPPED, detail=detail)


def _end_unopened(
    sim: _Run, session: TradingSession, slot: Slot, phase: Phase, reason: DecisionReason
) -> None:
    """CAMPAIGN_ENDED: a due replacement is not opened; the campaign closed at its roll close."""
    campaign = sim.campaign
    record = campaign_record(
        campaign,
        sim.journal.entries,
        outcome=CampaignOutcome.CLOSED,
        end_session=sim.roll_closed_on,
        exit_trigger=ExitTrigger.ROLL_NOT_REOPENED,
    )
    sim.campaigns.append(record)
    sim.campaign = ended(campaign)
    generation_id = f"c{campaign.number}.g{campaign.generation}"
    detail = DecisionDetail(OrderPurpose.ROLL_OPEN, reason, None)
    _emit(
        sim,
        session,
        slot,
        phase,
        SimEventKind.CAMPAIGN_ENDED,
        campaign_id=generation_id,
        detail=detail,
    )


# --- fills -----------------------------------------------------------------------------------


def _fill_slots(sim: _Run, session: TradingSession) -> None:
    """F1, F2, F3 4: attempt the live order at each fill slot until it fills or is cancelled."""
    for slot in FILL_SLOTS:
        order = sim.order
        if order is None:
            return
        _attempt_fill(sim, session, slot, order)


def _attempt_fill(sim: _Run, session: TradingSession, slot: Slot, order: Order) -> None:
    """Try the order once; a fill is committed, a nonfill recorded (and cancelled at F3)."""
    strategy = sim.resolved.strategy
    at_ns = slot_instant(session, slot)
    ctx = FillContext(
        event_id=_peek(sim, session, slot, Phase.FILL),
        at_ns=at_ns,
        settles_on=_following(sim, session.session_date),
        schedule=sim.resolved.fee_schedule,
        participation_fraction=strategy.spec.execution.participation_fraction,
        premium_direction=strategy.premium_direction,
    )
    outcome = try_fill(order, AsOfView(sim.dataset, at_ns), sim.capacity, sim.journal.state, ctx)
    if isinstance(outcome, Fill):
        _filled(sim, session, slot, outcome)
        return
    _not_filled(sim, session, slot, outcome)


def _filled(sim: _Run, session: TradingSession, slot: Slot, fill: Fill) -> None:
    """Commit a fill, consume its capacity, move the campaign and emit FILLED."""
    order = fill.order
    _commit(sim, fill.entry)
    sim.capacity = sim.capacity.consume(fill)
    sim.order = None
    _fill_transition(sim, fill, session.session_date)
    _check_invariants(sim)
    legs = tuple(
        FilledLeg(leg.terms.contract_id, leg.contracts, leg.price, quote_id)
        for leg, quote_id in zip(fill.legs, fill.quote_ids, strict=True)
    )
    detail = FillDetail(
        order_id=order.order_id,
        purpose=order.purpose,
        packages=order.packages,
        legs=legs,
        net_debit=fill.net_debit,
        fees=_fee_total(fill.fees),
        limit_usd=order.limit_usd,
    )
    _emit(
        sim,
        session,
        slot,
        Phase.FILL,
        SimEventKind.FILLED,
        campaign_id=order.campaign_id,
        input_refs=fill.quote_ids,
        detail=detail,
    )


def _fill_transition(sim: _Run, fill: Fill, day: date) -> None:
    """FillOpen or FillClose; an EXIT or FINAL fill records the campaign before it ends."""
    order = fill.order
    if order.purpose.opening:
        sim.campaign = open_filled(
            sim.campaign,
            purpose=order.purpose,
            legs=order.legs,
            packages=order.packages,
            expiry=_expiry(order.legs),
            fill_session=day,
            net_debit=fill.net_debit,
            fees=_fee_total(fill.fees),
        )
        return
    held = sim.campaign.held
    if held is None:
        raise SimulationInvariantError(f"closing fill {order.order_id} with no held generation")
    pnl = generation_pnl(sim.journal.entries, held.generation_id)
    if order.purpose is OrderPurpose.ROLL_CLOSE:
        sim.roll_closed_on = day
    else:
        record = campaign_record(
            sim.campaign,
            sim.journal.entries,
            outcome=CampaignOutcome.CLOSED,
            end_session=day,
            exit_trigger=order.trigger,
        )
        sim.campaigns.append(record)
    sim.campaign = close_filled(sim.campaign, purpose=order.purpose, generation_pnl=pnl)


def _not_filled(sim: _Run, session: TradingSession, slot: Slot, nonfill: Nonfill) -> None:
    """NOT_FILLED (the first funding refusal of an order disclosed); cancelled after F3."""
    order = nonfill.order
    refused = nonfill.reason is NonfillReason.INSUFFICIENT_CAPITAL
    if refused and order.order_id not in sim.refused:
        sim.refused.add(order.order_id)
        message = f"order {order.order_id} was refused for funding headroom"
        _warn(sim, WarningCode.INSUFFICIENT_CAPITAL, session, message, (order.order_id,))
    detail = NonfillDetail(
        order_id=order.order_id,
        purpose=order.purpose,
        reason=nonfill.reason,
        contract_id=nonfill.contract_id,
        net_debit=nonfill.net_debit,
    )
    _emit(
        sim,
        session,
        slot,
        Phase.FILL,
        SimEventKind.NOT_FILLED,
        campaign_id=order.campaign_id,
        detail=detail,
    )
    if slot is Slot.F3:
        _cancel(sim, session, order)


def _cancel(sim: _Run, session: TradingSession, order: Order) -> None:
    """ORDER_CANCELLED after F3; a closing order is disclosed, a replacement ends the campaign."""
    sim.order = None
    _emit(
        sim,
        session,
        Slot.F3,
        Phase.FILL,
        SimEventKind.ORDER_CANCELLED,
        campaign_id=order.campaign_id,
    )
    if not order.purpose.opening:
        message = f"closing order {order.order_id} was not filled by F3 and was cancelled"
        _warn(sim, WarningCode.EXIT_UNFILLED, session, message, (order.order_id,))
    if order.purpose is OrderPurpose.ROLL_OPEN:
        _end_unopened(sim, session, Slot.F3, Phase.FILL, DecisionReason.ROLL_OPEN_CANCELLED)


# --- close and cut ---------------------------------------------------------------------------


def _close(sim: _Run, session: TradingSession) -> None:
    """CLOSE 3: mark unexpired held legs, else MISSING_VALUATION; flag out-of-range marks."""
    held = sim.campaign.held
    day = session.session_date
    if held is None or held.expiry <= day:
        return
    observed = _observe(AsOfView(sim.dataset, session.close_ns), held.legs)
    marks = _usable(observed, dict.fromkeys(observed, _MARK_RATIO))
    missing = [contract_id for contract_id in observed if contract_id not in marks]
    if missing:
        message = f"held contracts {missing} have no VALID, LOCKED or NO_BID close quote on {day}"
        code = ErrorCode.MISSING_VALUATION
        _invalidate(sim, session, Slot.CLOSE, Phase.MARK, code=code, message=message)
        return
    sim.close_quotes = marks
    refs = tuple(sorted(_observation_ids(observed)))
    _emit(
        sim,
        session,
        Slot.CLOSE,
        Phase.MARK,
        SimEventKind.MARKED,
        campaign_id=held.generation_id,
        input_refs=refs,
    )
    _check_mark_range(sim, session, marks, refs)


def _check_mark_range(
    sim: _Run, session: TradingSession, marks: Mapping[str, Quote], refs: tuple[str, ...]
) -> None:
    """Record MARK_OUT_OF_RANGE when the package's mid value leaves its payoff range."""
    state = sim.journal.state
    holdings = tuple((state.contracts[cid], quantity) for cid, quantity in _held(state))
    mid_value = ZERO_USD
    for terms, quantity in holdings:
        quote = marks[terms.contract_id]
        mid_value += terms.premium_usd(Price.mid(quote.bid, quote.ask), quantity)
    if package_mark_in_range(holdings, mid_value):
        return
    message = f"held package mid value {mid_value.amount} lies outside its payoff range"
    finding = QualityFinding(
        QualityCode.MARK_OUT_OF_RANGE, session.session_date, session.close_ns, refs, message
    )
    sim.quality.append(finding)


def _settle_at_cut(sim: _Run, session: TradingSession) -> None:
    """CUT 6: cash-settle the expiring held package, or MISSING_SETTLEMENT without a value."""
    day = session.session_date
    view = AsOfView(sim.dataset, session.cutoff_ns)
    outcome = settle_expiring(
        sim.journal.state,
        view,
        sim.resolved.fee_schedule,
        event_id=_peek(sim, session, Slot.CUT, Phase.LIFECYCLE),
        session=session,
        settles_on=_following(sim, day),
    )
    if outcome is None:
        return
    observation = view.settlement(outcome.series, day)
    if outcome.entry is None or outcome.observation_id is None or observation is None:
        message = f"no final {outcome.series} settlement of {day} is available by the cutoff"
        code = ErrorCode.MISSING_SETTLEMENT
        _invalidate(sim, session, Slot.CUT, Phase.LIFECYCLE, code=code, message=message)
        return
    _commit(sim, outcome.entry)
    _settle_campaign(sim, outcome.generation_id, day)
    _check_invariants(sim)
    fees = _fee_total(outcome.entry.fee_lines)
    postings = outcome.entry.postings
    cash = sum((p.amount for p in postings if p.account.kind in CASH_KINDS), start=ZERO_USD)
    detail = SettlementDetail(
        series=outcome.series,
        observation_id=outcome.observation_id,
        value=observation.value,
        contract_ids=outcome.contract_ids,
        net_cash=cash + fees,
        fees=fees,
    )
    _emit(
        sim,
        session,
        Slot.CUT,
        Phase.LIFECYCLE,
        SimEventKind.SETTLED,
        campaign_id=outcome.generation_id,
        input_refs=(outcome.observation_id,),
        detail=detail,
    )


def _settle_campaign(sim: _Run, generation_id: str, day: date) -> None:
    """Record the settled campaign, then end it (``R1Campaign.Settle``)."""
    held = sim.campaign.held
    if held is None or held.generation_id != generation_id:
        raise SimulationInvariantError(f"settled {generation_id}, but the campaign holds {held}")
    record = campaign_record(
        sim.campaign,
        sim.journal.entries,
        outcome=CampaignOutcome.SETTLED,
        end_session=day,
        exit_trigger=ExitTrigger.SETTLEMENT,
    )
    sim.campaigns.append(record)
    sim.campaign = settled(sim.campaign)


def _snapshot_at_cut(sim: _Run, session: TradingSession) -> None:
    """CUT 7 of a window session: the snapshot at this session's CLOSE marks."""
    _snapshot(sim, session, sim.close_quotes)


def _snapshot(sim: _Run, session: TradingSession, quotes: Mapping[str, Quote] | None) -> None:
    """CUT 7 SNAPSHOT: the account point and position rows; ``quotes`` None after the window."""
    state = sim.journal.state
    schedule = sim.resolved.fee_schedule
    cash, receivable, payable = _cash_balances(state)
    mid_nlv, natural_nlv = _nlvs(state, quotes)
    point = AccountPoint(
        session_date=session.session_date,
        market_valuation_at_ns=session.close_ns,
        ledger_cutoff_at_ns=session.cutoff_ns,
        cash=cash,
        receivable=receivable,
        payable=payable,
        encumbrance=_reserve(state, sim.resolved),
        headroom=funding_headroom(state, schedule),
        mid_nlv=mid_nlv,
        natural_nlv=natural_nlv,
    )
    sim.curve.append(point)
    sim.positions.extend(_position_rows(state, session.session_date, quotes))
    _emit(sim, session, Slot.CUT, Phase.SNAPSHOT, SimEventKind.SNAPSHOT)


def _nlvs(state: LedgerState, quotes: Mapping[str, Quote] | None) -> tuple[Usd | None, Usd | None]:
    """Return (mid, natural) NLV at ``quotes``; (None, None) for a held position without them."""
    if quotes is None and _held(state):
        return None, None
    marks = {} if quotes is None else quotes
    try:
        mid = value_account(state, marks, {}, MarkBasis.MID)
        natural = value_account(state, marks, {}, MarkBasis.NATURAL)
    except MissingMarkError as e:
        raise SimulationInvariantError(f"the snapshot lacks a mark of a held contract: {e}") from e
    return mid.nlv, natural.nlv


def _position_rows(
    state: LedgerState, day: date, quotes: Mapping[str, Quote] | None
) -> list[PositionRow]:
    """Return one row per held contract, marked at ``quotes`` when given."""
    rows: list[PositionRow] = []
    for contract_id, quantity in _held(state):
        owners = {lot.campaign_id for lot in state.lots[contract_id]}
        owner = owners.pop() if len(owners) == 1 else None
        if owner is None:
            raise SimulationInvariantError(f"{contract_id} must be held by one generation")
        quote = None if quotes is None else quotes.get(contract_id)
        mid = None if quote is None else Price.mid(quote.bid, quote.ask)
        natural = None if quote is None else (quote.bid if quantity > 0 else quote.ask)
        rows.append(PositionRow(day, contract_id, owner, quantity, mid, natural))
    return rows


# --- after the window ------------------------------------------------------------------------


def _finish_window(sim: _Run) -> None:
    """Apply ``end_status`` (unless invalid) and record the campaign still active."""
    if not sim.invalid:
        liquidate = sim.resolved.strategy.spec.end_policy == "liquidate_at_final_session"
        sim.status = end_status(
            sim.status,
            liquidate_at_final=liquidate,
            held=bool(_held(sim.journal.state)),
            final_session=sim.resolved.end_date,
        )
    campaign = sim.campaign
    if campaign.held is None and not campaign.roll_open_due:
        return
    outcome, end, trigger = CampaignOutcome.INCOMPLETE, None, None
    if sim.status.status is CalculationStatus.VALID and campaign.held is not None:
        outcome = CampaignOutcome.OPEN
    elif sim.status.status is CalculationStatus.VALID:
        outcome, end, trigger = (
            CampaignOutcome.CLOSED,
            sim.roll_closed_on,
            ExitTrigger.ROLL_NOT_REOPENED,
        )
    record = campaign_record(
        campaign, sim.journal.entries, outcome=outcome, end_session=end, exit_trigger=trigger
    )
    sim.campaigns.append(record)


def _settle_after_window(sim: _Run) -> None:
    """Run settle-only sessions while anything is due; dues left after them are a defect."""
    if sim.invalid:
        return
    for session in sim.calendar.after:
        if not _dues(sim.journal.state):
            return
        _settle_due(sim, session)
        _snapshot(sim, session, None)
    if _dues(sim.journal.state):
        raise SimulationInvariantError(
            f"dues {_unsettled(sim.journal.state)} remain after the "
            f"{len(sim.calendar.after)} settle-only sessions"
        )


def _bundle(sim: _Run) -> ArtifactBundle:
    """Return the artifacts; the bundle digests each table."""
    return ArtifactBundle(
        _result(sim),
        tuple(sim.events),
        sim.journal.entries,
        tuple(sim.positions),
        tuple(sim.curve),
        tuple(sim.campaigns),
        tuple(sim.decisions),
        tuple(sim.quality),
    )


def _result(sim: _Run) -> SimulationResult:
    """Return the result envelope of the finished run."""
    resolved = sim.resolved
    spec = resolved.strategy.spec
    manifest = sim.dataset.manifest
    state = sim.journal.state
    valid = sim.status.status is CalculationStatus.VALID
    open_positions = _held(state)
    provenance = ResultProvenance(
        manifest_id=manifest.manifest_id,
        engine_version=resolved.engine_version,
        policy_versions=resolved.policy_versions,
        calendar_version=manifest.calendar_version,
        product_rules_version=manifest.product_rules_version,
        feature_versions=manifest.feature_versions,
        license_policy_id=manifest.license_policy_id,
    )
    return SimulationResult(
        calculation_status=sim.status.status,
        data_fidelity=manifest.fidelity,
        limitations=manifest.limitations,
        execution_basis="synthetic_natural_package",
        calibration_status="uncalibrated",
        cost_basis="assumed_schedule",
        assignment_basis="not_applicable",
        window_requested=(resolved.start_date, resolved.end_date),
        window_simulated=(resolved.start_date, sim.last_session),
        valuation_clock=spec.clock_profile,
        initial_equity_usd=spec.account.initial_cash_usd,
        final_equity_usd=_final_equity(sim) if valid else None,
        open_positions=open_positions,
        unsettled_cash=_unsettled(state),
        end_policy=spec.end_policy,
        warnings=tuple(sim.warnings),
        invalid_reasons=sim.status.reasons,
        headline_eligible=valid and not open_positions,
        provenance=provenance,
    )


def _final_equity(sim: _Run) -> Usd:
    """Return the final window session's mid NLV."""
    end = sim.resolved.end_date
    point = next((p for p in sim.curve if p.session_date == end), None)
    if point is None or point.mid_nlv is None:
        raise SimulationInvariantError(f"a valid run has no mid NLV on its final session {end}")
    return point.mid_nlv


# --- events, warnings and invariants ---------------------------------------------------------


def _peek(sim: _Run, session: TradingSession, slot: Slot, phase: Phase) -> str:
    """Return the id the next event of (session, slot, phase) will have."""
    day = session.session_date
    return event_id(day, slot, phase, sim.seqs.get((day, slot, phase), 0) + 1)


def _emit(  # noqa: PLR0913 — the event's coordinates and payload, all explicit
    sim: _Run,
    session: TradingSession,
    slot: Slot,
    phase: Phase,
    kind: SimEventKind,
    *,
    campaign_id: str | None = None,
    input_refs: tuple[str, ...] = (),
    detail: EventDetail | None = None,
) -> None:
    """Append the next event of (session, slot, phase), summarizing the state after it."""
    day = session.session_date
    seq = sim.seqs.get((day, slot, phase), 0) + 1
    sim.seqs[(day, slot, phase)] = seq
    event = SimEvent(
        event_id=event_id(day, slot, phase, seq),
        at_ns=slot_instant(session, slot),
        session_date=day,
        slot=slot,
        phase=phase,
        seq=seq,
        kind=kind,
        campaign_id=campaign_id,
        input_refs=input_refs,
        summary=_summary(sim),
        detail=detail,
    )
    sim.events.append(event)


def _summary(sim: _Run) -> EventSummary:
    """Return the α state: balances, reserve, held contracts, live order and status."""
    state = sim.journal.state
    cash, receivable, payable = _cash_balances(state)
    order = sim.order
    live = None
    if order is not None:
        live = OrderSnapshot(order.order_id, order.purpose, order.packages, order.limit_usd)
    reserve = _reserve(state, sim.resolved)
    return EventSummary(cash, receivable, payable, reserve, _held(state), live, sim.status.status)


def _invalidate(  # noqa: PLR0913 — the event's coordinates and issue, all explicit
    sim: _Run,
    session: TradingSession,
    slot: Slot,
    phase: Phase,
    *,
    code: ErrorCode,
    message: str,
) -> None:
    """Move the status to INVALID and emit INVALIDATED; the loop stops after it."""
    issue = Issue(
        code=code,
        message=message,
        json_pointer="",
        affected_interval=session.session_date.isoformat(),
    )
    sim.status = sim.status.invalidate(issue)
    _emit(sim, session, slot, phase, SimEventKind.INVALIDATED, detail=issue)


def _warn(
    sim: _Run, code: WarningCode, session: TradingSession, message: str, refs: tuple[str, ...]
) -> None:
    """Append a warning dated the session."""
    sim.warnings.append(RunWarning(code, session.session_date, message, refs))


def _commit(sim: _Run, entry: LedgerEntry) -> None:
    """Commit the engine's own entry; a ledger rejection is an engine defect."""
    try:
        sim.journal.commit(entry)
    except LedgerInvariantError as e:
        raise SimulationInvariantError(
            f"the ledger rejected the engine's own entry {entry.event_id}: {e}"
        ) from e


def _check_funded(sim: _Run) -> None:
    """Raise unless ``funding_headroom >= 0`` (``R1Campaign.FullyFunded``)."""
    headroom = funding_headroom(sim.journal.state, sim.resolved.fee_schedule)
    if headroom < ZERO_USD:
        raise SimulationInvariantError(
            f"FullyFunded: funding headroom {headroom.amount} < 0 after "
            f"{sim.journal.state.entry_count} entries"
        )


def _check_invariants(sim: _Run) -> None:
    """Raise unless the account is funded and reserves exactly the campaign's held package."""
    _check_funded(sim)
    state = sim.journal.state
    held = sim.campaign.held
    lots = dict(_held(state))
    reserved = set(campaign_encumbrances(state, sim.resolved.fee_schedule))
    expected: dict[str, int] = {}
    generations: set[str] = set()
    if held is not None:
        expected = {leg.terms.contract_id: leg.ratio * held.packages for leg in held.legs}
        generations = {held.generation_id}
    if lots != expected or reserved != generations:
        raise SimulationInvariantError(
            f"ReserveMatchesPosition: the ledger holds {lots} reserved for {sorted(reserved)}, "
            f"the campaign {expected} of {sorted(generations)}"
        )


# --- small pure helpers ----------------------------------------------------------------------


def _observe(view: AsOfView, legs: Iterable[OrderLeg]) -> dict[str, QuoteObservation | None]:
    """Return each leg's latest quote at most 120 s old (any status), in leg order."""
    return {
        leg.terms.contract_id: view.quote(leg.terms.contract_id, max_age_ns=QUOTE_MAX_AGE_NS)
        for leg in legs
    }


def _usable(
    observed: Mapping[str, QuoteObservation | None], ratios: Mapping[str, int]
) -> dict[str, Quote]:
    """Return the ``usable_quote`` of each contract whose status its side can use."""
    usable: dict[str, Quote] = {}
    for contract_id, observation in observed.items():
        quote = usable_quote(observation, ratios[contract_id])
        if quote is not None:
            usable[contract_id] = quote
    return usable


def _observation_ids(observed: Mapping[str, QuoteObservation | None]) -> tuple[str, ...]:
    """Return the ids of the observations that exist, in the mapping's order."""
    return tuple(obs.observation_id for obs in observed.values() if obs is not None)


def _cash_balances(state: LedgerState) -> tuple[Usd, Usd, Usd]:
    """Return (CASH, ΣRECEIVABLE, -ΣPAYABLE)."""
    cash = receivable = payable = ZERO_USD
    for account, amount in state.balances.items():
        if account.kind is AccountKind.CASH:
            cash += amount
        elif account.kind is AccountKind.RECEIVABLE:
            receivable += amount
        elif account.kind is AccountKind.PAYABLE:
            payable -= amount
    return cash, receivable, payable


def _reserve(state: LedgerState, resolved: ResolvedRun) -> Usd:
    """Return Σ ``campaign_encumbrances`` (settlement + fee provision)."""
    encumbrances = campaign_encumbrances(state, resolved.fee_schedule).values()
    return sum((e.settlement + e.fee_provision for e in encumbrances), start=ZERO_USD)


def _held(state: LedgerState) -> tuple[tuple[str, int], ...]:
    """Return (contract_id, signed quantity) of every held option contract, sorted by id."""
    return tuple(
        (contract_id, sum(lot.quantity for lot in lots))
        for contract_id, lots in sorted(state.lots.items())
        if contract_id in state.contracts
    )


def _dues(state: LedgerState) -> bool:
    """Return whether any RECEIVABLE or PAYABLE is outstanding."""
    return any(account.kind in DATED_KINDS for account in state.balances)


def _unsettled(state: LedgerState) -> tuple[tuple[str, Usd], ...]:
    """Return (ISO settle date, signed balance) of every outstanding due, sorted."""
    return tuple(
        sorted(
            (account.ref, amount)
            for account, amount in state.balances.items()
            if account.kind in DATED_KINDS
        )
    )


def _fee_total(lines: Iterable[FeeLine]) -> Usd:
    """Return Σ fee line amounts."""
    return sum((line.amount for line in lines), start=ZERO_USD)


def _count(sim: _Run, first: date | None, last: date) -> int:
    """Return the table sessions from ``first`` to ``last`` inclusive."""
    if first is None:
        raise SimulationInvariantError(f"a held or due campaign has no start session at {last}")
    return sim.index[last] - sim.index[first] + 1


def _following(sim: _Run, day: date) -> date:
    """Return the next table session (the T+1 settlement date)."""
    position = sim.index[day] + 1
    if position >= len(sim.dataset.sessions):
        raise SimulationInvariantError(f"the table has no session after {day}")
    return sim.dataset.sessions[position].session_date


def _prior(sim: _Run, day: date) -> date | None:
    """Return the previous table session; None on the table's first."""
    position = sim.index[day]
    return None if position == 0 else sim.dataset.sessions[position - 1].session_date


def _expiry(legs: Sequence[OrderLeg]) -> date:
    """Return the one expiry date spelled in the legs' contract ids ``{root}:{date}:{C|P}:{K}``."""
    expiries = {date.fromisoformat(leg.terms.contract_id.split(":")[1]) for leg in legs}
    if len(expiries) != 1:
        raise SimulationInvariantError(f"an opening package spans expiries {sorted(expiries)}")
    return expiries.pop()
