"""Journal properties over generated entry sequences (ADR 0001 §5 ``apply_entry``, §6 journal).

Each generated run deposits cash and stock, then books a random sequence of option trades
(filled at the natural side of freshly drawn quotes), due-cash transfers and cash settlements
of the whole held package (one campaign, one expiry; ADR §11 amendment) through the posting
functions. A settlement falls at the universe's expiry, after which its contracts never trade.
After every entry: the entry balances with nonzero, merged postings and whole cents on cash
accounts; the trial balance is zero; replaying the prefix from scratch reproduces the state;
OPTION_COST/STOCK_COST equal their lots' quantity x unit cost; ``reconcile`` is exactly zero;
MID and NATURAL NLV equal an in-test mark oracle, NATURAL never above MID; and a fill never
raises mid NLV at its own quotes (``R1Campaign.FillNeverRaisesNLV``). Stale,
unbalanced, sub-cent and retired-contract entries are rejected with ``LedgerInvariantError``.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

import pytest
from builders import (
    CASH_KINDS,
    EXPIRY_DAY,
    INDEX_ASSET,
    STOCK_ASSET,
    ZERO,
    account,
    cash_like,
    cents_price,
    cents_quotes,
    index_option,
    moment,
    package_contracts,
    posting_map,
    price,
    settle_day,
    total,
    usd,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from options_backtest.engine.fees import AssumedFlatFeeSchedule, lifecycle_fees, trade_fees
from options_backtest.engine.journal import Journal, apply_entry, replay
from options_backtest.engine.settlement import book_cash_settlement, book_settle_due
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.engine.valuation import MarkBasis, reconcile, stock_value, value_account
from options_backtest.errors import LedgerInvariantError
from options_backtest.models.ledger import (
    AccountKey,
    AccountKind,
    FeeEvent,
    LedgerEntry,
    LedgerState,
    LegFill,
    Lot,
    Posting,
)
from options_backtest.models.market import ContractTerms, OptionType, Quote
from options_backtest.money import Price, Usd

CAMPAIGN = "journal"
UNIVERSE = (
    index_option(OptionType.PUT, "95"),
    index_option(OptionType.PUT, "100"),
    index_option(OptionType.CALL, "105"),
)
SCHEDULE = AssumedFlatFeeSchedule(
    schedule_id="journal-property-schedule",
    trade_per_contract=usd("0.65"),
    exercise_assignment_per_contract=ZERO,
    cash_settlement_per_contract=usd("0.10"),
)
STOCK_PRICES = MappingProxyType({STOCK_ASSET: price("95.00")})
COST_KINDS = frozenset({AccountKind.OPTION_COST, AccountKind.STOCK_COST})
SETTINGS = settings(derandomize=True, database=None, max_examples=100, deadline=None)


@dataclass(frozen=True)
class Trade:
    """Fill ``legs`` (universe index, signed contracts) at the natural side of ``quotes``."""

    legs: tuple[tuple[int, int], ...]
    quotes: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class SettleDue:
    """Move every receivable and payable due by the step's date to cash."""


@dataclass(frozen=True)
class CashSettle:
    """Cash-settle every held contract, the campaign's one-expiry package, at ``level_cents``."""

    level_cents: int


Step = Trade | SettleDue | CashSettle


@dataclass(frozen=True)
class Applied:
    """One applied entry, the states around it and the quotes current when it was booked."""

    entry: LedgerEntry
    before: LedgerState
    after: LedgerState
    quotes: Mapping[str, Quote]
    is_fill: bool


QUOTE_CENTS = st.integers(1, 800).flatmap(lambda ask: st.tuples(st.integers(0, ask), st.just(ask)))
QUOTE_SETS = st.tuples(QUOTE_CENTS, QUOTE_CENTS, QUOTE_CENTS)
LEGS = st.lists(
    st.tuples(st.integers(0, len(UNIVERSE) - 1), st.sampled_from((-3, -2, -1, 1, 2, 3))),
    min_size=1,
    max_size=len(UNIVERSE),
    unique_by=lambda leg: leg[0],
).map(tuple)
TRADES = st.builds(Trade, legs=LEGS, quotes=QUOTE_SETS)
STEPS = st.lists(
    st.one_of(  # trades listed thrice: most steps trade, so lots are closed and reversed often
        TRADES,
        TRADES,
        TRADES,
        st.just(SettleDue()),
        st.builds(CashSettle, level_cents=st.integers(8_000, 12_000)),
    ),
    min_size=1,
    max_size=12,
)


