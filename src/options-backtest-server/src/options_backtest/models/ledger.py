"""Ledger records: accounts, postings, lots, quantity events, fees, entries and state.

ADR 0001 §5, design §11.1. One ``LedgerEntry`` carries both the balanced monetary postings and
the position-quantity events of one economic event, so the two journals cannot disagree. Each
record validates its own fields; invariants spanning records and state are enforced by
``engine.journal.apply_entry``, the only state transition.

Balance signs (debit positive): CASH, RECEIVABLE, OPTION_COST and STOCK_COST are positive when
held (a short lot's cost is ``quantity x unit_cost < 0``); PAYABLE <= 0; CAPITAL <= 0;
REALIZED_PNL < 0 for gains; FEES >= 0.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from options_backtest.models.market import (
    ContractTerms,
    require_id,
    require_int,
    require_non_negative_usd,
    require_type,
)
from options_backtest.money import ZERO_USD, Price, Usd


class AccountKind(StrEnum):
    """Monetary account types; financing, dividend and revaluation arrive with their posters."""

    CASH = "cash"
    CAPITAL = "capital"
    RECEIVABLE = "receivable"
    PAYABLE = "payable"
    OPTION_COST = "option_cost"
    STOCK_COST = "stock_cost"
    REALIZED_PNL = "realized_pnl"
    FEES = "fees"


CASH_KINDS: Final = frozenset({AccountKind.CASH, AccountKind.RECEIVABLE, AccountKind.PAYABLE})
"""Kinds that settle in cash: postings to them are whole cents, and they count in NLV."""
DATED_KINDS: Final = frozenset({AccountKind.RECEIVABLE, AccountKind.PAYABLE})
"""Open items, referenced by their ISO settlement date."""
COST_KINDS: Final = frozenset({AccountKind.OPTION_COST, AccountKind.STOCK_COST})
"""Position cost accounts, referenced by instrument: ``Σ quantity x unit_cost`` of its lots."""
_UNREFERENCED: Final = frozenset({AccountKind.CASH, AccountKind.CAPITAL})


def _require_optional_id(value: object, field: str) -> None:
    if value is not None:
        require_id(value, field)


def _require_nonzero_int(value: object, field: str) -> None:
    require_int(value, field)
    if value == 0:
        raise ValueError(f"{field} must be nonzero")


def _require_tuple_of(value: object, item_type: type[object], field: str) -> None:
    if not isinstance(value, tuple) or not all(isinstance(item, item_type) for item in value):
        raise TypeError(f"{field} must be a tuple of {item_type.__name__}")


def _require_settlement_date(value: object, field: str) -> None:
    """Reject anything but a plain ``date``; a ``datetime`` is a ``date`` subclass."""
    require_type(value, date, field)
    if isinstance(value, datetime):
        raise TypeError(f"{field} must be a date, got {type(value).__name__}")


def _require_iso_date(ref: str) -> None:
    try:
        parsed = date.fromisoformat(ref)
    except ValueError as e:
        raise ValueError(f"AccountKey ref must be an ISO settlement date, got {ref!r}") from e
    if parsed.isoformat() != ref:
        raise ValueError(f"AccountKey ref must be an ISO YYYY-MM-DD date, got {ref!r}")


@dataclass(frozen=True, slots=True, order=True)
class AccountKey:
    """One sub-ledger account; keys order by kind, then reference.

    Attributes:
        kind: Account type.
        ref: "" for CASH and CAPITAL; the ISO settlement date for RECEIVABLE and PAYABLE;
            ``contract_id`` for OPTION_COST; ``asset_id`` for STOCK_COST; the instrument id for
            REALIZED_PNL; the fee component id for FEES.

    """

    kind: AccountKind
    ref: str

    def __post_init__(self) -> None:
        """Reject a reference that does not fit the account kind."""
        require_type(self.kind, AccountKind, "AccountKey.kind")
        require_type(self.ref, str, "AccountKey.ref")
        if self.kind in _UNREFERENCED and self.ref:
            raise ValueError(f"AccountKey ref must be empty for {self.kind}, got {self.ref!r}")
        if self.kind in DATED_KINDS:
            _require_iso_date(self.ref)
        elif self.kind not in _UNREFERENCED and not self.ref:
            raise ValueError(f"AccountKey ref must be non-empty for {self.kind}")


CASH_ACCOUNT: Final = AccountKey(AccountKind.CASH, "")
CAPITAL_ACCOUNT: Final = AccountKey(AccountKind.CAPITAL, "")


@dataclass(frozen=True, slots=True)
class Posting:
    """One monetary posting; debit positive. The postings of an entry sum to exactly zero.

    Attributes:
        account: Account posted to.
        amount: Signed amount.

    """

    account: AccountKey
    amount: Usd

    def __post_init__(self) -> None:
        """Validate the field types."""
        require_type(self.account, AccountKey, "Posting.account")
        require_type(self.amount, Usd, "Posting.amount")


@dataclass(frozen=True, slots=True)
class Lot:
    """An open position lot within one instrument; lots of an instrument are relieved FIFO.

    Attributes:
        lot_id: Identifier, unique within the instrument.
        instrument_id: ``contract_id`` for an option, ``asset_id`` for stock.
        quantity: Signed contracts or shares, nonzero.
        unit_cost: Non-negative cost per unit (``multiplier x price`` for an option).
        campaign_id: Campaign the lot belongs to; None for deposited stock.
        opened_at_ns: When the lot opened, UTC nanoseconds.

    """

    lot_id: str
    instrument_id: str
    quantity: int
    unit_cost: Usd
    campaign_id: str | None
    opened_at_ns: int

    def __post_init__(self) -> None:
        """Validate every field."""
        require_id(self.lot_id, "Lot.lot_id")
        require_id(self.instrument_id, "Lot.instrument_id")
        _require_nonzero_int(self.quantity, "Lot.quantity")
        require_non_negative_usd(self.unit_cost, "Lot.unit_cost")
        _require_optional_id(self.campaign_id, "Lot.campaign_id")
        require_int(self.opened_at_ns, "Lot.opened_at_ns")

    @property
    def signed_cost(self) -> Usd:
        """Return ``quantity x unit_cost``: the lot's balance in its cost account."""
        return self.unit_cost.scaled_by(self.quantity)


