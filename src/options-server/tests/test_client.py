"""Tests for Massive client with retry logic."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.clients.massive_client import (
    CHAIN_SNAPSHOT_MAX_PAGES,
    MassiveAPIError,
    MassiveClient,
)
from src.config import Settings
from src.response_utils import _measure, truncate_response
from src.tools.options import get_option_chain_snapshot


class TestMassiveClientRetry:
    """Tests for Massive client exponential backoff retry logic."""

    @pytest.mark.asyncio
    async def test_successful_request_no_retry(
        self, mock_settings: Settings, sample_option_chain_response: dict[str, Any]
    ) -> None:
        """Test successful request doesn't trigger retry."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value=sample_option_chain_response)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with MassiveClient(mock_settings) as client:
                result = await client.get_option_chain_snapshot(underlying_asset="AAPL", limit=10)

            assert result["results"] == sample_option_chain_response["results"]
            assert result["truncated"] is False
            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_timeout(self, mock_settings: Settings) -> None:
        """Test request retries on timeout."""
        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()
        success_response.json = MagicMock(return_value={"status": "OK", "results": []})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [
                httpx.TimeoutException("Timeout"),
                success_response,
            ]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with MassiveClient(mock_settings) as client:
                    result = await client.get_option_chain_snapshot(
                        underlying_asset="AAPL", limit=5
                    )

            assert result["results"] == []
            assert result["truncated"] is False
            assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_5xx_error(self, mock_settings: Settings) -> None:
        """Test request retries on 5xx server errors."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 503
        error_response.json = MagicMock(return_value={})

        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()
        success_response.json = MagicMock(return_value={"status": "OK", "results": []})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = [error, success_response]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with MassiveClient(mock_settings) as client:
                    result = await client.get_option_chain_snapshot(
                        underlying_asset="AAPL", limit=5
                    )

            assert result["results"] == []
            assert result["truncated"] is False
            assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_4xx_error(self, mock_settings: Settings) -> None:
        """Test request does not retry on 4xx client errors."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 400
        error_response.json = MagicMock(return_value={"status": "ERROR", "error": "Bad request"})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Bad Request", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = error

            async with MassiveClient(mock_settings) as client:
                with pytest.raises(MassiveAPIError) as exc_info:
                    await client.get_option_chain_snapshot(underlying_asset="AAPL", limit=5)

            assert "Bad request" in str(exc_info.value)
            assert mock_get.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_429_rate_limit(self, mock_settings: Settings) -> None:
        """Test request retries on 429 rate limit errors."""
        rate_limit_response = MagicMock(spec=httpx.Response)
        rate_limit_response.status_code = 429
        rate_limit_response.json = MagicMock(return_value={})

        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()
        success_response.json = MagicMock(return_value={"status": "OK", "results": []})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Rate Limited", request=MagicMock(), response=rate_limit_response
            )
            mock_get.side_effect = [error, success_response]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with MassiveClient(mock_settings) as client:
                    result = await client.get_option_chain_snapshot(
                        underlying_asset="AAPL", limit=5
                    )

            assert result["results"] == []
            assert result["truncated"] is False
            assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_max_retries_exhausted(self, mock_settings: Settings) -> None:
        """Test exception raised when max retries exhausted."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 503
        error_response.json = MagicMock(return_value={})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError(
                "Server Error", request=MagicMock(), response=error_response
            )
            mock_get.side_effect = error

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with MassiveClient(mock_settings) as client:
                    with pytest.raises(httpx.HTTPStatusError):
                        await client.get_option_chain_snapshot(underlying_asset="AAPL", limit=5)

            # Initial + 3 retries = 4 calls
            assert mock_get.call_count == 4

    @pytest.mark.asyncio
    async def test_retry_on_connect_error(self, mock_settings: Settings) -> None:
        """Test request retries on connection errors."""
        success_response = MagicMock(spec=httpx.Response)
        success_response.status_code = 200
        success_response.raise_for_status = MagicMock()
        success_response.json = MagicMock(return_value={"status": "OK", "results": []})

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = [
                httpx.ConnectError("Connection failed"),
                success_response,
            ]

            with patch("asyncio.sleep", new_callable=AsyncMock):
                async with MassiveClient(mock_settings) as client:
                    result = await client.get_option_chain_snapshot(
                        underlying_asset="AAPL", limit=5
                    )

            assert result["results"] == []
            assert result["truncated"] is False
            assert mock_get.call_count == 2


def _make_chain_page(index: int, has_next: bool) -> MagicMock:
    """Build a mock Massive snapshot page with one contract and optional cursor."""
    contract = {
        "details": {
            "ticker": f"O:SPY240119C{index:08d}",
            "strike_price": 400.0 + index,
            "expiration_date": "2024-01-19",
            "contract_type": "call",
        },
        "open_interest": 1000 + index,
        "implied_volatility": 0.2,
    }
    body: dict[str, Any] = {"status": "OK", "results": [contract]}
    if has_next:
        body["next_url"] = f"https://api.massive.com/v3/snapshot?cursor=CUR{index}"

    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=body)
    return response


def _mixed_chain_page(has_next: bool) -> MagicMock:
    """One page holding calls and puts with known OI, volume and IV."""
    contracts = []
    for idx, (kind, oi, vol, iv) in enumerate(
        [("call", 100, 10, 0.20), ("call", 300, 30, 0.40), ("put", 200, 20, 0.30)]
    ):
        contracts.append(
            {
                "details": {
                    "ticker": f"O:SPY240119{kind[0].upper()}{idx:08d}",
                    "strike_price": 400.0 + idx,
                    "expiration_date": "2024-01-19",
                    "contract_type": kind,
                },
                "open_interest": oi,
                "implied_volatility": iv,
                "day": {"volume": vol},
            }
        )
    body: dict[str, Any] = {"status": "OK", "results": contracts}
    if has_next:
        body["next_url"] = "https://api.massive.com/v3/snapshot?cursor=X"

    response = MagicMock(spec=httpx.Response)
    response.status_code = 200
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value=body)
    return response


class TestOptionChainSnapshotPagination:
    """Tests for chain-snapshot pagination and truncation flagging."""

    @pytest.mark.asyncio
    async def test_chain_snapshot_flags_truncation(self, mock_settings: Settings) -> None:
        """Chain snapshot pages under a cap, aggregates all pages, and flags truncation."""
        # Every page advertises a further cursor, so the page cap is hit.
        pages = [_make_chain_page(i, has_next=True) for i in range(CHAIN_SNAPSHOT_MAX_PAGES)]

        with (
            patch("src.tools.options.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.side_effect = pages
            result = await get_option_chain_snapshot(underlying_asset="SPY")

        # Aggregated across every fetched page, not just the first slice.
        assert mock_get.call_count == CHAIN_SNAPSHOT_MAX_PAGES
        assert result["count"] == CHAIN_SNAPSHOT_MAX_PAGES
        tickers = {c["ticker"] for c in result["contracts"]}
        assert len(tickers) == CHAIN_SNAPSHOT_MAX_PAGES
        # Truncation is surfaced so chain-wide stats are not silently biased.
        assert result["truncated"] is True
        assert result["pages_fetched"] == CHAIN_SNAPSHOT_MAX_PAGES
        assert result["next_cursor"]
        assert "warning" in result

    @pytest.mark.asyncio
    async def test_chain_snapshot_not_truncated_when_cursor_exhausted(
        self, mock_settings: Settings
    ) -> None:
        """A chain that fits under the cap aggregates all pages without a truncation flag."""
        pages = [
            _make_chain_page(0, has_next=True),
            _make_chain_page(1, has_next=False),
        ]

        with (
            patch("src.tools.options.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.side_effect = pages
            result = await get_option_chain_snapshot(underlying_asset="SPY")

        assert mock_get.call_count == 2
        assert result["count"] == 2
        assert result["truncated"] is False
        assert "warning" not in result
        assert "next_cursor" not in result


class TestMassiveClientHealthCheck:
    """Tests for Massive client health check method."""

    @pytest.mark.asyncio
    async def test_health_check_returns_true_on_success(self, mock_settings: Settings) -> None:
        """Test health check returns True when API is reachable."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            async with MassiveClient(mock_settings) as client:
                result = await client.health_check()

            assert result is True

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_timeout(self, mock_settings: Settings) -> None:
        """Test health check returns False on timeout."""
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.side_effect = httpx.TimeoutException("Timeout")

            async with MassiveClient(mock_settings) as client:
                result = await client.health_check()

            assert result is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_on_http_error(self, mock_settings: Settings) -> None:
        """Test health check returns False on HTTP error."""
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 500

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            error = httpx.HTTPStatusError("Error", request=MagicMock(), response=error_response)
            mock_get.side_effect = error

            async with MassiveClient(mock_settings) as client:
                result = await client.health_check()

            assert result is False


