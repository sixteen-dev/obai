"""Historical entry features at each session's CLOSE (ADR 0002 §4, §17 items 27 and 47).

Markets come from the generator, with overrides that build each case: flat and pinned smiles,
an exact-F strike, a term structure, missing inputs. Expected values follow the documented
formulas from the pricing kernels (``parity_forward``, ``implied_vol``) or closed forms.
"""

import hashlib
import math
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from synthetic_builders import (
    MON,
    features,
    index,
    local_ns,
    market,
    quote,
    session,
    slot_ns,
    weekdays,
)

from options_backtest.data.asof import AsOfView
from options_backtest.data.manifest import FrozenDataset, canonical_json
from options_backtest.data.records import QuoteStatus, UnderlyingField
from options_backtest.pricing.features import (
    ATM30_IV,
    CLOSE_TO_SMA_50,
    FEATURE_VERSIONS,
    IV_PERCENTILE_252,
    IV_RANK_252,
    RETURN_20,
    feature_id,
    feature_series,
)
from options_backtest.pricing.iv import ParityPair, implied_vol, parity_forward
from options_backtest.reference.calendars import Slot
from options_backtest.reference.products import SPXW_RULES
from options_backtest.synthetic.market import (
    QuoteDrop,
    QuotePin,
    RateDrop,
    UnderlyingDrop,
    generate,
)

NS_PER_YEAR = 365 * 86_400 * 10**9
T30 = 30 / 365
APR1 = date(2024, 4, 1)
"""A Monday; 2024-05-01 is exactly 30 days later with no DST change between."""
MAY1 = "2024-05-01"
ATM = feature_id("SPX", ATM30_IV)
RANK = feature_id("SPX", IV_RANK_252)
PERCENTILE = feature_id("SPX", IV_PERCENTILE_252)
RET = feature_id("SPX", RETURN_20)
SMA = feature_id("SPX", CLOSE_TO_SMA_50)


def one_session(**changes: Any) -> dict[str, Any]:
    """Market keys of a one-session market on ``APR1`` with one expiry exactly at 30 days."""
    return {"first_session": APR1, "last_session": APR1, "weekly_dtes": (30,), **changes}


def close_pins(dataset: FrozenDataset, contracts: list[str], day: date) -> tuple[QuotePin, ...]:
    """Return pins copying ``dataset``'s CLOSE quotes of ``contracts`` on ``day``."""
    rows = [quote(dataset, contract, day, "CLOSE") for contract in contracts]
    return tuple(
        QuotePin(row.contract_id, day, (Slot.CLOSE,), row.bid, row.ask, 50, 50) for row in rows
    )


def price_pin(contract: str, bid: str, ask: str) -> QuotePin:
    return QuotePin(contract, APR1, (Slot.CLOSE,), Decimal(bid), Decimal(ask), 50, 50)


def mid(dataset: FrozenDataset, contract: str, day: date) -> float:
    row = quote(dataset, contract, day, "CLOSE")
    return float(row.bid + row.ask) / 2


def atm_value(dataset: FrozenDataset, position: int = 0) -> float:
    value = features(dataset, ATM)[position].value
    assert value is not None
    return float(value)


def atm_reason(dataset: FrozenDataset, position: int = 0) -> str | None:
    row = features(dataset, ATM)[position]
    assert row.value is None
    return row.missing_reason


# --- ids and arguments ----------------------------------------------------------------------------


def test_feature_id_joins_underlying_and_name() -> None:
    assert feature_id("SPX", RETURN_20) == "SPX:underlying.return_20s"
    assert feature_id("XSP", ATM30_IV) == "XSP:options.atm30_iv"


def test_feature_id_refuses_an_unknown_name() -> None:
    with pytest.raises(ValueError, match="return_21s"):
        feature_id("SPX", "underlying.return_21s")


def test_feature_series_requires_a_version_for_every_feature() -> None:
    dataset = generate(market())
    versions = {name: "1" for name in FEATURE_VERSIONS if name != IV_RANK_252}
    with pytest.raises(ValueError, match=IV_RANK_252):
        feature_series(dataset, SPXW_RULES, versions)


