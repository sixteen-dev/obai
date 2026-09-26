"""The R1 fill model: six checks in order, capacity, funding (ADR 0002 §9, §16, §17 item 38).

The market is the engine builders' SPXW 4900/4895 put vertical. Entries are sold at Monday's
fill slots (decision at DEC 15:45, F1-F3 15:46-15:48); closes are bought back on Tuesday. The
default quotes give the G01 package: sell 4900 at the bid 2.00, buy 4895 at the ask 1.10,
``D = 100·(-1)·2.00 + 100·(+1)·1.10 = -90``, $1.00 per contract side.
"""

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from data_builders import MON, SECOND_NS, TUE, quote_obs, slot_ns
from engine_builders import (
    GENERATION,
    LONG,
    LONG_ID,
    MON_S,
    SCHEDULE,
    SHORT,
    SHORT_ID,
    TUE_S,
    WED_S,
    funded,
    held,
    leg,
    market,
    order,
    price,
    usd,
)

from options_backtest.data.asof import AsOfView
from options_backtest.data.records import QuoteObservation, TradingSession
from options_backtest.engine import fills
from options_backtest.engine.fees import trade_fees
from options_backtest.engine.fills import (
    CapacityBook,
    Fill,
    FillContext,
    Nonfill,
    QuoteSide,
    try_fill,
)
from options_backtest.engine.funding import funding_headroom
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.orders import NonfillReason, Order, OrderPurpose
from options_backtest.errors import SimulationInvariantError
from options_backtest.models.ledger import AccountKey, AccountKind, EntryKind, LedgerState, LegFill
from options_backtest.models.market import Quote
from options_backtest.models.strategy_checks import PremiumDirection
from options_backtest.money import Price, Usd

SESSION_OF = {MON: MON_S, TUE: TUE_S}
NEXT_SESSION = {MON: TUE, TUE: date(2024, 3, 6)}


def q(  # noqa: PLR0913 — one keyword per raw field a test pins
    contract_id: str,
    bid: str,
    ask: str,
    *,
    slot: str = "F1",
    session: TradingSession = MON_S,
    bid_size: int = 50,
    ask_size: int = 50,
    observed_at_ns: int | None = None,
) -> QuoteObservation:
    return quote_obs(
        contract_id,
        session,
        slot,
        bid,
        ask,
        bid_size=bid_size,
        ask_size=ask_size,
        observed_at_ns=observed_at_ns,
    )


ENTRY_QUOTES = (q(SHORT_ID, "2.00", "2.20"), q(LONG_ID, "1.00", "1.10"))


def ctx(
    session: TradingSession = MON_S,
    slot: str = "F1",
    *,
    participation: str = "0.10",
    direction: PremiumDirection = PremiumDirection.CREDIT,
) -> FillContext:
    return FillContext(
        event_id=f"{session.session_date.isoformat()}:{slot}:4:1",
        at_ns=slot_ns(session, slot),
        settles_on=NEXT_SESSION[session.session_date],
        schedule=SCHEDULE,
        participation_fraction=Decimal(participation),
        premium_direction=direction,
    )


def attempt(  # noqa: PLR0913 — one keyword per attempt input a test varies
    the_order: Order,
    quotes: Sequence[QuoteObservation],
    *,
    state: LedgerState | None = None,
    capacity: CapacityBook | None = None,
    slot: str = "F1",
    participation: str = "0.10",
    direction: PremiumDirection = PremiumDirection.CREDIT,
) -> Fill | Nonfill:
    session = SESSION_OF[the_order.session_date]
    view = AsOfView(market(*quotes), slot_ns(session, slot))
    return try_fill(
        the_order,
        view,
        CapacityBook.empty() if capacity is None else capacity,
        funded() if state is None else state,
        ctx(session, slot, participation=participation, direction=direction),
    )


def nonfill(result: Fill | Nonfill) -> tuple[NonfillReason, str | None, Usd | None]:
    assert isinstance(result, Nonfill), result
    assert result.message
    return result.reason, result.contract_id, result.net_debit


def _filled(result: Fill | Nonfill) -> Fill:
    assert isinstance(result, Fill), result
    return result


# --- a fill --------------------------------------------------------------------------------


