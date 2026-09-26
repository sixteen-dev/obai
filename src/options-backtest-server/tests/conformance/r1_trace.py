"""R1Campaign trace checker: the refinement mapping α over a run's artifacts (ADR 0002 §7, §17).

``check(model, trace)`` walks ``events`` (and the ``account_curve``) as a behaviour of
``R1Campaign.tla`` (design ``options-backtesting-v3/tla/``) and reports every ``Safety`` conjunct
broken in some state, every action property broken by some step, and the non-vacuity canaries
of the TLC configs the trace reaches. The checker is self-contained: it reads the events, the
strategy's rules and the dataset's contracts and quotes, never the engine's own modules.

α (ADR 0002 §7 and §17 item 41):

- ``kind``: the premium direction; ``Q`` = -1 for credit, +1 for debit.
- ``cash - pay``: CASH - payable. TLA pays fees from cash while WP1 posts them payable, so α's
  ``cash`` and ``pay`` both carry the fees posted since the last SETTLE_DUE; only
  ``pay`` = payable - those fees is read on its own (``CanarySettledCreditLoss``).
- ``recv``, ``reserve``, ``pos``, ``order``, ``calc``: the event summary's receivable, reserve,
  held, order and status; ``order.market`` is ``limit_usd is None``.
- ``gen``: the ``campaign_id`` of the opening fill; ``settledGens``: the generations of SETTLED
  events; ``refused``: the purposes of INSUFFICIENT_CAPITAL nonfills (price-eligible, check 6).
- ``quote.ok``: a MARKED event at DEC or CLOSE; at F1-F3 a FILLED event, or a NOT_FILLED whose
  reason is not NO_OBSERVATION, QUOTE_INVALID or NO_SIDE.
- ``W`` (as ``W·m·n``): the held package's settlement bound ``max(0, -min payoff)``; ``Fee``: the
  fee of one fill of it, fee per contract side × Σ|q|; ``HeldReserve`` = the two summed.
- ``basis``, ``realized``, ``entryDebit``, ``cstart``, ``rolls``, ``rollOpenDue``: rebuilt from
  the fills exactly as ``FillOpen`` and ``FillClose`` define them.
- ``done``, ``headline``: the end of the trace and ``result.headline_eligible``.

Beyond the spec's variables the checker holds the run to its slot program: event keys and ids,
each event at its session's slot instant (so an early close moves every slot), the money delta
of every event kind, fill obligations (F1, F2, F3, cancel), the DecideHeld and DecideFlat
refinements at DEC 5, the settlement obligation at CUT, settle-only sessions and the account
curve. Every quote α reads (a mark's or a fill's) must be of the session, available and observed
by the event, and at most 120 s old; a mark's must also be usable for what it witnesses.

The spec's obligations are checked on α step by step too (ADR 0002 §17 item 53): ``PostDebit``
on every fill and settlement (``cash`` unchanged, ``d``'s positive part into ``pay`` and its
negative part into ``recv``), ``AdvanceSession`` (dues outstanding at a session's OPEN settle
there), ``MarkClose`` (a valid window session holding an unexpired position has one CLOSE mark,
MARKED or MISSING_VALUATION), the account curve's held NLVs at that session's CLOSE marks, and
every DEC mark's ``LiquidationDetail`` equal, component by component, to α's ``LiquidationPnL``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from itertools import groupby
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from options_backtest.engine.clock import Phase
from options_backtest.engine.orders import ExitTrigger, NonfillReason, OrderPurpose
from options_backtest.errors import ErrorCode, Issue
from options_backtest.models.artifacts import (
    DecisionDetail,
    DecisionReason,
    FillDetail,
    LiquidationDetail,
    NonfillDetail,
    OrderSnapshot,
    SettlementDetail,
    SimEvent,
    SimEventKind,
)
from options_backtest.models.result import CalculationStatus
from options_backtest.models.strategy import SequentialRoll
from options_backtest.models.strategy_checks import PremiumDirection
from options_backtest.reference.calendars import Slot

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from options_backtest.data.manifest import FrozenDataset
    from options_backtest.models.artifacts import AccountPoint, ArtifactBundle
    from options_backtest.models.market import ContractTerms
    from options_backtest.models.strategy_checks import ValidatedStrategy

CANARIES: Final = (
    # R1Campaign.cfg
    "CanaryValidClosedRun",
    "CanaryIncomplete",
    "CanaryMissingValuation",
    "CanarySettledCreditLoss",
    "CanaryRolled",
    "CanaryFundingRefusal",
    "CanaryCloseAboveWidth",
    "CanaryStopLossExit",
    # R1Campaign.pass-caps.cfg
    "CanaryCampaignCapExit",
    "CanaryRollCapExit",
    "CanaryFeeExceedsCredit",
    # R1Campaign.pass-deep.cfg (with CanaryRolled and CanaryStopLossExit above)
    "CanaryTakeProfitExit",
    # R1Campaign.pass-refusal.cfg
    "CanaryCreditCloseRefused",
    "CanaryRefusedThenSettled",
)
"""Every canary on a ``\\* canaries:`` line of the R1Campaign TLC configs (the design tree is not
under version control here, so the list is restated)."""

FEE_PER_CONTRACT: Final = MappingProxyType(
    {"illustrative_flat_1usd_per_contract_side_v1": Decimal("1.00")}
)
"""Fee schedule id to max(trade, settlement) fee per contract (ADR 0002 §17 item 28: $1.00 per
traded side, $0 settlement): the per-contract ``Fee`` and exit-fee provision."""

_ZERO: Final = Decimal(0)
_QUOTE_MAX_AGE_NS: Final = 120 * 10**9  # design §8.4: at most 120 s old, inclusive
_MINUTE_NS: Final = 60 * 10**9
_BEFORE_CLOSE_MIN: Final = MappingProxyType({Slot.DEC: 15, Slot.F1: 14, Slot.F2: 13, Slot.F3: 12})
"""Minutes before the session's close of each order slot (ADR 0002 §7)."""
_SETTLE_ONLY_SESSIONS: Final = 5  # ADR 0002 §7
_OPENING: Final = frozenset({OrderPurpose.ENTRY, OrderPurpose.ROLL_OPEN})
_CLOSING: Final = frozenset({OrderPurpose.EXIT, OrderPurpose.ROLL_CLOSE, OrderPurpose.FINAL})
_QUOTE_LEVEL: Final = frozenset(
    {NonfillReason.NO_OBSERVATION, NonfillReason.QUOTE_INVALID, NonfillReason.NO_SIDE}
)
_FILL_SLOTS: Final = (Slot.F1, Slot.F2, Slot.F3)
_ORDER_SLOTS: Final = frozenset({Slot.DEC, *_FILL_SLOTS})
_EVENT_ID: Final = re.compile(r"(\d{4}-\d{2}-\d{2}):([A-Z0-9]+):([1-7]):([1-9][0-9]*)")
_PLACES: Final[Mapping[SimEventKind, frozenset[tuple[Slot, Phase]]]] = MappingProxyType(
    {
        SimEventKind.DEPOSIT: frozenset({(Slot.OPEN, Phase.SETTLE_DUE)}),
        SimEventKind.SETTLE_DUE: frozenset({(Slot.OPEN, Phase.SETTLE_DUE)}),
        SimEventKind.INVALIDATED: frozenset(
            {
                (Slot.OPEN, Phase.SETTLE_DUE),
                (Slot.DEC, Phase.PUBLISH),
                (Slot.CLOSE, Phase.MARK),
                (Slot.CUT, Phase.LIFECYCLE),
            }
        ),
        SimEventKind.MARKED: frozenset({(Slot.DEC, Phase.MARK), (Slot.CLOSE, Phase.MARK)}),
        SimEventKind.ORDER_SUBMITTED: frozenset({(Slot.DEC, Phase.DECIDE)}),
        SimEventKind.EXIT_DEFERRED: frozenset({(Slot.DEC, Phase.DECIDE)}),
        SimEventKind.ENTRY_SKIPPED: frozenset({(Slot.DEC, Phase.DECIDE)}),
        SimEventKind.CAMPAIGN_ENDED: frozenset({(Slot.DEC, Phase.DECIDE), (Slot.F3, Phase.FILL)}),
        SimEventKind.FILLED: frozenset((slot, Phase.FILL) for slot in _FILL_SLOTS),
        SimEventKind.NOT_FILLED: frozenset((slot, Phase.FILL) for slot in _FILL_SLOTS),
        SimEventKind.ORDER_CANCELLED: frozenset({(Slot.F3, Phase.FILL)}),
        SimEventKind.SETTLED: frozenset({(Slot.CUT, Phase.LIFECYCLE)}),
        SimEventKind.SNAPSHOT: frozenset({(Slot.CUT, Phase.SNAPSHOT)}),
    }
)
"""Where each event kind may sit (``simulator.run``'s slot program)."""


