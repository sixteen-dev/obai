"""The R1Campaign trace checker (ADR 0002 §7, §17 item 41).

(a) Self-tests on hand-built traces: three valid runs pass (closed by a time exit, cash-settled
after a deferred exit, invalidated at a missing CLOSE mark) and each seeded violation is caught:
an unfunded fill, a fill before (or at) its submission, a position past its expiry, a generation
settled twice, a headline on an invalid run, a roll over its cap, an event off its slot instant,
a stale or unusable mark, a fill or settlement posted to the wrong one of cash, recv and pay,
dues not settled at the next OPEN, a held session without its CLOSE mark, held NLVs off their
CLOSE marks and a DEC mark without α's liquidation P&L components. The traces are TLA-scale:
multiplier 1, the 102/100 put vertical (W = 2) of ``R1Campaign.pass-refusal.cfg``, $0.50 per
contract side (``Fee`` = 1 per package fill), so ``HeldReserve`` = 2 + 1 = 3 and initial cash 4
sits exactly at the entry funding boundary.

(c) Hypothesis (``derandomize=True``, no database, bounded examples, no shrinking) feeds TLA-scale
random markets through the engine and the checker: multiplier 1, W in {2, 3}, cash at the
funding boundary (slack 0 or 1) or above it, one package quote per (session, slot) from
``Quotes`` held between change points, rare ``Missing`` quotes and settlements, daily expiries,
credit (put) and debit (call) verticals, rolls on and off, P&L rules on and off. Scripted cases,
also Hypothesis examples, reach every canary that multiplier 1 can reach. Only in-model runs
are drawn; outside the model (declared by ADR 0002 §7) are n > 1, 1- and 4-leg structures and
``mark_open_positions``.

(b), the golden suite through the checker, lives in ``tests/e2e/test_suite_properties.py``:
``tests/e2e`` is a package, so only there can the e2e runner be imported.
"""

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

import pytest
from hypothesis import Phase as HypothesisPhase
from hypothesis import event, example, given, settings
from hypothesis import strategies as st
from r1_trace import CANARIES, LegFacts, Model, QuoteFacts, Report, Trace, check, model_of, trace_of

from options_backtest.engine.clock import Phase
from options_backtest.engine.orders import ExitTrigger, OrderPurpose
from options_backtest.engine.simulator import run
from options_backtest.errors import ErrorCode, Issue
from options_backtest.ingest import load_strategy
from options_backtest.models.artifacts import (
    AccountPoint,
    ArtifactBundle,
    DecisionDetail,
    DecisionReason,
    EventSummary,
    FillDetail,
    FilledLeg,
    LiquidationDetail,
    OrderSnapshot,
    SettlementDetail,
    SimEvent,
    SimEventKind,
)
from options_backtest.models.result import CalculationStatus
from options_backtest.models.run import resolve
from options_backtest.models.strategy_checks import PremiumDirection
from options_backtest.money import Price, Usd
from options_backtest.reference.calendars import Slot
from options_backtest.synthetic.market import (
    MarketSpec,
    Override,
    QuoteDrop,
    QuotePin,
    SettlementDrop,
    SettlementPin,
    generate,
)

TLA_DIR: Final = Path(__file__).resolve().parents[4] / "docs/design/options-backtesting-v3/tla"
CANARY_LINE: Final = "\\* canaries:"
"""The TLC configs' canary lines (``run_tlc.sh`` reads the same prefix)."""
D1, D2, D3 = date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)
SESSIONS = (D1, D2, D3, date(2024, 3, 7), date(2024, 3, 8))
LATE_EXPIRY = date(2024, 3, 8)
G1 = "c1.g1"
VALID = CalculationStatus.VALID
_SLOT_SECONDS: Final = {
    Slot.OPEN: 34_200,
    Slot.DEC: 56_700,
    Slot.F1: 56_760,
    Slot.F2: 56_820,
    Slot.F3: 56_880,
    Slot.CLOSE: 57_600,
    Slot.CUT: 86_399,
}
"""Seconds after midnight: 09:30, 15:45, 15:46-15:48, 16:00, 23:59:59."""


# --- (a) hand-built traces -----------------------------------------------------------------


def _usd(text: str) -> Usd:
    return Usd(Decimal(text))


def _at(day: date, slot: Slot) -> int:
    return (day.toordinal() * 86_400 + _SLOT_SECONDS[slot]) * 10**9


def _quote_id(contract: str, day: date, slot: Slot) -> str:
    return f"q:{contract}:{day}:{slot}"


def _legs(expiry: date) -> tuple[str, str]:
    """Return the short 102 put and the long 100 put expiring on ``expiry``."""
    return f"X:{expiry}:P:102", f"X:{expiry}:P:100"


def _quotes(expiry: date) -> dict[str, QuoteFacts]:
    short, long = _legs(expiry)
    rows = (
        (short, D1, (Slot.DEC, Slot.F1, Slot.CLOSE), "1.50", "1.60"),
        (long, D1, (Slot.DEC, Slot.F1, Slot.CLOSE), "0.40", "0.50"),
        (short, D2, (Slot.DEC, Slot.F1), "0.90", "1.00"),
        (long, D2, (Slot.DEC, Slot.F1), "0.20", "0.30"),
    )
    return {
        _quote_id(contract, day, slot): QuoteFacts(
            contract, Decimal(bid), Decimal(ask), _at(day, slot), _at(day, slot), day
        )
        for contract, day, slots, bid, ask in rows
        for slot in slots
    }


def _model(expiry: date, cash: str = "4.00", max_rolls: int = 0) -> Model:
    short, long = _legs(expiry)
    one = Decimal(1)
    return Model(
        kind=PremiumDirection.CREDIT,
        sessions=SESSIONS,
        start=D1,
        end=D3,
        initial_cash=Decimal(cash),
        exit_dte=0,
        max_hold=2,
        take_profit=None,
        stop_loss=None,
        roll_dte=None,
        max_rolls=max_rolls,
        max_campaign=None,
        fee_per_contract=Decimal("0.50"),
        price_allowance=Decimal(0),
        liquidate_at_final=True,
        contracts={
            short: LegFacts("put", Decimal(102), one, one, expiry),
            long: LegFacts("put", Decimal(100), one, one, expiry),
        },
        quotes=_quotes(expiry),
        clock={
            day: (_at(day, Slot.OPEN), _at(day, Slot.CLOSE), _at(day, Slot.CUT)) for day in SESSIONS
        },
    )


