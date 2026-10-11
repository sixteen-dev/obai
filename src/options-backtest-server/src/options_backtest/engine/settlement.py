"""Due-cash transfer and package cash settlement (ADR 0001 §5 and its §11 amendment).

A cash-settled package settles in one entry, refining ``R1Campaign.Settle``: Σ quantity x
``intrinsic_usd(settlement)`` over every lot nets into one RECEIVABLE or PAYABLE, each contract's
cost is relieved with REALIZED_PNL as the balance, one EXPIRATION event extinguishes each
contract and the entry retires them all. A leg's settlement cash flow is therefore
``-(ΔOPTION_COST + ΔREALIZED_PNL)`` of its contract. No receivable is ever netted against a
payable of another entry.
"""

from collections.abc import Mapping
from datetime import date, datetime

from options_backtest.engine.positions import fifo_relief, held_contract
from options_backtest.errors import LedgerInvariantError
from options_backtest.models.ledger import (
    CASH_ACCOUNT,
    DATED_KINDS,
    AccountKey,
    AccountKind,
    EntryKind,
    FeeLine,
    LedgerEntry,
    LedgerState,
    Lot,
    QuantityEvent,
    QuantityKind,
    due_cash,
    fee_amounts,
    leg_amounts,
    merge_postings,
)
from options_backtest.models.market import ContractTerms, SettlementType
from options_backtest.money import ZERO_USD, Price, Usd


def book_settle_due(
    state: LedgerState, *, event_id: str, at_ns: int, through: date
) -> LedgerEntry | None:
    """Move every RECEIVABLE and PAYABLE dated on or before ``through`` into CASH.

    Args:
        state: Current state.
        event_id: Event identifier.
        at_ns: Transfer time, UTC nanoseconds.
        through: Last settlement date to transfer.

    Returns:
        The transfer entry, or None when nothing is due.

    Raises:
        TypeError: If ``through`` is not a date.

    """
    if not isinstance(through, date) or isinstance(through, datetime):
        raise TypeError(f"through must be a date, got {type(through).__name__}")
    due = [
        (account, amount)
        for account, amount in state.balances.items()
        if account.kind in DATED_KINDS and date.fromisoformat(account.ref) <= through
    ]
    if not due:
        return None
    amounts = [(account, -amount) for account, amount in due]
    amounts += [(CASH_ACCOUNT, amount) for _, amount in due]
    return LedgerEntry(
        event_id=event_id,
        sequence=state.entry_count + 1,
        kind=EntryKind.SETTLE_DUE,
        at_ns=at_ns,
        campaign_id=None,
        postings=merge_postings(amounts),
        quantity_events=(),
        contracts=(),
        fee_lines=(),
        input_refs=(),
    )


