"""Package selection and sizing at a decision (ADR 0002 §8, §17 items 37, 42, 45, 46; design §9.2).

The default market and strategy are ``selection_builders``'s, G01's: spot 5000.00 at DEC, DF 1,
one expiry 2024-03-15 (dte 11 on 2024-03-04) and a put credit vertical selling moneyness
0.98 +- 0.0005 (4900) and buying 5 below (4895), fixed 1 contract, $1.00 per contract side.
"""

import hashlib
from dataclasses import replace
from datetime import date
from decimal import Context, Decimal, localcontext
from typing import Any, get_args

import pytest
from data_builders import local_ns
from selection_builders import (
    EXPIRY,
    MON,
    SCHEDULE,
    TUE,
    call_id,
    dataset,
    decision,
    deposited,
    g01_pins,
    leg,
    pin,
    put_id,
    put_leg,
    quote_id,
    refrozen,
    same_as,
    session,
    strategy,
    strategy_with,
    target,
    usd,
    with_features,
)

from options_backtest.data.asof import AsOfView
from options_backtest.data.manifest import FrozenDataset, table_digest
from options_backtest.data.records import UnderlyingField
from options_backtest.engine import selector
from options_backtest.engine.clock import QUOTE_MAX_AGE_NS
from options_backtest.engine.fees import trade_fees
from options_backtest.engine.journal import apply_entry
from options_backtest.engine.orders import Order, OrderPurpose
from options_backtest.engine.selector import (
    MAX_PACKAGE_EVALUATIONS,
    SelectionOutcome,
    _Shape,
    _strike_order_ok,
    evaluate_conditions,
    select,
)
from options_backtest.engine.trades import book_option_trade
from options_backtest.errors import SimulationInvariantError
from options_backtest.models.artifacts import (
    CandidateRejection,
    ConditionCheck,
    DecisionReason,
    ExpiryCandidate,
    ExpirySkip,
    LegCandidate,
    PackageCandidate,
    PackageScore,
    PackageVerdict,
)
from options_backtest.models.ledger import LedgerState, LegFill
from options_backtest.models.strategy import Structure
from options_backtest.models.strategy_checks import ValidatedStrategy
from options_backtest.money import Price
from options_backtest.pricing.european import spot_delta
from options_backtest.pricing.iv import implied_vol
from options_backtest.reference.calendars import Slot, slot_times
from options_backtest.synthetic.market import (
    ActivityPin,
    QuoteDrop,
    QuoteStale,
    RateDrop,
    TermsRevision,
    UnderlyingDrop,
)

ENTRY, ROLL_OPEN = OrderPurpose.ENTRY, OrderPurpose.ROLL_OPEN
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
WIDE = Context(prec=1100)
"""Enough digits to subtract any float's exact expansion from a short decimal exactly."""
RETURN_20_NAME = "underlying.return_20s"
RETURN_20 = f"SPX:{RETURN_20_NAME}"
MAR_13, MAR_18, MAR_12 = date(2024, 3, 13), date(2024, 3, 18), date(2024, 3, 12)
G01_IDS = (put_id(4900), put_id(4895))
NS_PER_YEAR = 365 * 86_400 * 10**9


def _moneyness_error(strike: int, goal: str, spot: float = 5000.0) -> Decimal:
    with localcontext(WIDE):
        return abs(Decimal(strike / spot) - Decimal(goal))


def _select(
    validated: ValidatedStrategy, frozen: FrozenDataset, day: date = MON, **ctx: Any
) -> SelectionOutcome:
    view, context = decision(frozen, day, **ctx)
    return select(validated, view, context)


def _only_expiry(outcome: SelectionOutcome) -> ExpiryCandidate:
    (expiry,) = outcome.decision.expiries
    return expiry


def _candidate(expiry: ExpiryCandidate, leg_id: str, contract_id: str) -> LegCandidate:
    (found,) = (c for c in expiry.legs if (c.leg_id, c.contract_id) == (leg_id, contract_id))
    return found


def _eligible(expiry: ExpiryCandidate, leg_id: str) -> list[str]:
    return [c.contract_id for c in expiry.legs if c.leg_id == leg_id and c.rejection is None]


def _verdicts(outcome: SelectionOutcome) -> list[tuple[tuple[str, ...], PackageVerdict]]:
    return [(package.contract_ids, package.verdict) for package in outcome.decision.packages]


def _conditions(*rows: tuple[str, str, float]) -> ValidatedStrategy:
    conditions = [{"feature": name, "operator": op, "value": value} for name, op, value in rows]
    return strategy(entry={"all_conditions": conditions})


def _vertical(tolerance: float = 0.0005, **short: Any) -> list[dict[str, Any]]:
    """Return the default legs with the short put's tolerance (and expiry) changed."""
    return [
        leg("short_put", "sell", "put", moneyness=0.98, tolerance=tolerance, **short),
        leg("long_put", "buy", "put", expiry=same_as("short_put"), offset=("short_put", "-5")),
    ]


# --- step 1: conditions on the prior session ------------------------------------------------------


def test_no_conditions_is_true() -> None:
    view, _ = decision(dataset())

    assert evaluate_conditions(strategy(), view, None) == ()