def _summary(  # noqa: PLR0913 — one keyword per EventSummary field
    cash: Decimal,
    *,
    receivable: str = "0",
    payable: str = "0",
    reserve: str = "0",
    held: tuple[tuple[str, int], ...] = (),
    order: OrderSnapshot | None = None,
    status: CalculationStatus = VALID,
) -> EventSummary:
    return EventSummary(
        cash=Usd(cash),
        receivable=_usd(receivable),
        payable=_usd(payable),
        reserve=_usd(reserve),
        held=held,
        order=order,
        status=status,
    )


@dataclass(frozen=True, slots=True)
class _At:
    """Where an event sits: session, slot, phase and seq."""

    day: date
    slot: Slot
    phase: Phase
    seq: int = 1


def _event(
    at: _At,
    kind: SimEventKind,
    summary: EventSummary,
    *,
    refs: tuple[str, ...] = (),
    detail: object = None,
) -> SimEvent:
    return SimEvent(
        event_id=f"{at.day}:{at.slot}:{int(at.phase)}:{at.seq}",
        at_ns=_at(at.day, at.slot),
        session_date=at.day,
        slot=at.slot,
        phase=at.phase,
        seq=at.seq,
        kind=kind,
        campaign_id=G1 if kind in _CAMPAIGN_KINDS else None,
        input_refs=refs,
        summary=summary,
        detail=detail,  # type: ignore[arg-type]
    )


_CAMPAIGN_KINDS: Final = frozenset(
    {
        SimEventKind.ORDER_SUBMITTED,
        SimEventKind.FILLED,
        SimEventKind.EXIT_DEFERRED,
        SimEventKind.SETTLED,
        SimEventKind.MARKED,
    }
)


def _entry_day(cash: Decimal, expiry: date) -> list[SimEvent]:
    """D1: deposit, sell the vertical for a credit of 1 at F1, mark it at CLOSE, snapshot.

    D = -1.50 + 0.50 = -1.00; fees = 2 sides x 0.50 = 1.00; reserve = W + Fee = 2 + 1 = 3;
    headroom = cash - 1 - 3 = cash - 4.
    """
    short, long = _legs(expiry)
    held = ((long, 1), (short, -1))
    order = OrderSnapshot("o:2024-03-04:entry", OrderPurpose.ENTRY, 1, _usd("-1.00"))
    fill = FillDetail(
        order_id=order.order_id,
        purpose=OrderPurpose.ENTRY,
        packages=1,
        legs=(
            FilledLeg(short, -1, Price(Decimal("1.50")), _quote_id(short, D1, Slot.F1)),
            FilledLeg(long, 1, Price(Decimal("0.50")), _quote_id(long, D1, Slot.F1)),
        ),
        net_debit=_usd("-1.00"),
        fees=_usd("1.00"),
        limit_usd=_usd("-1.00"),
    )
    open_ = _summary(cash, receivable="1.00", payable="1.00", reserve="3.00", held=held)
    marks = tuple(sorted(_quote_id(c, D1, Slot.CLOSE) for c in (short, long)))
    return [
        _event(_At(D1, Slot.OPEN, Phase.SETTLE_DUE), SimEventKind.DEPOSIT, _summary(cash)),
        _event(
            _At(D1, Slot.DEC, Phase.DECIDE),
            SimEventKind.ORDER_SUBMITTED,
            _summary(cash, order=order),
            refs=(_quote_id(short, D1, Slot.DEC), _quote_id(long, D1, Slot.DEC)),
            detail=DecisionDetail(OrderPurpose.ENTRY, None, None),
        ),
        _event(
            _At(D1, Slot.F1, Phase.FILL),
            SimEventKind.FILLED,
            open_,
            refs=tuple(leg.quote_id for leg in fill.legs),
            detail=fill,
        ),
        _event(_At(D1, Slot.CLOSE, Phase.MARK), SimEventKind.MARKED, open_, refs=marks),
        _event(_At(D1, Slot.CUT, Phase.SNAPSHOT), SimEventKind.SNAPSHOT, open_),
    ]


def _exit_day(cash: Decimal, expiry: date, purpose: OrderPurpose) -> list[SimEvent]:
    """D2: held session 2 >= max_hold 2 -> TIME_EXIT; close D = +1.00 - 0.20 = 0.80, fees 1."""
    short, long = _legs(expiry)
    held = ((long, 1), (short, -1))
    order = OrderSnapshot(f"o:2024-03-05:{purpose}", purpose, 1, _usd("0.80"))
    trigger = ExitTrigger.TIME_EXIT if purpose is OrderPurpose.EXIT else None
    fill = FillDetail(
        order_id=order.order_id,
        purpose=purpose,
        packages=1,
        legs=(
            FilledLeg(short, 1, Price(Decimal("1.00")), _quote_id(short, D2, Slot.F1)),
            FilledLeg(long, -1, Price(Decimal("0.20")), _quote_id(long, D2, Slot.F1)),
        ),
        net_debit=_usd("0.80"),
        fees=_usd("1.00"),
        limit_usd=_usd("0.80"),
    )
    held_state = _summary(cash, reserve="3.00", held=held)
    flat = _summary(cash, payable="1.80")
    marks = tuple(sorted(_quote_id(c, D2, Slot.DEC) for c in (short, long)))
    pnl = LiquidationDetail(  # 0 realized - (D -1.00 + fees 1.00) - close D 0.80 - Fee 1.00
        _usd("0"), _usd("0.00"), _usd("0.80"), _usd("1.00"), _usd("1.00"), _usd("-1.80")
    )
    return [
        _event(_At(D2, Slot.OPEN, Phase.SETTLE_DUE), SimEventKind.SETTLE_DUE, held_state),
        _event(
            _At(D2, Slot.DEC, Phase.MARK), SimEventKind.MARKED, held_state, refs=marks, detail=pnl
        ),
        _event(
            _At(D2, Slot.DEC, Phase.DECIDE),
            SimEventKind.ORDER_SUBMITTED,
            _summary(cash, reserve="3.00", held=held, order=order),
            refs=(_quote_id(short, D2, Slot.DEC), _quote_id(long, D2, Slot.DEC)),
            detail=DecisionDetail(purpose, None, trigger),
        ),
        _event(
            _At(D2, Slot.F1, Phase.FILL),
            SimEventKind.FILLED,
            flat,
            refs=tuple(leg.quote_id for leg in fill.legs),
            detail=fill,
        ),
        _event(_At(D2, Slot.CUT, Phase.SNAPSHOT), SimEventKind.SNAPSHOT, flat),
    ]


