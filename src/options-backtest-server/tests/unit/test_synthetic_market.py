"""The synthetic market generator: ``MarketSpec`` to ``FrozenDataset`` (ADR 0002 §5, §17 17-25, 48).

Expected values are derived independently: instants from New York wall-clock times, the index
path from its §17 item 18 recurrence, quote sides from ``Fraction`` floor/ceil of the Black-76
mid ± half spread (the mid itself from the pricing kernel).
"""

import hashlib
import math
import random
import re
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from synthetic_builders import (
    EXPIRY,
    FRI,
    MON,
    NY,
    THU,
    TUE,
    WED,
    features,
    index,
    local_ns,
    market,
    quote,
    quote_ids,
    session,
    slot_ns,
)

from options_backtest.data.asof import AsOfView
from options_backtest.data.manifest import canonical_json
from options_backtest.data.records import (
    CoverageState,
    FidelityClass,
    QuoteStatus,
    UnderlyingField,
)
from options_backtest.data.store import read_dataset, write_dataset
from options_backtest.models.market import OptionType
from options_backtest.money import Price
from options_backtest.pricing.european import black76
from options_backtest.pricing.features import ATM30_IV, FEATURE_VERSIONS, feature_id
from options_backtest.reference.calendars import Slot
from options_backtest.reference.rates import DiscountCurve, bill_df
from options_backtest.synthetic.market import (
    CALENDAR_VERSION,
    GENERATOR_VERSION,
    ActivityPin,
    CoverageStatus,
    EarlyClose,
    LateAvailability,
    QuoteDrop,
    QuotePin,
    QuoteStale,
    RateDrop,
    SettlementDrop,
    SettlementPin,
    TermsRevision,
    UnderlyingDrop,
    generate,
)

QUOTE_SLOTS = ("DEC", "F1", "F2", "F3", "CLOSE")
NS_PER_YEAR = 365 * 86_400 * 10**9
PUT_5000 = "SPXW:2024-03-15:P:5000"
CALL_5000 = "SPXW:2024-03-15:C:5000"
SAT = date(2024, 3, 9)


def expected_sides(mid: float, *, tick: str = "0.05") -> tuple[Decimal, Decimal]:
    """Return (bid, ask): ``mid ∓ max(0.05, 0.02·mid)`` rounded down/up to the tick, exactly."""
    half = max(0.05, 0.02 * mid)
    step = Fraction(tick)
    bid = max(math.floor(Fraction(mid - half) / step), 0) * step
    ask = math.ceil(Fraction(mid + half) / step) * step
    return Decimal(bid.numerator) / bid.denominator, Decimal(ask.numerator) / ask.denominator


# --- MarketSpec validation --------------------------------------------------------------------

BROKEN_FIELDS = [
    ({"first_session": date(2024, 3, 3)}, "first_session"),
    ({"holidays": (MON,)}, "first_session"),
    ({"last_session": SAT}, "last_session"),
    ({"last_session": date(2024, 3, 1)}, "last_session"),
    ({"holidays": (SAT,)}, "holidays"),
    ({"early_closes": (date(2024, 3, 11),)}, "early_closes"),
    ({"early_closes": (WED,), "holidays": (WED,)}, "early_closes"),
    ({"index_start": Decimal(0)}, "index_start"),
    ({"index_start": Decimal("5000.001")}, "index_start"),
    ({"daily_drift": Decimal("NaN")}, "daily_drift"),
    ({"daily_vol": Decimal("-0.01")}, "daily_vol"),
    ({"sigma": Decimal(0)}, "sigma"),
    ({"rates": ()}, "rates"),
    ({"rates": ((91, Decimal(0)), (28, Decimal(0)))}, "rates"),
    ({"rates": ((0, Decimal(0)),)}, "rates"),
    ({"roots": ()}, "roots"),
    ({"roots": ("SPXW", "SPXW")}, "roots"),
    ({"roots": ("SPX",)}, "SPX"),
    ({"weekly_dtes": (0,)}, "weekly_dtes"),
    ({"weekly_dtes": (11, 12)}, "weekly_dtes"),
    ({"weekly_dtes": (182,)}, "tenor"),
    ({"strike_step": Decimal(0)}, "strike_step"),
    ({"strikes_each_side": -1}, "strikes_each_side"),
    ({"strikes_each_side": 1000}, "strike"),
    ({"tick": Decimal(0)}, "tick"),
    ({"half_spread_abs": Decimal(0)}, "half_spread_abs"),
    ({"half_spread_rel": Decimal("-0.01")}, "half_spread_rel"),
    ({"bid_size": 0}, "bid_size"),
    ({"ask_size": 0}, "ask_size"),
    ({"premium_multiplier": Decimal(0)}, "premium_multiplier"),
    ({"deliverable_units": Decimal(0)}, "deliverable_units"),
]


