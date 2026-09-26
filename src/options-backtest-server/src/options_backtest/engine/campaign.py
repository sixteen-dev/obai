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
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from options_backtest.engine.fees import AssumedFlatFeeSchedule
from options_backtest.engine.orders import ExitTrigger, OrderLeg, OrderPurpose
from options_backtest.models.artifacts import (
    CampaignOutcome,
    CampaignRecord,
    DecisionReason,
)
from options_backtest.models.ledger import LedgerEntry
from options_backtest.models.market import Quote
from options_backtest.models.strategy import StrategySpec
from options_backtest.money import Usd


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

    @classmethod
    def initial(cls) -> CampaignState:
        """Return the state before any campaign: zero counts, flat, nothing due."""
        raise NotImplementedError


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

    @property
    def exit_trigger(self) -> ExitTrigger | None:
        """Return the first true of TIME_EXIT, TAKE_PROFIT, STOP_LOSS, CAMPAIGN_CAP, ROLL_CAP."""
        raise NotImplementedError

    @property
    def roll_due(self) -> bool:
        """Return ``roll_trigger and not roll_cap`` (``R1Campaign.RollDue``)."""
        raise NotImplementedError


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
        ValueError: If nothing is held.

    """
    raise NotImplementedError


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

    """
    raise NotImplementedError


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
        ValueError: If a position is held.

    """
    raise NotImplementedError


def opening_campaign_id(state: CampaignState, purpose: OrderPurpose) -> str:
    """Return the generation id an opening order opens.

    Args:
        state: Campaign state.
        purpose: ENTRY gives ``c{number+1}.g1``; ROLL_OPEN gives ``c{number}.g{generation+1}``.

    Returns:
        The generation id.

    Raises:
        ValueError: If ``purpose`` is not opening.

    """
    raise NotImplementedError


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

    """
    raise NotImplementedError


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

    """
    raise NotImplementedError


def settled(state: CampaignState) -> CampaignState:
    """Return the state after the held generation's cash settlement: the campaign ends."""
    raise NotImplementedError


def ended(state: CampaignState) -> CampaignState:
    """Return the state after a due replacement is not opened: the campaign ends flat."""
    raise NotImplementedError


def generation_pnl(entries: Sequence[LedgerEntry], generation_id: str) -> Usd:
    """Return a generation's P&L: -ΣREALIZED_PNL - ΣFEES over entries with its ``campaign_id``.

    Args:
        entries: Journal entries.
        generation_id: ``c{n}.g{k}``.

    Returns:
        The P&L, fees included.

    """
    raise NotImplementedError


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

    """
    raise NotImplementedError
