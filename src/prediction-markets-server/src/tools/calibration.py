"""analyze_prediction_calibration — resolved-market calibration by price bucket.

Pipeline:
    1. Discover closed candidate markets via Gamma (query → public_search,
       else listing closed markets by endDate).
    2. Select universe deterministically (§9.3).
    3. Backfill metadata + price history via HistoryDownloader.
    4. Generate observations per (market, outcome) under the requested
       sampling mode.
    5. Aggregate via engine.calibration.

Response follows the §15 contract: tool, universe, selected_condition_ids,
filters, cache_actions, data_coverage, sample_size, metrics, examples,
limitations, quality_flags, reliability_label.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from ..data import (
    HistoryDownloader,
    UniverseFilters,
    build_data_coverage,
    compute_quality_flags,
    reliability_label,
    select_candidate_universe,
)
from ..engine import (
    DEFAULT_PRICE_BUCKET_SIZE,
    MarketContext,
    Observation,
    SamplingMode,
    aggregate_calibration,
    bucket_observations,
    summary_to_dict,
)
from ..logging_config import get_logger
from ..storage import PredictionStore, PriceRow

logger = get_logger(__name__)

_SUPPORTED_MODES: tuple[SamplingMode, ...] = (
    "market_bucket_once",
    "sample_weighted",
    "both",
)


async def analyze_prediction_calibration(
    *,
    downloader: HistoryDownloader,
    store: PredictionStore,
    query: str = "",
    category: str = "",
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    price_bucket_size: float = DEFAULT_PRICE_BUCKET_SIZE,
    min_lifetime_volume: float | None = None,
    max_markets: int = 100,
    fidelity: int = 60,
    sampling_mode: SamplingMode = "market_bucket_once",
    max_history_points: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run calibration over resolved markets matching the query/category filters.

    Args:
        downloader: Backfill orchestrator (constructed by the MCP wrapper).
        store: PredictionStore for read-back after backfill.
        query: Optional free-text topic (e.g. "election"); searches Gamma.
        category: Optional category match (case-insensitive).
        start_date: Optional inclusive lower bound on market end_date.
        end_date: Optional inclusive upper bound on market end_date.
        price_bucket_size: Price bucket width (default 0.05).
        min_lifetime_volume: Optional static volume filter (named contamination
            per §11.4 surfaces in limitations when supplied).
        max_markets: Hard cap on selected universe size.
        fidelity: Price-history fidelity in minutes.
        sampling_mode: market_bucket_once (default), sample_weighted, or both.
        max_history_points: Per-token CLOB cap from settings.
        now: Optional override; defaults to current UTC.

    Returns:
        Dict following the §15 response contract for calibration.

    Raises:
        ValueError: If sampling_mode is invalid or thresholds are out of
            range.

    """
    if sampling_mode not in _SUPPORTED_MODES:
        msg = f"sampling_mode must be one of {_SUPPORTED_MODES}, got {sampling_mode!r}"
        raise ValueError(msg)

    fetched_at = now or datetime.now(tz=timezone.utc)
    candidates = await _discover_candidates(
        downloader=downloader,
        query=query,
        max_markets=max_markets,
    )
    filters = UniverseFilters(
        category=category or None,
        min_lifetime_volume=min_lifetime_volume,
        start_date=start_date,
        end_date=end_date,
        require_resolved=False,  # candidates not yet resolved-tagged; we refresh + tag below
    )
    selection = select_candidate_universe(candidates, filters, max_markets)

    backfill_summary = await _backfill_selected(
        selection_ids=selection.condition_ids,
        downloader=downloader,
        fidelity=fidelity,
        max_history_points=max_history_points,
        now=fetched_at,
    )

    observations_by_mode: dict[str, list[Observation]] = {}  # mode → list[Observation]
    resolution_summary: dict[str, int] = {}
    examples: list[dict[str, Any]] = []
    price_rows_used_total = 0

    for condition_id in selection.condition_ids:
        market_row = store.get_market(condition_id)
        if market_row is None:
            continue
        method = (market_row.get("resolution_method") or "unresolved").strip()
        resolution_summary[method] = resolution_summary.get(method, 0) + 1
        if market_row.get("resolution_status") != "resolved":
            continue
        winning_outcome = (market_row.get("winning_outcome") or "").strip()
        if not winning_outcome:
            continue
        end_date_dt = _ensure_aware(market_row.get("end_date"))
        if end_date_dt is None:
            continue
        ctx = MarketContext(
            condition_id=condition_id,
            end_date=end_date_dt,
            winning_outcome_label=winning_outcome,
        )
        tokens = _fetch_tokens(store, condition_id)
        outcome_obs, market_rows_used = _observations_for_market(
            store=store,
            ctx=ctx,
            tokens=tokens,
            fidelity=fidelity,
            sampling_mode=sampling_mode,
            price_bucket_size=price_bucket_size,
        )
        price_rows_used_total += market_rows_used
        for mode_name, obs in outcome_obs.items():
            observations_by_mode.setdefault(mode_name, []).extend(obs)
        if outcome_obs and len(examples) < 10:
            examples.append(_example_record(market_row, outcome_obs))

    metrics = _aggregate_modes(observations_by_mode, sampling_mode)

    distinct_markets_used = max(
        (summary["market_count"] for summary in metrics.values()), default=0
    )
    observations_used = max((summary["sample_size"] for summary in metrics.values()), default=0)

    coverage = build_data_coverage(
        markets_requested=len(candidates),
        markets_selected=len(selection.condition_ids),
        markets_with_history=backfill_summary["markets_with_history"],
        markets_excluded=selection.candidates_in - len(selection.condition_ids),
        tokens_requested=backfill_summary["tokens_seen"],
        tokens_with_history=backfill_summary["tokens_with_history"],
        price_rows_loaded=backfill_summary["price_rows_loaded"],
        price_rows_used=price_rows_used_total,
        observations_used=observations_used,
        distinct_markets_used=distinct_markets_used,
        coverage_start=backfill_summary["coverage_start"],
        coverage_end=backfill_summary["coverage_end"],
        skipped_reasons=_skipped_counts(selection.excluded_counts, resolution_summary),
    )
    flags = compute_quality_flags(
        coverage=coverage,
        lifetime_volume_filter_used=min_lifetime_volume is not None,
        sparse_short_horizon=_short_horizon_sparse(metrics),
    )
    return {
        "tool": "analyze_prediction_calibration",
        "universe": "gamma_closed_markets",
        "selected_condition_ids": selection.condition_ids,
        "filters": {
            "query": query,
            "category": category,
            "start_date": _iso(start_date),
            "end_date": _iso(end_date),
            "price_bucket_size": price_bucket_size,
            "min_lifetime_volume": min_lifetime_volume,
            "max_markets": max_markets,
            "fidelity_minutes": fidelity,
            "sampling_mode": sampling_mode,
            "volume_filter_mode": (
                "lifetime_static" if min_lifetime_volume is not None else "none"
            ),
        },
        "cache_actions": backfill_summary["cache_actions"],
        "data_coverage": coverage,
        "sample_size": observations_used,
        "metrics": metrics,
        "examples": examples,
        "resolution_breakdown": resolution_summary,
        "limitations": _limitations(min_lifetime_volume),
        "quality_flags": flags,
        "reliability_label": reliability_label(coverage, flags),
    }