@pytest.mark.parametrize(("changes", "field"), BROKEN_FIELDS)
def test_generate_refuses_a_broken_market_spec(changes: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError, match=field):
        generate(market(**changes))


MISTYPED_FIELDS = [
    {"seed": "1"},
    {"seed": True},
    {"first_session": datetime(2024, 3, 4, tzinfo=NY)},
    {"index_start": 5000},
    {"sigma": 0.18},
    {"bid_size": 50.0},
    {"holidays": [WED]},
]


@pytest.mark.parametrize("changes", MISTYPED_FIELDS)
def test_generate_refuses_a_mistyped_market_spec(changes: dict[str, Any]) -> None:
    with pytest.raises(TypeError):
        generate(market(**changes))


def test_an_index_path_that_decays_to_zero_cents_is_refused() -> None:
    with pytest.raises(ValueError, match="index path"):
        generate(market(index_start=Decimal("0.01"), daily_drift=Decimal(-5)))


def test_an_override_of_an_unknown_kind_is_refused() -> None:
    with pytest.raises(TypeError, match="unknown override Price"):
        generate(market(overrides=(Price(Decimal(1)),)))  # type: ignore[arg-type]


def test_generate_refuses_something_other_than_a_market_spec() -> None:
    with pytest.raises(TypeError, match="MarketSpec"):
        generate(object())  # type: ignore[arg-type]


# --- sessions and the index path -----------------------------------------------------------------


def test_sessions_are_the_weekdays_between_the_bounds_without_holidays() -> None:
    dataset = generate(market(holidays=(WED,), early_closes=(THU,)))
    assert [row.session_date for row in dataset.sessions] == [MON, TUE, THU, FRI]
    tuesday, thursday = session(dataset, TUE), session(dataset, THU)
    assert (tuesday.open_ns, tuesday.close_ns, tuesday.cutoff_ns) == (
        local_ns(TUE, 9, 30),
        local_ns(TUE, 16),
        local_ns(TUE, 23, 59, 59),
    )
    assert not tuesday.early_close
    assert (thursday.close_ns, thursday.early_close) == (local_ns(THU, 13), True)


def test_zero_drift_and_vol_fix_every_index_print_at_index_start() -> None:
    dataset = generate(market())
    for day in (MON, TUE, WED, THU, FRI):
        for slot in QUOTE_SLOTS:
            row = index(dataset, "SPX", "index_value", day, slot)
            assert row.value == Price(Decimal(5000))
            assert row.observed_at_ns == row.available_at_ns == slot_ns(day, slot)
        official = index(dataset, "SPX", "official_close", day, "CLOSE")
        assert official.value == Price(Decimal(5000))
        assert official.observed_at_ns == slot_ns(day, "CLOSE")
        assert official.available_at_ns == local_ns(day, 17)
    assert len(dataset.underlying) == 5 * 6


def test_the_index_path_follows_the_seeded_log_normal_recurrence() -> None:
    spec = market(seed=7, daily_drift=Decimal("0.0005"), daily_vol=Decimal("0.01"))
    rng = random.Random(7)  # noqa: S311 — the generator's seeded path, not cryptography
    level, expected = 0.0, []
    for i in range(5):
        if i:
            level = level + 0.0005 + 0.01 * rng.gauss(0.0, 1.0)
        value = Decimal(5000.0 * math.exp(level)).quantize(Decimal("0.01"), ROUND_HALF_EVEN)
        expected.append(value)
    dataset = generate(spec)
    for day, value in zip((MON, TUE, WED, THU, FRI), expected, strict=True):
        assert {index(dataset, "SPX", "index_value", day, s).value for s in QUOTE_SLOTS} == {
            Price(value)
        }
    assert expected[0] == Decimal("5000.00")
    assert len(set(expected)) == 5


# --- expiries, strikes and contracts -------------------------------------------------------------


def test_contract_ids_and_strike_grid_of_a_small_market() -> None:
    dataset = generate(market())
    strikes = ("4990", "4995", "5000", "5005", "5010")
    expected = {f"SPXW:2024-03-15:{right}:{k}@v1" for right in "CP" for k in strikes}
    assert {row.version_id for row in dataset.contracts} == expected


