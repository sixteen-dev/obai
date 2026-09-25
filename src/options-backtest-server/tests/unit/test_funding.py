"""Expiry bounds and encumbrance branches beyond C14 and the fixtures (ADR 0001 §5, §11.2)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from ledger_cases import CASH, EXPIRES_AT_NS, SETTLES_ON, funded, option, price, trade, usd

from options_backtest.engine.exercise import book_physical_exercise
from options_backtest.engine.fees import AssumedFlatFeeSchedule, lifecycle_fees, trade_fees
from options_backtest.engine.funding import (
    Encumbrance,
    campaign_encumbrances,
    expiry_bounds,
    funding_headroom,
)
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.settlement import book_cash_settlement, book_settle_due
from options_backtest.engine.trades import book_option_trade
from options_backtest.errors import ErrorCode, LedgerInvariantError, UnsupportedLifecycle
from options_backtest.models.ledger import FeeEvent, LedgerState, LegFill, Lot
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    OptionType,
    SettlementType,
)

SCHEDULE = AssumedFlatFeeSchedule("flat", usd("1.00"), usd("0"), usd("0"))
PUT_100 = option(OptionType.PUT, "100")
PUT_95 = option(OptionType.PUT, "95")
CALL_100 = option(OptionType.CALL, "100")
# Deliverable 100 SPX plus 50 cash against AEA 0: exercise value 100·S + 50 for every S >= 0.
RICH = option(
    OptionType.CALL,
    "0",
    deliverable=Deliverable("SPX-cash", (DeliverableComponent("SPX", Decimal(100)),), usd("50")),
    aggregate="0",
)
# Lifecycle fees above the trade fee: a position may end by its settlement or assignment.
SETTLEMENT_ABOVE_TRADE = AssumedFlatFeeSchedule("settle", usd("0.50"), usd("0"), usd("1.50"))
ASSIGNMENT_ABOVE_TRADE = AssumedFlatFeeSchedule("assign", usd("1.00"), usd("5.00"), usd("0"))
EXPIRY_SETTLES_ON = date(2026, 9, 23)


def _r1_put(strike: str) -> ContractTerms:
    """Return an ``R1Campaign`` pass-refusal leg: multiplier 1 on one index unit."""
    one_unit = Deliverable("SPX-x1", (DeliverableComponent("SPX", Decimal(1)),), usd("0"))
    terms = option(OptionType.PUT, strike, deliverable=one_unit, aggregate=strike)
    return replace(terms, contract_id=f"SPX1-P{strike}", premium_multiplier=Decimal(1))


R1_SHORT = _r1_put("102")
R1_LONG = _r1_put("100")
R1_LEGS = (LegFill(R1_SHORT, -1, price("1.50")), LegFill(R1_LONG, 1, price("0.50")))
CSP_PUT = option(OptionType.PUT, "100", physical=True)
CSP_LEGS = (LegFill(CSP_PUT, -1, price("2.00")),)


def test_a_long_call_loses_at_most_its_premium_and_has_no_maximum() -> None:
    bounds = expiry_bounds(((CALL_100, 1),), 0, usd("-300.00"))

    assert bounds.breakpoints == (price("0"), price("100"))
    assert bounds.upper_slope == Decimal(100)
    assert bounds.min_value == usd("-300.00")
    assert bounds.max_value is None


def test_a_kink_below_zero_is_outside_the_payoff_domain() -> None:
    bounds = expiry_bounds(((RICH, 1),), 0, usd("0"))

    assert bounds.breakpoints == (price("0"),)
    assert bounds.min_value == usd("50")
    assert bounds.max_value is None


@pytest.mark.parametrize(
    ("holdings", "error"),
    [
        pytest.param((), ValueError, id="empty"),
        pytest.param(((PUT_100, 0),), ValueError, id="zero-quantity"),
        pytest.param(((PUT_100, True),), TypeError, id="bool-quantity"),
    ],
)
def test_expiry_bounds_guards(
    holdings: tuple[tuple[ContractTerms, int], ...], error: type[Exception]
) -> None:
    with pytest.raises(error):
        expiry_bounds(holdings, 0, usd("0"))


def _two_asset_put() -> ContractTerms:
    parts = (DeliverableComponent("SPX", Decimal(100)), DeliverableComponent("NDX", Decimal(1)))
    return option(OptionType.PUT, "100", deliverable=Deliverable("mix", parts, usd("0")))


@pytest.mark.parametrize(
    ("holdings", "stock_shares"),
    [
        pytest.param(
            ((PUT_100, -1), (option(OptionType.PUT, "95", physical=True), 1)), 0, id="two"
        ),
        pytest.param(((_two_asset_put(), 1),), 0, id="multi-component"),
        pytest.param(((PUT_100, -1),), -100, id="short-stock"),
    ],
)
def test_shapes_outside_one_deliverable_and_long_stock_are_unsupported(
    holdings: tuple[tuple[ContractTerms, int], ...], stock_shares: int
) -> None:
    with pytest.raises(UnsupportedLifecycle) as caught:
        expiry_bounds(holdings, stock_shares, usd("0"))
    assert caught.value.code is ErrorCode.UNSUPPORTED_ACCOUNT_STATE


def test_campaigns_are_reserved_separately_without_netting() -> None:
    # The long 95 put in campaign "hedge" would cap the short put's loss, but campaigns never net.
    short = trade(funded(), (PUT_100, -1, "2.00"), campaign="short")
    state = trade(short, (PUT_95, 1, "1.00"), campaign="hedge")

    assert campaign_encumbrances(state, SCHEDULE) == {
        "hedge": Encumbrance(usd("0"), usd("1.00")),
        "short": Encumbrance(usd("10000.00"), usd("1.00")),
    }


def test_a_debit_spread_needs_no_settlement_reserve() -> None:
    state = trade(funded(), (PUT_100, 1, "2.10"), (PUT_95, -1, "1.00"))

    assert campaign_encumbrances(state, SCHEDULE) == {"c1": Encumbrance(usd("0"), usd("2.00"))}
    # CASH 100,000 - debit payable 110 - exit-fee provision 2.
    assert funding_headroom(state, SCHEDULE) == usd("99888.00")


def test_a_campaign_with_a_positive_expiry_floor_reserves_nothing() -> None:
    state = trade(funded(), (RICH, 1, "1.00"))

    assert campaign_encumbrances(state, SCHEDULE) == {"c1": Encumbrance(usd("0"), usd("1.00"))}
    # CASH 100,000 - debit payable 100 - exit-fee provision 1; the +50 floor never adds headroom.
    assert funding_headroom(state, SCHEDULE) == usd("99899.00")


def test_an_option_lot_without_a_campaign_is_an_invalid_state() -> None:
    lot = Lot("l", PUT_100.contract_id, -1, usd("200"), None, 0)
    state = LedgerState(
        1, 0, {}, {PUT_100.contract_id: (lot,)}, {PUT_100.contract_id: PUT_100}, frozenset()
    )

    with pytest.raises(LedgerInvariantError, match="campaign"):
        campaign_encumbrances(state, SCHEDULE)


def test_stock_shares_are_an_int() -> None:
    with pytest.raises(TypeError, match="stock_shares"):
        expiry_bounds(((PUT_100, -1),), Decimal(100), usd("0"))  # type: ignore[arg-type]


def _open(cash: str, schedule: AssumedFlatFeeSchedule, legs: tuple[LegFill, ...]) -> LedgerState:
    """Return the state after depositing ``cash`` and one fill of ``legs`` paying trade fees."""
    state = funded(cash)
    entry = book_option_trade(
        state,
        event_id="open",
        at_ns=1,
        campaign_id="c1",
        legs=legs,
        fees=trade_fees(schedule, legs),
        settles_on=SETTLES_ON,
    )
    return apply_entry(state, entry)


def _settle_due(state: LedgerState, through: date) -> LedgerState:
    """Apply the transfer of every open item dated on or before ``through``."""
    entry = book_settle_due(
        state, event_id=f"due-{through}", at_ns=state.last_at_ns + 1, through=through
    )
    assert entry is not None
    return apply_entry(state, entry)


def test_a_settlement_fee_above_the_trade_fee_refuses_the_zero_slack_entry_of_r1() -> None:
    # R1 pass-refusal at Cash0 4: CASH 4 - trade-fee payable 1 - (W 2 + provision 2 x 1.50).
    state = _open("4.00", SETTLEMENT_ABOVE_TRADE, R1_LEGS)

    assert campaign_encumbrances(state, SETTLEMENT_ABOVE_TRADE) == {
        "c1": Encumbrance(usd("2.00"), usd("3.00"))
    }
    assert funding_headroom(state, SETTLEMENT_ABOVE_TRADE) == usd("-2.00")


@pytest.mark.parametrize(
    ("level", "package_value"),
    [
        pytest.param("50", "2.00", id="deep-itm"),
        pytest.param("101", "1.00", id="mid-width"),
        pytest.param("150", "0.00", id="far-otm"),
    ],
)
def test_a_package_funded_with_zero_slack_stays_funded_through_a_fee_paying_settlement(
    level: str, package_value: str
) -> None:
    opened = _open("6.00", SETTLEMENT_ABOVE_TRADE, R1_LEGS)
    kept = _settle_due(opened, SETTLES_ON)  # CASH 6 + credit 1 - trade fee 1
    fees = lifecycle_fees(SETTLEMENT_ABOVE_TRADE, FeeEvent.CASH_SETTLEMENT, 2)
    entry = book_cash_settlement(
        kept,
        event_id="settle",
        at_ns=EXPIRES_AT_NS,
        contract_ids=(R1_SHORT.contract_id, R1_LONG.contract_id),
        settlement={"SPX": price(level)},
        fees=fees,
        settles_on=EXPIRY_SETTLES_ON,
    )
    settled = apply_entry(kept, entry)
    final = _settle_due(settled, EXPIRY_SETTLES_ON)

    assert funding_headroom(opened, SETTLEMENT_ABOVE_TRADE) == usd("0.00")
    assert funding_headroom(kept, SETTLEMENT_ABOVE_TRADE) == usd("1.00")
    # CASH 6 - package value v - settlement fee 3: the provision paid the fee, so >= 1.
    left = usd("3.00") - usd(package_value)
    assert funding_headroom(settled, SETTLEMENT_ABOVE_TRADE) == left
    assert final.balances[CASH] == left
    assert funding_headroom(final, SETTLEMENT_ABOVE_TRADE) == left


def test_an_assignment_fee_above_the_trade_fee_refuses_the_zero_slack_sale_of_a_put() -> None:
    # F02-like: CASH 10,002 - trade-fee payable 1 - (full AEA 10,000 + provision 5).
    state = _open("10002.00", ASSIGNMENT_ABOVE_TRADE, CSP_LEGS)

    assert campaign_encumbrances(state, ASSIGNMENT_ABOVE_TRADE) == {
        "c1": Encumbrance(usd("10000.00"), usd("5.00"))
    }
    assert funding_headroom(state, ASSIGNMENT_ABOVE_TRADE) == usd("-4.00")


def test_a_put_funded_with_zero_slack_stays_funded_through_a_fee_paying_assignment() -> None:
    opened = _open("10006.00", ASSIGNMENT_ABOVE_TRADE, CSP_LEGS)
    fees = lifecycle_fees(ASSIGNMENT_ABOVE_TRADE, FeeEvent.EXERCISE_ASSIGNMENT, 1)
    entry = book_physical_exercise(
        opened,
        event_id="assigned",
        at_ns=2,
        contract_id=CSP_PUT.contract_id,
        contracts=1,
        fees=fees,
        settles_on=SETTLES_ON,
    )
    assigned = apply_entry(opened, entry)  # early, before the premium receivable settles
    final = _settle_due(assigned, SETTLES_ON)

    assert funding_headroom(opened, ASSIGNMENT_ABOVE_TRADE) == usd("0.00")
    # CASH 10,006 - trade fee 1 - AEA 10,000 - assignment fee 5.
    assert funding_headroom(assigned, ASSIGNMENT_ABOVE_TRADE) == usd("0.00")
    assert final.balances[CASH] == usd("200.00")
    assert funding_headroom(final, ASSIGNMENT_ABOVE_TRADE) == usd("200.00")


def test_the_exit_fee_provision_takes_each_contracts_costliest_exit() -> None:
    # One deliverable, both settlement types: cash legs end by trade (1.00 > 0.50), the
    # physical leg by assignment (5.00 > 1.00).
    schedule = AssumedFlatFeeSchedule("mixed", usd("1.00"), usd("5.00"), usd("0.50"))
    physical = replace(
        PUT_95, contract_id="SPX-P95-physical", settlement_type=SettlementType.PHYSICAL
    )
    state = trade(funded(), (PUT_100, -2, "2.00"), (physical, 1, "1.00"))

    provision = campaign_encumbrances(state, schedule)["c1"].fee_provision

    assert provision == usd("7.00")