def test_an_entry_fills_at_the_naturals_booked_t_plus_1_and_funded_but_not_committed() -> None:
    state = funded()

    fill = _filled(attempt(order(), ENTRY_QUOTES, state=state))

    assert fill.legs == (LegFill(SHORT, -1, price("2.00")), LegFill(LONG, 1, price("1.10")))
    assert fill.quote_ids == (f"q:{SHORT_ID}:2024-03-04:F1", f"q:{LONG_ID}:2024-03-04:F1")
    assert fill.net_debit == usd("-90.00")
    assert fill.fees == trade_fees(SCHEDULE, fill.legs)
    assert sum((line.amount for line in fill.fees), start=usd("0")) == usd("2.00")
    entry = fill.entry
    assert (entry.event_id, entry.kind, entry.campaign_id) == (
        "2024-03-04:F1:4:1",
        EntryKind.TRADE,
        GENERATION,
    )
    assert (entry.sequence, entry.at_ns) == (state.entry_count + 1, slot_ns(MON_S, "F1"))
    postings = {posting.account: posting.amount for posting in entry.postings}
    assert postings[AccountKey(AccountKind.RECEIVABLE, "2024-03-05")] == usd("90.00")
    assert postings[AccountKey(AccountKind.PAYABLE, "2024-03-05")] == usd("-2.00")
    assert fill.post_state == apply_entry(state, entry)
    # 10000 cash - 2 fee payable - (width 500 + exit-fee provision 2); the credit is uncounted.
    assert funding_headroom(fill.post_state, SCHEDULE) == usd("9496.00")


def test_an_order_equal_to_its_limit_fills() -> None:
    assert _filled(attempt(order(limit="-90.00"), ENTRY_QUOTES)).net_debit == usd("-90.00")


def test_a_favourable_move_fills_at_the_later_natural_price() -> None:
    quotes = (q(SHORT_ID, "2.10", "2.30"), q(LONG_ID, "1.00", "1.10"))

    fill = _filled(attempt(order(limit="-90"), quotes))

    assert fill.net_debit == usd("-100.00")
    assert fill.legs[0].price == price("2.10")


def test_a_locked_quote_and_a_no_bid_buy_leg_both_trade() -> None:
    quotes = (q(SHORT_ID, "2.10", "2.10"), q(LONG_ID, "0", "1.10", bid_size=0))

    assert _filled(attempt(order(), quotes)).net_debit == usd("-100.00")


# --- check 1: NO_OBSERVATION ---------------------------------------------------------------


def test_the_decision_observation_itself_never_fills() -> None:
    # G12: the long leg's only quote of the session is its DEC observation (C05).
    quotes = (q(SHORT_ID, "2.00", "2.20"), q(LONG_ID, "1.00", "1.10", slot="DEC"))

    assert nonfill(attempt(order(), quotes)) == (NonfillReason.NO_OBSERVATION, LONG_ID, None)


def test_the_first_leg_without_a_fresh_quote_is_reported() -> None:
    assert nonfill(attempt(order(), ())) == (NonfillReason.NO_OBSERVATION, SHORT_ID, None)


@pytest.mark.parametrize(
    ("observed_before_f1_s", "fills"),
    [(0, True), (1, False)],
)
def test_quote_age_is_measured_from_the_fill_slot_and_120_s_is_inclusive(
    observed_before_f1_s: int, fills: bool
) -> None:
    # At F3 (DEC + 180 s) an F1 observation is 120 s old; one second earlier it is 121 s old.
    observed = slot_ns(MON_S, "F1") - observed_before_f1_s * SECOND_NS
    quotes = (
        q(SHORT_ID, "2.00", "2.20", observed_at_ns=observed),
        q(LONG_ID, "1.00", "1.10", observed_at_ns=observed),
    )

    result = attempt(order(), quotes, slot="F3")

    if fills:
        assert isinstance(result, Fill)
    else:
        assert nonfill(result) == (NonfillReason.NO_OBSERVATION, SHORT_ID, None)


def test_every_leg_passes_check_1_before_any_leg_is_judged_by_check_2() -> None:
    quotes = (q(SHORT_ID, "2.30", "2.20"), q(LONG_ID, "1.00", "1.10", slot="DEC"))

    assert nonfill(attempt(order(), quotes)) == (NonfillReason.NO_OBSERVATION, LONG_ID, None)


