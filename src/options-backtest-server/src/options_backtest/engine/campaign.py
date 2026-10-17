"""The one-campaign state machine: triggers, decisions and transitions (ADR 0002 §7).

Refines ``R1Campaign``: ``CampaignState`` is its ``campaign`` tuple plus ``pos``; ``decide_held``
and ``decide_flat`` are ``DecideHeld`` and ``DecideFlat``; the transitions are ``FillOpen``,
``FillClose``, ``Settle`` and ``EndCampaign``. Campaign ``c{n}`` counts filled entries from 1;
each filled opening adds generation ``c{n}.g{k}``, the ledger ``campaign_id`` of its lots. An
opening order that never fills consumes no number. Session counts are table sessions, the
fill session being 1 (design §9.1 item 10).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import localcontext
from enum import StrEnum
from typing import Final

from options_backtest.engine.fees import AssumedFlatFeeSchedule, trade_fees
from options_backtest.engine.orders import (
    ExitTrigger,
    OrderLeg,
    OrderPurpose,
    closing_legs,
    natural_price,
    package_debit,
)
from options_backtest.models.artifacts import (
    CampaignOutcome,
    CampaignRecord,
    DecisionReason,
    LiquidationDetail,
)
from options_backtest.models.ledger import AccountKind, LedgerEntry, LegFill
from options_backtest.models.market import Quote
from options_backtest.models.strategy import SequentialRoll, StrategySpec
from options_backtest.money import EXACT, ZERO_USD, Usd
from options_backtest.reference.calendars import dte

_CLOSING: Final = frozenset({OrderPurpose.EXIT, OrderPurpose.ROLL_CLOSE, OrderPurpose.FINAL})


@dataclass(frozen=True, slots=True)
class HeldPosition:
    """The held generation (``R1Campaign.pos``).

    Attributes:
        generation_id: ``c{n}.g{k}``.
        legs: Opening legs, in the strategy's ``leg_order``.
        packages: Packages held.
        expiry: Expiry date of every leg.
        fill_session: Session its opening order filled (held session 1).
        entry_debit_incl_fees: Opening ``D`` plus its trade fees (``R1Campaign.entryDebit``;
            F01: -90 + 2 = -88).

    """

    generation_id: str
    legs: tuple[OrderLeg, ...]
    packages: int
    expiry: date
    fill_session: date
    entry_debit_incl_fees: Usd


@dataclass(frozen=True, slots=True)
class CampaignState:
    """Campaign bookkeeping between events.

    A campaign is active while ``held`` is set or ``roll_open_due``; ending it resets
    ``rolls``, ``basis``, ``realized_prior`` and ``start_session`` and keeps ``number``.

    Attributes:
        number: Filled entries so far; the current or last campaign is ``c{number}``.
        generation: Generations opened in campaign ``c{number}``.
        rolls: Successful replacements in the active campaign.
        roll_open_due: A roll close filled; the replacement is due at the next decision.
        basis: |entry ``D``| of the active campaign, before fees; ZERO when none.
        realized_prior: P&L of the active campaign's closed generations, fees included.
        start_session: Entry fill session of the active campaign (campaign session 1).
        held: The held generation; None when flat.

    """

    number: int
    generation: int
    rolls: int
    roll_open_due: bool
    basis: Usd
    realized_prior: Usd
    start_session: date | None
    held: HeldPosition | None

    def __post_init__(self) -> None:
        """Refuse negative counts and a state that holds while a replacement is due."""
        if min(self.number, self.generation, self.rolls) < 0:
            raise ValueError(f"CampaignState counts must be >= 0: {self}")
        if self.held is not None and self.roll_open_due:
            raise ValueError("CampaignState cannot hold a generation while roll_open_due")

    @classmethod
    def initial(cls) -> CampaignState:
        """Return the state before any campaign: zero counts, flat, nothing due."""
        return cls(0, 0, 0, False, ZERO_USD, ZERO_USD, None, None)


@dataclass(frozen=True, slots=True)
class Triggers:
    """Exit and roll conditions of the held generation at a DEC (ADR 0002 §7).

    Attributes:
        time_exit: ``dte <= exit_dte`` or ``held_sessions >= max_holding_sessions``.
        take_profit: A take-profit rule exists and ``pnl >= fraction · basis``.
        stop_loss: A stop-loss rule exists and ``pnl <= -multiple · basis``.
        campaign_cap: Sequential rolls and ``campaign_sessions >= max_campaign_sessions``.
        roll_trigger: Sequential rolls and ``dte <= trigger_dte``.
        roll_cap: ``roll_trigger`` and ``rolls >= max_rolls``.
        liquidation_pnl: ``realized_prior - entry_debit_incl_fees - close D - exit fees`` at
            the decision naturals; None without a usable quote for every leg.

    """

    time_exit: bool
    take_profit: bool
    stop_loss: bool
    campaign_cap: bool
    roll_trigger: bool
    roll_cap: bool
    liquidation_pnl: Usd | None

    def __post_init__(self) -> None:
        """Refuse a roll cap without a roll trigger and a P&L rule without a P&L."""
        if self.roll_cap and not self.roll_trigger:
            raise ValueError("Triggers.roll_cap needs roll_trigger")
        if (self.take_profit or self.stop_loss) and self.liquidation_pnl is None:
            raise ValueError("Triggers take_profit and stop_loss need a liquidation_pnl")

    @property
    def exit_trigger(self) -> ExitTrigger | None:
        """Return the first true of TIME_EXIT, TAKE_PROFIT, STOP_LOSS, CAMPAIGN_CAP, ROLL_CAP."""
        flags = (
            (self.time_exit, ExitTrigger.TIME_EXIT),
            (self.take_profit, ExitTrigger.TAKE_PROFIT),
            (self.stop_loss, ExitTrigger.STOP_LOSS),
            (self.campaign_cap, ExitTrigger.CAMPAIGN_CAP),
            (self.roll_cap, ExitTrigger.ROLL_CAP),
        )
        return next((trigger for holds, trigger in flags if holds), None)

    @property
    def roll_due(self) -> bool:
        """Return ``roll_trigger and not roll_cap`` (``R1Campaign.RollDue``)."""
        return self.roll_trigger and not self.roll_cap


def evaluate_triggers(  # noqa: PLR0913 — the TLA ExitTrigger inputs, all explicit
    state: CampaignState,
    spec: StrategySpec,
    schedule: AssumedFlatFeeSchedule,
    *,
    session_date: date,
    held_sessions: int,
    campaign_sessions: int,
    close_quotes: Mapping[str, Quote] | None,
) -> Triggers:
    """Evaluate the held generation's triggers at a DEC.

    ``dte = dte(session_date, held.expiry)``. The close ``D`` is ``package_debit(closing_legs(
    held.legs), held.packages, close_quotes)``; exit fees are Σ ``trade_fees`` of those legs.
    Take-profit and stop-loss compare exact ``Decimal`` products of the rule's fraction or
    multiple and ``basis`` (no ``Usd`` product) and are False without ``close_quotes``.

    Args:
        state: Campaign state with a held generation.
        spec: The strategy.
        schedule: Fee schedule.
        session_date: Decision session.
        held_sessions: Table sessions from ``held.fill_session`` to ``session_date``, inclusive.
        campaign_sessions: Table sessions from ``start_session`` to ``session_date``, inclusive.
        close_quotes: A ``usable_quote`` for every held leg's closing side at DEC, or None when
            any leg has none (``quote.ok`` false).

    Returns:
        The triggers.

    Raises:
        ValueError: If nothing is held, ``held_sessions < 1`` or
            ``campaign_sessions < held_sessions``.
        MissingMarkError: If ``close_quotes`` lacks a held leg.

    """
    held = state.held
    if held is None:
        raise ValueError("evaluate_triggers needs a held generation")
    if held_sessions < 1:
        raise ValueError(f"held_sessions counts the fill session as 1, got {held_sessions}")
    if campaign_sessions < held_sessions:
        raise ValueError(f"campaign_sessions {campaign_sessions} < held_sessions {held_sessions}")
    days = dte(session_date, held.expiry)
    exits, roll = spec.exits, spec.roll
    pnl = None
    if close_quotes is not None:
        pnl = liquidation(state, schedule, close_quotes).liquidation_pnl
    take_profit, stop_loss = _pnl_rules(spec, state.basis, pnl)
    sequential = roll if isinstance(roll, SequentialRoll) else None
    roll_trigger = sequential is not None and days <= sequential.trigger_dte
    return Triggers(
        time_exit=days <= exits.exit_dte or held_sessions >= exits.max_holding_sessions,
        take_profit=take_profit,
        stop_loss=stop_loss,
        campaign_cap=(
            sequential is not None and campaign_sessions >= sequential.max_campaign_sessions
        ),
        roll_trigger=roll_trigger,
        roll_cap=roll_trigger and sequential is not None and state.rolls >= sequential.max_rolls,
        liquidation_pnl=pnl,
    )


def _pnl_rules(spec: StrategySpec, basis: Usd, pnl: Usd | None) -> tuple[bool, bool]:
    """Return (take profit, stop loss): ``pnl >= fraction·basis``, ``pnl <= -multiple·basis``.

    The products are exact ``Decimal``s under ``EXACT``; both are False without a P&L.
    """
    if pnl is None:
        return False, False
    take_profit, stop_loss = spec.exits.take_profit, spec.exits.stop_loss
    with localcontext(EXACT):
        profit = take_profit is not None and pnl.amount >= take_profit.fraction * basis.amount
        loss = stop_loss is not None and pnl.amount <= -(stop_loss.multiple * basis.amount)
    return profit, loss


def liquidation(
    state: CampaignState,
    schedule: AssumedFlatFeeSchedule,
    close_quotes: Mapping[str, Quote],
) -> LiquidationDetail:
    """Return the held generation's liquidation P&L at the decision naturals, by component.

    ``realized_prior - entry_debit_incl_fees - close D - exit fees`` (ADR 0002 §17 item 35):
    the close ``D`` is ``package_debit(closing_legs(held.legs), held.packages, close_quotes)``
    and the exit fees are Σ ``trade_fees`` of those legs at their naturals.

    Args:
        state: Campaign state with a held generation.
        schedule: Fee schedule.
        close_quotes: A ``usable_quote`` for every held leg's closing side at DEC.

    Returns:
        The P&L and its components (design §10.4: "Store each component").

    Raises:
        ValueError: If nothing is held.
        MissingMarkError: If ``close_quotes`` lacks a held leg.

    """
    held = state.held
    if held is None:
        raise ValueError("liquidation needs a held generation")
    legs = closing_legs(held.legs)
    close_debit = package_debit(legs, held.packages, close_quotes)
    fills = [
        LegFill(
            leg.terms,
            leg.ratio * held.packages,
            natural_price(close_quotes[leg.terms.contract_id], leg.ratio),
        )
        for leg in legs
    ]
    exit_fees = sum((line.amount for line in trade_fees(schedule, fills)), start=ZERO_USD)
    return LiquidationDetail(
        realized_prior=state.realized_prior,
        entry_debit_incl_fees=held.entry_debit_incl_fees,
        close_debit=close_debit,
        exit_fees=exit_fees,
        basis=state.basis,
        liquidation_pnl=state.realized_prior - held.entry_debit_incl_fees - close_debit - exit_fees,
    )


class HeldAction(StrEnum):
    """Outcome of a decision while a position is held."""

    FINAL = "final"
    EXIT = "exit"
    ROLL_CLOSE = "roll_close"
    DEFER = "defer"
    HOLD = "hold"


@dataclass(frozen=True, slots=True)
class HeldDecision:
    """A held-position decision.

    Attributes:
        action: What to do.
        trigger: FINAL_LIQUIDATION for FINAL; the exit trigger for EXIT or a deferred exit;
            None otherwise.

    """

    action: HeldAction
    trigger: ExitTrigger | None


def decide_held(
    triggers: Triggers, *, final_session: bool, liquidate_at_final: bool, quote_ok: bool
) -> HeldDecision:
    """Decide for a held position (``R1Campaign.DecideHeld``), first match wins.

    FINAL on the final session under ``liquidate_at_final_session`` (no quote needed); EXIT if
    an exit trigger holds and ``quote_ok``; ROLL_CLOSE if no exit trigger, ``roll_due`` and
    ``quote_ok``; DEFER if an exit trigger or ``roll_due`` holds without ``quote_ok``
    (EXIT_DEFERRED, re-evaluated next decision); else HOLD. Under ``mark_open_positions`` the
    final session is decided like any other.

    Args:
        triggers: Triggers at this DEC.
        final_session: Whether this is the window's final session.
        liquidate_at_final: Whether the end policy is ``liquidate_at_final_session``.
        quote_ok: Whether every held leg has a usable closing quote at DEC.

    Returns:
        The decision.

    Raises:
        TypeError: If ``triggers`` is not ``Triggers``.

    """
    if not isinstance(triggers, Triggers):
        raise TypeError(f"decide_held needs Triggers, got {type(triggers).__name__}")
    if final_session and liquidate_at_final:
        return HeldDecision(HeldAction.FINAL, ExitTrigger.FINAL_LIQUIDATION)
    trigger = triggers.exit_trigger
    if trigger is not None:
        return HeldDecision(HeldAction.EXIT if quote_ok else HeldAction.DEFER, trigger)
    if triggers.roll_due:
        return HeldDecision(HeldAction.ROLL_CLOSE if quote_ok else HeldAction.DEFER, None)
    return HeldDecision(HeldAction.HOLD, None)


class FlatAction(StrEnum):
    """Outcome of a decision while flat."""

    ENTRY = "entry"
    ROLL_OPEN = "roll_open"
    END_CAMPAIGN = "end_campaign"
    SKIP = "skip"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class FlatDecision:
    """A flat decision; ENTRY and ROLL_OPEN are attempts the selector may still refuse.

    Attributes:
        action: What to attempt.
        reason: For END_CAMPAIGN and SKIP, why; None otherwise.

    """

    action: FlatAction
    reason: DecisionReason | None


def decide_flat(
    state: CampaignState,
    spec: StrategySpec,
    *,
    scheduled: bool,
    final_session: bool,
    campaign_sessions: int,
) -> FlatDecision:
    """Decide while flat (``R1Campaign.DecideFlat``), first match wins.

    With ``roll_open_due``: END_CAMPAIGN (FINAL_SESSION) on the final session; END_CAMPAIGN
    (CAMPAIGN_CAP) once ``campaign_sessions >= max_campaign_sessions``; else ROLL_OPEN,
    whatever the schedule. Otherwise, when scheduled: SKIP (FINAL_SESSION) on the final
    session, else ENTRY; when not scheduled: IDLE (no event).

    Args:
        state: Campaign state with nothing held.
        spec: The strategy.
        scheduled: ``reference.calendars.scheduled`` for this session.
        final_session: Whether this is the window's final session.
        campaign_sessions: Table sessions from ``start_session`` to this session, inclusive;
            ignored without ``roll_open_due``.

    Returns:
        The decision.

    Raises:
        ValueError: If a position is held, or a replacement is due without sequential rolls or
            with ``campaign_sessions < 1``.

    """
    if state.held is not None:
        raise ValueError("decide_flat needs a flat state; a generation is held")
    if state.roll_open_due:
        return _decide_replacement(spec, final_session, campaign_sessions)
    if not scheduled:
        return FlatDecision(FlatAction.IDLE, None)
    if final_session:
        return FlatDecision(FlatAction.SKIP, DecisionReason.FINAL_SESSION)
    return FlatDecision(FlatAction.ENTRY, None)


def _decide_replacement(
    spec: StrategySpec, final_session: bool, campaign_sessions: int
) -> FlatDecision:
    """Decide a due replacement: end on the final session or at the cap, else ROLL_OPEN."""
    roll = spec.roll
    if not isinstance(roll, SequentialRoll):
        raise ValueError("a replacement is due only under roll mode sequential")
    if campaign_sessions < 1:
        raise ValueError(f"campaign_sessions counts the entry session as 1: {campaign_sessions}")
    if final_session:
        return FlatDecision(FlatAction.END_CAMPAIGN, DecisionReason.FINAL_SESSION)
    if campaign_sessions >= roll.max_campaign_sessions:
        return FlatDecision(FlatAction.END_CAMPAIGN, DecisionReason.CAMPAIGN_CAP)
    return FlatDecision(FlatAction.ROLL_OPEN, None)


def opening_campaign_id(state: CampaignState, purpose: OrderPurpose) -> str:
    """Return the generation id an opening order opens.

    Args:
        state: Campaign state.
        purpose: ENTRY gives ``c{number+1}.g1``; ROLL_OPEN gives ``c{number}.g{generation+1}``.

    Returns:
        The generation id.

    Raises:
        ValueError: If ``purpose`` is not opening, a generation is held, or ``purpose`` is
            ROLL_OPEN without ``roll_open_due`` (ENTRY with it).

    """
    if not purpose.opening:
        raise ValueError(f"opening_campaign_id needs an opening purpose, got {purpose}")
    if state.held is not None:
        raise ValueError("no opening while a generation is held")
    rolling = purpose is OrderPurpose.ROLL_OPEN
    if rolling != state.roll_open_due:
        raise ValueError(f"{purpose} needs roll_open_due {rolling}, state has {not rolling}")
    if rolling:
        return f"c{state.number}.g{state.generation + 1}"
    return f"c{state.number + 1}.g1"


def open_filled(  # noqa: PLR0913 — every FillOpen input, explicit
    state: CampaignState,
    *,
    purpose: OrderPurpose,
    legs: tuple[OrderLeg, ...],
    packages: int,
    expiry: date,
    fill_session: date,
    net_debit: Usd,
    fees: Usd,
) -> CampaignState:
    """Return the state after an opening fill (``R1Campaign.FillOpen``).

    ENTRY starts campaign ``number+1`` at generation 1 with ``basis = |net_debit|``,
    ``realized_prior = 0``, ``rolls = 0``, ``start_session = fill_session``. ROLL_OPEN adds
    generation ``generation+1``, ``rolls+1``, keeping basis, realized and start. Both clear
    ``roll_open_due`` and hold ``HeldPosition(entry_debit_incl_fees = net_debit + fees)``.

    Args:
        state: Flat state.
        purpose: ENTRY or ROLL_OPEN.
        legs: The order's legs.
        packages: Packages filled.
        expiry: The legs' expiry date.
        fill_session: Session of the fill.
        net_debit: Whole-order ``D`` at the fill, before fees.
        fees: Σ trade fees of the fill.

    Returns:
        The new state.

    Raises:
        ValueError: As ``opening_campaign_id``; also for ``packages < 1``, no legs, negative
            fees or a zero ``net_debit`` (BasisPositive).

    """
    generation_id = opening_campaign_id(state, purpose)
    if type(packages) is not int or packages < 1:
        raise ValueError(f"open_filled packages must be an int >= 1, got {packages!r}")
    if not legs or fees.amount < 0:
        raise ValueError(f"open_filled needs legs and fees >= 0, got {len(legs)} and {fees}")
    if net_debit == ZERO_USD:
        raise ValueError("an opening fill needs a nonzero net_debit (BasisPositive)")
    held = HeldPosition(generation_id, legs, packages, expiry, fill_session, net_debit + fees)
    if purpose is OrderPurpose.ROLL_OPEN:
        return replace(
            state,
            generation=state.generation + 1,
            rolls=state.rolls + 1,
            roll_open_due=False,
            held=held,
        )
    basis = net_debit if net_debit.amount > 0 else -net_debit
    return CampaignState(state.number + 1, 1, 0, False, basis, ZERO_USD, fill_session, held)


def _end(state: CampaignState) -> CampaignState:
    """Return the flat state after the active campaign ends: only number and generation kept."""
    return replace(CampaignState.initial(), number=state.number, generation=state.generation)


def close_filled(
    state: CampaignState, *, purpose: OrderPurpose, generation_pnl: Usd
) -> CampaignState:
    """Return the state after a closing fill (``R1Campaign.FillClose``).

    ROLL_CLOSE: flat, ``roll_open_due``, ``realized_prior += generation_pnl``. EXIT or FINAL:
    the campaign ends.

    Args:
        state: State holding a generation.
        purpose: EXIT, ROLL_CLOSE or FINAL.
        generation_pnl: ``generation_pnl`` of the closed generation.

    Returns:
        The new state.

    Raises:
        ValueError: If ``purpose`` is not closing or nothing is held.

    """
    if purpose not in _CLOSING:
        raise ValueError(f"close_filled needs a closing purpose, got {purpose}")
    if state.held is None:
        raise ValueError("close_filled needs a held generation")
    if purpose is OrderPurpose.ROLL_CLOSE:
        return replace(
            state,
            held=None,
            roll_open_due=True,
            realized_prior=state.realized_prior + generation_pnl,
        )
    return _end(state)


def settled(state: CampaignState) -> CampaignState:
    """Return the state after the held generation's cash settlement: the campaign ends.

    Raises:
        ValueError: If nothing is held.

    """
    if state.held is None:
        raise ValueError("settled needs a held generation")
    return _end(state)


def ended(state: CampaignState) -> CampaignState:
    """Return the state after a due replacement is not opened: the campaign ends flat.

    Raises:
        ValueError: Without ``roll_open_due``.

    """
    if not state.roll_open_due:
        raise ValueError("ended needs roll_open_due: only a due replacement ends unopened")
    return _end(state)


def generation_pnl(entries: Sequence[LedgerEntry], generation_id: str) -> Usd:
    """Return a generation's P&L: -ΣREALIZED_PNL - ΣFEES over entries with its ``campaign_id``.

    Args:
        entries: Journal entries.
        generation_id: ``c{n}.g{k}``.

    Returns:
        The P&L, fees included.

    """
    realized, fees = _realized_and_fees(entries, {generation_id})
    return realized - fees


def _realized_and_fees(entries: Sequence[LedgerEntry], generation_ids: set[str]) -> tuple[Usd, Usd]:
    """Return (-ΣREALIZED_PNL, ΣFEES) over the postings of entries of ``generation_ids``."""
    postings = [
        posting
        for entry in entries
        if entry.campaign_id in generation_ids
        for posting in entry.postings
    ]
    realized = sum(
        (p.amount for p in postings if p.account.kind is AccountKind.REALIZED_PNL), start=ZERO_USD
    )
    fees = sum((p.amount for p in postings if p.account.kind is AccountKind.FEES), start=ZERO_USD)
    return -realized, fees


def campaign_record(
    state: CampaignState,
    entries: Sequence[LedgerEntry],
    *,
    outcome: CampaignOutcome,
    end_session: date | None,
    exit_trigger: ExitTrigger | None,
) -> CampaignRecord:
    """Return the record of the active campaign; call it before the state that ends it.

    Args:
        state: State of the active campaign (``c{number}``, generations ``g1..g{generation}``).
        entries: Journal entries.
        outcome: How it ended.
        end_session: Session its last position was closed or settled; None if OPEN or
            INCOMPLETE.
        exit_trigger: As ``CampaignRecord.exit_trigger``.

    Returns:
        The record.

    Raises:
        ValueError: If no campaign is active, or the record's own checks fail.

    """
    active = state.held is not None or state.roll_open_due
    if not active or state.start_session is None:
        raise ValueError("campaign_record needs an active campaign")
    campaign_id = f"c{state.number}"
    generations = tuple(f"{campaign_id}.g{k}" for k in range(1, state.generation + 1))
    realized, fees = _realized_and_fees(entries, set(generations))
    return CampaignRecord(
        campaign_id=campaign_id,
        generations=generations,
        start_session=state.start_session,
        end_session=end_session,
        basis=state.basis,
        realized_pnl=realized,
        fees=fees,
        rolls=state.rolls,
        outcome=outcome,
        exit_trigger=exit_trigger,
    )
