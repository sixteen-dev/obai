"""The LEAN reconciliation on a hand-built run and hand-built replay records (ADR 0002 §12).

These run in the default suite: they need neither LEAN nor .NET.

The run is a 5000/4995 put credit vertical on SPXW 2024-03-05, one package, $10,000, in a pinned
synthetic market (SPX 5000, zero rates) over 2024-03-04 .. 2024-03-06; m = 100, $1 per contract
side, T+1:

- 2024-03-04 F1: sell P5000 at 2.00, buy P4995 at 1.10: D = -90, fees 2; receivable 90, payable
  2. CLOSE quotes P5000 2.00/2.20 (mid 2.10) and P4995 0/0.10 (NO_BID, mid 0.05): mid NLV
  = 10088 + 100·(-2.10 + 0.05) = 9883.
- 2024-03-05 (expiry): OPEN cash 10088. DEC exit limit 100·1.20 - 100·0.40 = 80; F1-F3 quote
  1.05/1.25 and 0.40/0.50, D = 85 > 80: LIMIT three times, cancelled. No CLOSE quotes (expiry
  session). CUT settles at 4997: P5000 (q -1) pays 100·3 = 300, P4995 nothing; payable 300, mid
  NLV 9788.
- 2024-03-06: OPEN pays the 300: cash 9788.

The records are what LEAN reports for that replay: the combo fills at the same prices, cash
settled at once (M2); the NO_BID leg marked at its ask, tpv 10088 + 100·(-2.10 + 0.10) = 9888
(M13); on the expiry day LEAN still holds the package at 16:00 and marks it at the F3 bars, tpv
10088 + 100·(-1.15 + 0.45) = 10018, and exercises it at 01:00 on 2024-03-06 at fill price 0,
the intrinsic value a cash adjustment (M14); the diagnostic limit at 0.80 is never filled, its
legs CancelPending then Canceled at F3. LEAN at commit b1337938 wrote the same records, value
for value on every field reconcile reads, when this run was replayed natively.
"""

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Final

import pytest

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import FidelityClass, TradingSession
from options_backtest.engine.clock import Phase
from options_backtest.engine.orders import ExitTrigger, NonfillReason, OrderPurpose
from options_backtest.errors import ErrorCode, Issue
from options_backtest.models.artifacts import (
    AccountPoint,
    ArtifactBundle,
    DecisionDetail,
    EventDetail,
    EventSummary,
    FillDetail,
    FilledLeg,
    NonfillDetail,
    OrderSnapshot,
    SettlementDetail,
    SimEvent,
    SimEventKind,
)
from options_backtest.models.result import (
    CalculationStatus,
    ResultProvenance,
    RunWarning,
    SimulationResult,
    WarningCode,
)
from options_backtest.money import Price, Usd
from options_backtest.reference.calendars import FILL_SLOTS, Slot, slot_instant
from options_backtest.synthetic.market import (
    MarketSpec,
    Override,
    QuotePin,
    SettlementPin,
    generate,
)

from .reconcile import (
    RUN,
    Check,
    Comparison,
    KnownMismatch,
    reconcile,
    replay_input,
    unexplained,
)
from .runner import JsonRecord

MONDAY: Final = date(2024, 3, 4)
TUESDAY: Final = date(2024, 3, 5)
WEDNESDAY: Final = date(2024, 3, 6)
SHORT: Final = "SPXW:2024-03-05:P:5000"
LONG: Final = "SPXW:2024-03-05:P:4995"
HELD: Final = ((LONG, 1), (SHORT, -1))
ENTRY_ID: Final = "o:2024-03-04:entry"
EXIT_ID: Final = "o:2024-03-05:exit"
FILL_ID: Final = "2024-03-04:F1:4:1"
SETTLED_ID: Final = "2024-03-05:CUT:6:1"
PINS: Final[tuple[Override, ...]] = (
    QuotePin(
        SHORT, MONDAY, (Slot.DEC, Slot.F1, Slot.CLOSE), Decimal("2.00"), Decimal("2.20"), 50, 50
    ),
    QuotePin(LONG, MONDAY, (Slot.DEC, Slot.F1), Decimal("1.00"), Decimal("1.10"), 50, 50),
    QuotePin(LONG, MONDAY, (Slot.CLOSE,), Decimal(0), Decimal("0.10"), 0, 50),  # NO_BID: M13
    QuotePin(SHORT, TUESDAY, (Slot.DEC,), Decimal("1.00"), Decimal("1.20"), 50, 50),
    QuotePin(SHORT, TUESDAY, FILL_SLOTS, Decimal("1.05"), Decimal("1.25"), 50, 50),
    QuotePin(LONG, TUESDAY, (Slot.DEC, *FILL_SLOTS), Decimal("0.40"), Decimal("0.50"), 50, 50),
    SettlementPin("SPX_PM", TUESDAY, Price(Decimal(4997))),
)
ENTRY_ORDER: Final = OrderSnapshot(ENTRY_ID, OrderPurpose.ENTRY, 1, Usd(Decimal(-90)))
EXIT_ORDER: Final = OrderSnapshot(EXIT_ID, OrderPurpose.EXIT, 1, Usd(Decimal(80)))
MISSED: Final = NonfillDetail(
    EXIT_ID, OrderPurpose.EXIT, NonfillReason.LIMIT, None, Usd(Decimal(85))
)

