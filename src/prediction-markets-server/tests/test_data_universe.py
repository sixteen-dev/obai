"""Tests for data.universe deterministic selection (§9.3)."""

from __future__ import annotations

from datetime import datetime, timezone

from src.data.universe import UniverseFilters, select_candidate_universe


def _candidate(
    *,
    condition_id: str,
    volume: float,
    end_date: str,
    category: str = "politics",
    resolution_status: str = "resolved",
) -> dict[str, object]:
    return {
        "condition_id": condition_id,
        "volume": volume,
        "end_date": end_date,
        "category": category,
        "resolution_status": resolution_status,
    }


def test_universe_sorted_by_volume_then_end_date_then_condition_id() -> None:
    """Tie on volume + end_date → break ties by condition_id ASC."""
    candidates = [
        _candidate(condition_id="0xC", volume=100, end_date="2026-01-01T00:00:00Z"),
        _candidate(condition_id="0xA", volume=200, end_date="2026-01-01T00:00:00Z"),
        _candidate(condition_id="0xB", volume=200, end_date="2026-01-01T00:00:00Z"),
        _candidate(condition_id="0xD", volume=200, end_date="2026-02-01T00:00:00Z"),
    ]
    sel = select_candidate_universe(
        candidates,
        UniverseFilters(),
        max_markets=4,
    )
    # Volume DESC, then end_date DESC, then condition_id ASC.
    assert sel.condition_ids == ["0xD", "0xA", "0xB", "0xC"]


def test_universe_caps_at_max_markets_and_counts_overflow() -> None:
    """Cap is applied after sort; excess goes into excluded_counts."""
    candidates = [
        _candidate(condition_id=f"0x{i:03d}", volume=float(i), end_date="2026-01-01T00:00:00Z")
        for i in range(10)
    ]
    sel = select_candidate_universe(candidates, UniverseFilters(), max_markets=3)
    assert len(sel.condition_ids) == 3
    assert sel.excluded_counts.get("over_max_markets_cap") == 7


def test_universe_drops_unresolved_when_require_resolved() -> None:
    """Unresolved markets must be excluded with named reason."""
    candidates = [
        _candidate(condition_id="0xA", volume=100, end_date="2026-01-01T00:00:00Z"),
        _candidate(
            condition_id="0xB",
            volume=200,
            end_date="2026-01-01T00:00:00Z",
            resolution_status="ambiguous",
        ),
    ]
    sel = select_candidate_universe(
        candidates, UniverseFilters(require_resolved=True), max_markets=10
    )
    assert sel.condition_ids == ["0xA"]
    assert sel.excluded_counts.get("not_resolved") == 1


def test_universe_min_lifetime_volume_filter() -> None:
    """min_lifetime_volume drops markets below the threshold and records the reason."""
    candidates = [
        _candidate(condition_id="0xA", volume=500, end_date="2026-01-01T00:00:00Z"),
        _candidate(condition_id="0xB", volume=5000, end_date="2026-01-01T00:00:00Z"),
    ]
    sel = select_candidate_universe(
        candidates, UniverseFilters(min_lifetime_volume=1000), max_markets=10
    )
    assert sel.condition_ids == ["0xB"]
    assert sel.excluded_counts.get("below_lifetime_volume") == 1


def test_universe_category_filter_is_case_insensitive() -> None:
    """Category match should not depend on case."""
    candidates = [
        _candidate(
            condition_id="0xA", volume=100, end_date="2026-01-01T00:00:00Z", category="POLITICS"
        ),
        _candidate(
            condition_id="0xB", volume=200, end_date="2026-01-01T00:00:00Z", category="crypto"
        ),
    ]
    sel = select_candidate_universe(
        candidates, UniverseFilters(category="politics"), max_markets=10
    )
    assert sel.condition_ids == ["0xA"]


def test_universe_fingerprint_deterministic() -> None:
    """Two runs with the same inputs → same condition_ids and same fingerprint."""
    candidates = [
        _candidate(condition_id="0xA", volume=200, end_date="2026-01-01T00:00:00Z"),
        _candidate(condition_id="0xB", volume=100, end_date="2026-01-01T00:00:00Z"),
    ]
    sel1 = select_candidate_universe(candidates, UniverseFilters(), max_markets=10)
    sel2 = select_candidate_universe(candidates, UniverseFilters(), max_markets=10)
    assert sel1.condition_ids == sel2.condition_ids
    assert sel1.fingerprint == sel2.fingerprint


def test_universe_window_filters_by_end_date() -> None:
    """end_date outside the window should be dropped with named reason."""
    candidates = [
        _candidate(condition_id="0xA", volume=100, end_date="2024-01-01T00:00:00Z"),
        _candidate(condition_id="0xB", volume=200, end_date="2026-01-01T00:00:00Z"),
    ]
    sel = select_candidate_universe(
        candidates,
        UniverseFilters(
            start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2027, 1, 1, tzinfo=timezone.utc),
        ),
        max_markets=10,
    )
    assert sel.condition_ids == ["0xB"]
    assert sel.excluded_counts.get("ended_before_window") == 1
