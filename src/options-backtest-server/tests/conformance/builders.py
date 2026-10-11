"""Shared builders for the ledger conformance tests (ADR 0001 §3, §5, §6).

Expected numbers come from ``tests/contracts/ledger-fixtures.json``; every amount is an exact
``Decimal`` parsed from a string, never a float. Settlement dates and timestamps are synthetic:
calendars are out of WP1 scope, so day ``n`` is simply ``BASE_DATE + n`` and trades settle T+1.
"""

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from options_backtest.engine.fees import AssumedFlatFeeSchedule
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.settlement import book_settle_due
from options_backtest.models.ledger import (
    AccountKey,
    AccountKind,
    FeeLine,
    LedgerEntry,
    LedgerState,
)
from options_backtest.models.market import (
    ContractTerms,
    Deliverable,
    DeliverableComponent,
    ExerciseStyle,
    OptionType,
    Quote,
    SettlementType,
)
from options_backtest.money import Price, Usd

LEDGER_FIXTURES = Path(__file__).resolve().parents[1] / "contracts" / "ledger-fixtures.json"
ZERO = Usd(Decimal(0))
BASE_DATE = date(2026, 9, 21)
DAY_NS = 86_400 * 1_000_000_000
T0_NS = 1_790_000_000 * 1_000_000_000
EXPIRY_DAY = 30  # the synthetic day the default contracts expire; settle on or after it
EXPIRES_AT_NS = T0_NS + EXPIRY_DAY * DAY_NS
INDEX_ASSET = "SPX"
STOCK_ASSET = "XYZ"
CASH_KINDS = frozenset({AccountKind.CASH, AccountKind.RECEIVABLE, AccountKind.PAYABLE})


# --- fixtures and literals -------------------------------------------------------------------


def fixture(fixture_id: str) -> dict[str, Any]:
    """Return one fixture from the vendored ledger fixtures, numbers parsed as ``Decimal``."""
    document = json.loads(LEDGER_FIXTURES.read_text(encoding="utf-8"), parse_float=Decimal)
    matches = [item for item in document["fixtures"] if item["id"] == fixture_id]
    assert len(matches) == 1, f"expected exactly one fixture {fixture_id}"
    return dict(matches[0])


def fixture_leg(data: Mapping[str, Any], leg_id: str) -> dict[str, Any]:
    """Return the fixture leg with ``leg_id``."""
    matches = [leg for leg in data["legs"] if leg["leg_id"] == leg_id]
    assert len(matches) == 1, f"expected exactly one leg {leg_id}"
    return dict(matches[0])


def fixture_fee_schedule() -> AssumedFlatFeeSchedule:
    """Return the fixtures' assumed schedule: $1.00 per option contract side, $0 lifecycle."""
    document = json.loads(LEDGER_FIXTURES.read_text(encoding="utf-8"), parse_float=Decimal)
    conventions = document["conventions"]
    lifecycle = usd(conventions["assignment_exercise_settlement_fee"])
    return AssumedFlatFeeSchedule(
        schedule_id="ledger-fixtures-assumed-flat",
        trade_per_contract=usd(conventions["option_trade_fee_per_contract_side_usd"]),
        exercise_assignment_per_contract=lifecycle,
        cash_settlement_per_contract=lifecycle,
    )


def usd(text: str) -> Usd:
    """Return exact dollars from a decimal string."""
    return Usd(Decimal(text))


def price(text: str) -> Price:
    """Return an exact price from a decimal string."""
    return Price(Decimal(text))


def cents_price(cents: int) -> Price:
    """Return an exact price from an integer number of cents."""
    return Price(Decimal(cents).scaleb(-2))


def cents_usd(cents: int) -> Usd:
    """Return exact dollars from an integer number of cents."""
    return Usd(Decimal(cents).scaleb(-2))


def quote(bid: str, ask: str) -> Quote:
    """Return a two-sided quote from decimal strings."""
    return Quote(price(bid), price(ask))


def cents_quotes(
    contracts: Sequence[ContractTerms], cents: Sequence[tuple[int, int]]
) -> dict[str, Quote]:
    """Return one quote per contract from (bid, ask) cent pairs given in the same order."""
    return {
        terms.contract_id: Quote(cents_price(bid), cents_price(ask))
        for terms, (bid, ask) in zip(contracts, cents, strict=True)
    }


def settle_day(day: int) -> date:
    """Return the synthetic calendar date of day ``day``."""
    return BASE_DATE + timedelta(days=day)


def moment(day: int, step: int = 0) -> int:
    """Return a UTC nanosecond timestamp on day ``day``; ``step`` orders events within it."""
    return T0_NS + day * DAY_NS + step


# --- contract terms --------------------------------------------------------------------------


