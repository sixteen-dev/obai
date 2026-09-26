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

from options_backtest.data.records import QuoteObservation
from options_backtest.models.market import ContractTerms, Quote
from options_backtest.money import Usd


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
        raise NotImplementedError


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


def order_id(session_date: date, purpose: OrderPurpose) -> str:
    """Return ``o:{YYYY-MM-DD}:{purpose}``; one order per session at most.

    Args:
        session_date: Session of submission.
        purpose: Order purpose.

    Returns:
        The order id.

    """
    raise NotImplementedError


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
        ValueError: If ``ratio`` is zero.

    """
    raise NotImplementedError


def package_debit(legs: Sequence[OrderLeg], packages: int, quotes: Mapping[str, Quote]) -> Usd:
    """Return ``D = Σ premium_usd(natural_i, ratio_i · packages)`` at natural prices.

    The natural price is the ask for ``ratio > 0`` and the bid for ``ratio < 0``.

    Args:
        legs: Package legs.
        packages: Package count, >= 1.
        quotes: Quote per ``contract_id``; others ignored.

    Returns:
        The signed debit (negative for a credit), before fees.

    Raises:
        MissingMarkError: If a leg has no quote.
        ValueError: If ``packages < 1``.

    """
    raise NotImplementedError


def closing_legs(opening: Sequence[OrderLeg]) -> tuple[OrderLeg, ...]:
    """Return the legs that close a package: same terms, versions and order, negated ratios.

    Args:
        opening: The generation's opening legs.

    Returns:
        The closing legs.

    """
    raise NotImplementedError