def test_contract_versions_carry_the_spec_terms_from_the_first_open() -> None:
    dataset = generate(market(premium_multiplier=Decimal(1), deliverable_units=Decimal(3)))
    version = next(row for row in dataset.contracts if row.version_id == f"{PUT_5000}@v1")
    first_open = local_ns(MON, 9, 30)
    assert (version.root, version.underlying_id, version.settlement_series) == (
        "SPXW",
        "SPX",
        "SPX_PM",
    )
    assert version.listed_at_ns == version.effective_from_ns == version.known_from_ns == first_open
    assert version.effective_to_ns is None
    assert version.last_tradable_at_ns == version.terms.expires_at_ns == local_ns(EXPIRY, 16)
    terms = version.terms
    assert (terms.option_type, terms.strike) == (OptionType.PUT, Price(Decimal(5000)))
    assert terms.premium_multiplier == Decimal(1)
    assert terms.deliverable.deliverable_id == "SPX:3"
    assert terms.aggregate_exercise_amount.amount == Decimal(15000)


@pytest.mark.parametrize(
    ("changes", "expiry"),
    [
        ({"weekly_dtes": (12,)}, date(2024, 3, 15)),
        ({"weekly_dtes": (12,), "holidays": (date(2024, 3, 15),)}, date(2024, 3, 14)),
        ({"weekly_dtes": (11,), "holidays": (date(2024, 3, 15),)}, date(2024, 3, 14)),
    ],
)
def test_an_expiry_moves_back_to_the_nearest_weekday_without_a_holiday(
    changes: dict[str, Any], expiry: date
) -> None:
    dataset = generate(market(**changes))
    assert {row.terms.contract_id.split(":")[1] for row in dataset.contracts} == {
        expiry.isoformat()
    }


def test_an_expiry_on_an_early_close_expires_at_13_00() -> None:
    dataset = generate(market(last_session=EXPIRY, early_closes=(EXPIRY,)))
    assert {row.terms.expires_at_ns for row in dataset.contracts} == {local_ns(EXPIRY, 13)}
    ids = quote_ids(dataset)
    for slot in ("DEC", "F1", "F2", "F3"):
        row = quote(dataset, PUT_5000, EXPIRY, slot)
        assert row.observed_at_ns == slot_ns(EXPIRY, slot, close_hour=13)
    assert f"q:{PUT_5000}:{EXPIRY.isoformat()}:CLOSE" not in ids


@pytest.mark.parametrize(
    ("start", "centre"), [(Decimal("5002.50"), 5005), (Decimal("5002.49"), 5000)]
)
def test_the_strike_centre_rounds_half_up_on_the_step(start: Decimal, centre: int) -> None:
    dataset = generate(market(index_start=start))
    strikes = {row.terms.strike.value for row in dataset.contracts}
    assert strikes == {Decimal(centre + 5 * j) for j in range(-2, 3)}


def test_xsp_lists_its_grid_in_its_own_points_and_settles_a_rounded_tenth() -> None:
    spec = market(
        roots=("XSP",),
        index_start=Decimal("4512.37"),
        strike_step=Decimal(1),
        strikes_each_side=1,
        last_session=EXPIRY,
    )
    dataset = generate(spec)
    assert {row.version_id for row in dataset.contracts} == {
        f"XSP:2024-03-15:{right}:{k}@v1" for right in "CP" for k in ("450", "451", "452")
    }
    assert index(dataset, "XSP", "index_value", MON, "DEC").value == Price(Decimal("451.237"))
    assert index(dataset, "XSP", "official_close", MON, "CLOSE").value == Price(Decimal("451.237"))
    assert {row.underlying_id for row in dataset.underlying} == {"XSP"}
    (settlement,) = dataset.settlements
    assert settlement.observation_id == "s:XSP_PM:2024-03-15:c0"
    assert settlement.value == Price(Decimal("451.24"))


def test_both_roots_list_the_same_expiries_on_their_own_underlyings() -> None:
    dataset = generate(market(roots=("SPXW", "XSP"), strike_step=Decimal(1)))
    roots = {row.root: row.underlying_id for row in dataset.contracts}
    assert roots == {"SPXW": "SPX", "XSP": "XSP"}
    assert {row.underlying_id for row in dataset.underlying} == {"SPX", "XSP"}
    assert {row.feature_id.split(":")[0] for row in dataset.features} == {"SPX", "XSP"}
    assert len(dataset.features) == 2 * 5 * 5


# --- quotes ---------------------------------------------------------------------------------------