@dataclass(frozen=True, slots=True)
class LegFacts:
    """Contract terms the checker needs.

    Attributes:
        right: ``"call"`` or ``"put"``.
        strike: Strike in price units.
        multiplier: Premium multiplier (USD per price unit per contract).
        units: Deliverable index units per contract (the cash-settlement scale).
        expiry: The expiry date in the contract id (α's ``pos.expiry``).

    """

    right: str
    strike: Decimal
    multiplier: Decimal
    units: Decimal
    expiry: date


@dataclass(frozen=True, slots=True)
class QuoteFacts:
    """One quote observation.

    Attributes:
        contract_id: Contract quoted.
        bid: Bid price.
        ask: Ask price.
        observed_at_ns: Observation instant.
        available_at_ns: First instant it could inform a decision.
        session_date: Session the observation belongs to.

    """

    contract_id: str
    bid: Decimal
    ask: Decimal
    observed_at_ns: int
    available_at_ns: int
    session_date: date


@dataclass(frozen=True, slots=True)
class Model:
    """The constants of one run: R1Campaign's CONSTANTS in implementation units.

    Attributes:
        kind: Premium direction (``R1Campaign.kind``).
        sessions: Table sessions, in order.
        start: First window session.
        end: Final window session (``Sessions``).
        initial_cash: ``Cash0``.
        exit_dte: ``ExitDTE`` in calendar days.
        max_hold: ``MaxHold`` in table sessions, the fill session being 1.
        take_profit: Take-profit fraction of the basis; None when off.
        stop_loss: Stop-loss multiple of the basis; None when off.
        roll_dte: ``RollDTE``; None when rolls are disabled.
        max_rolls: ``MaxRolls``; 0 when rolls are disabled.
        max_campaign: ``MaxCampaign``; None when rolls are disabled.
        fee_per_contract: Fee per contract side, also the exit-fee provision per contract.
        price_allowance: ``price_allowance_usd``, added once to a closing limit.
        liquidate_at_final: ``end_policy == "liquidate_at_final_session"``.
        contracts: Terms by contract id.
        quotes: Quote observations by observation id.
        clock: (open_ns, close_ns, cutoff_ns) of every table session.

    """

    kind: PremiumDirection
    sessions: tuple[date, ...]
    start: date
    end: date
    initial_cash: Decimal
    exit_dte: int
    max_hold: int
    take_profit: Decimal | None
    stop_loss: Decimal | None
    roll_dte: int | None
    max_rolls: int
    max_campaign: int | None
    fee_per_contract: Decimal
    price_allowance: Decimal
    liquidate_at_final: bool
    contracts: Mapping[str, LegFacts]
    quotes: Mapping[str, QuoteFacts]
    clock: Mapping[date, tuple[int, int, int]]


@dataclass(frozen=True, slots=True)
class Trace:
    """What a run produced that α reads.

    Attributes:
        events: Events in key order.
        account_curve: The CUT account points.
        status: ``result.calculation_status``.
        headline: ``result.headline_eligible``.

    """

    events: tuple[SimEvent, ...]
    account_curve: tuple[AccountPoint, ...]
    status: CalculationStatus
    headline: bool


@dataclass(frozen=True, slots=True)
class Report:
    """The verdict on one trace.

    Attributes:
        violations: One message per broken property, each starting with the property's name.
        canaries: The canaries (``CANARIES`` names) the trace reaches.

    """

    violations: tuple[str, ...]
    canaries: frozenset[str]


@dataclass(frozen=True, slots=True)
class _Alpha:
    """The R1Campaign state after an event (money as exact ``Decimal`` USD)."""

    cash: Decimal
    receivable: Decimal
    payable: Decimal
    fees_due: Decimal
    reserve: Decimal
    held: tuple[tuple[str, int], ...]
    order: OrderSnapshot | None
    submitted_at_ns: int | None
    status: CalculationStatus
    gen: str | None
    opened: date | None
    entry_debit: Decimal
    basis: Decimal
    realized: Decimal
    cstart: date | None
    rolls: int
    roll_open_due: bool
    settled: frozenset[str]
    refused: frozenset[OrderPurpose]


_INITIAL: Final = _Alpha(
    cash=_ZERO,
    receivable=_ZERO,
    payable=_ZERO,
    fees_due=_ZERO,
    reserve=_ZERO,
    held=(),
    order=None,
    submitted_at_ns=None,
    status=CalculationStatus.VALID,
    gen=None,
    opened=None,
    entry_debit=_ZERO,
    basis=_ZERO,
    realized=_ZERO,
    cstart=None,
    rolls=0,
    roll_open_due=False,
    settled=frozenset(),
    refused=frozenset(),
)


@dataclass(frozen=True, slots=True)
class _Triggers:
    """DecideHeld's predicates at one DEC."""

    quote_ok: bool
    close_debit: Decimal | None
    pnl: Decimal | None
    time_exit: bool
    take_profit: bool
    stop_loss: bool
    campaign_cap: bool
    roll_trigger: bool
    roll_cap: bool
    roll_due: bool


# --- building the model and the trace ------------------------------------------------------


def model_of(strategy: ValidatedStrategy, dataset: FrozenDataset, start: date, end: date) -> Model:
    """Return the constants of a run of ``strategy`` on ``dataset`` over ``[start, end]``.

    Args:
        strategy: The validated strategy.
        dataset: The dataset the run used.
        start: First window session.
        end: Final window session.

    Returns:
        The model; a contract's facts come from its first version (a revision invalidates the
        run at the next OPEN, ADR 0002 §10).

    Raises:
        ValueError: For a fee schedule other than the one R1 binds, or a window outside the
            table.

    """
    spec = strategy.spec
    fee = FEE_PER_CONTRACT.get(spec.fee_schedule_id)
    sessions = tuple(session.session_date for session in dataset.sessions)
    if fee is None or start not in sessions or end not in sessions:
        raise ValueError(f"no R1 model for {spec.fee_schedule_id!r} over {start}..{end}")
    roll = spec.roll if isinstance(spec.roll, SequentialRoll) else None
    contracts: dict[str, LegFacts] = {}
    for version in dataset.contracts:
        contracts.setdefault(version.terms.contract_id, _leg_facts(version.terms))
    quotes = {
        q.observation_id: QuoteFacts(
            q.contract_id, q.bid, q.ask, q.observed_at_ns, q.available_at_ns, q.session_date
        )
        for q in dataset.quotes
    }
    clock = {s.session_date: (s.open_ns, s.close_ns, s.cutoff_ns) for s in dataset.sessions}
    exits = spec.exits
    return Model(
        kind=strategy.premium_direction,
        sessions=sessions,
        start=start,
        end=end,
        initial_cash=spec.account.initial_cash_usd.amount,
        exit_dte=exits.exit_dte,
        max_hold=exits.max_holding_sessions,
        take_profit=None if exits.take_profit is None else exits.take_profit.fraction,
        stop_loss=None if exits.stop_loss is None else exits.stop_loss.multiple,
        roll_dte=None if roll is None else roll.trigger_dte,
        max_rolls=0 if roll is None else roll.max_rolls,
        max_campaign=None if roll is None else roll.max_campaign_sessions,
        fee_per_contract=fee,
        price_allowance=spec.execution.price_allowance_usd.amount,
        liquidate_at_final=spec.end_policy == "liquidate_at_final_session",
        contracts=MappingProxyType(contracts),
        quotes=MappingProxyType(quotes),
        clock=MappingProxyType(clock),
    )