type Held = tuple[tuple[str, int], ...]
type EventKey = tuple[date, Slot, Phase, int]
type Mutation = Callable[[list[JsonRecord]], None]

# Positions in _lean_records().
SHORT_FILL: Final = 2
LONG_FILL: Final = 3
MONDAY_CLOSE: Final = 5
LONG_CANCEL: Final = 10
TUESDAY_CLOSE: Final = 12
LONG_EXERCISE: Final = 13
SHORT_EXERCISE: Final = 14
END: Final = 16


def _spec(overrides: tuple[Override, ...]) -> MarketSpec:
    return MarketSpec(
        seed=1,
        first_session=MONDAY,
        last_session=WEDNESDAY,
        holidays=(),
        early_closes=(),
        index_start=Decimal(5000),
        daily_drift=Decimal(0),
        daily_vol=Decimal(0),
        sigma=Decimal("0.18"),
        rates=((28, Decimal(0)),),
        roots=("SPXW",),
        weekly_dtes=(1,),
        strike_step=Decimal(5),
        strikes_each_side=1,
        tick=Decimal("0.05"),
        half_spread_abs=Decimal("0.05"),
        half_spread_rel=Decimal("0.02"),
        bid_size=50,
        ask_size=50,
        premium_multiplier=Decimal(100),
        deliverable_units=Decimal(100),
        overrides=overrides,
    )


@pytest.fixture(scope="module")
def dataset() -> FrozenDataset:
    return generate(_spec(PINS))


@pytest.fixture(scope="module")
def sessions(dataset: FrozenDataset) -> Mapping[date, TradingSession]:
    return {session.session_date: session for session in dataset.sessions}


@pytest.fixture
def bundle(sessions: Mapping[date, TradingSession]) -> ArtifactBundle:
    return _bundle(_events(sessions, EXIT_ORDER, MISSED), _curve(sessions), _result())


def _usd(text: str) -> Usd:
    return Usd(Decimal(text))


def _summary(
    cash: str,
    dues: tuple[str, str] = ("0", "0"),
    held: Held = HELD,
    order: OrderSnapshot | None = None,
) -> EventSummary:
    receivable, payable = dues
    reserve = _usd("502" if held else "0")
    return EventSummary(
        _usd(cash), _usd(receivable), _usd(payable), reserve, held, order, CalculationStatus.VALID
    )


def _event(
    sessions: Mapping[date, TradingSession],
    key: EventKey,
    kind: SimEventKind,
    summary: EventSummary,
    detail: EventDetail | None = None,
) -> SimEvent:
    day, slot, phase, seq = key
    event_id = f"{day.isoformat()}:{slot.value}:{phase.value}:{seq}"
    at_ns = slot_instant(sessions[day], slot)
    return SimEvent(event_id, at_ns, day, slot, phase, seq, kind, "c1.g1", (), summary, detail)