@pytest.mark.parametrize(
    ("operator", "value", "holds"),
    [
        ("gt", 0.01, True),
        ("gt", 0.02, False),
        ("gte", 0.02, True),
        ("lt", 0.02, False),
        ("lte", 0.02, True),
        ("lt", 0.03, True),
    ],
)
def test_a_condition_compares_the_prior_session_value_exactly(
    operator: str, value: float, holds: bool
) -> None:
    frozen = with_features(dataset(), {(RETURN_20, MON): Decimal("0.02")})
    view, _ = decision(frozen, TUE)

    checks = evaluate_conditions(_conditions((RETURN_20_NAME, operator, value)), view, MON)

    threshold = Decimal(str(value))
    assert checks == (ConditionCheck(RETURN_20, operator, threshold, MON, Decimal("0.02"), holds),)


def test_conditions_are_judged_in_spec_order() -> None:
    frozen = with_features(dataset(), {(RETURN_20, MON): Decimal("0.02")})
    view, _ = decision(frozen, TUE)
    validated = _conditions((RETURN_20_NAME, "gt", 0.03), (RETURN_20_NAME, "gt", 0.01))

    checks = evaluate_conditions(validated, view, MON)

    assert [(c.threshold, c.holds) for c in checks] == [
        (Decimal("0.03"), False),
        (Decimal("0.01"), True),
    ]


def test_a_feature_without_a_value_is_unknown_and_false() -> None:
    view, _ = decision(dataset(), TUE)  # generated five sessions: return_20s is in warmup

    (check,) = evaluate_conditions(_conditions((RETURN_20_NAME, "lt", 1)), view, MON)

    assert (check.session_date, check.value, check.holds) == (MON, None, False)


def test_without_a_prior_session_every_condition_is_false() -> None:
    view, _ = decision(dataset(), MON)

    (check,) = evaluate_conditions(_conditions((RETURN_20_NAME, "lt", 1)), view, None)

    assert (check.session_date, check.value, check.holds) == (None, None, False)


def test_the_same_session_value_is_never_read() -> None:
    frozen = with_features(dataset(), {(RETURN_20, TUE): Decimal("0.02")})
    view, _ = decision(frozen, TUE)

    (check,) = evaluate_conditions(_conditions((RETURN_20_NAME, "gt", 0.01)), view, MON)

    assert (check.value, check.holds) == (None, False)


def test_a_prior_value_published_after_the_decision_is_invisible() -> None:
    dec = slot_times(session(dataset(), TUE)).dec
    frozen = with_features(
        dataset(),
        {(RETURN_20, MON): Decimal("0.02")},
        available_at={(RETURN_20, MON): dec + 1},
    )
    view, _ = decision(frozen, TUE)

    (check,) = evaluate_conditions(_conditions((RETURN_20_NAME, "gt", 0.01)), view, MON)

    assert (check.value, check.holds) == (None, False)


@pytest.mark.parametrize("purpose", [ENTRY, ROLL_OPEN])
def test_false_conditions_stop_an_entry_or_a_replacement_before_the_search(
    purpose: OrderPurpose,
) -> None:
    validated = _conditions((RETURN_20_NAME, "gt", 0))

    outcome = _select(validated, dataset(*g01_pins(TUE)), TUE, purpose=purpose)

    record = outcome.decision
    assert outcome.order is None
    assert (record.conditions_hold, record.reason) == (False, DecisionReason.CONDITIONS_FALSE)
    assert (record.expiries, record.packages, record.chosen, record.evaluations) == (
        (),
        (),
        None,
        0,
    )
    assert record.candidate_set_digest == EMPTY_SHA256


def test_a_replacement_opens_when_its_conditions_hold() -> None:
    frozen = with_features(dataset(*g01_pins(TUE)), {(RETURN_20, MON): Decimal("0.02")})
    validated = _conditions((RETURN_20_NAME, "gt", 0.01))

    outcome = _select(validated, frozen, TUE, purpose=ROLL_OPEN, campaign_id="c1.g2")

    assert outcome.order is not None
    assert (outcome.order.order_id, outcome.order.purpose) == ("o:2024-03-05:roll_open", ROLL_OPEN)
    assert outcome.order.campaign_id == "c1.g2"
    assert outcome.decision.conditions_hold


# --- G01: the whole decision ----------------------------------------------------------------------


def test_g01_selects_sizes_and_prices_the_4900_4895_put_credit_vertical() -> None:
    frozen = dataset(*g01_pins())
    view, ctx = decision(frozen)

    outcome = select(strategy(), view, ctx)

    assert outcome.order == Order(
        order_id="o:2024-03-04:entry",
        campaign_id="c1.g1",
        purpose=ENTRY,
        legs=(put_leg("4900", -1), put_leg("4895", 1)),
        packages=1,
        limit_usd=usd("-90.00"),
        trigger=None,
        submitted_at_ns=view.at_ns,
        session_date=MON,
    )
    record = outcome.decision
    assert (record.decision_id, record.session_date, record.at_ns) == (
        ctx.decision_id,
        MON,
        view.at_ns,
    )
    assert (record.purpose, record.campaign_id, record.conditions, record.conditions_hold) == (
        ENTRY,
        "c1.g1",
        (),
        True,
    )
    assert (record.chosen, record.cap_bound, record.evaluations, record.budget_exceeded) == (
        0,
        False,
        1,
        False,
    )
    assert record.reason is None
    assert record.candidate_set_digest == table_digest((*record.expiries, *record.packages))