def _leg_facts(terms: ContractTerms) -> LegFacts:
    components = terms.deliverable.components
    if len(components) != 1:
        raise ValueError(f"{terms.contract_id}: expected one deliverable component")
    return LegFacts(
        right=terms.option_type.value,
        strike=terms.strike.value,
        multiplier=terms.premium_multiplier,
        units=components[0].units,
        expiry=date.fromisoformat(terms.contract_id.split(":")[1]),
    )


def trace_of(bundle: ArtifactBundle) -> Trace:
    """Return the parts of a run's artifacts the checker reads.

    Args:
        bundle: The run's artifacts.

    Returns:
        The trace.

    """
    return Trace(
        events=bundle.events,
        account_curve=bundle.account_curve,
        status=bundle.result.calculation_status,
        headline=bundle.result.headline_eligible,
    )


# --- the check ---------------------------------------------------------------------------------


def check(model: Model, trace: Trace) -> Report:
    """Check a trace against R1Campaign under α.

    Args:
        model: The run's constants.
        trace: The run's events, account curve and end status.

    Returns:
        Every violation found and every canary reached.

    """
    found = _check_keys(trace.events) + _check_instants(model, trace.events)
    found += _check_sessions(model, trace) + _check_curve(model, trace)
    reached: set[str] = set()
    state = _INITIAL
    for _, group in groupby(trace.events, key=lambda event: event.session_date):
        state, more, more_reached = _check_session(model, state, tuple(group))
        found += more
        reached |= more_reached
    found += _check_end(model, state, trace)
    reached |= _end_canaries(trace)
    return Report(violations=tuple(found), canaries=frozenset(reached))


def _violation(prop: str, event: SimEvent, message: str) -> str:
    return f"{prop} at {event.event_id}: {message}"


def _check_session(
    model: Model, state: _Alpha, events: tuple[SimEvent, ...]
) -> tuple[_Alpha, list[str], set[str]]:
    """Walk one session; DecideHeld/DecideFlat are checked on the state DEC 5 decides from.

    The session's obligations are checked on the state they start from: AdvanceSession at its
    first event, MarkClose after its last fill slot, TryFill over its order slots.
    """
    early = tuple(e for e in events if _before_decide(e))
    late = tuple(e for e in events if not _before_decide(e) and e.slot in _ORDER_SLOTS)
    rest = tuple(e for e in events if e.slot in (Slot.CLOSE, Slot.CUT))
    session = events[0].session_date
    found = _settle_obligation(state, events[0])
    state, more, reached = _walk(model, state, early)
    found += more
    if state.status is CalculationStatus.VALID and model.start <= session <= model.end:
        marked = next((e for e in early if e.kind is SimEventKind.MARKED), None)
        decision = next((e for e in late if e.slot is Slot.DEC), None)
        more, more_reached = _check_decision(model, state, session, marked, decision)
        found += more
        reached |= more_reached
    state, more, more_reached = _walk(model, state, late)
    found += more + _mark_obligation(model, state, session, rest) + _fill_obligation(events)
    state, more, last_reached = _walk(model, state, rest)
    return state, found + more, reached | more_reached | last_reached


def _settle_obligation(state: _Alpha, first: SimEvent) -> list[str]:
    """AdvanceSession: recv and pay settle at the next session's OPEN (T+1).

    A session that starts with dues outstanding must therefore start with SETTLE_DUE.
    """
    if not (state.receivable or state.payable) or first.kind is SimEventKind.SETTLE_DUE:
        return []
    return [f"AdvanceSession at {first.session_date}: dues outstanding at OPEN did not settle"]


def _mark_obligation(
    model: Model, state: _Alpha, session: date, events: Sequence[SimEvent]
) -> list[str]:
    """MarkClose: an unexpired position (``pos.expiry > sess``) is marked or invalidates the run.

    A valid window session holding one has exactly one CLOSE event of phase MARK: MARKED, or
    INVALIDATED for MISSING_VALUATION.
    """
    due = bool(state.held) and state.status is CalculationStatus.VALID and session <= model.end
    if not due or _expiry(model, state) <= session:
        return []
    marks = [e for e in events if e.slot is Slot.CLOSE and e.phase is Phase.MARK]
    if len(marks) == 1 and marks[0].kind is SimEventKind.MARKED:
        return []
    issue = None if len(marks) != 1 else marks[0].detail
    if isinstance(issue, Issue) and issue.code is ErrorCode.MISSING_VALUATION:
        return []
    return [f"MarkClose at {session}: a held position is neither marked nor invalidated"]


def _before_decide(event: SimEvent) -> bool:
    return event.slot is Slot.OPEN or (event.slot is Slot.DEC and event.phase < Phase.DECIDE)


def _walk(
    model: Model, state: _Alpha, events: Sequence[SimEvent]
) -> tuple[_Alpha, list[str], set[str]]:
    found: list[str] = []
    reached: set[str] = set()
    for event in events:
        after, more = _step(model, state, event)
        witnessed, witness_reached = _witness(model, state, after, event)
        found += more + witnessed
        reached |= witness_reached | _state_canaries(model, after)
        state = after
    return state, found, reached


def _step(model: Model, state: _Alpha, event: SimEvent) -> tuple[_Alpha, list[str]]:
    found = _placement(event) + _status_step(model, state, event)
    match event.kind:
        case SimEventKind.DEPOSIT:
            after, more = _on_deposit(model, state, event)
        case SimEventKind.SETTLE_DUE:
            after, more = _on_settle_due(state, event)
        case SimEventKind.ORDER_SUBMITTED:
            after, more = _on_submitted(model, state, event)
        case SimEventKind.FILLED:
            after, more = _on_fill(model, state, event)
        case SimEventKind.NOT_FILLED:
            after, more = _on_nonfill(state, event)
        case SimEventKind.ORDER_CANCELLED:
            after, more = _on_cancelled(state, event)
        case SimEventKind.CAMPAIGN_ENDED:
            after, more = _end_campaign(_observe(state, event)), _unchanged(state, event)
        case SimEventKind.SETTLED:
            after, more = _on_settled(model, state, event)
        case SimEventKind.INVALIDATED:
            after, more = _on_invalidated(state, event)
        case _:  # MARKED, EXIT_DEFERRED, ENTRY_SKIPPED, SNAPSHOT
            after, more = _observe(state, event), _unchanged(state, event)
    return after, found + more + _safety(model, after, event)


def _placement(event: SimEvent) -> list[str]:
    if (event.slot, event.phase) in _PLACES[event.kind]:
        return []
    return [_violation("SlotProgram", event, f"{event.kind} at {event.slot} phase {event.phase}")]


def _status_step(model: Model, state: _Alpha, event: SimEvent) -> list[str]:
    """Check CalcMonotone: VALID moves to INVALID at INVALIDATED or to INCOMPLETE after EndRun."""
    new = event.summary.status
    leaves_valid = state.status is CalculationStatus.VALID
    invalidated = new is CalculationStatus.INVALID and event.kind is SimEventKind.INVALIDATED
    ended = new is CalculationStatus.INCOMPLETE and event.session_date > model.end
    if new is state.status or (leaves_valid and (invalidated or ended)):
        return []
    return [_violation("CalcMonotone", event, f"{state.status} -> {new} at {event.kind}")]


# --- per-event transitions ---------------------------------------------------------------------


def _money(state: _Alpha) -> Decimal:
    """``cash + recv - pay`` of the spec: CASH + ΣRECEIVABLE + ΣPAYABLE."""
    return state.cash + state.receivable - state.payable


