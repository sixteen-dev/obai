"""Canonical records: fidelity order, quote status, field guards (ADR 0002 §2, §17 items 6-8)."""

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest
from data_builders import (
    EXPIRY,
    MON,
    TUE,
    activity_obs,
    contract_version,
    coverage,
    feature_obs,
    index_obs,
    local_ns,
    option_terms,
    provenance,
    quote_obs,
    rate_obs,
    settlement_obs,
    trading_session,
    with_field,
)

from options_backtest.data.records import (
    FidelityClass,
    QuoteStatus,
    UnderlyingField,
    require_sha256,
)
from options_backtest.models.market import OptionType, Quote
from options_backtest.money import Price

SESSION = trading_session(MON)
PUT = option_terms(EXPIRY, OptionType.PUT, "4900")
PUT_ID = PUT.contract_id


def test_fidelity_rank_orders_synthetic_below_snapshot_below_quote_events() -> None:
    ranks = [cls.rank for cls in FidelityClass]
    assert ranks == [0, 1, 2]
    assert min(FidelityClass, key=lambda cls: cls.rank) is FidelityClass.SYNTHETIC_FIXTURE


@pytest.mark.parametrize(
    ("bid", "ask", "bid_size", "ask_size", "status"),
    [
        ("-0.05", "1.00", 10, 10, QuoteStatus.NEGATIVE),
        ("1.00", "-1.10", 10, 10, QuoteStatus.NEGATIVE),
        ("1.00", "1.10", -1, 10, QuoteStatus.NEGATIVE),
        ("1.00", "1.10", 10, -1, QuoteStatus.NEGATIVE),
        ("-2", "-1", 10, 10, QuoteStatus.NEGATIVE),
        ("0", "0", 10, 10, QuoteStatus.ZERO_ASK),
        ("1.20", "0", 10, 10, QuoteStatus.ZERO_ASK),
        ("1.20", "1.10", 10, 10, QuoteStatus.CROSSED),
        ("1.10", "1.10", 10, 10, QuoteStatus.LOCKED),
        ("0", "0.05", 0, 10, QuoteStatus.NO_BID),
        ("1.00", "1.10", 10, 10, QuoteStatus.VALID),
        ("1.00", "1.10", 0, 0, QuoteStatus.VALID),
    ],
)
def test_status_checks_in_the_adr_order(
    bid: str, ask: str, bid_size: int, ask_size: int, status: QuoteStatus
) -> None:
    observation = quote_obs(PUT_ID, SESSION, "DEC", bid, ask, bid_size=bid_size, ask_size=ask_size)
    assert observation.status() is status
    assert (observation.bid, observation.ask) == (Decimal(bid), Decimal(ask))


@pytest.mark.parametrize(
    ("bid", "ask"), [("1.00", "1.10"), ("1.10", "1.10"), ("0", "0.05"), ("0.00", "1.5")]
)
def test_quote_of_a_usable_status_keeps_the_exact_sides(bid: str, ask: str) -> None:
    observation = quote_obs(PUT_ID, SESSION, "DEC", bid, ask)
    assert observation.quote() == Quote(Price(Decimal(bid)), Price(Decimal(ask)))
    assert str(observation.quote().bid.value) == bid


@pytest.mark.parametrize(("bid", "ask"), [("1.20", "1.10"), ("0", "0"), ("-0.05", "1.00")])
def test_quote_of_an_invalid_status_raises(bid: str, ask: str) -> None:
    observation = quote_obs(PUT_ID, SESSION, "DEC", bid, ask)
    with pytest.raises(ValueError, match=observation.status().value):
        observation.quote()


def test_valid_records_of_every_table_construct() -> None:
    version = contract_version(PUT)
    assert version.version_id == "SPXW:2024-04-19:P:4900@v1"
    assert index_obs("SPX", SESSION, "DEC", "5000").field is UnderlyingField.INDEX_VALUE
    assert activity_obs(PUT_ID, SESSION, "F1", 0).cumulative_volume == 0
    assert settlement_obs("SPX_PM", MON, "4897").correction_version == 0
    assert rate_obs(28, "-0.001", MON, SESSION.open_ns).bey == Decimal("-0.001")
    assert feature_obs("SPX:f", MON, None, max_input_available_at_ns=1).value is None
    assert coverage(MON).note == ""