def test_hand_computed_quotes_round_the_bid_down_and_the_ask_up() -> None:
    dataset = generate(market())
    t = (local_ns(EXPIRY, 16) - slot_ns(MON, "DEC")) / NS_PER_YEAR  # 263.25 h: DST on 03-10
    assert t * 365 * 24 == pytest.approx(263.25)
    cases = [
        ("SPXW:2024-03-15:P:4990", 4990.0, "put", Decimal("56.15"), Decimal("58.50")),
        ("SPXW:2024-03-15:C:5010", 5010.0, "call", Decimal("56.25"), Decimal("58.60")),
    ]
    for contract, strike, right, bid, ask in cases:
        mid = black76(5000.0, strike, t, 1.0, 0.18, right)
        assert expected_sides(mid) == (bid, ask)
        row = quote(dataset, contract, MON, "DEC")
        assert (row.bid, row.ask, row.bid_size, row.ask_size) == (bid, ask, 50, 50)
        assert row.bid <= Decimal(mid - 0.02 * mid) < row.bid + Decimal("0.05")
        assert row.ask - Decimal("0.05") < Decimal(mid + 0.02 * mid) <= row.ask
        assert row.status() is QuoteStatus.VALID


def test_a_bid_at_or_below_zero_is_no_bid_with_size_zero() -> None:
    dataset = generate(market(last_session=EXPIRY))
    t = (local_ns(EXPIRY, 16) - slot_ns(EXPIRY, "DEC")) / NS_PER_YEAR
    mid = black76(5000.0, 4990.0, t, 1.0, 0.18, "put")
    assert 0 < mid < 0.05  # mid - max(0.05, 0.02·mid) < 0
    assert expected_sides(mid) == (Decimal(0), Decimal("0.10"))
    row = quote(dataset, "SPXW:2024-03-15:P:4990", EXPIRY, "DEC")
    assert (row.bid, row.bid_size, row.ask, row.ask_size) == (0, 0, Decimal("0.10"), 50)
    assert row.status() is QuoteStatus.NO_BID


def test_quotes_use_the_forward_spot_over_df_from_the_spec_curve() -> None:
    rates = ((28, Decimal("0.05")), (91, Decimal("0.05")), (182, Decimal("0.05")))
    dataset = generate(market(rates=rates))
    curve = DiscountCurve(tuple((n, bill_df(y, n)) for n, y in rates))
    t = (local_ns(EXPIRY, 16) - slot_ns(TUE, "F2")) / NS_PER_YEAR
    df = curve.df(t * 365)
    assert df is not None
    assert df < 1
    mid = black76(5000.0 / df, 5005.0, t, df, 0.18, "call")
    row = quote(dataset, "SPXW:2024-03-15:C:5005", TUE, "F2")
    assert (row.bid, row.ask) == expected_sides(mid)


def test_every_generated_quote_is_two_sided_on_the_tick_and_published_when_observed() -> None:
    dataset = generate(market(last_session=EXPIRY, strikes_each_side=22))
    statuses = {row.status() for row in dataset.quotes}
    assert statuses == {QuoteStatus.VALID, QuoteStatus.NO_BID}
    for row in dataset.quotes:
        assert row.bid < row.ask
        assert row.bid % Decimal("0.05") == 0 == row.ask % Decimal("0.05")
        assert (row.bid_size == 0) == (row.bid == 0)
        assert row.ask_size == 50
        assert row.observed_at_ns == row.available_at_ns


def test_a_half_spread_too_small_to_separate_bid_and_ask_is_refused() -> None:
    # At sigma 0.001 the 4890 call's mid is exactly F - K = 110.0; mid ± 1e-20 is 110.0 again.
    spec = market(
        sigma=Decimal("0.001"),
        half_spread_abs=Decimal("1E-20"),
        half_spread_rel=Decimal(0),
        strikes_each_side=22,
    )
    with pytest.raises(ValueError, match="half_spread_abs"):
        generate(spec)


def test_quotes_stop_before_the_expiry_close() -> None:
    dataset = generate(market(last_session=date(2024, 3, 18)))
    ids = quote_ids(dataset)
    for slot in ("DEC", "F1", "F2", "F3"):
        assert f"q:{PUT_5000}:2024-03-15:{slot}" in ids
    assert f"q:{PUT_5000}:2024-03-15:CLOSE" not in ids
    assert not any(row.session_date > EXPIRY for row in dataset.quotes)
    assert len(dataset.quotes) == 10 * (9 * 5 + 4)


# --- settlements, rates, coverage ----------------------------------------------------------------