def test_g01_records_its_expiry_inputs_and_candidates() -> None:
    expiry = _only_expiry(_select(strategy(), dataset(*g01_pins())))

    assert (expiry.root, expiry.expiry, expiry.dte, expiry.expiry_error) == ("SPXW", EXPIRY, 11, 0)
    assert (expiry.spot, expiry.discount_factor, expiry.skip_reason) == (
        Price(Decimal("5000.00")),
        Decimal(1),
        None,
    )
    assert expiry.forward is not None
    assert abs(expiry.forward - 5000) <= Decimal("0.05")  # parity forward within one tick
    short_error = _moneyness_error(4900, "0.98")
    assert _candidate(expiry, "short_put", put_id(4900)) == LegCandidate(
        "short_put", put_id(4900), quote_id(put_id(4900)), None, None, short_error, usd("20"), None
    )
    assert _candidate(expiry, "long_put", put_id(4895)) == LegCandidate(
        "long_put", put_id(4895), quote_id(put_id(4895)), None, None, Decimal(0), usd("10"), None
    )
    assert [c.contract_id for c in expiry.legs if c.rejection is None] == list(G01_IDS)
    others = {c.rejection for c in expiry.legs if c.contract_id != put_id(4900)} - {None}
    assert others == {CandidateRejection.OUT_OF_TOLERANCE}
    assert len([c for c in expiry.legs if c.leg_id == "short_put"]) == 45  # every listed put


def test_g01_scores_and_chooses_its_one_package() -> None:
    record = _select(strategy(), dataset(*g01_pins())).decision

    score = PackageScore(0, _moneyness_error(4900, "0.98"), usd("30"), G01_IDS)
    assert record.packages == (
        PackageCandidate("SPXW", EXPIRY, G01_IDS, usd("-90"), 1, score, PackageVerdict.CHOSEN),
    )


def test_selection_is_deterministic() -> None:
    frozen = dataset(*g01_pins())

    first, second = _select(strategy(), frozen), _select(strategy(), frozen)

    assert first == second


def test_the_order_lists_legs_in_leg_order_not_document_order() -> None:
    reversed_legs = strategy(
        legs=[
            leg("long_put", "buy", "put", expiry=same_as("short_put"), offset=("short_put", "-5")),
            leg("short_put", "sell", "put", moneyness=0.98),
        ]
    )

    outcome = _select(reversed_legs, dataset(*g01_pins()))

    assert outcome.order is not None
    assert outcome.order.legs == (put_leg("4900", -1), put_leg("4895", 1))
    assert outcome.decision.packages[0].contract_ids == G01_IDS


# --- step 2: expiries -----------------------------------------------------------------------------


def _expiry_rows(outcome: SelectionOutcome) -> list[tuple[date, int, ExpirySkip | None]]:
    return [(e.expiry, e.dte, e.skip_reason) for e in outcome.decision.expiries]


def test_expiries_outside_the_dte_window_are_not_candidates() -> None:
    narrow = strategy(legs=_vertical(expiry=target(10, 7, 10)))

    outcome = _select(narrow, dataset(*g01_pins()))

    assert outcome.decision.expiries == ()
    assert (outcome.order, outcome.decision.reason) == (None, DecisionReason.NO_PACKAGE)


SEQUENTIAL = {"mode": "sequential", "trigger_dte": 8, "max_rolls": 1, "max_campaign_sessions": 20}


@pytest.mark.parametrize(
    ("patch", "dtes"),
    [({}, [8, 11]), ({"exits": {"exit_dte": 8}}, [11]), ({"roll": SEQUENTIAL}, [11])],
)
def test_an_expiry_must_lie_beyond_exit_dte_and_the_roll_trigger(
    patch: dict[str, Any], dtes: list[int]
) -> None:
    validated = strategy(legs=_vertical(expiry=target(9, 8, 11)), **patch)

    outcome = _select(validated, dataset(weekly_dtes=(8, 11)))

    assert [e.dte for e in outcome.decision.expiries] == dtes


@pytest.mark.parametrize(("offset_ns", "listed"), [(0, True), (-1, False)])
def test_an_expiry_must_stay_tradable_through_f3(offset_ns: int, listed: bool) -> None:
    frozen = dataset(*g01_pins())
    f3 = slot_times(session(frozen, MON)).f3
    contracts = tuple(replace(c, last_tradable_at_ns=f3 + offset_ns) for c in frozen.contracts)

    outcome = _select(strategy(), refrozen(frozen, contracts=contracts))

    assert bool(outcome.decision.expiries) is listed


def test_expiries_are_searched_by_distance_then_time_and_pruned_after_a_better_score() -> None:
    legs = _vertical(expiry=target(11, 9, 14))

    outcome = _select(strategy(legs=legs), dataset(weekly_dtes=(9, 11, 14)))

    assert _expiry_rows(outcome) == [
        (EXPIRY, 11, None),
        (MAR_13, 9, ExpirySkip.PRUNED),
        (MAR_18, 14, ExpirySkip.PRUNED),
    ]
    pruned = outcome.decision.expiries[1]
    assert (pruned.spot, pruned.discount_factor, pruned.forward, pruned.legs) == (
        None,
        None,
        None,
        (),
    )
    assert {p.expiry for p in outcome.decision.packages} == {EXPIRY}


def test_an_expiry_without_an_eligible_package_does_not_prune_the_next() -> None:
    legs = _vertical(expiry=target(11, 9, 14))
    no_anchor = QuoteDrop(put_id(4900), MON, (Slot.DEC,))

    outcome = _select(strategy(legs=legs), dataset(no_anchor, weekly_dtes=(9, 11, 14)))

    assert _expiry_rows(outcome) == [
        (EXPIRY, 11, None),
        (MAR_13, 9, None),
        (MAR_18, 14, ExpirySkip.PRUNED),
    ]
    assert outcome.order is not None
    assert outcome.order.legs[0].terms.contract_id == put_id(4900, MAR_13)


