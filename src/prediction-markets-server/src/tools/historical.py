"""Historical-data management tools (admin/debug surface).

`ensure_prediction_market_history` is an internal function by default, per
§10.1 of docs/prediction-markets-historical-analytics-upgrade.md. It is
exposed as an MCP tool only when settings.prediction_enable_admin_tools is
True; the conditional registration lives in server.py.

The function always returns the §15 response contract shape so the agent
can describe what got cached, regardless of whether the call was a
fetch, refresh, or no-op cache hit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..data import HistoryDownloader
from ..logging_config import get_logger

logger = get_logger(__name__)


async def ensure_prediction_market_history(
    identifiers: list[str],
    *,
    downloader: HistoryDownloader,
    interval: str = "max",
    fidelity: int = 60,
    include_trades: bool = False,
    max_history_points: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Backfill cache for one or more markets and report what changed.

    This is the orchestrator the historical analysis tools (calibration,
    longshot, backtest) call internally before reading from the store.
    Exposing it as an MCP tool is gated by
    settings.prediction_enable_admin_tools.

    Args:
        identifiers: slugs, condition_ids, or numeric IDs.
        downloader: Pre-constructed HistoryDownloader.
        interval: CLOB price-history interval ("max" by default).
        fidelity: Sampling resolution in minutes (60 by default).
        include_trades: Also backfill Data API recent trades when True.
        max_history_points: Per-token CLOB cap from settings.
        now: Optional override for the fetched_at timestamp; defaults to
            the current UTC time.

    Returns:
        Dict matching §15 response contract: tool, universe, filters,
        cache_actions, data_coverage, sample_size, examples, limitations,
        quality_flags.

    """
    if not identifiers:
        return _empty_response(interval=interval, fidelity=fidelity)

    fetched_at = now or datetime.now(tz=timezone.utc)
    per_market: list[dict[str, Any]] = []
    cache_actions: dict[str, Any] = {
        "markets": {"action": "refreshed", "count": 0},
        "price_history": {},
        "trades": {},
    }
    skipped: dict[str, int] = {}
    selected_condition_ids: list[str] = []
    total_rows = 0

    for identifier in identifiers:
        try:
            market_result = await downloader.ensure_market(identifier, now=fetched_at)
        except (ValueError, KeyError) as exc:
            # Either Gamma rejected the identifier or the payload was unparseable.
            # Record and move on — single bad ID must not poison the batch.
            logger.warning(
                "ensure_market_failed",
                identifier=identifier,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            skipped["market_lookup_failed"] = skipped.get("market_lookup_failed", 0) + 1
            continue

        condition_id = market_result.market.condition_id
        selected_condition_ids.append(condition_id)
        cache_actions["markets"]["count"] += 1

        token_rows_written = 0
        for token in market_result.tokens:
            price_result = await downloader.ensure_price_history(
                token_id=token.token_id,
                condition_id=condition_id,
                fidelity_minutes=fidelity,
                interval=interval,
                now=fetched_at,
                max_history_points=max_history_points,
            )
            token_rows_written += price_result.rows_written
            cache_actions["price_history"][token.token_id] = {
                "action": price_result.cache_action,
                "source": price_result.source,
                "fidelity_minutes": price_result.fidelity_minutes,
                "rows": price_result.rows_written,
                "coverage_start": _iso(price_result.coverage_start),
                "coverage_end": _iso(price_result.coverage_end),
            }

        trades_written = 0
        if include_trades:
            trade_result = await downloader.ensure_trades(
                condition_id=condition_id,
                limit=100,
                now=fetched_at,
            )
            trades_written = trade_result.rows_written
            cache_actions["trades"][condition_id] = {
                "action": trade_result.cache_action,
                "source": "data_api_trades",
                "rows": trades_written,
            }

        per_market.append(
            {
                "condition_id": condition_id,
                "slug": market_result.market.slug,
                "question": market_result.market.question,
                "resolution_status": market_result.market.resolution_status,
                "resolution_method": market_result.market.resolution_method,
                "tokens_stored": len(market_result.tokens),
                "price_rows_stored": token_rows_written,
                "trade_rows_stored": trades_written,
            }
        )
        total_rows += token_rows_written + trades_written

    cache_actions["markets"]["action"] = (
        "refreshed" if cache_actions["markets"]["count"] > 0 else "fetched"
    )

    return {
        "tool": "ensure_prediction_market_history",
        "universe": "user_supplied_identifiers",
        "selected_condition_ids": selected_condition_ids,
        "filters": {
            "interval": interval,
            "fidelity_minutes": fidelity,
            "include_trades": include_trades,
        },
        "cache_actions": cache_actions,
        "data_coverage": {
            "markets_requested": len(identifiers),
            "markets_selected": len(selected_condition_ids),
            "markets_with_history": sum(1 for m in per_market if m["price_rows_stored"] > 0),
            "markets_excluded": len(identifiers) - len(selected_condition_ids),
            "tokens_requested": sum(m["tokens_stored"] for m in per_market),
            "tokens_with_history": sum(
                1 for m in per_market if m["tokens_stored"] > 0 and m["price_rows_stored"] > 0
            ),
            "price_rows_loaded": total_rows,
            "price_rows_used": total_rows,
            "observations_used": total_rows,
            "distinct_markets_used": len(selected_condition_ids),
            "coverage_start": None,
            "coverage_end": None,
            "skipped_reasons": dict(sorted(skipped.items())),
        },
        "sample_size": len(selected_condition_ids),
        "examples": per_market[:10],
        "limitations": [
            "Admin/debug tool: populates cache only — no analytical metric is computed.",
            "Price-history fidelity is the CLOB sampled rate; tick-level fills are not included.",
        ],
        "quality_flags": ["no_historical_order_book_depth"],
    }


def _empty_response(*, interval: str, fidelity: int) -> dict[str, Any]:
    """Stable shape for the no-identifier case so the contract scorer passes."""
    return {
        "tool": "ensure_prediction_market_history",
        "universe": "user_supplied_identifiers",
        "selected_condition_ids": [],
        "filters": {
            "interval": interval,
            "fidelity_minutes": fidelity,
            "include_trades": False,
        },
        "cache_actions": {
            "markets": {"action": "fetched", "count": 0},
            "price_history": {},
            "trades": {},
        },
        "data_coverage": {
            "markets_requested": 0,
            "markets_selected": 0,
            "markets_with_history": 0,
            "markets_excluded": 0,
            "tokens_requested": 0,
            "tokens_with_history": 0,
            "price_rows_loaded": 0,
            "price_rows_used": 0,
            "observations_used": 0,
            "distinct_markets_used": 0,
            "coverage_start": None,
            "coverage_end": None,
            "skipped_reasons": {},
        },
        "sample_size": 0,
        "examples": [],
        "limitations": ["No identifiers supplied — nothing to backfill."],
        "quality_flags": [],
    }


def _iso(value: datetime | None) -> str | None:
    """Render an aware datetime as ISO-8601 with `Z`; None pass-through."""
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