def _events(
    sessions: Mapping[date, TradingSession], exit_order: OrderSnapshot, last_miss: NonfillDetail
) -> tuple[SimEvent, ...]:
    """Return the run's events; ``exit_order`` and the F3 ``last_miss`` vary for refusals."""
    held = _summary("10000", ("90", "2"))
    live = _summary("10088", order=exit_order)
    settled = _summary("10088", ("0", "300"), held=())
    fill = FillDetail(
        ENTRY_ID,
        OrderPurpose.ENTRY,
        1,
        (
            FilledLeg(SHORT, -1, Price(Decimal("2.00")), f"q:{SHORT}:2024-03-04:F1"),
            FilledLeg(LONG, 1, Price(Decimal("1.10")), f"q:{LONG}:2024-03-04:F1"),
        ),
        _usd("-90.00"),
        _usd("2.00"),
        _usd("-90.00"),
    )
    settlement = SettlementDetail(
        "SPX_PM",
        "s:SPX_PM:2024-03-05:c0",
        Price(Decimal(4997)),
        (LONG, SHORT),
        _usd("-300"),
        _usd("0"),
    )
    exit_decision = DecisionDetail(exit_order.purpose, None, ExitTrigger.TAKE_PROFIT)
    rows: list[tuple[EventKey, SimEventKind, EventSummary, EventDetail | None]] = [
        (
            (MONDAY, Slot.OPEN, Phase.SETTLE_DUE, 1),
            SimEventKind.DEPOSIT,
            _summary("10000", held=()),
            None,
        ),
        (
            (MONDAY, Slot.DEC, Phase.DECIDE, 1),
            SimEventKind.ORDER_SUBMITTED,
            _summary("10000", held=(), order=ENTRY_ORDER),
            DecisionDetail(OrderPurpose.ENTRY, None, None),
        ),
        ((MONDAY, Slot.F1, Phase.FILL, 1), SimEventKind.FILLED, held, fill),
        ((MONDAY, Slot.CLOSE, Phase.MARK, 1), SimEventKind.MARKED, held, None),
        ((MONDAY, Slot.CUT, Phase.SNAPSHOT, 1), SimEventKind.SNAPSHOT, held, None),
        (
            (TUESDAY, Slot.OPEN, Phase.SETTLE_DUE, 1),
            SimEventKind.SETTLE_DUE,
            _summary("10088"),
            None,
        ),
        ((TUESDAY, Slot.DEC, Phase.DECIDE, 1), SimEventKind.ORDER_SUBMITTED, live, exit_decision),
        ((TUESDAY, Slot.F1, Phase.FILL, 1), SimEventKind.NOT_FILLED, live, MISSED),
        ((TUESDAY, Slot.F2, Phase.FILL, 1), SimEventKind.NOT_FILLED, live, MISSED),
        ((TUESDAY, Slot.F3, Phase.FILL, 1), SimEventKind.NOT_FILLED, live, last_miss),
        ((TUESDAY, Slot.F3, Phase.FILL, 2), SimEventKind.ORDER_CANCELLED, _summary("10088"), None),
        ((TUESDAY, Slot.CUT, Phase.LIFECYCLE, 1), SimEventKind.SETTLED, settled, settlement),
        ((TUESDAY, Slot.CUT, Phase.SNAPSHOT, 1), SimEventKind.SNAPSHOT, settled, None),
        (
            (WEDNESDAY, Slot.OPEN, Phase.SETTLE_DUE, 1),
            SimEventKind.SETTLE_DUE,
            _summary("9788", held=()),
            None,
        ),
        (
            (WEDNESDAY, Slot.CUT, Phase.SNAPSHOT, 1),
            SimEventKind.SNAPSHOT,
            _summary("9788", held=()),
            None,
        ),
    ]
    return tuple(_event(sessions, *row) for row in rows)


def _point(
    sessions: Mapping[date, TradingSession], day: date, amounts: tuple[str, ...]
) -> AccountPoint:
    """Return a point from cash, receivable, payable, encumbrance, headroom, mid, natural."""
    session = sessions[day]
    close, cut = slot_instant(session, Slot.CLOSE), slot_instant(session, Slot.CUT)
    return AccountPoint(day, close, cut, *(_usd(amount) for amount in amounts))


def _curve(sessions: Mapping[date, TradingSession]) -> tuple[AccountPoint, ...]:
    return (
        _point(sessions, MONDAY, ("10000", "90", "2", "502", "9496", "9883", "9868")),
        _point(sessions, TUESDAY, ("10088", "0", "300", "0", "9788", "9788", "9788")),
        _point(sessions, WEDNESDAY, ("9788", "0", "0", "0", "9788", "9788", "9788")),
    )


