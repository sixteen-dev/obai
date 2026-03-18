"""Tests for health check endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import Settings


class TestLivenessProbe:
    """Tests for /health liveness endpoint."""

    @pytest.mark.asyncio
    async def test_liveness_returns_alive_when_settings_loaded(
        self, mock_settings: Settings
    ) -> None:
        """Test liveness probe returns alive status when settings are loaded."""
        from src.server import health_check

        mock_request = MagicMock()

        with patch("src.server.get_settings", return_value=mock_settings):
            response = await health_check(mock_request)

            assert response.status_code == 200
            data = response.body.decode()
            assert "alive" in data
            assert "fundamentals-server" in data
            assert "uptime_seconds" in data

    @pytest.mark.asyncio
    async def test_liveness_returns_starting_when_settings_not_loaded(self) -> None:
        """Test liveness probe returns starting status when settings fail."""
        from src.server import health_check

        mock_request = MagicMock()

        with patch("src.server.get_settings", side_effect=RuntimeError("Not loaded")):
            response = await health_check(mock_request)

            assert response.status_code == 200
            data = response.body.decode()
            assert "starting" in data
            assert "fundamentals-server" in data


class TestReadinessProbe:
    """Tests for /health/ready readiness endpoint."""

    @pytest.mark.asyncio
    async def test_readiness_returns_ready_when_api_healthy(self, mock_settings: Settings) -> None:
        """Test readiness probe returns ready when API is reachable."""
        from src.server import health_check_ready

        mock_request = MagicMock()

        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=True)
        mock_client.close = AsyncMock()

        with (
            patch("src.server.get_settings", return_value=mock_settings),
            patch("src.server.FMPClient", return_value=mock_client),
        ):
            response = await health_check_ready(mock_request)

            assert response.status_code == 200
            data = response.body.decode()
            assert "ready" in data
            assert "fundamentals-server" in data
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_readiness_returns_503_when_api_unhealthy(self, mock_settings: Settings) -> None:
        """Test readiness probe returns 503 when API is unreachable."""
        from src.server import health_check_ready

        mock_request = MagicMock()

        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=False)
        mock_client.close = AsyncMock()

        with (
            patch("src.server.get_settings", return_value=mock_settings),
            patch("src.server.FMPClient", return_value=mock_client),
        ):
            response = await health_check_ready(mock_request)

            assert response.status_code == 503
            data = response.body.decode()
            assert "not_ready" in data
            assert "FMP API unreachable" in data
            mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_readiness_returns_503_when_settings_not_loaded(self) -> None:
        """Test readiness probe returns 503 when settings fail to load."""
        from src.server import health_check_ready

        mock_request = MagicMock()

        with patch("src.server.get_settings", side_effect=RuntimeError("Not loaded")):
            response = await health_check_ready(mock_request)

            assert response.status_code == 503
            data = response.body.decode()
            assert "not_ready" in data
            assert "Settings not loaded" in data

    @pytest.mark.asyncio
    async def test_readiness_closes_client_on_exception(self, mock_settings: Settings) -> None:
        """Test that client is closed even when health check raises exception."""
        from src.server import health_check_ready

        mock_request = MagicMock()

        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(side_effect=Exception("API error"))
        mock_client.close = AsyncMock()

        with (
            patch("src.server.get_settings", return_value=mock_settings),
            patch("src.server.FMPClient", return_value=mock_client),
        ):
            response = await health_check_ready(mock_request)

            assert response.status_code == 503
            mock_client.close.assert_called_once()