# --- step 3: pricing inputs -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("override", "spot", "discount_factor"),
    [
        (UnderlyingDrop("SPX", UnderlyingField.INDEX_VALUE, MON, (Slot.DEC,)), None, Decimal(1)),
        (RateDrop(MON, None), Price(Decimal("5000.00")), None),
    ],
)
def test_an_expiry_without_spot_or_curve_is_skipped(
    override: Any, spot: Price | None, discount_factor: Decimal | None
) -> None:
    outcome = _select(strategy(), dataset(*g01_pins(), override))

    expiry = _only_expiry(outcome)
    assert (expiry.spot, expiry.discount_factor, expiry.forward) == (spot, discount_factor, None)
    assert (expiry.skip_reason, expiry.legs) == (ExpirySkip.PRICING_INPUT_UNAVAILABLE, ())
    assert (outcome.order, outcome.decision.reason) == (None, DecisionReason.NO_PACKAGE)


def test_an_expiry_without_a_parity_forward_is_skipped() -> None:
    dispersed = (
        pin(call_id(4990), "200.00", "201.00"),
        pin(call_id(4995), "200.00", "201.00"),
    )

    expiry = _only_expiry(_select(strategy(), dataset(*g01_pins(), *dispersed)))

    assert (expiry.spot, expiry.discount_factor, expiry.forward) == (
        Price(Decimal("5000.00")),
        Decimal(1),
        None,
    )
    assert expiry.skip_reason is ExpirySkip.PRICING_INPUT_UNAVAILABLE


# --- step 4: leg candidates -----------------------------------------------------------------------


def test_the_moneyness_error_is_the_float_ratio_lifted_to_decimal() -> None:
    # Both neighbours are exactly 0.001 away; the float lift puts 4905 inside and 4895 outside.
    expiry = _only_expiry(_select(strategy(legs=_vertical(0.001)), dataset()))

    below, above = (
        _candidate(expiry, "short_put", put_id(4895)),
        _candidate(expiry, "short_put", put_id(4905)),
    )
    assert (below.error, below.rejection) == (
        _moneyness_error(4895, "0.98"),
        CandidateRejection.OUT_OF_TOLERANCE,
    )
    assert (above.error, above.rejection) == (_moneyness_error(4905, "0.98"), None)
    assert _moneyness_error(4900, "0.98") != 0


def test_candidates_sort_by_error_then_spread_then_strike() -> None:
    expiry = _only_expiry(_select(strategy(legs=_vertical(0.0011)), dataset()))

    # short: errors ~0 < 0.000999... (4905) < 0.001000... (4895)
    assert _eligible(expiry, "short_put") == [put_id(4900), put_id(4905), put_id(4895)]
    # long (error 0 each): spread $90 (4890) < $100 (4895) = $100 (4900), then by strike
    assert _eligible(expiry, "long_put") == [put_id(4890), put_id(4895), put_id(4900)]


@pytest.mark.parametrize(
    ("bid", "ask", "rejection"),
    [
        ("1.00", "1.60", CandidateRejection.SPREAD),  # 0.60 > 0.50 and > 0.25 x 1.30
        ("2.05", "2.65", CandidateRejection.SPREAD),  # 0.60 > 0.25 x 2.35 = 0.5875
        ("2.10", "2.70", None),  # 0.60 = 0.25 x 2.40: the relative gate passes at equality
        ("2.20", "2.80", None),  # 0.60 <= 0.25 x 2.50: only the absolute gate fails
        ("0.10", "0.60", None),  # 0.50 = 0.50: only the relative gate fails
    ],
)
def test_a_spread_is_rejected_only_when_both_gates_fail(
    bid: str, ask: str, rejection: CandidateRejection | None
) -> None:
    frozen = dataset(pin(put_id(4900), bid, ask), pin(put_id(4895), "0.05", "0.10"))

    expiry = _only_expiry(_select(strategy(), frozen))

    assert _candidate(expiry, "short_put", put_id(4900)).rejection is rejection


@pytest.mark.parametrize(
    ("override", "rejection"),
    [
        (QuoteDrop(put_id(4900), MON, (Slot.DEC,)), CandidateRejection.QUOTE_UNAVAILABLE),
        (QuoteStale(put_id(4900), MON, (Slot.DEC,), 121), CandidateRejection.QUOTE_UNAVAILABLE),
        (QuoteStale(put_id(4900), MON, (Slot.DEC,), 120), None),
        (pin(put_id(4900), "0", "2.20"), CandidateRejection.QUOTE_STATUS),  # NO_BID: no sell
        (pin(put_id(4900), "2.20", "2.00"), CandidateRejection.QUOTE_STATUS),  # CROSSED
        (pin(put_id(4900), "0", "0"), CandidateRejection.QUOTE_STATUS),  # ZERO_ASK
        (pin(put_id(4900), "2.10", "2.10"), None),  # LOCKED sells at its bid
    ],
)
def test_the_sold_leg_needs_a_fresh_quote_its_side_can_use(
    override: Any, rejection: CandidateRejection | None
) -> None:
    frozen = dataset(*g01_pins(), override)

    found = _candidate(_only_expiry(_select(strategy(), frozen)), "short_put", put_id(4900))

    assert found.rejection is rejection
    if rejection is CandidateRejection.QUOTE_UNAVAILABLE:
        assert (found.quote_id, found.spread_usd, found.error) == (None, None, None)


