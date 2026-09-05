"""Tests for the backtest-server FMP client risk-free rate helper."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

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


class _RecordingTreasury:
    """Stand-in for ``_request_with_retry`` that records params and replays rows."""

    def __init__(self, rows: list[Any]) -> None:
        """Store the rows every call returns.

        Args:
            rows: Response body handed back for each recorded request.

        """
        self.rows = rows
        self.calls: list[dict[str, str]] = []

    async def __call__(self, endpoint: str, params: dict[str, str]) -> object:
        """Record one request and replay the configured rows."""
        assert endpoint == "treasury-rates"
        self.calls.append(dict(params))
        return self.rows

    @property
    def windows(self) -> list[tuple[str, str]]:
        """Return the (from, to) window of every recorded request."""
        return [(call["from"], call["to"]) for call in self.calls]


class TestTreasuryRates:
    """Test the raw latest-rates endpoint helper."""

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


class TestPeriodRiskFreeRate:
    """The rate must describe the backtest window, not the day it was run."""

    async def test_mean_of_the_window_is_returned_with_its_source(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The window's daily 3-month yields average into one annual decimal.

        A 2015-2020 backtest priced off today's yield changes its Sharpe every
        time the Treasury moves while its prices stay identical, so the rate is
        the mean printed inside the window instead.
        """
        recorder = _RecordingTreasury(
            [
                {"date": "2023-01-03", "month3": 4.0},
                {"date": "2023-01-04", "month3": 5.0},
                {"date": "2023-01-05", "month3": 6.0},
            ]
        )
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        rate, source = await client.get_period_risk_free_rate_with_source(
            "2023-01-03",
            "2023-01-05",
        )

        assert rate == pytest.approx(0.05)
        assert source == "treasury_3m_period_mean"

    async def test_a_repeated_date_counts_once(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Chunk boundaries can repeat a date; a repeat must not reweight the mean."""
        recorder = _RecordingTreasury(
            [
                {"date": "2023-01-03", "month3": 4.0},
                {"date": "2023-01-03", "month3": 4.0},
                {"date": "2023-01-04", "month3": 6.0},
            ]
        )
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        rate, source = await client.get_period_risk_free_rate_with_source(
            "2023-01-03",
            "2023-01-04",
        )

        assert rate == pytest.approx(0.05)
        assert source == "treasury_3m_period_mean"

    async def test_rows_without_a_numeric_month3_are_skipped(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A missing, null or non-numeric yield is dropped, not coerced."""
        recorder = _RecordingTreasury(
            [
                {"date": "2023-01-03", "month3": 4.0},
                {"date": "2023-01-04"},
                {"date": "2023-01-05", "month3": None},
                {"date": "2023-01-06", "month3": "6.0"},
                {"month3": 9.0},
            ]
        )
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        rate, source = await client.get_period_risk_free_rate_with_source(
            "2023-01-03",
            "2023-01-06",
        )

        assert rate == pytest.approx(0.04)
        assert source == "treasury_3m_period_mean"

    async def test_a_long_window_is_fetched_in_quarterly_chunks(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FMP caps a response at roughly a quarter, so the window is tiled.

        The chunks must cover the whole window with no gap: a hole would drop
        the yields of those days out of the mean without any error.
        """
        recorder = _RecordingTreasury([{"date": "2023-01-03", "month3": 4.5}])
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        rate, _ = await client.get_period_risk_free_rate_with_source(
            "2023-01-01",
            "2023-07-19",
        )

        assert recorder.windows == [
            ("2023-01-01", "2023-03-31"),
            ("2023-04-01", "2023-06-29"),
            ("2023-06-30", "2023-07-19"),
        ]
        assert rate == pytest.approx(0.045)

    async def test_a_short_window_is_one_request(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A window inside the provider's cap is fetched whole."""
        recorder = _RecordingTreasury([{"date": "2023-01-03", "month3": 4.5}])
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-01-30")

        assert recorder.windows == [("2023-01-01", "2023-01-30")]

    async def test_the_same_window_is_fetched_once(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Walk-forward asks ~2N times for one window; that is one lookup."""
        recorder = _RecordingTreasury([{"date": "2023-01-03", "month3": 4.5}])
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        first = await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-01-30")
        second = await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-01-30")

        assert first == second
        assert len(recorder.calls) == 1

    async def test_a_different_window_is_fetched_again(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The memo is keyed by the window, so another period is its own lookup."""
        recorder = _RecordingTreasury([{"date": "2023-01-03", "month3": 4.5}])
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-01-30")
        await client.get_period_risk_free_rate_with_source("2024-01-01", "2024-01-30")

        assert recorder.windows == [
            ("2023-01-01", "2023-01-30"),
            ("2024-01-01", "2024-01-30"),
        ]

    async def test_fallback_on_provider_error(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A treasury outage must not break the backtest."""

        async def boom(endpoint: str, params: dict[str, str]) -> object:
            raise httpx.HTTPError("treasury outage")

        monkeypatch.setattr(client, "_request_with_retry", boom)

        rate, source = await client.get_period_risk_free_rate_with_source(
            "2023-01-01",
            "2023-01-30",
        )

        assert rate == FALLBACK_RISK_FREE_RATE
        assert source == "fallback"

    async def test_fallback_on_malformed_body(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A body that is not a list of rows yields the fallback, not a crash."""

        async def malformed(endpoint: str, params: dict[str, str]) -> object:
            return {"Error Message": "Invalid API KEY"}

        monkeypatch.setattr(client, "_request_with_retry", malformed)

        assert await client.get_period_risk_free_rate_with_source(
            "2023-01-01",
            "2023-01-30",
        ) == (FALLBACK_RISK_FREE_RATE, "fallback")

    async def test_fallback_on_row_shapes_that_are_not_dicts(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Junk inside the list is skipped and leaves nothing to average."""
        recorder = _RecordingTreasury(["oops"])
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        assert await client.get_period_risk_free_rate_with_source(
            "2023-01-01",
            "2023-01-30",
        ) == (FALLBACK_RISK_FREE_RATE, "fallback")

    async def test_fallback_on_empty_series(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A window the provider has no rows for falls back rather than averaging nothing."""
        recorder = _RecordingTreasury([])
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        assert await client.get_period_risk_free_rate_with_source(
            "2023-01-01",
            "2023-01-30",
        ) == (FALLBACK_RISK_FREE_RATE, "fallback")

    async def test_fallback_when_the_window_exceeds_the_chunk_cap(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The chunk loop is capped; a century-long window falls back unfetched."""
        recorder = _RecordingTreasury([{"date": "1900-01-03", "month3": 4.5}])
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        assert await client.get_period_risk_free_rate_with_source(
            "1900-01-01",
            "2026-01-01",
        ) == (FALLBACK_RISK_FREE_RATE, "fallback")
        assert recorder.calls == []

    async def test_a_backwards_window_raises(self, client: FMPClient) -> None:
        """An end before its start is a programming error, not a provider failure."""
        with pytest.raises(ValueError, match="after end_date"):
            await client.get_period_risk_free_rate_with_source("2024-05-01", "2024-01-01")

    async def test_a_non_iso_date_raises(self, client: FMPClient) -> None:
        """A malformed date must fail loudly instead of silently falling back."""
        with pytest.raises(ValueError, match="must be ISO YYYY-MM-DD"):
            await client.get_period_risk_free_rate_with_source("05/01/2024", "2024-01-01")
