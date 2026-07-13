"""Tests for the backtest-server FMP client risk-free rate helper."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import httpx
import pytest

from src.clients.fmp_client import FALLBACK_RISK_FREE_RATE, FMPClient
from src.config import Settings


@pytest.fixture()
async def client() -> AsyncGenerator[FMPClient, None]:
    """Provide an FMP client with a dummy key and guaranteed teardown."""
    fmp = FMPClient(settings=Settings(fmp_api_key="test-key"))
    try:
        yield fmp
    finally:
        await fmp.close()


class TestRiskFreeRate:
    """Test the 3-month treasury risk-free rate helper."""

    async def test_treasury_rates_extracts_first_element(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """get_treasury_rates returns the first dict from the FMP list response."""

        async def fake_request(endpoint: str, params: dict[str, str]) -> object:
            assert endpoint == "treasury-rates"
            return [{"month3": 4.5}]

        monkeypatch.setattr(client, "_request_with_retry", fake_request)
        rates = await client.get_treasury_rates()
        assert rates == {"month3": 4.5}

    async def test_risk_free_rate_from_treasury(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A live month3 rate (percent) is converted to a decimal and sourced."""

        async def fake_request(endpoint: str, params: dict[str, str]) -> object:
            return [{"month3": 4.5}]

        monkeypatch.setattr(client, "_request_with_retry", fake_request)
        rate, source = await client.get_risk_free_rate_with_source()
        assert rate == pytest.approx(0.045)
        assert source == "treasury_3m"

    async def test_risk_free_rate_fallback_on_error(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Any upstream failure yields the fallback rate without raising."""

        async def boom(endpoint: str, params: dict[str, str]) -> object:
            raise httpx.HTTPError("treasury outage")

        monkeypatch.setattr(client, "_request_with_retry", boom)
        rate, source = await client.get_risk_free_rate_with_source()
        assert rate == FALLBACK_RISK_FREE_RATE
        assert source == "fallback"

    async def test_risk_free_rate_is_cached_per_day(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The daily cache prevents walk-forward from re-hitting the API."""
        calls = 0

        async def counting_request(endpoint: str, params: dict[str, str]) -> object:
            nonlocal calls
            calls += 1
            return [{"month3": 4.5}]

        monkeypatch.setattr(client, "_request_with_retry", counting_request)
        first = await client.get_risk_free_rate_with_source()
        second = await client.get_risk_free_rate_with_source()
        assert first == second == (pytest.approx(0.045), "treasury_3m")
        assert calls == 1
