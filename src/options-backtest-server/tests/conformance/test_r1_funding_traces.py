"""R1Campaign ``pass-refusal`` / ``fail-unfunded-close`` traces replayed on the ledger.

Design §11.2 (closing fills are funding-checked; ``FullyFunded`` holds after every event) and
§21; ADR 0001 §5 (funding rule and the ``R1Campaign`` refinement table: cash = CASH,
pay = -ΣPAYABLE, reserve = Σ encumbrances, ``Funded(c, p, r)`` = ``funding_headroom >= 0``) and
its §11 amendment (a package cash-settles in one entry netted to one payable, refining
``R1Campaign.Settle``).

Constants of ``R1Campaign.pass-refusal.cfg``: multiplier 1, width W = 2, Cash0 = 4, Fee = 1 per
package fill, here 0.50 per contract side on two legs, which also makes the exit-fee provision 1
(``HeldReserve`` = W + Fee = 3). Trace: sell the 102/100 put vertical for a credit of 1, funded
with zero slack, (4 - 1) - 0 - 3 = 0, the credit receivable uncounted; T+1 moves cash to 4; a
natural close at package ask 4 would leave (4 - 1) - 4 - 0 = -1 < 0, so the fill is refused and
the position kept. It later cash-settles in one entry at package value
v = max(102 - S, 0) - max(100 - S, 0) in 0..W, and the account stays funded at every level S,
deep in the money included: 4 - v >= Fee >= 0 both before and after the T+1 transfer. The
``fail-unfunded-close`` twin commits the close anyway and T+1 leaves settled cash at -1,
breaking ``FullyFunded``.
"""