def index_option(
    option_type: OptionType,
    strike: str,
    *,
    multiplier: str = "100",
    units: int = 100,
    expires_at_ns: int = EXPIRES_AT_NS,
) -> ContractTerms:
    """Return a European cash-settled index option: deliverable ``{SPX: units}``, AEA units x K."""
    code = "C" if option_type is OptionType.CALL else "P"
    component = DeliverableComponent(INDEX_ASSET, Decimal(units))
    return ContractTerms(
        contract_id=f"SPXW-{code}{strike}-{expires_at_ns}",
        option_type=option_type,
        strike=price(strike),
        exercise_style=ExerciseStyle.EUROPEAN,
        settlement_type=SettlementType.CASH,
        premium_multiplier=Decimal(multiplier),
        deliverable=Deliverable(f"{INDEX_ASSET}-x{units}", (component,), ZERO),
        aggregate_exercise_amount=usd(strike).scaled_by(units),
        expires_at_ns=expires_at_ns,
    )


def stock_option(
    option_type: OptionType,
    strike: str,
    *,
    shares: str,
    aggregate: str,
    multiplier: str = "100",
) -> ContractTerms:
    """Return an American physically settled option: ``shares`` of XYZ for ``aggregate`` USD."""
    code = "C" if option_type is OptionType.CALL else "P"
    component = DeliverableComponent(STOCK_ASSET, Decimal(shares))
    return ContractTerms(
        contract_id=f"{STOCK_ASSET}-{code}{strike}-x{shares}",
        option_type=option_type,
        strike=price(strike),
        exercise_style=ExerciseStyle.AMERICAN,
        settlement_type=SettlementType.PHYSICAL,
        premium_multiplier=Decimal(multiplier),
        deliverable=Deliverable(f"{STOCK_ASSET}-x{shares}", (component,), ZERO),
        aggregate_exercise_amount=usd(aggregate),
        expires_at_ns=EXPIRES_AT_NS,
    )


# --- ledger reading --------------------------------------------------------------------------


def account(kind: AccountKind, ref: str = "") -> AccountKey:
    """Return an account key; ``ref`` is "" for CASH and CAPITAL."""
    return AccountKey(kind, ref)


def dated(kind: AccountKind, day: int) -> AccountKey:
    """Return the RECEIVABLE or PAYABLE account for the settlement date of day ``day``."""
    return AccountKey(kind, settle_day(day).isoformat())


def total(amounts: Iterable[Usd]) -> Usd:
    """Return the exact sum of ``amounts``."""
    return sum(amounts, start=ZERO)


def posting_map(entry: LedgerEntry) -> dict[AccountKey, Usd]:
    """Return the entry's postings by account, asserting the merged form (one per account)."""
    accounts = [posting.account for posting in entry.postings]
    assert len(set(accounts)) == len(accounts), f"postings not merged per account: {accounts}"
    return {posting.account: posting.amount for posting in entry.postings}


def fee_postings(fees: Iterable[FeeLine]) -> dict[AccountKey, Usd]:
    """Return the FEES postings that ``fees`` imply: one per component, zero amounts omitted."""
    totals: dict[AccountKey, Usd] = {}
    for line in fees:
        key = AccountKey(AccountKind.FEES, line.component_id)
        totals[key] = totals.get(key, ZERO) + line.amount
    return {key: amount for key, amount in totals.items() if amount != ZERO}


def balance(state: LedgerState, key: AccountKey) -> Usd:
    """Return the balance of ``key``; an account never posted to is zero."""
    return state.balances.get(key, ZERO)


def kind_total(state: LedgerState, kind: AccountKind) -> Usd:
    """Return the sum of every balance of ``kind``."""
    return total(amount for key, amount in state.balances.items() if key.kind is kind)


def cash_like(state: LedgerState) -> Usd:
    """Return CASH + ΣRECEIVABLE + ΣPAYABLE: the part of NLV that is not a marked position."""
    return total(amount for key, amount in state.balances.items() if key.kind in CASH_KINDS)


def held_quantity(state: LedgerState, instrument_id: str) -> int:
    """Return the signed quantity held in ``instrument_id`` across its lots."""
    return sum(lot.quantity for lot in state.lots.get(instrument_id, ()))


def package_contracts(state: LedgerState, contract_ids: Iterable[str]) -> int:
    """Return Σ|quantity| over the lots of ``contract_ids``: the package's fee base."""
    return sum(abs(lot.quantity) for cid in contract_ids for lot in state.lots.get(cid, ()))


def settle_through(state: LedgerState, *, event_id: str, at_ns: int, through: date) -> LedgerState:
    """Book and apply the due-cash transfer through ``through``; something must be due."""
    entry = book_settle_due(state, event_id=event_id, at_ns=at_ns, through=through)
    assert entry is not None, f"nothing was due through {through}"
    return apply_entry(state, entry)
