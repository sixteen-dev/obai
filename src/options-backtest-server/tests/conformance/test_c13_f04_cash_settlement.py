"""C13: fixture F04, a European cash-settled put vertical held to an official reference of 97.

Design §18.3 (F04), §12.1 (PM cash expiry posts q x m x intrinsic as a signed receivable or
payable and extinguishes the option once); ADR 0001 §5 and its §11 amendment: a package settles
in one entry that nets Σ quantity x intrinsic into one RECEIVABLE or PAYABLE, relieves each
contract's cost with REALIZED_PNL, carries one EXPIRATION event per contract and retires them
all; a leg's settlement cash flow is -(ΔOPTION_COST + ΔREALIZED_PNL) of its contract.

Hand check. Same opening as F01 at single prices: sell the 100 put at 2.00 (+200), buy the 95
put at 1.10 (-110), fees 2: cash 10,088. At 97 the SPX deliverable (100 units) is worth 9,700;
the short 100 put (AEA 10,000) pays 300, the long 95 put (AEA 9,500) pays nothing, so the one
entry posts PAYABLE -300. Cash 10,088 - 300 = 9,788 = NLV; profit -212 = realized
(200 - 300) + (0 - 110) - fees 2.
"""

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from builders import (
    DAY_NS,
    EXPIRES_AT_NS,
    EXPIRY_DAY,
    INDEX_ASSET,
    ZERO,
    account,
    balance,
    cash_like,
    dated,
    fee_postings,
    fixture,
    fixture_fee_schedule,
    fixture_leg,
    held_quantity,
    index_option,
    kind_total,
    moment,
    package_contracts,
    posting_map,
    price,
    settle_day,
    settle_through,
    stock_option,
    total,
    usd,
)

from options_backtest.engine.fees import AssumedFlatFeeSchedule, lifecycle_fees, trade_fees
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.settlement import book_cash_settlement
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.engine.valuation import MarkBasis, net_pnl, reconcile, value_account
from options_backtest.errors import LedgerInvariantError
from options_backtest.models.ledger import (
    AccountKind,
    FeeEvent,
    FeeLine,
    LedgerEntry,
    LedgerState,
    LegFill,
    QuantityKind,
)
from options_backtest.models.market import ContractTerms, OptionType
from options_backtest.money import Price, Usd

CAMPAIGN = "F04"
ENTRY_DAY = 0
# Package-shape cases, independent of the fixture: all one-contract positions opened at 1.00.
NEAR_SHORT = index_option(OptionType.PUT, "100")
NEAR_LONG = index_option(OptionType.PUT, "95")
FAR_LONG = index_option(OptionType.PUT, "95", expires_at_ns=EXPIRES_AT_NS + DAY_NS)
PHYSICAL_LONG = stock_option(OptionType.PUT, "95.00", shares="100", aggregate="9500.00")


@dataclass(frozen=True)
class F04Run:
    """Every ledger state and entry of the F04 vertical through cash settlement."""

    data: Mapping[str, Any]
    schedule: AssumedFlatFeeSchedule
    short: ContractTerms
    long: ContractTerms
    opened_settled: LedgerState
    settlement_fees: tuple[FeeLine, ...]
    settlement: LedgerEntry
    final: LedgerState

    @property
    def expected(self) -> Mapping[str, Any]:
        """Return the fixture's expected values."""
        return dict(self.data["expected"])

    @property
    def package(self) -> tuple[str, str]:
        """Return the held package's contract ids."""
        return (self.short.contract_id, self.long.contract_id)

    def official(self) -> Price:
        """Return the fixture's official settlement value."""
        return price(self.data["official_settlement_value"])


def _leg_terms(leg: Mapping[str, Any]) -> ContractTerms:
    return index_option(
        OptionType(leg["option_type"]), leg["strike"], multiplier=leg["premium_multiplier"]
    )


def _open(
    data: Mapping[str, Any], schedule: AssumedFlatFeeSchedule, legs: tuple[LegFill, ...]
) -> LedgerState:
    deposit = book_deposit(
        event_id="f04-deposit", at_ns=moment(ENTRY_DAY), cash=usd(data["initial_cash_usd"])
    )
    funded = apply_entry(LedgerState.empty(), deposit)
    entry = book_option_trade(
        funded,
        event_id="f04-entry",
        at_ns=moment(ENTRY_DAY, 1),
        campaign_id=CAMPAIGN,
        legs=legs,
        fees=trade_fees(schedule, legs),
        settles_on=settle_day(ENTRY_DAY + 1),
    )
    return settle_through(
        apply_entry(funded, entry),
        event_id="f04-settle-entry",
        at_ns=moment(ENTRY_DAY + 1),
        through=settle_day(ENTRY_DAY + 1),
    )


