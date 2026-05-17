"""Deterministic fingerprints for prediction-market data and analysis.

Used to detect when cached analysis outputs in pm_analysis_cache are stale.
Fingerprints are SHA-256 hex digests over a canonical (sorted-key) JSON
serialization; we truncate to 16 chars because the use case is
change-detection inside one DB, not cryptographic uniqueness.

All helpers are pure and have no I/O.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

_FINGERPRINT_LEN = 16


def fingerprint_universe(condition_ids: list[str]) -> str:
    """Fingerprint the selected market universe.

    Args:
        condition_ids: Selected condition IDs. Order does not affect the
            fingerprint — sorted before hashing.

    Returns:
        16-char hex digest.

    """
    deduped_sorted = sorted({cid for cid in condition_ids if isinstance(cid, str) and cid})
    return _hash({"v": 1, "kind": "universe", "ids": deduped_sorted})


def fingerprint_resolution(market_row: dict[str, Any]) -> str:
    """Fingerprint the resolution-relevant fields of a single market row.

    Captures winning_outcome, resolution_status, resolution_method,
    resolution_confidence, plus the terminal outcome_prices when present.
    A change to any of these (e.g. a disputed UMA re-resolution) flips the
    fingerprint and invalidates downstream analysis cache.

    Args:
        market_row: Row dict with the resolution fields.

    Returns:
        16-char hex digest.

    """
    keys = (
        "condition_id",
        "winning_outcome",
        "resolution_status",
        "resolution_method",
        "resolution_confidence",
    )
    payload: dict[str, Any] = {
        "v": 1,
        "kind": "resolution",
        "fields": {k: market_row.get(k) for k in keys},
    }
    terminal_prices = market_row.get("outcome_prices")
    if isinstance(terminal_prices, list):
        payload["terminal_prices"] = [
            (float(p) if isinstance(p, (int, float)) else None) for p in terminal_prices
        ]
    return _hash(payload)


def fingerprint_analysis(
    tool_name: str,
    params: dict[str, Any],
    universe_fingerprint: str,
    resolution_fingerprints: list[str],
) -> str:
    """Fingerprint a full analysis call: tool + params + universe + resolutions.

    Args:
        tool_name: Stable tool identifier (e.g. ``"analyze_prediction_calibration"``).
        params: User-supplied parameters dict; nested dicts/lists are
            serialized recursively under sorted keys.
        universe_fingerprint: Output of :func:`fingerprint_universe`.
        resolution_fingerprints: Per-market resolution fingerprints (will
            be deduped + sorted before hashing).

    Returns:
        16-char hex digest.

    """
    return _hash(
        {
            "v": 1,
            "kind": "analysis",
            "tool": tool_name,
            "params": params,
            "universe": universe_fingerprint,
            "resolutions": sorted(set(resolution_fingerprints)),
        }
    )


def _hash(payload: dict[str, Any]) -> str:
    """Canonical-JSON + SHA-256 + truncate. Sort keys so dict ordering is irrelevant."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_default)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:_FINGERPRINT_LEN]


def _default(obj: Any) -> Any:
    """Last-resort coercion for json.dumps; reject silently-incompatible types loudly."""
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    msg = f"Unhashable type for fingerprint: {type(obj).__name__}"
    raise TypeError(msg)
