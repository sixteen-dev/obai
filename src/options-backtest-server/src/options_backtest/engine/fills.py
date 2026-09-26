"""The R1 fill model: natural-price, all-or-none package fills after submission (ADR 0002 §9).

``try_fill`` runs the checks in order and reports the first failure; a fill it returns is
already booked, previewed and funded, ready for ``Journal.commit``. The one funding check is at
once the opening rule, design §11.2's closing rule and ``R1Campaign.FillFunded``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from types import MappingProxyType
from typing import Final

from options_backtest.data.asof import AsOfView
from options_backtest.data.records import QuoteObservation, QuoteStatus
from options_backtest.engine.clock import QUOTE_MAX_AGE_NS
from options_backtest.engine.fees import AssumedFlatFeeSchedule, trade_fees
from options_backtest.engine.funding import funding_headroom
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.orders import NonfillReason, Order, natural_price, package_debit
from options_backtest.engine.trades import book_option_trade
from options_backtest.errors import (
    LedgerInvariantError,
    SimulationInvariantError,
    UnsupportedLifecycle,
)
from options_backtest.models.ledger import CASH_KINDS, FeeLine, LedgerEntry, LedgerState, LegFill
from options_backtest.models.market import Quote, require_id, require_int, require_type
from options_backtest.models.strategy_checks import PremiumDirection
from options_backtest.money import ZERO_USD, Price, Usd
from options_backtest.reference.calendars import next_session

_TRADABLE: Final = frozenset({QuoteStatus.VALID, QuoteStatus.LOCKED, QuoteStatus.NO_BID})
"""Statuses check 2 accepts; the side used must then show a price and a size."""


class QuoteSide(StrEnum):
    """The displayed side an order leg consumes: ASK for buys, BID for sells."""

    BID = "bid"
    ASK = "ask"


def _side(contracts: int) -> QuoteSide:
    """Return the side a signed quantity consumes: the ask for a buy, the bid for a sell."""
    return QuoteSide.ASK if contracts > 0 else QuoteSide.BID


def _displayed(observation: QuoteObservation, side: QuoteSide) -> tuple[Decimal, int]:
    """Return the raw price and size the observation displays on ``side``."""
    if side is QuoteSide.ASK:
        return observation.ask, observation.ask_size
    return observation.bid, observation.bid_size


@dataclass(frozen=True, slots=True)
class CapacityBook:
    """Displayed size already consumed per (quote observation, side) in a run.

    Immutable: ``consume`` returns a new book. Keyed by observation id, so one book serves a
    whole run and no displayed size is ever used twice (design §10.3).

    Attributes:
        consumed: Contracts consumed per (observation_id, side).

    """

    consumed: Mapping[tuple[str, QuoteSide], int]

    def __post_init__(self) -> None:
        """Freeze the mapping and require a positive count per (observation id, side)."""
        require_type(self.consumed, Mapping, "CapacityBook.consumed")
        for (observation_id, side), count in self.consumed.items():
            require_id(observation_id, "CapacityBook observation id")
            require_type(side, QuoteSide, "CapacityBook side")
            if type(count) is not int or count < 1:
                raise ValueError(
                    f"CapacityBook consumed count of {observation_id} {side} must be a positive "
                    f"int, got {count!r}"
                )
        object.__setattr__(self, "consumed", MappingProxyType(dict(self.consumed)))

    @classmethod
    def empty(cls) -> CapacityBook:
        """Return a book with nothing consumed."""
        return cls({})

    def remaining(self, observation: QuoteObservation, side: QuoteSide) -> int:
        """Return the side's displayed size less what earlier fills consumed, >= 0.

        Args:
            observation: The quote observation.
            side: Side used.

        Returns:
            Remaining contracts.

        Raises:
            TypeError: If an argument has the wrong type.
            ValueError: If the book holds more than the side displayed (a book used against a
                fill it did not admit).

        """
        require_type(observation, QuoteObservation, "CapacityBook.remaining observation")
        require_type(side, QuoteSide, "CapacityBook.remaining side")
        _, displayed = _displayed(observation, side)
        used = self.consumed.get((observation.observation_id, side), 0)
        if used > displayed:
            raise ValueError(
                f"{observation.observation_id} {side.value}: displayed {displayed} is less than "
                f"the {used} consumed"
            )
        return displayed - used

    def consume(self, fill: Fill) -> CapacityBook:
        """Return the book after a fill consumed ``packages · |ratio_i|`` on each leg's side.

        The fill carries its quote ids, not the displayed sizes: ``try_fill`` admits a fill only
        within what remains in the book it is given, and ``remaining`` fails loud should a book
        ever hold more than an observation displayed.

        Args:
            fill: A committed fill.

        Returns:
            The new book.

        Raises:
            TypeError: If ``fill`` is not a ``Fill``.

        """
        require_type(fill, Fill, "CapacityBook.consume fill")
        consumed = dict(self.consumed)
        for quote_id, leg in zip(fill.quote_ids, fill.legs, strict=True):
            key = (quote_id, _side(leg.contracts))
            consumed[key] = consumed.get(key, 0) + abs(leg.contracts)
        return CapacityBook(consumed)


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

    def __post_init__(self) -> None:
        """Validate the fields and a participation fraction in (0, 1]."""
        require_id(self.event_id, "FillContext.event_id")
        require_int(self.at_ns, "FillContext.at_ns")
        if type(self.settles_on) is not date:
            raise TypeError(f"FillContext.settles_on must be a date, got {self.settles_on!r}")
        require_type(self.schedule, AssumedFlatFeeSchedule, "FillContext.schedule")
        require_type(self.premium_direction, PremiumDirection, "FillContext.premium_direction")
        fraction = self.participation_fraction
        if type(fraction) is not Decimal:
            raise TypeError(
                f"FillContext.participation_fraction must be exactly Decimal, got {fraction!r}"
            )
        if not fraction.is_finite() or not 0 < fraction <= 1:
            raise ValueError(
                f"FillContext.participation_fraction must be in (0, 1], got {fraction}"
            )


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

    def __post_init__(self) -> None:
        """Require the order's legs times its packages and one quote id per leg.

        ``CapacityBook.consume`` charges each leg's ``|contracts|`` to its quote id.
        """
        require_type(self.order, Order, "Fill.order")
        for leg in self.legs:
            require_type(leg, LegFill, "Fill.legs item")
        ordered = [(leg.terms, leg.ratio * self.order.packages) for leg in self.order.legs]
        if [(leg.terms, leg.contracts) for leg in self.legs] != ordered:
            raise ValueError(f"Fill.legs must be order {self.order.order_id}'s legs x packages")
        if len(self.quote_ids) != len(ordered):
            raise ValueError(f"Fill.quote_ids needs one quote per leg, got {self.quote_ids}")
        for quote_id in self.quote_ids:
            require_id(quote_id, "Fill.quote_ids item")
        require_type(self.net_debit, Usd, "Fill.net_debit")
        require_type(self.entry, LedgerEntry, "Fill.entry")
        require_type(self.post_state, LedgerState, "Fill.post_state")


@dataclass(frozen=True, slots=True)
class Nonfill:
    """A failed fill attempt; the first failed check.

    Attributes:
        order: The order.
        reason: The check that failed.
        contract_id: First leg (in leg order) failing a per-leg check; None otherwise.
        net_debit: Whole-order ``D`` when it was computed (checks 3 to 6); None before.
        message: Human-readable detail; never empty.

    """

    order: Order
    reason: NonfillReason
    contract_id: str | None
    net_debit: Usd | None
    message: str

    def __post_init__(self) -> None:
        """Require the order, a reason, one of its legs or None, a ``Usd`` or None, a message."""
        require_type(self.order, Order, "Nonfill.order")
        require_type(self.reason, NonfillReason, "Nonfill.reason")
        legs = {leg.terms.contract_id for leg in self.order.legs}
        if self.contract_id is not None and self.contract_id not in legs:
            raise ValueError(f"Nonfill.contract_id {self.contract_id!r} is not a leg of the order")
        if self.net_debit is not None:
            require_type(self.net_debit, Usd, "Nonfill.net_debit")
        require_id(self.message, "Nonfill.message")


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
        TypeError: If an argument has the wrong type.
        ValueError: If the view is not at ``ctx.at_ns``, the attempt is not in the order's
            session after its submission, or ``ctx.settles_on`` is not the next table session.
        SimulationInvariantError: If the fill would raise mid NLV valued at the fill
            observations' mids (``R1Campaign.FillNeverRaisesNLV``), or the ledger rejects the
            engine's own entry.

    """
    _require_attempt(order, view, capacity, state, ctx)
    observed = _fresh_observations(order, view)
    if isinstance(observed, Nonfill):
        return observed
    rejection = _side_rejection(order, observed)
    if rejection is not None:
        return rejection
    debit = package_debit(order.legs, order.packages, _quotes(order, observed))
    rejection = _price_rejection(order, debit, ctx.premium_direction)
    if rejection is not None:
        return rejection
    rejection = _capacity_rejection(order, observed, capacity, ctx.participation_fraction, debit)
    if rejection is not None:
        return rejection
    return _funded_fill(order, observed, debit, state, ctx)


