"""Deterministic candidate-universe selection for historical analyses (§9.3).

Pulled out of the analysis tools so calibration and backtest cannot drift
on which markets they consider. Pure function — given the same
candidates and filters, it always returns the same condition_ids in the
same order, with the same fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..storage import fingerprint_universe


@dataclass(frozen=True)
class UniverseFilters:
    """Filters applied before the deterministic sort.

    Match the §11.4 + §10.2 fields a calibration/backtest tool exposes so the
    same dataclass can be reused across tools. Optional fields default to
    "no filter".
    """

    category: str | None = None
    min_lifetime_volume: float | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    require_resolved: bool = True


@dataclass(frozen=True)
class UniverseSelection:
    """Result of select_candidate_universe."""

    condition_ids: list[str]
    fingerprint: str
    candidates_in: int
    excluded_counts: dict[str, int]


def select_candidate_universe(
    candidates: list[dict[str, Any]],
    filters: UniverseFilters,
    max_markets: int,
) -> UniverseSelection:
    """Filter + sort + cap a candidate market list per §9.3.

    Steps:
        1. Apply explicit user filters (drop with named reason).
        2. Drop unresolved/ambiguous markets when require_resolved is True.
        3. Sort by ``(volume DESC, end_date DESC, condition_id ASC)``.
        4. Cap to ``max_markets``.
        5. Return condition_ids list, fingerprint, exclusion counts.

    Args:
        candidates: Normalized Gamma market dicts (typically the output of
            GammaClient.list_markets or _normalize_market).
        filters: UniverseFilters dataclass.
        max_markets: Hard cap applied after sort (typically
            settings.prediction_max_markets_per_analysis).

    Returns:
        UniverseSelection with deterministic condition_ids and a fingerprint
        derived from those condition_ids.

    """
    excluded: dict[str, int] = {}
    eligible: list[dict[str, Any]] = []

    for market in candidates:
        reason = _exclusion_reason(market, filters)
        if reason is not None:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        eligible.append(market)

    eligible.sort(key=_sort_key)
    selected = eligible[:max_markets]
    if len(eligible) > max_markets:
        excluded["over_max_markets_cap"] = excluded.get("over_max_markets_cap", 0) + (
            len(eligible) - max_markets
        )
    condition_ids = [str(m["condition_id"]) for m in selected]
    return UniverseSelection(
        condition_ids=condition_ids,
        fingerprint=fingerprint_universe(condition_ids),
        candidates_in=len(candidates),
        excluded_counts=dict(sorted(excluded.items())),
    )


def _exclusion_reason(market: dict[str, Any], filters: UniverseFilters) -> str | None:
    """Return the first reason this market should be dropped, else None.

    Implemented as a predicate list so adding a new exclusion stays a
    one-line addition and the function does not balloon past the
    too-many-returns ceiling.
    """
    end_date = _to_dt(market.get("end_date"))
    volume = float(market.get("volume") or 0.0)
    category = (market.get("category") or "").lower()
    resolution_status = market.get("resolution_status")

    checks: list[tuple[bool, str]] = [
        (not market.get("condition_id"), "missing_condition_id"),
        (
            bool(filters.category) and category != (filters.category or "").lower(),
            "category_mismatch",
        ),
        (
            filters.min_lifetime_volume is not None
            and volume < (filters.min_lifetime_volume or 0.0),
            "below_lifetime_volume",
        ),
        (
            filters.start_date is not None
            and end_date is not None
            and end_date < filters.start_date,
            "ended_before_window",
        ),
        (
            filters.end_date is not None and end_date is not None and end_date > filters.end_date,
            "ended_after_window",
        ),
        (filters.require_resolved and resolution_status != "resolved", "not_resolved"),
    ]
    for matches, reason in checks:
        if matches:
            return reason
    return None


def _sort_key(market: dict[str, Any]) -> tuple[float, float, str]:
    """Tuple key matching §9.3 sort order, with negatives for DESC ordering."""
    volume = float(market.get("volume") or 0.0)
    end_date = _to_dt(market.get("end_date"))
    # Negate the floats so ascending tuple sort yields DESC for the
    # primary + secondary keys; condition_id stays ASC.
    end_epoch = end_date.timestamp() if end_date is not None else 0.0
    return (-volume, -end_epoch, str(market.get("condition_id") or ""))


def _to_dt(value: Any) -> datetime | None:
    """Coerce a datetime-like value into a datetime (or None)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        cleaned = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return None
    return None
