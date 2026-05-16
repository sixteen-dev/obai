"""Structured setup testing for prediction markets.

V1 supports descriptive event-study summaries driven by explicit
price/liquidity filters. It is not a generalized strategy engine.
"""

from __future__ import annotations

from statistics import median
from typing import Any

from ..clients.clob_client import ClobClient
from ..clients.gamma_client import GammaClient
from ..logging_config import get_logger

logger = get_logger(__name__)


def _find_yes_token(token_ids: list[Any], outcomes: list[Any]) -> str | None:
    """Return the CLOB token ID that maps to the YES outcome.

    The previous behavior assumed ``token_ids[0]`` was YES, which breaks for
    multi-outcome markets and any market where Polymarket reorders outputs.
    Pair token IDs with their declared outcome label and pick the literal
    YES; bail out if the pairing is ambiguous so the caller can skip the
    market entirely.
    """
    if len(token_ids) != len(outcomes):
        return None
    for token_id, outcome in zip(token_ids, outcomes, strict=True):
        label = str(outcome).strip().upper() if outcome is not None else ""
        if label == "YES":
            return str(token_id)
    return None


def _parse_window_seconds(window: str) -> int | None:
    """Parse compact windows like 6h, 3d, or 30m into seconds."""
    unit = window[-1:].lower()
    value = window[:-1]
    if not value.isdigit():
        return None

    multiplier = {
        "m": 60,
        "h": 60 * 60,
        "d": 24 * 60 * 60,
    }.get(unit)
    if multiplier is None:
        return None

    return int(value) * multiplier


