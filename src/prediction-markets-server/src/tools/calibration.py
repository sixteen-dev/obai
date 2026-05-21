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

import asyncio
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

# CLOB fetches are network-bound; parallelize per-token with a small
# semaphore so wide windows finish well under the MCP timeout. Five is a
# Bound on per-token CLOB fetch concurrency. The endpoint is a public
# read path with no observed per-IP rate limit at our volume; raising
# from 5 → 20 cuts cold-cache backfill latency ~4x without tripping
# throttling.
_BACKFILL_CONCURRENCY = 20


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
    categories: list[str] | None = None,
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
        category: Optional single-category filter (case-insensitive).
        categories: Optional list of categories to compare side-by-side.
            Mutually exclusive with ``category``. The tool fans out one
            calibration per category and returns a ``per_category`` block
            instead of the single-category response shape, so cross-category
            results cannot be silently merged into one.
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
        Dict following the §15 response contract for calibration. When
        ``categories`` is supplied, returns the per-category dispatch
        shape (``tool``, ``categories``, ``per_category``).

    Raises:
        ValueError: If sampling_mode is invalid, thresholds are out of
            range, or both ``category`` and ``categories`` are supplied.

    """
    if sampling_mode not in _SUPPORTED_MODES:
        msg = f"sampling_mode must be one of {_SUPPORTED_MODES}, got {sampling_mode!r}"
        raise ValueError(msg)
    if categories is not None and category:
        msg = "Pass either `category` or `categories`, not both."
        raise ValueError(msg)
    if categories is not None:
        return await _run_per_category(
            downloader=downloader,
            store=store,
            query=query,
            categories=categories,
            start_date=start_date,
            end_date=end_date,
            price_bucket_size=price_bucket_size,
            min_lifetime_volume=min_lifetime_volume,
            max_markets=max_markets,
            fidelity=fidelity,
            sampling_mode=sampling_mode,
            max_history_points=max_history_points,
            now=now,
        )

    fetched_at = now or datetime.now(tz=timezone.utc)
    candidates = await _discover_candidates(
        downloader=downloader,
        query=query,
        max_markets=max_markets,
        category=category,
        start_date=start_date,
        end_date=end_date,
    )
    # Gamma's market.category is not reliable for historical closed-market
    # discovery: listing returns values like "US-current-affairs", while
    # public_search often returns None on nested markets. Push category to
    # Gamma as tag_slug/events_tag in _discover_candidates and do not repeat
    # an exact client-side category check here.
    filters = UniverseFilters(
        category=None,
        min_lifetime_volume=min_lifetime_volume,
        start_date=start_date,
        end_date=end_date,
        require_resolved=False,  # candidates not yet resolved-tagged; we refresh + tag below
    )
    selection = select_candidate_universe(candidates, filters, max_markets)
    payloads_by_cid: dict[str, dict[str, Any]] = {
        (c.get("condition_id") or "").strip(): c
        for c in candidates
        if (c.get("condition_id") or "").strip()
    }

    backfill_summary = await _backfill_selected(
        selection_ids=selection.condition_ids,
        payloads_by_cid=payloads_by_cid,
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
    universe_composition = _build_universe_composition(
        store=store,
        selected_condition_ids=selection.condition_ids,
        metrics=metrics,
    )
    return {
        "tool": "analyze_prediction_calibration",
        "universe": "gamma_closed_markets",
        "universe_composition": universe_composition,
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


def _build_universe_composition(
    *,
    store: PredictionStore,
    selected_condition_ids: list[str],
    metrics: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Summarize what landed in the selected universe.

    Surfaces event-slug concentration and the TTR distribution across all
    observation rows. Both are needed so the caller can tell when a result
    is degenerate (one event dominating, or every observation in the same
    TTR bucket) before quoting numbers.
    """
    event_counts: dict[str, int] = {}
    for cid in selected_condition_ids:
        row = store.get_market(cid)
        if row is None:
            continue
        slug = (row.get("event_slug") or "").strip() or "__unknown__"
        event_counts[slug] = event_counts.get(slug, 0) + 1

    ttr_totals: dict[str, int] = {}
    seen_mode: dict[str, Any] | None = None
    for mode_summary in metrics.values():
        if not isinstance(mode_summary, dict):
            continue
        seen_mode = mode_summary
        for bucket in mode_summary.get("buckets") or []:
            label = bucket.get("ttr_bucket") or "__unknown__"
            ttr_totals[label] = ttr_totals.get(label, 0) + int(bucket.get("sample_size") or 0)
        break  # one mode is enough for the distribution shape

    return {
        "distinct_event_slugs": len(event_counts),
        "event_slug_breakdown": dict(sorted(event_counts.items(), key=lambda kv: -kv[1])),
        "ttr_bucket_distribution": ttr_totals,
        "ttr_strata_present": sum(1 for v in ttr_totals.values() if v > 0),
        "sampling_mode_observed": (seen_mode or {}).get("sampling_mode"),
    }