def _observe(state: _Alpha, event: SimEvent) -> _Alpha:
    s = event.summary
    return replace(
        state,
        cash=s.cash.amount,
        receivable=s.receivable.amount,
        payable=s.payable.amount,
        reserve=s.reserve.amount,
        held=s.held,
        order=s.order,
        status=s.status,
    )


def _unchanged(
    state: _Alpha, event: SimEvent, *, money: bool = True, position: bool = True, order: bool = True
) -> list[str]:
    """UNCHANGED clauses: the event must leave these α variables as they were."""
    after = _observe(state, event)
    found: list[str] = []
    if money and (after.cash, after.receivable, after.payable) != (
        state.cash,
        state.receivable,
        state.payable,
    ):
        found.append(_violation("Unchanged", event, f"{event.kind} moved cash, recv or pay"))
    if position and (after.held, after.reserve) != (state.held, state.reserve):
        found.append(_violation("Unchanged", event, f"{event.kind} changed pos or reserve"))
    if order and after.order != state.order:
        found.append(_violation("Unchanged", event, f"{event.kind} changed the order"))
    return found


def _end_campaign(state: _Alpha) -> _Alpha:
    return replace(state, rolls=0, roll_open_due=False, basis=_ZERO, realized=_ZERO, cstart=None)


def _on_deposit(model: Model, state: _Alpha, event: SimEvent) -> tuple[_Alpha, list[str]]:
    after = _observe(state, event)
    want = replace(_INITIAL, cash=model.initial_cash)
    if state != _INITIAL or after != want:
        return after, [_violation("Init", event, "the deposit must open the run with Cash0")]
    return after, []


def _on_settle_due(state: _Alpha, event: SimEvent) -> tuple[_Alpha, list[str]]:
    """AdvanceSession: cash' = cash + recv - pay, recv' = pay' = 0."""
    after = _observe(state, event)
    found = _unchanged(state, event, money=False)
    if (after.cash, after.receivable, after.payable) != (_money(state), _ZERO, _ZERO):
        found.append(_violation("AdvanceSession", event, "dues did not settle exactly"))
    return replace(after, fees_due=_ZERO), found


def _on_submitted(model: Model, state: _Alpha, event: SimEvent) -> tuple[_Alpha, list[str]]:
    after = replace(_observe(state, event), submitted_at_ns=event.at_ns)
    found = _unchanged(state, event, order=False)
    detail, order = event.detail, after.order
    if not isinstance(detail, DecisionDetail) or order is None or detail.purpose is None:
        return after, [*found, _violation("Submit", event, "no live order or decision")]
    if state.order is not None or order.purpose is not detail.purpose:
        found.append(_violation("Submit", event, "an order was live, or purposes disagree"))
    found += _submission_rules(model, state, event, order.purpose)
    return after, found


def _submission_rules(
    model: Model, state: _Alpha, event: SimEvent, purpose: OrderPurpose
) -> list[str]:
    """OneCampaign, RollsWithinCaps and CanOpen's ``sess < Sessions`` at submission."""
    found: list[str] = []
    if (purpose in _OPENING) == bool(state.held):
        found.append(_violation("OneCampaign", event, f"{purpose} with pos.held={state.held}"))
    cap = _campaign_cap(model, state, event.session_date)
    if purpose is OrderPurpose.ROLL_CLOSE and (state.rolls >= model.max_rolls or cap):
        found.append(_violation("RollsWithinCaps", event, f"roll close at rolls {state.rolls}"))
    if purpose is OrderPurpose.ROLL_OPEN and cap:
        found.append(_violation("RollsWithinCaps", event, "replacement at the campaign cap"))
    if purpose in _OPENING and event.session_date == model.end:
        found.append(_violation("CanOpen", event, "opening order on the final session"))
    if (purpose is OrderPurpose.ROLL_OPEN) != (purpose in _OPENING and state.roll_open_due):
        found.append(_violation("DecideFlat", event, f"{purpose} with rollOpenDue mismatch"))
    return found


def _on_fill(model: Model, state: _Alpha, event: SimEvent) -> tuple[_Alpha, list[str]]:
    after = _observe(state, event)
    detail = event.detail
    if not isinstance(detail, FillDetail):
        return after, [_violation("Fill", event, "FILLED without a FillDetail")]
    unknown = [leg.contract_id for leg in detail.legs if leg.contract_id not in model.contracts]
    if unknown:
        return after, [_violation("Fill", event, f"unknown contracts {unknown}")]
    found = _fill_order_rules(model, state, event, detail)
    found += _fill_quote_rules(model, state, event, detail)
    found += _fill_position_rules(state, event, detail)
    found += _post_debit(state, after, event, detail.net_debit.amount, detail.fees.amount)
    if after.order is not None:
        found.append(_violation("Fill", event, "the order is still live after its fill"))
    return _fill_campaign(state, after, event, detail), found


def _post_debit(
    state: _Alpha, after: _Alpha, event: SimEvent, debit: Decimal, fees: Decimal
) -> list[str]:
    """PostDebit: cash' = cash, recv' = recv + max(0, -d), pay' = pay + max(0, d) + fees.

    TLA's ``cash' = cash - Fee`` is WP1's fee payable (α's ``cash - pay``); a debit ``d`` is
    FillOpen/FillClose's premium and Settle's ``-v`` (a settlement received is ``-d``).
    """
    posted = (state.cash, state.receivable + max(-debit, _ZERO), state.payable + max(debit, _ZERO))
    if (after.cash, after.receivable, after.payable - fees) == posted:
        return []
    return [_violation("PostDebit", event, f"{event.kind} did not post d to recv or pay")]


def _fill_order_rules(
    model: Model, state: _Alpha, event: SimEvent, detail: FillDetail
) -> list[str]:
    """Check the live order of this fill (FillsOnlyAfterSubmission), D, Fee and PriceOK."""
    order = state.order
    if order is None or (order.order_id, order.purpose) != (detail.order_id, detail.purpose):
        return [_violation("FillsOnlyAfterSubmission", event, "no live order of this fill")]
    found: list[str] = []
    debit = sum(
        (
            leg.contracts * model.contracts[leg.contract_id].multiplier * leg.price.value
            for leg in detail.legs
        ),
        _ZERO,
    )
    contracts = sum(abs(leg.contracts) for leg in detail.legs)
    if debit != detail.net_debit.amount or detail.packages != order.packages:
        found.append(_violation("Fill", event, "D is not Σ q·m·price, or packages differ"))
    if detail.fees.amount != model.fee_per_contract * contracts:
        found.append(_violation("Fee", event, "fees are not fee x Σ|q|"))
    if (detail.limit_usd is None) != (order.purpose is OrderPurpose.FINAL):
        found.append(_violation("PriceOK", event, "only FINAL is market-style"))
    limit = order.limit_usd
    if limit is not None and (detail.limit_usd != limit or debit > limit.amount):
        found.append(_violation("PriceOK", event, f"D {debit} above the limit {limit.amount}"))
    if order.purpose in _OPENING and _q(model) * debit <= 0:
        found.append(_violation("PriceOK", event, "opening premium against the direction"))
    return found


def _fill_quote_rules(
    model: Model, state: _Alpha, event: SimEvent, detail: FillDetail
) -> list[str]:
    """Each leg trades its natural at an observation strictly after submission, <= 120 s old."""
    found: list[str] = []
    submitted = state.submitted_at_ns
    for leg in detail.legs:
        quote = model.quotes.get(leg.quote_id)
        if quote is None or quote.contract_id != leg.contract_id:
            found.append(_violation("Fill", event, f"{leg.quote_id} is not a quote of the leg"))
            continue
        if submitted is None or quote.observed_at_ns <= submitted:
            found.append(_violation("FillsOnlyAfterSubmission", event, f"{leg.quote_id} (C05)"))
        found += _fresh(quote, event, leg.quote_id, "Fill")
        natural = quote.ask if leg.contracts > 0 else quote.bid
        if leg.price.value != natural:
            found.append(_violation("Fill", event, f"{leg.contract_id} off its natural price"))
    return found


