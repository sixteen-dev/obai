"""Tests for /health and /health/ready endpoints."""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings


@pytest.mark.asyncio
async def test_liveness_returns_alive_when_settings_loaded(
    mock_settings: Settings, mock_request: MagicMock
) -> None:
    """When settings are loaded, /health returns 200 with status=alive."""
    from src.server import health_check

    with patch("src.server.get_settings", return_value=mock_settings):
        response = await health_check(mock_request)
        assert response.status_code == 200
        payload = json.loads(bytes(response.body).decode())
        assert payload["status"] == "alive"
        assert payload["service"] == "knowledge-base-server"
        assert "uptime_seconds" in payload


@pytest.mark.asyncio
async def test_liveness_returns_starting_when_settings_missing(mock_request: MagicMock) -> None:
    """When settings haven't loaded, /health returns a starting payload (still 200)."""
    from src.server import health_check

    with patch("src.server.get_settings", side_effect=RuntimeError("Not loaded")):
        response = await health_check(mock_request)
        assert response.status_code == 200
        payload = json.loads(bytes(response.body).decode())
        assert payload["status"] == "starting"


@pytest.mark.asyncio
async def test_readiness_503_when_db_missing(
    mock_settings: Settings, mock_request: MagicMock
) -> None:
    """/health/ready returns 503 when corpus.db doesn't exist."""
    from src.server import health_check_ready

    assert not mock_settings.corpus_db_path.is_file()
    with patch("src.server.get_settings", return_value=mock_settings):
        response = await health_check_ready(mock_request)
        assert response.status_code == 503
        payload = json.loads(bytes(response.body).decode())
        assert payload["status"] == "not_ready"
        assert "corpus.db not found" in payload["reason"]


@pytest.mark.asyncio
async def test_readiness_200_when_db_present(
    mock_settings: Settings, mock_request: MagicMock
) -> None:
    """/health/ready returns 200 with corpus_entries count when the DB is readable."""
    from src.server import health_check_ready

    conn = sqlite3.connect(mock_settings.corpus_db_path)
    conn.execute("CREATE TABLE corpus_entries (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO corpus_entries VALUES ('test_entry')")
    conn.commit()
    conn.close()

    with patch("src.server.get_settings", return_value=mock_settings):
        response = await health_check_ready(mock_request)
        assert response.status_code == 200
        payload = json.loads(bytes(response.body).decode())
        assert payload["status"] == "ready"
        assert payload["corpus_entries"] == 1
