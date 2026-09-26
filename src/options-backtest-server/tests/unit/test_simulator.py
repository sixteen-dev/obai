"""The R1 event loop: slot program, event keys, invariants and the result (ADR 0002 §7, §17).

Markets are the e2e default over five sessions (2024-03-04 Monday to 2024-03-08), spot 5000.00,
DF 1, one expiry 2024-03-15. The strategy is the SPXW put credit vertical (short 4900, long
4895) entered on Mondays. G01's pins make the numbers hand-checkable: entry D = -90, fees 2;
exit D = 80, fees 2; so the account ends at 10006.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from selection_builders import (
    LATER_EXPIRY,
    MON,
    THU,
    TUE,
    WED,
    dataset,
    leg,
    pin,
    put_id,
    strategy,
    target,
)

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import CoverageState
from options_backtest.engine import simulator
from options_backtest.engine.campaign import CampaignState
from options_backtest.engine.orders import ExitTrigger, OrderPurpose
from options_backtest.engine.simulator import run
from options_backtest.engine.trades import book_deposit
from options_backtest.errors import ErrorCode, SimulationInvariantError
from options_backtest.models.artifacts import (
    ArtifactBundle,
    CampaignOutcome,
    DecisionDetail,
    DecisionReason,
    LiquidationDetail,
    QualityCode,
    SimEventKind,
)
from options_backtest.models.ledger import LedgerEntry, LedgerState
from options_backtest.models.result import CalculationStatus, WarningCode
from options_backtest.models.run import resolve
from options_backtest.models.strategy_checks import ValidatedStrategy
from options_backtest.money import Usd
from options_backtest.reference.calendars import Slot
from options_backtest.synthetic.market import CoverageStatus, QuoteDrop, QuotePin

SHORT, LONG = put_id(4900), put_id(4895)
WEEKLY: dict[str, Any] = {
    "schedule": {"frequency": "weekly", "weekday": 1, "holiday_policy": "next_session_same_week"}
}
TAKE_PROFIT: dict[str, Any] = {"take_profit": {"basis": "initial_credit", "fraction": 0.05}}
FILL_SLOTS = (Slot.F1, Slot.F2, Slot.F3)


def weekly(**patch: Any) -> ValidatedStrategy:
    """Return the put credit vertical entered on Mondays, merge-patched."""
    return strategy(entry=WEEKLY, **patch)


def rolling(**patch: Any) -> ValidatedStrategy:
    """Return the weekly vertical rolling at dte <= 10, expiries in [9, 16] (MON: only 03-15).

    The 2024-03-15 package's roll trigger holds from TUE (dte 10); a replacement must have
    dte > 10, which from WED is only 2024-03-21 (dte 15).
    """
    short = leg("short_put", "sell", "put", expiry=target(11, 9, 16), moneyness=0.98)
    long = {
        **leg("long_put", "buy", "put", offset=("short_put", "-5")),
        "expiry_selection": {"method": "same_as", "anchor_leg_id": "short_put"},
    }
    roll = {"mode": "sequential", "trigger_dte": 10, "max_rolls": 1, "max_campaign_sessions": 20}
    return weekly(roll=roll, legs=[short, long], **patch)


def entry_pins(slots: tuple[Slot, ...] = (Slot.DEC, Slot.F1)) -> tuple[QuotePin, ...]:
    """Return G01's Monday entry quotes: P4900 2.00/2.20, P4895 1.00/1.10."""
    return (
        pin(SHORT, "2.00", "2.20", slots=slots),
        pin(LONG, "1.00", "1.10", slots=slots),
    )


def exit_pins(
    day: date = TUE, slots: tuple[Slot, ...] = (Slot.DEC, Slot.F1)
) -> tuple[QuotePin, ...]:
    """Return G01's exit quotes: P4900 1.00/1.20, P4895 0.40/0.50 (close D = 80)."""
    return (
        pin(SHORT, "1.00", "1.20", day=day, slots=slots),
        pin(LONG, "0.40", "0.50", day=day, slots=slots),
    )


def run_on(
    chosen: ValidatedStrategy, frozen: FrozenDataset, start: date = MON, end: date = WED
) -> ArtifactBundle:
    resolved = resolve(
        chosen, start_date=start, end_date=end, manifest_id=frozen.manifest.manifest_id
    )
    return run(resolved, frozen)