def _fill_position_rules(state: _Alpha, event: SimEvent, detail: FillDetail) -> list[str]:
    """FillOpen from flat to the traded legs; FillClose from exactly those legs to flat."""
    traded = tuple(sorted((leg.contract_id, leg.contracts) for leg in detail.legs))
    flat: tuple[tuple[str, int], ...] = ()
    if detail.purpose in _OPENING:
        before, after = flat, traded
    else:
        before, after = tuple((c, -q) for c, q in traded), flat
    if state.held != before or event.summary.held != after:
        return [_violation("FillsOnlyAfterSubmission", event, "pos does not follow the fill")]
    return []


def _fill_campaign(state: _Alpha, after: _Alpha, event: SimEvent, detail: FillDetail) -> _Alpha:
    """FillOpen's and FillClose's campaign updates."""
    debit, fees = detail.net_debit.amount, detail.fees.amount
    after = replace(after, fees_due=state.fees_due + fees, submitted_at_ns=None)
    opened = replace(after, gen=event.campaign_id, opened=event.session_date)
    match detail.purpose:
        case OrderPurpose.ENTRY:
            return replace(
                opened,
                entry_debit=debit + fees,
                basis=abs(debit),
                realized=_ZERO,
                rolls=0,
                cstart=event.session_date,
                roll_open_due=False,
            )
        case OrderPurpose.ROLL_OPEN:
            return replace(
                opened, entry_debit=debit + fees, rolls=state.rolls + 1, roll_open_due=False
            )
        case OrderPurpose.ROLL_CLOSE:
            realized = state.realized - state.entry_debit - debit - fees
            return replace(after, realized=realized, roll_open_due=True)
        case _:
            return _end_campaign(after)


def _on_nonfill(state: _Alpha, event: SimEvent) -> tuple[_Alpha, list[str]]:
    after = _observe(state, event)
    found = _unchanged(state, event)
    detail, order = event.detail, state.order
    if not isinstance(detail, NonfillDetail) or order is None or order.order_id != detail.order_id:
        return after, [*found, _violation("TryFill", event, "nonfill without its live order")]
    if detail.reason is NonfillReason.INSUFFICIENT_CAPITAL:
        return replace(after, refused=state.refused | {order.purpose}), found
    return after, found


def _on_cancelled(state: _Alpha, event: SimEvent) -> tuple[_Alpha, list[str]]:
    """NonFill at F3: order' = NoOrder; a cancelled replacement ends the campaign."""
    after = replace(_observe(state, event), submitted_at_ns=None)
    found = _unchanged(state, event, order=False)
    if state.order is None or after.order is not None:
        return after, [*found, _violation("NonFill", event, "cancel without a live order")]
    if state.order.purpose is OrderPurpose.ROLL_OPEN:
        return _end_campaign(after), found
    return after, found


def _on_settled(model: Model, state: _Alpha, event: SimEvent) -> tuple[_Alpha, list[str]]:
    """Settle: the expiring package posts q x intrinsic once and leaves pos."""
    after = _observe(state, event)
    detail = event.detail
    if not isinstance(detail, SettlementDetail):
        return after, [_violation("Settle", event, "SETTLED without a SettlementDetail")]
    held = dict(state.held)
    found: list[str] = []
    if event.campaign_id in state.settled:
        found.append(_violation("SettledOnce", event, f"{event.campaign_id} settled again"))
    ids = detail.contract_ids
    if any(c not in held or model.contracts[c].expiry != event.session_date for c in ids):
        return after, [*found, _violation("Settle", event, "contracts not held or not expiring")]
    net = sum(
        (
            held[c] * model.contracts[c].units * _intrinsic(model.contracts[c], detail.value.value)
            for c in ids
        ),
        _ZERO,
    )
    if net != detail.net_cash.amount:
        found.append(_violation("Settle", event, f"net cash {detail.net_cash.amount}, want {net}"))
    found += _post_debit(state, after, event, -detail.net_cash.amount, detail.fees.amount)
    if after.held != tuple((c, q) for c, q in state.held if c not in ids):
        found.append(_violation("Settle", event, "pos does not lose the settled contracts"))
    settled = state.settled | {event.campaign_id or ""}
    after = replace(after, settled=settled, fees_due=state.fees_due + detail.fees.amount)
    return _end_campaign(after), found


def _on_invalidated(state: _Alpha, event: SimEvent) -> tuple[_Alpha, list[str]]:
    after = _observe(state, event)
    found = _unchanged(state, event)
    if after.status is not CalculationStatus.INVALID:
        found.append(_violation("CalcMonotone", event, "INVALIDATED without an invalid status"))
    return after, found


# --- Safety on every state ---------------------------------------------------------------------


def _safety(model: Model, state: _Alpha, event: SimEvent) -> list[str]:
    found: list[str] = []
    in_window = event.session_date <= model.end
    valid = state.status is CalculationStatus.VALID
    if state.cash - state.payable < state.reserve or state.reserve < 0:
        found.append(_violation("FullyFunded", event, f"cash - pay < reserve {state.reserve}"))
    if state.reserve != _held_reserve(model, state.held):
        found.append(_violation("ReserveMatchesPosition", event, f"reserve {state.reserve}"))
    if state.held and state.basis <= 0:
        found.append(_violation("BasisPositive", event, f"basis {state.basis}"))
    if state.order is not None and (state.order.purpose in _OPENING) == bool(state.held):
        found.append(_violation("OneCampaign", event, "order and position disagree"))
    if state.order is not None and event.slot not in _ORDER_SLOTS:
        found.append(_violation("OrdersExpireInSession", event, "order live after F3"))
    if state.held and valid and in_window and _expiry(model, state) < event.session_date:
        found.append(_violation("NoPositionPastExpiry", event, "held past its expiry cutoff"))
    if state.held and state.gen in state.settled:
        found.append(_violation("SettledOnce", event, f"{state.gen} held again after settling"))
    if not 0 <= state.rolls <= model.max_rolls:
        found.append(_violation("TypeOK", event, f"rolls {state.rolls}"))
    cut = event.kind is SimEventKind.SNAPSHOT and valid and in_window
    if cut and state.held and _expiry(model, state) <= event.session_date:
        found.append(_violation("Settle", event, "an expiring position survived its CUT"))
    return found


# --- witnesses of quote.ok: marks, fills, price-level nonfills --------------------------------


def _witness(
    model: Model, before: _Alpha, after: _Alpha, event: SimEvent
) -> tuple[list[str], set[str]]:
    """NoNegativeEquity, FillNeverRaisesNLV and CanaryCloseAboveWidth where quote.ok holds."""
    if event.kind is SimEventKind.MARKED:
        return _marked_witness(model, after, event)
    if event.kind is SimEventKind.FILLED and isinstance(event.detail, FillDetail):
        return _fill_witness(model, before, after, event, event.detail)
    detail = event.detail
    if event.kind is not SimEventKind.NOT_FILLED or not isinstance(detail, NonfillDetail):
        return [], set()
    if detail.reason in _QUOTE_LEVEL or detail.net_debit is None or detail.purpose in _OPENING:
        return [], set()
    return [], _close_above_width(model, before.held, detail.net_debit.amount)


def _marked_witness(model: Model, state: _Alpha, event: SimEvent) -> tuple[list[str], set[str]]:
    quotes = _quotes_of(model, event.input_refs)
    mark = _mark(model, state.held, quotes)
    close = _close_debit(model, state.held, quotes)
    if not state.held or mark is None or close is None:
        return [_violation("MarkClose", event, "MARKED without a quote for every held leg")], set()
    found = _mark_quote_rules(model, state, event) + _no_negative_equity(model, state, mark, event)
    return found, _close_above_width(model, state.held, close)


