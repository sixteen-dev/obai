"""C12: fixture F03, a covered call whose assignment delivers the stock already owned.

Design §18.3 (F03), §12.2 (short call assignment: -100 shares, +100 x K cash; reclassify covered
lots before creating short inventory); ADR 0001 §5 (deposit at stated unit cost, delivered
shares relieve held lots FIFO, over-delivery raises ``UnsupportedLifecycle``).

Hand check. Deposit 100 shares at 90 and no cash: STOCK_COST +9,000 / CAPITAL -9,000, NLV
9,000. Sell the 100 call at 3.00 (+300), fee 1: cash 299. Assignment delivers the 100 shares
(cost 9,000) for the AEA 10,000: stock gain 1,000, option premium 300 realized separately;
cash 299 + 10,000 = 10,299 = NLV; profit 10,299 - 9,000 = 1,299.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from builders import (
    STOCK_ASSET,
    ZERO,
    account,
    balance,
    cash_like,
    dated,
    fixture,
    fixture_fee_schedule,
    held_quantity,
    moment,
    posting_map,
    price,
    settle_day,
    settle_through,
    stock_option,
    usd,
)

from options_backtest.engine.exercise import book_physical_exercise
from options_backtest.engine.fees import AssumedFlatFeeSchedule, lifecycle_fees, trade_fees
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.engine.valuation import MarkBasis, net_pnl, reconcile, value_account
from options_backtest.errors import ErrorCode, UnsupportedLifecycle
from options_backtest.models.ledger import (
    AccountKind,
    FeeEvent,
    LedgerEntry,
    LedgerState,
    LegFill,
    Lot,
)
from options_backtest.models.market import ContractTerms, OptionType
from options_backtest.money import Price

CAMPAIGN = "F03"
DEPOSIT_LOT = "f03-deposited-stock"
CALL_PRICE = Price(Decimal("3.00"))  # the fixture's entry_price, asserted when the run is built
ENTRY_DAY = 0
ASSIGNMENT_DAY = 2


@dataclass(frozen=True)
class F03Run:
    """Every ledger state and entry of the F03 covered call."""

    data: Mapping[str, Any]
    call: ContractTerms
    deposit: LedgerEntry
    deposited: LedgerState
    opened_settled: LedgerState
    assignment: LedgerEntry
    assigned_settled: LedgerState

    @property
    def expected(self) -> Mapping[str, Any]:
        """Return the fixture's expected values."""
        return dict(self.data["expected"])

    def stock_prices(self) -> dict[str, Price]:
        """Return the initial stock mark, the only stock price the fixture states."""
        return {STOCK_ASSET: price(self.data["initial_stock_mark"])}


def _call(data: Mapping[str, Any]) -> ContractTerms:
    contract = data["contract"]
    return stock_option(
        OptionType(contract["option_type"]),
        contract["strike"],
        shares=contract["deliverable_shares"],
        aggregate=contract["aggregate_exercise_amount_usd"],
        multiplier=contract["premium_multiplier"],
    )


def _deposit(data: Mapping[str, Any]) -> LedgerEntry:
    lot = Lot(
        lot_id=DEPOSIT_LOT,
        instrument_id=STOCK_ASSET,
        quantity=data["initial_stock_shares"],
        unit_cost=usd(data["initial_stock_mark"]),
        campaign_id=None,
        opened_at_ns=moment(ENTRY_DAY),
    )
    return book_deposit(
        event_id="f03-deposit",
        at_ns=moment(ENTRY_DAY),
        cash=usd(data["initial_cash_usd"]),
        stock=(lot,),
    )


def _sell_calls(
    state: LedgerState, schedule: AssumedFlatFeeSchedule, call: ContractTerms, contracts: int
) -> LedgerState:
    legs = (LegFill(call, -contracts, CALL_PRICE),)
    entry = book_option_trade(
        state,
        event_id="f03-entry",
        at_ns=moment(ENTRY_DAY, 1),
        campaign_id=CAMPAIGN,
        legs=legs,
        fees=trade_fees(schedule, legs),
        settles_on=settle_day(ENTRY_DAY + 1),
    )
    return apply_entry(state, entry)


def _assign(
    state: LedgerState, schedule: AssumedFlatFeeSchedule, call: ContractTerms, contracts: int
) -> LedgerEntry:
    return book_physical_exercise(
        state,
        event_id="f03-assignment",
        at_ns=moment(ASSIGNMENT_DAY),
        contract_id=call.contract_id,
        contracts=contracts,
        fees=lifecycle_fees(schedule, FeeEvent.EXERCISE_ASSIGNMENT, contracts),
        settles_on=settle_day(ASSIGNMENT_DAY + 1),
    )