# --- building runs ---------------------------------------------------------------------------


def _deposit() -> LedgerEntry:
    lot = Lot(
        lot_id="journal-stock",
        instrument_id=STOCK_ASSET,
        quantity=100,
        unit_cost=usd("90.00"),
        campaign_id=None,
        opened_at_ns=moment(0),
    )
    return book_deposit(
        event_id="journal-deposit", at_ns=moment(0), cash=usd("100000.00"), stock=(lot,)
    )


def _natural_fill(terms: ContractTerms, contracts: int, quotes: Mapping[str, Quote]) -> LegFill:
    side = quotes[terms.contract_id]
    return LegFill(terms, contracts, side.ask if contracts > 0 else side.bid)


def _trade(
    state: LedgerState,
    legs: Sequence[tuple[ContractTerms, int]],
    quotes: Mapping[str, Quote],
    day: int,
) -> LedgerEntry:
    fills = tuple(_natural_fill(terms, contracts, quotes) for terms, contracts in legs)
    return book_option_trade(
        state,
        event_id=f"trade-day-{day}",
        at_ns=moment(day),
        campaign_id=CAMPAIGN,
        legs=fills,
        fees=trade_fees(SCHEDULE, fills),
        settles_on=settle_day(day + 1),
    )


def _cash_settle(
    state: LedgerState, package: Sequence[ContractTerms], level: Price, day: int
) -> LedgerEntry:
    contract_ids = tuple(terms.contract_id for terms in package)
    contracts = package_contracts(state, contract_ids)
    return book_cash_settlement(
        state,
        event_id=f"cash-settle-day-{day}",
        at_ns=moment(day),
        contract_ids=contract_ids,
        settlement={INDEX_ASSET: level},
        fees=lifecycle_fees(SCHEDULE, FeeEvent.CASH_SETTLEMENT, contracts),
        settles_on=settle_day(day + 1),
        settlement_ref=f"official-settlement-day-{day}",
    )


def _book(step: Step, state: LedgerState, day: int) -> LedgerEntry | None:
    """Book ``step`` on ``state``, or return None when it does not apply to the position."""
    if isinstance(step, Trade):
        live = [(UNIVERSE[i], n) for i, n in step.legs if UNIVERSE[i].expires_at_ns > moment(day)]
        return _trade(state, live, cents_quotes(UNIVERSE, step.quotes), day) if live else None
    if isinstance(step, CashSettle):
        package = [terms for terms in UNIVERSE if state.lots.get(terms.contract_id)]
        return _cash_settle(state, package, cents_price(step.level_cents), day) if package else None
    return book_settle_due(
        state, event_id=f"settle-day-{day}", at_ns=moment(day), through=settle_day(day)
    )


def run_steps(initial: Sequence[tuple[int, int]], steps: Sequence[Step]) -> list[Applied]:
    """Apply the deposit and every applicable step; return each entry with its surroundings."""
    deposit = _deposit()
    state = apply_entry(LedgerState.empty(), deposit)
    quotes = cents_quotes(UNIVERSE, initial)
    applied = [Applied(deposit, LedgerState.empty(), state, quotes, is_fill=False)]
    expired = False
    for number, step in enumerate(steps, start=1):
        # One step per day; a cash settlement moves the clock to the universe's expiry, so it and
        # every later step fall at or after it (STEPS has fewer steps than EXPIRY_DAY days).
        settles = isinstance(step, CashSettle)
        day = number + EXPIRY_DAY if expired or settles else number
        quotes = cents_quotes(UNIVERSE, step.quotes) if isinstance(step, Trade) else quotes
        entry = _book(step, state, day)
        if entry is None:
            continue
        after = apply_entry(state, entry)
        applied.append(Applied(entry, state, after, quotes, is_fill=isinstance(step, Trade)))
        state = after
        expired = expired or settles
    return applied