def _mark_quote_rules(model: Model, state: _Alpha, event: SimEvent) -> list[str]:
    """Each mark is a fresh quote of a held leg, usable for what it witnesses.

    At CLOSE a mark needs a mid: VALID, LOCKED or NO_BID. At DEC it witnesses ``usable_quote``
    for the leg's closing side, so a long leg's (a sell) also needs a positive bid.
    """
    found: list[str] = []
    held = dict(state.held)
    for ref in event.input_refs:
        quote = model.quotes.get(ref)
        if quote is None or quote.contract_id not in held:
            found.append(_violation("MarkClose", event, f"{ref} is not a quote of a held leg"))
            continue
        found += _fresh(quote, event, ref, "MarkClose")
        sells = event.slot is Slot.DEC and held[quote.contract_id] > 0
        if not _usable(quote, sells=sells):
            found.append(_violation("MarkClose", event, f"{ref} is not usable for its side"))
    return found


def _usable(quote: QuoteFacts, *, sells: bool) -> bool:
    """Return VALID, LOCKED or NO_BID (0 <= bid <= ask, ask > 0); a sell also needs bid > 0."""
    priced = _ZERO <= quote.bid <= quote.ask and quote.ask > 0
    return priced and (quote.bid > 0 or not sells)


def _fresh(quote: QuoteFacts, event: SimEvent, quote_id: str, prop: str) -> list[str]:
    """Check that the quote is of the event's session, visible by it and at most 120 s old."""
    visible = max(quote.observed_at_ns, quote.available_at_ns) <= event.at_ns
    young = event.at_ns - quote.observed_at_ns <= _QUOTE_MAX_AGE_NS
    if quote.session_date == event.session_date and visible and young:
        return []
    return [_violation(prop, event, f"{quote_id} is not a fresh visible quote of the session")]


def _fill_witness(
    model: Model, before: _Alpha, after: _Alpha, event: SimEvent, detail: FillDetail
) -> tuple[list[str], set[str]]:
    """FillNeverRaisesNLV: NLV at the fill observation's mids does not rise across the fill."""
    quotes = _quotes_of(model, tuple(leg.quote_id for leg in detail.legs))
    mark_before = _mark(model, before.held, quotes)
    mark_after = _mark(model, after.held, quotes)
    if mark_before is None or mark_after is None:
        return [_violation("FillNeverRaisesNLV", event, "no mid for a traded leg")], set()
    found = _no_negative_equity(model, after, mark_after, event) if after.held else []
    if _money(after) + mark_after > _money(before) + mark_before:
        found.append(_violation("FillNeverRaisesNLV", event, "NLV rose at the fill's mids"))
    if detail.purpose in _OPENING:
        return found, set()
    return found, _close_above_width(model, before.held, detail.net_debit.amount)


def _no_negative_equity(model: Model, state: _Alpha, mark: Decimal, event: SimEvent) -> list[str]:
    """NoNegativeEquity, under the spec's premise that the package mid lies in its payoff range."""
    low, high = _payoff_bounds(model, state.held)
    if mark < low or (high is not None and mark > high):
        return []  # a MARK_OUT_OF_RANGE mark: outside the model's Quotes
    if _money(state) + mark < 0:
        return [_violation("NoNegativeEquity", event, f"NLV {_money(state) + mark}")]
    return []


def _close_above_width(model: Model, held: tuple[tuple[str, int], ...], close: Decimal) -> set[str]:
    if model.kind is PremiumDirection.CREDIT and held and close > _max_loss(model, held):
        return {"CanaryCloseAboveWidth"}
    return set()


def _quotes_of(model: Model, refs: Sequence[str]) -> dict[str, QuoteFacts]:
    return {model.quotes[r].contract_id: model.quotes[r] for r in refs if r in model.quotes}


def _mark(
    model: Model, held: tuple[tuple[str, int], ...], quotes: Mapping[str, QuoteFacts]
) -> Decimal | None:
    """Σ q·m·mid over ``held``; None when a held contract has no quote."""
    if any(c not in quotes for c, _ in held):
        return None
    return sum(
        (q * model.contracts[c].multiplier * (quotes[c].bid + quotes[c].ask) / 2 for c, q in held),
        _ZERO,
    )


def _close_debit(
    model: Model, held: tuple[tuple[str, int], ...], quotes: Mapping[str, QuoteFacts]
) -> Decimal | None:
    """CloseDebit: Σ (-q)·m·natural, buying back at the ask and selling at the bid."""
    if any(c not in quotes for c, _ in held):
        return None
    return sum(
        (
            -q * model.contracts[c].multiplier * (quotes[c].ask if q < 0 else quotes[c].bid)
            for c, q in held
        ),
        _ZERO,
    )


# --- payoff, reserve and fee -------------------------------------------------------------------


def _q(model: Model) -> int:
    return -1 if model.kind is PremiumDirection.CREDIT else 1


def _intrinsic(leg: LegFacts, level: Decimal) -> Decimal:
    if leg.right == "put":
        return max(leg.strike - level, _ZERO)
    return max(level - leg.strike, _ZERO)


def _payoff_bounds(
    model: Model, held: tuple[tuple[str, int], ...]
) -> tuple[Decimal, Decimal | None]:
    """Min and max of Σ q·units·intrinsic over S >= 0; max None when net long calls.

    Raises:
        ValueError: For net short calls (unbounded loss: not an R1 structure).

    """
    legs = [(model.contracts[c], q) for c, q in held]
    slope = sum((q * leg.units for leg, q in legs if leg.right == "call"), _ZERO)
    if slope < 0:
        raise ValueError(f"unbounded loss: net short calls in {held}")
    values = [
        sum((q * leg.units * _intrinsic(leg, level) for leg, q in legs), _ZERO)
        for level in {_ZERO, *(leg.strike for leg, _ in legs)}
    ]
    return min(values, default=_ZERO), None if slope > 0 else max(values, default=_ZERO)


def _max_loss(model: Model, held: tuple[tuple[str, int], ...]) -> Decimal:
    """Return the settlement bound ``W·m·n``: max(0, -min payoff)."""
    low, _ = _payoff_bounds(model, held)
    return max(-low, _ZERO)


def _fee(model: Model, held: tuple[tuple[str, int], ...]) -> Decimal:
    """``Fee``: the fees of one fill of the held package, fee x Σ|q|."""
    return model.fee_per_contract * sum(abs(q) for _, q in held)


def _held_reserve(model: Model, held: tuple[tuple[str, int], ...]) -> Decimal:
    return _max_loss(model, held) + _fee(model, held) if held else _ZERO


def _expiry(model: Model, state: _Alpha) -> date:
    return min(model.contracts[c].expiry for c, _ in state.held)


def _sessions_between(model: Model, first: date, last: date) -> int:
    """Table sessions from ``first`` to ``last`` inclusive (``first`` counts as 1)."""
    return model.sessions.index(last) - model.sessions.index(first) + 1


def _campaign_cap(model: Model, state: _Alpha, session: date) -> bool:
    """Return CampaignCap: MaxRolls > 0 and sess - cstart + 1 >= MaxCampaign."""
    if model.max_rolls == 0 or model.max_campaign is None or state.cstart is None:
        return False
    return _sessions_between(model, state.cstart, session) >= model.max_campaign


# --- DEC 5: DecideHeld and DecideFlat -----------------------------------------------------------


def _triggers(model: Model, state: _Alpha, session: date, marked: SimEvent | None) -> _Triggers:
    quotes = {} if marked is None else _quotes_of(model, marked.input_refs)
    close = None if marked is None else _close_debit(model, state.held, quotes)
    dte = (_expiry(model, state) - session).days
    held_sessions = _sessions_between(model, state.opened or session, session)
    pnl = None
    if close is not None:
        pnl = state.realized - state.entry_debit - close - _fee(model, state.held)
    tp, sl = model.take_profit, model.stop_loss
    rolling = model.max_rolls > 0 and model.roll_dte is not None and dte <= model.roll_dte
    return _Triggers(
        quote_ok=close is not None,
        close_debit=close,
        pnl=pnl,
        time_exit=dte <= model.exit_dte or held_sessions >= model.max_hold,
        take_profit=pnl is not None and tp is not None and pnl >= tp * state.basis,
        stop_loss=pnl is not None and sl is not None and pnl <= -sl * state.basis,
        campaign_cap=_campaign_cap(model, state, session),
        roll_trigger=rolling,
        roll_cap=rolling and state.rolls >= model.max_rolls,
        roll_due=rolling and state.rolls < model.max_rolls,
    )


