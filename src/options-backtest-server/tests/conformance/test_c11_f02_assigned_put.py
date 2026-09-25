"""C11: fixture F02, a cash-secured put assigned into long stock.

Design §18.3 (F02), §11.2 (full-AEA reserve, receivables never fund), §12.2 (short put
assignment: +100 shares, -100 x K cash); ADR 0001 §5 (worked F02 postings) and §11 (assigned
stock carries AEA as unit cost, the premium is realized separately).

Hand check. Sell the 100 put at 2.00 (+200), fee 1: cash 10,000 + 200 - 1 = 10,199. Marked at
the entry price the option is worth -200, so NLV 9,999: equity moves by the fee only, never by
the premium or the 10,000 reserve. Assignment pays the AEA 10,000 for 100 shares: cash 199;
100 x 90 = 9,000 of stock; NLV 9,199; profit 9,199 - 10,000 = -801.
"""

from collections.abc import Mapping
from dataclasses import dataclass
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
    total,
    usd,
)

from options_backtest.engine.exercise import book_physical_exercise
from options_backtest.engine.fees import AssumedFlatFeeSchedule, lifecycle_fees, trade_fees
from options_backtest.engine.funding import campaign_encumbrances, funding_headroom
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.engine.valuation import MarkBasis, net_pnl, reconcile, value_account
from options_backtest.models.ledger import (
    AccountKind,
    FeeEvent,
    FeeLine,
    LedgerEntry,
    LedgerState,
    LegFill,
)
from options_backtest.models.market import ContractTerms, OptionType, Quote

CAMPAIGN = "F02"
ENTRY_DAY = 0
ASSIGNMENT_DAY = 2


@dataclass(frozen=True)
class F02Run:
    """Every ledger state and entry of the F02 put sale and assignment."""

    data: Mapping[str, Any]
    schedule: AssumedFlatFeeSchedule
    put: ContractTerms
    entry_fees: tuple[FeeLine, ...]
    opened: LedgerState
    opened_settled: LedgerState
    assignment: LedgerEntry
    assignment_fees: tuple[FeeLine, ...]
    assigned_settled: LedgerState

    @property
    def expected(self) -> Mapping[str, Any]:
        """Return the fixture's expected values."""
        return dict(self.data["expected"])

    def entry_price_quotes(self) -> dict[str, Quote]:
        """Return a quote whose bid, ask and mid are all the entry price."""
        entry_price = price(self.data["entry_price"])
        return {self.put.contract_id: Quote(entry_price, entry_price)}


def _open(
    data: Mapping[str, Any], schedule: AssumedFlatFeeSchedule, put: ContractTerms
) -> tuple[tuple[FeeLine, ...], LedgerState]:
    deposit = book_deposit(
        event_id="f02-deposit", at_ns=moment(ENTRY_DAY), cash=usd(data["initial_cash_usd"])
    )
    funded = apply_entry(LedgerState.empty(), deposit)
    legs = (LegFill(put, data["contract"]["position_quantity"], price(data["entry_price"])),)
    fees = trade_fees(schedule, legs)
    entry = book_option_trade(
        funded,
        event_id="f02-entry",
        at_ns=moment(ENTRY_DAY, 1),
        campaign_id=CAMPAIGN,
        legs=legs,
        fees=fees,
        settles_on=settle_day(ENTRY_DAY + 1),
    )
    return fees, apply_entry(funded, entry)


@pytest.fixture(scope="module")
def f02() -> F02Run:
    data = fixture("F02")
    schedule = fixture_fee_schedule()
    contract = data["contract"]
    put = stock_option(
        OptionType(contract["option_type"]),
        contract["strike"],
        shares=contract["deliverable_shares"],
        aggregate=contract["aggregate_exercise_amount_usd"],
        multiplier=contract["premium_multiplier"],
    )
    entry_fees, opened = _open(data, schedule, put)
    opened_settled = settle_through(
        opened, event_id="f02-settle-entry", at_ns=moment(1), through=settle_day(ENTRY_DAY + 1)
    )
    assignment_fees = lifecycle_fees(schedule, FeeEvent.EXERCISE_ASSIGNMENT, 1)
    assignment = book_physical_exercise(
        opened_settled,
        event_id="f02-assignment",
        at_ns=moment(ASSIGNMENT_DAY),
        contract_id=put.contract_id,
        contracts=1,
        fees=assignment_fees,
        settles_on=settle_day(ASSIGNMENT_DAY + 1),
    )
    assigned_settled = settle_through(
        apply_entry(opened_settled, assignment),
        event_id="f02-settle-assignment",
        at_ns=moment(ASSIGNMENT_DAY + 1),
        through=settle_day(ASSIGNMENT_DAY + 1),
    )
    return F02Run(
        data,
        schedule,
        put,
        entry_fees,
        opened,
        opened_settled,
        assignment,
        assignment_fees,
        assigned_settled,
    )