def test_every_session_gets_five_versioned_features_as_the_generator_stores_them() -> None:
    dataset = generate(market(weekly_dtes=(11, 39)))
    rows = feature_series(dataset, SPXW_RULES, FEATURE_VERSIONS)
    assert len(rows) == 5 * len(dataset.sessions)
    assert {row.feature_id for row in rows} == {ATM, RANK, PERCENTILE, RET, SMA}
    assert {row.feature_version for row in rows} == {"1"}
    key = lambda row: (row.feature_id, row.feature_version, row.session_date)  # noqa: E731
    assert sorted(rows, key=key) == list(dataset.features)


# --- atm30_iv -------------------------------------------------------------------------------------


def test_a_flat_market_gives_its_sigma() -> None:
    dataset = generate(market(weekly_dtes=(11, 39)))
    for position in range(5):
        assert atm_value(dataset, position) == pytest.approx(0.18, abs=5e-4)


def test_an_exact_forward_strike_averages_its_call_and_put_variances() -> None:
    # F_j = K + C_mid - P_mid = 5000 exactly at every strike, so F = 5000.0 is a listed strike.
    puts = {"4990": "40", "4995": "45", "5000": "50", "5005": "55", "5010": "60"}
    pins = [price_pin(f"SPXW:{MAY1}:P:{k}", f"{p}.00", f"{p}.10") for k, p in puts.items()]
    pins += [price_pin(f"SPXW:{MAY1}:C:{k}", "50.00", "50.10") for k in puts]
    dataset = generate(market(**one_session(overrides=tuple(pins))))
    t = (local_ns(date(2024, 5, 1), 16) - local_ns(APR1, 16)) / NS_PER_YEAR
    assert t == T30
    put = implied_vol(50.05, 5000.0, 5000.0, t, 1.0, "put").value
    call = implied_vol(50.05, 5000.0, 5000.0, t, 1.0, "call").value
    assert put is not None
    assert call is not None
    expected = math.sqrt((put**2 * t + call**2 * t) / 2 / T30)
    assert atm_value(dataset) == pytest.approx(expected, rel=1e-12)


def test_an_exact_forward_strike_needs_both_its_call_and_its_put() -> None:
    puts = {"4990": "40", "4995": "45", "5000": "50", "5005": "55", "5010": "60"}
    pins = [price_pin(f"SPXW:{MAY1}:P:{k}", f"{p}.00", f"{p}.10") for k, p in puts.items()]
    pins += [price_pin(f"SPXW:{MAY1}:C:{k}", "50.00", "50.10") for k in puts]
    drop = QuoteDrop(f"SPXW:{MAY1}:C:5000", APR1, (Slot.CLOSE,))
    dataset = generate(market(**one_session(overrides=(*pins, drop))))
    assert atm_reason(dataset) == "strike_unavailable"


def test_variance_is_linear_in_log_moneyness_between_the_otm_put_and_call() -> None:
    base = one_session(index_start=Decimal("5002.50"))  # strikes 4995 ... 5015, F near 5002.5
    rich = generate(market(**base, sigma=Decimal("0.20")))
    cheap = generate(market(**base, sigma=Decimal("0.16")))
    pins = close_pins(rich, [f"SPXW:{MAY1}:{r}:5000" for r in "CP"], APR1)
    pins += close_pins(cheap, [f"SPXW:{MAY1}:{r}:5005" for r in "CP"], APR1)
    dataset = generate(market(**base, overrides=pins))
    pairs = [
        ParityPair(
            float(k),
            mid(dataset, f"SPXW:{MAY1}:C:{k}", APR1),
            mid(dataset, f"SPXW:{MAY1}:P:{k}", APR1),
        )
        for k in (4995, 5000, 5005, 5010, 5015)
    ]
    forward = parity_forward(pairs, 1.0, 5002.5).value
    assert forward is not None
    assert 5000 < forward < 5005
    put = implied_vol(mid(dataset, f"SPXW:{MAY1}:P:5000", APR1), forward, 5000.0, T30, 1.0, "put")
    call = implied_vol(mid(dataset, f"SPXW:{MAY1}:C:5005", APR1), forward, 5005.0, T30, 1.0, "call")
    assert put.value is not None
    assert call.value is not None
    low, high = math.log(5000.0 / forward), math.log(5005.0 / forward)
    w_low, w_high = put.value**2 * T30, call.value**2 * T30
    w_atm = w_low + (w_high - w_low) * (0.0 - low) / (high - low)
    assert atm_value(dataset) == pytest.approx(math.sqrt(w_atm / T30), rel=1e-12)
    assert 0.16 < atm_value(dataset) < 0.20