def test_an_expiry_inside_the_table_settles_at_17_00_payable_the_next_business_day() -> None:
    holiday = date(2024, 3, 18)
    dataset = generate(market(last_session=EXPIRY, holidays=(holiday,)))
    (settlement,) = dataset.settlements
    assert settlement.observation_id == "s:SPX_PM:2024-03-15:c0"
    assert (settlement.settlement_series, settlement.session_date) == ("SPX_PM", EXPIRY)
    assert settlement.value == Price(Decimal(5000))
    assert settlement.available_at_ns == local_ns(EXPIRY, 17)
    assert settlement.payable_date == date(2024, 3, 19)
    assert (settlement.final, settlement.correction_version) == (True, 0)


def test_an_expiry_after_the_last_session_has_no_settlement() -> None:
    assert generate(market()).settlements == ()


def test_rates_are_dated_the_previous_session_and_published_at_dec() -> None:
    dataset = generate(market())
    ids = {row.observation_id: row for row in dataset.rates}
    assert len(ids) == 5 * 3
    first = ids["r:UST_CMT:28:2024-03-03"]
    assert (first.observation_date, first.available_at_ns) == (
        date(2024, 3, 3),
        slot_ns(MON, "DEC"),
    )
    second = ids["r:UST_CMT:182:2024-03-04"]
    assert (second.bey, second.available_at_ns) == (Decimal(0), slot_ns(TUE, "DEC"))


def test_the_first_session_already_sees_a_curve() -> None:
    dataset = generate(market())
    view = AsOfView(dataset, slot_ns(MON, "DEC"))
    assert view.curve("UST_CMT") == DiscountCurve(((28, 1.0), (91, 1.0), (182, 1.0)))


def test_every_session_has_a_complete_quotes_partition_and_no_activity() -> None:
    dataset = generate(market())
    assert [(c.table, c.session_date, c.status, c.note) for c in dataset.coverage] == [
        ("quotes", day, CoverageState.COMPLETE, "") for day in (MON, TUE, WED, THU, FRI)
    ]
    assert dataset.activity == ()


# --- manifest, provenance, determinism, storage ---------------------------------------------------


def test_the_manifest_is_a_synthetic_fixture_under_the_public_license() -> None:
    spec = market()
    dataset = generate(spec)
    manifest = dataset.manifest
    assert manifest.fidelity is FidelityClass.SYNTHETIC_FIXTURE
    assert manifest.license_policy_id == "synthetic_public"
    assert manifest.limitations == ()
    assert manifest.calendar_version == CALENDAR_VERSION == "synthetic_weekdays_v1"
    assert manifest.product_rules_version == "cboe_template_unverified_v1"
    assert manifest.feature_versions == tuple(sorted(FEATURE_VERSIONS.items()))
    digest = hashlib.sha256(canonical_json(spec)).hexdigest()
    provenances = {
        row.provenance
        for table in (dataset.contracts, dataset.quotes, dataset.underlying, dataset.rates)
        for row in table
    }
    assert {
        (p.source_id, p.source_schema_version, p.raw_object_digest, p.normalizer_version)
        for p in provenances
    } == {("synthetic", "market_spec_v1", digest, GENERATOR_VERSION)}
    assert {p.revision_id for p in provenances} == {"0"}


def test_the_same_spec_gives_the_same_dataset_and_another_seed_another() -> None:
    spec = market(daily_vol=Decimal("0.01"))
    first, again = generate(spec), generate(spec)
    assert first == again
    assert first.manifest.manifest_id == again.manifest.manifest_id
    other = generate(replace(spec, seed=2))
    assert other.manifest.manifest_id != first.manifest.manifest_id
    assert index(other, "SPX", "index_value", TUE, "DEC") != index(
        first, "SPX", "index_value", TUE, "DEC"
    )


def test_a_generated_dataset_survives_the_store_round_trip(tmp_path: Path) -> None:
    dataset = generate(market(weekly_dtes=(11, 39), last_session=EXPIRY))
    write_dataset(dataset, tmp_path)
    assert read_dataset(tmp_path) == dataset


# --- overrides ------------------------------------------------------------------------------------


def test_quote_pin_replaces_prices_and_sizes_raw_and_keeps_instants() -> None:
    pin = QuotePin(PUT_5000, MON, (Slot.DEC, Slot.F1), Decimal("2.20"), Decimal("2.00"), 7, 9)
    dataset = generate(market(overrides=(pin,)))
    for slot in ("DEC", "F1"):
        row = quote(dataset, PUT_5000, MON, slot)
        assert (row.bid, row.ask, row.bid_size, row.ask_size) == (
            Decimal("2.20"),
            Decimal("2.00"),
            7,
            9,
        )
        assert row.observed_at_ns == row.available_at_ns == slot_ns(MON, slot)
        assert row.status() is QuoteStatus.CROSSED
    assert quote(dataset, PUT_5000, MON, "F2").bid != Decimal("2.20")