def _settle(
    state: LedgerState,
    contract_ids: tuple[str, ...],
    level: Price,
    fees: tuple[FeeLine, ...],
    day: int = EXPIRY_DAY,
) -> LedgerEntry:
    return book_cash_settlement(
        state,
        event_id=f"f04-cash-settle-day-{day}",
        at_ns=moment(day, state.entry_count),
        contract_ids=contract_ids,
        settlement={INDEX_ASSET: level},
        fees=fees,
        settles_on=settle_day(day + 1),
    )


def _leg_flow(entry: LedgerEntry, terms: ContractTerms) -> Usd:
    """Return a leg's settlement cash flow, -(ΔOPTION_COST + ΔREALIZED_PNL) of its contract."""
    postings = posting_map(entry)
    cost = postings.get(account(AccountKind.OPTION_COST, terms.contract_id), ZERO)
    realized = postings.get(account(AccountKind.REALIZED_PNL, terms.contract_id), ZERO)
    return -(cost + realized)


@pytest.fixture(scope="module")
def f04() -> F04Run:
    data = fixture("F04")
    schedule = fixture_fee_schedule()
    short_leg, long_leg = fixture_leg(data, "short_put"), fixture_leg(data, "long_put")
    short, long = _leg_terms(short_leg), _leg_terms(long_leg)
    opened_settled = _open(
        data,
        schedule,
        (
            LegFill(short, short_leg["position_quantity"], price(short_leg["entry_price"])),
            LegFill(long, long_leg["position_quantity"], price(long_leg["entry_price"])),
        ),
    )
    package = (short.contract_id, long.contract_id)
    fees = lifecycle_fees(
        schedule, FeeEvent.CASH_SETTLEMENT, package_contracts(opened_settled, package)
    )
    settlement = _settle(opened_settled, package, price(data["official_settlement_value"]), fees)
    final = settle_through(
        apply_entry(opened_settled, settlement),
        event_id="f04-settle-expiry",
        at_ns=moment(EXPIRY_DAY + 1),
        through=settle_day(EXPIRY_DAY + 1),
    )
    return F04Run(data, schedule, short, long, opened_settled, fees, settlement, final)


def test_cash_after_entry(f04: F04Run) -> None:
    cash = usd(f04.expected["cash_after_entry_usd"])

    assert balance(f04.opened_settled, account(AccountKind.CASH)) == cash
    assert cash_like(f04.opened_settled) == cash


def test_settlement_fees_are_zero(f04: F04Run) -> None:
    assert total(line.amount for line in f04.settlement_fees) == usd(
        f04.expected["settlement_fees_usd"]
    )
    assert f04.settlement.fee_lines == f04.settlement_fees


def test_the_package_settles_in_one_entry_netted_to_one_payable(f04: F04Run) -> None:
    net = usd(f04.expected["short_put_settlement_cash_flow_usd"]) + usd(
        f04.expected["long_put_settlement_cash_flow_usd"]
    )

    assert posting_map(f04.settlement) == {
        dated(AccountKind.PAYABLE, EXPIRY_DAY + 1): net,
        account(AccountKind.OPTION_COST, f04.short.contract_id): usd("200.00"),
        account(AccountKind.REALIZED_PNL, f04.short.contract_id): usd("100.00"),
        account(AccountKind.OPTION_COST, f04.long.contract_id): usd("-110.00"),
        account(AccountKind.REALIZED_PNL, f04.long.contract_id): usd("110.00"),
    }


def test_each_legs_settlement_cash_flow_is_the_fixture_value(f04: F04Run) -> None:
    assert _leg_flow(f04.settlement, f04.short) == usd(
        f04.expected["short_put_settlement_cash_flow_usd"]
    )
    assert _leg_flow(f04.settlement, f04.long) == usd(
        f04.expected["long_put_settlement_cash_flow_usd"]
    )