def _quote() -> Any:
    return quote_obs(PUT_ID, SESSION, "DEC")


def _index() -> Any:
    return index_obs("SPX", SESSION, "DEC", "5000")


def _activity() -> Any:
    return activity_obs(PUT_ID, SESSION, "F1", 5)


def _settlement() -> Any:
    return settlement_obs("SPX_PM", MON, "4897")


def _rate() -> Any:
    return rate_obs(28, "0.05", MON, SESSION.open_ns)


def _feature() -> Any:
    return feature_obs("SPX:f", MON, "1.5", max_input_available_at_ns=SESSION.close_ns)


def _version() -> Any:
    return contract_version(PUT)


BAD_TYPES: list[tuple[Callable[[], Any], str, object]] = [
    (_quote, "observation_id", 7),
    (_quote, "bid", 2.0),
    (_quote, "ask", "2.20"),
    (_quote, "bid_size", True),
    (_quote, "ask_size", 5.0),
    (_quote, "observed_at_ns", 1.5e18),
    (_quote, "session_date", datetime(2024, 3, 4)),
    (_quote, "provenance", None),
    (_index, "value", Decimal("5000")),
    (_index, "field", "index_value"),
    (_activity, "cumulative_volume", Decimal(5)),
    (_activity, "measured_through_ns", None),
    (_settlement, "final", 1),
    (_settlement, "payable_date", "2024-03-05"),
    (_settlement, "value", Decimal("4897")),
    (_rate, "bey", 0.05),
    (_rate, "tenor_days", "28"),
    (_rate, "observation_date", datetime(2024, 3, 4)),
    (_feature, "value", 1.5),
    (_feature, "missing_reason", 3),
    (_feature, "warmup_count", False),
    (_version, "terms", "SPXW:2024-04-19:P:4900"),
    (_version, "effective_to_ns", 2.0),
    (_version, "provenance", ()),
    (lambda: SESSION, "early_close", "no"),
    (lambda: SESSION, "open_ns", None),
    (lambda: coverage(MON), "status", "gap"),
    (lambda: coverage(MON), "note", None),
    (provenance, "revision_id", 0),
]


@pytest.mark.parametrize(("build", "field", "value"), BAD_TYPES)
def test_a_field_of_the_wrong_type_is_a_type_error_naming_it(
    build: Callable[[], Any], field: str, value: object
) -> None:
    record = build()
    with pytest.raises(TypeError, match=rf"{type(record).__name__}\.{field}\b"):
        with_field(record, **{field: value})


BAD_VALUES: list[tuple[Callable[[], Any], str, object]] = [
    (_quote, "observation_id", "q:SPXW:2024-04-19:P:4900:2024-03-05:DEC"),
    (_quote, "observation_id", ""),
    (_quote, "contract_id", ""),
    (_quote, "bid", Decimal("NaN")),
    (_quote, "ask", Decimal("Infinity")),
    (_quote, "bid", Decimal("0.0000000001")),
    (_quote, "ask", Decimal("1e15")),
    (_quote, "observed_at_ns", -1),
    (_quote, "available_at_ns", 2**63),
    (_quote, "available_at_ns", local_ns(MON, 15, 44)),
    (_index, "observation_id", "u:SPX:official_close:2024-03-04:DEC"),
    (_activity, "cumulative_volume", -1),
    (_activity, "observation_id", "a:SPXW:2024-04-19:P:4905:2024-03-04:F1"),
    (_activity, "available_at_ns", local_ns(MON, 15, 45)),
    (_settlement, "correction_version", -1),
    (_settlement, "observation_id", "s:SPX_PM:2024-03-04:c1"),
    (_settlement, "payable_date", MON),
    (_rate, "tenor_days", 0),
    (_rate, "bey", Decimal("NaN")),
    (_rate, "observation_id", "r:UST_CMT:91:2024-03-04"),
    (_rate, "curve_id", ""),
    (_feature, "missing_reason", "warmup"),
    (_feature, "warmup_count", -1),
    (_feature, "input_digest", "not-a-digest"),
    (_feature, "value", Decimal("NaN")),
    (_feature, "feature_version", ""),
    (_version, "version_id", "SPXW:2024-04-19:P:4900@v0"),
    (_version, "version_id", "SPXW:2024-04-19:P:4900@v01"),
    (_version, "version_id", "SPXW:2024-04-19:P:4905@v1"),
    (_version, "version_id", "SPXW:2024-04-19:P:4900"),
    (_version, "root", "XSP"),
    (_version, "effective_to_ns", local_ns(MON, 9, 30)),
    (_version, "listed_at_ns", local_ns(EXPIRY, 16, 1)),
    (_version, "last_tradable_at_ns", local_ns(EXPIRY, 16, 1)),
    (_version, "underlying_id", ""),
    (lambda: SESSION, "close_ns", SESSION.open_ns),
    (lambda: SESSION, "cutoff_ns", SESSION.close_ns),
    (lambda: SESSION, "open_ns", -5),
    (lambda: coverage(MON), "table", ""),
    (provenance, "source_id", ""),
    (provenance, "raw_object_digest", "A" * 64),
]