@dataclass(frozen=True, slots=True)
class LotRelief:
    """The part of one lot relieved by a quantity event.

    Attributes:
        lot_id: Relieved lot.
        quantity: Relieved quantity, with the lot's sign; ``0 < |quantity| <= |lot.quantity|``.
        cost: ``|quantity| x unit_cost``, exact and non-negative.

    """

    lot_id: str
    quantity: int
    cost: Usd

    def __post_init__(self) -> None:
        """Validate every field."""
        require_id(self.lot_id, "LotRelief.lot_id")
        _require_nonzero_int(self.quantity, "LotRelief.quantity")
        require_non_negative_usd(self.cost, "LotRelief.cost")

    @property
    def signed_cost(self) -> Usd:
        """Return the relieved cost with the lot's sign: the cost account's decrease."""
        return self.cost if self.quantity > 0 else -self.cost


class QuantityKind(StrEnum):
    """Why a position quantity changed."""

    DEPOSIT = "deposit"
    OPEN = "open"
    CLOSE = "close"
    EXERCISE = "exercise"
    ASSIGNMENT = "assignment"
    DELIVERY = "delivery"
    EXPIRATION = "expiration"
    ADJUST_OUT = "adjust_out"
    ADJUST_IN = "adjust_in"


@dataclass(frozen=True, slots=True)
class QuantityEvent:
    """One signed quantity change of one instrument, with the FIFO reliefs it causes.

    Attributes:
        instrument_id: Instrument whose quantity changes.
        kind: Reason for the change.
        delta: Signed change, nonzero.
        opened: The lot opened by the unrelieved remainder of ``delta``, if any.
        reliefs: FIFO reliefs of existing lots, oldest first.

    """

    instrument_id: str
    kind: QuantityKind
    delta: int
    opened: Lot | None
    reliefs: tuple[LotRelief, ...]

    def __post_init__(self) -> None:
        """Validate every field and the opened lot's instrument and sign."""
        require_id(self.instrument_id, "QuantityEvent.instrument_id")
        require_type(self.kind, QuantityKind, "QuantityEvent.kind")
        _require_nonzero_int(self.delta, "QuantityEvent.delta")
        _require_tuple_of(self.reliefs, LotRelief, "QuantityEvent.reliefs")
        if self.opened is None:
            return
        require_type(self.opened, Lot, "QuantityEvent.opened")
        if self.opened.instrument_id != self.instrument_id:
            raise ValueError(
                f"opened lot instrument {self.opened.instrument_id!r} is not the event's"
            )
        if (self.opened.quantity > 0) != (self.delta > 0):
            raise ValueError("opened lot sign differs from the event delta's sign")

    @property
    def cost_change(self) -> Usd:
        """Return the cost account's change: the opened lot's cost less the relieved cost."""
        opened = self.opened.signed_cost if self.opened is not None else ZERO_USD
        relieved = sum((relief.signed_cost for relief in self.reliefs), start=ZERO_USD)
        return opened - relieved


class FeeEvent(StrEnum):
    """Events a fee schedule assesses."""

    TRADE = "trade"
    EXERCISE_ASSIGNMENT = "exercise_assignment"
    CASH_SETTLEMENT = "cash_settlement"


