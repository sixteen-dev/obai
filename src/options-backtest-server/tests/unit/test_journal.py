"""``apply_entry`` rejections, ``Journal`` and ``replay`` (ADR 0001 §5 "The one transition").

The conformance suite covers stale sequence, unbalanced, sub-cent cash, cost/lot disagreement,
retired contracts and repeated event ids; these are the remaining invariants.
"""

import dataclasses
from collections.abc import Callable

import pytest
from ledger_cases import (
    CAPITAL,
    CASH,
    PAYABLE,
    RECEIVABLE,
    SETTLES_ON,
    funded,
    option,
    option_cost,
    price,
    stock_cost,
    stock_lot,
    trade,
    usd,
)

from options_backtest.engine.fees import AssumedFlatFeeSchedule, trade_fees
from options_backtest.engine.journal import Journal, apply_entry, replay
from options_backtest.engine.positions import fifo_relief
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.errors import LedgerInvariantError
from options_backtest.models.ledger import (
    AccountKey,
    AccountKind,
    EntryKind,
    FeeEvent,
    FeeLine,
    LedgerEntry,
    LedgerState,
    LegFill,
    Lot,
    Posting,
    QuantityEvent,
    QuantityKind,
    merge_postings,
)
from options_backtest.models.market import ContractTerms, OptionType

PUT = option(OptionType.PUT, "100")


def entry(
    state: LedgerState,
    postings: tuple[Posting, ...],
    *,
    events: tuple[QuantityEvent, ...] = (),
    contracts: tuple[ContractTerms, ...] = (),
    at_ns: int | None = None,
) -> LedgerEntry:
    return LedgerEntry(
        event_id=f"hand-{state.entry_count + 1}",
        sequence=state.entry_count + 1,
        kind=EntryKind.TRADE,
        at_ns=state.last_at_ns if at_ns is None else at_ns,
        campaign_id=None,
        postings=postings,
        quantity_events=events,
        contracts=contracts,
        fee_lines=(),
        input_refs=(),
    )


def moves(*pairs: tuple[AccountKey, str]) -> tuple[Posting, ...]:
    return merge_postings((key, usd(amount)) for key, amount in pairs)


def test_time_cannot_go_backwards() -> None:
    state = trade(funded(), (PUT, 1, "1.00"))

    with pytest.raises(LedgerInvariantError, match="at_ns"):
        apply_entry(state, entry(state, moves((CASH, "1"), (CAPITAL, "-1")), at_ns=0))


def test_a_zero_posting_raises() -> None:
    state = funded()
    postings = (Posting(CAPITAL, usd("-1")), Posting(CASH, usd("1")), Posting(PAYABLE, usd("0")))

    with pytest.raises(LedgerInvariantError, match="zero"):
        apply_entry(state, entry(state, postings))


@pytest.mark.parametrize(
    "postings",
    [
        pytest.param((Posting(CASH, usd("1")), Posting(CAPITAL, usd("-1"))), id="unsorted"),
        pytest.param(
            (Posting(CAPITAL, usd("-1")), Posting(CAPITAL, usd("-1")), Posting(CASH, usd("2"))),
            id="unmerged",
        ),
    ],
)
def test_postings_must_be_merged_and_sorted(postings: tuple[Posting, ...]) -> None:
    state = funded()

    with pytest.raises(LedgerInvariantError, match="canonical"):
        apply_entry(state, entry(state, postings))


@pytest.mark.parametrize(
    ("account", "amount", "match"),
    [
        pytest.param(RECEIVABLE, "-5", "RECEIVABLE", id="negative-receivable"),
        pytest.param(PAYABLE, "5", "PAYABLE", id="positive-payable"),
    ],
)
def test_open_items_keep_their_sign(account: AccountKey, amount: str, match: str) -> None:
    state = funded()
    postings = moves((account, amount), (CASH, str(-usd(amount).amount)))

    with pytest.raises(LedgerInvariantError, match=match):
        apply_entry(state, entry(state, postings))


def test_registered_terms_cannot_change() -> None:
    state = trade(funded(), (PUT, 1, "1.00"))
    changed = dataclasses.replace(PUT, aggregate_exercise_amount=usd("9999.00"))

    with pytest.raises(LedgerInvariantError, match="terms"):
        apply_entry(state, entry(state, (), contracts=(changed,)))