def test_cash_after_entry(f02: F02Run) -> None:
    cash = usd(f02.expected["cash_after_entry_usd"])

    assert total(line.amount for line in f02.entry_fees) == f02.schedule.trade_per_contract
    assert balance(f02.opened_settled, account(AccountKind.CASH)) == cash
    assert cash_like(f02.opened_settled) == cash


def test_nlv_at_the_entry_price_moves_by_the_fee_only(f02: F02Run) -> None:
    for state in (f02.opened, f02.opened_settled):
        valuation = value_account(state, f02.entry_price_quotes(), {}, MarkBasis.MID)

        assert valuation.nlv == usd(f02.expected["entry_nlv_at_entry_price_usd"])
        assert reconcile(valuation, state) == ZERO


def test_reserve_is_the_full_aggregate_exercise_amount_and_stays_out_of_nlv(
    f02: F02Run,
) -> None:
    encumbrances = campaign_encumbrances(f02.opened_settled, f02.schedule)
    valuation = value_account(f02.opened_settled, f02.entry_price_quotes(), {}, MarkBasis.MID)

    assert set(encumbrances) == {CAMPAIGN}
    assert encumbrances[CAMPAIGN].settlement == usd(
        f02.data["contract"]["aggregate_exercise_amount_usd"]
    )
    assert encumbrances[CAMPAIGN].fee_provision == f02.schedule.trade_per_contract.scaled_by(1)
    assert valuation.nlv == usd(f02.expected["entry_nlv_at_entry_price_usd"])


def test_premium_receivable_cannot_fund_the_reserve_before_it_settles(f02: F02Run) -> None:
    # CASH 10,000 - fee payable 1 - (AEA 10,000 + provision 1) = -2: the 200 receivable does not
    # count until it settles, after which 10,199 - 10,001 = 198.
    premium = usd("200.00")
    fee = f02.schedule.trade_per_contract
    reserve = usd(f02.data["contract"]["aggregate_exercise_amount_usd"]) + fee

    before = funding_headroom(f02.opened, f02.schedule)
    after = funding_headroom(f02.opened_settled, f02.schedule)

    assert before == usd(f02.data["initial_cash_usd"]) - fee - reserve
    assert after - before == premium


def test_assignment_buys_stock_at_aea_and_realizes_the_premium_separately(f02: F02Run) -> None:
    assert total(line.amount for line in f02.assignment_fees) == ZERO
    assert posting_map(f02.assignment) == {
        dated(AccountKind.PAYABLE, ASSIGNMENT_DAY + 1): usd(f02.expected["exercise_cash_flow_usd"]),
        account(AccountKind.STOCK_COST, STOCK_ASSET): usd("10000.00"),
        account(AccountKind.OPTION_COST, f02.put.contract_id): usd("200.00"),
        account(AccountKind.REALIZED_PNL, f02.put.contract_id): usd("-200.00"),
    }


def test_assigned_stock_opens_one_lot_at_aea_per_share(f02: F02Run) -> None:
    state = f02.assigned_settled
    lots = state.lots[STOCK_ASSET]

    assert [(lot.instrument_id, lot.quantity, lot.unit_cost) for lot in lots] == [
        (STOCK_ASSET, f02.expected["final_stock_shares"], usd("100.00"))
    ]
    assert balance(state, account(AccountKind.STOCK_COST, STOCK_ASSET)) == total(
        lot.unit_cost.scaled_by(lot.quantity) for lot in lots
    )


def test_final_state_matches_the_fixture(f02: F02Run) -> None:
    expected = f02.expected
    state = f02.assigned_settled
    stock_prices = {STOCK_ASSET: price(f02.data["stock_mark_after_assignment"])}
    valuation = value_account(state, {}, stock_prices, MarkBasis.MID)

    assert held_quantity(state, STOCK_ASSET) == expected["final_stock_shares"]
    assert balance(state, account(AccountKind.CASH)) == usd(expected["final_cash_usd"])
    assert cash_like(state) == usd(expected["final_cash_usd"])
    assert valuation.nlv - cash_like(state) == usd(expected["final_stock_value_usd"])
    assert held_quantity(state, f02.put.contract_id) == expected["final_option_quantity"]
    assert valuation.nlv == usd(expected["final_nlv_usd"])
    assert net_pnl(valuation, state) == usd(expected["net_profit_usd"])
    assert reconcile(valuation, state) == ZERO