def _result() -> SimulationResult:
    return SimulationResult(
        calculation_status=CalculationStatus.VALID,
        data_fidelity=FidelityClass.SYNTHETIC_FIXTURE,
        limitations=(),
        execution_basis="synthetic_natural_package",
        calibration_status="uncalibrated",
        cost_basis="assumed_schedule",
        assignment_basis="not_applicable",
        window_requested=(MONDAY, WEDNESDAY),
        window_simulated=(MONDAY, WEDNESDAY),
        valuation_clock="scheduled_daily_v1",
        initial_equity_usd=_usd("10000"),
        final_equity_usd=_usd("9788"),
        open_positions=(),
        unsettled_cash=(),
        end_policy="liquidate_at_final_session",
        warnings=(
            RunWarning(WarningCode.SYNTHETIC_FIXTURE_NOT_HISTORICAL, MONDAY, "synthetic", ()),
            RunWarning(WarningCode.EXIT_UNFILLED, TUESDAY, "exit cancelled", (EXIT_ID,)),
        ),
        invalid_reasons=(),
        headline_eligible=True,
        provenance=ResultProvenance("m", "0.1.0", (), "c", "p", (), "synthetic_public"),
    )


def _bundle(
    events: tuple[SimEvent, ...], curve: tuple[AccountPoint, ...], result: SimulationResult
) -> ArtifactBundle:
    return ArtifactBundle(
        result=result,
        events=events,
        journal=(),
        positions=(),
        account_curve=curve,
        campaigns=(),
        candidate_decisions=(),
        quality=(),
    )


def _snapshot(kind: str, tag: str, cash: str, tpv: str, held: Held) -> JsonRecord:
    quantities = dict(held)
    return {
        "kind": kind,
        "tag": tag,
        "cash": cash,
        "unsettled_cash": "0.0",
        "total_portfolio_value": tpv,
        "holdings": [
            {"contract": contract, "quantity": str(quantities.get(contract, 0))}
            for contract in (LONG, SHORT)
        ],
    }


def _lean_records() -> list[JsonRecord]:
    """Return LEAN's records of the replay, in output order (positions named above)."""
    base = {"kind": "order_event", "fill_price": "0", "fill_quantity": "0", "fee": "0"}
    monday = {
        **base,
        "utc_time": "2024-03-04T20:46:00",
        "tag": FILL_ID,
        "order_type": "ComboMarket",
        "index_price": "5000",
        "cash": "10000",
    }
    dec = {
        **base,
        "utc_time": "2024-03-05T20:45:00",
        "tag": EXIT_ID,
        "order_type": "ComboLimit",
        "index_price": "5000",
        "cash": "10088.00",
    }
    f3 = {**dec, "utc_time": "2024-03-05T20:48:00"}
    exercise = {
        **base,
        "utc_time": "2024-03-06T06:00:00",
        "tag": None,
        "order_type": "OptionExercise",
        "status": "Filled",
        "index_price": "4997",
    }
    end = _snapshot("end", "end", "9788.00", "9788.00", ())
    return [
        {**monday, "contract": SHORT, "status": "Submitted"},
        {**monday, "contract": LONG, "status": "Submitted"},
        {
            **monday,
            "contract": SHORT,
            "status": "Filled",
            "fill_price": "2.00",
            "fill_quantity": "-1",
            "fee": "1",
            "cash": "10088.00",
        },
        {
            **monday,
            "contract": LONG,
            "status": "Filled",
            "fill_price": "1.1",
            "fill_quantity": "1",
            "fee": "1",
            "cash": "10088.00",
        },
        _snapshot("snapshot", FILL_ID, "10088.00", "9983.00", HELD),
        _snapshot("snapshot", "2024-03-04", "10088.00", "9888.0000", HELD),
        {**dec, "contract": LONG, "status": "Submitted"},
        {**dec, "contract": SHORT, "status": "Submitted"},
        {**f3, "contract": LONG, "status": "CancelPending"},
        {**f3, "contract": SHORT, "status": "CancelPending"},
        {**f3, "contract": LONG, "status": "Canceled"},
        {**f3, "contract": SHORT, "status": "Canceled"},
        _snapshot("snapshot", "2024-03-05", "10088.00", "10018.0000", HELD),
        {**exercise, "contract": LONG, "fill_quantity": "-1", "cash": "10088.00"},
        {**exercise, "contract": SHORT, "fill_quantity": "1", "cash": "9788.00"},
        _snapshot("snapshot", "2024-03-06", "9788.00", "9788.00", ()),
        {**end, "unexecuted": []},
    ]


