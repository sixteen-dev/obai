"""The one-campaign state machine refines ``R1Campaign`` (ADR 0002 §7, §17 items 34-36, 42).

Numbers are the goldens': G01 (credit vertical, take-profit), G04 (debit vertical, stop-loss),
G08a/b (roll cap, campaign cap), G09 (a linked roll) and G12 (two packages).
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from selection_builders import (
    EXPIRY,
    FRI,
    LATER_EXPIRY,
    MON,
    SCHEDULE,
    THU,
    TUE,
    WED,
    call_id,
    call_leg,
    deposited,
    leg,
    put_id,
    put_leg,
    quote,
    same_as,
    strategy,
    target,
    usd,
)

from options_backtest.engine.campaign import (
    CampaignState,
    FlatAction,
    FlatDecision,
    HeldAction,
    HeldDecision,
    Triggers,
    campaign_record,
    close_filled,
    decide_flat,
    decide_held,
    ended,
    evaluate_triggers,
    generation_pnl,
    open_filled,
    opening_campaign_id,
    settled,
)
from options_backtest.engine.fees import trade_fees
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.orders import ExitTrigger, OrderLeg, OrderPurpose
from options_backtest.engine.trades import book_option_trade
from options_backtest.errors import MissingMarkError
from options_backtest.models.artifacts import CampaignOutcome, CampaignRecord, DecisionReason
from options_backtest.models.ledger import LedgerEntry, LedgerState, LegFill
from options_backtest.models.market import Quote
from options_backtest.models.strategy import StrategySpec
from options_backtest.money import ZERO_USD, Price

ENTRY, ROLL_OPEN = OrderPurpose.ENTRY, OrderPurpose.ROLL_OPEN
EXIT, ROLL_CLOSE, FINAL = OrderPurpose.EXIT, OrderPurpose.ROLL_CLOSE, OrderPurpose.FINAL
A_LEGS = (put_leg("4900", -1), put_leg("4895", 1))
B_LEGS = (put_leg("4900", -1, LATER_EXPIRY), put_leg("4895", 1, LATER_EXPIRY))
DEBIT_EXPIRY = date(2024, 3, 22)
DEBIT_LEGS = (call_leg("5100", 1, DEBIT_EXPIRY), call_leg("5105", -1, DEBIT_EXPIRY))
MAR_11, MAR_12, MAR_13 = date(2024, 3, 11), date(2024, 3, 12), date(2024, 3, 13)
SEQUENTIAL = {"mode": "sequential", "trigger_dte": 8, "max_rolls": 1, "max_campaign_sessions": 20}
TRIGGER_ORDER = (
    ExitTrigger.TIME_EXIT,
    ExitTrigger.TAKE_PROFIT,
    ExitTrigger.STOP_LOSS,
    ExitTrigger.CAMPAIGN_CAP,
    ExitTrigger.ROLL_CAP,
)


def _spec(**patch: Any) -> StrategySpec:
    return strategy(**patch).spec


def _entered(  # noqa: PLR0913 — one keyword per FillOpen input a test varies
    legs: tuple[OrderLeg, ...] = A_LEGS,
    *,
    debit: str = "-90",
    fees: str = "2",
    packages: int = 1,
    expiry: date = EXPIRY,
    day: date = MON,
) -> CampaignState:
    return open_filled(
        CampaignState.initial(),
        purpose=ENTRY,
        legs=legs,
        packages=packages,
        expiry=expiry,
        fill_session=day,
        net_debit=usd(debit),
        fees=usd(fees),
    )


def _rolled(generation_pnl_usd: str = "-14") -> CampaignState:
    return close_filled(_entered(), purpose=ROLL_CLOSE, generation_pnl=usd(generation_pnl_usd))


def _a_quotes(p4900: tuple[str, str], p4895: tuple[str, str]) -> dict[str, Quote]:
    return {put_id(4900): quote(*p4900), put_id(4895): quote(*p4895)}


def _triggers(  # noqa: PLR0913 — one keyword per evaluate_triggers input a test varies
    state: CampaignState,
    spec: StrategySpec,
    *,
    day: date = TUE,
    held: int = 2,
    campaign: int = 2,
    quotes: Mapping[str, Quote] | None = None,
) -> Triggers:
    return evaluate_triggers(
        state,
        spec,
        SCHEDULE,
        session_date=day,
        held_sessions=held,
        campaign_sessions=campaign,
        close_quotes=quotes,
    )


# --- state and opening ----------------------------------------------------------------------------


def test_the_initial_state_is_flat_with_no_campaign() -> None:
    state = CampaignState.initial()

    assert (state.number, state.generation, state.rolls) == (0, 0, 0)
    assert (state.roll_open_due, state.held, state.start_session) == (False, None, None)
    assert (state.basis, state.realized_prior) == (ZERO_USD, ZERO_USD)


def test_an_entry_fill_starts_campaign_1_with_its_basis_and_entry_debit() -> None:
    state = _entered()

    assert (state.number, state.generation, state.rolls, state.roll_open_due) == (1, 1, 0, False)
    assert (state.basis, state.realized_prior, state.start_session) == (usd("90"), ZERO_USD, MON)
    assert state.held is not None
    assert (state.held.generation_id, state.held.entry_debit_incl_fees) == ("c1.g1", usd("-88"))
    assert (state.held.legs, state.held.packages) == (A_LEGS, 1)
    assert (state.held.expiry, state.held.fill_session) == (EXPIRY, MON)


def test_a_debit_entry_takes_the_debit_as_its_basis() -> None:
    state = _entered(DEBIT_LEGS, debit="70", expiry=DEBIT_EXPIRY)

    assert state.basis == usd("70")
    assert state.held is not None
    assert state.held.entry_debit_incl_fees == usd("72")


def test_the_basis_is_the_whole_order_premium() -> None:
    state = _entered(debit="-180", fees="4", packages=2)  # G12

    assert state.basis == usd("180")
    assert state.held is not None
    assert (state.held.packages, state.held.entry_debit_incl_fees) == (2, usd("-176"))


def test_opening_ids_count_filled_entries_and_generations() -> None:
    assert opening_campaign_id(CampaignState.initial(), ENTRY) == "c1.g1"
    assert opening_campaign_id(_rolled(), ROLL_OPEN) == "c1.g2"
    assert opening_campaign_id(ended(_rolled()), ENTRY) == "c2.g1"
    assert (
        opening_campaign_id(close_filled(_entered(), purpose=EXIT, generation_pnl=ZERO_USD), ENTRY)
        == "c2.g1"
    )


def test_an_unfilled_opening_consumes_no_number() -> None:
    # An entry that never fills leaves the state as it was: the next attempt is c1.g1 again.
    state = CampaignState.initial()

    assert opening_campaign_id(state, ENTRY) == opening_campaign_id(state, ENTRY) == "c1.g1"


def test_opening_ids_refuse_a_closing_purpose_and_the_wrong_opening() -> None:
    with pytest.raises(ValueError, match="opening"):
        opening_campaign_id(CampaignState.initial(), EXIT)
    with pytest.raises(ValueError, match="roll_open_due"):
        opening_campaign_id(CampaignState.initial(), ROLL_OPEN)
    with pytest.raises(ValueError, match="roll_open_due"):
        opening_campaign_id(_rolled(), ENTRY)
    with pytest.raises(ValueError, match="held"):
        opening_campaign_id(_entered(), ENTRY)


def test_an_opening_fill_refuses_a_held_state_a_zero_premium_and_no_packages() -> None:
    fill: dict[str, Any] = {
        "purpose": ENTRY,
        "legs": A_LEGS,
        "packages": 1,
        "expiry": EXPIRY,
        "fill_session": MON,
        "net_debit": usd("-90"),
        "fees": usd("2"),
    }

    with pytest.raises(ValueError, match="held"):
        open_filled(_entered(), **fill)
    with pytest.raises(ValueError, match="BasisPositive"):
        open_filled(CampaignState.initial(), **{**fill, "net_debit": ZERO_USD})
    with pytest.raises(ValueError, match="packages"):
        open_filled(CampaignState.initial(), **{**fill, "packages": 0})
    with pytest.raises(ValueError, match="roll_open_due"):
        open_filled(CampaignState.initial(), **{**fill, "purpose": ROLL_OPEN})
    with pytest.raises(ValueError, match="roll_open_due"):
        open_filled(_rolled(), **fill)


# --- closing, settlement, ending ------------------------------------------------------------------


def test_a_roll_close_goes_flat_with_the_replacement_due_and_the_pnl_linked() -> None:
    state = _rolled("-14")

    assert (state.held, state.roll_open_due) == (None, True)
    assert (state.number, state.generation, state.rolls) == (1, 1, 0)
    assert (state.basis, state.realized_prior, state.start_session) == (usd("90"), usd("-14"), MON)


def test_a_replacement_fill_adds_a_generation_and_keeps_the_campaign() -> None:
    state = open_filled(
        _rolled("-14"),
        purpose=ROLL_OPEN,
        legs=B_LEGS,
        packages=1,
        expiry=LATER_EXPIRY,
        fill_session=FRI,
        net_debit=usd("-120"),
        fees=usd("2"),
    )

    assert (state.number, state.generation, state.rolls, state.roll_open_due) == (1, 2, 1, False)
    assert (state.basis, state.realized_prior, state.start_session) == (usd("90"), usd("-14"), MON)
    assert state.held is not None
    assert (state.held.generation_id, state.held.fill_session) == ("c1.g2", FRI)
    assert state.held.entry_debit_incl_fees == usd("-118")


@pytest.mark.parametrize("purpose", [EXIT, FINAL])
def test_an_exit_or_final_fill_ends_the_campaign(purpose: OrderPurpose) -> None:
    state = close_filled(_entered(), purpose=purpose, generation_pnl=usd("6"))

    assert state == replace(CampaignState.initial(), number=1, generation=1)


def test_settlement_ends_the_campaign_and_so_does_a_replacement_not_opened() -> None:
    finished = replace(CampaignState.initial(), number=1, generation=1)

    assert settled(_entered()) == finished
    assert ended(_rolled()) == finished


def test_transitions_refuse_a_state_they_do_not_apply_to() -> None:
    flat = CampaignState.initial()

    with pytest.raises(ValueError, match="held"):
        close_filled(flat, purpose=EXIT, generation_pnl=ZERO_USD)
    with pytest.raises(ValueError, match="closing"):
        close_filled(_entered(), purpose=ENTRY, generation_pnl=ZERO_USD)
    with pytest.raises(ValueError, match="held"):
        settled(flat)
    with pytest.raises(ValueError, match="roll_open_due"):
        ended(flat)
    with pytest.raises(ValueError, match="roll_open_due"):
        ended(_entered())


def test_a_campaign_state_cannot_hold_and_await_a_replacement_at_once() -> None:
    held = _entered()

    with pytest.raises(ValueError, match="roll_open_due"):
        replace(held, roll_open_due=True)


# --- triggers -------------------------------------------------------------------------------------


def test_liquidation_pnl_is_g01s_and_takes_profit_above_the_fraction() -> None:
    spec = _spec(exits={"take_profit": {"basis": "initial_credit", "fraction": 0.05}})

    triggers = _triggers(_entered(), spec, quotes=_a_quotes(("1.00", "1.20"), ("0.40", "0.50")))

    # 0 - (-90 + 2) - (120 - 40) - 2 = 6 >= 0.05 x 90 = 4.5
    assert triggers.liquidation_pnl == usd("6")
    assert triggers.take_profit
    assert triggers.exit_trigger is ExitTrigger.TAKE_PROFIT


def test_exit_fees_and_the_close_debit_scale_with_the_packages_held() -> None:
    spec = _spec(exits={"take_profit": {"basis": "initial_credit", "fraction": 0.05}})
    state = _entered(debit="-180", fees="4", packages=2)

    triggers = _triggers(state, spec, quotes=_a_quotes(("1.00", "1.20"), ("0.40", "0.50")))

    # G12: 0 + 176 - (240 - 80) - 4 = 12 >= 0.05 x 180 = 9
    assert (triggers.liquidation_pnl, triggers.take_profit) == (usd("12"), True)


def test_take_profit_holds_at_exact_equality() -> None:
    spec = _spec(exits={"take_profit": {"basis": "initial_credit", "fraction": 0.5}})
    at_threshold = _a_quotes(("0.60", "0.81"), ("0.40", "0.50"))
    one_cent_short = _a_quotes(("0.60", "0.8101"), ("0.40", "0.50"))

    reached = _triggers(_entered(), spec, quotes=at_threshold)
    missed = _triggers(_entered(), spec, quotes=one_cent_short)

    # 88 - (81 - 40) - 2 = 45 = 0.5 x 90
    assert (reached.liquidation_pnl, reached.take_profit) == (usd("45"), True)
    assert (missed.liquidation_pnl, missed.take_profit) == (usd("44.99"), False)


def test_stop_loss_holds_at_exact_equality() -> None:
    spec = _spec(exits={"stop_loss": {"basis": "initial_credit", "multiple": 1}})
    at_threshold = _a_quotes(("2.00", "2.16"), ("0.40", "0.50"))
    one_cent_short = _a_quotes(("2.00", "2.1599"), ("0.40", "0.50"))

    reached = _triggers(_entered(), spec, quotes=at_threshold)
    missed = _triggers(_entered(), spec, quotes=one_cent_short)

    # 88 - (216 - 40) - 2 = -90 = -1 x 90
    assert (reached.liquidation_pnl, reached.stop_loss) == (usd("-90"), True)
    assert (missed.liquidation_pnl, missed.stop_loss) == (usd("-89.99"), False)
    assert reached.exit_trigger is ExitTrigger.STOP_LOSS


def test_a_debit_campaign_stops_out_on_its_debit_basis() -> None:
    debit = _spec(
        legs=[
            leg("long_call", "buy", "call", moneyness=1.02, expiry=target(17, 15, 19)),
            leg(
                "short_call", "sell", "call", expiry=same_as("long_call"), offset=("long_call", "5")
            ),
        ],
        exits={"exit_dte": 14, "stop_loss": {"basis": "initial_debit", "multiple": 0.5}},
    )
    quotes = {
        call_id(5100, DEBIT_EXPIRY): quote("0.80", "0.90"),
        call_id(5105, DEBIT_EXPIRY): quote("0.50", "0.60"),
    }

    triggers = _triggers(
        _entered(DEBIT_LEGS, debit="70", expiry=DEBIT_EXPIRY), debit, quotes=quotes
    )

    # 0 - 72 - (-80 + 60) - 2 = -54 <= -0.5 x 70 = -35; dte(03-05, 03-22) = 17 > 14
    assert triggers.liquidation_pnl == usd("-54")
    assert (triggers.stop_loss, triggers.time_exit) == (True, False)


def test_profit_and_loss_rules_need_decision_quotes() -> None:
    spec = _spec(
        exits={
            "take_profit": {"basis": "initial_credit", "fraction": 0.01},
            "stop_loss": {"basis": "initial_credit", "multiple": 0.01},
        }
    )

    triggers = _triggers(_entered(), spec, quotes=None)

    assert triggers.liquidation_pnl is None
    assert (triggers.take_profit, triggers.stop_loss, triggers.exit_trigger) == (False, False, None)


def test_disabled_rules_never_trigger() -> None:
    quotes = _a_quotes(("0.00", "0.05"), ("0.40", "0.50"))

    triggers = _triggers(_entered(), _spec(), quotes=quotes)

    # 0 + 88 - (5 - 40) - 2 = 121: a large profit, and no rule to take it
    assert triggers.liquidation_pnl == usd("121")
    assert (triggers.take_profit, triggers.stop_loss, triggers.exit_trigger) == (False, False, None)


def test_a_close_quote_is_needed_for_every_held_leg() -> None:
    with pytest.raises(MissingMarkError):
        _triggers(_entered(), _spec(), quotes={put_id(4900): quote("1.00", "1.20")})


def test_time_exit_at_exit_dte_and_at_max_holding_sessions() -> None:
    spec = _spec(exits={"exit_dte": 3, "max_holding_sessions": 3})
    state = _entered()

    assert _triggers(state, spec, day=MAR_12).time_exit  # dte 3 <= 3
    assert not _triggers(state, spec, day=MAR_11).time_exit  # dte 4
    assert _triggers(state, spec, held=3, campaign=3).time_exit  # the fill session is 1
    assert not _triggers(state, spec, held=2, campaign=2).time_exit
    assert _triggers(state, _spec(), day=EXPIRY).time_exit  # dte 0 <= exit_dte 0


def test_the_campaign_cap_counts_campaign_sessions_under_sequential_rolls_only() -> None:
    capped = _spec(roll={**SEQUENTIAL, "max_campaign_sessions": 5})

    assert _triggers(_entered(), capped, held=5, campaign=5).campaign_cap
    assert not _triggers(_entered(), capped, held=4, campaign=4).campaign_cap
    assert not _triggers(_entered(), _spec(), held=5, campaign=700).campaign_cap


def test_a_roll_is_due_below_max_rolls_and_capped_at_it() -> None:
    spec = _spec(roll=SEQUENTIAL)
    fresh = _entered()
    rolled_once = replace(fresh, rolls=1)

    due = _triggers(fresh, spec, day=THU)  # dte(2024-03-07, 2024-03-15) = 8 <= 8
    capped = _triggers(rolled_once, spec, day=THU)
    early = _triggers(fresh, spec, day=WED)  # dte 9

    assert (due.roll_trigger, due.roll_due, due.roll_cap, due.exit_trigger) == (
        True,
        True,
        False,
        None,
    )
    assert (capped.roll_due, capped.roll_cap, capped.exit_trigger) == (
        False,
        True,
        ExitTrigger.ROLL_CAP,
    )
    assert (early.roll_trigger, early.roll_due) == (False, False)
    assert not _triggers(fresh, _spec(), day=THU).roll_trigger


def test_g08a_the_roll_trigger_after_max_rolls_exits_with_roll_cap() -> None:
    replaced = open_filled(
        _rolled(),
        purpose=ROLL_OPEN,
        legs=B_LEGS,
        packages=1,
        expiry=LATER_EXPIRY,
        fill_session=FRI,
        net_debit=usd("-120"),
        fees=usd("2"),
    )

    triggers = _triggers(replaced, _spec(roll=SEQUENTIAL), day=MAR_13, held=4, campaign=8)

    assert (triggers.time_exit, triggers.campaign_cap, triggers.roll_cap) == (False, False, True)
    assert (triggers.exit_trigger, triggers.roll_due) == (ExitTrigger.ROLL_CAP, False)


@pytest.mark.parametrize("first", range(len(TRIGGER_ORDER)))
def test_an_exit_carries_the_first_true_trigger(first: int) -> None:
    flags = [index >= first for index in range(len(TRIGGER_ORDER))]
    triggers = Triggers(
        time_exit=flags[0],
        take_profit=flags[1],
        stop_loss=flags[2],
        campaign_cap=flags[3],
        roll_trigger=flags[4],
        roll_cap=flags[4],
        liquidation_pnl=usd("0"),
    )

    assert triggers.exit_trigger is TRIGGER_ORDER[first]
    assert not triggers.roll_due


def test_no_trigger_means_no_exit() -> None:
    triggers = Triggers(False, False, False, False, False, False, None)

    assert (triggers.exit_trigger, triggers.roll_due) == (None, False)


def test_triggers_refuse_inconsistent_flags() -> None:
    with pytest.raises(ValueError, match="roll_cap"):
        Triggers(False, False, False, False, False, True, None)
    with pytest.raises(ValueError, match="liquidation_pnl"):
        Triggers(False, True, False, False, False, False, None)
    with pytest.raises(ValueError, match="liquidation_pnl"):
        Triggers(False, False, True, False, False, False, None)


def test_triggers_refuse_a_flat_state_and_impossible_session_counts() -> None:
    with pytest.raises(ValueError, match="held"):
        _triggers(CampaignState.initial(), _spec())
    with pytest.raises(ValueError, match="held_sessions"):
        _triggers(_entered(), _spec(), held=0, campaign=0)
    with pytest.raises(ValueError, match="campaign_sessions"):
        _triggers(_entered(), _spec(), held=3, campaign=2)


# --- decisions ------------------------------------------------------------------------------------


def _held_triggers(*, exit_now: bool = False, roll_due: bool = False) -> Triggers:
    return Triggers(exit_now, False, False, False, roll_due, False, None)


@pytest.mark.parametrize("quote_ok", [True, False])
def test_final_liquidation_needs_no_quote_and_beats_every_trigger(quote_ok: bool) -> None:
    decision = decide_held(
        _held_triggers(exit_now=True, roll_due=True),
        final_session=True,
        liquidate_at_final=True,
        quote_ok=quote_ok,
    )

    assert decision == HeldDecision(HeldAction.FINAL, ExitTrigger.FINAL_LIQUIDATION)


def test_mark_open_positions_decides_the_final_session_like_any_other() -> None:
    hold = decide_held(
        _held_triggers(), final_session=True, liquidate_at_final=False, quote_ok=True
    )
    exit_ = decide_held(
        _held_triggers(exit_now=True), final_session=True, liquidate_at_final=False, quote_ok=True
    )

    assert hold == HeldDecision(HeldAction.HOLD, None)
    assert exit_ == HeldDecision(HeldAction.EXIT, ExitTrigger.TIME_EXIT)


@pytest.mark.parametrize(
    ("exit_now", "roll_due", "quote_ok", "expected"),
    [
        (True, False, True, HeldDecision(HeldAction.EXIT, ExitTrigger.TIME_EXIT)),
        (True, False, False, HeldDecision(HeldAction.DEFER, ExitTrigger.TIME_EXIT)),
        (True, True, True, HeldDecision(HeldAction.EXIT, ExitTrigger.TIME_EXIT)),
        (True, True, False, HeldDecision(HeldAction.DEFER, ExitTrigger.TIME_EXIT)),
        (False, True, True, HeldDecision(HeldAction.ROLL_CLOSE, None)),
        (False, True, False, HeldDecision(HeldAction.DEFER, None)),
        (False, False, True, HeldDecision(HeldAction.HOLD, None)),
        (False, False, False, HeldDecision(HeldAction.HOLD, None)),
    ],
)
def test_held_decisions_put_exit_before_roll_and_defer_without_quotes(
    exit_now: bool, roll_due: bool, quote_ok: bool, expected: HeldDecision
) -> None:
    decision = decide_held(
        _held_triggers(exit_now=exit_now, roll_due=roll_due),
        final_session=False,
        liquidate_at_final=True,
        quote_ok=quote_ok,
    )

    assert decision == expected


def _flat(
    state: CampaignState, spec: StrategySpec, *, scheduled: bool, final: bool, sessions: int = 1
) -> FlatDecision:
    return decide_flat(
        state, spec, scheduled=scheduled, final_session=final, campaign_sessions=sessions
    )


@pytest.mark.parametrize("scheduled", [True, False])
def test_a_due_replacement_skips_the_schedule(scheduled: bool) -> None:
    spec = _spec(roll={**SEQUENTIAL, "max_campaign_sessions": 5})

    decision = _flat(_rolled(), spec, scheduled=scheduled, final=False, sessions=4)

    assert decision == FlatDecision(FlatAction.ROLL_OPEN, None)


def test_a_due_replacement_ends_the_campaign_at_the_cap_and_on_the_final_session() -> None:
    spec = _spec(roll={**SEQUENTIAL, "max_campaign_sessions": 5})
    cap = FlatDecision(FlatAction.END_CAMPAIGN, DecisionReason.CAMPAIGN_CAP)
    final = FlatDecision(FlatAction.END_CAMPAIGN, DecisionReason.FINAL_SESSION)

    assert _flat(_rolled(), spec, scheduled=True, final=False, sessions=5) == cap  # G08b
    assert _flat(_rolled(), spec, scheduled=False, final=True, sessions=2) == final
    assert _flat(_rolled(), spec, scheduled=True, final=True, sessions=9) == final


def test_a_scheduled_entry_is_skipped_on_the_final_session() -> None:
    initial, spec = CampaignState.initial(), _spec()

    assert _flat(initial, spec, scheduled=True, final=False) == FlatDecision(FlatAction.ENTRY, None)
    assert _flat(initial, spec, scheduled=True, final=True) == FlatDecision(
        FlatAction.SKIP, DecisionReason.FINAL_SESSION
    )
    assert _flat(initial, spec, scheduled=False, final=False) == FlatDecision(FlatAction.IDLE, None)
    assert _flat(initial, spec, scheduled=False, final=True) == FlatDecision(FlatAction.IDLE, None)


def test_flat_decisions_refuse_a_held_state_and_an_impossible_replacement() -> None:
    with pytest.raises(ValueError, match="held"):
        _flat(_entered(), _spec(), scheduled=True, final=False)
    with pytest.raises(ValueError, match="sequential"):
        _flat(_rolled(), _spec(), scheduled=True, final=False)
    with pytest.raises(ValueError, match="campaign_sessions"):
        _flat(_rolled(), _spec(roll=SEQUENTIAL), scheduled=True, final=False, sessions=0)


# --- P&L from the journal and campaign records ----------------------------------------------------


def _fills(legs: Sequence[OrderLeg], prices: Sequence[str], sign: int) -> tuple[LegFill, ...]:
    return tuple(
        LegFill(leg_.terms, sign * leg_.ratio, Price(Decimal(price)))
        for leg_, price in zip(legs, prices, strict=True)
    )


def _book(
    state: LedgerState, event_id: str, campaign_id: str, fills: tuple[LegFill, ...], day: date
) -> tuple[LedgerEntry, LedgerState]:
    entry = book_option_trade(
        state,
        event_id=event_id,
        at_ns=state.last_at_ns + 1,
        campaign_id=campaign_id,
        legs=fills,
        fees=trade_fees(SCHEDULE, fills),
        settles_on=day,
    )
    return entry, apply_entry(state, entry)


def _g09_journal() -> tuple[LedgerEntry, ...]:
    """G09: c1.g1 opens A at -90 and roll-closes at 100; c1.g2 opens B at -120, exits at 50."""
    entries = []
    state = deposited()
    steps = (
        ("e1", "c1.g1", _fills(A_LEGS, ("2.00", "1.10"), 1), TUE),
        ("e2", "c1.g1", _fills(A_LEGS, ("1.40", "0.40"), -1), FRI),
        ("e3", "c1.g2", _fills(B_LEGS, ("2.40", "1.20"), 1), MAR_11),
        ("e4", "c1.g2", _fills(B_LEGS, ("0.90", "0.40"), -1), MAR_13),
    )
    for event_id, generation, fills, day in steps:
        entry, state = _book(state, event_id, generation, fills, day)
        entries.append(entry)
    return tuple(entries)


def test_generation_pnl_is_minus_realized_minus_fees_of_its_own_entries() -> None:
    state = deposited()
    opened, state = _book(state, "e1", "c1.g1", _fills(A_LEGS, ("2.00", "1.10"), 1), TUE)
    closed, state = _book(state, "e2", "c1.g1", _fills(A_LEGS, ("1.20", "0.40"), -1), WED)
    other, _ = _book(state, "e3", "c2.g1", _fills(A_LEGS, ("2.00", "1.10"), 1), THU)

    assert generation_pnl((opened, closed, other), "c1.g1") == usd("6")  # G01: 10006 - 10000
    assert generation_pnl((opened, closed, other), "c2.g1") == usd("-2")
    assert generation_pnl((), "c1.g1") == ZERO_USD


def test_a_linked_roll_judges_the_replacement_on_the_campaign_basis_as_g09() -> None:
    journal = _g09_journal()
    spec = _spec(
        roll=SEQUENTIAL, exits={"take_profit": {"basis": "initial_credit", "fraction": 0.5}}
    )
    rolled = close_filled(
        _entered(), purpose=ROLL_CLOSE, generation_pnl=generation_pnl(journal[:2], "c1.g1")
    )
    replaced = open_filled(
        rolled,
        purpose=ROLL_OPEN,
        legs=B_LEGS,
        packages=1,
        expiry=LATER_EXPIRY,
        fill_session=FRI,
        net_debit=usd("-120"),
        fees=usd("2"),
    )
    b_quotes = {
        put_id(4900, LATER_EXPIRY): quote("0.70", "0.90"),
        put_id(4895, LATER_EXPIRY): quote("0.40", "0.50"),
    }
    hold_quotes = {**b_quotes, put_id(4900, LATER_EXPIRY): quote("0.80", "1.00")}

    held_on_11th = _triggers(replaced, spec, day=MAR_11, held=2, campaign=6, quotes=hold_quotes)
    exit_on_12th = _triggers(replaced, spec, day=MAR_12, held=3, campaign=7, quotes=b_quotes)

    assert rolled.realized_prior == usd("-14")
    # -14 + 118 - 60 - 2 = 42 < 45; -14 + 118 - 50 - 2 = 52 >= 45
    assert (held_on_11th.liquidation_pnl, held_on_11th.take_profit) == (usd("42"), False)
    assert (exit_on_12th.liquidation_pnl, exit_on_12th.exit_trigger) == (
        usd("52"),
        ExitTrigger.TAKE_PROFIT,
    )


def test_a_campaign_record_links_its_generations_as_g09() -> None:
    journal = _g09_journal()
    replaced = open_filled(
        _rolled(),
        purpose=ROLL_OPEN,
        legs=B_LEGS,
        packages=1,
        expiry=LATER_EXPIRY,
        fill_session=FRI,
        net_debit=usd("-120"),
        fees=usd("2"),
    )

    record = campaign_record(
        replaced,
        journal,
        outcome=CampaignOutcome.CLOSED,
        end_session=MAR_12,
        exit_trigger=ExitTrigger.TAKE_PROFIT,
    )

    assert record == CampaignRecord(
        campaign_id="c1",
        generations=("c1.g1", "c1.g2"),
        start_session=MON,
        end_session=MAR_12,
        basis=usd("90"),
        realized_pnl=usd("60"),  # -(+10 - 70)
        fees=usd("8"),
        rolls=1,
        outcome=CampaignOutcome.CLOSED,
        exit_trigger=ExitTrigger.TAKE_PROFIT,
    )
    assert record.net_pnl == usd("52")


def test_a_replacement_not_opened_closes_the_campaign_as_g08b() -> None:
    journal = _g09_journal()[:2]
    rolled = close_filled(
        _entered(), purpose=ROLL_CLOSE, generation_pnl=generation_pnl(journal, "c1.g1")
    )

    record = campaign_record(
        rolled,
        journal,
        outcome=CampaignOutcome.CLOSED,
        end_session=THU,
        exit_trigger=ExitTrigger.ROLL_NOT_REOPENED,
    )

    assert (record.generations, record.rolls, record.start_session) == (("c1.g1",), 0, MON)
    assert (record.realized_pnl, record.fees, record.net_pnl) == (usd("-10"), usd("4"), usd("-14"))


def test_an_open_campaign_is_recorded_without_an_end() -> None:
    state = deposited()
    opened, _ = _book(state, "e1", "c1.g1", _fills(A_LEGS, ("2.00", "1.10"), 1), TUE)

    record = campaign_record(
        _entered(),
        (opened,),
        outcome=CampaignOutcome.INCOMPLETE,
        end_session=None,
        exit_trigger=None,
    )

    assert (record.end_session, record.exit_trigger) == (None, None)
    assert (record.realized_pnl, record.fees, record.net_pnl) == (ZERO_USD, usd("2"), usd("-2"))


def test_a_campaign_record_needs_an_active_campaign() -> None:
    with pytest.raises(ValueError, match="campaign"):
        campaign_record(
            CampaignState.initial(),
            (),
            outcome=CampaignOutcome.CLOSED,
            end_session=MON,
            exit_trigger=ExitTrigger.TIME_EXIT,
        )
    with pytest.raises(ValueError, match="campaign"):
        campaign_record(
            settled(_entered()),
            (),
            outcome=CampaignOutcome.SETTLED,
            end_session=EXPIRY,
            exit_trigger=ExitTrigger.SETTLEMENT,
        )
