"""Deliverable adjustment branches beyond F05 (ADR 0001 §5, design §12.5)."""

import dataclasses
from decimal import Decimal

import pytest
from ledger_cases import funded, option, option_cost, trade, usd

from options_backtest.engine.adjustments import book_deliverable_adjustment
from options_backtest.engine.journal import apply_entry
from options_backtest.errors import ErrorCode, LedgerInvariantError, UnsupportedLifecycle
from options_backtest.models.ledger import EntryKind, LedgerEntry, LedgerState, QuantityKind
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    OptionType,
)

OLD = option(OptionType.PUT, "60", physical=True)
NEW = dataclasses.replace(
    OLD,
    contract_id="XYZ-P60-adjusted",
    deliverable=Deliverable("XYZ-x50", (DeliverableComponent("XYZ", Decimal(50)),), usd("0")),
)


def adjust(
    state: LedgerState, old: ContractTerms = OLD, new: ContractTerms = NEW, ref: str = "split"
) -> LedgerEntry:
    return book_deliverable_adjustment(
        state,
        event_id=f"adjust-{state.entry_count + 1}",
        at_ns=state.last_at_ns + 1,
        old_contract_id=old.contract_id,
        new_terms=new,
        action_ref=ref,
    )


def test_every_lot_moves_in_fifo_order_keeping_its_identity() -> None:
    state = trade(trade(funded(), (OLD, 1, "10.00"), campaign="a"), (OLD, 2, "12.00"), campaign="b")
    old_lots = state.lots[OLD.contract_id]

    entry = adjust(state)
    after = apply_entry(state, entry)

    assert (entry.kind, entry.campaign_id, entry.input_refs) == (
        EntryKind.DELIVERABLE_ADJUSTMENT,
        None,
        ("split",),
    )
    assert entry.contracts == (OLD, NEW)
    assert [(e.instrument_id, e.kind, e.delta) for e in entry.quantity_events] == [
        (OLD.contract_id, QuantityKind.ADJUST_OUT, -3),
        (NEW.contract_id, QuantityKind.ADJUST_IN, 1),
        (NEW.contract_id, QuantityKind.ADJUST_IN, 2),
    ]
    assert after.lots[NEW.contract_id] == tuple(
        dataclasses.replace(lot, instrument_id=NEW.contract_id) for lot in old_lots
    )
    assert {p.account: p.amount for p in entry.postings} == {
        option_cost(OLD.contract_id): usd("-3400.00"),
        option_cost(NEW.contract_id): usd("3400.00"),
    }


def test_a_short_position_moves_its_negative_cost() -> None:
    state = trade(funded(), (OLD, -2, "10.00"))

    after = apply_entry(state, adjust(state))

    assert [lot.quantity for lot in after.lots[NEW.contract_id]] == [-2]
    assert after.balances[option_cost(NEW.contract_id)] == usd("-2000.00")


def test_an_unregistered_contract_cannot_be_adjusted() -> None:
    with pytest.raises(LedgerInvariantError, match="registered"):
        adjust(funded())


def test_a_flat_contract_cannot_be_adjusted() -> None:
    state = trade(trade(funded(), (OLD, 1, "10.00")), (OLD, -1, "10.00"))

    with pytest.raises(LedgerInvariantError, match="held"):
        adjust(state)


def test_a_retired_contract_cannot_be_adjusted_again() -> None:
    state = trade(funded(), (OLD, 1, "10.00"))
    adjusted = apply_entry(state, adjust(state))
    again = dataclasses.replace(NEW, contract_id="XYZ-P60-adjusted-twice")

    with pytest.raises(LedgerInvariantError, match="retired"):
        adjust(adjusted, OLD, again)


def test_the_new_contract_id_must_be_unregistered() -> None:
    state = trade(trade(funded(), (OLD, 1, "10.00")), (NEW, 1, "10.00"))

    with pytest.raises(UnsupportedLifecycle) as caught:
        adjust(state)
    assert caught.value.code is ErrorCode.UNSUPPORTED_CORPORATE_ACTION


def test_the_action_reference_is_required() -> None:
    state = trade(funded(), (OLD, 1, "10.00"))

    with pytest.raises(ValueError, match="action_ref"):
        adjust(state, ref="")


def test_the_new_terms_are_contract_terms() -> None:
    state = trade(funded(), (OLD, 1, "10.00"))

    with pytest.raises(TypeError, match="ContractTerms"):
        adjust(state, new="XYZ-P60-adjusted")  # type: ignore[arg-type]