def ids_and_kinds(bundle: ArtifactBundle) -> list[tuple[str, SimEventKind]]:
    return [(event.event_id, event.kind) for event in bundle.events]


def round_trip() -> ArtifactBundle:
    """G01 in miniature: enter Monday, take profit Tuesday, flat on the final Wednesday."""
    frozen = dataset(*entry_pins(), *exit_pins())
    return run_on(weekly(exits=TAKE_PROFIT), frozen)


# --- setup -------------------------------------------------------------------------------------


def test_run_refuses_a_dataset_other_than_the_resolved_manifest() -> None:
    chosen = weekly()
    resolved = resolve(chosen, start_date=MON, end_date=WED, manifest_id="0" * 64)
    with pytest.raises(ValueError, match="manifest"):
        run(resolved, dataset())


def test_run_refuses_a_window_with_no_session_after_it() -> None:
    frozen = dataset()
    with pytest.raises(ValueError, match="T\\+1"):
        run_on(weekly(), frozen, end=date(2024, 3, 8))


def test_run_refuses_arguments_of_the_wrong_type() -> None:
    frozen = dataset()
    resolved = resolve(
        weekly(), start_date=MON, end_date=WED, manifest_id=frozen.manifest.manifest_id
    )
    with pytest.raises(TypeError, match="ResolvedRun"):
        run(object(), frozen)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="FrozenDataset"):
        run(resolved, object())  # type: ignore[arg-type]


def test_the_deposit_opens_the_run_and_the_synthetic_warning_comes_first() -> None:
    bundle = run_on(weekly(), dataset())
    first = bundle.events[0]
    assert (first.event_id, first.kind) == ("2024-03-04:OPEN:1:1", SimEventKind.DEPOSIT)
    assert first.summary.cash == Usd(Decimal("10000.00"))
    assert bundle.journal[0].event_id == first.event_id
    warning = bundle.result.warnings[0]
    assert warning.code is WarningCode.SYNTHETIC_FIXTURE_NOT_HISTORICAL
    assert (warning.session_date, warning.refs) == (MON, (bundle.result.provenance.manifest_id,))


# --- slot program and event keys ----------------------------------------------------------------


def test_a_round_trip_runs_the_slot_program_in_order() -> None:
    assert ids_and_kinds(round_trip()) == [
        ("2024-03-04:OPEN:1:1", SimEventKind.DEPOSIT),
        ("2024-03-04:DEC:5:1", SimEventKind.ORDER_SUBMITTED),
        ("2024-03-04:F1:4:1", SimEventKind.FILLED),
        ("2024-03-04:CLOSE:3:1", SimEventKind.MARKED),
        ("2024-03-04:CUT:7:1", SimEventKind.SNAPSHOT),
        ("2024-03-05:OPEN:1:1", SimEventKind.SETTLE_DUE),
        ("2024-03-05:DEC:3:1", SimEventKind.MARKED),
        ("2024-03-05:DEC:5:1", SimEventKind.ORDER_SUBMITTED),
        ("2024-03-05:F1:4:1", SimEventKind.FILLED),
        ("2024-03-05:CUT:7:1", SimEventKind.SNAPSHOT),
        ("2024-03-06:OPEN:1:1", SimEventKind.SETTLE_DUE),
        ("2024-03-06:CUT:7:1", SimEventKind.SNAPSHOT),
    ]


def test_event_keys_strictly_increase_and_every_entry_is_booked_by_its_event() -> None:
    bundle = round_trip()
    keys = [(event.at_ns, int(event.phase), event.seq) for event in bundle.events]
    assert all(earlier < later for earlier, later in zip(keys, keys[1:], strict=False))
    for event in bundle.events:
        spelled = f"{event.session_date}:{event.slot}:{int(event.phase)}:{event.seq}"
        assert event.event_id == spelled
    event_ids = {event.event_id for event in bundle.events}
    assert all(entry.event_id in event_ids for entry in bundle.journal)