def test_a_contract_id_cannot_reuse_a_held_stock_instrument() -> None:
    state = funded(stock=(stock_lot(100, "90.00"),))
    clash = dataclasses.replace(PUT, contract_id="XYZ")

    with pytest.raises(LedgerInvariantError, match="stock"):
        apply_entry(state, entry(state, (), contracts=(clash,)))


def test_cost_postings_must_use_the_instruments_cost_kind() -> None:
    lot = stock_lot(100, "90.00")
    good = book_deposit(event_id="deposit", at_ns=0, cash=usd("0"), stock=(lot,))
    postings = moves((option_cost("XYZ"), "9000.00"), (CAPITAL, "-9000.00"))

    with pytest.raises(LedgerInvariantError, match="cost"):
        apply_entry(LedgerState.empty(), dataclasses.replace(good, postings=postings))
    assert apply_entry(LedgerState.empty(), good).balances[stock_cost("XYZ")] == usd("9000.00")


def _expire_one_of_two(state: LedgerState, instrument: str) -> LedgerEntry:
    reliefs = fifo_relief(state.lots[instrument], -1)
    event = QuantityEvent(instrument, QuantityKind.EXPIRATION, -1, None, reliefs)
    postings = moves((option_cost(instrument), "-100.00"), (CASH, "100.00"))
    return entry(state, postings, events=(event,))


def test_retirement_leaves_the_contract_flat() -> None:
    state = trade(funded(), (PUT, 2, "1.00"))

    with pytest.raises(LedgerInvariantError, match="retire"):
        apply_entry(state, _expire_one_of_two(state, PUT.contract_id))


def test_only_a_contract_can_retire() -> None:
    state = funded(stock=(stock_lot(100, "1.00"),))
    reliefs = fifo_relief(state.lots["XYZ"], -100)
    event = QuantityEvent("XYZ", QuantityKind.EXPIRATION, -100, None, reliefs)
    postings = moves((stock_cost("XYZ"), "-100.00"), (CASH, "100.00"))

    with pytest.raises(LedgerInvariantError, match="retire"):
        apply_entry(state, entry(state, postings, events=(event,)))


def test_journal_commits_in_order_and_is_unchanged_by_a_rejected_entry() -> None:
    journal = Journal()
    deposit = book_deposit(event_id="deposit", at_ns=0, cash=usd("10.00"))

    state = journal.commit(deposit)
    unbalanced = dataclasses.replace(
        deposit, event_id="bad", sequence=2, postings=moves((CASH, "1"), (CAPITAL, "-2"))
    )
    with pytest.raises(LedgerInvariantError):
        journal.commit(unbalanced)

    assert journal.state == state
    assert journal.entries == (deposit,)
    assert state.balances == {CASH: usd("10.00"), CAPITAL: usd("-10.00")}


def test_replay_of_nothing_is_the_empty_state() -> None:
    assert replay(()) == LedgerState.empty()


def test_replay_reproduces_the_committed_state() -> None:
    journal = Journal()
    journal.commit(book_deposit(event_id="deposit", at_ns=0, cash=usd("10.00")))

    assert replay(journal.entries) == journal.state


def test_replay_rejects_a_repeated_event_id_as_the_journal_does() -> None:
    deposit = book_deposit(event_id="dup", at_ns=0, cash=usd("100000.00"))
    state = apply_entry(LedgerState.empty(), deposit)
    sale = book_option_trade(
        state,
        event_id="dup",
        at_ns=1,
        campaign_id="c1",
        legs=(LegFill(PUT, -1, price("2.00")),),
        fees=(),
        settles_on=SETTLES_ON,
    )
    apply_entry(state, sale)  # apply_entry alone does not know the journal's event ids

    with pytest.raises(LedgerInvariantError, match="already committed"):
        replay((deposit, sale))


def test_two_held_lots_cannot_share_a_lot_id() -> None:
    lots = (stock_lot(100, "90.00"), stock_lot(100, "95.00"))  # both named "deposited"

    with pytest.raises(LedgerInvariantError, match="lot id"):
        Journal().commit(book_deposit(event_id="deposit", at_ns=0, cash=usd("0"), stock=lots))


