"""Order vocabulary shared by decisions, fills and artifacts (ADR 0002 §7-§9, design §10.3).

An order is one package of legs times an integer package count. Signed leg quantities follow
design §10.3: ``q > 0`` buys at the ask, ``q < 0`` sells at the bid, and the package debit
``D = Σ premium_usd(natural_i, q_i · n)`` is negative for a credit. A limit is on the whole
order's ``D``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from options_backtest.data.records import QuoteObservation, QuoteStatus
from options_backtest.errors import MissingMarkError
from options_backtest.models.market import (
    ContractTerms,
    Quote,
    require_id,
    require_int,
    require_type,
)
from options_backtest.money import ZERO_USD, Price, Usd

_BUY_STATUSES: Final = frozenset({QuoteStatus.VALID, QuoteStatus.LOCKED, QuoteStatus.NO_BID})
_SELL_STATUSES: Final = frozenset({QuoteStatus.VALID, QuoteStatus.LOCKED})


class OrderPurpose(StrEnum):
    """Why an order was submitted (``R1Campaign`` purposes)."""

    ENTRY = "entry"
    ROLL_OPEN = "roll_open"
    EXIT = "exit"
    ROLL_CLOSE = "roll_close"
    FINAL = "final"

    @property
    def opening(self) -> bool:
        """Return whether the order opens a position (ENTRY or ROLL_OPEN)."""
        return self in (OrderPurpose.ENTRY, OrderPurpose.ROLL_OPEN)


class ExitTrigger(StrEnum):
    """Why a campaign's position was or is being closed.

    An EXIT order carries the first true of TIME_EXIT, TAKE_PROFIT, STOP_LOSS, CAMPAIGN_CAP,
    ROLL_CAP (ADR 0002 §7's order); FINAL carries FINAL_LIQUIDATION. SETTLEMENT and
    ROLL_NOT_REOPENED appear only on campaign records.
    """

    TIME_EXIT = "time_exit"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    CAMPAIGN_CAP = "campaign_cap"
    ROLL_CAP = "roll_cap"
    FINAL_LIQUIDATION = "final_liquidation"
    SETTLEMENT = "settlement"
    ROLL_NOT_REOPENED = "roll_not_reopened"


_EXIT_TRIGGERS: Final = frozenset(
    {
        ExitTrigger.TIME_EXIT,
        ExitTrigger.TAKE_PROFIT,
        ExitTrigger.STOP_LOSS,
        ExitTrigger.CAMPAIGN_CAP,
        ExitTrigger.ROLL_CAP,
    }
)


class NonfillReason(StrEnum):
    """The first failed fill check (ADR 0002 §9), in check order."""

    NO_OBSERVATION = "NO_OBSERVATION"
    QUOTE_INVALID = "QUOTE_INVALID"
    NO_SIDE = "NO_SIDE"
    PREMIUM_DIRECTION = "PREMIUM_DIRECTION"
    LIMIT = "LIMIT"
    CAPACITY = "CAPACITY"
    INSUFFICIENT_CAPITAL = "INSUFFICIENT_CAPITAL"


@dataclass(frozen=True, slots=True)
class OrderLeg:
    """One leg of an order's package.

    Attributes:
        terms: Contract terms of the version decided on.
        version_id: ``ContractVersion.version_id`` decided on; the held version for closes.
        ratio: Signed contracts per package: +1 buys, -1 sells (R1 ratios are 1).

    """

    terms: ContractTerms
    version_id: str
    ratio: int

    def __post_init__(self) -> None:
        """Require the terms, a version of that contract and a nonzero integer ratio."""
        require_type(self.terms, ContractTerms, "OrderLeg.terms")
        require_id(self.version_id, "OrderLeg.version_id")
        if not self.version_id.startswith(f"{self.terms.contract_id}@v"):
            raise ValueError(
                f"OrderLeg.version_id {self.version_id!r} is not a version of "
                f"{self.terms.contract_id}"
            )
        _require_ratio(self.ratio, "OrderLeg.ratio")


@dataclass(frozen=True, slots=True)
class Order:
    """A marketable limit (or FINAL's market-style) package order, live DEC to F3.

    Attributes:
        order_id: ``o:{session}:{purpose}``.
        campaign_id: Generation id ``c{n}.g{k}`` of the opened or closed position.
        purpose: Why it was submitted.
        legs: Opening orders in the strategy's ``leg_order``; closing orders in the held
            generation's opening leg order with negated ratios.
        packages: Package count n >= 1.
        limit_usd: Largest acceptable whole-order ``D``: the decision naturals' ``D(n)`` plus
            ``price_allowance_usd`` once; None for FINAL (market-style: no limit, still
            capacity- and funding-checked).
        trigger: Exit trigger of an EXIT or FINAL order; None otherwise.
        submitted_at_ns: The DEC instant; fills need observations strictly after it.
        session_date: Session of submission; the order is cancelled after its F3.

    """

    order_id: str
    campaign_id: str
    purpose: OrderPurpose
    legs: tuple[OrderLeg, ...]
    packages: int
    limit_usd: Usd | None
    trigger: ExitTrigger | None
    submitted_at_ns: int
    session_date: date

    def __post_init__(self) -> None:
        """Validate the fields, the id they spell, and the purpose's limit and trigger."""
        require_type(self.purpose, OrderPurpose, "Order.purpose")
        _require_day(self.session_date, "Order.session_date")
        expected = order_id(self.session_date, self.purpose)
        if self.order_id != expected:
            raise ValueError(f"Order.order_id {self.order_id!r} must be {expected!r}")
        require_id(self.campaign_id, "Order.campaign_id")
        _require_legs(self.legs)
        _require_packages(self.packages, "Order.packages")
        require_int(self.submitted_at_ns, "Order.submitted_at_ns")
        _require_limit(self.purpose, self.limit_usd)
        _require_trigger(self.purpose, self.trigger)


def _require_day(value: object, field: str) -> None:
    if type(value) is not date:
        raise TypeError(f"{field} must be a date, got {type(value).__name__}")


def _require_ratio(value: object, field: str) -> None:
    require_int(value, field)
    if value == 0:
        raise ValueError(f"{field} must be nonzero")


def _require_packages(value: int, field: str) -> None:
    require_int(value, field)
    if value < 1:
        raise ValueError(f"{field} must be >= 1, got {value}")


def _require_legs(legs: object) -> None:
    """Require a non-empty tuple of ``OrderLeg`` trading each contract once."""
    if not isinstance(legs, tuple) or not legs:
        raise ValueError(f"Order.legs must be a non-empty tuple, got {legs!r}")
    for leg in legs:
        require_type(leg, OrderLeg, "Order.legs item")
    contract_ids = [leg.terms.contract_id for leg in legs]
    if len(set(contract_ids)) != len(contract_ids):
        raise ValueError(f"Order.legs trade a contract twice: {contract_ids}")


def _require_limit(purpose: OrderPurpose, limit: object) -> None:
    """Require no limit on a FINAL order (market-style) and a ``Usd`` limit on every other."""
    final = purpose is OrderPurpose.FINAL
    if final != (limit is None):
        raise ValueError(f"Order.limit_usd must be None exactly for FINAL, got {limit!r}")
    if limit is not None:
        require_type(limit, Usd, "Order.limit_usd")


def _require_trigger(purpose: OrderPurpose, trigger: object) -> None:
    """EXIT carries an exit trigger, FINAL carries FINAL_LIQUIDATION, the rest carry none."""
    if trigger is not None:
        require_type(trigger, ExitTrigger, "Order.trigger")
    if purpose is OrderPurpose.EXIT:
        fits = trigger in _EXIT_TRIGGERS
    elif purpose is OrderPurpose.FINAL:
        fits = trigger is ExitTrigger.FINAL_LIQUIDATION
    else:
        fits = trigger is None
    if not fits:
        raise ValueError(f"Order.trigger {trigger} does not fit a {purpose.value} order")


def order_id(session_date: date, purpose: OrderPurpose) -> str:
    """Return ``o:{YYYY-MM-DD}:{purpose}``; one order per session at most.

    Args:
        session_date: Session of submission.
        purpose: Order purpose.

    Returns:
        The order id.

    Raises:
        TypeError: If ``session_date`` is not exactly a date or ``purpose`` not an
            ``OrderPurpose``.

    """
    _require_day(session_date, "order_id session_date")
    require_type(purpose, OrderPurpose, "order_id purpose")
    return f"o:{session_date.isoformat()}:{purpose.value}"


def usable_quote(observation: QuoteObservation | None, ratio: int) -> Quote | None:
    """Return the quote if its status lets this side trade, else None.

    A buy (``ratio > 0``, uses the ask) accepts VALID, LOCKED and NO_BID; a sell (uses the bid)
    accepts VALID and LOCKED only, since a zero bid cannot be sold into (design §8.4).

    Args:
        observation: The leg's latest fresh observation, or None.
        ratio: Signed per-package quantity, nonzero.

    Returns:
        ``observation.quote()`` or None.

    Raises:
        TypeError: If ``ratio`` is not exactly ``int`` or ``observation`` is not a
            ``QuoteObservation``.
        ValueError: If ``ratio`` is zero.

    """
    _require_ratio(ratio, "usable_quote ratio")
    if observation is None:
        return None
    require_type(observation, QuoteObservation, "usable_quote observation")
    allowed = _BUY_STATUSES if ratio > 0 else _SELL_STATUSES
    return observation.quote() if observation.status() in allowed else None


def package_debit(legs: Sequence[OrderLeg], packages: int, quotes: Mapping[str, Quote]) -> Usd:
    """Return ``D = Σ premium_usd(natural_i, ratio_i · packages)`` at natural prices.

    The natural price is the ask for ``ratio > 0`` and the bid for ``ratio < 0``.

    Args:
        legs: Package legs; at least one.
        packages: Package count, >= 1.
        quotes: Quote per ``contract_id``; others ignored.

    Returns:
        The signed debit (negative for a credit), before fees.

    Raises:
        MissingMarkError: If a leg has no quote.
        ValueError: If ``packages < 1`` or there is no leg.

    """
    _require_packages(packages, "package_debit packages")
    if not legs:
        raise ValueError("package_debit needs at least one leg")
    missing = [leg.terms.contract_id for leg in legs if leg.terms.contract_id not in quotes]
    if missing:
        raise MissingMarkError(missing)
    debit = ZERO_USD
    for leg in legs:
        natural = natural_price(quotes[leg.terms.contract_id], leg.ratio)
        debit += leg.terms.premium_usd(natural, leg.ratio * packages)
    return debit


def natural_price(quote: Quote, ratio: int) -> Price:
    """Return the natural price a leg trades at: the ask for a buy, the bid for a sell.

    Args:
        quote: The leg's quote.
        ratio: Signed quantity, nonzero; ``> 0`` buys (design §10.3).

    Returns:
        ``quote.ask`` or ``quote.bid``.

    Raises:
        TypeError: If ``quote`` is not a ``Quote`` or ``ratio`` not exactly ``int``.
        ValueError: If ``ratio`` is zero.

    """
    require_type(quote, Quote, "natural_price quote")
    _require_ratio(ratio, "natural_price ratio")
    return quote.ask if ratio > 0 else quote.bid


def closing_legs(opening: Sequence[OrderLeg]) -> tuple[OrderLeg, ...]:
    """Return the legs that close a package: same terms, versions and order, negated ratios.

    Args:
        opening: The generation's opening legs; at least one.

    Returns:
        The closing legs.

    Raises:
        ValueError: If there is no leg.

    """
    if not opening:
        raise ValueError("closing_legs needs at least one leg")
    return tuple(OrderLeg(leg.terms, leg.version_id, -leg.ratio) for leg in opening)