def _flat_day(day: date, cash: Decimal) -> list[SimEvent]:
    flat = _summary(cash)
    return [
        _event(_At(day, Slot.OPEN, Phase.SETTLE_DUE), SimEventKind.SETTLE_DUE, flat),
        _event(_At(day, Slot.CUT, Phase.SNAPSHOT), SimEventKind.SNAPSHOT, flat),
    ]


def _point(event: SimEvent, mid: str | None = None, natural: str | None = None) -> AccountPoint:
    s = event.summary
    money = s.cash.amount + s.receivable.amount - s.payable.amount
    return AccountPoint(
        session_date=event.session_date,
        market_valuation_at_ns=_at(event.session_date, Slot.CLOSE),
        ledger_cutoff_at_ns=event.at_ns,
        cash=s.cash,
        receivable=s.receivable,
        payable=s.payable,
        encumbrance=s.reserve,
        headroom=Usd(s.cash.amount - s.payable.amount - s.reserve.amount),
        mid_nlv=Usd(money + Decimal(mid)) if mid else Usd(money),
        natural_nlv=Usd(money + Decimal(natural)) if natural else Usd(money),
    )


def _trace(events: list[SimEvent], status: CalculationStatus, headline: bool) -> Trace:
    snapshots = [e for e in events if e.kind is SimEventKind.SNAPSHOT]
    held_marks = {D1: ("-1.10", "-1.20")}  # CLOSE mids 1.55/0.45, naturals ask 1.60, bid 0.40
    curve = tuple(
        _point(e, *held_marks[e.session_date]) if e.summary.held else _point(e) for e in snapshots
    )
    return Trace(events=tuple(events), account_curve=curve, status=status, headline=headline)


def closed_run(
    cash: Decimal = Decimal(4),
    expiry: date = LATE_EXPIRY,
    purpose: OrderPurpose = OrderPurpose.EXIT,
) -> Trace:
    """Enter on D1, time-exit on D2 (cash - 1.80 after T+1), flat on the final session D3."""
    events = _entry_day(cash, expiry) + _exit_day(cash, expiry, purpose)
    return _trace(events + _flat_day(D3, cash - Decimal("1.80")), VALID, headline=True)


def settled_run() -> Trace:
    """Enter on D1 in the vertical expiring D2; D2's exit is deferred and it settles at 101.

    Settlement: -1 x max(102 - 101, 0) + 1 x max(100 - 101, 0) = -1.00, payable at D3.
    """
    events = _entry_day(Decimal(4), D2)
    short, long = _legs(D2)
    held = ((long, 1), (short, -1))
    held_state = _summary(Decimal(4), reserve="3.00", held=held)
    settled = _summary(Decimal(4), payable="1.00")
    settlement = SettlementDetail(
        series="X_PM",
        observation_id="s:X_PM:2024-03-05:c0",
        value=Price(Decimal(101)),
        contract_ids=(long, short),
        net_cash=_usd("-1.00"),
        fees=_usd("0"),
    )
    deferred = DecisionDetail(
        OrderPurpose.EXIT, DecisionReason.DECISION_QUOTE_INVALID, ExitTrigger.TIME_EXIT
    )
    events += [
        _event(_At(D2, Slot.OPEN, Phase.SETTLE_DUE), SimEventKind.SETTLE_DUE, held_state),
        _event(
            _At(D2, Slot.DEC, Phase.DECIDE),
            SimEventKind.EXIT_DEFERRED,
            held_state,
            detail=deferred,
        ),
        _event(
            _At(D2, Slot.CUT, Phase.LIFECYCLE),
            SimEventKind.SETTLED,
            settled,
            refs=(settlement.observation_id,),
            detail=settlement,
        ),
        _event(_At(D2, Slot.CUT, Phase.SNAPSHOT), SimEventKind.SNAPSHOT, settled),
    ]
    return _trace(events + _flat_day(D3, Decimal(3)), VALID, headline=True)


def invalid_run(headline: bool = False) -> Trace:
    """Enter on D1; the D1 CLOSE mark of the held vertical is missing: MISSING_VALUATION."""
    events = _entry_day(Decimal(4), LATE_EXPIRY)[:3]
    issue = Issue(ErrorCode.MISSING_VALUATION, "no CLOSE mark", "", affected_interval="2024-03-04")
    invalid = replace(events[-1].summary, status=CalculationStatus.INVALID)
    events.append(
        _event(_At(D1, Slot.CLOSE, Phase.MARK), SimEventKind.INVALIDATED, invalid, detail=issue)
    )
    return _trace(events, CalculationStatus.INVALID, headline=headline)


def _named(report: Report, prop: str) -> list[str]:
    return [v for v in report.violations if v.startswith(prop)]


def test_a_closed_run_satisfies_every_property_and_reaches_the_valid_closed_canary() -> None:
    report = check(_model(LATE_EXPIRY), closed_run())
    assert report.violations == ()
    assert report.canaries == {"CanaryValidClosedRun"}