@pytest.mark.parametrize(
    ("require", "rejection"), [(True, CandidateRejection.NO_BID_FOR_ENTRY), (False, None)]
)
def test_a_bought_leg_without_a_bid_is_rejected_only_when_entry_requires_one(
    require: bool, rejection: CandidateRejection | None
) -> None:
    validated = strategy(liquidity={"require_positive_bid_for_entry": require})
    # ask 0.40: the spread passes its absolute gate, so only the missing bid can reject it
    frozen = dataset(pin(put_id(4900), "2.00", "2.20"), pin(put_id(4895), "0", "0.40"))

    expiry = _only_expiry(_select(validated, frozen))

    assert _candidate(expiry, "long_put", put_id(4895)).rejection is rejection


@pytest.mark.parametrize(("volume", "eligible"), [(None, False), (99, False), (100, True)])
def test_a_positive_volume_minimum_needs_that_cumulative_volume(
    volume: int | None, eligible: bool
) -> None:
    validated = strategy(liquidity={"min_cumulative_volume": 100})
    pins: tuple[Any, ...] = ()
    if volume is not None:
        pins = tuple(ActivityPin(c, MON, Slot.DEC, volume) for c in G01_IDS)

    expiry = _only_expiry(_select(validated, dataset(*g01_pins(), *pins)))

    rejection = None if eligible else CandidateRejection.VOLUME
    assert _candidate(expiry, "short_put", put_id(4900)).rejection is rejection


def test_an_offset_strike_that_is_not_listed_is_never_rounded() -> None:
    legs = [
        leg("short_put", "sell", "put", moneyness=0.98),
        leg("long_put", "buy", "put", expiry=same_as("short_put"), offset=("short_put", "-7")),
    ]

    outcome = _select(strategy(legs=legs), dataset(*g01_pins()))

    missing = _candidate(_only_expiry(outcome), "long_put", put_id(4893))
    assert missing == LegCandidate(
        "long_put", put_id(4893), None, None, None, None, None, CandidateRejection.NO_OFFSET_STRIKE
    )
    assert (outcome.order, outcome.decision.reason) == (None, DecisionReason.NO_PACKAGE)


def test_an_offset_leg_passes_the_same_quote_filters() -> None:
    frozen = dataset(*g01_pins(), QuoteDrop(put_id(4895), MON, (Slot.DEC,)))

    outcome = _select(strategy(), frozen)

    found = _candidate(_only_expiry(outcome), "long_put", put_id(4895))
    assert found.rejection is CandidateRejection.QUOTE_UNAVAILABLE
    assert (outcome.decision.evaluations, outcome.decision.reason) == (0, DecisionReason.NO_PACKAGE)


def _single_put(delta: float, tolerance: float) -> ValidatedStrategy:
    return strategy(
        structure="single_long",
        legs=[leg("long_put", "buy", "put", delta=(delta, tolerance))],
    )


def test_a_delta_leg_prices_each_candidate_from_the_decision_inputs() -> None:
    view, ctx = decision(dataset(), cash="100000.00")  # a long put costs ~$3,000

    outcome = select(_single_put(-0.30, 0.05), view, ctx)

    expiry = _only_expiry(outcome)
    assert expiry.forward is not None and expiry.discount_factor is not None
    forward, df = float(expiry.forward), float(expiry.discount_factor)
    t = (local_ns(EXPIRY, 16) - view.at_ns) / NS_PER_YEAR
    judged = [c for c in expiry.legs if c.error is not None]
    assert judged
    for found in judged:
        observation = view.quote(found.contract_id, max_age_ns=QUOTE_MAX_AGE_NS)
        assert observation is not None
        mid = float(observation.bid + observation.ask) / 2
        strike = float(found.contract_id.rsplit(":", 1)[1])
        vol = implied_vol(mid, forward, strike, t, df, "put").value
        assert vol is not None
        delta = spot_delta(forward, 5000.0, strike, t, df, vol, "put")
        with localcontext(WIDE):
            error = abs(Decimal(delta) - Decimal("-0.3"))
        assert (found.implied_vol, found.spot_delta, found.error) == (
            Decimal(vol),
            Decimal(delta),
            error,
        )
    best = min((c for c in judged if c.rejection is None), key=lambda c: c.error or 0)
    assert outcome.order is not None
    assert outcome.order.legs[0].terms.contract_id == best.contract_id


def test_a_delta_leg_without_an_implied_volatility_is_rejected() -> None:
    below_intrinsic = pin(put_id(5110), "50.00", "51.00")  # mid 50.50 < 5110 - 5000

    expiry = _only_expiry(_select(_single_put(-0.95, 0.25), dataset(below_intrinsic)))

    found = _candidate(expiry, "long_put", put_id(5110))
    assert (found.rejection, found.implied_vol, found.spot_delta, found.error) == (
        CandidateRejection.PRICING_INPUT,
        None,
        None,
        None,
    )


# --- step 5: search -------------------------------------------------------------------------------


