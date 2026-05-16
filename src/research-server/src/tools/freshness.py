"""Freshness summary helper for tool outputs."""

from __future__ import annotations

from ..clients.exa_client import ResearchResult


def freshness_summary(results: list[ResearchResult]) -> dict[str, int]:
    """Summarize freshness across a list of results.

    Args:
        results: List of ResearchResult objects.

    Returns:
        Dict with counts per freshness category.

    """
    counts: dict[str, int] = {
        "future": 0,
        "recent": 0,
        "older": 0,
        "stale": 0,
        "unknown": 0,
    }
    for r in results:
        key = r.freshness if r.freshness in counts else "unknown"
        counts[key] += 1
    return counts