@dataclass(frozen=True, slots=True)
class FeeLine:
    """One assessed fee line item, kept on the entry even when its amount is zero.

    Attributes:
        component_id: Fee schedule component; the FEES account reference.
        event: Assessed event.
        contracts: Contracts assessed, > 0.
        rate: Rate per contract, >= 0.
        amount: Fee charged, >= 0 (whole cents when posted).

    """

    component_id: str
    event: FeeEvent
    contracts: int
    rate: Usd
    amount: Usd

    def __post_init__(self) -> None:
        """Validate every field."""
        require_id(self.component_id, "FeeLine.component_id")
        require_type(self.event, FeeEvent, "FeeLine.event")
        require_int(self.contracts, "FeeLine.contracts")
        if self.contracts <= 0:
            raise ValueError(f"FeeLine.contracts must be > 0, got {self.contracts}")
        require_non_negative_usd(self.rate, "FeeLine.rate")
        require_non_negative_usd(self.amount, "FeeLine.amount")


@dataclass(frozen=True, slots=True)
class LegFill:
    """One filled leg of an option package.

    Attributes:
        terms: Contract traded.
        contracts: Signed contracts, nonzero: + buy, - sell.
        price: Fill price per unit.

    """

    terms: ContractTerms
    contracts: int
    price: Price

    def __post_init__(self) -> None:
        """Validate every field."""
        require_type(self.terms, ContractTerms, "LegFill.terms")
        _require_nonzero_int(self.contracts, "LegFill.contracts")
        require_type(self.price, Price, "LegFill.price")


class EntryKind(StrEnum):
    """Which posting function produced an entry."""

    DEPOSIT = "deposit"
    TRADE = "trade"
    SETTLE_DUE = "settle_due"
    CASH_SETTLEMENT = "cash_settlement"
    PHYSICAL_EXERCISE = "physical_exercise"
    DELIVERABLE_ADJUSTMENT = "deliverable_adjustment"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One sequence-bound journal entry: balanced postings plus the quantity events they price.

    Attributes:
        event_id: Unique event identifier.
        sequence: Position in the journal, starting at 1.
        kind: Posting function that produced the entry.
        at_ns: Economic time, UTC nanoseconds, >= 0.
        campaign_id: Campaign the entry belongs to, if one.
        postings: Canonical postings: one per account, nonzero, sorted by account.
        quantity_events: Quantity changes, applied in order.
        contracts: Terms of every contract the entry touches; unknown ones are registered.
        fee_lines: Fee line items posted by the entry.
        input_refs: References to the inputs the entry used (such as a corporate action).

    """

    event_id: str
    sequence: int
    kind: EntryKind
    at_ns: int
    campaign_id: str | None
    postings: tuple[Posting, ...]
    quantity_events: tuple[QuantityEvent, ...]
    contracts: tuple[ContractTerms, ...]
    fee_lines: tuple[FeeLine, ...]
    input_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate every field's type and range."""
        require_id(self.event_id, "LedgerEntry.event_id")
        require_int(self.sequence, "LedgerEntry.sequence")
        if self.sequence < 1:
            raise ValueError(f"LedgerEntry.sequence must be >= 1, got {self.sequence}")
        require_type(self.kind, EntryKind, "LedgerEntry.kind")
        require_int(self.at_ns, "LedgerEntry.at_ns")
        if self.at_ns < 0:
            raise ValueError(f"LedgerEntry.at_ns must be >= 0, got {self.at_ns}")
        _require_optional_id(self.campaign_id, "LedgerEntry.campaign_id")
        _require_tuple_of(self.postings, Posting, "LedgerEntry.postings")
        _require_tuple_of(self.quantity_events, QuantityEvent, "LedgerEntry.quantity_events")
        _require_tuple_of(self.contracts, ContractTerms, "LedgerEntry.contracts")
        _require_tuple_of(self.fee_lines, FeeLine, "LedgerEntry.fee_lines")
        _require_tuple_of(self.input_refs, str, "LedgerEntry.input_refs")
        if not all(self.input_refs):
            raise ValueError("LedgerEntry.input_refs must be non-empty strings")