# --- check 2: QUOTE_INVALID and NO_SIDE ----------------------------------------------------


@pytest.mark.parametrize(
    ("bid", "ask", "bid_size"),
    [
        ("2.30", "2.20", 50),  # CROSSED
        ("0", "0", 50),  # ZERO_ASK
        ("-0.05", "2.20", 50),  # NEGATIVE price
        ("2.00", "2.20", -1),  # NEGATIVE size
    ],
)
def test_an_invalid_status_is_quote_invalid(bid: str, ask: str, bid_size: int) -> None:
    quotes = (q(SHORT_ID, bid, ask, bid_size=bid_size), q(LONG_ID, "1.00", "1.10"))

    assert nonfill(attempt(order(), quotes)) == (NonfillReason.QUOTE_INVALID, SHORT_ID, None)


@pytest.mark.parametrize(
    ("short", "long", "contract_id"),
    [
        (("0", "0.05", 50, 50), ("1.00", "1.10", 50, 50), SHORT_ID),  # NO_BID on the sell
        (("2.00", "2.20", 0, 50), ("1.00", "1.10", 50, 50), SHORT_ID),  # no bid size to sell
        (("2.00", "2.20", 50, 50), ("1.00", "1.10", 50, 0), LONG_ID),  # no ask size to buy
    ],
)
def test_a_side_without_price_or_size_is_no_side(
    short: tuple[str, str, int, int], long: tuple[str, str, int, int], contract_id: str
) -> None:
    quotes = (
        q(SHORT_ID, short[0], short[1], bid_size=short[2], ask_size=short[3]),
        q(LONG_ID, long[0], long[1], bid_size=long[2], ask_size=long[3]),
    )

    assert nonfill(attempt(order(), quotes)) == (NonfillReason.NO_SIDE, contract_id, None)


def test_check_2_runs_leg_by_leg_status_then_side() -> None:
    no_side_first = (q(SHORT_ID, "0", "0.05"), q(LONG_ID, "1.20", "1.10"))
    invalid_second = (q(SHORT_ID, "2.00", "2.20"), q(LONG_ID, "1.20", "1.10"))

    assert nonfill(attempt(order(), no_side_first)) == (NonfillReason.NO_SIDE, SHORT_ID, None)
    assert nonfill(attempt(order(), invalid_second)) == (
        NonfillReason.QUOTE_INVALID,
        LONG_ID,
        None,
    )


# --- check 3: PREMIUM_DIRECTION ------------------------------------------------------------


@pytest.mark.parametrize(
    ("short_bid", "debit"),
    [("1.10", "0.00"), ("1.00", "10.00")],
)
def test_a_credit_opening_needs_a_positive_credit(short_bid: str, debit: str) -> None:
    # Checked before the limit (-90), which these would also fail.
    quotes = (q(SHORT_ID, short_bid, "2.20"), q(LONG_ID, "1.00", "1.10"))

    assert nonfill(attempt(order(), quotes)) == (
        NonfillReason.PREMIUM_DIRECTION,
        None,
        usd(debit),
    )


def test_a_debit_opening_needs_a_positive_debit() -> None:
    # G07: buy 4900 at the ask, sell 4895 at the bid; a zero net debit is a quote anomaly.
    legs = (leg(SHORT, 1), leg(LONG, -1))
    zero = (q(SHORT_ID, "1.00", "1.10"), q(LONG_ID, "1.10", "1.20"))
    positive = (q(SHORT_ID, "2.00", "2.20"), q(LONG_ID, "1.00", "1.10"))
    debit = PremiumDirection.DEBIT

    assert nonfill(attempt(order(legs=legs, limit="10"), zero, direction=debit)) == (
        NonfillReason.PREMIUM_DIRECTION,
        None,
        usd("0.00"),
    )
    assert _filled(attempt(order(legs=legs, limit="120"), positive, direction=debit)).net_debit == (
        usd("120.00")
    )


def test_a_replacement_is_direction_checked_like_an_entry() -> None:
    quotes = (q(SHORT_ID, "1.10", "2.20"), q(LONG_ID, "1.00", "1.10"))

    assert nonfill(attempt(order(OrderPurpose.ROLL_OPEN), quotes))[0] is (
        NonfillReason.PREMIUM_DIRECTION
    )


