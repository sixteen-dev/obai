"""Builders shared by the ledger unit tests; not a test module.

Timestamps and settlement dates are synthetic: every booked entry is one nanosecond after the
previous one and settles on ``SETTLES_ON``.
"""

from datetime import date
from decimal import Decimal

from options_backtest.engine.journal import apply_entry
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.models.ledger import AccountKey, AccountKind, LedgerState, LegFill, Lot
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    ExerciseStyle,
    OptionType,
    SettlementType,
)
from options_backtest.money import Price, Usd

SETTLES_ON = date(2026, 9, 22)
EXPIRES_AT_NS = 2_000_000_000_000_000_000
CASH = AccountKey(AccountKind.CASH, "")
CAPITAL = AccountKey(AccountKind.CAPITAL, "")
RECEIVABLE = AccountKey(AccountKind.RECEIVABLE, SETTLES_ON.isoformat())
PAYABLE = AccountKey(AccountKind.PAYABLE, SETTLES_ON.isoformat())


def usd(text: str) -> Usd:
    return Usd(Decimal(text))


def price(text: str) -> Price:
    return Price(Decimal(text))


def option_cost(contract_id: str) -> AccountKey:
    return AccountKey(AccountKind.OPTION_COST, contract_id)


def realized(instrument_id: str) -> AccountKey:
    return AccountKey(AccountKind.REALIZED_PNL, instrument_id)


def stock_cost(asset_id: str) -> AccountKey:
    return AccountKey(AccountKind.STOCK_COST, asset_id)


def option(
    option_type: OptionType,
    strike: str,
    *,
    physical: bool = False,
    deliverable: Deliverable | None = None,
    aggregate: str | None = None,
) -> ContractTerms:
    """Return a 100-unit option on SPX (cash) or XYZ (physical) with AEA 100 x K by default."""
    asset = "XYZ" if physical else "SPX"
    component = DeliverableComponent(asset, Decimal(100))
    code = "C" if option_type is OptionType.CALL else "P"
    return ContractTerms(
        contract_id=f"{asset}-{code}{strike}",
        option_type=option_type,
        strike=price(strike),
        exercise_style=ExerciseStyle.AMERICAN if physical else ExerciseStyle.EUROPEAN,
        settlement_type=SettlementType.PHYSICAL if physical else SettlementType.CASH,
        premium_multiplier=Decimal(100),
        deliverable=deliverable or Deliverable(f"{asset}-x100", (component,), usd("0")),
        aggregate_exercise_amount=usd(aggregate) if aggregate else usd(strike).scaled_by(100),
        expires_at_ns=EXPIRES_AT_NS,
    )


def stock_lot(quantity: int, unit_cost: str, lot_id: str = "deposited") -> Lot:
    return Lot(lot_id, "XYZ", quantity, usd(unit_cost), None, 0)


def funded(cash: str = "100000.00", stock: tuple[Lot, ...] = ()) -> LedgerState:
    """Return the state after one deposit at time 0."""
    entry = book_deposit(event_id="deposit", at_ns=0, cash=usd(cash), stock=stock)
    return apply_entry(LedgerState.empty(), entry)


def trade(
    state: LedgerState,
    *legs: tuple[ContractTerms, int, str],
    campaign: str = "c1",
) -> LedgerState:
    """Book and apply one fee-free fill of ``(terms, signed contracts, price)`` legs."""
    fills = tuple(LegFill(terms, contracts, price(text)) for terms, contracts, text in legs)
    entry = book_option_trade(
        state,
        event_id=f"trade-{state.entry_count + 1}",
        at_ns=state.last_at_ns + 1,
        campaign_id=campaign,
        legs=fills,
        fees=(),
        settles_on=SETTLES_ON,
    )
    return apply_entry(state, entry)