def test_a_settled_credit_run_satisfies_every_property_and_reaches_the_credit_loss_canary() -> None:
    report = check(_model(D2), settled_run())
    assert report.violations == ()
    assert report.canaries == {"CanaryValidClosedRun", "CanarySettledCreditLoss"}


def test_an_invalidated_run_satisfies_every_property_and_reaches_the_invalid_canary() -> None:
    report = check(_model(LATE_EXPIRY), invalid_run())
    assert report.violations == ()
    assert report.canaries == {"CanaryMissingValuation"}


def test_an_unfunded_fill_breaks_fully_funded() -> None:
    # cash 3.50: headroom after the entry fill = 3.50 - 1 - 3 = -0.50.
    report = check(_model(LATE_EXPIRY, cash="3.50"), closed_run(cash=Decimal("3.50")))
    assert _named(report, "FullyFunded")


def test_a_fill_without_a_live_submission_breaks_fills_only_after_submission() -> None:
    trace = closed_run()
    events = tuple(e for e in trace.events if e.event_id != "2024-03-04:DEC:5:1")
    report = check(_model(LATE_EXPIRY), replace(trace, events=events))
    assert _named(report, "FillsOnlyAfterSubmission")


def test_a_fill_at_its_decision_observation_breaks_fills_only_after_submission() -> None:
    trace = closed_run()
    fill = trace.events[2]
    assert isinstance(fill.detail, FillDetail)
    dec_legs = tuple(
        replace(leg, quote_id=_quote_id(leg.contract_id, D1, Slot.DEC)) for leg in fill.detail.legs
    )
    early = replace(fill, detail=replace(fill.detail, legs=dec_legs))
    events = (*trace.events[:2], early, *trace.events[3:])
    report = check(_model(LATE_EXPIRY), replace(trace, events=events))
    assert _named(report, "FillsOnlyAfterSubmission")


def test_a_position_held_past_its_expiry_breaks_no_position_past_expiry() -> None:
    # The vertical expires on D1 yet is still held, valid, on D2.
    report = check(_model(D1), closed_run(expiry=D1))
    assert _named(report, "NoPositionPastExpiry")


def test_a_generation_settled_twice_breaks_settled_once() -> None:
    trace = settled_run()
    settled = next(e for e in trace.events if e.kind is SimEventKind.SETTLED)
    again = replace(settled, event_id="2024-03-05:CUT:6:2", seq=2)
    index = trace.events.index(settled) + 1
    events = (*trace.events[:index], again, *trace.events[index:])
    report = check(_model(D2), replace(trace, events=events))
    assert _named(report, "SettledOnce")


def test_a_headline_on_an_invalid_run_breaks_headline_only_if_valid() -> None:
    report = check(_model(LATE_EXPIRY), invalid_run(headline=True))
    assert _named(report, "HeadlineOnlyIfValid")


def test_a_roll_close_with_rolls_disabled_breaks_rolls_within_caps() -> None:
    # Rolls disabled is MaxRolls = 0: a roll close needs rolls < MaxRolls.
    trace = closed_run(purpose=OrderPurpose.ROLL_CLOSE)
    report = check(_model(LATE_EXPIRY, max_rolls=0), trace)
    assert _named(report, "RollsWithinCaps")


def test_an_event_off_its_slot_instant_breaks_slot_clock() -> None:
    # The D1 entry fill a minute late: 15:47 is F2's instant, not F1's.
    trace = closed_run()
    fill = trace.events[2]
    late = replace(fill, at_ns=fill.at_ns + 60 * 10**9)
    events = (*trace.events[:2], late, *trace.events[3:])
    report = check(_model(LATE_EXPIRY), replace(trace, events=events))
    assert _named(report, "SlotClock")


def test_a_stale_mark_breaks_mark_close() -> None:
    # D2's DEC mark cites D1's CLOSE quotes: another session's, a day old.
    trace = closed_run()
    marked = next(e for e in trace.events if e.event_id == "2024-03-05:DEC:3:1")
    short, long = _legs(LATE_EXPIRY)
    stale = replace(
        marked, input_refs=(_quote_id(long, D1, Slot.CLOSE), _quote_id(short, D1, Slot.CLOSE))
    )
    events = tuple(stale if e is marked else e for e in trace.events)
    report = check(_model(LATE_EXPIRY), replace(trace, events=events))
    assert _named(report, "MarkClose")


def test_a_dec_mark_without_a_bid_for_the_closing_sell_breaks_mark_close() -> None:
    # The long put closes by selling; a NO_BID DEC quote cannot witness quote_ok.
    model = _model(LATE_EXPIRY)
    _, long = _legs(LATE_EXPIRY)
    ref = _quote_id(long, D2, Slot.DEC)
    quotes = {**model.quotes, ref: replace(model.quotes[ref], bid=Decimal(0))}
    report = check(replace(model, quotes=quotes), closed_run())
    assert _named(report, "MarkClose")


def _restated(event: SimEvent, **money: str) -> SimEvent:
    """Return ``event`` with its summary's cash, receivable or payable replaced."""
    return replace(event, summary=replace(event.summary, **{k: _usd(v) for k, v in money.items()}))


@pytest.mark.parametrize(
    "stored",
    [
        None,  # no components stored
        LiquidationDetail(  # the exit fee left out of the P&L
            _usd("0"), _usd("0.00"), _usd("0.80"), _usd("0"), _usd("1.00"), _usd("-0.80")
        ),
    ],
)
def test_a_dec_mark_without_alphas_liquidation_components_breaks_liquidation_pnl(
    stored: LiquidationDetail | None,
) -> None:
    trace = closed_run()
    events = tuple(
        replace(e, detail=stored) if e.event_id == "2024-03-05:DEC:3:1" else e for e in trace.events
    )
    report = check(_model(LATE_EXPIRY), replace(trace, events=events))
    assert _named(report, "LiquidationPnL")