def _first_trigger(t: _Triggers) -> ExitTrigger | None:
    """Return the first true of TIME_EXIT, TAKE_PROFIT, STOP_LOSS, CAMPAIGN_CAP, ROLL_CAP."""
    ordered = (
        (t.time_exit, ExitTrigger.TIME_EXIT),
        (t.take_profit, ExitTrigger.TAKE_PROFIT),
        (t.stop_loss, ExitTrigger.STOP_LOSS),
        (t.campaign_cap, ExitTrigger.CAMPAIGN_CAP),
        (t.roll_cap, ExitTrigger.ROLL_CAP),
    )
    return next((trigger for holds, trigger in ordered if holds), None)


def _expected_held(
    model: Model, t: _Triggers, final: bool
) -> tuple[SimEventKind | None, OrderPurpose | None, ExitTrigger | None]:
    """DecideHeld: (event kind, purpose, trigger) DEC 5 must emit; kind None emits nothing."""
    first = _first_trigger(t)
    if final and model.liquidate_at_final:
        return SimEventKind.ORDER_SUBMITTED, OrderPurpose.FINAL, None
    if first is not None:
        kind = SimEventKind.ORDER_SUBMITTED if t.quote_ok else SimEventKind.EXIT_DEFERRED
        return kind, OrderPurpose.EXIT, first
    if t.roll_due:
        kind = SimEventKind.ORDER_SUBMITTED if t.quote_ok else SimEventKind.EXIT_DEFERRED
        return kind, OrderPurpose.ROLL_CLOSE, None
    return None, None, None


def _check_decision(
    model: Model, state: _Alpha, session: date, marked: SimEvent | None, decision: SimEvent | None
) -> tuple[list[str], set[str]]:
    if not state.held:
        return _check_flat_decision(model, state, session, decision), set()
    t = _triggers(model, state, session, marked)
    found = [] if marked is None else _stored_liquidation(model, state, t, marked)
    want = _expected_held(model, t, session == model.end)
    got = _decision_of(decision)
    if want[1] is OrderPurpose.FINAL:
        got = (got[0], got[1], None)  # FINAL's trigger label is not DecideHeld's
    if got != want:
        return [*found, f"DecideHeld at {session}: want {want}, got {got}"], set()
    found += [] if decision is None else _closing_limit(model, state, t, decision)
    if want[:2] != (SimEventKind.ORDER_SUBMITTED, OrderPurpose.EXIT):
        return found, set()
    return found, _exit_canaries(t)


def _stored_liquidation(model: Model, state: _Alpha, t: _Triggers, marked: SimEvent) -> list[str]:
    """Check that the DEC mark stores LiquidationPnL by component (design §10.4) as α has it."""
    fee = _fee(model, state.held)
    want = (state.realized, state.entry_debit, t.close_debit, fee, state.basis, t.pnl)
    detail = marked.detail
    got = None
    if isinstance(detail, LiquidationDetail):
        parts = (
            detail.realized_prior,
            detail.entry_debit_incl_fees,
            detail.close_debit,
            detail.exit_fees,
            detail.basis,
            detail.liquidation_pnl,
        )
        got = tuple(part.amount for part in parts)
    if got == want:
        return []
    return [_violation("LiquidationPnL", marked, f"stored {got}, want {want}")]


def _decision_of(
    decision: SimEvent | None,
) -> tuple[SimEventKind | None, OrderPurpose | None, ExitTrigger | None]:
    if decision is None:
        return None, None, None
    detail = decision.detail
    deferred = decision.kind is SimEventKind.EXIT_DEFERRED
    if not isinstance(detail, DecisionDetail) or (
        deferred and detail.reason is not DecisionReason.DECISION_QUOTE_INVALID
    ):
        return decision.kind, None, None
    return decision.kind, detail.purpose, detail.trigger


def _closing_limit(model: Model, state: _Alpha, t: _Triggers, decision: SimEvent) -> list[str]:
    """Submit: a closing limit is CloseDebit at DEC plus the allowance; FINAL has none."""
    order = decision.summary.order
    if decision.kind is not SimEventKind.ORDER_SUBMITTED or order is None:
        return []
    packages = abs(state.held[0][1])
    limit = None if order.purpose is OrderPurpose.FINAL else t.close_debit
    want = None if limit is None else limit + model.price_allowance
    got = None if order.limit_usd is None else order.limit_usd.amount
    if (got, order.packages) != (want, packages):
        return [_violation("Submit", decision, f"limit {got} x{order.packages}, want {want}")]
    return []


def _exit_canaries(t: _Triggers) -> set[str]:
    """Canaries on ExitSubmitted (slot Dec, the step done, a live exit order)."""
    reached: set[str] = set()
    if t.take_profit:
        reached.add("CanaryTakeProfitExit")
    if t.stop_loss:
        reached.add("CanaryStopLossExit")
    if t.campaign_cap and not t.time_exit:
        reached.add("CanaryCampaignCapExit")
    if t.roll_cap and not t.time_exit:
        reached.add("CanaryRollCapExit")
    return reached


def _check_flat_decision(
    model: Model, state: _Alpha, session: date, decision: SimEvent | None
) -> list[str]:
    """DecideFlat: a due replacement opens (not at the cap, not on the final session) or ends."""
    kind = None if decision is None else decision.kind
    if kind is SimEventKind.EXIT_DEFERRED:
        return [f"DecideFlat at {session}: EXIT_DEFERRED while flat"]
    if not state.roll_open_due:
        if kind is SimEventKind.CAMPAIGN_ENDED:
            return [f"DecideFlat at {session}: CAMPAIGN_ENDED with no replacement due"]
        return []
    blocked = _campaign_cap(model, state, session) or session == model.end
    ended = kind is SimEventKind.CAMPAIGN_ENDED
    opened = kind is SimEventKind.ORDER_SUBMITTED
    if not (ended or opened) or (blocked and not ended):
        return [f"DecideFlat at {session}: a due replacement must open or end the campaign"]
    return []


# --- canaries ----------------------------------------------------------------------------------


def _state_canaries(model: Model, state: _Alpha) -> set[str]:
    reached: set[str] = set()
    credit = model.kind is PremiumDirection.CREDIT
    if credit and state.held and state.basis < _fee(model, state.held):
        reached.add("CanaryFeeExceedsCredit")
    if model.max_rolls > 0 and state.rolls == model.max_rolls and state.held:
        reached.add("CanaryRolled")
    if state.refused:
        reached.add("CanaryFundingRefusal")
    closing_refused = credit and bool(state.refused & _CLOSING)
    if closing_refused:
        reached.add("CanaryCreditCloseRefused")
    if closing_refused and state.settled:
        reached.add("CanaryRefusedThenSettled")
    if credit and state.settled and state.payable - state.fees_due > 0:
        reached.add("CanarySettledCreditLoss")
    return reached


def _end_canaries(trace: Trace) -> set[str]:
    reached: set[str] = set()
    if trace.headline:
        reached.add("CanaryValidClosedRun")
    if trace.status is CalculationStatus.INCOMPLETE:
        reached.add("CanaryIncomplete")
    if trace.status is CalculationStatus.INVALID:
        reached.add("CanaryMissingValuation")
    return reached


# --- whole-trace structure -----------------------------------------------------------------------


def _check_keys(events: Sequence[SimEvent]) -> list[str]:
    """Keys (at_ns, phase, seq) strictly increase; ids and seq follow ADR 0002 §17 item 6."""
    if not events or events[0].kind is not SimEventKind.DEPOSIT:
        return ["Init: the run must start with its DEPOSIT event"]
    found: list[str] = []
    counts: dict[tuple[date, Slot, Phase], int] = {}
    for previous, event in zip((None, *events), events, strict=False):
        place = (event.session_date, event.slot, event.phase)
        counts[place] = counts.get(place, 0) + 1
        if f"{event.session_date}:{event.slot}:{int(event.phase)}:{event.seq}" != event.event_id:
            found.append(_violation("EventId", event, "id is not {date}:{slot}:{phase}:{seq}"))
        if _EVENT_ID.fullmatch(event.event_id) is None or event.seq != counts[place]:
            found.append(_violation("EventId", event, "seq does not count from 1 in its phase"))
        if previous is not None and _key(previous) >= _key(event):
            found.append(_violation("EventKey", event, "(at_ns, phase, seq) did not increase"))
    return found + _after_invalidated(events)