def test_a_round_trip_books_g01s_numbers_and_ends_headline_eligible() -> None:
    bundle = round_trip()
    fills = [event for event in bundle.events if event.kind is SimEventKind.FILLED]
    assert [event.campaign_id for event in fills] == ["c1.g1", "c1.g1"]
    assert [event.input_refs for event in fills][0] == (
        f"q:{SHORT}:2024-03-04:F1",
        f"q:{LONG}:2024-03-04:F1",
    )
    result = bundle.result
    assert result.calculation_status is CalculationStatus.VALID
    assert result.final_equity_usd == Usd(Decimal("10006.00"))
    assert result.headline_eligible
    assert (result.open_positions, result.unsettled_cash) == ((), ())
    assert [point.session_date for point in bundle.account_curve] == [MON, TUE, WED]
    (record,) = bundle.campaigns
    assert (record.outcome, record.net_pnl) == (CampaignOutcome.CLOSED, Usd(Decimal("6.00")))
    (decision,) = bundle.candidate_decisions
    assert decision.decision_id == "2024-03-04:DEC:5:1"


def test_a_held_dec_mark_stores_each_liquidation_pnl_component() -> None:
    # Tuesday: 0 realized - (entry D -90 + fees 2) - close D 80 - exit fees 2 = 6 >= 0.05 x 90.
    marks = [e for e in round_trip().events if e.kind is SimEventKind.MARKED]
    assert [(e.slot, e.detail) for e in marks] == [
        (Slot.CLOSE, None),
        (
            Slot.DEC,
            LiquidationDetail(
                realized_prior=Usd(Decimal(0)),
                entry_debit_incl_fees=Usd(Decimal(-88)),
                close_debit=Usd(Decimal(80)),
                exit_fees=Usd(Decimal(2)),
                basis=Usd(Decimal(90)),
                liquidation_pnl=Usd(Decimal(6)),
            ),
        ),
    ]


def test_an_unfilled_opening_is_tried_at_f1_f2_f3_then_cancelled_without_a_warning() -> None:
    worse = (  # D = -190 + 110 = -80 > limit -90 at every fill slot
        pin(SHORT, "1.90", "2.20", slots=FILL_SLOTS),
        pin(LONG, "1.00", "1.10", slots=FILL_SLOTS),
    )
    bundle = run_on(weekly(), dataset(*entry_pins((Slot.DEC,)), *worse), end=TUE)
    monday = [event for event in bundle.events if event.session_date == MON]
    assert [(event.event_id, event.kind) for event in monday] == [
        ("2024-03-04:OPEN:1:1", SimEventKind.DEPOSIT),
        ("2024-03-04:DEC:5:1", SimEventKind.ORDER_SUBMITTED),
        ("2024-03-04:F1:4:1", SimEventKind.NOT_FILLED),
        ("2024-03-04:F2:4:1", SimEventKind.NOT_FILLED),
        ("2024-03-04:F3:4:1", SimEventKind.NOT_FILLED),
        ("2024-03-04:F3:4:2", SimEventKind.ORDER_CANCELLED),
        ("2024-03-04:CUT:7:1", SimEventKind.SNAPSHOT),
    ]
    orders = [event.summary.order for event in monday]
    assert all(order is not None for order in orders[1:5])
    assert orders[5] is None
    assert [warning.code for warning in bundle.result.warnings] == [
        WarningCode.SYNTHETIC_FIXTURE_NOT_HISTORICAL
    ]
    assert bundle.campaigns == ()


def test_a_final_liquidation_cancelled_after_f3_is_disclosed_and_the_run_is_incomplete() -> None:
    missing = QuoteDrop(SHORT, TUE, FILL_SLOTS)
    bundle = run_on(weekly(), dataset(*entry_pins(), missing), end=TUE)
    submitted = next(
        event
        for event in bundle.events
        if event.kind is SimEventKind.ORDER_SUBMITTED and event.session_date == TUE
    )
    assert submitted.summary.order is not None
    assert submitted.summary.order.purpose is OrderPurpose.FINAL
    assert submitted.summary.order.limit_usd is None
    assert [warning.code for warning in bundle.result.warnings] == [
        WarningCode.SYNTHETIC_FIXTURE_NOT_HISTORICAL,
        WarningCode.EXIT_UNFILLED,
    ]
    assert bundle.result.warnings[1].refs == ("o:2024-03-05:final",)
    result = bundle.result
    assert result.calculation_status is CalculationStatus.INCOMPLETE
    assert result.invalid_reasons[0].message.startswith("incomplete_liquidation")
    assert result.open_positions == ((LONG, 1), (SHORT, -1))
    assert bundle.campaigns[0].outcome is CampaignOutcome.INCOMPLETE