def _require_attempt(
    order: Order, view: AsOfView, capacity: CapacityBook, state: LedgerState, ctx: FillContext
) -> None:
    """Require the argument types, one fill instant, the order's session and T+1."""
    require_type(order, Order, "try_fill order")
    require_type(view, AsOfView, "try_fill view")
    require_type(capacity, CapacityBook, "try_fill capacity")
    require_type(state, LedgerState, "try_fill state")
    require_type(ctx, FillContext, "try_fill ctx")
    if view.at_ns != ctx.at_ns:
        raise ValueError(f"the view's at_ns {view.at_ns} is not the fill instant {ctx.at_ns}")
    session_date = view.session.session_date
    if session_date != order.session_date:
        raise ValueError(
            f"order {order.order_id} is live only in its session {order.session_date}, "
            f"not {session_date}"
        )
    if ctx.at_ns <= order.submitted_at_ns:
        raise ValueError(f"order {order.order_id} fills only after its submission")
    following = next_session(view.dataset.sessions, session_date)
    if following is None or ctx.settles_on != following.session_date:
        raise ValueError(
            f"settles_on {ctx.settles_on} must be the table session after {session_date}"
        )


def _fresh_observations(order: Order, view: AsOfView) -> tuple[QuoteObservation, ...] | Nonfill:
    """Check 1: every leg's latest quote, at most 120 s old, observed after the submission."""
    observed: list[QuoteObservation] = []
    for leg in order.legs:
        observation = view.quote(
            leg.terms.contract_id,
            max_age_ns=QUOTE_MAX_AGE_NS,
            observed_after_ns=order.submitted_at_ns,
        )
        if observation is None:
            message = "no quote at most 120 s old observed after the order's submission"
            return Nonfill(
                order, NonfillReason.NO_OBSERVATION, leg.terms.contract_id, None, message
            )
        observed.append(observation)
    return tuple(observed)