def test_quote_drop_removes_and_a_second_drop_has_no_target() -> None:
    drop = QuoteDrop(PUT_5000, MON, (Slot.F1,))
    dataset = generate(market(overrides=(drop,)))
    assert f"q:{PUT_5000}:2024-03-04:F1" not in quote_ids(dataset)
    assert f"q:{PUT_5000}:2024-03-04:F2" in quote_ids(dataset)
    with pytest.raises(ValueError, match=re.escape(f"q:{PUT_5000}:2024-03-04:F1")):
        generate(market(overrides=(drop, drop)))


def test_quote_stale_moves_only_the_observed_instant() -> None:
    stale = QuoteStale(PUT_5000, TUE, (Slot.DEC,), 121)
    row = quote(generate(market(overrides=(stale,))), PUT_5000, TUE, "DEC")
    assert row.observed_at_ns == slot_ns(TUE, "DEC") - 121 * 10**9
    assert row.available_at_ns == slot_ns(TUE, "DEC")


def test_overrides_apply_in_spec_order() -> None:
    pin = QuotePin(PUT_5000, MON, (Slot.DEC,), Decimal(1), Decimal(2), 1, 1)
    drop = QuoteDrop(PUT_5000, MON, (Slot.DEC,))
    assert f"q:{PUT_5000}:2024-03-04:DEC" not in quote_ids(generate(market(overrides=(pin, drop))))
    with pytest.raises(ValueError, match="QuotePin"):
        generate(market(overrides=(drop, pin)))


def later(selector_ns: int, minutes: int) -> datetime:
    return datetime.fromtimestamp(selector_ns / 10**9, tz=NY) + timedelta(minutes=minutes)


@pytest.mark.parametrize(
    ("selector", "current_ns"),
    [
        (f"q:{PUT_5000}:2024-03-04:DEC", slot_ns(MON, "DEC")),
        ("u:SPX:official_close:2024-03-05:CLOSE", local_ns(TUE, 17)),
        ("r:UST_CMT:91:2024-03-04", slot_ns(TUE, "DEC")),
    ],
)
def test_late_availability_delays_one_record(selector: str, current_ns: int) -> None:
    moved = later(current_ns, 5)
    dataset = generate(market(overrides=(LateAvailability(selector, moved),)))
    rows = {row.observation_id: row for row in (*dataset.quotes, *dataset.underlying)}
    rows |= {row.observation_id: row for row in dataset.rates}
    assert rows[selector].available_at_ns == current_ns + 5 * 60 * 10**9


def test_late_availability_delays_a_settlement() -> None:
    selector = "s:SPX_PM:2024-03-15:c0"
    override = LateAvailability(selector, datetime(2024, 3, 16, 9, 0, tzinfo=NY))
    (row,) = generate(market(last_session=EXPIRY, overrides=(override,))).settlements
    assert row.available_at_ns == local_ns(date(2024, 3, 16), 9)


def test_late_availability_needs_a_later_instant_and_an_existing_record() -> None:
    selector = f"q:{PUT_5000}:2024-03-04:DEC"
    same = datetime.fromtimestamp(slot_ns(MON, "DEC") / 10**9, tz=NY)
    with pytest.raises(ValueError, match="later"):
        generate(market(overrides=(LateAvailability(selector, same),)))
    with pytest.raises(ValueError, match=re.escape("q:SPXW:2024-03-15:P:4000:2024-03-04:DEC")):
        missing = LateAvailability("q:SPXW:2024-03-15:P:4000:2024-03-04:DEC", later(0, 1))
        generate(market(overrides=(missing,)))


def test_underlying_drop_removes_index_prints_and_the_official_close() -> None:
    overrides = (
        UnderlyingDrop("SPX", UnderlyingField.INDEX_VALUE, MON, (Slot.DEC, Slot.CLOSE)),
        UnderlyingDrop("SPX", UnderlyingField.OFFICIAL_CLOSE, MON, (Slot.CLOSE,)),
    )
    ids = {row.observation_id for row in generate(market(overrides=overrides)).underlying}
    assert "u:SPX:index_value:2024-03-04:DEC" not in ids
    assert "u:SPX:index_value:2024-03-04:CLOSE" not in ids
    assert "u:SPX:official_close:2024-03-04:CLOSE" not in ids
    assert "u:SPX:index_value:2024-03-04:F1" in ids


