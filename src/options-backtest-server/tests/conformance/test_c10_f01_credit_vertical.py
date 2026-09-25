"""C10: fixture F01, a credit put vertical opened and closed at natural prices.

Design §18.3 (F01), §11.1 (NLV), §11.2 (reserve, receivables never fund); ADR 0001 §5 (worked
F01 postings, views, funding rule).

Hand check. Entry: sell the 100 put at its 2.00 bid (+200), buy the 95 put at its 1.10 ask
(-110): credit 90, fees 2 x $1, cash 10,000 + 90 - 2 = 10,088. Mids 2.10 and 1.05 value the
options at -210 + 105 = -105, so NLV 9,983. Reserve: width 5 x 100 = 500; expiry loss before
fees 500 - 90 = 410. Exit: buy the 100 put at its 1.20 ask (-120), sell the 95 put at its 0.40
bid (+40): debit 80, fees 2, cash 10,088 - 82 = 10,006 = NLV; profit 6.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from builders import (
    ZERO,
    account,
    balance,
    cash_like,
    dated,
    fee_postings,
    fixture,
    fixture_fee_schedule,
    fixture_leg,
    index_option,
    moment,
    posting_map,
    price,
    quote,
    settle_day,
    settle_through,
    total,
    usd,
)

from options_backtest.engine.fees import AssumedFlatFeeSchedule, trade_fees
from options_backtest.engine.funding import (
    campaign_encumbrances,
    expiry_bounds,
    funding_headroom,
)
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.engine.valuation import MarkBasis, net_pnl, reconcile, value_account
from options_backtest.errors import MissingMarkError
from options_backtest.models.ledger import (
    AccountKind,
    FeeLine,
    LedgerEntry,
    LedgerState,
    LegFill,
)
from options_backtest.models.market import ContractTerms, OptionType, Quote
from options_backtest.money import Usd

CAMPAIGN = "F01"
ENTRY_DAY = 0
EXIT_DAY = 1


@dataclass(frozen=True)
class F01Run:
    """Every ledger state and entry of the F01 round trip."""

    initial_cash: Usd
    expected: Mapping[str, str]
    schedule: AssumedFlatFeeSchedule
    short: ContractTerms
    long: ContractTerms
    entry_quotes: Mapping[str, Quote]
    entry: LedgerEntry
    entry_fees: tuple[FeeLine, ...]
    opened: LedgerState
    opened_settled: LedgerState
    exit: LedgerEntry
    exit_fees: tuple[FeeLine, ...]
    closed_settled: LedgerState


def _leg_terms(leg: Mapping[str, Any]) -> ContractTerms:
    return index_option(
        OptionType(leg["option_type"]), leg["strike"], multiplier=leg["premium_multiplier"]
    )


def _natural_fill(terms: ContractTerms, leg: Mapping[str, Any], phase: str) -> LegFill:
    """Open the fixture position at entry and close it at exit: buy at the ask, sell at the bid."""
    contracts = leg["position_quantity"] if phase == "entry" else -leg["position_quantity"]
    side = "ask" if contracts > 0 else "bid"
    return LegFill(terms, contracts, price(leg[f"{phase}_{side}"]))


def _trade(
    state: LedgerState, schedule: AssumedFlatFeeSchedule, legs: tuple[LegFill, ...], day: int
) -> tuple[LedgerEntry, tuple[FeeLine, ...]]:
    fees = trade_fees(schedule, legs)
    entry = book_option_trade(
        state,
        event_id=f"f01-trade-day-{day}",
        at_ns=moment(day, 1),
        campaign_id=CAMPAIGN,
        legs=legs,
        fees=fees,
        settles_on=settle_day(day + 1),
    )
    return entry, fees


@pytest.fixture(scope="module")
def f01() -> F01Run:
    data = fixture("F01")
    schedule = fixture_fee_schedule()
    short_leg, long_leg = fixture_leg(data, "short_put"), fixture_leg(data, "long_put")
    short, long = _leg_terms(short_leg), _leg_terms(long_leg)
    initial_cash = usd(data["initial_cash_usd"])
    deposit = book_deposit(event_id="f01-deposit", at_ns=moment(ENTRY_DAY), cash=initial_cash)
    funded = apply_entry(LedgerState.empty(), deposit)

    entry_legs = (_natural_fill(short, short_leg, "entry"), _natural_fill(long, long_leg, "entry"))
    entry, entry_fees = _trade(funded, schedule, entry_legs, ENTRY_DAY)
    opened = apply_entry(funded, entry)
    opened_settled = settle_through(
        opened, event_id="f01-settle-entry", at_ns=moment(EXIT_DAY), through=settle_day(EXIT_DAY)
    )

    exit_legs = (_natural_fill(short, short_leg, "exit"), _natural_fill(long, long_leg, "exit"))
    exit_entry, exit_fees = _trade(opened_settled, schedule, exit_legs, EXIT_DAY)
    closed_settled = settle_through(
        apply_entry(opened_settled, exit_entry),
        event_id="f01-settle-exit",
        at_ns=moment(EXIT_DAY + 1),
        through=settle_day(EXIT_DAY + 1),
    )
    entry_quotes = {
        short.contract_id: quote(short_leg["entry_bid"], short_leg["entry_ask"]),
        long.contract_id: quote(long_leg["entry_bid"], long_leg["entry_ask"]),
    }
    return F01Run(
        initial_cash,
        data["expected"],
        schedule,
        short,
        long,
        entry_quotes,
        entry,
        entry_fees,
        opened,
        opened_settled,
        exit_entry,
        exit_fees,
        closed_settled,
    )


def test_entry_posts_the_adr_worked_f01_entry(f01: F01Run) -> None:
    expected = f01.expected

    assert total(line.amount for line in f01.entry_fees) == usd(expected["entry_fees_usd"])
    assert f01.entry.fee_lines == f01.entry_fees
    assert posting_map(f01.entry) == {
        dated(AccountKind.RECEIVABLE, ENTRY_DAY + 1): usd(
            expected["entry_net_credit_before_fees_usd"]
        ),
        account(AccountKind.OPTION_COST, f01.short.contract_id): usd("-200.00"),
        account(AccountKind.OPTION_COST, f01.long.contract_id): usd("110.00"),
        dated(AccountKind.PAYABLE, ENTRY_DAY + 1): -usd(expected["entry_fees_usd"]),
    } | fee_postings(f01.entry_fees)


def test_entry_opens_one_lot_per_leg_at_multiplier_times_price(f01: F01Run) -> None:
    def lots(terms: ContractTerms) -> list[tuple[int, object, str | None, int]]:
        return [
            (lot.quantity, lot.unit_cost, lot.campaign_id, lot.opened_at_ns)
            for lot in f01.opened.lots[terms.contract_id]
        ]

    assert lots(f01.short) == [(-1, usd("200.00"), CAMPAIGN, f01.entry.at_ns)]
    assert lots(f01.long) == [(1, usd("110.00"), CAMPAIGN, f01.entry.at_ns)]
    assert f01.opened.contracts[f01.short.contract_id] == f01.short
    assert f01.opened.contracts[f01.long.contract_id] == f01.long


def test_cash_after_entry_is_observed_after_its_due_transfer(f01: F01Run) -> None:
    cash = usd(f01.expected["cash_after_entry_usd"])

    assert balance(f01.opened_settled, account(AccountKind.CASH)) == cash
    assert cash_like(f01.opened_settled) == cash


def test_mid_nlv_after_entry_is_unchanged_by_premium_settlement(f01: F01Run) -> None:
    for state in (f01.opened, f01.opened_settled):
        valuation = value_account(state, f01.entry_quotes, {}, MarkBasis.MID)

        assert valuation.nlv == usd(f01.expected["mid_nlv_after_entry_usd"])
        assert valuation.nlv - cash_like(state) == usd(
            f01.expected["signed_option_mid_value_after_entry_usd"]
        )
        assert reconcile(valuation, state) == ZERO


def test_natural_valuation_marks_longs_at_bid_and_shorts_at_ask(f01: F01Run) -> None:
    # Not a fixture field; from its quotes: -1 x 100 x 2.20 + 1 x 100 x 1.00 = -120.
    state = f01.opened_settled
    valuation = value_account(state, f01.entry_quotes, {}, MarkBasis.NATURAL)

    assert valuation.nlv - cash_like(state) == usd("-120.00")
    assert valuation.nlv == usd("9968.00")


def test_missing_mark_raises_instead_of_valuing_at_zero(f01: F01Run) -> None:
    quotes = {f01.short.contract_id: f01.entry_quotes[f01.short.contract_id]}

    with pytest.raises(MissingMarkError) as caught:
        value_account(f01.opened_settled, quotes, {}, MarkBasis.MID)
    assert caught.value.instrument_ids == (f01.long.contract_id,)


def test_reserve_is_the_width_plus_an_exit_fee_provision(f01: F01Run) -> None:
    encumbrances = campaign_encumbrances(f01.opened_settled, f01.schedule)

    assert set(encumbrances) == {CAMPAIGN}
    assert encumbrances[CAMPAIGN].settlement == usd(f01.expected["terminal_cash_reserve_usd"])
    # trade_per_contract x Σ|q| over the campaign's two one-contract lots.
    assert encumbrances[CAMPAIGN].fee_provision == f01.schedule.trade_per_contract.scaled_by(2)


def test_max_expiry_loss_before_fees(f01: F01Run) -> None:
    credit = usd(f01.expected["entry_net_credit_before_fees_usd"])

    bounds = expiry_bounds(((f01.short, -1), (f01.long, 1)), 0, credit)

    assert bounds.min_value == -usd(f01.expected["max_expiry_loss_before_fees_usd"])
    assert bounds.max_value == credit
    assert bounds.upper_slope == 0
    assert bounds.breakpoints == (price("0"), price("95.00"), price("100.00"))


def test_premium_receivable_is_absent_from_headroom_until_it_settles(f01: F01Run) -> None:
    expected = f01.expected
    provision = f01.schedule.trade_per_contract.scaled_by(2)
    reserve = usd(expected["terminal_cash_reserve_usd"]) + provision

    before = funding_headroom(f01.opened, f01.schedule)
    after = funding_headroom(f01.opened_settled, f01.schedule)

    # CASH 10,000 - fee payable 2 - (500 + 2); the 90 receivable does not count.
    assert before == f01.initial_cash - usd(expected["entry_fees_usd"]) - reserve
    assert after - before == usd(expected["entry_net_credit_before_fees_usd"])


def test_exit_posts_the_adr_worked_f01_exit(f01: F01Run) -> None:
    expected = f01.expected
    debit_and_fees = usd(expected["exit_net_debit_before_fees_usd"]) + usd(
        expected["exit_fees_usd"]
    )

    assert total(line.amount for line in f01.exit_fees) == usd(expected["exit_fees_usd"])
    assert posting_map(f01.exit) == {
        dated(AccountKind.PAYABLE, EXIT_DAY + 1): -debit_and_fees,
        account(AccountKind.OPTION_COST, f01.short.contract_id): usd("200.00"),
        account(AccountKind.REALIZED_PNL, f01.short.contract_id): usd("-80.00"),
        account(AccountKind.OPTION_COST, f01.long.contract_id): usd("-110.00"),
        account(AccountKind.REALIZED_PNL, f01.long.contract_id): usd("70.00"),
    } | fee_postings(f01.exit_fees)


def test_round_trip_ends_flat_at_the_fixture_cash_nlv_and_profit(f01: F01Run) -> None:
    state = f01.closed_settled
    valuation = value_account(state, {}, {}, MarkBasis.MID)

    assert balance(state, account(AccountKind.CASH)) == usd(f01.expected["final_cash_usd"])
    assert cash_like(state) == usd(f01.expected["final_cash_usd"])
    assert valuation.nlv == usd(f01.expected["final_nlv_usd"])
    assert net_pnl(valuation, state) == usd(f01.expected["net_profit_usd"])
    assert reconcile(valuation, state) == ZERO
    for terms in (f01.short, f01.long):
        assert state.lots.get(terms.contract_id, ()) == ()
        assert balance(state, account(AccountKind.OPTION_COST, terms.contract_id)) == ZERO