def _side_rejection(order: Order, observed: Sequence[QuoteObservation]) -> Nonfill | None:
    """Check 2, leg by leg: a tradable status, then a used side showing price and size."""
    for leg, observation in zip(order.legs, observed, strict=True):
        contract_id = leg.terms.contract_id
        status = observation.status()
        if status not in _TRADABLE:
            message = f"{observation.observation_id} is {status.value}"
            return Nonfill(order, NonfillReason.QUOTE_INVALID, contract_id, None, message)
        side = _side(leg.ratio)
        price, size = _displayed(observation, side)
        if price <= 0 or size <= 0:
            message = f"{observation.observation_id} {side.value} shows price {price}, size {size}"
            return Nonfill(order, NonfillReason.NO_SIDE, contract_id, None, message)
    return None


def _quotes(order: Order, observed: Sequence[QuoteObservation]) -> dict[str, Quote]:
    """Return each leg's quote by contract id; check 2 passed, so every status is quotable."""
    return {
        leg.terms.contract_id: observation.quote()
        for leg, observation in zip(order.legs, observed, strict=True)
    }


def _price_rejection(order: Order, debit: Usd, direction: PremiumDirection) -> Nonfill | None:
    """Apply checks 3 and 4: an opening premium in the declared direction, ``D`` in the limit."""
    sign = 1 if direction is PremiumDirection.DEBIT else -1
    if order.purpose.opening and debit.scaled_by(sign) <= ZERO_USD:
        message = f"opening D {debit.amount} is not a positive {direction.value}"
        return Nonfill(order, NonfillReason.PREMIUM_DIRECTION, None, debit, message)
    # The limit is None exactly for FINAL (Order validates it): market-style, no limit.
    if order.limit_usd is not None and debit > order.limit_usd:
        message = f"D {debit.amount} is above the limit {order.limit_usd.amount}"
        return Nonfill(order, NonfillReason.LIMIT, None, debit, message)
    return None


