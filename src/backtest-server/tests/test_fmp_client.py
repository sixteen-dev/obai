"""Tests for the backtest-server FMP client risk-free rate helper."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date, timedelta
from typing import Any

import httpx
import pytest

from src.clients import fmp_client
from src.clients.fmp_client import (
    FALLBACK_RISK_FREE_RATE,
    MAX_RISK_FREE_WINDOW_YEARS,
    MAX_TREASURY_CHUNKS,
    TREASURY_CHUNK_DAYS,
    FMPClient,
)
from src.config import Settings
from src.models.strategy import TIMEFRAME_MAX_YEARS


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

    def __init__(self, rows: list[Any] | None = None, *, month_valued: bool = False) -> None:
        """Store the rows every call returns.

        Args:
            rows: Response body handed back for each recorded request. When
                None, each request is answered with one row per day of its
                own ``from``..``to`` range carrying a 4.5 yield, so every
                chunk is in-window.
            month_valued: Make each generated row's yield its month number,
                so a sub-window's mean is distinguishable from the whole.

        """
        self.rows = rows
        self.month_valued = month_valued
        self.calls: list[dict[str, str]] = []

    async def __call__(self, endpoint: str, params: dict[str, str]) -> object:
        """Record one request and replay the configured rows."""
        assert endpoint == "treasury-rates"
        self.calls.append(dict(params))
        if self.rows is not None:
            return self.rows
        first = date.fromisoformat(params["from"])
        last = date.fromisoformat(params["to"])
        days = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
        return [
            {"date": day.isoformat(), "month3": float(day.month) if self.month_valued else 4.5}
            for day in days
        ]

    @property
    def windows(self) -> list[tuple[str, str]]:
        """Return the (from, to) window of every recorded request."""
        return [(call["from"], call["to"]) for call in self.calls]


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
        recorder = _RecordingTreasury()
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
        recorder = _RecordingTreasury()
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

    async def test_rows_outside_the_requested_chunk_do_not_enter_the_mean(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A row the provider dated outside the chunk is evidence the range was ignored.

        FMP answers a request with no range with the latest quotes; if the
        range were dropped, today's yield would be averaged and published as
        the window's. Such rows are skipped so the label stays honest.
        """
        recorder = _RecordingTreasury(
            [
                {"date": "2023-01-03", "month3": 4.0},
                {"date": "2026-09-03", "month3": 9.0},
                {"date": "2022-12-30", "month3": 9.0},
            ]
        )
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        rate, source = await client.get_period_risk_free_rate_with_source(
            "2023-01-01",
            "2023-01-30",
        )

        assert rate == pytest.approx(0.04)
        assert source == "treasury_3m_period_mean"

    async def test_only_out_of_window_rows_fall_back(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When nothing the provider returned lies in the window, there is no mean."""
        recorder = _RecordingTreasury([{"date": "2026-09-03", "month3": 9.0}])
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        assert await client.get_period_risk_free_rate_with_source(
            "2023-01-01",
            "2023-01-30",
        ) == (FALLBACK_RISK_FREE_RATE, "fallback")

    def test_the_chunk_cap_admits_the_longest_window_the_schema_accepts(self) -> None:
        """The cap is derived from the schema ceiling, so no valid window falls back."""
        assert TIMEFRAME_MAX_YEARS["daily"] == MAX_RISK_FREE_WINDOW_YEARS
        longest_span_days = int(MAX_RISK_FREE_WINDOW_YEARS * 365.25) + 1
        assert longest_span_days <= MAX_TREASURY_CHUNKS * TREASURY_CHUNK_DAYS

    async def test_the_longest_daily_window_is_fetched_rather_than_defaulted(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A 30-year daily backtest is legal, so its rate must be the period mean.

        A cap of 120 chunks silently defaulted every daily window between
        29.57 and 30 years to the constant while the schema accepted them.
        """
        recorder = _RecordingTreasury()
        monkeypatch.setattr(client, "_request_with_retry", recorder)

        rate, source = await client.get_period_risk_free_rate_with_source(
            "1996-01-01",
            "2025-12-31",
        )

        assert source == "treasury_3m_period_mean"
        assert rate == pytest.approx(0.045)
        assert len(recorder.calls) == 122

    async def test_a_window_is_looked_up_again_on_a_new_day(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A window ending today keeps accruing yields, so the memo lasts one UTC day."""
        recorder = _RecordingTreasury()
        monkeypatch.setattr(client, "_request_with_retry", recorder)
        monkeypatch.setattr(fmp_client, "_utc_today", lambda: date(2026, 9, 5))
        await client.get_period_risk_free_rate_with_source("2026-08-01", "2026-09-05")
        monkeypatch.setattr(fmp_client, "_utc_today", lambda: date(2026, 9, 6))

        await client.get_period_risk_free_rate_with_source("2026-08-01", "2026-09-05")

        assert len(recorder.calls) == 2

    async def test_the_memo_is_bounded(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The oldest window is evicted once the memo is full, so it cannot grow unbounded."""
        recorder = _RecordingTreasury()
        monkeypatch.setattr(client, "_request_with_retry", recorder)
        monkeypatch.setattr(fmp_client, "MAX_RISK_FREE_MEMO_ENTRIES", 2)
        await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-01-30")
        await client.get_period_risk_free_rate_with_source("2023-02-01", "2023-02-28")
        await client.get_period_risk_free_rate_with_source("2023-03-01", "2023-03-30")

        await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-01-30")

        assert len(client._yield_series) == 2  # noqa: SLF001
        assert len(recorder.calls) == 4

    async def test_a_sub_window_is_served_from_a_covering_series(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A walk-forward fold inside the fetched range costs no request.

        The fold's mean is its own days' mean, not the range's: March alone
        averages 3.0 while the year averages about 6.5.
        """
        recorder = _RecordingTreasury(month_valued=True)
        monkeypatch.setattr(client, "_request_with_retry", recorder)
        await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-12-31")
        fetched = len(recorder.calls)

        rate, source = await client.get_period_risk_free_rate_with_source(
            "2023-03-01",
            "2023-03-31",
        )

        assert len(recorder.calls) == fetched
        assert rate == pytest.approx(0.03)
        assert source == "treasury_3m_period_mean"

    async def test_a_window_no_series_covers_is_fetched(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A window that spills past every fetched series is its own fetch."""
        recorder = _RecordingTreasury()
        monkeypatch.setattr(client, "_request_with_retry", recorder)
        await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-06-30")

        await client.get_period_risk_free_rate_with_source("2023-06-01", "2023-07-31")

        assert recorder.windows[-1] == ("2023-06-01", "2023-07-31")
        assert len(client._yield_series) == 2  # noqa: SLF001

    async def test_a_provider_failure_is_not_memoized(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An outage falls back for this lookup only; the next one retries."""
        attempts = 0

        async def flaky(endpoint: str, params: dict[str, str]) -> object:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise httpx.HTTPError("treasury outage")
            return [{"date": params["from"], "month3": 4.5}]

        monkeypatch.setattr(client, "_request_with_retry", flaky)
        first = await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-01-30")

        second = await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-01-30")

        assert first == (FALLBACK_RISK_FREE_RATE, "fallback")
        assert second == (pytest.approx(0.045), "treasury_3m_period_mean")

    async def test_an_empty_answer_does_not_shadow_a_later_series(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A glitch that returns nothing must not pin a window to the fallback all day.

        Otherwise a walk-forward fetched afterwards would price the folds
        inside the glitched span off the constant and their siblings off the
        Treasury, two rate bases in one fold table.
        """
        populated = _RecordingTreasury()
        glitches = iter([[]])

        async def flaky(endpoint: str, params: dict[str, str]) -> object:
            glitch = next(glitches, None)
            if glitch is not None:
                return glitch
            return await populated(endpoint, params)

        monkeypatch.setattr(client, "_request_with_retry", flaky)
        glitched = await client.get_period_risk_free_rate_with_source("2020-01-01", "2020-01-30")
        await client.get_period_risk_free_rate_with_source("2018-01-01", "2024-12-31")

        fold = await client.get_period_risk_free_rate_with_source("2020-01-01", "2020-01-30")

        assert glitched == (FALLBACK_RISK_FREE_RATE, "fallback")
        assert fold == (pytest.approx(0.045), "treasury_3m_period_mean")

    async def test_an_earlier_day_is_purged_on_the_next_insert(
        self,
        client: FMPClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Yesterday's series can serve nothing today, so they give up their slots."""
        recorder = _RecordingTreasury()
        monkeypatch.setattr(client, "_request_with_retry", recorder)
        monkeypatch.setattr(fmp_client, "_utc_today", lambda: date(2026, 9, 5))
        await client.get_period_risk_free_rate_with_source("2023-01-01", "2023-01-30")
        monkeypatch.setattr(fmp_client, "_utc_today", lambda: date(2026, 9, 6))

        await client.get_period_risk_free_rate_with_source("2023-02-01", "2023-02-28")

        assert list(client._yield_series) == [  # noqa: SLF001
            (date(2023, 2, 1), date(2023, 2, 28), "2026-09-06")
        ]

    async def test_a_backwards_window_raises(self, client: FMPClient) -> None:
        """An end before its start is a programming error, not a provider failure."""
        with pytest.raises(ValueError, match="after end_date"):
            await client.get_period_risk_free_rate_with_source("2024-05-01", "2024-01-01")

    async def test_a_non_iso_date_raises(self, client: FMPClient) -> None:
        """A malformed date must fail loudly instead of silently falling back."""
        with pytest.raises(ValueError, match="must be ISO YYYY-MM-DD"):
            await client.get_period_risk_free_rate_with_source("05/01/2024", "2024-01-01")