def test_a_credit_booked_to_cash_at_its_fill_breaks_post_debit() -> None:
    # FillOpen leaves cash, posts the credit 1 to recv and the fees 1 to pay. Booking the credit
    # to cash keeps cash + recv - pay (and here hides the FullyFunded breach of cash 3.50).
    trace = closed_run(cash=Decimal("3.50"))
    moved = {"2024-03-04:F1:4:1", "2024-03-04:CLOSE:3:1", "2024-03-04:CUT:7:1"}
    events = [
        _restated(e, cash="4.50", receivable="0") if e.event_id in moved else e
        for e in trace.events
    ]
    report = check(_model(LATE_EXPIRY, cash="3.50"), _trace(events, VALID, headline=True))
    assert _named(report, "PostDebit")


def test_dues_left_unsettled_at_the_next_open_break_advance_session() -> None:
    # D1's receivable 1 and payable 1 stay open through D2 (no SETTLE_DUE) and settle at D3: T+2.
    trace = closed_run()
    d2 = [e for e in trace.events if e.session_date == D2 and e.kind is not SimEventKind.SETTLE_DUE]
    late = {"payable": "1.00", "receivable": "1.00"}
    exit_fill = {"payable": "2.80", "receivable": "1.00"}
    restated = [_restated(e, **(exit_fill if e.slot in (Slot.F1, Slot.CUT) else late)) for e in d2]
    events = [e for e in trace.events if e.session_date == D1] + restated
    events += [e for e in trace.events if e.session_date == D3]
    report = check(_model(LATE_EXPIRY), _trace(events, VALID, headline=True))
    assert _named(report, "AdvanceSession")


def test_a_held_session_without_a_close_mark_breaks_mark_close() -> None:
    # D1 holds the vertical expiring D3 at CLOSE: MarkClose marks it or invalidates the run.
    trace = closed_run()
    events = tuple(e for e in trace.events if e.event_id != "2024-03-04:CLOSE:3:1")
    report = check(_model(LATE_EXPIRY), replace(trace, events=events))
    assert _named(report, "MarkClose")


@pytest.mark.parametrize("mid_natural", [("-1.09", "-1.20"), ("-1.10", "-1.60"), None])
def test_a_held_account_point_off_its_close_marks_breaks_the_curve(
    mid_natural: tuple[str, str] | None,
) -> None:
    # D1's held NLVs are money + Σ q·m·mark at the CLOSE quotes: mid -1.10, natural -1.20.
    trace = closed_run()
    first, *rest = trace.account_curve
    money = first.cash.amount + first.receivable.amount - first.payable.amount
    nlvs = (None, None) if mid_natural is None else tuple(Decimal(v) for v in mid_natural)
    mid, natural = (None if v is None else Usd(money + v) for v in nlvs)
    point = replace(first, mid_nlv=mid, natural_nlv=natural)
    report = check(_model(LATE_EXPIRY), replace(trace, account_curve=(point, *rest)))
    assert _named(report, "Curve")


def test_the_canary_list_names_fourteen_distinct_canaries() -> None:
    assert len(CANARIES) == len(set(CANARIES)) == 14


def test_the_canary_list_is_the_union_of_the_local_tlc_configs() -> None:
    if not TLA_DIR.is_dir():
        pytest.skip("docs/design/ is gitignored; CANARIES restates the TLC configs' canary lines")
    lines = [
        line
        for config in sorted(TLA_DIR.glob("R1Campaign*.cfg"))
        for line in config.read_text(encoding="utf-8").splitlines()
        if line.startswith(CANARY_LINE)
    ]
    assert lines, f"no {CANARY_LINE!r} line in {TLA_DIR}"
    assert {name for line in lines for name in line[len(CANARY_LINE) :].split()} == set(CANARIES)


# --- (c) TLA-scale random markets -------------------------------------------------------------

TLA_WINDOW: Final = (date(2024, 3, 4), date(2024, 3, 13))
TLA_SESSIONS: Final = (
    date(2024, 3, 4),
    date(2024, 3, 5),
    date(2024, 3, 6),
    date(2024, 3, 7),
    date(2024, 3, 8),
    date(2024, 3, 11),
    date(2024, 3, 12),
    date(2024, 3, 13),
)
TLA_EXPIRIES: Final = (
    date(2024, 3, 11),
    date(2024, 3, 12),
    date(2024, 3, 13),
    date(2024, 3, 14),
    date(2024, 3, 15),
    date(2024, 3, 18),
    date(2024, 3, 19),
    date(2024, 3, 20),
)
"""``weekly_dtes`` 7 ... 11, 14 ... 16 from 2024-03-04: a daily expiry, so every session has
one at dte 7, the schema's shortest. With ``RollDTE`` 6 a vertical rolls the session after its
entry and its replacement can hit a roll or campaign cap two sessions later, as in
``R1Campaign.pass-caps.cfg`` (Tenor 2, RollDTE 1); the verticals of the first three sessions
expire inside the window."""
QUOTE_SLOTS: Final = (Slot.DEC, Slot.F1, Slot.F2, Slot.F3, Slot.CLOSE)
CELLS: Final = tuple((day, slot) for day in TLA_SESSIONS for slot in QUOTE_SLOTS)
"""Every (session, slot) with a package quote, in ``TlaCase.quotes`` order."""
PIN_SIZE: Final = 5
"""Displayed size of every pinned side: capacity floor(5 x participation 1) = 5 >= n = 1, so a
fill never waits on size (TLA has none)."""
TLA_SETTINGS: Final = settings(
    derandomize=True,
    database=None,
    max_examples=40,
    deadline=None,
    phases=(HypothesisPhase.explicit, HypothesisPhase.reuse, HypothesisPhase.generate),
)
"""Bounded, reproducible, and without shrinking: a failing case prints whole, and each example
runs the engine once."""

