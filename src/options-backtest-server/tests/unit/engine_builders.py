"""Builders shared by the D1 engine unit tests (clock, orders, fills, validity, lifecycle).

A four-session week (Mon 2024-03-04 to Thu 2024-03-07) and a SPXW put credit vertical, short
4900 / long 4895, expiring Wednesday at 16:00. Ledger states are built with WP1's posting
functions directly, never with the functions under test.
"""

from datetime import date
from decimal import Decimal

from data_builders import MON, TUE, WED, contract_version, freeze, option_terms, slot_ns
from data_builders import trading_session as _trading_session

from options_backtest.data.manifest import FrozenDataset
from options_backtest.data.records import ContractVersion, QuoteObservation, SettlementObservation
from options_backtest.engine.fees import AssumedFlatFeeSchedule, trade_fees
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.orders import ExitTrigger, Order, OrderLeg, OrderPurpose
from options_backtest.engine.settlement import book_settle_due
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.models.ledger import LedgerState, LegFill
from options_backtest.models.market import ContractTerms, OptionType
from options_backtest.money import Price, Usd

THU = date(2024, 3, 7)
MON_S = _trading_session(MON)
TUE_S = _trading_session(TUE)
WED_S = _trading_session(WED)
THU_S = _trading_session(THU)
SESSIONS = (MON_S, TUE_S, WED_S, THU_S)

SHORT = option_terms(WED, OptionType.PUT, "4900")
LONG = option_terms(WED, OptionType.PUT, "4895")
SHORT_ID = SHORT.contract_id
LONG_ID = LONG.contract_id
GENERATION = "c1.g1"

SCHEDULE = AssumedFlatFeeSchedule(
    schedule_id="illustrative_flat_1usd_per_contract_side_v1",
    trade_per_contract=Usd(Decimal("1.00")),
    exercise_assignment_per_contract=Usd(Decimal(0)),
    cash_settlement_per_contract=Usd(Decimal(0)),
)
_TRIGGERS = {
    OrderPurpose.EXIT: ExitTrigger.TIME_EXIT,
    OrderPurpose.FINAL: ExitTrigger.FINAL_LIQUIDATION,
}


def usd(text: str) -> Usd:
    return Usd(Decimal(text))


def price(text: str) -> Price:
    return Price(Decimal(text))


def leg(terms: ContractTerms, ratio: int) -> OrderLeg:
    return OrderLeg(terms, f"{terms.contract_id}@v1", ratio)


OPENING_LEGS = (leg(SHORT, -1), leg(LONG, 1))
CLOSING_LEGS = (leg(SHORT, 1), leg(LONG, -1))


def order(  # noqa: PLR0913 — one keyword per field a test varies
    purpose: OrderPurpose = OrderPurpose.ENTRY,
    *,
    legs: tuple[OrderLeg, ...] | None = None,
    packages: int = 1,
    limit: str | None = "-90",
    session_date: date = MON,
    campaign_id: str = GENERATION,
) -> Order:
    """Return an order submitted at the session's DEC; closing purposes default to closing legs."""
    session = {MON: MON_S, TUE: TUE_S, WED: WED_S}[session_date]
    if legs is None:
        legs = (
            OPENING_LEGS
            if purpose in {OrderPurpose.ENTRY, OrderPurpose.ROLL_OPEN}
            else CLOSING_LEGS
        )
    return Order(
        order_id=f"o:{session_date.isoformat()}:{purpose.value}",
        campaign_id=campaign_id,
        purpose=purpose,
        legs=legs,
        packages=packages,
        limit_usd=None if limit is None else usd(limit),
        trigger=_TRIGGERS.get(purpose),
        submitted_at_ns=slot_ns(session, "DEC"),
        session_date=session_date,
    )


def market(
    *quotes: QuoteObservation,
    settlements: tuple[SettlementObservation, ...] = (),
    contracts: tuple[ContractVersion, ...] | None = None,
) -> FrozenDataset:
    """Return the four-session dataset listing the two puts, with the given observations."""
    listed = (contract_version(SHORT), contract_version(LONG)) if contracts is None else contracts
    return freeze(sessions=SESSIONS, contracts=listed, quotes=quotes, settlements=settlements)


def funded(cash: str = "10000.00", *, at_ns: int = MON_S.open_ns) -> LedgerState:
    """Return the state after one cash deposit."""
    entry = book_deposit(event_id="deposit", at_ns=at_ns, cash=usd(cash))
    return apply_entry(LedgerState.empty(), entry)


def entered(state: LedgerState, *, packages: int = 1, campaign_id: str = GENERATION) -> LedgerState:
    """Return ``state`` after selling the vertical at Monday F1 (2.00 / 1.10), fees $1 a side."""
    legs = (
        LegFill(SHORT, -packages, price("2.00")),
        LegFill(LONG, packages, price("1.10")),
    )
    entry = book_option_trade(
        state,
        event_id=f"{MON.isoformat()}:F1:4:{state.entry_count}",
        at_ns=slot_ns(MON_S, "F1"),
        campaign_id=campaign_id,
        legs=legs,
        fees=trade_fees(SCHEDULE, legs),
        settles_on=TUE,
    )
    return apply_entry(state, entry)


def held(cash: str = "10000.00", *, packages: int = 1) -> LedgerState:
    """Return a funded state holding the vertical, its T+1 dues moved to cash at Tuesday OPEN."""
    state = entered(funded(cash), packages=packages)
    due = book_settle_due(state, event_id="2024-03-05:OPEN:1:1", at_ns=TUE_S.open_ns, through=TUE)
    assert due is not None
    return apply_entry(state, due)