def test_a_closing_order_is_not_direction_checked() -> None:
    # Buying the vertical back for a credit (short ask 0.50 < long bid 1.00) is not an anomaly.
    quotes = (
        q(SHORT_ID, "0.40", "0.50", session=TUE_S),
        q(LONG_ID, "1.00", "1.10", session=TUE_S),
    )

    result = attempt(order(OrderPurpose.EXIT, limit="100", session_date=TUE), quotes, state=held())

    assert _filled(result).net_debit == usd("-50.00")


# --- check 4: LIMIT ------------------------------------------------------------------------


def test_a_debit_above_the_limit_is_limit() -> None:
    quotes = (q(SHORT_ID, "1.90", "2.20"), q(LONG_ID, "1.00", "1.10"))

    assert nonfill(attempt(order(limit="-90"), quotes)) == (
        NonfillReason.LIMIT,
        None,
        usd("-80.00"),
    )


@pytest.mark.parametrize(("limit", "fills"), [("-89.99", True), ("-90.01", False)])
def test_the_limit_compares_the_whole_order_debit_exactly(limit: str, fills: bool) -> None:
    result = attempt(order(limit=limit), ENTRY_QUOTES)

    assert isinstance(result, Fill) is fills


def test_the_limit_is_checked_before_capacity() -> None:
    quotes = (q(SHORT_ID, "1.90", "2.20", bid_size=1), q(LONG_ID, "1.00", "1.10", ask_size=1))

    assert nonfill(attempt(order(limit="-90"), quotes))[0] is NonfillReason.LIMIT


def test_final_ignores_the_limit_but_not_capacity() -> None:
    wide = (
        q(SHORT_ID, "6.90", "7.20", session=TUE_S),
        q(LONG_ID, "0.10", "0.20", session=TUE_S),
    )
    thin = (
        q(SHORT_ID, "6.90", "7.20", session=TUE_S, ask_size=5),
        q(LONG_ID, "0.10", "0.20", session=TUE_S),
    )
    final = order(OrderPurpose.FINAL, limit=None, session_date=TUE)

    assert _filled(attempt(final, wide, state=held())).net_debit == usd("710.00")
    assert nonfill(attempt(final, thin, state=held())) == (
        NonfillReason.CAPACITY,
        None,
        usd("710.00"),
    )


# --- check 5: CAPACITY ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("bid_size", "packages", "fills"),
    [
        (10, 2, False),  # G12: floor(10 · 0.10) = 1 < 2, all or none
        (19, 2, False),  # floor(1.9) = 1
        (20, 2, True),  # floor(2.0) = 2, exactly
        (29, 2, True),  # floor(2.9) = 2
    ],
)
def test_capacity_is_the_floor_of_displayed_size_times_participation(
    bid_size: int, packages: int, fills: bool
) -> None:
    quotes = (q(SHORT_ID, "2.00", "2.20", bid_size=bid_size), q(LONG_ID, "1.00", "1.10"))
    limit = f"{-90 * packages}"

    result = attempt(order(packages=packages, limit=limit), quotes)

    if fills:
        assert _filled(result).legs[0].contracts == -packages
    else:
        assert nonfill(result) == (NonfillReason.CAPACITY, None, usd(limit))


def test_capacity_divides_each_side_by_its_ratio_and_takes_the_minimum() -> None:
    # min(50 / |-2|, 50 / 1) · 0.10 = 2.5 → 2 packages.
    legs = (leg(SHORT, -2), leg(LONG, 1))
    rich = funded("2000000.00")

    two = attempt(order(legs=legs, packages=2, limit="-580"), ENTRY_QUOTES, state=rich)
    three = attempt(order(legs=legs, packages=3, limit="-870"), ENTRY_QUOTES, state=rich)

    assert _filled(two).legs[0].contracts == -4
    assert nonfill(three)[0] is NonfillReason.CAPACITY