def test_one_expiration_event_per_contract_extinguishes_each_position(f04: F04Run) -> None:
    events = f04.settlement.quantity_events

    assert len(events) == 2
    assert {(e.instrument_id, e.kind, e.delta, e.opened) for e in events} == {
        (f04.short.contract_id, QuantityKind.EXPIRATION, 1, None),
        (f04.long.contract_id, QuantityKind.EXPIRATION, -1, None),
    }
    assert all(sum(abs(r.quantity) for r in e.reliefs) == abs(e.delta) for e in events)


def test_settlement_extinguishes_both_contracts_once(f04: F04Run) -> None:
    for terms in (f04.short, f04.long):
        assert terms.contract_id in f04.final.retired
        assert f04.final.lots.get(terms.contract_id, ()) == ()
        assert balance(f04.final, account(AccountKind.OPTION_COST, terms.contract_id)) == ZERO


def test_final_state_matches_the_fixture(f04: F04Run) -> None:
    expected = f04.expected
    state = f04.final
    valuation = value_account(state, {}, {}, MarkBasis.MID)

    assert balance(state, account(AccountKind.CASH)) == usd(expected["final_cash_usd"])
    assert cash_like(state) == usd(expected["final_cash_usd"])
    assert valuation.nlv == usd(expected["final_nlv_usd"])
    assert net_pnl(valuation, state) == usd(expected["net_profit_usd"])
    assert reconcile(valuation, state) == ZERO
    assert held_quantity(state, INDEX_ASSET) == expected["final_stock_shares"]
    for terms in (f04.short, f04.long):
        assert held_quantity(state, terms.contract_id) == expected["final_option_quantity"]


def test_profit_is_realized_pnl_less_fees_with_no_separate_expiry_pnl(f04: F04Run) -> None:
    realized = -kind_total(f04.final, AccountKind.REALIZED_PNL)
    fees = kind_total(f04.final, AccountKind.FEES)

    assert realized - fees == usd(f04.expected["net_profit_usd"])
    assert {key.ref for key in f04.final.balances if key.kind is AccountKind.REALIZED_PNL} <= {
        f04.short.contract_id,
        f04.long.contract_id,
    }


def test_both_legs_in_the_money_net_to_one_payable(f04: F04Run) -> None:
    # Not a fixture field. At 90 the short 100 put pays 1,000 and the long 95 put receives 500:
    # one PAYABLE -500, never a separate RECEIVABLE +500. Final cash 10,088 - 500 = 9,588; loss
    # 412 = the F01 maximum expiry loss 410 plus fees 2.
    entry = _settle(f04.opened_settled, f04.package, price("90.00"), f04.settlement_fees)
    final = settle_through(
        apply_entry(f04.opened_settled, entry),
        event_id="f04-settle-expiry-at-90",
        at_ns=moment(EXPIRY_DAY + 1),
        through=settle_day(EXPIRY_DAY + 1),
    )
    valuation = value_account(final, {}, {}, MarkBasis.MID)

    assert posting_map(entry) == {
        dated(AccountKind.PAYABLE, EXPIRY_DAY + 1): usd("-500.00"),
        account(AccountKind.OPTION_COST, f04.short.contract_id): usd("200.00"),
        account(AccountKind.REALIZED_PNL, f04.short.contract_id): usd("800.00"),
        account(AccountKind.OPTION_COST, f04.long.contract_id): usd("-110.00"),
        account(AccountKind.REALIZED_PNL, f04.long.contract_id): usd("-390.00"),
    }
    assert _leg_flow(entry, f04.short) == usd("-1000.00")
    assert _leg_flow(entry, f04.long) == usd("500.00")
    assert balance(final, account(AccountKind.CASH)) == usd("9588.00")
    assert valuation.nlv == usd("9588.00")
    assert net_pnl(valuation, final) == usd("-412.00")
    assert reconcile(valuation, final) == ZERO