def test_settlement_pin_uses_the_value_as_is_and_drop_removes() -> None:
    pin = SettlementPin("SPX_PM", EXPIRY, Price(Decimal("4897.123")))
    (row,) = generate(market(last_session=EXPIRY, overrides=(pin,))).settlements
    assert row.value == Price(Decimal("4897.123"))
    dropped = generate(market(last_session=EXPIRY, overrides=(SettlementDrop("SPX_PM", EXPIRY),)))
    assert dropped.settlements == ()


def test_rate_drop_removes_one_tenor_or_the_whole_curve_of_a_session() -> None:
    one = generate(market(overrides=(RateDrop(TUE, 91),)))
    ids = {row.observation_id for row in one.rates}
    assert "r:UST_CMT:91:2024-03-04" not in ids
    assert {"r:UST_CMT:28:2024-03-04", "r:UST_CMT:182:2024-03-04"} <= ids
    whole = generate(market(overrides=(RateDrop(TUE, None),)))
    assert not any(row.observation_date == MON for row in whole.rates)
    assert len(whole.rates) == 4 * 3


def test_activity_pin_adds_then_replaces_a_cumulative_volume() -> None:
    first = ActivityPin(PUT_5000, MON, Slot.F1, 12)
    second = ActivityPin(PUT_5000, MON, Slot.F1, 30)
    (row,) = generate(market(overrides=(first, second))).activity
    assert row.observation_id == f"a:{PUT_5000}:2024-03-04:F1"
    assert (row.contract_id, row.cumulative_volume) == (PUT_5000, 30)
    assert row.measured_through_ns == row.available_at_ns == slot_ns(MON, "F1")


def test_terms_revision_ends_the_current_version_at_the_session_open() -> None:
    overrides = (
        TermsRevision(PUT_5000, WED, Decimal(50)),
        TermsRevision(PUT_5000, THU, Decimal(40)),
    )
    dataset = generate(market(overrides=overrides))
    versions = {row.version_id: row for row in dataset.contracts}
    v1, v2, v3 = (versions[f"{PUT_5000}@v{n}"] for n in (1, 2, 3))
    assert v1.effective_to_ns == local_ns(WED, 9, 30)
    assert (v2.effective_from_ns, v2.known_from_ns, v2.effective_to_ns) == (
        local_ns(WED, 9, 30),
        local_ns(WED, 9, 30),
        local_ns(THU, 9, 30),
    )
    assert (v3.effective_from_ns, v3.effective_to_ns) == (local_ns(THU, 9, 30), None)
    assert v2.terms.deliverable.deliverable_id == "SPX:50"
    assert v2.terms.deliverable.components[0].units == Decimal(50)
    assert v3.terms.deliverable.deliverable_id == "SPX:40"
    for later_version in (v2, v3):
        assert later_version.terms.contract_id == PUT_5000
        assert later_version.terms.aggregate_exercise_amount == v1.terms.aggregate_exercise_amount
        assert later_version.terms.premium_multiplier == v1.terms.premium_multiplier
        assert later_version.terms.expires_at_ns == v1.terms.expires_at_ns
        assert later_version.listed_at_ns == v1.listed_at_ns
    assert versions[f"{CALL_5000}@v1"].effective_to_ns is None


def test_coverage_status_replaces_a_partition() -> None:
    override = CoverageStatus("quotes", TUE, CoverageState.GAP, "vendor outage")
    rows = {c.session_date: c for c in generate(market(overrides=(override,))).coverage}
    assert (rows[TUE].status, rows[TUE].note) == (CoverageState.GAP, "vendor outage")
    assert rows[WED].status is CoverageState.COMPLETE


def test_early_close_moves_only_the_session_close() -> None:
    dataset = generate(market(overrides=(EarlyClose(THU),)))
    thursday = session(dataset, THU)
    assert (thursday.close_ns, thursday.early_close) == (local_ns(THU, 13), True)
    assert quote(dataset, PUT_5000, THU, "DEC").observed_at_ns == slot_ns(THU, "DEC")
    assert index(dataset, "SPX", "index_value", THU, "CLOSE").observed_at_ns == local_ns(THU, 16)


