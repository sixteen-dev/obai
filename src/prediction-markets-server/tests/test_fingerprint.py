"""Tests for storage.fingerprint deterministic SHA-256 helpers."""

from __future__ import annotations

import pytest

from src.storage.fingerprint import (
    fingerprint_analysis,
    fingerprint_resolution,
    fingerprint_universe,
)


def test_universe_fingerprint_is_order_independent() -> None:
    """Universe fingerprint must not depend on input order or duplicates."""
    a = fingerprint_universe(["0xC", "0xA", "0xB"])
    b = fingerprint_universe(["0xA", "0xB", "0xC"])
    c = fingerprint_universe(["0xA", "0xA", "0xB", "0xC"])
    assert a == b == c


def test_universe_fingerprint_changes_on_set_change() -> None:
    """Adding a new condition_id flips the fingerprint."""
    a = fingerprint_universe(["0xA", "0xB"])
    b = fingerprint_universe(["0xA", "0xB", "0xC"])
    assert a != b


def test_resolution_fingerprint_changes_on_winner_flip() -> None:
    """Flipping the winner field invalidates the fingerprint (UMA dispute case)."""
    base = {
        "condition_id": "0xC",
        "winning_outcome": "Yes",
        "resolution_status": "resolved",
        "resolution_method": "explicit_api",
        "resolution_confidence": 1.0,
        "outcome_prices": [1.0, 0.0],
    }
    flipped = dict(base, winning_outcome="No", outcome_prices=[0.0, 1.0])
    assert fingerprint_resolution(base) != fingerprint_resolution(flipped)


def test_resolution_fingerprint_stable_across_runs() -> None:
    """Same inputs → same digest (no hash randomization leaks)."""
    payload = {
        "condition_id": "0xC",
        "winning_outcome": "Yes",
        "resolution_status": "resolved",
        "resolution_method": "explicit_api",
        "resolution_confidence": 1.0,
    }
    assert fingerprint_resolution(payload) == fingerprint_resolution(payload)


def test_analysis_fingerprint_sensitive_to_params() -> None:
    """Changing any of {tool, params, universe, resolutions} flips the digest."""
    base_universe = fingerprint_universe(["0xA"])
    fp1 = fingerprint_analysis("calibration", {"k": 1}, base_universe, ["rf1"])
    fp2 = fingerprint_analysis("calibration", {"k": 2}, base_universe, ["rf1"])
    fp3 = fingerprint_analysis("longshot", {"k": 1}, base_universe, ["rf1"])
    fp4 = fingerprint_analysis("calibration", {"k": 1}, base_universe, ["rf2"])
    assert len({fp1, fp2, fp3, fp4}) == 4


def test_analysis_fingerprint_order_independent_on_resolutions() -> None:
    """Resolution fingerprint order should not affect the analysis fingerprint."""
    u = fingerprint_universe(["0xA", "0xB"])
    fp_a = fingerprint_analysis("calibration", {}, u, ["rfA", "rfB"])
    fp_b = fingerprint_analysis("calibration", {}, u, ["rfB", "rfA"])
    assert fp_a == fp_b


def test_default_rejects_unhashable_types() -> None:
    """Non-JSON-serializable values must raise TypeError, not silently coerce."""

    class Unhashable:
        pass

    with pytest.raises(TypeError, match="Unhashable type"):
        fingerprint_analysis("calibration", {"obj": Unhashable()}, "u", [])
