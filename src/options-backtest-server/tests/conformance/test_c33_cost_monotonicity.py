"""C33: on the same fill sequence, worse prices or higher fees never improve net P&L.

Design §18.2 C33 (not asserted across different resulting trade sequences); ADR 0001 §6. Both
runs book identical legs and contract counts through the same posting functions and are valued
at identical marks; only the fill prices (buys up, sells down) or the per-contract fee differ.
Positions and marks being equal, net P&L differs by exactly the extra premium paid plus the extra
fees: both the ``<=`` contract and that exact gap are asserted.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from builders import (
    ZERO,
    cents_price,
    cents_quotes,
    cents_usd,
    held_quantity,
    index_option,
    moment,
    settle_day,
    usd,
)
from hypothesis import given, settings
from hypothesis import strategies as st

from options_backtest.engine.fees import AssumedFlatFeeSchedule, trade_fees
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.settlement import book_settle_due
from options_backtest.engine.trades import book_deposit, book_option_trade
from options_backtest.engine.valuation import MarkBasis, net_pnl, value_account
from options_backtest.models.ledger import LedgerState, LegFill
from options_backtest.models.market import OptionType
from options_backtest.money import Price, Usd

UNIVERSE = (
    index_option(OptionType.PUT, "95"),
    index_option(OptionType.PUT, "100"),
    index_option(OptionType.CALL, "105"),
)
SETTINGS = settings(derandomize=True, database=None, max_examples=100, deadline=None)


@dataclass(frozen=True)
class LegDraw:
    """One leg of a fill: universe index, signed contracts, baseline price, price worsening."""

    index: int
    contracts: int
    price_cents: int
    worsening_cents: int


QUOTE_CENTS = st.integers(1, 1_500).flatmap(
    lambda ask: st.tuples(st.integers(0, ask), st.just(ask))
)
MARKS = st.tuples(QUOTE_CENTS, QUOTE_CENTS, QUOTE_CENTS)
LEG_DRAWS = st.builds(
    LegDraw,
    index=st.integers(0, len(UNIVERSE) - 1),
    contracts=st.sampled_from((-3, -2, -1, 1, 2, 3)),
    price_cents=st.integers(0, 1_000),
    worsening_cents=st.integers(0, 200),
)
FILLS = st.lists(
    st.lists(LEG_DRAWS, min_size=1, max_size=len(UNIVERSE), unique_by=lambda d: d.index).map(tuple),
    min_size=1,
    max_size=6,
)
FEE_CENTS = st.integers(0, 150)


def _fill_price(draw: LegDraw, worse: bool) -> Price:
    """Return the baseline price, or a worse one: higher for a buy, lower (floor 0) for a sell."""
    if not worse:
        return cents_price(draw.price_cents)
    if draw.contracts > 0:
        return cents_price(draw.price_cents + draw.worsening_cents)
    return cents_price(max(draw.price_cents - draw.worsening_cents, 0))


def _run(fills: Sequence[tuple[LegDraw, ...]], fee_cents: int, worse: bool) -> LedgerState:
    """Book every fill in order, then move all due cash; return the final state."""
    schedule = AssumedFlatFeeSchedule(
        schedule_id=f"c33-{fee_cents}-cents",
        trade_per_contract=cents_usd(fee_cents),
        exercise_assignment_per_contract=ZERO,
        cash_settlement_per_contract=ZERO,
    )
    deposit = book_deposit(event_id="c33-deposit", at_ns=moment(0), cash=usd("100000.00"))
    state = apply_entry(LedgerState.empty(), deposit)
    for day, fill in enumerate(fills, start=1):
        legs = tuple(
            LegFill(UNIVERSE[draw.index], draw.contracts, _fill_price(draw, worse)) for draw in fill
        )
        entry = book_option_trade(
            state,
            event_id=f"c33-fill-{day}",
            at_ns=moment(day),
            campaign_id="c33",
            legs=legs,
            fees=trade_fees(schedule, legs),
            settles_on=settle_day(day + 1),
        )
        state = apply_entry(state, entry)
    last = len(fills) + 1
    due = book_settle_due(
        state, event_id="c33-settle", at_ns=moment(last), through=settle_day(last)
    )
    return state if due is None else apply_entry(state, due)


def _positions(state: LedgerState) -> dict[str, int]:
    return {terms.contract_id: held_quantity(state, terms.contract_id) for terms in UNIVERSE}


def _net_pnl(state: LedgerState, marks: Sequence[tuple[int, int]]) -> Usd:
    valuation = value_account(state, cents_quotes(UNIVERSE, marks), {}, MarkBasis.MID)
    return net_pnl(valuation, state)


@SETTINGS
@given(fills=FILLS, fee_cents=FEE_CENTS, marks=MARKS)
def test_worse_fill_prices_never_improve_net_pnl(
    fills: list[tuple[LegDraw, ...]], fee_cents: int, marks: tuple[tuple[int, int], ...]
) -> None:
    baseline = _run(fills, fee_cents, worse=False)
    worse = _run(fills, fee_cents, worse=True)

    assert _positions(worse) == _positions(baseline)
    assert _net_pnl(worse, marks) <= _net_pnl(baseline, marks)
    extra_premium = sum(
        (
            UNIVERSE[d.index].premium_usd(_fill_price(d, True), d.contracts)
            - UNIVERSE[d.index].premium_usd(_fill_price(d, False), d.contracts)
            for fill in fills
            for d in fill
        ),
        start=ZERO,
    )
    assert _net_pnl(baseline, marks) - _net_pnl(worse, marks) == extra_premium


@SETTINGS
@given(fills=FILLS, fee_cents=FEE_CENTS, extra_fee_cents=st.integers(0, 100), marks=MARKS)
def test_higher_fees_never_improve_net_pnl(
    fills: list[tuple[LegDraw, ...]],
    fee_cents: int,
    extra_fee_cents: int,
    marks: tuple[tuple[int, int], ...],
) -> None:
    baseline = _run(fills, fee_cents, worse=False)
    costlier = _run(fills, fee_cents + extra_fee_cents, worse=False)

    assert _positions(costlier) == _positions(baseline)
    assert _net_pnl(costlier, marks) <= _net_pnl(baseline, marks)
    traded = sum(abs(d.contracts) for fill in fills for d in fill)
    extra_fees = cents_usd(extra_fee_cents).scaled_by(traded)
    assert _net_pnl(baseline, marks) - _net_pnl(costlier, marks) == extra_fees