def _key(event: SimEvent) -> tuple[int, int, int]:
    return event.at_ns, int(event.phase), event.seq


def _check_instants(model: Model, events: Sequence[SimEvent]) -> list[str]:
    """Each event sits at its session's slot instant: OPEN, DEC..F3 before the close, CUT."""
    found: list[str] = []
    for event in events:
        want = _slot_instant(model, event.session_date, event.slot)
        if want != event.at_ns:
            found.append(_violation("SlotClock", event, f"at {event.at_ns}, want {want}"))
    return found


def _slot_instant(model: Model, session: date, slot: Slot) -> int | None:
    """Return the slot's instant from the session's open, close and cutoff; None off the table."""
    times = model.clock.get(session)
    if times is None:
        return None
    open_ns, close_ns, cutoff_ns = times
    if slot is Slot.OPEN:
        return open_ns
    if slot is Slot.CUT:
        return cutoff_ns
    return close_ns - _BEFORE_CLOSE_MIN.get(slot, 0) * _MINUTE_NS


def _after_invalidated(events: Sequence[SimEvent]) -> list[str]:
    kinds = [event.kind for event in events]
    if SimEventKind.INVALIDATED in kinds[:-1]:
        return ["RunTerminates: events follow an INVALIDATED event"]
    return []


def _check_sessions(model: Model, trace: Trace) -> list[str]:
    """Check the sessions: the window's in order, then at most 5 settle-only ones."""
    seen = list(dict.fromkeys(event.session_date for event in trace.events))
    window = [d for d in model.sessions if model.start <= d <= model.end]
    after = [d for d in model.sessions if d > model.end][:_SETTLE_ONLY_SESSIONS]
    inside = [d for d in seen if d <= model.end]
    beyond = [d for d in seen if d > model.end]
    stopped = any(event.kind is SimEventKind.INVALIDATED for event in trace.events)
    found: list[str] = []
    if inside != window[: len(inside)] or (not stopped and len(inside) != len(window)):
        found.append(f"AdvanceSession: sessions {inside} are not the window {window}")
    if beyond != after[: len(beyond)]:
        found.append(f"RunTerminates: settle-only sessions {beyond}, table {after}")
    extra = [
        event.event_id
        for event in trace.events
        if event.session_date > model.end
        and event.kind not in (SimEventKind.SETTLE_DUE, SimEventKind.SNAPSHOT)
    ]
    if extra:
        found.append(f"RunTerminates: settle-only sessions do more than settle: {extra}")
    return found


def _check_curve(model: Model, trace: Trace) -> list[str]:
    """One account point per SNAPSHOT, equal to its summary, with the NLVs of ``_nlvs``."""
    snapshots = [event for event in trace.events if event.kind is SimEventKind.SNAPSHOT]
    dates = [point.session_date for point in trace.account_curve]
    if [event.session_date for event in snapshots] != dates:
        return [f"Curve: points {dates} do not match the SNAPSHOT sessions"]
    marks = {
        e.session_date: e
        for e in trace.events
        if e.kind is SimEventKind.MARKED and e.slot is Slot.CLOSE
    }
    found: list[str] = []
    for event, point in zip(snapshots, trace.account_curve, strict=True):
        found += _point_mismatch(event, point)
        want = _nlvs(model, event, marks.get(event.session_date))
        got = tuple(
            None if nlv is None else nlv.amount for nlv in (point.mid_nlv, point.natural_nlv)
        )
        if got != want:
            found.append(_violation("Curve", event, f"NLVs (mid, natural) {got}, want {want}"))
    return found


def _nlvs(
    model: Model, event: SimEvent, close_mark: SimEvent | None
) -> tuple[Decimal | None, Decimal | None]:
    """Return the account point's (mid, natural) NLV (ADR 0002 §17 items 31, 32).

    Flat: ``cash + recv - pay``. Held: that plus Σ q·m·mid and Σ q·m·natural (bid long, ask
    short) at the session's CLOSE marks. Held on a settle-only session: None.
    """
    s = event.summary
    money = s.cash.amount + s.receivable.amount - s.payable.amount
    if not s.held:
        return money, money
    if event.session_date > model.end or close_mark is None:
        return None, None
    quotes = _quotes_of(model, close_mark.input_refs)
    mid, close = _mark(model, s.held, quotes), _close_debit(model, s.held, quotes)
    return (None if mid is None else money + mid), (None if close is None else money - close)


def _point_mismatch(event: SimEvent, point: AccountPoint) -> list[str]:
    s = event.summary
    want = (s.cash, s.receivable, s.payable, s.reserve, event.at_ns)
    got = (
        point.cash,
        point.receivable,
        point.payable,
        point.encumbrance,
        point.ledger_cutoff_at_ns,
    )
    found: list[str] = []
    if got != want or point.headroom.amount != s.cash.amount - s.payable.amount - s.reserve.amount:
        found.append(_violation("Curve", event, "account point differs from the summary"))
    return found


def _fill_obligation(events: Sequence[SimEvent]) -> list[str]:
    """TryFill: a submitted order is tried at F1, F2, F3 until it fills; an F3 nonfill cancels."""
    submitted = [e for e in events if e.kind is SimEventKind.ORDER_SUBMITTED]
    attempts = [e for e in events if e.kind in (SimEventKind.FILLED, SimEventKind.NOT_FILLED)]
    cancelled = [e for e in events if e.kind is SimEventKind.ORDER_CANCELLED]
    if not submitted:
        return [] if not attempts and not cancelled else ["TryFill: attempts without an order"]
    session = submitted[0].session_date
    slots = tuple(e.slot for e in attempts)
    if not slots or slots != _FILL_SLOTS[: len(slots)]:
        return [f"TryFill at {session}: attempts at {slots}, want F1, F2, F3 in order"]
    if any(e.kind is SimEventKind.FILLED for e in attempts[:-1]):
        return [f"TryFill at {session}: an attempt after the fill"]
    filled = attempts[-1].kind is SimEventKind.FILLED
    if len(cancelled) != (0 if filled else 1) or (not filled and slots != _FILL_SLOTS):
        return [f"TryFill at {session}: an F3 nonfill, and only it, cancels the order"]
    return []


def _check_end(model: Model, state: _Alpha, trace: Trace) -> list[str]:
    """EndRun, HeadlineOnlyIfValid, OpenAtEndIsNotValid and termination."""
    found: list[str] = []
    last = trace.events[-1] if trace.events else None
    if last is None or last.kind not in (SimEventKind.SNAPSHOT, SimEventKind.INVALIDATED):
        found.append("RunTerminates: the trace does not end at a CUT or an invalidation")
    held = bool(state.held)
    want = state.status
    if want is CalculationStatus.VALID and held and model.liquidate_at_final:
        want = CalculationStatus.INCOMPLETE
    if trace.status is not want:
        found.append(f"EndRun: status {trace.status}, want {want}")
    if trace.headline and (trace.status is not CalculationStatus.VALID or held):
        found.append("HeadlineOnlyIfValid: headline on an invalid or open run")
    if not trace.headline and trace.status is CalculationStatus.VALID and not held:
        found.append("EndRun: a valid flat run is headline-eligible")
    if model.liquidate_at_final and held and trace.status is CalculationStatus.VALID:
        found.append("OpenAtEndIsNotValid: held at the end of a valid run")
    if state.status is not CalculationStatus.INVALID and (state.receivable or state.payable):
        found.append("RunTerminates: dues outstanding after the settle-only sessions")
    return found