# -- discovery + backfill ----------------------------------------------------


async def _discover_candidates(
    *,
    downloader: HistoryDownloader,
    query: str,
    max_markets: int,
) -> list[dict[str, Any]]:
    """Fetch closed market candidates, with a query path and a listing path."""
    capped_limit = max(min(max_markets * 2, 500), 1)  # oversample before filtering
    if query.strip():
        result = await downloader.gamma.public_search(
            query=query.strip(),
            limit_per_type=capped_limit,
            events_status="closed",
        )
        events = result.get("events", []) if isinstance(result, dict) else []
        markets: list[dict[str, Any]] = []
        for event in events:
            for market in event.get("markets", []) if isinstance(event, dict) else []:
                if isinstance(market, dict):
                    markets.append(market)
        return markets
    return await downloader.gamma.list_markets(
        limit=capped_limit,
        active=False,
        closed=True,
        order="endDate",
        ascending=False,
    )


async def _backfill_selected(
    *,
    selection_ids: list[str],
    downloader: HistoryDownloader,
    fidelity: int,
    max_history_points: int,
    now: datetime,
) -> dict[str, Any]:
    """Refresh every selected market + its price history. Aggregates cache actions."""
    cache_actions: dict[str, Any] = {
        "markets": {"action": "refreshed", "count": 0},
        "price_history": {},
    }
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None
    tokens_seen = 0
    tokens_with_history = 0
    markets_with_history = 0
    price_rows_loaded = 0

    for condition_id in selection_ids:
        try:
            market_result = await downloader.ensure_market(condition_id, now=now)
        except (ValueError, KeyError):
            continue
        cache_actions["markets"]["count"] += 1
        market_has_rows = False
        for token in market_result.tokens:
            tokens_seen += 1
            price_result = await downloader.ensure_price_history(
                token_id=token.token_id,
                condition_id=condition_id,
                fidelity_minutes=fidelity,
                interval="max",
                now=now,
                max_history_points=max_history_points,
            )
            cache_actions["price_history"][token.token_id] = {
                "action": price_result.cache_action,
                "source": price_result.source,
                "fidelity_minutes": price_result.fidelity_minutes,
                "rows": price_result.rows_written,
                "coverage_start": _iso(price_result.coverage_start),
                "coverage_end": _iso(price_result.coverage_end),
            }
            if price_result.coverage_start is not None:
                coverage_start = _min_dt(coverage_start, price_result.coverage_start)
            if price_result.coverage_end is not None:
                coverage_end = _max_dt(coverage_end, price_result.coverage_end)
            row_count = price_result.rows_written or _existing_row_count(
                downloader.store, token.token_id
            )
            price_rows_loaded += row_count
            if row_count > 0:
                tokens_with_history += 1
                market_has_rows = True
        if market_has_rows:
            markets_with_history += 1

    return {
        "cache_actions": cache_actions,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "tokens_seen": tokens_seen,
        "tokens_with_history": tokens_with_history,
        "markets_with_history": markets_with_history,
        "price_rows_loaded": price_rows_loaded,
    }