@dataclass(frozen=True, slots=True)
class LedgerState:
    """The ledger after ``entry_count`` entries; built only by ``apply_entry`` and ``replay``.

    The state is canonical: zero balances and empty lot lists are absent, so equal histories
    give equal states. Mappings are read-only copies.

    Attributes:
        entry_count: Entries applied so far.
        last_at_ns: ``at_ns`` of the last entry; 0 when empty.
        balances: Nonzero balance per account.
        lots: Open lots per instrument, FIFO order; one sign per instrument.
        contracts: Registered contract terms by ``contract_id``.
        retired: Contracts extinguished by expiration or adjustment; never touched again.

    """

    entry_count: int
    last_at_ns: int
    balances: Mapping[AccountKey, Usd]
    lots: Mapping[str, tuple[Lot, ...]]
    contracts: Mapping[str, ContractTerms]
    retired: frozenset[str]

    def __post_init__(self) -> None:
        """Freeze the mappings and reject a non-canonical state."""
        require_int(self.entry_count, "LedgerState.entry_count")
        if self.entry_count < 0:
            raise ValueError(f"LedgerState.entry_count must be >= 0, got {self.entry_count}")
        require_int(self.last_at_ns, "LedgerState.last_at_ns")
        require_type(self.retired, frozenset, "LedgerState.retired")
        zero = sorted(key for key, amount in self.balances.items() if amount == ZERO_USD)
        if zero:
            raise ValueError(f"LedgerState holds a zero balance for {zero}")
        empty = sorted(instrument for instrument, lots in self.lots.items() if not lots)
        if empty:
            raise ValueError(f"LedgerState holds an empty lot list for {empty}")
        object.__setattr__(self, "balances", MappingProxyType(dict(self.balances)))
        object.__setattr__(self, "lots", MappingProxyType(dict(self.lots)))
        object.__setattr__(self, "contracts", MappingProxyType(dict(self.contracts)))

    @classmethod
    def empty(cls) -> LedgerState:
        """Return the state before any entry."""
        return cls(0, 0, {}, {}, {}, frozenset())


def merge_postings(amounts: Iterable[tuple[AccountKey, Usd]]) -> tuple[Posting, ...]:
    """Return canonical postings: one per account, zero totals dropped, sorted by account.

    Args:
        amounts: Account and signed amount pairs, in any order, accounts possibly repeated.

    Returns:
        The merged postings; zero amounts post nothing.

    """
    totals: dict[AccountKey, Usd] = {}
    for account, amount in amounts:
        totals[account] = totals.get(account, ZERO_USD) + amount
    return tuple(
        Posting(account, totals[account])
        for account in sorted(totals)
        if totals[account] != ZERO_USD
    )


def due_cash(amount: Usd, settles_on: date) -> tuple[AccountKey, Usd]:
    """Return net cash due on ``settles_on``: RECEIVABLE when positive, else PAYABLE.

    This is ``R1Campaign.PostDebit``: an entry's net premium or settlement cash becomes one
    receivable or one payable; fees are separate payables (``fee_amounts``).

    Args:
        amount: Signed net cash flow to the account (+ received).
        settles_on: Settlement date.

    Returns:
        The dated account and ``amount``.

    Raises:
        TypeError: If ``settles_on`` is not a date, or is a datetime.

    """
    _require_settlement_date(settles_on, "settles_on")
    kind = AccountKind.RECEIVABLE if amount > ZERO_USD else AccountKind.PAYABLE
    return AccountKey(kind, settles_on.isoformat()), amount


def leg_amounts(
    event: QuantityEvent, cost_kind: AccountKind, cash: Usd
) -> list[tuple[AccountKey, Usd]]:
    """Return one leg's cost-side change and the REALIZED_PNL that balances it.

    ADR 0001 §5, per leg or lot: the cash side ``cash`` (posted by the caller, netted into one
    receivable or payable per entry), the cost side ``event.cost_change`` and REALIZED_PNL as
    the balancing amount ``-(cash + cost change)``; an opening leg therefore realizes nothing.

    Args:
        event: The leg's quantity event.
        cost_kind: OPTION_COST or STOCK_COST.
        cash: Signed cash the leg brings in (+ received, - paid).

    Returns:
        The cost and realized amounts on the event's instrument.

    Raises:
        ValueError: If ``cost_kind`` is not a cost account kind.

    """
    if cost_kind not in COST_KINDS:
        raise ValueError(f"leg_amounts cost_kind must be a cost account kind, got {cost_kind}")
    cost = event.cost_change
    return [
        (AccountKey(cost_kind, event.instrument_id), cost),
        (AccountKey(AccountKind.REALIZED_PNL, event.instrument_id), -(cash + cost)),
    ]


def fee_amounts(fees: Iterable[FeeLine], settles_on: date) -> list[tuple[AccountKey, Usd]]:
    """Return each fee line as a FEES debit and a PAYABLE credit due on ``settles_on``.

    Fees are never netted against a premium receivable (ADR 0001 §5, ``R1Campaign.FillOpen``).

    Args:
        fees: Fee line items.
        settles_on: Settlement date of the fee payable.

    Returns:
        Account and amount pairs, two per line.

    Raises:
        TypeError: If ``settles_on`` is not a date, or is a datetime.

    """
    _require_settlement_date(settles_on, "settles_on")
    payable = AccountKey(AccountKind.PAYABLE, settles_on.isoformat())
    amounts: list[tuple[AccountKey, Usd]] = []
    for line in fees:
        amounts.append((AccountKey(AccountKind.FEES, line.component_id), line.amount))
        amounts.append((payable, -line.amount))
    return amounts