def test_a_cost_posting_beside_a_matching_cost_account_still_needs_the_right_kind() -> None:
    # XYZ is stock, so its STOCK_COST (unposted, unchanged) agrees; OPTION_COST[XYZ] is left over.
    state = funded(stock=(stock_lot(100, "90.00"),))
    postings = moves((option_cost("XYZ"), "1.00"), (CASH, "-1.00"))

    with pytest.raises(LedgerInvariantError, match="wrong cost kind"):
        apply_entry(state, entry(state, postings))


def _ghost_short_open(state: LedgerState) -> LedgerEntry:
    # OPEN of -1 in an id that is no registered contract, costed as stock.
    lot = Lot("ghost", "GHOST-OPTION", -1, usd("200.00"), None, 0)
    event = QuantityEvent("GHOST-OPTION", QuantityKind.OPEN, -1, lot, ())
    postings = moves((stock_cost("GHOST-OPTION"), "-200.00"), (RECEIVABLE, "200.00"))
    return entry(state, postings, events=(event,))


def _oversold_stock(state: LedgerState) -> LedgerEntry:
    # Delivers 150 of the 100 deposited XYZ shares: relieves the lot and opens -50.
    event = QuantityEvent(
        "XYZ",
        QuantityKind.DELIVERY,
        -150,
        Lot("oversold", "XYZ", -50, usd("90.00"), None, 0),
        fifo_relief(state.lots["XYZ"], -150),
    )
    postings = moves((stock_cost("XYZ"), "-13500.00"), (CASH, "13500.00"))
    return entry(state, postings, events=(event,))


@pytest.mark.parametrize(
    ("state", "build"),
    [
        pytest.param(funded(), _ghost_short_open, id="unregistered-id"),
        pytest.param(funded(stock=(stock_lot(100, "90.00"),)), _oversold_stock, id="oversold"),
    ],
)
def test_an_entry_leaving_short_stock_raises(
    state: LedgerState, build: Callable[[LedgerState], LedgerEntry]
) -> None:
    with pytest.raises(LedgerInvariantError, match="short stock"):
        apply_entry(state, build(state))


def _fee_paying_sale() -> tuple[LedgerState, LedgerEntry]:
    """Return a funded state and a real sale of one put paying a $1.00 trade fee."""
    state = funded()
    legs = (LegFill(PUT, -1, price("2.00")),)
    schedule = AssumedFlatFeeSchedule("flat", usd("1.00"), usd("0"), usd("0"))
    sale = book_option_trade(
        state,
        event_id="fee-paying-sale",
        at_ns=1,
        campaign_id="c1",
        legs=legs,
        fees=trade_fees(schedule, legs),
        settles_on=SETTLES_ON,
    )
    return state, sale


@pytest.mark.parametrize(
    "fee_lines",
    [
        pytest.param((), id="no-lines"),
        pytest.param(
            (FeeLine("flat:trade", FeeEvent.TRADE, 1, usd("1.00"), usd("0")),), id="zero-amount"
        ),
        pytest.param(
            (FeeLine("other", FeeEvent.TRADE, 1, usd("1.00"), usd("1.00")),), id="other-component"
        ),
    ],
)
def test_fee_lines_must_match_the_fees_postings(fee_lines: tuple[FeeLine, ...]) -> None:
    state, sale = _fee_paying_sale()
    assert apply_entry(state, sale).balances[AccountKey(AccountKind.FEES, "flat:trade")] == usd(
        "1.00"
    )

    with pytest.raises(LedgerInvariantError, match="fee lines"):
        apply_entry(state, dataclasses.replace(sale, fee_lines=fee_lines))


def test_a_lot_opened_outside_the_entrys_campaign_raises() -> None:
    state, sale = _fee_paying_sale()
    (event,) = sale.quantity_events
    assert event.opened is not None
    elsewhere = dataclasses.replace(event.opened, campaign_id="someone-else")
    forged = dataclasses.replace(
        sale, quantity_events=(dataclasses.replace(event, opened=elsewhere),)
    )

    with pytest.raises(LedgerInvariantError, match="campaign"):
        apply_entry(state, forged)