def test_capacity_uses_the_side_each_leg_trades() -> None:
    # The sell uses the bid size, the buy the ask size; the other sides do not bind.
    quotes = (
        q(SHORT_ID, "2.00", "2.20", bid_size=50, ask_size=1),
        q(LONG_ID, "1.00", "1.10", bid_size=1, ask_size=50),
    )

    assert isinstance(attempt(order(packages=5, limit="-450"), quotes), Fill)


def test_capacity_is_checked_before_funding() -> None:
    quotes = (q(SHORT_ID, "2.00", "2.20", bid_size=10), q(LONG_ID, "1.00", "1.10"))

    result = attempt(order(packages=2, limit="-180"), quotes, state=funded("1.00"))

    assert nonfill(result)[0] is NonfillReason.CAPACITY


def test_capacity_book_is_reused_across_orders_on_one_observation() -> None:
    quotes = (q(SHORT_ID, "2.00", "2.20", bid_size=20), q(LONG_ID, "1.00", "1.10", ask_size=20))
    empty = CapacityBook.empty()

    first = _filled(attempt(order(packages=2, limit="-180"), quotes, capacity=empty))
    book = empty.consume(first)

    # try_fill never consumes; only the committed fill's consume does.
    assert empty.remaining(quotes[0], QuoteSide.BID) == 20
    assert book.remaining(quotes[0], QuoteSide.BID) == 18
    assert book.remaining(quotes[0], QuoteSide.ASK) == 50
    assert book.remaining(quotes[1], QuoteSide.ASK) == 18
    second = order(packages=2, limit="-180", campaign_id="c2.g1")
    assert nonfill(attempt(second, quotes, capacity=book)) == (
        NonfillReason.CAPACITY,
        None,
        usd("-180.00"),
    )  # floor(18 · 0.10) = 1 < 2
    third = order(packages=1, limit="-90", campaign_id="c2.g1")
    assert isinstance(attempt(third, quotes, capacity=book), Fill)


def test_consumption_is_per_observation() -> None:
    f1 = (q(SHORT_ID, "2.00", "2.20", bid_size=20), q(LONG_ID, "1.00", "1.10", ask_size=20))
    f2 = (
        q(SHORT_ID, "2.00", "2.20", slot="F2", bid_size=20),
        q(LONG_ID, "1.00", "1.10", slot="F2", ask_size=20),
    )
    book = CapacityBook.empty().consume(_filled(attempt(order(packages=2, limit="-180"), f1)))

    assert book.remaining(f2[0], QuoteSide.BID) == 20
    later = attempt(order(packages=2, limit="-180"), (*f1, *f2), capacity=book, slot="F2")
    assert isinstance(later, Fill)


# --- CapacityBook --------------------------------------------------------------------------


def test_an_empty_book_leaves_the_displayed_sizes() -> None:
    observation = q(SHORT_ID, "2.00", "2.20", bid_size=7, ask_size=9)

    assert dict(CapacityBook.empty().consumed) == {}
    assert CapacityBook.empty().remaining(observation, QuoteSide.BID) == 7
    assert CapacityBook.empty().remaining(observation, QuoteSide.ASK) == 9


def test_consume_accumulates_and_returns_a_new_book() -> None:
    fill = _filled(attempt(order(), ENTRY_QUOTES))
    once = CapacityBook.empty().consume(fill)
    twice = once.consume(fill)

    assert dict(once.consumed) == {
        (fill.quote_ids[0], QuoteSide.BID): 1,
        (fill.quote_ids[1], QuoteSide.ASK): 1,
    }
    assert twice.remaining(ENTRY_QUOTES[0], QuoteSide.BID) == 48
    assert once.remaining(ENTRY_QUOTES[0], QuoteSide.BID) == 49


def test_the_book_is_read_only() -> None:
    book = CapacityBook({("q:x:2024-03-04:F1", QuoteSide.BID): 1})

    with pytest.raises(TypeError):
        book.consumed[("q:x:2024-03-04:F1", QuoteSide.BID)] = 2  # type: ignore[index]


def test_a_book_consumed_beyond_the_displayed_size_fails_loud() -> None:
    observation = q(SHORT_ID, "2.00", "2.20", bid_size=5)
    book = CapacityBook({(observation.observation_id, QuoteSide.BID): 6})

    with pytest.raises(ValueError, match="displayed"):
        book.remaining(observation, QuoteSide.BID)