def _find(
    comparisons: tuple[Comparison, ...], check: Check, at: str, subject: str = ""
) -> Comparison:
    found = [c for c in comparisons if (c.check, c.at, c.subject) == (check, at, subject)]
    assert len(found) == 1, f"{check} {at} {subject}: {found}"
    return found[0]


type Values = tuple[Decimal, Decimal, Decimal, tuple[KnownMismatch, ...]]


def _values(comparison: Comparison) -> Values:
    return comparison.ours, comparison.lean, comparison.expected_difference, comparison.mismatches


def _expected(ours: str, lean: str, difference: str, *mismatches: KnownMismatch) -> Values:
    return Decimal(ours), Decimal(lean), Decimal(difference), mismatches


def _edit(position: int, **fields: object) -> Mutation:
    def apply(records: list[JsonRecord]) -> None:
        records[position] = {**records[position], **fields}

    return apply


def _drop(position: int) -> Mutation:
    def apply(records: list[JsonRecord]) -> None:
        del records[position]

    return apply


def _stray_fill(records: list[JsonRecord]) -> None:
    stray = {
        **records[SHORT_FILL],
        "tag": "stray",
        "utc_time": "2024-03-06T20:46:00",
        "cash": "9788.00",
    }
    records.insert(END, stray)


def test_replay_input_scripts_fills_missed_limits_and_closes(
    bundle: ArtifactBundle, dataset: FrozenDataset
) -> None:
    assert replay_input(bundle, dataset) == {
        "start_date": "2024-03-04",
        "end_date": "2024-03-06",
        "cash": "10000",
        "contracts": [LONG, SHORT],
        "actions": [
            {
                "at": "2024-03-04T15:46:00",
                "kind": "combo_market",
                "tag": FILL_ID,
                "legs": [[SHORT, -1], [LONG, 1]],
                "quantity": 1,
            },
            {"at": "2024-03-04T15:46:00", "kind": "snapshot", "tag": FILL_ID},
            {"at": "2024-03-04T16:00:00", "kind": "snapshot", "tag": "2024-03-04"},
            {
                "at": "2024-03-05T15:45:00",
                "kind": "combo_limit",
                "tag": EXIT_ID,
                "legs": [[LONG, -1], [SHORT, 1]],
                "quantity": 1,
                "limit": "0.8",
            },
            {"at": "2024-03-05T15:48:00", "kind": "cancel", "tag": EXIT_ID},
            {"at": "2024-03-05T16:00:00", "kind": "snapshot", "tag": "2024-03-05"},
            {"at": "2024-03-06T16:00:00", "kind": "snapshot", "tag": "2024-03-06"},
        ],
    }


def test_an_agreeing_replay_leaves_no_difference_unexplained(
    bundle: ArtifactBundle, dataset: FrozenDataset
) -> None:
    comparisons = reconcile(bundle, dataset, _lean_records())

    assert unexplained(comparisons) == ()
    assert {comparison.check for comparison in comparisons} == set(Check)
    fill = _find(comparisons, Check.FILL_PRICE, FILL_ID, SHORT)
    assert _values(fill) == _expected("2.00", "2.00", "0")
    assert _values(_find(comparisons, Check.FILL_FEES, FILL_ID)) == _expected("2", "2", "0")
    total = _find(comparisons, Check.TOTAL_CASH_AFTER_FILL, FILL_ID)
    assert _values(total) == _expected("10088", "10088", "0")
    closing = _find(comparisons, Check.TOTAL_CASH_AT_CLOSE, "2024-03-05")
    assert _values(closing) == _expected("10088", "10088", "0")  # before our CUT settles
    final = _find(comparisons, Check.MID_NLV, "2024-03-06")
    assert _values(final) == _expected("9788", "9788", "0")