@pytest.fixture(scope="module")
def f03() -> F03Run:
    data = fixture("F03")
    schedule = fixture_fee_schedule()
    call = _call(data)
    assert price(data["entry_price"]) == CALL_PRICE
    deposit = _deposit(data)
    deposited = apply_entry(LedgerState.empty(), deposit)
    opened_settled = settle_through(
        _sell_calls(deposited, schedule, call, -data["contract"]["position_quantity"]),
        event_id="f03-settle-entry",
        at_ns=moment(1),
        through=settle_day(ENTRY_DAY + 1),
    )
    assignment = _assign(opened_settled, schedule, call, 1)
    assigned_settled = settle_through(
        apply_entry(opened_settled, assignment),
        event_id="f03-settle-assignment",
        at_ns=moment(ASSIGNMENT_DAY + 1),
        through=settle_day(ASSIGNMENT_DAY + 1),
    )
    return F03Run(data, call, deposit, deposited, opened_settled, assignment, assigned_settled)


def test_deposit_posts_stock_at_its_stated_cost_and_zero_cash_posts_nothing(f03: F03Run) -> None:
    assert posting_map(f03.deposit) == {
        account(AccountKind.STOCK_COST, STOCK_ASSET): usd("9000.00"),
        account(AccountKind.CAPITAL): usd("-9000.00"),
    }


def test_initial_nlv(f03: F03Run) -> None:
    valuation = value_account(f03.deposited, {}, f03.stock_prices(), MarkBasis.MID)

    assert valuation.nlv == usd(f03.expected["initial_nlv_usd"])
    assert net_pnl(valuation, f03.deposited) == ZERO
    assert reconcile(valuation, f03.deposited) == ZERO


def test_cash_after_entry(f03: F03Run) -> None:
    cash = usd(f03.expected["cash_after_entry_usd"])

    assert balance(f03.opened_settled, account(AccountKind.CASH)) == cash
    assert cash_like(f03.opened_settled) == cash


def test_assignment_delivers_the_owned_lot_once_at_aea(f03: F03Run) -> None:
    # Per lot: cash side +10,000, cost side -9,000, REALIZED_PNL balances at -1,000 (a gain);
    # the call's -300 cost is relieved and realized separately.
    assert posting_map(f03.assignment) == {
        dated(AccountKind.RECEIVABLE, ASSIGNMENT_DAY + 1): usd(
            f03.expected["exercise_cash_flow_usd"]
        ),
        account(AccountKind.STOCK_COST, STOCK_ASSET): usd("-9000.00"),
        account(AccountKind.REALIZED_PNL, STOCK_ASSET): usd("-1000.00"),
        account(AccountKind.OPTION_COST, f03.call.contract_id): usd("300.00"),
        account(AccountKind.REALIZED_PNL, f03.call.contract_id): usd("-300.00"),
    }
    stock_events = [e for e in f03.assignment.quantity_events if e.instrument_id == STOCK_ASSET]
    assert [(event.delta, event.opened) for event in stock_events] == [(-100, None)]
    assert [
        (relief.lot_id, abs(relief.quantity), relief.cost) for relief in stock_events[0].reliefs
    ] == [(DEPOSIT_LOT, 100, usd("9000.00"))]


def test_no_stock_lot_remains_and_no_short_stock_is_created(f03: F03Run) -> None:
    state = f03.assigned_settled
    stock_lots = [
        lot for lots in state.lots.values() for lot in lots if lot.instrument_id == STOCK_ASSET
    ]

    assert stock_lots == []
    assert balance(state, account(AccountKind.STOCK_COST, STOCK_ASSET)) == ZERO


def test_final_state_matches_the_fixture(f03: F03Run) -> None:
    expected = f03.expected
    state = f03.assigned_settled
    valuation = value_account(state, {}, f03.stock_prices(), MarkBasis.MID)

    assert held_quantity(state, STOCK_ASSET) == expected["final_stock_shares"]
    assert held_quantity(state, f03.call.contract_id) == expected["final_option_quantity"]
    assert balance(state, account(AccountKind.CASH)) == usd(expected["final_cash_usd"])
    assert cash_like(state) == usd(expected["final_cash_usd"])
    assert valuation.nlv == usd(expected["final_nlv_usd"])
    assert net_pnl(valuation, state) == usd(expected["net_profit_usd"])
    assert reconcile(valuation, state) == ZERO


def test_delivering_more_shares_than_held_raises(f03: F03Run) -> None:
    # 100 shares held, two calls assigned: 200 to deliver would need short stock (§12.3).
    schedule = fixture_fee_schedule()
    oversold = settle_through(
        _sell_calls(f03.deposited, schedule, f03.call, 2),
        event_id="f03-settle-oversold-entry",
        at_ns=moment(1),
        through=settle_day(ENTRY_DAY + 1),
    )

    with pytest.raises(UnsupportedLifecycle) as caught:
        _assign(oversold, schedule, f03.call, 2)
    assert caught.value.code is ErrorCode.UNSUPPORTED_ACCOUNT_STATE
