"""Deposit and option-trade booking branches beyond the fixtures (ADR 0001 §5 posting functions)."""

import pytest
from ledger_cases import (
    CAPITAL,
    CASH,
    EXPIRES_AT_NS,
    RECEIVABLE,
    SETTLES_ON,
    funded,
    option,
    option_cost,
    price,
    realized,
    stock_lot,
    trade,
    usd,
)

from options_backtest.engine.journal import apply_entry
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.errors import ErrorCode, LedgerInvariantError, UnsupportedLifecycle
from options_backtest.models.ledger import (
    EntryKind,
    LedgerEntry,
    LedgerState,
    LegFill,
    Lot,
    LotRelief,
    QuantityKind,
)
from options_backtest.models.market import OptionType

PUT = option(OptionType.PUT, "100")


def test_deposit_is_the_first_entry_and_books_cash_against_capital() -> None:
    entry = book_deposit(event_id="deposit", at_ns=7, cash=usd("10.00"))

    assert (entry.sequence, entry.kind, entry.at_ns, entry.campaign_id) == (
        1,
        EntryKind.DEPOSIT,
        7,
        None,
    )
    assert apply_entry(LedgerState.empty(), entry).balances == {
        CASH: usd("10.00"),
        CAPITAL: usd("-10.00"),
    }


@pytest.mark.parametrize(
    ("cash", "stock", "match"),
    [
        pytest.param("-1.00", (), ">= 0", id="negative-cash"),
        pytest.param("0", (), "nothing", id="nothing"),
    ],
)
def test_deposit_guards(cash: str, stock: tuple[Lot, ...], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        book_deposit(event_id="deposit", at_ns=0, cash=usd(cash), stock=stock)


def test_depositing_short_stock_is_unsupported() -> None:
    with pytest.raises(UnsupportedLifecycle) as caught:
        book_deposit(event_id="deposit", at_ns=0, cash=usd("0"), stock=(stock_lot(-100, "1"),))
    assert caught.value.code is ErrorCode.UNSUPPORTED_ACCOUNT_STATE


def _book(state: LedgerState, *legs: LegFill) -> LedgerEntry:
    return book_option_trade(
        state,
        event_id="t",
        at_ns=1,
        campaign_id="c1",
        legs=legs,
        fees=(),
        settles_on=SETTLES_ON,
    )


def test_a_trade_needs_a_leg() -> None:
    with pytest.raises(ValueError, match="leg"):
        _book(funded())


def test_a_package_cannot_trade_one_contract_twice() -> None:
    legs = (LegFill(PUT, 1, price("1.00")), LegFill(PUT, -1, price("1.10")))

    with pytest.raises(LedgerInvariantError, match="twice"):
        _book(funded(), *legs)


def test_a_reversing_fill_closes_fifo_then_opens_the_remainder_in_one_event() -> None:
    # Long 1 bought at 1.00 (cost 100); sell 3 at 2.00 (+600): close 1 for a 100 gain, open -2.
    state = trade(funded(), (PUT, 1, "1.00"))
    held = state.lots[PUT.contract_id][0]

    entry = book_option_trade(
        state,
        event_id="reverse",
        at_ns=5,
        campaign_id="c2",
        legs=(LegFill(PUT, -3, price("2.00")),),
        fees=(),
        settles_on=SETTLES_ON,
    )
    after = apply_entry(state, entry)

    (event,) = entry.quantity_events
    assert (event.kind, event.delta) == (QuantityKind.CLOSE, -3)
    assert event.reliefs == (LotRelief(held.lot_id, 1, usd("100.00")),)
    assert event.opened == Lot(
        "reverse:" + PUT.contract_id, PUT.contract_id, -2, usd("200.00"), "c2", 5
    )
    assert after.lots[PUT.contract_id] == (event.opened,)
    assert {p.account: p.amount for p in entry.postings} == {
        RECEIVABLE: usd("600.00"),
        option_cost(PUT.contract_id): usd("-500.00"),
        realized(PUT.contract_id): usd("-100.00"),
    }


def test_an_opening_fill_is_an_open_event_and_a_zero_premium_posts_nothing() -> None:
    entry = book_option_trade(
        funded(),
        event_id="free",
        at_ns=1,
        campaign_id="c1",
        legs=(LegFill(PUT, 2, price("0")),),
        fees=(),
        settles_on=SETTLES_ON,
    )

    assert [(e.kind, e.delta, e.reliefs) for e in entry.quantity_events] == [
        (QuantityKind.OPEN, 2, ())
    ]
    assert entry.postings == ()
    assert entry.contracts == (PUT,)


def test_a_trade_needs_a_campaign() -> None:
    with pytest.raises(ValueError, match="campaign_id"):
        book_option_trade(
            funded(),
            event_id="t",
            at_ns=1,
            campaign_id="",
            legs=(LegFill(PUT, 1, price("1.00")),),
            fees=(),
            settles_on=SETTLES_ON,
        )


def _sell_put_at(state: LedgerState, at_ns: int) -> LedgerEntry:
    return book_option_trade(
        state,
        event_id="sell-put",
        at_ns=at_ns,
        campaign_id="c1",
        legs=(LegFill(PUT, -1, price("2.00")),),
        fees=(),
        settles_on=SETTLES_ON,
    )


@pytest.mark.parametrize("after_expiry_ns", [0, 1, 86_400 * 10**9])
def test_a_trade_at_or_after_expiry_raises(after_expiry_ns: int) -> None:
    state = funded()

    with pytest.raises(LedgerInvariantError, match="expir"):
        apply_entry(state, _sell_put_at(state, EXPIRES_AT_NS + after_expiry_ns))


def test_a_trade_just_before_expiry_is_booked() -> None:
    state = funded()

    after = apply_entry(state, _sell_put_at(state, EXPIRES_AT_NS - 1))

    assert [lot.quantity for lot in after.lots[PUT.contract_id]] == [-1]
