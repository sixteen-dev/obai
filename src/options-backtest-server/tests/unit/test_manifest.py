"""Canonical JSON, table digests and freezing (ADR 0002 §2, §17 items 9-10, C34)."""

import hashlib
import random
from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from data_builders import (
    EXPIRY,
    MON,
    TUE,
    contract_version,
    coverage,
    feature_obs,
    freeze,
    local_ns,
    option_terms,
    quote_obs,
    sample_dataset,
    sample_tables,
    trading_session,
    with_field,
)

from options_backtest.data.manifest import (
    SYNTHETIC_LICENSE_POLICY_ID,
    TABLE_NAMES,
    DatasetManifest,
    FrozenDataset,
    TableDigest,
    canonical_json,
    table_digest,
)
from options_backtest.data.records import CoverageState, FidelityClass, QuoteStatus
from options_backtest.models.market import OptionType
from options_backtest.money import Price

EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_canonical_json_sorts_keys_without_whitespace_and_keeps_decimal_text() -> None:
    tree = {"b": [Decimal("2.00"), Decimal("1E+2")], "a": {"z": None, "y": True, "x": 3}}
    assert canonical_json(tree) == b'{"a":{"x":3,"y":true,"z":null},"b":["2.00","1E+2"]}'


def test_canonical_json_spells_dates_enums_dataclasses_and_unicode() -> None:
    tree = (
        date(2024, 3, 4),
        datetime(2024, 3, 4, 21, 0, tzinfo=UTC),
        datetime(2024, 3, 4, 16, 0, tzinfo=timezone(timedelta(hours=-5))),
        CoverageState.GAP,
        Price(Decimal("451.50")),
        "é",
    )
    assert (
        canonical_json(tree)
        == (
            '["2024-03-04","2024-03-04T21:00:00+00:00","2024-03-04T16:00:00-05:00",'
            '"gap",{"value":"451.50"},"é"]'
        ).encode()
    )


def test_canonical_json_of_a_quote_record_is_pinned() -> None:
    observation = quote_obs("SPXW:2024-04-19:P:4900", trading_session(MON), "DEC")
    assert canonical_json(observation) == (
        b'{"ask":"2.20","ask_size":50,"available_at_ns":1709585100000000000,"bid":"2.00",'
        b'"bid_size":50,"contract_id":"SPXW:2024-04-19:P:4900",'
        b'"observation_id":"q:SPXW:2024-04-19:P:4900:2024-03-04:DEC",'
        b'"observed_at_ns":1709585100000000000,"provenance":{"normalizer_version":'
        b'"synthetic_market_v1","raw_object_digest":'
        b'"0c5687d36405618aeb2878fc869ad767cc7e998b3b08f02157c1c3fc039fefd6",'
        b'"revision_id":"0","source_id":"synthetic","source_schema_version":'
        b'"market_spec_v1"},"session_date":"2024-03-04"}'
    )


@pytest.mark.parametrize(
    "value",
    [1.5, {"a": [0.0]}, datetime(2024, 3, 4, 16, 0), {1: "x"}, {"a", "b"}, b"x", object()],
)
def test_canonical_json_refuses_floats_naive_datetimes_and_unknown_types(value: object) -> None:
    with pytest.raises(TypeError):
        canonical_json(value)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("-Infinity"), Decimal("sNaN")])
def test_canonical_json_refuses_non_finite_decimals(value: Decimal) -> None:
    with pytest.raises(ValueError, match="finite"):
        canonical_json([value])


def test_canonical_json_bounds_its_recursion() -> None:
    tree: Any = "leaf"
    for _ in range(100):
        tree = [tree]
    with pytest.raises(ValueError, match="deeper"):
        canonical_json(tree)


def test_table_digest_hashes_each_row_then_a_newline() -> None:
    rows = (coverage(MON), coverage(TUE, CoverageState.GAP))
    expected = hashlib.sha256(b"".join(canonical_json(row) + b"\n" for row in rows))
    assert table_digest(rows) == expected.hexdigest()
    assert table_digest(()) == EMPTY_SHA256