def test_settlement_fees_post_to_fees_and_the_settlement_payable(f04: F04Run) -> None:
    # Not a fixture field: $0.25 per contract over the package's Σ|q| = 2 is $0.50.
    schedule = AssumedFlatFeeSchedule(
        schedule_id="c13-settlement-fee",
        trade_per_contract=f04.schedule.trade_per_contract,
        exercise_assignment_per_contract=ZERO,
        cash_settlement_per_contract=usd("0.25"),
    )
    fees = lifecycle_fees(schedule, FeeEvent.CASH_SETTLEMENT, 2)
    entry = _settle(f04.opened_settled, f04.package, f04.official(), fees)

    assert total(line.amount for line in fees) == usd("0.50")
    assert entry.fee_lines == fees
    assert posting_map(entry) == {
        dated(AccountKind.PAYABLE, EXPIRY_DAY + 1): usd("-300.50"),
        account(AccountKind.OPTION_COST, f04.short.contract_id): usd("200.00"),
        account(AccountKind.REALIZED_PNL, f04.short.contract_id): usd("100.00"),
        account(AccountKind.OPTION_COST, f04.long.contract_id): usd("-110.00"),
        account(AccountKind.REALIZED_PNL, f04.long.contract_id): usd("110.00"),
    } | fee_postings(fees)


def test_a_second_settlement_of_the_retired_package_raises(f04: F04Run) -> None:
    with pytest.raises(LedgerInvariantError):
        later = EXPIRY_DAY + 1
        entry = _settle(f04.final, f04.package, f04.official(), f04.settlement_fees, later)
        apply_entry(f04.final, entry)


def test_replaying_a_settlement_entry_is_rejected(f04: F04Run) -> None:
    replayed = dataclasses.replace(
        f04.settlement,
        event_id="f04-cash-settle-again",
        sequence=f04.final.entry_count + 1,
        at_ns=f04.final.last_at_ns,
    )

    with pytest.raises(LedgerInvariantError):
        apply_entry(f04.final, replayed)


@pytest.mark.parametrize(
    "roles",
    [
        pytest.param((), id="empty"),
        pytest.param(("short",), id="short-leg-only"),
        pytest.param(("long",), id="long-leg-only"),
        pytest.param(("short", "long", "short"), id="duplicate"),
        pytest.param(("short", "long", "unregistered"), id="unregistered"),
    ],
)
def test_a_tuple_other_than_exactly_the_held_package_raises(
    f04: F04Run, roles: tuple[str, ...]
) -> None:
    # ADR §11 amendment: book_cash_settlement itself rejects the tuple; nothing reaches the ledger.
    ids = {
        "short": f04.short.contract_id,
        "long": f04.long.contract_id,
        "unregistered": index_option(OptionType.PUT, "90").contract_id,
    }

    contract_ids = tuple(ids[role] for role in roles)

    with pytest.raises(LedgerInvariantError):
        _settle(f04.opened_settled, contract_ids, f04.official(), f04.settlement_fees)


def _hold(holdings: tuple[tuple[str, ContractTerms, int], ...]) -> LedgerState:
    """Open each (campaign, contract, signed contracts) in its own fee-free fill at 1.00."""
    deposit = book_deposit(event_id="c13-deposit", at_ns=moment(ENTRY_DAY), cash=usd("100000.00"))
    state = apply_entry(LedgerState.empty(), deposit)
    for step, (campaign_id, terms, contracts) in enumerate(holdings, start=1):
        entry = book_option_trade(
            state,
            event_id=f"c13-open-{step}",
            at_ns=moment(ENTRY_DAY, step),
            campaign_id=campaign_id,
            legs=(LegFill(terms, contracts, price("1.00")),),
            fees=(),
            settles_on=settle_day(ENTRY_DAY + 1),
        )
        state = apply_entry(state, entry)
    return state


@pytest.mark.parametrize(
    ("other_campaign", "other"),
    [
        pytest.param(CAMPAIGN, FAR_LONG, id="mixed-expiry"),
        pytest.param("another-campaign", NEAR_LONG, id="cross-campaign"),
        pytest.param(CAMPAIGN, PHYSICAL_LONG, id="physical-leg"),
    ],
)
def test_a_package_must_share_one_campaign_one_expiry_and_cash_settlement(
    other_campaign: str, other: ContractTerms
) -> None:
    state = _hold(((CAMPAIGN, NEAR_SHORT, -1), (other_campaign, other, 1)))
    level = price("97.00")

    # The short put alone is exactly its campaign's held cash package at its expiry.
    alone = apply_entry(state, _settle(state, (NEAR_SHORT.contract_id,), level, ()))
    assert NEAR_SHORT.contract_id in alone.retired
    assert other.contract_id not in alone.retired
    with pytest.raises(LedgerInvariantError):
        _settle(state, (NEAR_SHORT.contract_id, other.contract_id), level, ())