def _existing_row_count(store: PredictionStore, token_id: str) -> int:
    """Quick read of existing PriceRow count for cached tokens."""
    row = store.manager.conn.execute(
        "SELECT count(*) FROM pm_price_history WHERE token_id = ?",
        [token_id],
    ).fetchone()
    return int(row[0]) if row is not None else 0


# -- observation helpers ------------------------------------------------------


def _fetch_tokens(store: PredictionStore, condition_id: str) -> list[tuple[str, str]]:
    """(token_id, outcome_label) tuples for one market, sorted by outcome_index."""
    rows = store.manager.conn.execute(
        "SELECT token_id, outcome_label FROM pm_tokens "
        "WHERE condition_id = ? ORDER BY outcome_index ASC",
        [condition_id],
    ).fetchall()
    return [(str(r[0]), str(r[1])) for r in rows]


def _observations_for_market(
    *,
    store: PredictionStore,
    ctx: MarketContext,
    tokens: list[tuple[str, str]],
    fidelity: int,
    sampling_mode: SamplingMode,
    price_bucket_size: float,
) -> tuple[dict[str, list[Observation]], int]:
    """Materialize observations per mode for one market.

    Returns a (mode → list[Observation], rows_used) tuple. ``rows_used``
    is the count of price rows that survived the post-resolution filter
    (so the data_coverage metric does not overstate the "rows used"
    number with the pre-filter backfill total).
    """
    modes_to_run: tuple[SamplingMode, ...] = (
        ("market_bucket_once", "sample_weighted") if sampling_mode == "both" else (sampling_mode,)
    )
    out: dict[str, list[Observation]] = {mode: [] for mode in modes_to_run}
    rows_used = 0
    for token_id, outcome_label in tokens:
        rows = _load_price_rows(
            store, token_id, ctx.condition_id, fidelity, max_timestamp=ctx.end_date
        )
        if not rows:
            continue
        rows_used += len(rows)
        for mode in modes_to_run:
            obs = bucket_observations(
                market=ctx,
                outcome_label=outcome_label,
                rows=rows,
                sampling_mode=mode,
                price_bucket_size=price_bucket_size,
            )
            out[mode].extend(obs)
    return out, rows_used


