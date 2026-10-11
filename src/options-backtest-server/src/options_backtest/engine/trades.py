"""Deposit and option-trade posting functions (ADR 0001 §5 "Posting functions").

A trade books ``t = premium_usd(price, contracts)`` per leg: the package's cash nets to one
RECEIVABLE (credit) or PAYABLE (debit) due on ``settles_on`` (``R1Campaign.PostDebit``); each leg
relieves its contract's lots FIFO and opens the remainder at ``multiplier x price``, with
REALIZED_PNL as the balancing amount. Fees post separately to FEES and the dated PAYABLE.
"""

from datetime import date

from options_backtest.engine.positions import fifo_relief, open_remainder
from options_backtest.errors import ErrorCode, LedgerInvariantError, UnsupportedLifecycle
from options_backtest.models.ledger import (
    CAPITAL_ACCOUNT,
    CASH_ACCOUNT,
    AccountKey,
    AccountKind,
    EntryKind,
    FeeLine,
    LedgerEntry,
    LedgerState,
    LegFill,
    Lot,
    QuantityEvent,
    QuantityKind,
    due_cash,
    fee_amounts,
    leg_amounts,
    merge_postings,
)
from options_backtest.money import ZERO_USD, Usd


def book_deposit(
    *, event_id: str, at_ns: int, cash: Usd, stock: tuple[Lot, ...] = ()
) -> LedgerEntry:
    """Book the opening deposit: cash and stock lots against CAPITAL; always sequence 1.

    Args:
        event_id: Event identifier.
        at_ns: Deposit time, UTC nanoseconds.
        cash: Cash deposited, >= 0 whole cents; zero posts nothing.
        stock: Long stock lots deposited at their stated unit cost.

    Returns:
        The deposit entry.

    Raises:
        ValueError: If ``cash`` is negative or nothing is deposited.
        UnsupportedLifecycle: If a stock lot is short (needs a borrow policy, design §12.3).

    """
    if cash < ZERO_USD:
        raise ValueError(f"deposit cash must be >= 0, got {cash.amount}")
    if cash == ZERO_USD and not stock:
        raise ValueError("deposit has nothing to deposit")
    short = [lot.lot_id for lot in stock if lot.quantity < 0]
    if short:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_ACCOUNT_STATE, f"short stock lots {short} need a borrow policy"
        )
    amounts = [(CASH_ACCOUNT, cash), (CAPITAL_ACCOUNT, -cash)]
    for lot in stock:
        amounts.append((AccountKey(AccountKind.STOCK_COST, lot.instrument_id), lot.signed_cost))
        amounts.append((CAPITAL_ACCOUNT, -lot.signed_cost))
    events = tuple(
        QuantityEvent(lot.instrument_id, QuantityKind.DEPOSIT, lot.quantity, lot, ())
        for lot in stock
    )
    return LedgerEntry(
        event_id=event_id,
        sequence=1,
        kind=EntryKind.DEPOSIT,
        at_ns=at_ns,
        campaign_id=None,
        postings=merge_postings(amounts),
        quantity_events=events,
        contracts=(),
        fee_lines=(),
        input_refs=(),
    )


def book_option_trade(  # noqa: PLR0913 — signature fixed by ADR 0001 §5
    state: LedgerState,
    *,
    event_id: str,
    at_ns: int,
    campaign_id: str,
    legs: tuple[LegFill, ...],
    fees: tuple[FeeLine, ...],
    settles_on: date,
) -> LedgerEntry:
    """Book one filled option package.

    Args:
        state: State the trade applies to (sequence, lots).
        event_id: Event identifier; also prefixes opened lot ids.
        at_ns: Fill time, UTC nanoseconds.
        campaign_id: Campaign the opened lots belong to.
        legs: Filled legs, one per contract.
        fees: Fee lines of the fill.
        settles_on: Premium and fee settlement date.

    Returns:
        The trade entry.

    Raises:
        ValueError: If there is no leg or ``campaign_id`` is empty.
        LedgerInvariantError: If two legs trade the same contract.

    """
    if not legs:
        raise ValueError("an option trade needs at least one leg")
    if not isinstance(campaign_id, str) or not campaign_id:
        raise ValueError(f"campaign_id must be a non-empty str, got {campaign_id!r}")
    contract_ids = [leg.terms.contract_id for leg in legs]
    if len(set(contract_ids)) != len(contract_ids):
        raise LedgerInvariantError(f"a package trades a contract twice: {contract_ids}")
    events = tuple(_leg_event(state, leg, event_id, at_ns, campaign_id) for leg in legs)
    premiums = [leg.terms.premium_usd(leg.price, leg.contracts) for leg in legs]
    amounts = [due_cash(-sum(premiums, start=ZERO_USD), settles_on), *fee_amounts(fees, settles_on)]
    for event, premium in zip(events, premiums, strict=True):
        amounts.extend(leg_amounts(event, AccountKind.OPTION_COST, -premium))
    return LedgerEntry(
        event_id=event_id,
        sequence=state.entry_count + 1,
        kind=EntryKind.TRADE,
        at_ns=at_ns,
        campaign_id=campaign_id,
        postings=merge_postings(amounts),
        quantity_events=events,
        contracts=tuple(leg.terms for leg in legs),
        fee_lines=fees,
        input_refs=(),
    )


def _leg_event(
    state: LedgerState, leg: LegFill, event_id: str, at_ns: int, campaign_id: str
) -> QuantityEvent:
    """Return the leg's quantity event: FIFO relief, then a lot for the unrelieved remainder."""
    contract_id = leg.terms.contract_id
    reliefs = fifo_relief(state.lots.get(contract_id, ()), leg.contracts)
    remainder = open_remainder(leg.contracts, reliefs)
    opened = None
    if remainder:
        unit_cost = leg.terms.premium_usd(leg.price, 1)
        opened = Lot(
            f"{event_id}:{contract_id}", contract_id, remainder, unit_cost, campaign_id, at_ns
        )
    kind = QuantityKind.CLOSE if reliefs else QuantityKind.OPEN
    return QuantityEvent(contract_id, kind, leg.contracts, opened, reliefs)