import pytest
from builders import (
    EXPIRY_DAY,
    INDEX_ASSET,
    ZERO,
    account,
    balance,
    cents_price,
    cents_usd,
    dated,
    index_option,
    moment,
    posting_map,
    price,
    settle_day,
    settle_through,
    total,
    usd,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from options_backtest.engine.fees import AssumedFlatFeeSchedule, lifecycle_fees, trade_fees
from options_backtest.engine.funding import campaign_encumbrances, funding_headroom
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.settlement import book_cash_settlement, book_settle_due
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.models.ledger import AccountKind, FeeEvent, LedgerEntry, LedgerState, LegFill
from options_backtest.models.market import OptionType
from options_backtest.money import Price

CAMPAIGN = "r1-pass-refusal"
SHORT = index_option(OptionType.PUT, "102", multiplier="1", units=1)
LONG = index_option(OptionType.PUT, "100", multiplier="1", units=1)
PACKAGE = (SHORT.contract_id, LONG.contract_id)
SCHEDULE = AssumedFlatFeeSchedule(
    schedule_id="r1-pass-refusal-fee",
    trade_per_contract=usd("0.50"),
    exercise_assignment_per_contract=ZERO,
    cash_settlement_per_contract=ZERO,
)
CASH0 = usd("4.00")
WIDTH = usd("2.00")
FEE = usd("1.00")
SETTINGS = settings(derandomize=True, database=None, max_examples=200, deadline=None)


def _open() -> tuple[LedgerState, LedgerState]:
    """Return the state right after the entry fill and after its T+1 cash transfer."""
    deposit = book_deposit(event_id="r1-deposit", at_ns=moment(0), cash=CASH0)
    funded = apply_entry(LedgerState.empty(), deposit)
    # Package bid 1: sell the 102 put at its 1.50 bid, buy the 100 put at its 0.50 ask.
    legs = (LegFill(SHORT, -1, price("1.50")), LegFill(LONG, 1, price("0.50")))
    entry = book_option_trade(
        funded,
        event_id="r1-entry",
        at_ns=moment(0, 1),
        campaign_id=CAMPAIGN,
        legs=legs,
        fees=trade_fees(SCHEDULE, legs),
        settles_on=settle_day(1),
    )
    opened = apply_entry(funded, entry)
    settled = settle_through(
        opened, event_id="r1-settle-entry", at_ns=moment(1), through=settle_day(1)
    )
    return opened, settled


def _natural_close(state: LedgerState) -> LedgerEntry:
    """Book the close at package ask 4: buy the 102 put at its 4.00 ask, sell the 100 at 0.00."""
    legs = (LegFill(SHORT, 1, price("4.00")), LegFill(LONG, -1, price("0.00")))
    return book_option_trade(
        state,
        event_id="r1-close",
        at_ns=moment(1, 1),
        campaign_id=CAMPAIGN,
        legs=legs,
        fees=trade_fees(SCHEDULE, legs),
        settles_on=settle_day(2),
    )


def _cash_settle(state: LedgerState, level: Price) -> LedgerEntry:
    """Book the kept package's settlement in one entry; the fee base is Σ|q| = 2."""
    return book_cash_settlement(
        state,
        event_id="r1-cash-settle",
        at_ns=moment(EXPIRY_DAY),
        contract_ids=PACKAGE,
        settlement={INDEX_ASSET: level},
        fees=lifecycle_fees(SCHEDULE, FeeEvent.CASH_SETTLEMENT, 2),
        settles_on=settle_day(EXPIRY_DAY + 1),
        settlement_ref="r1-official-settlement",
    )


def _settle_due(state: LedgerState) -> LedgerState:
    """Apply the T+1 transfer of the settlement payable; nothing is due when v = 0."""
    due = book_settle_due(
        state,
        event_id="r1-settle-expiry",
        at_ns=moment(EXPIRY_DAY + 1),
        through=settle_day(EXPIRY_DAY + 1),
    )
    return state if due is None else apply_entry(state, due)


def test_entry_fill_is_funded_with_zero_slack_and_its_credit_uncounted() -> None:
    opened, _ = _open()
    encumbrances = campaign_encumbrances(opened, SCHEDULE)

    assert set(encumbrances) == {CAMPAIGN}
    assert encumbrances[CAMPAIGN].settlement == WIDTH
    assert encumbrances[CAMPAIGN].fee_provision == FEE
    assert balance(opened, dated(AccountKind.RECEIVABLE, 1)) == usd("1.00")
    assert funding_headroom(opened, SCHEDULE) == ZERO


def test_natural_close_above_the_width_is_refused() -> None:
    _, settled = _open()

    preview = apply_entry(settled, _natural_close(settled))

    assert balance(settled, account(AccountKind.CASH)) == CASH0
    assert funding_headroom(settled, SCHEDULE) == CASH0 - WIDTH - FEE
    assert funding_headroom(preview, SCHEDULE) == usd("-1.00")


@pytest.mark.parametrize(
    ("level", "package_value"),
    [
        pytest.param("50", "2.00", id="deep-itm"),
        pytest.param("100", "2.00", id="at-long-strike"),
        pytest.param("100.75", "1.25", id="between-strikes"),
        pytest.param("101", "1.00", id="mid-width"),
        pytest.param("102", "0.00", id="at-short-strike"),
        pytest.param("150", "0.00", id="far-otm"),
    ],
)
def test_the_kept_position_cash_settles_and_the_account_stays_funded(
    level: str, package_value: str
) -> None:
    _, kept = _open()

    state = apply_entry(kept, _cash_settle(kept, price(level)))

    remaining = campaign_encumbrances(state, SCHEDULE).values()
    assert total(e.settlement + e.fee_provision for e in remaining) == ZERO
    assert funding_headroom(state, SCHEDULE) == CASH0 - usd(package_value)
    final = _settle_due(state)
    assert balance(final, account(AccountKind.CASH)) == CASH0 - usd(package_value)
    assert funding_headroom(final, SCHEDULE) == CASH0 - usd(package_value)


def test_deep_in_the_money_settlement_nets_the_legs_to_the_width() -> None:
    # At 50 the short 102 put pays 52 and the long 100 put receives 50: one PAYABLE -2 (= W),
    # never a gross -52 beside an uncounted +50 receivable.
    _, kept = _open()

    entry = _cash_settle(kept, price("50"))

    assert posting_map(entry) == {
        dated(AccountKind.PAYABLE, EXPIRY_DAY + 1): -WIDTH,
        account(AccountKind.OPTION_COST, SHORT.contract_id): usd("1.50"),
        account(AccountKind.REALIZED_PNL, SHORT.contract_id): usd("50.50"),
        account(AccountKind.OPTION_COST, LONG.contract_id): usd("-0.50"),
        account(AccountKind.REALIZED_PNL, LONG.contract_id): usd("-49.50"),
    }


@SETTINGS
@given(level_cents=st.integers(0, 30_000))
def test_headroom_stays_at_cash_less_package_value_at_every_settlement_level(
    level_cents: int,
) -> None:
    # Integer-cent oracle: v = max(102 - S, 0) - max(100 - S, 0), so 0 <= v <= W.
    value_cents = max(10_200 - level_cents, 0) - max(10_000 - level_cents, 0)
    _, kept = _open()

    state = apply_entry(kept, _cash_settle(kept, cents_price(level_cents)))
    final = _settle_due(state)

    for observed in (state, final):
        headroom = funding_headroom(observed, SCHEDULE)
        assert headroom == CASH0 - cents_usd(value_cents)
        assert headroom >= FEE


def test_without_the_gate_the_close_drives_settled_cash_negative() -> None:
    _, settled = _open()

    closed = apply_entry(settled, _natural_close(settled))  # apply_entry itself never gates funding
    final = settle_through(
        closed, event_id="r1-settle-close", at_ns=moment(2), through=settle_day(2)
    )

    assert balance(final, account(AccountKind.CASH)) == usd("-1.00")
    assert funding_headroom(final, SCHEDULE) == usd("-1.00")