def test_total_variance_is_linear_in_time_between_the_bracketing_expiries() -> None:
    spec = market(weekly_dtes=(11, 39), last_session=MON)
    far = [f"SPXW:2024-04-12:{r}:{k}" for r in "CP" for k in (4990, 4995, 5000, 5005, 5010)]
    steep = generate(replace(spec, sigma=Decimal("0.25")))
    dataset = generate(replace(spec, overrides=close_pins(steep, far, MON)))
    t1 = (local_ns(date(2024, 3, 15), 16) - local_ns(MON, 16)) / NS_PER_YEAR
    t2 = (local_ns(date(2024, 4, 12), 16) - local_ns(MON, 16)) / NS_PER_YEAR
    w1, w2 = 0.18**2 * t1, 0.25**2 * t2
    w30 = w1 + (w2 - w1) * (T30 - t1) / (t2 - t1)
    assert atm_value(dataset) == pytest.approx(math.sqrt(w30 / T30), abs=5e-4)
    linear_in_vol = 0.18 + (0.25 - 0.18) * (T30 - t1) / (t2 - t1)
    assert abs(atm_value(dataset) - linear_in_vol) > 1e-2


# --- atm30_iv missing reasons ------------------------------------------------------------------


def test_no_expiry_at_or_beyond_30_days_is_no_bracket() -> None:
    assert atm_reason(generate(market())) == "no_bracket"


def test_a_session_without_a_curve_is_curve_unavailable() -> None:
    spec = market(weekly_dtes=(11, 39), last_session=MON, overrides=(RateDrop(MON, None),))
    assert atm_reason(generate(spec)) == "curve_unavailable"


def test_an_expiry_beyond_the_last_published_tenor_is_curve_unavailable() -> None:
    drops = (RateDrop(MON, 91), RateDrop(MON, 182))  # the 28-day point cannot reach 39 days
    spec = market(weekly_dtes=(11, 39), last_session=MON, overrides=drops)
    assert atm_reason(generate(spec)) == "curve_unavailable"


def test_fewer_than_three_valid_pairs_is_forward_unavailable() -> None:
    drop = QuoteDrop("SPXW:2024-03-15:P:5000", MON, (Slot.CLOSE,))
    spec = market(weekly_dtes=(11, 39), last_session=MON, strikes_each_side=1, overrides=(drop,))
    assert atm_reason(generate(spec)) == "forward_unavailable"


def test_an_unsolvable_implied_volatility_is_iv_unavailable() -> None:
    # The 5000 call and put both move up by 4950: parity (hence F = 5000) is unchanged and the
    # put's mid 5000.05 is at or above its upper bound df·K.
    puts = {"4990": "40", "4995": "45", "5005": "55", "5010": "60"}
    pins = [price_pin(f"SPXW:{MAY1}:P:{k}", f"{p}.00", f"{p}.10") for k, p in puts.items()]
    pins += [price_pin(f"SPXW:{MAY1}:C:{k}", "50.00", "50.10") for k in puts]
    pins += [price_pin(f"SPXW:{MAY1}:{r}:5000", "5000.00", "5000.10") for r in "CP"]
    dataset = generate(market(**one_session(overrides=tuple(pins))))
    assert atm_reason(dataset) == "iv_unavailable"


def test_a_missing_close_print_is_input_missing() -> None:
    drop = UnderlyingDrop("SPX", UnderlyingField.INDEX_VALUE, APR1, (Slot.CLOSE,))
    dataset = generate(market(**one_session(overrides=(drop,))))
    assert atm_reason(dataset) == "input_missing"
    assert features(dataset, RET)[0].missing_reason == "input_missing"


def test_every_generated_close_quote_the_feature_reads_is_valid() -> None:
    dataset = generate(market(**one_session()))
    assert {
        quote(dataset, c.terms.contract_id, APR1, "CLOSE").status() for c in dataset.contracts
    } == {QuoteStatus.VALID}
    assert atm_value(dataset) == pytest.approx(0.18, abs=5e-4)


# --- underlying return and SMA ------------------------------------------------------------------


@pytest.fixture(scope="module")
def drifting() -> tuple[FrozenDataset, tuple[date, ...]]:
    """56 sessions of a drifting index; one expiry the next day, so no option feature."""
    days = weekdays(MON, 56)
    spec = market(
        last_session=days[-1],
        daily_drift=Decimal("0.001"),
        weekly_dtes=(1,),
        strikes_each_side=0,
    )
    return generate(spec), days


