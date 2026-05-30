"""analyze_longshot_bias — longshot vs favorite analysis over resolved markets.

Shares the discovery + backfill + observation pipeline with
analyze_prediction_calibration so the two tools cannot disagree on universe
or per-market observation generation. The aggregation step is the
longshot-specific divergence.

Default sampling_mode is ``market_bucket_once`` for the same reason as
calibration: long-lived markets should not silently dominate the tail
samples.
"""

from __future__ import annotations

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
    HoldoutSpec,
    MarketContext,
    Observation,
    Side,
    bucket_observations,
    build_out_of_sample,
    evaluate_longshot_bias,
    result_to_dict,
)
from ..logging_config import get_logger
from ..storage import PredictionStore
from .calibration import (
    _backfill_selected,
    _discover_candidates,
    _ensure_aware,
    _fetch_tokens,
    _iso,
    _limitations,
    _load_price_rows,
    _skipped_counts,
)

logger = get_logger(__name__)


async def analyze_longshot_bias(
    *,
    downloader: HistoryDownloader,
    store: PredictionStore,
    query: str = "",
    category: str = "",
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    longshot_max_price: float = 0.10,
    favorite_min_price: float = 0.90,
    side: Side = "yes",
    min_lifetime_volume: float | None = None,
    max_markets: int = 100,
    fidelity: int = 60,
    price_bucket_size: float = DEFAULT_PRICE_BUCKET_SIZE,
    holdout_fraction: float | None = None,
    holdout_split_at: datetime | None = None,
    max_history_points: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate longshot vs favorite realized win rates over resolved markets.

    Args:
        downloader: Backfill orchestrator (constructed by MCP wrapper).
        store: PredictionStore for read-back after backfill.
        query: Optional free-text topic.
        category: Optional category filter (case-insensitive).
        start_date: Optional inclusive lower bound on market end_date.
        end_date: Optional inclusive upper bound on market end_date.
        longshot_max_price: Strict upper bound for the longshot tail.
        favorite_min_price: Inclusive lower bound for the favorite tail.
        side: "yes", "no", or "both" (carried into response only in V1).
        min_lifetime_volume: Optional static volume filter (limitation
            surfaces in the response when used).
        max_markets: Hard cap on selected universe.
        fidelity: Price-history fidelity in minutes.
        price_bucket_size: Width for the per-bucket detail breakdown.
        holdout_fraction: Optional out-of-sample split (§11.5); latest
            ``fraction`` of observations become holdout. Mutually exclusive
            with ``holdout_split_at``. Unset → no split.
        holdout_split_at: Optional explicit out-of-sample cutoff timestamp.
        max_history_points: Per-token CLOB cap from settings.
        now: Optional override.

    Returns:
        Dict following the §15 response contract for longshot bias.

    """
    fetched_at = now or datetime.now(tz=timezone.utc)
    candidates = await _discover_candidates(
        downloader=downloader,
        query=query,
        max_markets=max_markets,
        category=category,
        start_date=start_date,
        end_date=end_date,
    )
    filters = UniverseFilters(
        category=None,
        min_lifetime_volume=min_lifetime_volume,
        start_date=start_date,
        end_date=end_date,
        require_resolved=False,
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

    observations: list[Observation] = []
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
        market_obs: list[Observation] = []
        for token_id, outcome_label in tokens:
            rows = _load_price_rows(
                store, token_id, ctx.condition_id, fidelity, max_timestamp=ctx.end_date
            )
            if not rows:
                continue
            price_rows_used_total += len(rows)
            market_obs.extend(
                bucket_observations(
                    market=ctx,
                    outcome_label=outcome_label,
                    rows=rows,
                    sampling_mode="market_bucket_once",
                    price_bucket_size=price_bucket_size,
                )
            )
        observations.extend(market_obs)
        if market_obs and len(examples) < 10:
            examples.append(
                {
                    "condition_id": market_row.get("condition_id"),
                    "slug": market_row.get("slug"),
                    "question": market_row.get("question"),
                    "winning_outcome": market_row.get("winning_outcome"),
                    "observation_count": len(market_obs),
                }
            )

    result = evaluate_longshot_bias(
        observations,
        longshot_max_price=longshot_max_price,
        favorite_min_price=favorite_min_price,
        side=side,
    )
    out_of_sample = _longshot_out_of_sample(
        observations,
        longshot_max_price=longshot_max_price,
        favorite_min_price=favorite_min_price,
        side=side,
        spec=HoldoutSpec(fraction=holdout_fraction, cutoff=holdout_split_at),
    )
    distinct_markets_used = len({o.condition_id for o in observations})
    coverage = build_data_coverage(
        markets_requested=len(candidates),
        markets_selected=len(selection.condition_ids),
        markets_with_history=backfill_summary["markets_with_history"],
        markets_excluded=selection.candidates_in - len(selection.condition_ids),
        tokens_requested=backfill_summary["tokens_seen"],
        tokens_with_history=backfill_summary["tokens_with_history"],
        price_rows_loaded=backfill_summary["price_rows_loaded"],
        price_rows_used=price_rows_used_total,
        observations_used=len(observations),
        distinct_markets_used=distinct_markets_used,
        coverage_start=backfill_summary["coverage_start"],
        coverage_end=backfill_summary["coverage_end"],
        skipped_reasons=_skipped_counts(selection.excluded_counts, resolution_summary),
    )
    flags = compute_quality_flags(
        coverage=coverage,
        lifetime_volume_filter_used=min_lifetime_volume is not None,
    )
    response: dict[str, Any] = {
        "tool": "analyze_longshot_bias",
        "universe": "gamma_closed_markets",
        "selected_condition_ids": selection.condition_ids,
        "filters": {
            "query": query,
            "category": category,
            "start_date": _iso(start_date),
            "end_date": _iso(end_date),
            "longshot_max_price": longshot_max_price,
            "favorite_min_price": favorite_min_price,
            "side": side,
            "min_lifetime_volume": min_lifetime_volume,
            "max_markets": max_markets,
            "fidelity_minutes": fidelity,
            "price_bucket_size": price_bucket_size,
            "volume_filter_mode": (
                "lifetime_static" if min_lifetime_volume is not None else "none"
            ),
        },
        "cache_actions": backfill_summary["cache_actions"],
        "data_coverage": coverage,
        "sample_size": len(observations),
        "metrics": result_to_dict(result),
        "examples": examples,
        "resolution_breakdown": resolution_summary,
        "limitations": _limitations(min_lifetime_volume),
        "quality_flags": flags,
        "reliability_label": reliability_label(coverage, flags),
    }
    if out_of_sample is not None:
        response["out_of_sample"] = out_of_sample
    return response


def _longshot_out_of_sample(
    observations: list[Observation],
    *,
    longshot_max_price: float,
    favorite_min_price: float,
    side: Side,
    spec: HoldoutSpec,
) -> dict[str, Any] | None:
    """Build the §11.5 out_of_sample block for longshot, or None when off.

    Observations are already ``market_bucket_once`` (one per market), so the
    split units are independent. Deltas the per-tail ``excess_return``.
    """
    if not spec.engaged:
        return None
    return build_out_of_sample(
        observations,
        key=lambda obs: obs.observation_ts,
        market_key=lambda obs: obs.condition_id,
        spec=spec,
        aggregate=lambda group: result_to_dict(
            evaluate_longshot_bias(
                group,
                longshot_max_price=longshot_max_price,
                favorite_min_price=favorite_min_price,
                side=side,
            )
        ),
        delta=_longshot_oos_delta,
    )


def _longshot_oos_delta(holdout: dict[str, Any], train: dict[str, Any]) -> dict[str, Any]:
    """Signed holdout − train delta on the longshot/favorite tail excess_return."""
    return {
        tail: {
            "excess_return": round(holdout[tail]["excess_return"] - train[tail]["excess_return"], 6)
        }
        for tail in ("longshot", "favorite")
    }
