"""Deliverable adjustment of a held contract (ADR 0001 §5; design §12.5, fixture F05).

An authoritative corporate action maps a contract to new terms under a new ``contract_id``: the
deliverable, AEA and listed strike may change; multiplier, option type, style, settlement and
expiry may not. Lots move one-for-one keeping lot id, unit cost, campaign and open time; their
OPTION_COST moves with them; nothing is realized; the old contract is retired.
"""

import dataclasses
from typing import Final

from options_backtest.engine.positions import fifo_relief, held_contract
from options_backtest.errors import ErrorCode, UnsupportedLifecycle
from options_backtest.models.ledger import (
    AccountKey,
    AccountKind,
    EntryKind,
    LedgerEntry,
    LedgerState,
    Lot,
    QuantityEvent,
    QuantityKind,
    merge_postings,
)
from options_backtest.models.market import ContractTerms

_KEPT_TERMS: Final = (
    "premium_multiplier",
    "option_type",
    "exercise_style",
    "settlement_type",
    "expires_at_ns",
)


def book_deliverable_adjustment(  # noqa: PLR0913 — signature fixed by ADR 0001 §5
    state: LedgerState,
    *,
    event_id: str,
    at_ns: int,
    old_contract_id: str,
    new_terms: ContractTerms,
    action_ref: str,
) -> LedgerEntry:
    """Move every lot of a held contract to its adjusted terms and retire the old contract.

    Args:
        state: Current state.
        event_id: Event identifier.
        at_ns: Effective time of the action, UTC nanoseconds.
        old_contract_id: Held contract the action adjusts.
        new_terms: Terms after the action, under a new, unregistered ``contract_id``.
        action_ref: Reference of the authoritative corporate action; kept in ``input_refs``.

    Returns:
        The adjustment entry.

    Raises:
        TypeError: If ``new_terms`` is not ``ContractTerms``.
        ValueError: If ``action_ref`` is empty.
        LedgerInvariantError: If the old contract is unregistered, retired or not held.
        UnsupportedLifecycle: UNSUPPORTED_CORPORATE_ACTION if the action changes anything but
            the deliverable, AEA and strike, or does not map to a new contract id.

    """
    if not isinstance(action_ref, str) or not action_ref:
        raise ValueError(f"action_ref must be a non-empty str, got {action_ref!r}")
    if not isinstance(new_terms, ContractTerms):
        raise TypeError(f"new_terms must be ContractTerms, got {type(new_terms).__name__}")
    old_terms, lots = held_contract(state, old_contract_id)
    _require_deliverable_change_only(state, old_terms, new_terms)
    held = sum(lot.quantity for lot in lots)
    moved_out = QuantityEvent(
        old_contract_id, QuantityKind.ADJUST_OUT, -held, None, fifo_relief(lots, -held)
    )
    moved_in = _moved_in(lots, new_terms.contract_id)
    amounts = [
        (AccountKey(AccountKind.OPTION_COST, event.instrument_id), event.cost_change)
        for event in (moved_out, *moved_in)
    ]
    return LedgerEntry(
        event_id=event_id,
        sequence=state.entry_count + 1,
        kind=EntryKind.DELIVERABLE_ADJUSTMENT,
        at_ns=at_ns,
        campaign_id=None,
        postings=merge_postings(amounts),
        quantity_events=(moved_out, *moved_in),
        contracts=(old_terms, new_terms),
        fee_lines=(),
        input_refs=(action_ref,),
    )


def _moved_in(lots: tuple[Lot, ...], contract_id: str) -> tuple[QuantityEvent, ...]:
    """Return one ADJUST_IN per lot, reopening it under ``contract_id`` otherwise unchanged."""
    return tuple(
        QuantityEvent(
            contract_id,
            QuantityKind.ADJUST_IN,
            lot.quantity,
            dataclasses.replace(lot, instrument_id=contract_id),
            (),
        )
        for lot in lots
    )


def _require_deliverable_change_only(
    state: LedgerState, old_terms: ContractTerms, new_terms: ContractTerms
) -> None:
    """Reject an action that changes kept terms or reuses a known contract id."""
    changed = [name for name in _KEPT_TERMS if getattr(old_terms, name) != getattr(new_terms, name)]
    if changed:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_CORPORATE_ACTION,
            f"adjustment of {old_terms.contract_id} changes {changed}",
        )
    if new_terms.contract_id in state.contracts:
        raise UnsupportedLifecycle(
            ErrorCode.UNSUPPORTED_CORPORATE_ACTION,
            f"adjusted contract id {new_terms.contract_id} is already registered",
        )