class TestChainAggregatesSurviveTruncation:
    """Chain-wide stats must describe the chain, not the surviving prefix."""

    @pytest.mark.asyncio
    async def test_aggregates_cover_every_fetched_contract(self, mock_settings: Settings) -> None:
        """Put/call, open interest and IV are what paging was added to get right."""
        with (
            patch("src.tools.options.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.side_effect = [_mixed_chain_page(has_next=False)]
            result = await get_option_chain_snapshot(underlying_asset="SPY")

        stats = result["chain_stats"]
        assert stats["calls"] == 2
        assert stats["puts"] == 1
        assert stats["open_interest_total"] == 600
        assert stats["put_call_open_interest_ratio"] == pytest.approx(200 / 400)
        assert stats["volume_total"] == 60
        assert stats["max_open_interest"] == 300
        assert stats["implied_volatility_min"] == pytest.approx(0.20)
        assert stats["implied_volatility_max"] == pytest.approx(0.40)
        assert stats["strike_min"] == pytest.approx(400.0)
        assert stats["strike_max"] == pytest.approx(402.0)

    @pytest.mark.asyncio
    async def test_payload_truncation_leaves_the_stats_intact(
        self, mock_settings: Settings
    ) -> None:
        """truncate_response drops tail contracts; the stats must not move.

        Trimming the list is what biased put/call, open-interest and IV-skew
        readings toward whichever contracts happened to come first.
        """
        with (
            patch("src.tools.options.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.side_effect = [_mixed_chain_page(has_next=False)]
            result = await get_option_chain_snapshot(underlying_asset="SPY")

        before = dict(result["chain_stats"])
        # Sit between "the whole payload fits" and "only one contract fits", so
        # the tail is popped rather than the response being replaced wholesale.
        full = _measure(result)
        one_contract = _measure({**result, "contracts": result["contracts"][:1]})
        trimmed = truncate_response(result, max_chars=(full + one_contract) // 2)

        assert trimmed.get("_truncated") is True
        assert len(trimmed["contracts"]) < before["contracts_total"]
        assert trimmed["chain_stats"] == before

    @pytest.mark.asyncio
    async def test_empty_chain_reports_no_stats(self, mock_settings: Settings) -> None:
        """An empty chain must not invent a ratio or a zero-strike range."""
        empty = MagicMock(spec=httpx.Response)
        empty.status_code = 200
        empty.raise_for_status = MagicMock()
        empty.json = MagicMock(return_value={"status": "OK", "results": []})

        with (
            patch("src.tools.options.get_settings", return_value=mock_settings),
            patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.side_effect = [empty]
            result = await get_option_chain_snapshot(underlying_asset="SPY")

        assert result["chain_stats"]["contracts_total"] == 0
        assert result["chain_stats"]["put_call_open_interest_ratio"] is None
