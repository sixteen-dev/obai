"""The R1 fill model: natural-price, all-or-none package fills after submission (ADR 0002 §9).

``try_fill`` runs the checks in order and reports the first failure; a fill it returns is
already booked, previewed and funded, ready for ``Journal.commit``. The one funding check is at
once the opening rule, design §11.2's closing rule and ``R1Campaign.FillFunded``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from options_backtest.data.asof import AsOfView
from options_backtest.data.records import QuoteObservation
from options_backtest.engine.fees import AssumedFlatFeeSchedule
from options_backtest.engine.orders import NonfillReason, Order
from options_backtest.models.ledger import FeeLine, LedgerEntry, LedgerState, LegFill
from options_backtest.models.strategy_checks import PremiumDirection
from options_backtest.money import Usd


class QuoteSide(StrEnum):
    """The displayed side an order leg consumes: ASK for buys, BID for sells."""

    BID = "bid"
    ASK = "ask"


@dataclass(frozen=True, slots=True)
class CapacityBook:
    """Displayed size already consumed per (quote observation, side) in a run.

    Immutable: ``consume`` returns a new book. Keyed by observation id, so one book serves a
    whole run and no displayed size is ever used twice (design §10.3).

    Attributes:
        consumed: Contracts consumed per (observation_id, side).

    """

    consumed: Mapping[tuple[str, QuoteSide], int]

    @classmethod
    def empty(cls) -> CapacityBook:
        """Return a book with nothing consumed."""
        raise NotImplementedError

    def remaining(self, observation: QuoteObservation, side: QuoteSide) -> int:
        """Return the side's displayed size less what earlier fills consumed, >= 0.

        Args:
            observation: The quote observation.
            side: Side used.

        Returns:
            Remaining contracts.

        """
        raise NotImplementedError

    def consume(self, fill: Fill) -> CapacityBook:
        """Return the book after a fill consumed ``packages · |ratio_i|`` on each leg's side.

        Args:
            fill: A committed fill.

        Returns:
            The new book.

        Raises:
            ValueError: If the fill consumes more than remains.

        """
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class FillContext:
    """Per-attempt inputs of ``try_fill``.

    Attributes:
        event_id: Event id of the FILLED event; the ledger entry's id.
        at_ns: The fill slot's instant.
        settles_on: The next table session (premium and fees settle T+1).
        schedule: Fee schedule.
        participation_fraction: ``execution.participation_fraction``.
        premium_direction: The strategy's declared opening direction.

    """

    event_id: str
    at_ns: int
    settles_on: date
    schedule: AssumedFlatFeeSchedule
    participation_fraction: Decimal
    premium_direction: PremiumDirection


@dataclass(frozen=True, slots=True)
class Fill:
    """A funded fill, booked but not committed.

    Attributes:
        order: The order filled.
        legs: Filled legs, in the order's leg order, ``contracts = ratio · packages``.
        quote_ids: Observation id of each leg's price, in leg order.
        net_debit: Whole-order ``D`` at the fill's naturals (negative for a credit).
        fees: ``trade_fees`` of the legs.
        entry: The ``book_option_trade`` entry (``campaign_id = order.campaign_id``,
            ``settles_on`` T+1).
        post_state: ``apply_entry(state, entry)``; headroom >= 0.

    """

    order: Order
    legs: tuple[LegFill, ...]
    quote_ids: tuple[str, ...]
    net_debit: Usd
    fees: tuple[FeeLine, ...]
    entry: LedgerEntry
    post_state: LedgerState


@dataclass(frozen=True, slots=True)
class Nonfill:
    """A failed fill attempt; the first failed check.

    Attributes:
        order: The order.
        reason: The check that failed.
        contract_id: First leg (in leg order) failing a per-leg check; None otherwise.
        net_debit: Whole-order ``D`` when it was computed (checks 3 to 7); None before.
        message: Human-readable detail; never empty.

    """

    order: Order
    reason: NonfillReason
    contract_id: str | None
    net_debit: Usd | None
    message: str


def try_fill(
    order: Order, view: AsOfView, capacity: CapacityBook, state: LedgerState, ctx: FillContext
) -> Fill | Nonfill:
    """Attempt an order at one fill slot; checks in order, the first failure is returned.

    1. Every leg: ``view.quote(leg, max_age_ns=QUOTE_MAX_AGE_NS, observed_after_ns=
       order.submitted_at_ns)``, else NO_OBSERVATION.
    2. Per leg, in leg order: status VALID, LOCKED or NO_BID, else QUOTE_INVALID; then the side
       used (ask for ``ratio > 0``, bid for ``ratio < 0``) has price > 0 and size > 0, else
       NO_SIDE.
    3. ``D = package_debit(legs, packages, quotes)``; an opening order needs ``Q·D > 0``
       (Q = +1 debit, -1 credit), else PREMIUM_DIRECTION.
    4. Unless FINAL: ``D <= limit_usd``, else LIMIT (a better price fills at that price).
    5. ``capacity = floor(min_i(remaining_i / |ratio_i|) · participation_fraction)``;
       ``packages > capacity`` is CAPACITY (all or none). FINAL is capacity-bound too.
    6. ``trade_fees`` → ``book_option_trade(settles_on=ctx.settles_on)`` → ``apply_entry``
       preview → ``funding_headroom >= 0``, else INSUFFICIENT_CAPITAL (price-eligible; the
       position is kept).

    Args:
        order: A live order of this session.
        view: As-of view at the fill slot's instant.
        capacity: Capacity consumed so far in the run.
        state: Current ledger state.
        ctx: Attempt inputs.

    Returns:
        The fill, or the nonfill with its reason.

    Raises:
        SimulationInvariantError: If the fill would raise mid NLV valued at the fill
            observations' mids (``R1Campaign.FillNeverRaisesNLV``), or the ledger rejects the
            engine's own entry.

    """
    raise NotImplementedError