def _coerce_timestamp(value: Any) -> int | None:
    """Normalize history timestamps to integer epoch seconds."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _find_entry_index(
    history: list[dict[str, Any]],
    price_threshold_min: float,
    price_threshold_max: float,
) -> int | None:
    """Use the first history point within the requested price band as the entry event."""
    for idx, point in enumerate(history):
        price = point.get("price")
        timestamp = _coerce_timestamp(point.get("timestamp"))
        if (
            isinstance(price, (int, float))
            and timestamp is not None
            and price_threshold_min <= float(price) <= price_threshold_max
        ):
            return idx
    return None


def _find_forward_point(
    history: list[dict[str, Any]],
    entry_index: int,
    window: str,
) -> dict[str, Any] | None:
    """Locate the forward comparison point for a requested window."""
    entry_timestamp = _coerce_timestamp(history[entry_index].get("timestamp"))
    if entry_timestamp is None:
        return None

    if window == "to_resolution":
        return history[-1] if len(history) > entry_index + 1 else None

    offset_seconds = _parse_window_seconds(window)
    if offset_seconds is None:
        return None

    target_timestamp = entry_timestamp + offset_seconds
    for point in history[entry_index + 1 :]:
        point_timestamp = _coerce_timestamp(point.get("timestamp"))
        if point_timestamp is not None and point_timestamp >= target_timestamp:
            # Attach the actual elapsed seconds so callers can detect
            # sparse-data drift (e.g., "1d" window matched a 5d gap).
            return {**point, "_actual_offset": point_timestamp - entry_timestamp}
    return None


def _summarize_window_moves(moves: list[float]) -> dict[str, Any]:
    """Aggregate directional results for a single forward window."""
    if not moves:
        return {
            "sample_size": 0,
            "avg_price_change": None,
            "median_price_change": None,
            "positive_rate": None,
        }

    positive_moves = sum(1 for move in moves if move > 0)
    return {
        "sample_size": len(moves),
        "avg_price_change": round(sum(moves) / len(moves), 4),
        "median_price_change": round(median(moves), 4),
        "positive_rate": round(positive_moves / len(moves), 4),
    }


async def backtest_prediction_setup(
    setup_description: str,
    *,
    min_volume: float = 1000,
    min_liquidity: float = 500,
    price_threshold_min: float = 0.0,
    price_threshold_max: float = 1.0,
    forward_windows: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Run a descriptive event-study over resolved markets.

    V1 evaluates structured filters only:
    - market-level volume/liquidity thresholds
    - YES price entry band
    - forward windows over historical YES price series

    The free-text setup description is kept for user context, but is
    not parsed into additional signals beyond these structured filters.
    """
    if forward_windows is None:
        forward_windows = ["24h", "72h", "to_resolution"]

    gamma = GammaClient()
    clob = ClobClient()
    try:
        markets = await gamma.list_markets(
            limit=limit,
            active=False,
            closed=True,
            order="endDate",
            ascending=False,
        )

        # Closed markets have 0 current liquidity (order book is gone),
        # so we filter on lifetime volume only.  min_liquidity is kept
        # in the tool signature for documentation but is NOT applied to
        # resolved markets — it would filter out everything.
        filtered = [m for m in markets if m.get("volume", 0) >= min_volume]

        window_moves: dict[str, list[float]] = {window: [] for window in forward_windows}
        examples: list[dict[str, Any]] = []
        resolved_yes = 0
        resolved_no = 0
        markets_with_history = 0

        for market in filtered:
            token_ids = market.get("clob_token_ids", [])
            outcomes = market.get("outcomes", [])
            if not token_ids:
                continue

            yes_token = _find_yes_token(token_ids, outcomes)
            if yes_token is None:
                # Multi-outcome or reordered markets without an explicit YES
                # token can't be priced against a YES/NO assumption — skip
                # them so the aggregate stats don't get poisoned.
                continue

            history_result = await clob.get_price_history(yes_token, interval="max", fidelity=500)
            history = history_result.get("history", [])
            if len(history) < 2:
                continue

            markets_with_history += 1
            entry_index = _find_entry_index(history, price_threshold_min, price_threshold_max)
            if entry_index is None:
                continue

            entry_point = history[entry_index]
            entry_price = float(entry_point["price"])
            entry_timestamp = _coerce_timestamp(entry_point.get("timestamp"))
            if entry_timestamp is None:
                continue

            forward_results: dict[str, dict[str, Any]] = {}
            for window in forward_windows:
                forward_point = _find_forward_point(history, entry_index, window)
                if forward_point is None:
                    continue

                forward_price = forward_point.get("price")
                if not isinstance(forward_price, (int, float)):
                    continue

                change = round(float(forward_price) - entry_price, 4)

                actual_offset = forward_point.get("_actual_offset")
                requested_offset = _parse_window_seconds(window)
                is_drifted = (
                    actual_offset is not None
                    and requested_offset is not None
                    and actual_offset > requested_offset * 2
                )

                fwd_entry: dict[str, Any] = {
                    "forward_price": round(float(forward_price), 4),
                    "forward_timestamp": _coerce_timestamp(forward_point.get("timestamp")),
                    "price_change": change,
                }

                if is_drifted and actual_offset is not None:
                    fwd_entry["actual_elapsed_hours"] = round(actual_offset / 3600, 1)
                    fwd_entry["data_drift"] = True
                else:
                    # Only clean (non-drifted) samples feed the
                    # aggregate window stats.
                    window_moves[window].append(change)

                forward_results[window] = fwd_entry

            if not forward_results:
                continue

            final_price = history[-1].get("price")
            resolution = "UNRESOLVED"
            if isinstance(final_price, (int, float)):
                if float(final_price) > 0.95:
                    resolved_yes += 1
                    resolution = "YES"
                elif float(final_price) < 0.05:
                    resolved_no += 1
                    resolution = "NO"

            examples.append(
                {
                    "question": market.get("question", ""),
                    "category": market.get("category", ""),
                    "entry_price": round(entry_price, 4),
                    "entry_timestamp": entry_timestamp,
                    "resolution": resolution,
                    "volume": market.get("volume", 0),
                    "liquidity": market.get("liquidity", 0),
                    "data_points": len(history),
                    "forward_results": forward_results,
                }
            )

        # Count data-drift instances per window
        drift_counts: dict[str, int] = {window: 0 for window in forward_windows}
        for ex in examples:
            for window, fwd in ex.get("forward_results", {}).items():
                if fwd.get("data_drift"):
                    drift_counts[window] = drift_counts.get(window, 0) + 1

        window_stats: dict[str, dict[str, Any]] = {}
        for window, moves in window_moves.items():
            stats = _summarize_window_moves(moves)
            if drift_counts.get(window, 0) > 0:
                stats["data_drift_count"] = drift_counts[window]
            window_stats[window] = stats
        total_resolved = resolved_yes + resolved_no
        yes_resolution_rate = (
            round(resolved_yes / total_resolved, 4) if total_resolved > 0 else None
        )

        warnings: list[str] = [
            "min_liquidity is not applied to resolved markets (order books "
            "are gone post-resolution and current liquidity is 0). Filtering "
            "is performed on lifetime volume only."
        ]

        return {
            "tool": "backtest_prediction_setup",
            "setup_description": setup_description,
            "warnings": warnings,
            "evaluated_setup": {
                "min_volume": min_volume,
                "min_liquidity_requested_not_applied": min_liquidity,
                "yes_entry_price_range": [price_threshold_min, price_threshold_max],
                "forward_windows": forward_windows,
            },
            "sample_size": len(examples),
            "markets_scanned": len(markets),
            "markets_after_filters": len(filtered),
            "markets_with_price_history": markets_with_history,
            "resolved_yes": resolved_yes,
            "resolved_no": resolved_no,
            "yes_resolution_rate": yes_resolution_rate,
            "window_stats": window_stats,
            "examples": examples[:10],
            "limitations": [
                "V1 evaluates structured price/liquidity filters only; "
                "setup_description is descriptive context, not a parsed rule language.",
                "Each market contributes at most one entry event: "
                "the earliest YES price point inside the requested entry band.",
                "Historical order book depth is not available, "
                "so this does not estimate fill quality or slippage.",
                "Historical volume trend conditions are not reconstructed from intraday snapshots.",
                "Price history is sampled at ~500-minute (~8-hour) intervals; "
                "short-lived price moves between samples are not captured.",
                "Results are descriptive event-study summaries, not proof of causal edge.",
            ],
        }
    finally:
        await gamma.close()
        await clob.close()