def test_packages_are_judged_duplicate_direction_size_then_scored() -> None:
    legs = [
        leg("short_put", "sell", "put", moneyness=0.98, tolerance=0.0011),
        leg(
            "long_put", "buy", "put", expiry=same_as("short_put"), moneyness=0.979, tolerance=0.0011
        ),
    ]

    outcome = _select(strategy(legs=legs), dataset())

    verdict = PackageVerdict
    p = put_id
    assert _verdicts(outcome) == [
        ((p(4900), p(4895)), verdict.CHOSEN),  # D -30, the smallest error sum
        ((p(4900), p(4900)), verdict.DUPLICATE_CONTRACT),
        ((p(4900), p(4890)), verdict.ELIGIBLE),  # D -160, risk 844
        ((p(4905), p(4895)), verdict.ELIGIBLE),
        ((p(4905), p(4900)), verdict.ELIGIBLE),
        ((p(4905), p(4890)), verdict.NO_SIZE),  # D -290: risk 1500 - 288 + 2 = 1214 > 1000
        ((p(4895), p(4895)), verdict.DUPLICATE_CONTRACT),
        ((p(4895), p(4900)), verdict.PREMIUM_DIRECTION),  # D +230 on a credit structure
        ((p(4895), p(4890)), verdict.ELIGIBLE),
    ]
    assert outcome.decision.evaluations == 9
    assert [pkg.packages for pkg in outcome.decision.packages] == [1, 0, 1, 1, 1, 0, 0, 0, 1]


def test_a_zero_net_premium_has_no_direction() -> None:
    frozen = dataset(pin(put_id(4900), "1.00", "1.20"), pin(put_id(4895), "0.90", "1.00"))

    outcome = _select(strategy(), frozen)

    (package,) = outcome.decision.packages
    assert (package.net_debit, package.packages, package.verdict) == (
        usd("0"),
        0,
        PackageVerdict.PREMIUM_DIRECTION,
    )
    assert (outcome.order, outcome.decision.reason) == (None, DecisionReason.NO_PACKAGE)


def test_a_strangle_needs_its_put_strike_below_its_call_strike() -> None:
    legs = [
        leg("long_put", "buy", "put", moneyness=1.0, tolerance=0.0011),
        leg(
            "long_call", "buy", "call", expiry=same_as("long_put"), moneyness=1.0, tolerance=0.0011
        ),
    ]

    outcome = _select(strategy(structure="long_strangle", legs=legs), dataset(), cash="1000000.00")

    wrong = {ids for ids, v in _verdicts(outcome) if v is PackageVerdict.STRIKE_ORDER}
    strikes = {(int(a.rsplit(":", 1)[1]), int(b.rsplit(":", 1)[1])) for a, b in wrong}
    assert strikes == {(k, c) for k in (4995, 5000, 5005) for c in (4995, 5000, 5005) if k >= c}
    assert outcome.order is not None


def test_a_condor_needs_ascending_strikes_after_distinct_contracts() -> None:
    legs = [
        leg("lp", "buy", "put", moneyness=0.98),
        leg("sp", "sell", "put", expiry=same_as("lp"), moneyness=0.98, tolerance=0.0011),
        leg("sc", "sell", "call", expiry=same_as("lp"), moneyness=1.02),
        leg("lc", "buy", "call", expiry=same_as("lp"), moneyness=1.02, tolerance=0.0011),
    ]

    outcome = _select(strategy(structure="iron_condor", legs=legs), dataset())

    verdicts = {(ids[1][-4:], ids[3][-4:]): v for ids, v in _verdicts(outcome)}
    assert verdicts == {
        ("4900", "5100"): PackageVerdict.DUPLICATE_CONTRACT,
        ("4900", "5105"): PackageVerdict.DUPLICATE_CONTRACT,
        ("4900", "5095"): PackageVerdict.DUPLICATE_CONTRACT,
        ("4905", "5100"): PackageVerdict.DUPLICATE_CONTRACT,
        ("4905", "5105"): PackageVerdict.CHOSEN,
        ("4905", "5095"): PackageVerdict.STRIKE_ORDER,
        ("4895", "5100"): PackageVerdict.DUPLICATE_CONTRACT,
        ("4895", "5105"): PackageVerdict.STRIKE_ORDER,
        ("4895", "5095"): PackageVerdict.STRIKE_ORDER,
    }


STRUCTURE_ROLES = {
    "single_long": (("buy", "call"),),
    "vertical": (("buy", "put"), ("sell", "put")),
    "iron_condor": (("buy", "put"), ("sell", "put"), ("sell", "call"), ("buy", "call")),
    "long_straddle": (("buy", "put"), ("buy", "call")),
    "long_strangle": (("buy", "put"), ("buy", "call")),
}


@pytest.mark.parametrize("structure", get_args(Structure))
def test_the_strike_order_check_knows_every_structure(structure: str) -> None:
    # No public path reaches an unknown structure (ValidatedStrategy re-runs the checks), so
    # the private check is driven directly: a new Structure literal must fail loud here.
    roles = STRUCTURE_ROLES[structure]
    strikes = tuple(Decimal(4900 + 5 * n) for n in range(len(roles)))
    assert isinstance(_strike_order_ok(_Shape(structure, roles, 1), strikes), bool)


def test_the_strike_order_check_refuses_a_structure_it_does_not_know() -> None:
    with pytest.raises(SimulationInvariantError, match="'butterfly'"):
        _strike_order_ok(_Shape("butterfly", (("buy", "put"),), 1), (Decimal(4900),))


