"""backtest_prediction_rule — structured prediction-market rule simulation (§10.4).

Pipeline mirrors analyze_prediction_calibration so universe selection and
backfill behave identically across calibration, longshot, and backtest
tools — preventing accidental drift on which markets each tool considers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pydantic

from ..data import (
    HistoryDownloader,
    UniverseFilters,
    build_data_coverage,
    compute_quality_flags,
    reliability_label,
    select_candidate_universe,
)
from ..engine import (
    BacktestMarket,
    StopTakeProfitExit,
    build_monte_carlo_input,
    simulate_rule,
    summarize_trades,
    trade_to_dict,
    validate_rule,
)
from ..logging_config import get_logger
from ..storage import PredictionStore, PriceRow, fingerprint_analysis, fingerprint_resolution
from .calibration import (
    _backfill_selected,
    _discover_candidates,
    _ensure_aware,
    _fetch_tokens,
    _iso,
    _load_price_rows,
    _skipped_counts,
)

logger = get_logger(__name__)

_DEFAULT_SEED = 12_345


async def backtest_prediction_rule(
    rule_payload: dict[str, Any],
    *,
    downloader: HistoryDownloader,
    store: PredictionStore,
    query: str = "",
    max_markets: int = 100,
    fidelity: int = 60,
    seed: int = _DEFAULT_SEED,
    max_history_points: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate + simulate a structured prediction-market rule.

    Args:
        rule_payload: Dict matching the V1 rule schema (§10.4).
        downloader: Backfill orchestrator (constructed by MCP wrapper).
        store: PredictionStore for read-back after backfill.
        query: Optional free-text discovery topic; otherwise listing path.
        max_markets: Hard cap on selected universe.
        fidelity: Sampled-price resolution in minutes.
        seed: Seed echoed into monte_carlo_input for downstream reproducibility.
        max_history_points: Per-token CLOB cap from settings.
        now: Optional override; defaults to current UTC.

    Returns:
        Dict matching the §15 response contract for the backtest tool,
        with sample_size, win_rate, distribution stats, examples,
        monte_carlo_input, limitations, quality_flags, reliability_label.

    Raises:
        pydantic.ValidationError: Re-raised from validate_rule for
            agent-visible feedback on unsupported fields.

    """
    rule = validate_rule(rule_payload)

    fetched_at = now or datetime.now(tz=timezone.utc)
    candidates = await _discover_candidates(
        downloader=downloader,
        query=query,
        max_markets=max_markets,
        category=rule.filters.category or "",
    )
    filters = UniverseFilters(
        category=None,
        min_lifetime_volume=rule.filters.min_lifetime_volume,
        start_date=None,
        end_date=None,
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

    backtest_markets: list[BacktestMarket] = []
    resolution_summary: dict[str, int] = {}
    examples_for_response: list[dict[str, Any]] = []
    resolution_fingerprints: list[str] = []
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
        # TTR filter moved into engine.backtest.simulate_rule so it can
        # evaluate against the candidate entry timestamp instead of the
        # tool-invocation time. For resolved markets the latter is
        # always after end_date and would skip every market.
        yes_rows = _load_yes_rows(store, condition_id, fidelity, max_timestamp=end_date_dt)
        if not yes_rows:
            resolution_summary["missing_price_history"] = (
                resolution_summary.get("missing_price_history", 0) + 1
            )
            continue
        price_rows_used_total += len(yes_rows)
        backtest_markets.append(
            BacktestMarket(
                condition_id=condition_id,
                event_slug=market_row.get("event_slug"),
                end_date=end_date_dt,
                winning_outcome_label=winning_outcome,
                yes_token_rows=yes_rows,
            )
        )
        # Append the per-market resolution fingerprint, NOT the condition_id,
        # so a re-resolved (UMA dispute) market flips data_fingerprint even
        # when the selected condition_id set is unchanged.
        resolution_fingerprints.append(fingerprint_resolution(market_row))

    simulate_skipped: dict[str, int] = {}
    trades = simulate_rule(rule, backtest_markets, out_skipped=simulate_skipped)
    for reason, count in simulate_skipped.items():
        resolution_summary[reason] = resolution_summary.get(reason, 0) + count
    summary = summarize_trades(trades)
    limitations = _limitations(rule)
    fingerprint = fingerprint_analysis(
        tool_name="backtest_prediction_rule",
        params={"rule": rule.model_dump(), "max_markets": max_markets, "fidelity": fidelity},
        universe_fingerprint=selection.fingerprint,
        resolution_fingerprints=resolution_fingerprints,
    )
    monte_carlo_input = build_monte_carlo_input(
        trades=trades,
        seed=seed,
        source_backtest_fingerprint=fingerprint,
        limitations=limitations,
    )
    examples_for_response = [trade_to_dict(t) for t in trades[:10]]

    coverage = build_data_coverage(
        markets_requested=len(candidates),
        markets_selected=len(selection.condition_ids),
        markets_with_history=backfill_summary["markets_with_history"],
        markets_excluded=selection.candidates_in - len(selection.condition_ids),
        tokens_requested=backfill_summary["tokens_seen"],
        tokens_with_history=backfill_summary["tokens_with_history"],
        price_rows_loaded=backfill_summary["price_rows_loaded"],
        price_rows_used=price_rows_used_total,
        observations_used=len(trades),
        distinct_markets_used=len({t.condition_id for t in trades}),
        coverage_start=backfill_summary["coverage_start"],
        coverage_end=backfill_summary["coverage_end"],
        skipped_reasons=_skipped_counts(selection.excluded_counts, resolution_summary),
    )
    flags = compute_quality_flags(
        coverage=coverage,
        lifetime_volume_filter_used=rule.filters.volume_filter_mode == "lifetime_static",
    )
    return {
        "tool": "backtest_prediction_rule",
        "universe": "gamma_closed_markets",
        "selected_condition_ids": selection.condition_ids,
        "filters": {
            "query": query,
            "rule": rule.model_dump(),
            "max_markets": max_markets,
            "fidelity_minutes": fidelity,
            "seed": seed,
            "volume_filter_mode": rule.filters.volume_filter_mode,
        },
        "cache_actions": backfill_summary["cache_actions"],
        "data_coverage": coverage,
        "sample_size": summary["sample_size"],
        "metrics": summary,
        "monte_carlo_input": monte_carlo_input,
        "examples": examples_for_response,
        "resolution_breakdown": resolution_summary,
        "limitations": limitations,
        "quality_flags": flags,
        "reliability_label": reliability_label(coverage, flags),
        "data_fingerprint": fingerprint,
    }


# -- helpers ------------------------------------------------------------------


def _load_yes_rows(
    store: PredictionStore,
    condition_id: str,
    fidelity: int,
    *,
    max_timestamp: datetime | None = None,
) -> list[PriceRow]:
    """Return the YES-outcome PriceRows for one market, sorted ascending.

    ``max_timestamp`` (typically the market end date) is forwarded so
    post-resolution rows cannot become eligible entries.
    """
    tokens = _fetch_tokens(store, condition_id)
    yes_token = next((tid for tid, label in tokens if label.strip().lower() == "yes"), None)
    if yes_token is None:
        return []
    return _load_price_rows(store, yes_token, condition_id, fidelity, max_timestamp=max_timestamp)


def _limitations(rule: object) -> list[str]:
    exit_rule = getattr(rule, "exit", None) if isinstance(rule, pydantic.BaseModel) else None
    if isinstance(exit_rule, StopTakeProfitExit):
        exit_line = (
            "Exit = first stop/take-profit/max-hold trigger on the sampled "
            "price track, else hold to resolution; no historical order-book "
            "depth or fees modeled."
        )
    else:
        exit_line = "Exit = hold to resolution; no historical order-book depth or fees modeled."
    out = [
        "V1 backtest: single entry per market at the earliest eligible YES price.",
        exit_line,
        "Sampled price history (CLOB), not tick-level fills.",
        "Resolved markets only; ambiguous resolutions are skipped and reported.",
        "Results are descriptive base-rate evidence, not proof of causal edge.",
    ]
    # Pydantic v2: filters.volume_filter_mode is on the validated rule.
    if isinstance(rule, pydantic.BaseModel):
        filters = getattr(rule, "filters", None)
        if (
            filters is not None
            and getattr(filters, "volume_filter_mode", "none") == "lifetime_static"
        ):
            out.append(
                "Volume filter used final/lifetime volume, not volume known at "
                "simulated entry time."
            )
        if isinstance(exit_rule, StopTakeProfitExit):
            out.extend(_stop_take_profit_limitations())
    return out


def _stop_take_profit_limitations() -> list[str]:
    """Caveats specific to the stop_take_profit exit path.

    Surfaces the three semantic concessions the engine made: intra-bucket
    blindness, exit-at-observed-sample (not trigger level), and zero
    market-impact. The agent prompt requires quoting ``limitations``
    verbatim, so these strings propagate directly to user-facing output.
    """
    return [
        (
            "Intra-bucket price paths are unobserved; stops or take-profits that "
            "fire and revert inside a single sampling interval are missed, so "
            "trigger counts under-state real triggers."
        ),
        (
            "Exit price is the sampled row price at trigger, not the trigger "
            "level — approximates a market-order fill at the sample, not a clean "
            "limit fill at the trigger price."
        ),
        ("Results ignore spread, depth, and market impact regardless of position size."),
    ]


def _drop_iso(value: datetime | None) -> str | None:
    """Re-export of _iso so static callers can use it without reaching into calibration."""
    return _iso(value)