@pytest.mark.parametrize("count", [0, -1])
def test_a_book_holds_positive_counts(count: int) -> None:
    with pytest.raises(ValueError, match="consumed"):
        CapacityBook({("q:x:2024-03-04:F1", QuoteSide.BID): count})


def test_a_book_is_a_mapping() -> None:
    with pytest.raises(TypeError, match="consumed"):
        CapacityBook([])  # type: ignore[arg-type]


# --- Fill and Nonfill records --------------------------------------------------------------


def test_a_fill_trades_every_order_leg_times_its_packages_at_one_quote_each() -> None:
    # CapacityBook.consume charges |contracts| to each quote id, so these must match the order.
    fill = _filled(attempt(order(), ENTRY_QUOTES))

    with pytest.raises(ValueError, match="legs"):
        replace(fill, legs=fill.legs[:1])
    with pytest.raises(ValueError, match="legs"):
        replace(fill, legs=(LegFill(SHORT, -2, price("2.00")), fill.legs[1]))
    with pytest.raises(ValueError, match="quote_ids"):
        replace(fill, quote_ids=fill.quote_ids[:1])


def test_a_nonfill_names_a_leg_of_its_order_and_says_why() -> None:
    with pytest.raises(ValueError, match="message"):
        Nonfill(order(), NonfillReason.LIMIT, None, usd("-80"), "")
    with pytest.raises(ValueError, match="contract_id"):
        Nonfill(order(), NonfillReason.NO_SIDE, "SPXW:2024-03-06:C:5100", None, "no ask")
    with pytest.raises(TypeError, match="reason"):
        Nonfill(order(), "LIMIT", None, None, "above the limit")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="net_debit"):
        Nonfill(order(), NonfillReason.LIMIT, None, "-80", "above the limit")  # type: ignore[arg-type]


# --- check 6: INSUFFICIENT_CAPITAL ---------------------------------------------------------


@pytest.mark.parametrize(("cash", "fills"), [("504.00", True), ("503.99", False)])
def test_an_opening_fill_must_leave_non_negative_headroom(cash: str, fills: bool) -> None:
    # 504 - 2 fees payable - (500 width + 2 exit-fee provision) = 0; the credit never counts.
    result = attempt(order(), ENTRY_QUOTES, state=funded(cash))

    if fills:
        assert funding_headroom(_filled(result).post_state, SCHEDULE) == usd("0.00")
    else:
        assert nonfill(result) == (NonfillReason.INSUFFICIENT_CAPITAL, None, usd("-90.00"))


def test_a_refused_close_keeps_the_position_and_leaves_the_state_unchanged() -> None:
    # G06-like: cash 600 + 90 - 2 = 688 after T+1, headroom 688 - 502 reserve = 186. Closing at
    # the naturals costs 710 + 2 fees = 712 > 186 + the released 502; post-fill headroom -24.
    state = held("600.00")
    quotes = (
        q(SHORT_ID, "6.90", "7.20", session=TUE_S),
        q(LONG_ID, "0.10", "0.20", session=TUE_S),
    )

    result = attempt(order(OrderPurpose.EXIT, limit="710", session_date=TUE), quotes, state=state)

    assert nonfill(result) == (NonfillReason.INSUFFICIENT_CAPITAL, None, usd("710.00"))
    assert state == held("600.00")
    assert sorted(state.lots) == sorted([SHORT_ID, LONG_ID])


def test_a_close_is_funded_by_its_released_reserve_with_zero_slack() -> None:
    # 688 - (686 + 2) - 0 = 0.
    quotes = (
        q(SHORT_ID, "6.90", "6.96", session=TUE_S),
        q(LONG_ID, "0.10", "0.20", session=TUE_S),
    )

    result = attempt(
        order(OrderPurpose.EXIT, limit="686", session_date=TUE), quotes, state=held("600.00")
    )

    fill = _filled(result)
    assert fill.net_debit == usd("686.00")
    assert funding_headroom(fill.post_state, SCHEDULE) == usd("0.00")
    assert dict(fill.post_state.lots) == {}


# --- engine invariants ---------------------------------------------------------------------