# --- rolls ------------------------------------------------------------------------------------


def test_a_replacement_cancelled_after_f3_ends_the_campaign_at_its_roll_close() -> None:
    # TUE: the roll close fills at G01's exit pins. WED: the 03-21 replacement is selected at
    # DEC from generated quotes, but its short leg has no quote at F1-F3 (NO_OBSERVATION).
    missing = QuoteDrop(put_id(4900, LATER_EXPIRY), WED, FILL_SLOTS)
    bundle = run_on(
        rolling(), dataset(*entry_pins(), *exit_pins(), missing, weekly_dtes=(11, 17)), end=THU
    )
    wednesday = [
        (event.event_id, event.kind) for event in bundle.events if event.session_date == WED
    ]
    assert wednesday == [
        ("2024-03-06:OPEN:1:1", SimEventKind.SETTLE_DUE),
        ("2024-03-06:DEC:5:1", SimEventKind.ORDER_SUBMITTED),
        ("2024-03-06:F1:4:1", SimEventKind.NOT_FILLED),
        ("2024-03-06:F2:4:1", SimEventKind.NOT_FILLED),
        ("2024-03-06:F3:4:1", SimEventKind.NOT_FILLED),
        ("2024-03-06:F3:4:2", SimEventKind.ORDER_CANCELLED),
        ("2024-03-06:F3:4:3", SimEventKind.CAMPAIGN_ENDED),
        ("2024-03-06:CUT:7:1", SimEventKind.SNAPSHOT),
    ]
    ended = bundle.events[[e.event_id for e in bundle.events].index("2024-03-06:F3:4:3")]
    assert ended.campaign_id == "c1.g1"
    assert ended.detail == DecisionDetail(
        OrderPurpose.ROLL_OPEN, DecisionReason.ROLL_OPEN_CANCELLED, None
    )
    (record,) = bundle.campaigns
    assert (record.outcome, record.end_session, record.exit_trigger, record.generations) == (
        CampaignOutcome.CLOSED,
        TUE,
        ExitTrigger.ROLL_NOT_REOPENED,
        ("c1.g1",),
    )
    assert [warning.code for warning in bundle.result.warnings] == [
        WarningCode.SYNTHETIC_FIXTURE_NOT_HISTORICAL
    ]
    assert bundle.result.calculation_status is CalculationStatus.VALID


def test_a_roll_close_on_the_final_session_under_mark_open_positions_ends_closed() -> None:
    # Deviation (reported): no decision follows the final session's roll close, so the valid
    # run ends flat with the replacement due; the record closes at the roll close.
    frozen = dataset(*entry_pins(), *exit_pins(), weekly_dtes=(11, 17))
    bundle = run_on(rolling(end_policy="mark_open_positions"), frozen, end=TUE)
    result = bundle.result
    assert (result.calculation_status, result.open_positions) == (CalculationStatus.VALID, ())
    assert result.headline_eligible
    (record,) = bundle.campaigns
    assert (record.outcome, record.end_session, record.exit_trigger) == (
        CampaignOutcome.CLOSED,
        TUE,
        ExitTrigger.ROLL_NOT_REOPENED,
    )


# --- validity -----------------------------------------------------------------------------------


def test_an_unknown_chain_partition_invalidates_at_dec_and_stops_the_loop() -> None:
    unknown = CoverageStatus("quotes", MON, CoverageState.UNKNOWN, "feed outage")
    bundle = run_on(weekly(), dataset(unknown))
    assert ids_and_kinds(bundle) == [
        ("2024-03-04:OPEN:1:1", SimEventKind.DEPOSIT),
        ("2024-03-04:DEC:2:1", SimEventKind.INVALIDATED),
    ]
    assert bundle.account_curve == ()
    result = bundle.result
    assert result.calculation_status is CalculationStatus.INVALID
    assert result.window_simulated == (MON, MON)
    (issue,) = result.invalid_reasons
    assert (issue.code, issue.json_pointer, issue.affected_interval) == (
        ErrorCode.DATA_COVERAGE_GAP,
        "",
        "2024-03-04",
    )
    assert bundle.events[-1].detail == issue
    assert (result.final_equity_usd, result.headline_eligible) == (None, False)