def closes(dataset: FrozenDataset, days: tuple[date, ...]) -> list[float]:
    return [float(index(dataset, "SPX", "index_value", d, "CLOSE").value.value) for d in days]


def test_return_20s_warms_up_for_20_sessions(
    drifting: tuple[FrozenDataset, tuple[date, ...]],
) -> None:
    dataset, days = drifting
    rows, c = features(dataset, RET), closes(dataset, days)
    for position in range(20):
        assert (rows[position].value, rows[position].missing_reason) == (None, "warmup")
        assert rows[position].warmup_count == position
    assert rows[20].value == Decimal(c[20] / c[0] - 1)
    assert rows[20].value != 0
    assert (rows[20].missing_reason, rows[20].warmup_count) == (None, 20)
    assert rows[55].value == Decimal(c[55] / c[35] - 1)
    assert rows[55].warmup_count == 20
    ids = sorted(f"u:SPX:index_value:{d.isoformat()}:CLOSE" for d in (days[0], days[20]))
    assert rows[20].input_digest == hashlib.sha256(canonical_json(ids)).hexdigest()


def test_close_to_sma_50s_warms_up_for_49_sessions(
    drifting: tuple[FrozenDataset, tuple[date, ...]],
) -> None:
    dataset, days = drifting
    rows, c = features(dataset, SMA), closes(dataset, days)
    assert (rows[48].value, rows[48].missing_reason, rows[48].warmup_count) == (None, "warmup", 48)
    assert rows[49].value == Decimal(c[49] / (math.fsum(c[0:50]) / 50) - 1)
    assert rows[49].warmup_count == 49
    assert rows[55].value == Decimal(c[55] / (math.fsum(c[6:56]) / 50) - 1)


def test_a_session_feature_is_visible_from_its_close_so_first_at_the_next_decision(
    drifting: tuple[FrozenDataset, tuple[date, ...]],
) -> None:
    dataset, days = drifting
    row = features(dataset, RET)[20]
    assert row.max_input_available_at_ns == session(dataset, days[20]).close_ns
    assert AsOfView(dataset, slot_ns(days[20], "DEC")).feature(RET, days[20]) is None
    assert AsOfView(dataset, slot_ns(days[21], "DEC")).feature(RET, days[20]) == row


def test_a_missing_close_breaks_the_windows_that_need_it() -> None:
    days = weekdays(MON, 56)
    drop = UnderlyingDrop("SPX", UnderlyingField.INDEX_VALUE, days[5], (Slot.CLOSE,))
    spec = market(last_session=days[-1], weekly_dtes=(1,), strikes_each_side=0, overrides=(drop,))
    dataset = generate(spec)
    returns, sma = features(dataset, RET), features(dataset, SMA)
    assert returns[5].missing_reason == sma[5].missing_reason == "input_missing"
    assert returns[24].value is not None
    assert (returns[25].missing_reason, returns[25].warmup_count) == ("input_missing", 19)
    assert (sma[49].missing_reason, sma[49].warmup_count) == ("warmup", 43)
    assert (sma[54].missing_reason, sma[54].warmup_count) == ("warmup", 48)
    assert (sma[55].value is not None, sma[55].warmup_count) == (True, 49)


# --- the 252-session IV window (G22) --------------------------------------------------------------


def test_the_iv_window_needs_252_prior_sessions() -> None:
    first = date(2024, 1, 1)  # a Monday; expiries every 28 days bracket 30 days every session
    days = weekdays(first, 253)
    spec = market(
        first_session=first,
        last_session=days[-1],
        weekly_dtes=tuple(28 * k for k in range(1, 15)),
        rates=((28, Decimal(0)), (400, Decimal(0))),
    )
    dataset = generate(spec)
    assert all(row.value is not None for row in features(dataset, ATM))
    percentile, rank = features(dataset, PERCENTILE), features(dataset, RANK)
    for rows in (percentile, rank):
        assert (rows[251].value, rows[251].missing_reason) == (None, "warmup")
        assert rows[251].warmup_count == 251
        assert rows[252].warmup_count == 252
    value = percentile[252].value
    assert value is not None
    assert 0 <= value <= 100
    history = [row.value for row in features(dataset, ATM)]
    now = history[252]
    assert now is not None
    assert value == Decimal(100 * sum(1 for h in history[:252] if h is not None and h <= now) / 252)
    assert rank[252].value is not None or rank[252].missing_reason == "range_zero"
    assert features(dataset, ATM)[0].session_date == first