def test_m2_our_t_plus_1_dues_are_cash_lean_settled_at_once(
    bundle: ArtifactBundle, dataset: FrozenDataset
) -> None:
    comparisons = reconcile(bundle, dataset, _lean_records())

    after_fill = _find(comparisons, Check.SETTLED_CASH_AFTER_FILL, FILL_ID)
    # our receivable 90 less payable 2 is LEAN's cash already
    assert _values(after_fill) == _expected("10000", "10088", "88", KnownMismatch.M2)
    at_close = _find(comparisons, Check.SETTLED_CASH_AT_CLOSE, "2024-03-05")
    assert _values(at_close) == _expected("10088", "10088", "0")


def test_m13_lean_marks_a_no_bid_leg_at_its_ask(
    bundle: ArtifactBundle, dataset: FrozenDataset
) -> None:
    comparisons = reconcile(bundle, dataset, _lean_records())

    nlv = _find(comparisons, Check.MID_NLV, "2024-03-04")
    # 1·100·(ask 0.10 - mid 0.05)
    assert _values(nlv) == _expected("9883", "9888", "5", KnownMismatch.M13)


def test_m14_lean_marks_the_expiry_close_at_f3_and_exercises_at_one_next_day(
    bundle: ArtifactBundle, dataset: FrozenDataset
) -> None:
    comparisons = reconcile(bundle, dataset, _lean_records())

    nlv = _find(comparisons, Check.MID_NLV, "2024-03-05")
    # 100·(-1·1.15 + 1·0.45) at the F3 bars, less the settlement's -300
    assert _values(nlv) == _expected("9788", "10018", "230", KnownMismatch.M14)
    for contract in (LONG, SHORT):
        exercised = _find(comparisons, Check.EXERCISE_TIME, SETTLED_ID, contract)
        # our CUT 2024-03-05 23:59:59 to LEAN's 2024-03-06 01:00, New York
        assert _values(exercised)[2:] == (Decimal(3601), (KnownMismatch.M14,))
        assert exercised.explained
    quantity = _find(comparisons, Check.EXERCISE_QUANTITY, SETTLED_ID, SHORT)
    assert _values(quantity) == _expected("1", "1", "0")
    value = _find(comparisons, Check.SETTLEMENT_VALUE, SETTLED_ID, SHORT)
    assert _values(value) == _expected("4997", "4997", "0")
    cash = _find(comparisons, Check.SETTLEMENT_CASH, SETTLED_ID)
    assert _values(cash) == _expected("-300", "-300", "0")


@pytest.mark.parametrize(
    ("mutation", "failing"),
    [
        pytest.param(
            _edit(SHORT_FILL, fill_price="2.05"),
            {(Check.FILL_PRICE, FILL_ID, SHORT)},
            id="fill price",
        ),
        pytest.param(
            _drop(LONG_FILL),
            {(Check.FILL_COUNT, FILL_ID, ""), (Check.FILL_FEES, FILL_ID, "")},
            id="leg unfilled",
        ),
        pytest.param(
            _edit(MONDAY_CLOSE, total_portfolio_value="9883.00"),
            {(Check.MID_NLV, "2024-03-04", "")},
            id="no bid at mid",
        ),
        pytest.param(
            _edit(TUESDAY_CLOSE, total_portfolio_value="9788.00"),
            {(Check.MID_NLV, "2024-03-05", "")},
            id="expiry close",
        ),
        pytest.param(
            _edit(MONDAY_CLOSE, cash="10000.00", unsettled_cash="88.00"),
            {(Check.SETTLED_CASH_AT_CLOSE, "2024-03-04", "")},
            id="unsettled",
        ),
        pytest.param(
            _edit(MONDAY_CLOSE, holdings=[{"contract": SHORT, "quantity": "-1"}]),
            {(Check.HOLDING, "2024-03-04", LONG)},
            id="holding",
        ),
        pytest.param(
            _edit(LONG_EXERCISE, utc_time="2024-03-06T05:00:00"),
            {(Check.EXERCISE_TIME, SETTLED_ID, LONG)},
            id="exercise time",
        ),
        pytest.param(
            _edit(SHORT_EXERCISE, cash="9790.00"),
            {(Check.SETTLEMENT_CASH, SETTLED_ID, "")},
            id="settlement cash",
        ),
        pytest.param(
            _edit(LONG_CANCEL, status="Filled"),
            {(Check.DIAGNOSTIC_FILLS, EXIT_ID, ""), (Check.DIAGNOSTIC_CANCELS, EXIT_ID, "")},
            id="limit filled",
        ),
        pytest.param(_stray_fill, {(Check.UNMATCHED_FILLS, RUN, "")}, id="stray fill"),
        pytest.param(
            _edit(END, unexecuted=["snapshot 2024-03-06T16:00:00 2024-03-06"]),
            {(Check.UNEXECUTED_ACTIONS, RUN, "")},
            id="unexecuted",
        ),
        pytest.param(
            _edit(END, cash="9787.00"), {(Check.FINAL_TOTAL_CASH, RUN, "")}, id="final cash"
        ),
    ],
)
def test_a_difference_no_known_mismatch_explains_is_reported(
    bundle: ArtifactBundle,
    dataset: FrozenDataset,
    mutation: Mutation,
    failing: set[tuple[Check, str, str]],
) -> None:
    records = _lean_records()
    mutation(records)

    comparisons = reconcile(bundle, dataset, records)

    assert {(c.check, c.at, c.subject) for c in unexplained(comparisons)} == failing