MISSING_TARGETS = [
    QuotePin("SPXW:2024-03-15:P:4000", MON, (Slot.DEC,), Decimal(1), Decimal(2), 1, 1),
    QuotePin(PUT_5000, MON, (Slot.OPEN,), Decimal(1), Decimal(2), 1, 1),
    QuoteDrop(PUT_5000, SAT, (Slot.DEC,)),
    QuoteStale("SPXW:2024-03-22:P:5000", MON, (Slot.DEC,), 5),
    UnderlyingDrop("XSP", UnderlyingField.INDEX_VALUE, MON, (Slot.DEC,)),
    UnderlyingDrop("SPX", UnderlyingField.OFFICIAL_CLOSE, MON, (Slot.DEC,)),
    SettlementPin("SPX_PM", FRI, Price(Decimal(5000))),
    SettlementDrop("SPX_PM", EXPIRY),
    RateDrop(SAT, None),
    ActivityPin("SPXW:2024-03-15:P:4000", MON, Slot.F1, 1),
    ActivityPin(PUT_5000, SAT, Slot.F1, 1),
    TermsRevision("SPXW:2024-03-15:P:4000", TUE, Decimal(50)),
    TermsRevision(PUT_5000, SAT, Decimal(50)),
    CoverageStatus("quotes", SAT, CoverageState.GAP, ""),
    CoverageStatus("underlying", TUE, CoverageState.GAP, ""),
    EarlyClose(SAT),
]


@pytest.mark.parametrize("override", MISSING_TARGETS, ids=lambda o: type(o).__name__)
def test_an_override_without_a_target_is_refused(override: Any) -> None:
    with pytest.raises(ValueError, match=type(override).__name__):
        generate(market(overrides=(override,)))


def test_a_second_rate_drop_of_the_same_tenor_has_no_target() -> None:
    with pytest.raises(ValueError, match="RateDrop"):
        generate(market(overrides=(RateDrop(TUE, 91), RateDrop(TUE, 91))))


def test_early_close_refuses_a_session_that_already_closes_early() -> None:
    with pytest.raises(ValueError, match="EarlyClose"):
        generate(market(early_closes=(THU,), overrides=(EarlyClose(THU),)))


def test_terms_revision_refuses_a_session_after_expiry_or_at_the_listing() -> None:
    late = TermsRevision(PUT_5000, date(2024, 3, 18), Decimal(50))
    with pytest.raises(ValueError, match="TermsRevision"):
        generate(market(last_session=date(2024, 3, 18), overrides=(late,)))
    with pytest.raises(ValueError, match="effective"):
        generate(market(overrides=(TermsRevision(PUT_5000, MON, Decimal(50)),)))


MALFORMED_OVERRIDES = [
    QuotePin(PUT_5000, MON, (), Decimal(1), Decimal(2), 1, 1),
    QuoteDrop(PUT_5000, MON, (Slot.DEC, Slot.DEC)),
    QuoteStale(PUT_5000, MON, (Slot.DEC,), 0),
    UnderlyingDrop("SPX", UnderlyingField.INDEX_VALUE, MON, ()),
    LateAvailability(f"q:{PUT_5000}:2024-03-04:DEC", datetime(2024, 3, 4, 16, 0)),
]


MISTYPED_OVERRIDES = [
    QuoteDrop(PUT_5000, MON, ("DEC",)),  # type: ignore[arg-type]
    UnderlyingDrop("SPX", "index_value", MON, (Slot.DEC,)),  # type: ignore[arg-type]
]


@pytest.mark.parametrize("override", MISTYPED_OVERRIDES, ids=lambda o: type(o).__name__)
def test_a_mistyped_override_field_is_refused(override: Any) -> None:
    with pytest.raises(TypeError, match=type(override).__name__):
        generate(market(overrides=(override,)))


@pytest.mark.parametrize("override", MALFORMED_OVERRIDES, ids=lambda o: type(o).__name__)
def test_a_malformed_override_is_refused(override: Any) -> None:
    with pytest.raises(ValueError, match=type(override).__name__):
        generate(market(overrides=(override,)))


def test_features_are_computed_after_the_overrides() -> None:
    spec = market(weekly_dtes=(11, 39))
    drop = UnderlyingDrop("SPX", UnderlyingField.INDEX_VALUE, MON, (Slot.CLOSE,))
    plain = features(generate(spec), feature_id("SPX", ATM30_IV))
    dropped = features(generate(replace(spec, overrides=(drop,))), feature_id("SPX", ATM30_IV))
    assert plain[0].value is not None
    assert (dropped[0].value, dropped[0].missing_reason) == (None, "input_missing")
    assert dropped[1].value == plain[1].value