def test_freeze_seals_tables_in_manifest_order() -> None:
    dataset = sample_dataset(limitations=("b_limit", "a_limit"))
    manifest = dataset.manifest
    assert tuple(digest.table for digest in manifest.tables) == TABLE_NAMES
    for digest in manifest.tables:
        rows = getattr(dataset, digest.table)
        assert digest == TableDigest(digest.table, len(rows), table_digest(rows))
    assert manifest.limitations == ("a_limit", "b_limit")
    assert manifest.fidelity is FidelityClass.SYNTHETIC_FIXTURE
    assert manifest.license_policy_id == SYNTHETIC_LICENSE_POLICY_ID


def test_manifest_id_is_the_sha256_of_the_canonical_manifest_without_it() -> None:
    manifest = sample_dataset().manifest
    body = {field.name: getattr(manifest, field.name) for field in fields(manifest)}
    del body["manifest_id"]
    assert manifest.manifest_id == hashlib.sha256(canonical_json(body)).hexdigest()


def test_freeze_sorts_every_table_by_its_natural_key() -> None:
    tue_first = trading_session(TUE)
    feature_ids = ("SPX:b", "SPX:a")
    features = tuple(
        feature_obs(fid, day, "1", max_input_available_at_ns=1, version=version)
        for fid in feature_ids
        for version in ("2", "1")
        for day in (TUE, MON)
    )
    dataset = freeze(
        sessions=(tue_first, trading_session(MON)),
        features=features,
        coverage=(coverage(TUE), coverage(MON, table="underlying"), coverage(MON)),
    )
    assert [s.session_date for s in dataset.sessions] == [MON, TUE]
    keys = [(f.feature_id, f.feature_version, f.session_date) for f in dataset.features]
    assert keys == sorted(keys)
    assert [(c.table, c.session_date) for c in dataset.coverage] == [
        ("quotes", MON),
        ("quotes", TUE),
        ("underlying", MON),
    ]
    quotes = sample_dataset().quotes
    assert [q.observation_id for q in quotes] == sorted(q.observation_id for q in quotes)


@pytest.mark.parametrize("seed", range(5))
def test_shuffled_rows_give_the_same_dataset_and_manifest_id(seed: int) -> None:
    baseline = sample_dataset()
    shuffled: dict[str, Any] = {}
    rng = random.Random(seed)  # noqa: S311 — a seeded shuffle of test rows, not cryptography
    for name, rows in sample_tables().items():
        permuted = list(rows)
        rng.shuffle(permuted)
        shuffled[name] = tuple(permuted)
    assert sample_dataset(**shuffled) == baseline


def test_any_changed_row_changes_the_manifest_id() -> None:
    tables = sample_tables()
    first, *rest = tables["quotes"]
    changed = sample_dataset(quotes=(with_field(first, bid=Decimal("2.0")), *rest))
    assert changed.manifest.manifest_id != sample_dataset().manifest.manifest_id


def _duplicate(table: str) -> dict[str, Any]:
    rows = sample_tables()[table]
    return {table: (*rows, rows[0])}


@pytest.mark.parametrize(
    "table",
    ["sessions", "contracts", "quotes", "underlying", "activity", "settlements", "rates"],
)
def test_freeze_refuses_a_duplicate_id(table: str) -> None:
    with pytest.raises(ValueError, match=f"{table}: duplicate key"):
        sample_dataset(**_duplicate(table))


def test_freeze_refuses_a_duplicate_feature_or_coverage_key() -> None:
    feature = feature_obs("SPX:f", MON, "1", max_input_available_at_ns=1)
    with pytest.raises(ValueError, match="features: duplicate key"):
        freeze(features=(feature, with_field(feature, value=Decimal(2))))
    with pytest.raises(ValueError, match="coverage: duplicate key"):
        freeze(coverage=(coverage(MON), coverage(MON, CoverageState.GAP)))


def test_freeze_refuses_overlapping_versions_of_one_contract() -> None:
    terms = option_terms(EXPIRY, OptionType.PUT, "4900")
    first = contract_version(terms)
    second = contract_version(terms, version=2, effective_from_ns=local_ns(TUE, 9, 30))
    with pytest.raises(ValueError, match="contracts: overlapping versions"):
        freeze(contracts=(first, second))
    ended = contract_version(terms, effective_to_ns=local_ns(TUE, 9, 30))
    assert len(freeze(contracts=(second, ended)).contracts) == 2