def test_only_a_valid_run_is_replayed(bundle: ArtifactBundle, dataset: FrozenDataset) -> None:
    invalid = replace(
        bundle.result,
        calculation_status=CalculationStatus.INVALID,
        window_simulated=(MONDAY, TUESDAY),
        final_equity_usd=None,
        headline_eligible=False,
        invalid_reasons=(Issue(ErrorCode.MISSING_VALUATION, "no mark", ""),),
    )
    stopped = _bundle(bundle.events, bundle.account_curve, invalid)

    with pytest.raises(ValueError, match="only a valid run"):
        replay_input(stopped, dataset)
    with pytest.raises(ValueError, match="only a valid run"):
        reconcile(stopped, dataset, _lean_records())


@pytest.mark.parametrize(
    ("exit_order", "last_miss"),
    [
        pytest.param(EXIT_ORDER, replace(MISSED, reason=NonfillReason.CAPACITY), id="capacity"),
        pytest.param(replace(EXIT_ORDER, purpose=OrderPurpose.ROLL_OPEN), MISSED, id="opening"),
        pytest.param(
            replace(EXIT_ORDER, purpose=OrderPurpose.FINAL, limit_usd=None), MISSED, id="final"
        ),
    ],
)
def test_a_cancelled_order_is_replayed_only_as_a_missed_closing_limit(
    sessions: Mapping[date, TradingSession],
    dataset: FrozenDataset,
    exit_order: OrderSnapshot,
    last_miss: NonfillDetail,
) -> None:
    run = _bundle(_events(sessions, exit_order, last_miss), _curve(sessions), _result())

    with pytest.raises(ValueError, match=EXIT_ID):
        replay_input(run, dataset)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        pytest.param(_drop(END), "end record", id="no end"),
        pytest.param(_drop(MONDAY_CLOSE), "no snapshot tagged 2024-03-04", id="no close"),
        pytest.param(_edit(SHORT_FILL, fill_price="two"), "fill_price", id="not a number"),
        pytest.param(_edit(SHORT_FILL, kind="log"), "kind 'log'", id="unknown kind"),
    ],
)
def test_malformed_replay_records_are_refused(
    bundle: ArtifactBundle, dataset: FrozenDataset, mutation: Mutation, message: str
) -> None:
    records = _lean_records()
    mutation(records)

    with pytest.raises(ValueError, match=message):
        reconcile(bundle, dataset, records)


def test_a_held_leg_lean_cannot_mark_is_refused(bundle: ArtifactBundle) -> None:
    unquoted = QuotePin(SHORT, MONDAY, (Slot.CLOSE,), Decimal(0), Decimal(0), 0, 0)
    dataset = generate(_spec((*PINS, unquoted)))

    with pytest.raises(ValueError, match="LEAN has no mark"):
        reconcile(bundle, dataset, _lean_records())


def test_inputs_of_the_wrong_type_are_refused(
    bundle: ArtifactBundle, dataset: FrozenDataset
) -> None:
    with pytest.raises(TypeError, match="bundle"):
        replay_input(bundle.result, dataset)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="dataset"):
        reconcile(bundle, bundle, _lean_records())  # type: ignore[arg-type]