def test_a_package_mixing_deliverables_is_not_an_r1_package() -> None:
    # P4895 delivers 50 index units from TUE; P4900 still 100 (design §9.1 item 1).
    revised = TermsRevision(put_id(4895), TUE, Decimal(50))

    outcome = _select(strategy(), dataset(*g01_pins(day=TUE), revised), TUE)

    assert _verdicts(outcome) == [(G01_IDS, PackageVerdict.DELIVERABLE_MISMATCH)]
    assert (outcome.order, outcome.decision.reason) == (None, DecisionReason.NO_PACKAGE)


def _traded_on_monday(*strikes: str) -> LedgerState:
    """Return a flat account that bought and sold one of each version-1 put on MON."""
    state = deposited()
    for event, ratio in (("MON:open", 1), ("MON:close", -1)):
        fills = tuple(LegFill(put_leg(k, ratio).terms, ratio, Price(Decimal(1))) for k in strikes)
        entry = book_option_trade(
            state,
            event_id=event,
            at_ns=state.last_at_ns,
            campaign_id="c1.g1",
            legs=fills,
            fees=trade_fees(SCHEDULE, fills),
            settles_on=TUE,
        )
        state = apply_entry(state, entry)
    return state


def test_a_contract_traded_before_its_revision_cannot_be_selected_again() -> None:
    # Both legs deliver 50 units from TUE; the ledger keys contracts by id and holds MON's terms.
    revisions = (TermsRevision(put_id(k), TUE, Decimal(50)) for k in (4900, 4895))
    frozen = dataset(*g01_pins(day=TUE), *revisions)

    with pytest.raises(SimulationInvariantError, match="SPXW:2024-03-15:P:4900"):
        _select(strategy(), frozen, TUE, state=_traded_on_monday("4900", "4895"))


def test_equal_packages_on_two_expiries_break_ties_by_spread_then_ids() -> None:
    legs = _vertical(expiry=target(10, 9, 11))
    both = (*g01_pins(expiry=MAR_13), *g01_pins())
    narrower = (pin(put_id(4900), "2.00", "2.10"), pin(put_id(4895), "1.00", "1.05"))

    tied = _select(strategy(legs=legs), dataset(*both, weekly_dtes=(9, 11)))
    cheaper = _select(strategy(legs=legs), dataset(*both, *narrower, weekly_dtes=(9, 11)))

    assert _expiry_rows(tied) == [(MAR_13, 9, None), (EXPIRY, 11, None)]  # same expiry_error 1
    assert _verdicts(tied) == [
        ((put_id(4900, MAR_13), put_id(4895, MAR_13)), PackageVerdict.CHOSEN),
        (G01_IDS, PackageVerdict.ELIGIBLE),
    ]
    assert _verdicts(cheaper)[1] == (G01_IDS, PackageVerdict.CHOSEN)  # Σspread $15 < $30
    assert cheaper.decision.packages[1].score.spread_sum == usd("15")


def test_the_ten_thousandth_evaluation_exceeds_the_selection_budget() -> None:
    condor = strategy(
        structure="iron_condor",
        legs=[
            leg("lp", "buy", "put", moneyness=0.991, tolerance=0.25),
            leg("sp", "sell", "put", expiry=same_as("lp"), moneyness=0.995, tolerance=0.25),
            leg("sc", "sell", "call", expiry=same_as("lp"), moneyness=1.005, tolerance=0.25),
            leg("lc", "buy", "call", expiry=same_as("lp"), moneyness=1.009, tolerance=0.25),
        ],
        execution={"participation_fraction": 0.01},  # capacity 0: every sizing fails fast
    )

    outcome = _select(condor, dataset(strikes_each_side=9))  # 19^4 packages

    record = outcome.decision
    assert MAX_PACKAGE_EVALUATIONS == 10_000
    assert (record.evaluations, len(record.packages)) == (10_000, 10_000)
    assert (record.budget_exceeded, record.reason, record.chosen) == (
        True,
        DecisionReason.SELECTION_BUDGET_EXCEEDED,
        None,
    )
    assert outcome.order is None


@pytest.mark.parametrize(("cap", "exceeded"), [(3, True), (4, False)])
def test_reaching_the_evaluation_cap_stops_without_an_order(
    monkeypatch: pytest.MonkeyPatch, cap: int, exceeded: bool
) -> None:
    monkeypatch.setattr(selector, "MAX_PACKAGE_EVALUATIONS", cap)

    outcome = _select(strategy(legs=_vertical(0.0011)), dataset())  # exactly 3 packages

    record = outcome.decision
    assert (record.evaluations, record.budget_exceeded) == (3, exceeded)
    assert (outcome.order is None) is exceeded
    assert all(p.verdict is not PackageVerdict.CHOSEN for p in record.packages) is exceeded
    expected = DecisionReason.SELECTION_BUDGET_EXCEEDED if exceeded else None
    assert record.reason is expected


# --- step 6: sizing -------------------------------------------------------------------------------


def _fixed(contracts: int, **patch: Any) -> ValidatedStrategy:
    return strategy(sizing={"contracts": contracts}, **patch)


def _risk_budget(max_contracts: int, **patch: Any) -> ValidatedStrategy:
    sizing = {"method": "risk_budget", "max_contracts": max_contracts}
    return strategy_with({"sizing": sizing}, **patch)