_TLA_STRATEGY: Final = """{{
  "schema_version": 1,
  "name": "TLA-scale random market (synthetic fixture, no performance implied)",
  "product": {{"underlying_symbol": "SPX", "allowed_option_roots": ["SPXW"],
    "family": "us_european_pm_cash_index"}},
  "structure": "vertical",
  "legs": [{legs}],
  "clock_profile": "scheduled_daily_v1",
  "entry": {{"schedule": {{"frequency": "daily"}}, "all_conditions": []}},
  "exits": {{"take_profit": {take_profit}, "stop_loss": {stop_loss}, "exit_dte": 0,
    "max_holding_sessions": {max_hold}}},
  "roll": {roll},
  "account": {{"currency": "USD", "initial_cash_usd": "{cash}", "policy": "fully_funded_v1",
    "max_campaign_risk_fraction": 1, "max_total_risk_fraction": 1,
    "max_concurrent_campaigns": 1}},
  "sizing": {{"method": "fixed_contracts", "contracts": 1}},
  "liquidity": {{"min_open_interest": 0, "min_cumulative_volume": 0,
    "max_absolute_spread_price_units": "5.00", "max_relative_spread": 5,
    "require_positive_bid_for_entry": false}},
  "execution": {{"model": "natural_package_limit_v1", "participation_fraction": 1,
    "max_contracts_per_order": 10, "price_allowance_usd": "0.00", "fill_attempts": 3,
    "quantity_policy": "all_or_none_complete_packages"}},
  "fee_schedule_id": "illustrative_flat_1usd_per_contract_side_v1",
  "funding_policy_id": "illustrative_zero_interest_no_borrow_v1",
  "comparison_policy_id": "historical_usd_cash_comparison_v1",
  "end_policy": "liquidate_at_final_session"
}}"""
_ROLLS_ON: Final = (
    '{{"mode": "sequential", "trigger_dte": {roll_dte}, "max_rolls": {max_rolls}, '
    '"max_campaign_sessions": {max_campaign}}}'
)
_ROLLS_OFF: Final = '{"mode": "disabled"}'
_TARGET_EXPIRY: Final = (
    '"expiry_selection": {"method": "target_dte", "target_dte": 7, "min_dte": 7, "max_dte": 10}'
)


@dataclass(frozen=True, slots=True)
class TlaCase:
    """One TLA-scale run; the market and strategy are built from it in the test body.

    Attributes:
        kind: ``"credit"`` sells the put vertical 96/96-W; ``"debit"`` buys the call vertical
            104/104+W (spot 100, so every traded strike is off the five nearest 98 ... 102).
        width: ``W``.
        slack: Initial cash above the funding boundary ``W + 4``: 0 or 1 to reach refusals,
            10 to fund the fees of several generations (``R1Campaign.pass-caps.cfg``'s Cash0).
        rolls: Sequential rolls or none.
        roll_dte: ``RollDTE`` (``trigger_dte``) when rolling.
        max_rolls: ``MaxRolls`` when rolling.
        max_campaign: ``MaxCampaign`` (``max_campaign_sessions``) when rolling.
        max_hold: ``MaxHold`` (``max_holding_sessions``).
        pnl_rules: Take profit at half the basis and stop loss at twice it (``R1Campaign``'s
            ``TakeProfit`` and ``StopLoss``), or neither.
        quotes: The package quote (bid, ask) of each of ``CELLS``, or None for ``Missing``.
        settlements: The SPX_PM value of each of ``TLA_EXPIRIES``, or None when it is missing.

    """

    kind: str
    width: int
    slack: int
    rolls: bool
    roll_dte: int
    max_rolls: int
    max_campaign: int
    max_hold: int
    pnl_rules: bool
    quotes: tuple[tuple[int, int] | None, ...]
    settlements: tuple[int | None, ...]


def package_quotes(width: int) -> st.SearchStrategy[tuple[int, int]]:
    """Draw from ``Quotes``: bid in -1..W, ask in bid..W+2, mid in [0, W].

    MaxAsk = W + 2 is ``R1Campaign.pass-refusal.cfg``'s, so a credit close can cost more than
    the width and be refused for funding.
    """
    return st.integers(-1, width).flatmap(
        lambda bid: st.tuples(
            st.just(bid), st.integers(max(bid, -bid), min(width + 2, 2 * width - bid))
        )
    )


@st.composite
def tla_cases(draw: st.DrawFn) -> TlaCase:
    """Draw one in-model case: n = 1, a 2-leg vertical, liquidate_at_final_session.

    A drawn quote holds until the next of at most 12 change points, and ``Missing`` is rare (at
    most three quote cells and one settlement per case), so a run lives long enough to fill at
    its decision price, close, roll, settle and be refused. At even odds of ``Missing`` and a
    fresh quote per cell, nearly every run ended at its first CLOSE mark.
    """
    width = draw(st.sampled_from((2, 3)))
    pairs = draw(st.lists(package_quotes(width), min_size=len(CELLS), max_size=len(CELLS)))
    changes = draw(st.sets(st.integers(1, len(CELLS) - 1), max_size=12))
    missing = draw(st.sets(st.integers(0, len(CELLS) - 1), max_size=3))
    values = draw(
        st.lists(st.integers(90, 110), min_size=len(TLA_EXPIRIES), max_size=len(TLA_EXPIRIES))
    )
    dropped = draw(st.sets(st.integers(0, len(TLA_EXPIRIES) - 1), max_size=1))
    return TlaCase(
        kind=draw(st.sampled_from(("credit", "debit"))),
        width=width,
        slack=draw(st.sampled_from((0, 1, 10))),
        rolls=draw(st.booleans()),
        roll_dte=draw(st.sampled_from((4, 6))),
        max_rolls=draw(st.sampled_from((1, 2))),
        max_campaign=draw(st.sampled_from((2, 4, 6))),
        max_hold=draw(st.sampled_from((3, 4, 6))),
        pnl_rules=draw(st.booleans()),
        quotes=_held_quotes(pairs, changes, missing),
        settlements=tuple(None if i in dropped else v for i, v in enumerate(values)),
    )


