"""Tests for FMP client with retry logic."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.config import Settings


class TestFMPClientRetry:
    """Tests for FMP client retry logic via decorator."""

    @pytest.mark.asyncio
    async def test_successful_request_no_retry(
        self,
        mock_settings: Settings,
        sample_income_statement: list[dict[str, Any]],
    ) -> None:
        """Test successful request doesn't trigger retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_income_statement)

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.get_income_statement("AAPL", period="annual", limit=1)
            await client.close()

            assert result == sample_income_statement
            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_request_includes_api_key(self, mock_settings: Settings) -> None:
        """Test that API key is included in request parameters."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[{"symbol": "AAPL"}])

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            await client.get_company_profile("AAPL")
            await client.close()

            # Check that apikey was in the params
            call_kwargs = mock_get.call_args
            params = call_kwargs.kwargs.get("params", {})
            assert params.get("apikey") == "test_api_key"

    @pytest.mark.asyncio
    async def test_get_balance_sheet(self, mock_settings: Settings) -> None:
        """Test balance sheet endpoint."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value=[{"symbol": "AAPL", "totalAssets": 352755000000}]
        )

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.get_balance_sheet("AAPL")
            await client.close()

            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_get_cash_flow(self, mock_settings: Settings) -> None:
        """Test cash flow endpoint."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value=[{"symbol": "AAPL", "operatingCashFlow": 110543000000}]
        )

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.get_cash_flow("AAPL")
            await client.close()

            assert len(result) == 1
            assert result[0]["symbol"] == "AAPL"


class TestFMPClientHealthCheck:
    """Tests for FMP client health check method."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_success(self, mock_settings: Settings) -> None:
        """Test health check returns True when API is reachable."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.health_check()
            await client.close()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_timeout(self, mock_settings: Settings) -> None:
        """Test health check returns False on timeout."""
        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.side_effect = httpx.TimeoutException("Timeout")

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.health_check()
            await client.close()

            assert result is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_http_error(self, mock_settings: Settings) -> None:
        """Test health check returns False on HTTP error."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 500

        with (
            patch("src.config.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            error = httpx.HTTPStatusError("Error", request=MagicMock(), response=error_response)
            mock_get.side_effect = error

            from src.clients.fmp_client import FMPClient

            client = FMPClient()
            result = await client.health_check()
            await client.close()

            assert result is False


class TestCombinedToolProviderFailure:
    """Combined fundamentals tools must surface total provider failure, not empty success."""

    @pytest.mark.asyncio
    async def test_valuation_all_failed_returns_error(self) -> None:
        """Both sub-fetches failing yields a typed isError payload, not empty lists."""
        from src.tools.fundamentals import get_valuation_metrics

        mock_fmp = MagicMock()
        mock_fmp.get_key_metrics = AsyncMock(side_effect=httpx.ConnectError("fmp down"))
        mock_fmp.get_financial_ratios = AsyncMock(side_effect=httpx.ConnectError("fmp down"))
        mock_fmp.close = AsyncMock()

        with patch("src.tools.fundamentals.FMPClient", return_value=mock_fmp):
            result = await get_valuation_metrics("AAPL", period="annual", limit=1)

        assert result.get("isError") is True
        assert result.get("error")
        assert result.get("error_type")
        assert result["symbol"] == "AAPL"
        assert "key_metrics" not in result
        assert "financial_ratios" not in result
        mock_fmp.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_valuation_partial_failure_flags_degraded(
        self, sample_key_metrics: list[dict[str, Any]]
    ) -> None:
        """One failing source keeps the success shape and discloses degraded_sources."""
        from src.tools.fundamentals import get_valuation_metrics

        mock_fmp = MagicMock()
        mock_fmp.get_key_metrics = AsyncMock(return_value=sample_key_metrics)
        mock_fmp.get_financial_ratios = AsyncMock(side_effect=httpx.ConnectError("fmp down"))
        mock_fmp.close = AsyncMock()

        with patch("src.tools.fundamentals.FMPClient", return_value=mock_fmp):
            result = await get_valuation_metrics("AAPL", period="annual")

        assert result.get("isError") is not True
        assert result["degraded_sources"] == ["financial_ratios"]
        assert len(result["key_metrics"]) == 1
        assert result["financial_ratios"] == []

    @pytest.mark.asyncio
    async def test_analyst_outlook_all_failed_returns_error(self) -> None:
        """All three analyst sub-fetches failing yields a typed isError payload."""
        from src.tools.fundamentals import get_analyst_outlook

        mock_fmp = MagicMock()
        mock_fmp.get_analyst_estimates = AsyncMock(side_effect=httpx.ConnectError("fmp down"))
        mock_fmp.get_price_target_summary = AsyncMock(side_effect=httpx.ConnectError("fmp down"))
        mock_fmp.get_company_rating = AsyncMock(side_effect=httpx.ConnectError("fmp down"))
        mock_fmp.close = AsyncMock()

        with patch("src.tools.fundamentals.FMPClient", return_value=mock_fmp):
            result = await get_analyst_outlook("AAPL")

        assert result.get("isError") is True
        assert result.get("error_type")
        assert result["symbol"] == "AAPL"
        assert "analyst_estimates" not in result


class TestValuationTTMBasis:
    """Default valuation must use FMP TTM endpoints and label records with a basis."""

    @pytest.mark.asyncio
    async def test_valuation_uses_ttm_endpoint(self, mock_settings: Settings) -> None:
        """Default (no explicit period) hits key-metrics-ttm/ratios-ttm with a ttm basis."""
        from src.tools.fundamentals import get_valuation_metrics

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=[{"symbol": "AAPL", "peRatioTTM": 30.1}])

        with (
            patch("src.clients.fmp_client.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            result = await get_valuation_metrics("AAPL")

        called_urls = [call.args[0] for call in mock_get.call_args_list]
        assert any("key-metrics-ttm" in url for url in called_urls)
        assert any("ratios-ttm" in url for url in called_urls)
        assert result["key_metrics"][0]["basis"] == "ttm"
        assert result["financial_ratios"][0]["basis"] == "ttm"

    @pytest.mark.asyncio
    async def test_valuation_period_uses_fiscal_endpoint(self, mock_settings: Settings) -> None:
        """Explicit period keeps the fiscal-period endpoints and a fiscal basis label."""
        from src.tools.fundamentals import get_valuation_metrics

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value=[{"symbol": "AAPL", "date": "2023-09-30", "period": "FY", "peRatio": 28.5}]
        )

        with (
            patch("src.clients.fmp_client.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = mock_response

            result = await get_valuation_metrics("AAPL", period="annual")

        called_urls = [call.args[0] for call in mock_get.call_args_list]
        assert not any("ttm" in url for url in called_urls)
        assert result["key_metrics"][0]["basis"] == "FY2023"
