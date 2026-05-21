"""Cache coverage decisions, data_coverage builder, and quality-flag generation.

All functions here are pure — they read coverage dicts and request params,
they do not call the DB or HTTP layer. The downloader translates a function
result into actions (fetch vs cache vs refresh) and meta updates.

Design references:
    §9.2 fidelity rules     — classify_cache_action
    §16 data_coverage shape — build_data_coverage
    §16 quality flags       — compute_quality_flags
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

CacheAction = Literal["fetched", "cached", "refreshed"]

# Thresholds straight from §16 — keep as named constants so tightening
# them is a one-line change, not a hunt-the-magic-number exercise.
_SAMPLE_SIZE_BELOW_FLOOR = 30
_SAMPLE_SIZE_MODERATE_CEILING = 100
_SAMPLE_SIZE_STRONGER_FLOOR = 100
_DISTINCT_MARKETS_BELOW_FLOOR = 20
_DISTINCT_MARKETS_STRONGER_FLOOR = 50
_HIGH_SKIP_RATE_PCT = 0.40
_HIGH_AMBIG_RESOLUTION_RATE_PCT = 0.10


@dataclass(frozen=True)
class CacheDecision:
    """Why the downloader picked a particular cache action for one entity."""

    action: CacheAction
    reason: str


def classify_cache_action(
    *,
    coverage: dict[str, Any] | None,
    requested_fidelity: int,
    requested_start: datetime | None,
    requested_end: datetime | None,
    freshness_hours: int,
    now: datetime,
) -> CacheDecision:
    """Decide whether to fetch, refresh, or serve from cache for one token.

    Fidelity rules (§9.2):
        - finer than cached coverage cannot be served from cache (always
          ``fetched``)
        - V1 does not resample coarser-from-finer, so a coarser request
          when only finer cache exists is also a ``fetched``

    Args:
        coverage: Optional row from store.get_price_history_coverage. None
            means no cache for the (token, fidelity, source) triple.
        requested_fidelity: Requested fidelity in minutes.
        requested_start: Optional lower bound of the requested range.
        requested_end: Optional upper bound of the requested range.
        freshness_hours: Settings-derived staleness threshold.
        now: Current time, passed explicitly so tests can pin it.

    Returns:
        CacheDecision naming the action and a short reason.

    """
    if coverage is None:
        return CacheDecision(action="fetched", reason="no cached coverage for triple")

    cached_fidelity = _safe_int(coverage.get("fidelity_minutes"))
    if cached_fidelity is None or cached_fidelity != requested_fidelity:
        # Fidelity differences always fall through to refetch — V1 will not
        # resample finer rows down to a coarser bucket.
        return CacheDecision(
            action="fetched",
            reason="cached fidelity does not match requested fidelity",
        )

    last_refreshed = _ensure_aware(coverage.get("last_refreshed"))
    if last_refreshed is None:
        return CacheDecision(action="fetched", reason="cache missing last_refreshed")

    if (now - last_refreshed) > timedelta(hours=freshness_hours):
        return CacheDecision(action="refreshed", reason="cached data older than freshness window")

    if not _covers_range(coverage, requested_start, requested_end):
        return CacheDecision(
            action="refreshed",
            reason="cached range does not cover requested window",
        )

    return CacheDecision(action="cached", reason="cache fresh and covers requested range")


def build_data_coverage(
    *,
    markets_requested: int,
    markets_selected: int,
    markets_with_history: int,
    markets_excluded: int,
    tokens_requested: int,
    tokens_with_history: int,
    price_rows_loaded: int,
    price_rows_used: int,
    observations_used: int,
    distinct_markets_used: int,
    coverage_start: datetime | None,
    coverage_end: datetime | None,
    skipped_reasons: dict[str, int],
) -> dict[str, Any]:
    """Pack the §16 data_coverage block into one dict.

    Centralizing the shape here keeps every historical tool emitting the
    same field set in the same order, which the contract scorer in §17
    will rely on.

    Args:
        markets_requested: Markets passed in by the caller (before filters).
        markets_selected: Markets after universe selection.
        markets_with_history: Selected markets with at least one cached price row.
        markets_excluded: Markets dropped by filters.
        tokens_requested: Outcome tokens implied by the selected markets.
        tokens_with_history: Tokens with at least one cached price row.
        price_rows_loaded: Raw rows loaded from the cache.
        price_rows_used: Rows surviving per-tool filtering.
        observations_used: Metric denominator after applying sampling mode.
        distinct_markets_used: Distinct condition_ids contributing observations.
        coverage_start: Earliest used timestamp.
        coverage_end: Latest used timestamp.
        skipped_reasons: Mapping reason → count.

    Returns:
        Dict matching the §16 data_coverage shape exactly.

    """
    return {
        "markets_requested": markets_requested,
        "markets_selected": markets_selected,
        "markets_with_history": markets_with_history,
        "markets_excluded": markets_excluded,
        "tokens_requested": tokens_requested,
        "tokens_with_history": tokens_with_history,
        "price_rows_loaded": price_rows_loaded,
        "price_rows_used": price_rows_used,
        "observations_used": observations_used,
        "distinct_markets_used": distinct_markets_used,
        "coverage_start": _iso(coverage_start),
        "coverage_end": _iso(coverage_end),
        "skipped_reasons": dict(sorted(skipped_reasons.items())),
    }


def compute_quality_flags(
    *,
    coverage: dict[str, Any],
    lifetime_volume_filter_used: bool,
    iid_monte_carlo: bool = False,
    sparse_short_horizon: bool = False,
    category_match_broad: bool = False,
) -> list[str]:
    """Emit the §16 quality_flags appropriate for one analysis response.

    Only flags whose triggering condition is met appear in the output —
    callers should not strip "uninteresting" flags after the fact, the
    set is the message. ``no_returns_to_simulate`` fires whenever
    ``observations_used == 0`` so downstream callers know an empty
    ``monte_carlo_input.returns`` array is a designed terminal state,
    not a transport-layer bug.

    Args:
        coverage: data_coverage dict built by build_data_coverage.
        lifetime_volume_filter_used: True when min_lifetime_volume was
            actually applied (per §11.4 the contamination must be named).
        iid_monte_carlo: True when an IID Monte Carlo run was the source
            of distributions.
        sparse_short_horizon: True when one or more short
            time-to-resolution buckets had very few samples.
        category_match_broad: True when category/topic match was
            tag-based rather than semantic.

    Returns:
        Sorted, deduped list of flag names.

    """
    flags: list[str] = []
    observations_used = int(coverage.get("observations_used", 0))
    distinct_markets = int(coverage.get("distinct_markets_used", 0))
    skipped = coverage.get("skipped_reasons", {})
    markets_selected = int(coverage.get("markets_selected", 0))

    if observations_used == 0:
        flags.append("no_returns_to_simulate")
    if observations_used < _SAMPLE_SIZE_BELOW_FLOOR:
        flags.append("sample_size_below_30")
    elif observations_used < _SAMPLE_SIZE_MODERATE_CEILING:
        flags.append("sample_size_30_to_100")
    if distinct_markets < _DISTINCT_MARKETS_BELOW_FLOOR:
        flags.append("distinct_markets_below_20")

    total_skipped = sum(int(v) for v in skipped.values())
    if markets_selected > 0 and (total_skipped / markets_selected) > _HIGH_SKIP_RATE_PCT:
        flags.append("high_skip_rate")
    ambig = int(skipped.get("ambiguous_resolution", 0))
    if markets_selected > 0 and (ambig / markets_selected) > _HIGH_AMBIG_RESOLUTION_RATE_PCT:
        flags.append("high_ambiguous_resolution_rate")

    if sparse_short_horizon:
        flags.append("sparse_short_horizon_history")
    if lifetime_volume_filter_used:
        flags.append("lifetime_volume_filter_uses_final_volume")
    if category_match_broad:
        flags.append("category_or_topic_match_is_broad")
    flags.append("no_historical_order_book_depth")
    if iid_monte_carlo:
        flags.append("iid_monte_carlo_assumption")

    return sorted(set(flags))


def reliability_label(coverage: dict[str, Any], quality_flags: list[str]) -> str:
    """Pick the §16 reliability label from coverage counts + quality flags.

    Args:
        coverage: data_coverage dict.
        quality_flags: Flag list from compute_quality_flags.

    Returns:
        One of "weak", "moderate", "stronger".

    """
    observations_used = int(coverage.get("observations_used", 0))
    distinct_markets = int(coverage.get("distinct_markets_used", 0))
    if (
        observations_used < _SAMPLE_SIZE_BELOW_FLOOR
        or distinct_markets < _DISTINCT_MARKETS_BELOW_FLOOR
    ):
        base = "weak"
    elif (
        observations_used >= _SAMPLE_SIZE_STRONGER_FLOOR
        and distinct_markets >= _DISTINCT_MARKETS_STRONGER_FLOOR
    ):
        base = "stronger"
    else:
        base = "moderate"
    if "high_skip_rate" in quality_flags or "high_ambiguous_resolution_rate" in quality_flags:
        return _downgrade(base)
    return base


# -- helpers ------------------------------------------------------------------


def _downgrade(label: str) -> str:
    """Step one level down on the reliability ladder."""
    return {"stronger": "moderate", "moderate": "weak", "weak": "weak"}[label]


def _safe_int(value: Any) -> int | None:
    """Coerce a numeric value to int; return None for None/non-numeric."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ensure_aware(value: Any) -> datetime | None:
    """Promote naive datetimes to UTC so comparisons against `now` are safe."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _covers_range(
    coverage: dict[str, Any],
    requested_start: datetime | None,
    requested_end: datetime | None,
) -> bool:
    """Return True iff the cached coverage range overlaps the requested window."""
    first = _ensure_aware(coverage.get("first_timestamp"))
    last = _ensure_aware(coverage.get("last_timestamp"))
    if first is None or last is None:
        return False
    if requested_start is not None and first > requested_start:
        return False
    return not (requested_end is not None and last < requested_end)


def _iso(value: datetime | None) -> str | None:
    """ISO-8601 with `Z` suffix when UTC; preserves None."""
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    iso = aware.astimezone(timezone.utc).isoformat()
    return iso.replace("+00:00", "Z")