def _held_quotes(
    pairs: list[tuple[int, int]], changes: set[int], missing: set[int]
) -> tuple[tuple[int, int] | None, ...]:
    """Return each cell's quote: the pair drawn at its last change point, or Missing."""
    held = pairs[0]
    quotes: list[tuple[int, int] | None] = []
    for index, pair in enumerate(pairs):
        held = pair if index in changes else held
        quotes.append(None if index in missing else held)
    return tuple(quotes)


def scripted(  # noqa: PLR0913 — a case's fields, the quotes as a default plus exceptions
    kind: str,
    width: int,
    default: tuple[int, int],
    cells: dict[tuple[date, Slot], tuple[int, int] | None],
    settlements: dict[date, int | None],
    **rules: Any,
) -> TlaCase:
    """Return a scripted case: ``default`` in every quote cell but ``cells``.

    Every expiry settles at 100 (all out of the money) but those in ``settlements``; ``rules``
    override the case's rule fields.
    """
    fields: dict[str, Any] = {
        "slack": 0,
        "rolls": False,
        "roll_dte": 6,
        "max_rolls": 1,
        "max_campaign": 6,
        "max_hold": 4,
        "pnl_rules": False,
    }
    return TlaCase(
        kind=kind,
        width=width,
        quotes=tuple(cells.get(cell, default) for cell in CELLS),
        settlements=tuple(settlements.get(expiry, 100) for expiry in TLA_EXPIRIES),
        **{**fields, **rules},
    )


_FILL_SLOTS: Final = (Slot.F1, Slot.F2, Slot.F3)
_DEC_AND_FILLS: Final = (Slot.DEC, *_FILL_SLOTS)
CANARY_CASES: Final = MappingProxyType(
    {
        # Credit 1 each entry (basis 1 < Fee 2). Monday's 03-11 vertical rolls at dte 6 on
        # Tuesday; Wednesday's replacement (03-13) makes rolls = MaxRolls; on Thursday, dte 6
        # again and campaign session 4 = MaxCampaign: a roll-cap and campaign-cap exit without a
        # time exit (held 2 < 4, dte 6 > 0). FINAL closes the last vertical on 03-13.
        "rolled_then_capped": scripted(
            "credit", 2, (1, 1), {}, {}, slack=10, rolls=True, max_campaign=4
        ),
        # Credit 1; from Tuesday a close costs 4 > W + credit, more than cash can fund: the
        # stop-loss exit is refused at F1-F3 every session through the 03-11 expiry, where the
        # vertical settles at 95 (the short 96 put pays 1). Funding refuses every later entry.
        "refused_then_settled": scripted(
            "credit",
            2,
            (1, 1),
            {(day, slot): (0, 4) for day in TLA_SESSIONS[1:6] for slot in _DEC_AND_FILLS},
            {date(2024, 3, 11): 95},
            pnl_rules=True,
        ),
        # Debit 1 per entry, time exits at held session 3; the final session's FINAL finds no
        # quote at F1-F3 and the held vertical is still marked at CLOSE: incomplete.
        "final_unfilled": scripted(
            "debit",
            3,
            (1, 1),
            {(TLA_SESSIONS[-1], slot): None for slot in _FILL_SLOTS},
            {},
            slack=10,
            max_hold=3,
        ),
        # Monday's entry has no CLOSE mark: MISSING_VALUATION.
        "unmarked": scripted("credit", 3, (2, 2), {(TLA_SESSIONS[0], Slot.CLOSE): None}, {}),
    }
)
UNREACHABLE_CANARIES: Final = frozenset({"CanaryTakeProfitExit"})
"""At multiplier 1 the $1-per-side schedule charges ``Fee`` = 2 per package fill, so a package
worth at most W <= 3 never earns half its basis back after the entry and exit fees (G01, at
multiplier 100, reaches it)."""


def _leg(leg_id: str, side: str, right: str, selection: str, expiry: str) -> str:
    return (
        f'{{"leg_id": "{leg_id}", "side": "{side}", "option_type": "{right}", "ratio": 1, '
        f'{expiry}, "strike_selection": {selection}}}'
    )


def _vertical_legs(kind: str, width: int) -> str:
    """Return the legs' JSON: the moneyness leg (tolerance 0.004, neighbours 0.01 away) first."""
    right, anchor, other, target, offset = (
        ("put", "short_put", "long_put", "0.96", -width)
        if kind == "credit"
        else ("call", "long_call", "short_call", "1.04", width)
    )
    sides = ("sell", "buy") if kind == "credit" else ("buy", "sell")
    moneyness = f'{{"method": "moneyness", "target_strike_to_spot": {target}, "tolerance": 0.004}}'
    offset_rule = (
        f'{{"method": "strike_offset", "anchor_leg_id": "{anchor}", '
        f'"offset_price_units": "{offset}"}}'
    )
    same_expiry = f'"expiry_selection": {{"method": "same_as", "anchor_leg_id": "{anchor}"}}'
    return ", ".join(
        (
            _leg(anchor, sides[0], right, moneyness, _TARGET_EXPIRY),
            _leg(other, sides[1], right, offset_rule, same_expiry),
        )
    )


def tla_strategy(case: TlaCase) -> bytes:
    """Return the strategy document of ``case``; cash is ``W + 4 + slack``.

    ``W + 4`` is the credit entry's funding boundary: entry fees 2 plus the reserve W + 2 (the
    credit receivable is not spendable). A debit entry at that cash is funded for D <= W + slack.
    """
    basis = "initial_credit" if case.kind == "credit" else "initial_debit"
    rolls = _ROLLS_ON.format(
        roll_dte=case.roll_dte, max_rolls=case.max_rolls, max_campaign=case.max_campaign
    )
    document = _TLA_STRATEGY.format(
        legs=_vertical_legs(case.kind, case.width),
        take_profit=f'{{"basis": "{basis}", "fraction": 0.5}}' if case.pnl_rules else "null",
        stop_loss=f'{{"basis": "{basis}", "multiple": 2}}' if case.pnl_rules else "null",
        max_hold=case.max_hold,
        roll=rolls if case.rolls else _ROLLS_OFF,
        cash=f"{case.width + 4 + case.slack}.00",
    )
    return document.encode("utf-8")