def test_a_ledger_rejection_of_the_engines_own_entry_is_an_invariant_error() -> None:
    # The state's last entry is at the session's cutoff, so an F1 trade would go back in time.
    late = funded(at_ns=MON_S.cutoff_ns)

    with pytest.raises(SimulationInvariantError, match="ledger"):
        attempt(order(), ENTRY_QUOTES, state=late)


def test_a_fill_that_would_raise_mid_nlv_is_an_invariant_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A defect trading each leg on the wrong side (buy at the bid, sell at the ask) books a
    # fill worth more than it costs at the fill observations' mids (R1Campaign.FillNeverRaisesNLV).
    def wrong_side(quote: Quote, ratio: int) -> Price:
        return quote.bid if ratio > 0 else quote.ask

    monkeypatch.setattr(fills, "natural_price", wrong_side)

    with pytest.raises(SimulationInvariantError, match="NLV"):
        attempt(order(), ENTRY_QUOTES)


# --- guards --------------------------------------------------------------------------------


def test_the_view_and_the_context_are_at_the_same_fill_instant() -> None:
    view = AsOfView(market(*ENTRY_QUOTES), slot_ns(MON_S, "F2"))

    with pytest.raises(ValueError, match="at_ns"):
        try_fill(order(), view, CapacityBook.empty(), funded(), ctx(MON_S, "F1"))


def test_an_order_fills_only_in_its_own_session_after_submission() -> None:
    other_session = AsOfView(market(), slot_ns(WED_S, "F1"))
    wed = FillContext(
        event_id="2024-03-06:F1:4:1",
        at_ns=slot_ns(WED_S, "F1"),
        settles_on=date(2024, 3, 7),
        schedule=SCHEDULE,
        participation_fraction=Decimal("0.10"),
        premium_direction=PremiumDirection.CREDIT,
    )
    at_submission = AsOfView(market(*ENTRY_QUOTES), slot_ns(MON_S, "DEC"))

    with pytest.raises(ValueError, match="session"):
        try_fill(order(), other_session, CapacityBook.empty(), funded(), wed)
    with pytest.raises(ValueError, match="after"):
        try_fill(order(), at_submission, CapacityBook.empty(), funded(), ctx(MON_S, "DEC"))


def test_fill_cash_settles_after_the_order_session() -> None:
    same_day = FillContext(
        event_id="2024-03-04:F1:4:1",
        at_ns=slot_ns(MON_S, "F1"),
        settles_on=MON,
        schedule=SCHEDULE,
        participation_fraction=Decimal("0.10"),
        premium_direction=PremiumDirection.CREDIT,
    )
    view = AsOfView(market(*ENTRY_QUOTES), slot_ns(MON_S, "F1"))

    with pytest.raises(ValueError, match="settles_on"):
        try_fill(order(), view, CapacityBook.empty(), funded(), same_day)


@pytest.mark.parametrize(
    ("participation", "error"),
    [
        (Decimal(0), ValueError),
        (Decimal("1.01"), ValueError),
        (Decimal("NaN"), ValueError),
        (0.1, TypeError),
    ],
)
def test_participation_is_a_decimal_fraction_in_0_1(participation: object, error: type) -> None:
    with pytest.raises(error, match="participation_fraction"):
        FillContext(
            event_id="2024-03-04:F1:4:1",
            at_ns=slot_ns(MON_S, "F1"),
            settles_on=TUE,
            schedule=SCHEDULE,
            participation_fraction=participation,  # type: ignore[arg-type]
            premium_direction=PremiumDirection.CREDIT,
        )


def test_the_settlement_date_is_a_date() -> None:
    with pytest.raises(TypeError, match="settles_on"):
        FillContext(
            event_id="2024-03-04:F1:4:1",
            at_ns=slot_ns(MON_S, "F1"),
            settles_on="2024-03-05",  # type: ignore[arg-type]
            schedule=SCHEDULE,
            participation_fraction=Decimal("0.10"),
            premium_direction=PremiumDirection.CREDIT,
        )


def test_full_participation_fills_the_whole_displayed_size() -> None:
    quotes = (q(SHORT_ID, "2.00", "2.20", bid_size=3), q(LONG_ID, "1.00", "1.10", ask_size=3))

    result = attempt(order(packages=3, limit="-270"), quotes, participation="1")

    assert _filled(result).legs[1].contracts == 3