def book_cash_settlement(  # noqa: PLR0913 — signature fixed by ADR 0001 §11 amendment
    state: LedgerState,
    *,
    event_id: str,
    at_ns: int,
    contract_ids: tuple[str, ...],
    settlement: Mapping[str, Price],
    fees: tuple[FeeLine, ...],
    settles_on: date,
    settlement_ref: str,
) -> LedgerEntry:
    """Settle one campaign's whole held cash-settled package at one expiry, in one entry.

    Args:
        state: Current state.
        event_id: Event identifier.
        at_ns: Settlement time, UTC nanoseconds.
        contract_ids: Exactly the held ``SettlementType.CASH`` contracts of one campaign at one
            ``expires_at_ns``: non-empty, distinct, registered and unretired.
        settlement: Official settlement price per deliverable asset.
        fees: Fee lines, computed by the caller over the package's Σ|quantity|.
        settles_on: Settlement cash date.
        settlement_ref: Reference of the source of every value in ``settlement`` (the
            settlement observation, or the manifest entry that supplied it); kept in
            ``input_refs``, since an all-out-of-the-money package's postings do not reveal it.

    Returns:
        The settlement entry; applying it retires every contract in ``contract_ids``.

    Raises:
        ValueError: If ``settlement_ref`` is not a non-empty str.
        LedgerInvariantError: If ``contract_ids`` is not exactly such a package.
        MissingMarkError: If ``settlement`` lacks a deliverable asset; never settled at zero.

    """
    if not isinstance(settlement_ref, str) or not settlement_ref:
        raise ValueError(f"settlement_ref must be a non-empty str, got {settlement_ref!r}")
    package = _package_terms(state, contract_ids)
    campaign_id = _require_whole_package(state, package)
    amounts: list[tuple[AccountKey, Usd]] = [*fee_amounts(fees, settles_on)]
    events: list[QuantityEvent] = []
    net = ZERO_USD
    for terms in package:
        lots = state.lots[terms.contract_id]
        held = sum(lot.quantity for lot in lots)
        flow = terms.intrinsic_usd(settlement).scaled_by(held)
        event = QuantityEvent(
            terms.contract_id, QuantityKind.EXPIRATION, -held, None, fifo_relief(lots, -held)
        )
        amounts.extend(leg_amounts(event, AccountKind.OPTION_COST, flow))
        events.append(event)
        net += flow
    amounts.append(due_cash(net, settles_on))
    return LedgerEntry(
        event_id=event_id,
        sequence=state.entry_count + 1,
        kind=EntryKind.CASH_SETTLEMENT,
        at_ns=at_ns,
        campaign_id=campaign_id,
        postings=merge_postings(amounts),
        quantity_events=tuple(events),
        contracts=package,
        fee_lines=fees,
        input_refs=(settlement_ref,),
    )


def _package_terms(state: LedgerState, contract_ids: object) -> tuple[ContractTerms, ...]:
    """Return the terms of distinct, registered, unretired, held, cash-settled contracts."""
    if not isinstance(contract_ids, tuple) or not contract_ids:
        raise LedgerInvariantError(f"contract_ids must be a non-empty tuple, got {contract_ids!r}")
    if len(set(contract_ids)) != len(contract_ids):
        raise LedgerInvariantError(f"contract_ids repeats a contract: {contract_ids}")
    package = tuple(held_contract(state, contract_id)[0] for contract_id in contract_ids)
    physical = [t.contract_id for t in package if t.settlement_type is not SettlementType.CASH]
    if physical:
        raise LedgerInvariantError(f"contracts {physical} are not cash-settled")
    return package


def _require_whole_package(state: LedgerState, package: tuple[ContractTerms, ...]) -> str | None:
    """Return the package's one campaign after checking it is that campaign's whole package."""
    campaigns = {lot.campaign_id for terms in package for lot in state.lots[terms.contract_id]}
    expiries = {terms.expires_at_ns for terms in package}
    if len(campaigns) != 1 or len(expiries) != 1:
        raise LedgerInvariantError(
            f"a package spans one campaign and one expiry, got {len(campaigns)} and {len(expiries)}"
        )
    campaign_id, expires_at_ns = campaigns.pop(), expiries.pop()
    held = {
        contract_id
        for contract_id, lots in state.lots.items()
        if _in_package(state.contracts.get(contract_id), lots, campaign_id, expires_at_ns)
    }
    requested = {terms.contract_id for terms in package}
    if held != requested:
        raise LedgerInvariantError(
            f"contracts {sorted(requested)} are not the whole held cash package {sorted(held)} "
            f"of campaign {campaign_id} at {expires_at_ns}"
        )
    return campaign_id


def _in_package(
    terms: ContractTerms | None, lots: tuple[Lot, ...], campaign_id: str | None, expires_at_ns: int
) -> bool:
    """Return whether a held instrument belongs to the campaign's cash package at the expiry."""
    return (
        terms is not None
        and terms.settlement_type is SettlementType.CASH
        and terms.expires_at_ns == expires_at_ns
        and any(lot.campaign_id == campaign_id for lot in lots)
    )