def _load_price_rows(
    store: PredictionStore,
    token_id: str,
    condition_id: str,
    fidelity: int,
    *,
    max_timestamp: datetime | None = None,
) -> list[PriceRow]:
    """Read PriceRows for a (token, fidelity) ordered by timestamp.

    When ``max_timestamp`` is supplied (typically the market's end_date),
    rows whose timestamp exceeds it are filtered out at the DB layer so
    post-resolution / terminal-price samples cannot leak into calibration
    or backtest observation generation.
    """
    if max_timestamp is None:
        rows = store.manager.conn.execute(
            """
            SELECT token_id, condition_id, timestamp, price, fidelity_minutes, source,
                   fetched_at
            FROM pm_price_history
            WHERE token_id = ? AND fidelity_minutes = ?
            ORDER BY timestamp ASC
            """,
            [token_id, fidelity],
        ).fetchall()
    else:
        rows = store.manager.conn.execute(
            """
            SELECT token_id, condition_id, timestamp, price, fidelity_minutes, source,
                   fetched_at
            FROM pm_price_history
            WHERE token_id = ? AND fidelity_minutes = ? AND timestamp <= ?
            ORDER BY timestamp ASC
            """,
            [token_id, fidelity, max_timestamp],
        ).fetchall()
    return [
        PriceRow(
            token_id=str(r[0]),
            condition_id=str(r[1]),
            timestamp=_ensure_aware(r[2]) or datetime.fromtimestamp(0, tz=timezone.utc),
            price=float(r[3]),
            fidelity_minutes=int(r[4]),
            source=str(r[5]),
            fetched_at=_ensure_aware(r[6]) or datetime.fromtimestamp(0, tz=timezone.utc),
        )
        for r in rows
        if r[2] is not None and r[3] is not None
    ]


def _aggregate_modes(
    observations_by_mode: dict[str, list[Observation]],
    requested: SamplingMode,
) -> dict[str, Any]:
    """Aggregate one or both modes; key the result by mode name."""
    if requested != "both":
        obs = observations_by_mode.get(requested, [])
        return {requested: summary_to_dict(aggregate_calibration(obs, sampling_mode=requested))}
    modes: tuple[SamplingMode, ...] = ("market_bucket_once", "sample_weighted")
    return {
        mode: summary_to_dict(
            aggregate_calibration(observations_by_mode.get(mode, []), sampling_mode=mode)
        )
        for mode in modes
    }


# -- helpers ------------------------------------------------------------------


def _skipped_counts(
    selection_excluded: dict[str, int],
    resolution_summary: dict[str, int],
) -> dict[str, int]:
    """Merge universe-selection exclusion counts with resolution skip counts."""
    out: dict[str, int] = dict(selection_excluded)
    ambiguous = resolution_summary.get("ambiguous", 0) + resolution_summary.get("unresolved", 0)
    if ambiguous:
        out["ambiguous_resolution"] = out.get("ambiguous_resolution", 0) + ambiguous
    return dict(sorted(out.items()))


def _short_horizon_sparse(metrics: dict[str, Any]) -> bool:
    """Return True when any short-TTR bucket has fewer than 5 observations."""
    for summary in metrics.values():
        for bucket in summary.get("buckets", []):
            if bucket["ttr_bucket"] in {"0_3h", "3_6h"} and bucket["sample_size"] < 5:
                return True
    return False


def _limitations(min_lifetime_volume: float | None) -> list[str]:
    out = [
        "sampled price history, not tick-level trades",
        "no historical order-book depth",
        "resolved markets only",
        "market category inferred from tags/title",
    ]
    if min_lifetime_volume is not None:
        out.append(
            "final/lifetime volume used as a static universe filter when min_lifetime_volume is set"
        )
    return out


def _ensure_aware(value: Any) -> datetime | None:
    """Promote naive datetimes to UTC for safe comparisons."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _min_dt(current: datetime | None, candidate: datetime) -> datetime:
    if current is None:
        return candidate
    return min(current, candidate)


def _max_dt(current: datetime | None, candidate: datetime) -> datetime:
    if current is None:
        return candidate
    return max(current, candidate)


def _example_record(
    market_row: dict[str, Any], outcome_obs: dict[str, list[Observation]]
) -> dict[str, Any]:
    """Compact example used in tool response."""
    return {
        "condition_id": market_row.get("condition_id"),
        "slug": market_row.get("slug"),
        "question": market_row.get("question"),
        "winning_outcome": market_row.get("winning_outcome"),
        "resolution_method": market_row.get("resolution_method"),
        "observation_counts": {mode: len(obs) for mode, obs in outcome_obs.items()},
    }


def _iter_outcomes(modes: Iterable[str], obs_map: dict[str, list[Observation]]) -> list[str]:
    """Return mode names present in obs_map (helper kept for test introspection)."""
    return [m for m in modes if obs_map.get(m)]