def test_a_missing_close_mark_invalidates_and_keeps_the_exposure_and_dues() -> None:
    no_mark = QuoteDrop(LONG, MON, (Slot.CLOSE,))  # latest is F3's, 720 s old at CLOSE
    bundle = run_on(weekly(), dataset(*entry_pins(), no_mark))
    last = bundle.events[-1]
    assert (last.event_id, last.kind) == ("2024-03-04:CLOSE:3:1", SimEventKind.INVALIDATED)
    result = bundle.result
    assert result.invalid_reasons[0].code is ErrorCode.MISSING_VALUATION
    assert result.open_positions == ((LONG, 1), (SHORT, -1))
    assert result.unsettled_cash == (
        ("2024-03-05", Usd(Decimal("-2.00"))),
        ("2024-03-05", Usd(Decimal("90.00"))),
    )
    (record,) = bundle.campaigns
    assert (record.outcome, record.end_session, record.exit_trigger) == (
        CampaignOutcome.INCOMPLETE,
        None,
        None,
    )


def test_a_close_mark_outside_the_payoff_range_is_a_finding_never_clamped() -> None:
    # Package value at the CLOSE mids: 100 * (-1.00 + 2.00) = +100, above a credit vertical's 0.
    inverted = (
        pin(SHORT, "0.95", "1.05", slots=(Slot.CLOSE,)),
        pin(LONG, "1.95", "2.05", slots=(Slot.CLOSE,)),
    )
    bundle = run_on(weekly(), dataset(*entry_pins(), *inverted), end=TUE)
    (finding,) = bundle.quality
    assert (finding.code, finding.session_date) == (QualityCode.MARK_OUT_OF_RANGE, MON)
    monday = bundle.account_curve[0]
    # 10000 + 90 - 2 + 100: the unclamped mark.
    assert monday.mid_nlv == Usd(Decimal("10188.00"))


# --- after the window ---------------------------------------------------------------------------


def test_dues_after_the_window_settle_in_a_settle_only_session() -> None:
    frozen = dataset(*entry_pins(), *exit_pins(slots=(Slot.F1,)))
    bundle = run_on(weekly(), frozen, end=TUE)
    wednesday = [event for event in bundle.events if event.session_date == WED]
    assert [(event.event_id, event.kind) for event in wednesday] == [
        ("2024-03-06:OPEN:1:1", SimEventKind.SETTLE_DUE),
        ("2024-03-06:CUT:7:1", SimEventKind.SNAPSHOT),
    ]
    assert [point.session_date for point in bundle.account_curve] == [MON, TUE, WED]
    assert bundle.account_curve[-1].cash == Usd(Decimal("10006.00"))
    result = bundle.result
    assert (result.window_simulated, result.final_equity_usd) == (
        (MON, TUE),
        Usd(Decimal("10006.00")),
    )


# --- invariants: engine defects fail the job ----------------------------------------------------


def test_negative_headroom_after_a_commit_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def overdrawn(state: LedgerState, schedule: object) -> Usd:
        return Usd(Decimal("-0.01"))

    monkeypatch.setattr(simulator, "funding_headroom", overdrawn)
    with pytest.raises(SimulationInvariantError, match="FullyFunded"):
        run_on(weekly(), dataset())


def test_a_position_the_campaign_does_not_hold_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def forgetful(state: CampaignState, **_: object) -> CampaignState:
        return state

    monkeypatch.setattr(simulator, "open_filled", forgetful)
    with pytest.raises(SimulationInvariantError, match="ReserveMatchesPosition"):
        run_on(weekly(), dataset(*entry_pins()))


def test_a_ledger_rejection_of_the_engines_own_entry_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def replayed(state: LedgerState, **_: object) -> LedgerEntry:
        return book_deposit(event_id="2024-03-04:OPEN:1:1", at_ns=0, cash=Usd(Decimal("1.00")))

    monkeypatch.setattr(simulator, "book_settle_due", replayed)
    with pytest.raises(SimulationInvariantError, match="rejected the engine's own entry"):
        run_on(weekly(), dataset())


def test_dues_left_after_the_settle_only_sessions_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def never_due(state: LedgerState, **_: object) -> None:
        return None

    monkeypatch.setattr(simulator, "book_settle_due", never_due)
    frozen = dataset(*entry_pins(), *exit_pins(slots=(Slot.F1,)))
    with pytest.raises(SimulationInvariantError, match="settle-only"):
        run_on(weekly(), frozen, end=TUE)