def _assert_costs_match_lots(state: LedgerState) -> None:
    carried = {
        key.ref: amount
        for key, amount in state.balances.items()
        if key.kind in COST_KINDS and amount != ZERO
    }
    from_lots = {
        instrument: total(lot.unit_cost.scaled_by(lot.quantity) for lot in lots)
        for instrument, lots in state.lots.items()
    }
    assert carried == {instrument: cost for instrument, cost in from_lots.items() if cost != ZERO}
    for instrument, lots in state.lots.items():
        assert all(lot.instrument_id == instrument and lot.unit_cost >= ZERO for lot in lots)
        assert all(lot.quantity != 0 for lot in lots)
        assert len({lot.quantity > 0 for lot in lots}) <= 1, "long and short lots coexist"


# --- properties of generated sequences -------------------------------------------------------


@SETTINGS
@given(initial=QUOTE_SETS, steps=STEPS)
def test_every_entry_balances_and_the_trial_balance_stays_zero(
    initial: tuple[tuple[int, int], ...], steps: list[Step]
) -> None:
    for record in run_steps(initial, steps):
        postings = posting_map(record.entry)

        assert total(postings.values()) == ZERO
        assert ZERO not in postings.values()
        assert all(amount.is_cents() for key, amount in postings.items() if key.kind in CASH_KINDS)
        assert total(record.after.balances.values()) == ZERO
        assert record.after.entry_count == record.entry.sequence == record.before.entry_count + 1


@SETTINGS
@given(initial=QUOTE_SETS, steps=STEPS)
def test_replaying_every_prefix_reproduces_the_incremental_state(
    initial: tuple[tuple[int, int], ...], steps: list[Step]
) -> None:
    applied = run_steps(initial, steps)
    entries = [record.entry for record in applied]

    for count, record in enumerate(applied, start=1):
        assert replay(entries[:count]) == record.after


@SETTINGS
@given(initial=QUOTE_SETS, steps=STEPS)
def test_cost_accounts_agree_with_lot_quantities(
    initial: tuple[tuple[int, int], ...], steps: list[Step]
) -> None:
    for record in run_steps(initial, steps):
        _assert_costs_match_lots(record.after)


@SETTINGS
@given(initial=QUOTE_SETS, steps=STEPS)
def test_reconcile_is_exactly_zero_after_every_entry(
    initial: tuple[tuple[int, int], ...], steps: list[Step]
) -> None:
    for record in run_steps(initial, steps):
        for basis in MarkBasis:
            valuation = value_account(record.after, record.quotes, STOCK_PRICES, basis)
            assert reconcile(valuation, record.after) == ZERO


def _oracle_nlv(state: LedgerState, quotes: Mapping[str, Quote], basis: MarkBasis) -> Usd:
    """Return cash-like balances plus every holding at ``basis`` marks, computed here."""
    marked = []
    for instrument, lots in state.lots.items():
        quantity = sum(lot.quantity for lot in lots)
        terms = state.contracts.get(instrument)
        if terms is None:
            marked.append(stock_value(instrument, quantity, STOCK_PRICES[instrument]))
            continue
        quote = quotes[instrument]
        natural = quote.bid if quantity > 0 else quote.ask
        mark = Price.mid(quote.bid, quote.ask) if basis is MarkBasis.MID else natural
        marked.append(terms.premium_usd(mark, quantity))
    return cash_like(state) + total(marked)


@SETTINGS
@given(initial=QUOTE_SETS, steps=STEPS)
def test_nlv_matches_the_mark_oracle_and_natural_never_exceeds_mid(
    initial: tuple[tuple[int, int], ...], steps: list[Step]
) -> None:
    for record in run_steps(initial, steps):
        mid = value_account(record.after, record.quotes, STOCK_PRICES, MarkBasis.MID)
        natural = value_account(record.after, record.quotes, STOCK_PRICES, MarkBasis.NATURAL)

        assert mid.nlv == _oracle_nlv(record.after, record.quotes, MarkBasis.MID)
        assert natural.nlv == _oracle_nlv(record.after, record.quotes, MarkBasis.NATURAL)
        assert natural.nlv <= mid.nlv


