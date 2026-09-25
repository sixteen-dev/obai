"""Due-cash transfer and cash-settlement branches beyond the fixtures (ADR 0001 §5, §11)."""

from datetime import date, datetime

import pytest
from ledger_cases import CASH, EXPIRES_AT_NS, SETTLES_ON, funded, option, price, trade, usd

from options_backtest.engine.journal import apply_entry
from options_backtest.engine.settlement import book_cash_settlement, book_settle_due
from options_backtest.engine.trades import book_option_trade
from options_backtest.errors import LedgerInvariantError, MissingMarkError
from options_backtest.models.ledger import (
    AccountKey,
    AccountKind,
    EntryKind,
    LedgerEntry,
    LedgerState,
    LegFill,
)
from options_backtest.models.market import OptionType

PUT = option(OptionType.PUT, "100")
CALL = option(OptionType.CALL, "105")
LATER = date(2026, 9, 25)


def test_nothing_due_books_nothing() -> None:
    assert book_settle_due(funded(), event_id="s", at_ns=1, through=SETTLES_ON) is None


def test_only_items_due_through_the_date_move_to_cash() -> None:
    state = trade(funded(), (PUT, -1, "2.00"))  # RECEIVABLE +200 due SETTLES_ON
    later_buy = book_option_trade(
        state,
        event_id="later-buy",
        at_ns=2,
        campaign_id="c1",
        legs=(LegFill(PUT, 2, price("1.00")),),
        fees=(),
        settles_on=LATER,
    )
    state = apply_entry(state, later_buy)  # PAYABLE -200 due LATER

    due = book_settle_due(state, event_id="s", at_ns=3, through=SETTLES_ON)

    assert due is not None
    assert (due.kind, due.sequence, due.campaign_id) == (EntryKind.SETTLE_DUE, 4, None)
    settled = apply_entry(state, due)
    assert settled.balances[CASH] == usd("100200.00")
    assert settled.balances[AccountKey(AccountKind.PAYABLE, LATER.isoformat())] == usd("-200.00")
    assert AccountKey(AccountKind.RECEIVABLE, SETTLES_ON.isoformat()) not in settled.balances


def test_settle_due_needs_a_date() -> None:
    with pytest.raises(TypeError, match="date"):
        book_settle_due(funded(), event_id="s", at_ns=1, through="2026-09-22")  # type: ignore[arg-type]


def test_settle_due_rejects_a_datetime() -> None:
    with pytest.raises(TypeError, match="date"):
        book_settle_due(funded(), event_id="s", at_ns=1, through=datetime(2026, 9, 22, 16, 0))


def test_a_missing_settlement_value_raises_instead_of_settling_at_zero() -> None:
    state = trade(funded(), (PUT, -1, "2.00"))

    with pytest.raises(MissingMarkError) as caught:
        book_cash_settlement(
            state,
            event_id="x",
            at_ns=EXPIRES_AT_NS,
            contract_ids=(PUT.contract_id,),
            settlement={},
            fees=(),
            settles_on=LATER,
        )
    assert caught.value.instrument_ids == ("SPX",)


def test_contract_ids_must_be_a_tuple() -> None:
    state = trade(funded(), (PUT, -1, "2.00"))

    with pytest.raises(LedgerInvariantError, match="tuple"):
        book_cash_settlement(
            state,
            event_id="x",
            at_ns=EXPIRES_AT_NS,
            contract_ids=[PUT.contract_id],  # type: ignore[arg-type]
            settlement={"SPX": price("97")},
            fees=(),
            settles_on=LATER,
        )


def _settle_put(state: LedgerState, at_ns: int) -> LedgerEntry:
    return book_cash_settlement(
        state,
        event_id="settle",
        at_ns=at_ns,
        contract_ids=(PUT.contract_id,),
        settlement={"SPX": price("97")},
        fees=(),
        settles_on=LATER,
    )


def test_cash_settlement_before_expiry_raises() -> None:
    state = trade(funded(), (PUT, -1, "2.00"))

    with pytest.raises(LedgerInvariantError, match="expir"):
        apply_entry(state, _settle_put(state, EXPIRES_AT_NS - 1))


def test_cash_settlement_at_expiry_retires_the_contract() -> None:
    state = trade(funded(), (PUT, -1, "2.00"))

    after = apply_entry(state, _settle_put(state, EXPIRES_AT_NS))

    assert PUT.contract_id in after.retired
    assert PUT.contract_id not in after.lots


def test_a_flat_same_expiry_contract_cannot_be_reopened_after_the_package_settles() -> None:
    # The call was opened and closed, so settlement neither holds nor retires it; it still
    # expired with the package and cannot trade again.
    state = trade(trade(trade(funded(), (PUT, -1, "2.00")), (CALL, 1, "1.00")), (CALL, -1, "1.00"))
    settled = apply_entry(state, _settle_put(state, EXPIRES_AT_NS))
    assert CALL.contract_id not in settled.retired
    reopen = book_option_trade(
        settled,
        event_id="reopen",
        at_ns=EXPIRES_AT_NS + 1,
        campaign_id="c1",
        legs=(LegFill(CALL, -1, price("0.50")),),
        fees=(),
        settles_on=LATER,
    )

    with pytest.raises(LedgerInvariantError, match="expir"):
        apply_entry(settled, reopen)