async def _run_per_category(
    *,
    downloader: HistoryDownloader,
    store: PredictionStore,
    query: str,
    categories: list[str],
    start_date: datetime | None,
    end_date: datetime | None,
    price_bucket_size: float,
    min_lifetime_volume: float | None,
    max_markets: int,
    fidelity: int,
    sampling_mode: SamplingMode,
    max_history_points: int,
    now: datetime | None,
) -> dict[str, Any]:
    """Fan out one calibration per category and return a per_category block.

    Runs the per-category calibrations concurrently. The CLOB fetch
    semaphore in ``_backfill_selected`` already bounds total network
    concurrency, so adding outer parallelism here speeds up the wall time
    without breaching Polymarket's effective rate budget.
    """
    cleaned = [c.strip() for c in categories if c and c.strip()]
    if not cleaned:
        msg = "`categories` must contain at least one non-empty value."
        raise ValueError(msg)

    async def _one(cat: str) -> dict[str, Any]:
        return await analyze_prediction_calibration(
            downloader=downloader,
            store=store,
            query=query,
            category=cat,
            start_date=start_date,
            end_date=end_date,
            price_bucket_size=price_bucket_size,
            min_lifetime_volume=min_lifetime_volume,
            max_markets=max_markets,
            fidelity=fidelity,
            sampling_mode=sampling_mode,
            max_history_points=max_history_points,
            now=now,
        )

    results = await asyncio.gather(*(_one(cat) for cat in cleaned))
    return {
        "tool": "analyze_prediction_calibration",
        "universe": "gamma_closed_markets",
        "categories": cleaned,
        "per_category": dict(zip(cleaned, results, strict=True)),
    }


# -- discovery + backfill ----------------------------------------------------


async def _discover_candidates(
    *,
    downloader: HistoryDownloader,
    query: str,
    max_markets: int,
    category: str = "",
    start_date: datetime | None = None,
    end_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """Fetch closed market candidates, with a query path and a listing path.

    Pushes the user-supplied ``category`` to Gamma as a ``tag_slug`` filter
    and the date window as ``end_date_min``/``end_date_max``. The Gamma
    listing endpoint does not populate per-market category/tags on closed
    markets, so server-side filtering is the only reliable path.
    """
    capped_limit = max(min(max_markets * 2, 500), 1)  # oversample before filtering
    if query.strip():
        result = await downloader.gamma.public_search(
            query=query.strip(),
            limit_per_type=capped_limit,
            events_status="closed",
            events_tag=[category.strip().lower()] if category.strip() else None,
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
        end_date_min=start_date.date().isoformat() if start_date is not None else "",
        end_date_max=end_date.date().isoformat() if end_date is not None else "",
        tag_slug=category.strip().lower(),
    )


async def _backfill_selected(
    *,
    selection_ids: list[str],
    payloads_by_cid: dict[str, dict[str, Any]],
    downloader: HistoryDownloader,
    fidelity: int,
    max_history_points: int,
    now: datetime,
) -> dict[str, Any]:
    """Refresh every selected market + its price history. Aggregates cache actions.

    Uses the candidate payloads already returned by Gamma instead of
    re-fetching by condition_id — Gamma's /markets condition_id filter is
    broken (silently returns the wrong market or none), so the per-market
    refetch was previously dropping the entire selection on the floor.
    """
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

    # Phase 1: persist market metadata sequentially (DuckDB-bound, fast).
    market_jobs: list[tuple[str, Any]] = []  # (condition_id, TokenRow)
    for condition_id in selection_ids:
        payload = payloads_by_cid.get(condition_id)
        if payload is None:
            continue
        try:
            market_result = await downloader.ensure_market_from_payload(payload, now=now)
        except (ValueError, KeyError):
            continue
        cache_actions["markets"]["count"] += 1
        for token in market_result.tokens:
            market_jobs.append((condition_id, token))

    # Phase 2: fan out per-token CLOB fetches under a bounded semaphore.
    sem = asyncio.Semaphore(_BACKFILL_CONCURRENCY)

    async def _fetch_one(cid: str, token: Any) -> tuple[str, str, Any]:
        async with sem:
            result = await downloader.ensure_price_history(
                token_id=token.token_id,
                condition_id=cid,
                fidelity_minutes=fidelity,
                interval="max",
                now=now,
                max_history_points=max_history_points,
            )
            return cid, token.token_id, result

    price_results = await asyncio.gather(*(_fetch_one(cid, tok) for cid, tok in market_jobs))

    # Phase 3: aggregate stats and per-market history bookkeeping serially.
    tokens_seen = len(market_jobs)
    markets_with_rows: set[str] = set()
    for cid, token_id, price_result in price_results:
        cache_actions["price_history"][token_id] = {
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
        row_count = price_result.rows_written or _existing_row_count(downloader.store, token_id)
        price_rows_loaded += row_count
        if row_count > 0:
            tokens_with_history += 1
            markets_with_rows.add(cid)
    markets_with_history = len(markets_with_rows)

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