@SETTINGS
@given(initial=QUOTE_SETS, steps=STEPS)
def test_a_fill_never_raises_mid_nlv_at_its_own_quotes(
    initial: tuple[tuple[int, int], ...], steps: list[Step]
) -> None:
    fills = [record for record in run_steps(initial, steps) if record.is_fill]

    for record in fills:
        before = value_account(record.before, record.quotes, STOCK_PRICES, MarkBasis.MID)
        after = value_account(record.after, record.quotes, STOCK_PRICES, MarkBasis.MID)
        assert after.nlv <= before.nlv


# --- rejected entries ------------------------------------------------------------------------


def _with_amounts(entry: LedgerEntry, amounts: Mapping[AccountKey, Usd]) -> LedgerEntry:
    """Return ``entry`` with the named accounts' posting amounts replaced, order kept."""
    postings = tuple(
        Posting(posting.account, amounts.get(posting.account, posting.amount))
        for posting in entry.postings
    )
    return dataclasses.replace(entry, postings=postings)


def _sell_one(state: LedgerState, terms: ContractTerms, day: int) -> LedgerEntry:
    return _trade(state, [(terms, -1)], cents_quotes(UNIVERSE, ((150, 200),) * len(UNIVERSE)), day)


def test_a_stale_sequence_raises() -> None:
    funded = apply_entry(LedgerState.empty(), _deposit())
    trade = _sell_one(funded, UNIVERSE[1], day=1)
    traded = apply_entry(funded, trade)

    with pytest.raises(LedgerInvariantError):
        apply_entry(traded, trade)


def test_an_unbalanced_entry_raises() -> None:
    deposit = book_deposit(event_id="unbalanced", at_ns=moment(0), cash=usd("10000.00"))
    capital = account(AccountKind.CAPITAL)

    with pytest.raises(LedgerInvariantError):
        apply_entry(LedgerState.empty(), _with_amounts(deposit, {capital: usd("-9999.99")}))


def test_a_balanced_sub_cent_cash_posting_raises() -> None:
    deposit = book_deposit(event_id="sub-cent", at_ns=moment(0), cash=usd("10000.00"))
    amounts = {
        account(AccountKind.CASH): usd("10000.005"),
        account(AccountKind.CAPITAL): usd("-10000.005"),
    }

    with pytest.raises(LedgerInvariantError):
        apply_entry(LedgerState.empty(), _with_amounts(deposit, amounts))


def test_a_cost_posting_that_disagrees_with_its_lots_raises() -> None:
    # Balanced and in whole cents, but STOCK_COST no longer equals the lot's 100 x 90.00.
    deposit = _deposit()
    amounts = {
        account(AccountKind.STOCK_COST, STOCK_ASSET): usd("9000.01"),
        account(AccountKind.CAPITAL): usd("-109000.01"),
    }

    with pytest.raises(LedgerInvariantError):
        apply_entry(LedgerState.empty(), _with_amounts(deposit, amounts))


def test_an_entry_touching_a_retired_contract_raises() -> None:
    terms = UNIVERSE[1]
    funded = apply_entry(LedgerState.empty(), _deposit())
    held = apply_entry(funded, _sell_one(funded, terms, day=1))
    settled = apply_entry(held, _cash_settle(held, (terms,), price("97.00"), day=EXPIRY_DAY))
    assert terms.contract_id in settled.retired

    with pytest.raises(LedgerInvariantError, match="retired"):
        apply_entry(settled, _sell_one(settled, terms, day=EXPIRY_DAY + 1))


def test_journal_commit_rejects_a_repeated_event_id() -> None:
    journal = Journal()
    deposit = _deposit()
    journal.commit(deposit)
    trade = _sell_one(apply_entry(LedgerState.empty(), deposit), UNIVERSE[1], day=1)

    with pytest.raises(LedgerInvariantError):
        journal.commit(dataclasses.replace(trade, event_id=deposit.event_id))
    journal.commit(trade)
