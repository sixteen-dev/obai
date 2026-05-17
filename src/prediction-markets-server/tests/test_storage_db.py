"""Tests for storage.db.PredictionDuckDBManager."""

from __future__ import annotations

import pytest

from src.storage.db import PredictionDuckDBManager


@pytest.fixture
def manager() -> PredictionDuckDBManager:
    """In-memory manager — no temp file required."""
    return PredictionDuckDBManager(db_path=":memory:", memory_limit="256MB")


def test_connect_initializes_all_six_tables(manager: PredictionDuckDBManager) -> None:
    """All six tables from §8 should exist after connect()."""
    manager.connect()
    try:
        tables = {
            row[0]
            for row in manager.conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        expected = {
            "pm_markets",
            "pm_tokens",
            "pm_price_history",
            "pm_trades",
            "_pm_meta",
            "pm_analysis_cache",
        }
        assert expected.issubset(tables), f"missing: {expected - tables}"
    finally:
        manager.close()


def test_connect_is_idempotent(manager: PredictionDuckDBManager) -> None:
    """Re-calling connect() should return the same connection without errors."""
    conn1 = manager.connect()
    conn2 = manager.connect()
    try:
        assert conn1 is conn2
    finally:
        manager.close()


def test_pm_meta_check_constraint_rejects_unknown_entity_type(
    manager: PredictionDuckDBManager,
) -> None:
    """_pm_meta.entity_type CHECK constraint should fail loud on an unknown type."""
    import duckdb

    manager.connect()
    try:
        with pytest.raises(duckdb.Error):
            manager.conn.execute(
                "INSERT INTO _pm_meta (entity_type, entity_id, source, fidelity_minutes, "
                "last_refreshed) VALUES (?, ?, ?, ?, NOW())",
                ["not_a_real_entity_type", "x", "s", 0],
            )
    finally:
        manager.close()


def test_db_size_bytes_zero_for_memory(manager: PredictionDuckDBManager) -> None:
    """In-memory databases report zero bytes on disk."""
    manager.connect()
    try:
        assert manager.db_size_bytes() == 0
        assert manager.db_size_gb() == 0.0
    finally:
        manager.close()


def test_db_size_bytes_grows_with_file_backed(tmp_path) -> None:
    """File-backed DB should report a non-negative size after connect."""
    mgr = PredictionDuckDBManager(db_path=str(tmp_path / "pm.duckdb"))
    mgr.connect()
    try:
        # Force a CHECKPOINT so the file is materialized.
        mgr.conn.execute("CHECKPOINT")
        assert mgr.db_size_bytes() > 0
    finally:
        mgr.close()