@pytest.mark.parametrize(("build", "field", "value"), BAD_VALUES)
def test_a_field_out_of_range_is_a_value_error_naming_it(
    build: Callable[[], Any], field: str, value: object
) -> None:
    record = build()
    with pytest.raises(ValueError, match=rf"{type(record).__name__}\.{field}\b"):
        with_field(record, **{field: value})


def test_a_missing_value_needs_a_reason() -> None:
    missing = feature_obs("SPX:f", MON, None, max_input_available_at_ns=1)
    with pytest.raises(ValueError, match=r"FeatureObservation\.missing_reason"):
        with_field(missing, missing_reason=None)


@pytest.mark.parametrize(
    ("contract_id", "right", "strike"),
    [
        ("XSP:2024-04-19:P:4900", OptionType.PUT, "4900"),
        ("SPXW:2024-04-19:C:4900", OptionType.PUT, "4900"),
        ("SPXW:2024-04-19:P:4900.0", OptionType.PUT, "4900"),
        ("SPXW:2024-4-19:P:4900", OptionType.PUT, "4900"),
        ("SPXW:2024-04-31:P:4900", OptionType.PUT, "4900"),
        ("SPXW:20240419:P:4900", OptionType.PUT, "4900"),
        ("SPXW:2024-04-19:P:4900:x", OptionType.PUT, "4900"),
    ],
)
def test_a_contract_id_must_spell_root_expiry_right_and_strike(
    contract_id: str, right: OptionType, strike: str
) -> None:
    terms = with_field(option_terms(EXPIRY, right, strike), contract_id=contract_id)
    with pytest.raises(ValueError, match=r"ContractVersion\.terms\.contract_id"):
        contract_version(terms)


def test_a_fractional_strike_is_spelled_normalized() -> None:
    terms = option_terms(EXPIRY, OptionType.CALL, "451.5", root="XSP", underlying="XSP")
    assert contract_version(terms, root="XSP").version_id == "XSP:2024-04-19:C:451.5@v1"


def test_a_revision_may_end_at_its_successor_start() -> None:
    revised_at = local_ns(TUE, 9, 30)
    first = contract_version(PUT, effective_to_ns=revised_at)
    second = contract_version(PUT, version=2, effective_from_ns=revised_at)
    assert (first.effective_to_ns, second.effective_from_ns) == (revised_at, revised_at)
    assert second.version_id.endswith("@v2")


@pytest.mark.parametrize("digest", ["0" * 64, "0123456789abcdef" * 4])
def test_require_sha256_accepts_lower_hex(digest: str) -> None:
    require_sha256(digest, "field")


@pytest.mark.parametrize(
    ("digest", "error"),
    [
        ("0" * 63, ValueError),
        ("g" * 64, ValueError),
        ("F" * 64, ValueError),
        (b"0" * 64, TypeError),
    ],
)
def test_require_sha256_rejects_anything_else(digest: object, error: type[Exception]) -> None:
    with pytest.raises(error, match="field"):
        require_sha256(digest, "field")