@pytest.mark.parametrize(("cash", "packages"), [("12420.00", 3), ("12419.99", 0)])
def test_fixed_contracts_pass_the_risk_test_at_exact_equality(cash: str, packages: int) -> None:
    # risk(3) = -(3 x -500 + 3 x 90 - 3 x 2) + 3 x 2 = 1242 <= 0.10 x mid NLV
    outcome = _select(_fixed(3), dataset(*g01_pins()), cash=cash)

    assert outcome.decision.packages[0].packages == packages
    assert (outcome.order is None) is (packages == 0)


@pytest.mark.parametrize(("cash", "fits"), [("504.00", True), ("503.99", False)])
def test_the_preview_headroom_must_stay_non_negative(cash: str, fits: bool) -> None:
    # headroom = cash - fees 2 - (width reserve 500 + fee provision 2); receivables never count
    whole = _fixed(1, account={"max_campaign_risk_fraction": 1, "max_total_risk_fraction": 1})

    outcome = _select(whole, dataset(*g01_pins()), cash=cash)

    assert (outcome.order is not None) is fits


@pytest.mark.parametrize(
    ("bid_size", "ask_size", "contracts", "fits"),
    [(20, 50, 2, True), (20, 50, 3, False), (50, 20, 3, True)],
)
def test_fixed_contracts_fit_the_decision_capacity_of_the_side_used(
    bid_size: int, ask_size: int, contracts: int, fits: bool
) -> None:
    short = pin(put_id(4900), "2.00", "2.20", bid_size=bid_size, ask_size=ask_size)
    frozen = dataset(short, pin(put_id(4895), "1.00", "1.10"))

    outcome = _select(_fixed(contracts), frozen, cash="100000.00")

    assert (outcome.order is not None) is fits


def test_risk_budget_counts_down_to_the_largest_size_that_fits() -> None:
    # risk(n) = 414 n <= 0.10 x 10000: n = 2
    outcome = _select(_risk_budget(10), dataset(*g01_pins()))

    assert outcome.order is not None
    assert (outcome.order.packages, outcome.order.limit_usd) == (2, usd("-180.00"))
    assert (outcome.decision.packages[0].packages, outcome.decision.cap_bound) == (2, False)


def test_risk_budget_is_bound_by_the_decision_capacity() -> None:
    thin = pin(put_id(4900), "2.00", "2.20", bid_size=10)  # floor(10 x 0.10) = 1

    outcome = _select(_risk_budget(10), dataset(thin, pin(put_id(4895), "1.00", "1.10")))

    assert outcome.order is not None
    assert outcome.order.packages == 1


@pytest.mark.parametrize(("max_contracts", "per_order", "packages"), [(3, 10, 3), (10, 4, 4)])
def test_risk_budget_reports_a_binding_cap(
    max_contracts: int, per_order: int, packages: int
) -> None:
    validated = _risk_budget(max_contracts, execution={"max_contracts_per_order": per_order})

    outcome = _select(validated, dataset(*g01_pins()), cash="100000.00")

    assert outcome.order is not None
    assert (outcome.order.packages, outcome.decision.cap_bound) == (packages, True)


def test_the_price_allowance_is_added_once_to_the_sized_debit() -> None:
    validated = _risk_budget(10, execution={"price_allowance_usd": "0.50"})

    outcome = _select(validated, dataset(*g01_pins()))

    assert outcome.order is not None
    assert (outcome.order.packages, outcome.order.limit_usd) == (2, usd("-179.50"))


def test_risk_budget_without_a_fitting_size_gives_no_package() -> None:
    outcome = _select(_risk_budget(10), dataset(*g01_pins()), cash="4000.00")  # 414 > 400

    assert outcome.decision.packages[0].verdict is PackageVerdict.NO_SIZE
    assert (outcome.order, outcome.decision.reason) == (None, DecisionReason.NO_PACKAGE)


# --- inputs ---------------------------------------------------------------------------------------


def test_select_refuses_a_view_that_is_not_the_decision_instant() -> None:
    frozen = dataset(*g01_pins())
    _, ctx = decision(frozen)
    at_f1 = AsOfView(frozen, slot_times(session(frozen, MON)).f1)

    with pytest.raises(ValueError, match="DEC"):
        select(strategy(), at_f1, ctx)


def test_select_refuses_a_closing_purpose() -> None:
    view, ctx = decision(dataset(*g01_pins()))

    with pytest.raises(ValueError, match="opening"):
        select(strategy(), view, replace(ctx, purpose=OrderPurpose.EXIT))


def test_a_selection_context_refuses_a_state_that_holds_options() -> None:
    frozen = dataset(*g01_pins())
    open_ns = session(frozen, MON).open_ns
    state = deposited(at_ns=open_ns)
    fills = (LegFill(put_leg("4900", -1).terms, -1, Price(Decimal("2.00"))),)
    trade = book_option_trade(
        state,
        event_id="held",
        at_ns=open_ns + 1,
        campaign_id="c1.g1",
        legs=fills,
        fees=trade_fees(SCHEDULE, fills),
        settles_on=TUE,
    )
    held = apply_entry(state, trade)

    with pytest.raises(ValueError, match="flat"):
        decision(frozen, state=held)


def test_select_refuses_a_context_of_another_session() -> None:
    frozen = dataset(*g01_pins())
    view, _ = decision(frozen, MON)
    _, tuesday = decision(frozen, TUE)

    with pytest.raises(ValueError, match="session"):
        select(strategy(), view, tuesday)


def test_a_selection_context_refuses_a_settlement_date_not_after_its_session() -> None:
    _, ctx = decision(dataset())

    with pytest.raises(ValueError, match="settles_on"):
        replace(ctx, settles_on=MON)