def _package_contracts(case: TlaCase, expiry: date) -> tuple[str, str]:
    """Return (the package's long leg, its short leg): the pricier and the cheaper strike."""
    if case.kind == "credit":
        return f"SPXW:{expiry}:P:96", f"SPXW:{expiry}:P:{96 - case.width}"
    return f"SPXW:{expiry}:C:104", f"SPXW:{expiry}:C:{104 + case.width}"


def _quote_exists(expiry: date, day: date, slot: Slot) -> bool:
    return day < expiry or (day == expiry and slot is not Slot.CLOSE)


def _cell_overrides(
    case: TlaCase, day: date, slot: Slot, quote: tuple[int, int] | None
) -> list[Override]:
    """Realize one package quote on every live expiry of its (session, slot).

    The cheap leg is LOCKED at 1/1 and the pricier leg quotes (bid + 1, ask + 1), so the
    package's natural bid and ask are the drawn ones; Missing drops the pricier leg's quote.
    """
    overrides: list[Override] = []
    for expiry in (e for e in TLA_EXPIRIES if _quote_exists(e, day, slot)):
        rich, cheap = _package_contracts(case, expiry)
        one = Decimal(1)
        overrides.append(QuotePin(cheap, day, (slot,), one, one, PIN_SIZE, PIN_SIZE))
        if quote is None:
            overrides.append(QuoteDrop(rich, day, (slot,)))
            continue
        bid, ask = (Decimal(price + 1) for price in quote)
        overrides.append(QuotePin(rich, day, (slot,), bid, ask, PIN_SIZE, PIN_SIZE))
    return overrides


def tla_market(case: TlaCase) -> MarketSpec:
    """Return the TLA-scale market of ``case``: multiplier and units 1, spot 100, DF 1."""
    overrides: list[Override] = []
    for (day, slot), quote in zip(CELLS, case.quotes, strict=True):
        overrides += _cell_overrides(case, day, slot, quote)
    for expiry, value in zip(TLA_EXPIRIES, case.settlements, strict=True):
        pin = None if value is None else SettlementPin("SPX_PM", expiry, Price(Decimal(value)))
        overrides.append(SettlementDrop("SPX_PM", expiry) if pin is None else pin)
    zero = Decimal(0)
    return MarketSpec(
        seed=1,
        first_session=date(2024, 3, 4),
        last_session=date(2024, 3, 28),
        holidays=(),
        early_closes=(),
        index_start=Decimal(100),
        daily_drift=zero,
        daily_vol=zero,
        sigma=Decimal("0.18"),
        rates=((28, zero), (91, zero)),
        roots=("SPXW",),
        weekly_dtes=(7, 8, 9, 10, 11, 14, 15, 16),
        strike_step=Decimal(1),
        strikes_each_side=8,
        tick=Decimal("0.01"),
        half_spread_abs=Decimal("0.01"),
        half_spread_rel=Decimal("0.02"),
        bid_size=50,
        ask_size=50,
        premium_multiplier=Decimal(1),
        deliverable_units=Decimal(1),
        overrides=tuple(overrides),
    )


def refine(case: TlaCase) -> tuple[Report, ArtifactBundle]:
    """Run ``case`` through the engine and check the trace against R1Campaign."""
    strategy = load_strategy(tla_strategy(case))
    dataset = generate(tla_market(case))
    start, end = TLA_WINDOW
    resolved = resolve(
        strategy, start_date=start, end_date=end, manifest_id=dataset.manifest.manifest_id
    )
    bundle = run(resolved, dataset)
    return check(model_of(strategy, dataset, start, end), trace_of(bundle)), bundle


def test_tla_case_strategies_load_with_the_intended_directions() -> None:
    for kind, direction in (("credit", PremiumDirection.CREDIT), ("debit", PremiumDirection.DEBIT)):
        for rules in ({}, {"rolls": True, "pnl_rules": True}):
            case = scripted(kind, 2, (1, 1), {}, {}, **rules)
            assert load_strategy(tla_strategy(case)).premium_direction is direction


def test_package_quotes_realize_the_drawn_package_naturals() -> None:
    # Credit W = 2, package (bid -1, ask 3): P96 at 0/4 and P94 locked at 1/1 on 2024-03-11.
    case = scripted("credit", 2, (1, 1), {}, {})
    pins = _cell_overrides(case, date(2024, 3, 4), Slot.DEC, (-1, 3))
    by_contract = {
        pin.contract: (pin.bid, pin.ask)
        for pin in pins
        if isinstance(pin, QuotePin) and pin.contract.startswith("SPXW:2024-03-11")
    }
    rich, cheap = by_contract["SPXW:2024-03-11:P:96"], by_contract["SPXW:2024-03-11:P:94"]
    assert (rich[0] - cheap[1], rich[1] - cheap[0]) == (Decimal(-1), Decimal(3))
    assert len(pins) == 2 * len(TLA_EXPIRIES)


def test_the_scripted_tla_cases_reach_every_reachable_canary() -> None:
    reached: set[str] = set()
    for name, case in CANARY_CASES.items():
        report, _ = refine(case)
        assert report.violations == (), name
        reached |= report.canaries
    assert reached == set(CANARIES) - UNREACHABLE_CANARIES


@TLA_SETTINGS
@given(case=tla_cases())
@example(case=CANARY_CASES["rolled_then_capped"])
@example(case=CANARY_CASES["refused_then_settled"])
def test_random_tla_scale_markets_refine_r1_campaign(case: TlaCase) -> None:
    report, bundle = refine(case)
    fills = [e.detail for e in bundle.events if isinstance(e.detail, FillDetail)]
    event(f"status {bundle.result.calculation_status}")
    event(f"fills {min(len(fills), 3)}")
    for purpose in sorted({fill.purpose for fill in fills}):
        event(f"filled {purpose}")
    for canary in sorted(report.canaries):
        event(canary)
    assert report.violations == ()
