"""Tests for health check endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.server import _ServerState, health_check, health_check_ready


class TestLivenessProbe:
    """Tests for the /health liveness endpoint."""

    @pytest.mark.asyncio
    async def test_liveness_returns_healthy(self) -> None:
        """Liveness reports healthy without inspecting bootstrap state."""
        response = await health_check(MagicMock())

        assert response.status_code == 200
        body = response.body.decode()
        assert "healthy" in body
        assert "backtest-server" in body


class TestReadinessProbe:
    """Tests for the /health/ready readiness endpoint."""

    @pytest.mark.asyncio
    async def test_readiness_returns_ready_when_bootstrapped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Readiness reports ready once bootstrap populated every component."""
        state = _ServerState(
            fmp_client=MagicMock(),
            db_manager=MagicMock(),
            data_store=MagicMock(),
            downloader=MagicMock(),
            cache=MagicMock(),
            job_store=MagicMock(),
            settings=MagicMock(),
        )
        monkeypatch.setattr("src.server._state", state)

        response = await health_check_ready(MagicMock())

        assert response.status_code == 200
        assert "ready" in response.body.decode()

    @pytest.mark.asyncio
    async def test_readiness_returns_503_before_bootstrap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Readiness reports 503 while components are still uninitialized."""
        monkeypatch.setattr("src.server._state", _ServerState())

        response = await health_check_ready(MagicMock())

        assert response.status_code == 503
        assert "not_ready" in response.body.decode()

    @pytest.mark.asyncio
    async def test_readiness_returns_503_when_store_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single missing component is enough to hold readiness at 503."""
        state = _ServerState(
            fmp_client=MagicMock(),
            db_manager=MagicMock(),
            data_store=None,
            downloader=MagicMock(),
            cache=MagicMock(),
            job_store=MagicMock(),
            settings=MagicMock(),
        )
        monkeypatch.setattr("src.server._state", state)

        response = await health_check_ready(MagicMock())

        assert response.status_code == 503
        assert "not_ready" in response.body.decode()
