"""Tests for data.coverage (pure decision logic)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.data.coverage import (
    build_data_coverage,
    classify_cache_action,
    compute_quality_flags,
    reliability_label,
)


def _now() -> datetime:
    return datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)


def test_classify_cache_action_no_cache_yields_fetched() -> None:
    """No coverage row at all → fresh fetch."""
    decision = classify_cache_action(
        coverage=None,
        requested_fidelity=60,
        requested_start=None,
        requested_end=None,
        freshness_hours=24,
        now=_now(),
    )
    assert decision.action == "fetched"


def test_classify_cache_action_fidelity_mismatch_yields_fetched() -> None:
    """Cached fidelity differs from request → must re-fetch (§9.2 finer-from-coarse rule)."""
    coverage = {
        "fidelity_minutes": 1440,
        "first_timestamp": _now() - timedelta(days=30),
        "last_timestamp": _now() - timedelta(days=1),
        "last_refreshed": _now() - timedelta(hours=1),
    }
    decision = classify_cache_action(
        coverage=coverage,
        requested_fidelity=60,
        requested_start=None,
        requested_end=None,
        freshness_hours=24,
        now=_now(),
    )
    assert decision.action == "fetched"
    assert "fidelity" in decision.reason


def test_classify_cache_action_stale_yields_refreshed() -> None:
    """Cached fidelity matches but last_refreshed exceeds the freshness window."""
    coverage = {
        "fidelity_minutes": 60,
        "first_timestamp": _now() - timedelta(days=30),
        "last_timestamp": _now() - timedelta(days=1),
        "last_refreshed": _now() - timedelta(hours=72),
    }
    decision = classify_cache_action(
        coverage=coverage,
        requested_fidelity=60,
        requested_start=None,
        requested_end=None,
        freshness_hours=24,
        now=_now(),
    )
    assert decision.action == "refreshed"


def test_classify_cache_action_fresh_and_covers_yields_cached() -> None:
    """Cached + fresh + range covered → no API call."""
    coverage = {
        "fidelity_minutes": 60,
        "first_timestamp": _now() - timedelta(days=30),
        "last_timestamp": _now() - timedelta(hours=2),
        "last_refreshed": _now() - timedelta(hours=1),
    }
    decision = classify_cache_action(
        coverage=coverage,
        requested_fidelity=60,
        requested_start=_now() - timedelta(days=20),
        requested_end=_now() - timedelta(hours=3),
        freshness_hours=24,
        now=_now(),
    )
    assert decision.action == "cached"


def test_build_data_coverage_emits_all_required_fields() -> None:
    """§16 required fields must all appear in the output dict."""
    coverage = build_data_coverage(
        markets_requested=500,
        markets_selected=312,
        markets_with_history=284,
        markets_excluded=216,
        tokens_requested=568,
        tokens_with_history=541,
        price_rows_loaded=1_000_000,
        price_rows_used=18_472,
        observations_used=426,
        distinct_markets_used=284,
        coverage_start=_now(),
        coverage_end=_now(),
        skipped_reasons={"ambiguous_resolution": 18},
    )
    expected = {
        "markets_requested",
        "markets_selected",
        "markets_with_history",
        "markets_excluded",
        "tokens_requested",
        "tokens_with_history",
        "price_rows_loaded",
        "price_rows_used",
        "observations_used",
        "distinct_markets_used",
        "coverage_start",
        "coverage_end",
        "skipped_reasons",
    }
    assert expected.issubset(coverage.keys())


def test_quality_flags_below_30_observations_flags_weak_sample() -> None:
    """observations_used < 30 → sample_size_below_30 + distinct_markets_below_20."""
    coverage = build_data_coverage(
        markets_requested=10,
        markets_selected=10,
        markets_with_history=10,
        markets_excluded=0,
        tokens_requested=20,
        tokens_with_history=20,
        price_rows_loaded=1000,
        price_rows_used=200,
        observations_used=12,
        distinct_markets_used=8,
        coverage_start=None,
        coverage_end=None,
        skipped_reasons={},
    )
    flags = compute_quality_flags(coverage=coverage, lifetime_volume_filter_used=True)
    assert "sample_size_below_30" in flags
    assert "distinct_markets_below_20" in flags
    assert "lifetime_volume_filter_uses_final_volume" in flags
    assert "no_historical_order_book_depth" in flags


def test_quality_flags_high_skip_rate() -> None:
    """Skip rate > 40% should trigger high_skip_rate."""
    coverage = build_data_coverage(
        markets_requested=100,
        markets_selected=100,
        markets_with_history=40,
        markets_excluded=0,
        tokens_requested=200,
        tokens_with_history=80,
        price_rows_loaded=5000,
        price_rows_used=2000,
        observations_used=300,
        distinct_markets_used=80,
        coverage_start=None,
        coverage_end=None,
        skipped_reasons={"missing_price_history": 50},
    )
    flags = compute_quality_flags(coverage=coverage, lifetime_volume_filter_used=False)
    assert "high_skip_rate" in flags


def test_reliability_label_weak_when_distinct_markets_low() -> None:
    """distinct_markets < 20 forces weak even if observations are high."""
    coverage = build_data_coverage(
        markets_requested=10,
        markets_selected=10,
        markets_with_history=10,
        markets_excluded=0,
        tokens_requested=20,
        tokens_with_history=20,
        price_rows_loaded=1000,
        price_rows_used=300,
        observations_used=500,
        distinct_markets_used=8,
        coverage_start=None,
        coverage_end=None,
        skipped_reasons={},
    )
    flags = compute_quality_flags(coverage=coverage, lifetime_volume_filter_used=False)
    assert reliability_label(coverage, flags) == "weak"


def test_reliability_label_stronger_only_for_large_universe() -> None:
    """100+ observations + 50+ distinct markets is the stronger threshold."""
    coverage = build_data_coverage(
        markets_requested=200,
        markets_selected=200,
        markets_with_history=200,
        markets_excluded=0,
        tokens_requested=400,
        tokens_with_history=400,
        price_rows_loaded=50_000,
        price_rows_used=10_000,
        observations_used=500,
        distinct_markets_used=200,
        coverage_start=None,
        coverage_end=None,
        skipped_reasons={},
    )
    flags = compute_quality_flags(coverage=coverage, lifetime_volume_filter_used=False)
    assert reliability_label(coverage, flags) == "stronger"


def test_reliability_label_downgrades_on_high_skip_rate() -> None:
    """high_skip_rate flag should drop stronger → moderate."""
    coverage = build_data_coverage(
        markets_requested=200,
        markets_selected=200,
        markets_with_history=80,
        markets_excluded=0,
        tokens_requested=400,
        tokens_with_history=160,
        price_rows_loaded=50_000,
        price_rows_used=10_000,
        observations_used=500,
        distinct_markets_used=200,
        coverage_start=None,
        coverage_end=None,
        skipped_reasons={"missing_price_history": 120},
    )
    flags = compute_quality_flags(coverage=coverage, lifetime_volume_filter_used=False)
    assert "high_skip_rate" in flags
    assert reliability_label(coverage, flags) == "moderate"
