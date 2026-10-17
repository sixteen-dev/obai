"""FIFO lots and the position-quantity journal (ADR 0001 §5, design §11.1).

All lots of one instrument share one sign. A quantity change first relieves the oldest lots of
the opposite sign; whatever it does not absorb opens one new lot. No lot ever crosses zero.
"""

import dataclasses
from collections.abc import Mapping, Sequence

from options_backtest.errors import LedgerInvariantError
from options_backtest.models.ledger import LedgerState, Lot, LotRelief, QuantityEvent
from options_backtest.models.market import ContractTerms


def fifo_relief(lots: Sequence[Lot], delta: int) -> tuple[LotRelief, ...]:
    """Return the reliefs a signed quantity change causes, oldest lot first.

    Args:
        lots: One instrument's open lots in FIFO order, all of one sign.
        delta: Signed quantity change, nonzero.

    Returns:
        One relief per touched lot; only the last may be partial. Empty when nothing is held or
        ``delta`` has the held sign.

    Raises:
        TypeError: If ``delta`` is not exactly ``int``.
        ValueError: If ``delta`` is zero.
        LedgerInvariantError: If ``lots`` mixes signs.

    """
    if type(delta) is not int:
        raise TypeError(f"fifo_relief delta must be int, got {type(delta).__name__}")
    if delta == 0:
        raise ValueError("fifo_relief delta must be nonzero")
    if len({lot.quantity > 0 for lot in lots}) > 1:
        raise LedgerInvariantError("lots of both signs are held in one instrument")
    if not lots or (lots[0].quantity > 0) == (delta > 0):
        return ()
    remaining = abs(delta)
    reliefs: list[LotRelief] = []
    for lot in lots:
        taken = min(abs(lot.quantity), remaining)
        signed = taken if lot.quantity > 0 else -taken
        reliefs.append(LotRelief(lot.lot_id, signed, lot.unit_cost.scaled_by(taken)))
        remaining -= taken
        if remaining == 0:
            break
    return tuple(reliefs)


def held_contract(state: LedgerState, contract_id: str) -> tuple[ContractTerms, tuple[Lot, ...]]:
    """Return a registered, unretired contract's terms and its open lots.

    Args:
        state: Ledger state.
        contract_id: Contract to look up.

    Returns:
        The registered terms and the non-empty FIFO lots.

    Raises:
        LedgerInvariantError: If the contract is unregistered, retired or not held.

    """
    terms = state.contracts.get(contract_id)
    if terms is None:
        raise LedgerInvariantError(f"contract {contract_id} is not registered")
    if contract_id in state.retired:
        raise LedgerInvariantError(f"contract {contract_id} is retired (SettledOnce)")
    lots = state.lots.get(contract_id, ())
    if not lots:
        raise LedgerInvariantError(f"contract {contract_id} is not held")
    return terms, lots


def open_remainder(delta: int, reliefs: Sequence[LotRelief]) -> int:
    """Return the part of ``delta`` its reliefs did not absorb; nonzero opens a new lot.

    Args:
        delta: Signed quantity change.
        reliefs: The change's reliefs, each carrying its lot's (opposite) sign.

    Returns:
        ``delta + Σ relief.quantity``, with the sign of ``delta`` or zero.

    """
    return delta + sum(relief.quantity for relief in reliefs)


def apply_quantity_events(
    lots: Mapping[str, tuple[Lot, ...]], events: Sequence[QuantityEvent]
) -> dict[str, tuple[Lot, ...]]:
    """Apply quantity events in order, verifying each against a recomputed FIFO relief.

    Args:
        lots: Open lots per instrument before the events; not modified.
        events: Events to apply, in order.

    Returns:
        Open lots per instrument afterwards; instruments left flat are absent.

    Raises:
        LedgerInvariantError: If an event's reliefs differ from ``fifo_relief``, its opened
            lot is not exactly the unrelieved remainder, or the opened lot's id is already held
            in its instrument.

    """
    result = dict(lots)
    for event in events:
        updated = _apply_event(result.get(event.instrument_id, ()), event)
        if updated:
            result[event.instrument_id] = updated
        else:
            result.pop(event.instrument_id, None)
    return result


def _apply_event(held: tuple[Lot, ...], event: QuantityEvent) -> tuple[Lot, ...]:
    reliefs = fifo_relief(held, event.delta)
    if event.reliefs != reliefs:
        raise LedgerInvariantError(
            f"{event.instrument_id}: reliefs {event.reliefs} are not the FIFO reliefs {reliefs}"
        )
    remaining = _unrelieved_lots(held, reliefs)
    remainder = open_remainder(event.delta, reliefs)
    if event.opened is None and remainder == 0:
        return remaining
    if event.opened is None or event.opened.quantity != remainder:
        raise LedgerInvariantError(
            f"{event.instrument_id}: the opened lot must hold the unrelieved {remainder}, "
            f"got {event.opened}"
        )
    if event.opened.lot_id in {lot.lot_id for lot in remaining}:
        raise LedgerInvariantError(f"{event.instrument_id}: lot id {event.opened.lot_id} is held")
    return (*remaining, event.opened)


def _unrelieved_lots(held: tuple[Lot, ...], reliefs: tuple[LotRelief, ...]) -> tuple[Lot, ...]:
    """Return ``held`` less its FIFO ``reliefs``; the last relieved lot may survive reduced."""
    if not reliefs:
        return held
    last = held[len(reliefs) - 1]
    left = last.quantity - reliefs[-1].quantity
    kept = held[len(reliefs) :]
    return kept if left == 0 else (dataclasses.replace(last, quantity=left), *kept)