def _capacity_rejection(
    order: Order,
    observed: Sequence[QuoteObservation],
    capacity: CapacityBook,
    participation_fraction: Decimal,
    debit: Usd,
) -> Nonfill | None:
    """Check 5, all or none: ``floor(min_i(remaining_i / |ratio_i|) · participation)``."""
    per_package = min(
        Fraction(capacity.remaining(observation, _side(leg.ratio)), abs(leg.ratio))
        for leg, observation in zip(order.legs, observed, strict=True)
    )
    packages = math.floor(per_package * Fraction(participation_fraction))
    if order.packages <= packages:
        return None
    message = f"capacity {packages} package(s) < {order.packages} ordered (all or none)"
    return Nonfill(order, NonfillReason.CAPACITY, None, debit, message)


def _funded_fill(
    order: Order,
    observed: Sequence[QuoteObservation],
    debit: Usd,
    state: LedgerState,
    ctx: FillContext,
) -> Fill | Nonfill:
    """Check 6: book T+1, preview, and require ``funding_headroom >= 0`` after the fill."""
    quotes = _quotes(order, observed)
    legs = tuple(
        LegFill(
            leg.terms,
            leg.ratio * order.packages,
            natural_price(quotes[leg.terms.contract_id], leg.ratio),
        )
        for leg in order.legs
    )
    fees = trade_fees(ctx.schedule, legs)
    try:
        entry = book_option_trade(
            state,
            event_id=ctx.event_id,
            at_ns=ctx.at_ns,
            campaign_id=order.campaign_id,
            legs=legs,
            fees=fees,
            settles_on=ctx.settles_on,
        )
        post_state = apply_entry(state, entry)
        headroom = funding_headroom(post_state, ctx.schedule)
    except (LedgerInvariantError, UnsupportedLifecycle) as e:
        raise SimulationInvariantError(
            f"order {order.order_id}: the ledger rejected the engine's own fill: {e}"
        ) from e
    if headroom < ZERO_USD:
        message = f"post-fill funding headroom {headroom.amount} < 0"
        return Nonfill(order, NonfillReason.INSUFFICIENT_CAPITAL, None, debit, message)
    quote_ids = tuple(observation.observation_id for observation in observed)
    fill = Fill(order, legs, quote_ids, debit, fees, entry, post_state)
    _require_nlv_not_raised(fill, state, quotes)
    return fill


def _require_nlv_not_raised(fill: Fill, state: LedgerState, quotes: Mapping[str, Quote]) -> None:
    """Assert ``R1Campaign.FillNeverRaisesNLV``: mid NLV at the fill quotes does not rise.

    Only the fill's cash accounts and its legs' positions change, so the NLV change is the
    change of CASH + RECEIVABLE + PAYABLE plus each leg's contracts valued at its mid.
    """
    cash_change = _cash_total(fill.post_state) - _cash_total(state)
    marked = ZERO_USD
    for leg in fill.legs:
        quote = quotes[leg.terms.contract_id]
        marked += leg.terms.premium_usd(Price.mid(quote.bid, quote.ask), leg.contracts)
    change = cash_change + marked
    if change > ZERO_USD:
        raise SimulationInvariantError(
            f"fill {fill.entry.event_id} of order {fill.order.order_id} would raise mid NLV by "
            f"{change.amount}"
        )


def _cash_total(state: LedgerState) -> Usd:
    """Return CASH + ΣRECEIVABLE + ΣPAYABLE, the cash part of NLV."""
    return sum(
        (amount for account, amount in state.balances.items() if account.kind in CASH_KINDS),
        start=ZERO_USD,
    )