def test_freeze_refuses_overlapping_sessions() -> None:
    mon = trading_session(MON)
    overlapping = with_field(trading_session(TUE), open_ns=mon.cutoff_ns)
    with pytest.raises(ValueError, match="sessions: overlapping"):
        freeze(sessions=(mon, overlapping))


@pytest.mark.parametrize(
    "overrides",
    [
        {"fidelity": FidelityClass.HISTORICAL_SNAPSHOT},
        {"fidelity": FidelityClass.HISTORICAL_QUOTE_EVENTS},
        {"license_policy_id": "massive_individual"},
    ],
)
def test_freeze_refuses_synthetic_records_outside_a_synthetic_manifest(
    overrides: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="synthetic"):
        sample_dataset(**overrides)


def test_non_synthetic_records_may_be_historical() -> None:
    quote = quote_obs("SPXW:2024-04-19:P:4900", trading_session(MON), "DEC", source_id="massive")
    dataset = freeze(
        quotes=(quote,),
        fidelity=FidelityClass.HISTORICAL_SNAPSHOT,
        limitations=("missing_side_update_time",),
        license_policy_id="massive_individual",
    )
    assert dataset.quotes[0].status() is QuoteStatus.VALID


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"sessions": (coverage(MON),)}, TypeError),
        ({"quotes": [None]}, TypeError),
        ({"fidelity": "synthetic_fixture"}, TypeError),
        ({"limitations": ("a", "a")}, ValueError),
        ({"limitations": "abc"}, TypeError),
        ({"coverage": iter(())}, TypeError),
        ({"limitations": ("",)}, ValueError),
        ({"feature_versions": (("f", "1"), ("f", "2"))}, ValueError),
        ({"feature_versions": (("f",),)}, ValueError),
        ({"calendar_version": ""}, ValueError),
        ({"product_rules_version": None}, TypeError),
    ],
)
def test_freeze_refuses_malformed_arguments(
    overrides: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        freeze(**overrides)


def test_a_manifest_with_a_wrong_id_cannot_be_built() -> None:
    manifest = sample_dataset().manifest
    with pytest.raises(ValueError, match="manifest_id"):
        replace(manifest, manifest_id="0" * 64)
    with pytest.raises(ValueError, match="manifest_id"):
        replace(manifest, calendar_version="other_calendar_v1")


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"table": "trades"}, ValueError),
        ({"rows": -1}, ValueError),
        ({"rows": True}, TypeError),
        ({"sha256": "x"}, ValueError),
    ],
)
def test_table_digest_record_is_validated(changes: dict[str, Any], error: type[Exception]) -> None:
    with pytest.raises(error, match="TableDigest"):
        replace(TableDigest("quotes", 0, EMPTY_SHA256), **changes)


def test_manifest_tables_must_follow_table_names() -> None:
    manifest = sample_dataset().manifest
    with pytest.raises(ValueError, match="tables"):
        DatasetManifest(
            manifest.manifest_id,
            manifest.fidelity,
            manifest.limitations,
            tuple(reversed(manifest.tables)),
            manifest.calendar_version,
            manifest.product_rules_version,
            manifest.feature_versions,
            manifest.license_policy_id,
        )


def test_freeze_is_the_dataset_constructor_for_every_table() -> None:
    dataset = sample_dataset()
    assert isinstance(dataset, FrozenDataset)
    assert all(len(getattr(dataset, name)) >= 1 for name in TABLE_NAMES)


def test_with_features_reseals_exactly_as_freeze_does() -> None:
    features = sample_tables()["features"]
    unfeatured = sample_dataset(features=())
    assert unfeatured.with_features(tuple(reversed(features))) == sample_dataset()
    with pytest.raises(ValueError, match="features: duplicate key"):
        unfeatured.with_features((*features, features[0]))


def test_table_digest_needs_a_sequence_of_rows() -> None:
    with pytest.raises(TypeError, match="sequence"):
        table_digest("rows")


@pytest.mark.parametrize("field", ["limitations", "tables", "feature_versions"])
def test_manifest_sequences_must_be_tuples(field: str) -> None:
    manifest = sample_dataset().manifest
    with pytest.raises(TypeError, match=f"DatasetManifest.{field}"):
        replace(manifest, **{field: list(getattr(manifest, field))})
