"""Tests for async contract: tri-state async_mode, response fields, TTL cleanup."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.jobs import JobResult, JobStatus, JobStore
from src.models.strategy import (
    Condition,
    DataConfig,
    IndicatorConfig,
    Operand,
    PositionSizing,
    RiskManagement,
    RuleSet,
    StrategyDefinition,
    Universe,
)
from src.server import (
    backtest_run_strategy,
)


def _make_strategy(
    symbols: list[str] | None = None,
    start: str = "2023-01-01",
    end: str = "2024-01-01",
) -> StrategyDefinition:
    """Build a minimal valid strategy for testing."""
    return StrategyDefinition(
        name="Test",
        universe=Universe(symbols=symbols or ["AAPL"]),
        data_config=DataConfig(start_date=start, end_date=end),
        indicators=[
            IndicatorConfig(id="sma", type="SMA", params={"length": 20}),
        ],
        entry_rules=RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator="sma"),
                    operator="greater_than",
                    right=Operand(constant=100.0),
                ),
            ],
        ),
        exit_rules=RuleSet(
            logic="OR",
            conditions=[
                Condition(
                    left=Operand(indicator="sma"),
                    operator="less_than",
                    right=Operand(constant=100.0),
                ),
            ],
        ),
        position_sizing=PositionSizing(),
        risk_management=RiskManagement(),
    )


def _strategy_json(
    symbols: list[str] | None = None,
    start: str = "2023-01-01",
    end: str = "2024-01-01",
) -> str:
    """Return strategy JSON string."""
    return json.dumps(_make_strategy(symbols, start, end).to_dict())


@pytest.fixture()
def _mock_server_globals() -> Any:  # noqa: ANN401
    """Patch server globals for isolated testing."""
    mock_cache = MagicMock()
    mock_cache.get.return_value = None

    mock_downloader = MagicMock()
    mock_downloader.count_stale.return_value = 0

    mock_job_store = JobStore()

    mock_settings = MagicMock()
    mock_settings.auto_async_threshold_seconds = 10
    mock_settings.job_result_ttl_seconds = 3600
    mock_settings.estimate_symbol_year_weight = 0.5
    mock_settings.estimate_indicator_weight = 0.1
    mock_settings.estimate_download_penalty = 2.0

    mock_store = MagicMock()
    mock_store.get_last_modified.return_value = None

    with (
        patch("src.server._cache", mock_cache),
        patch("src.server._downloader", mock_downloader),
        patch("src.server._job_store", mock_job_store),
        patch("src.server._settings", mock_settings),
        patch("src.server._data_store", mock_store),
    ):
        yield {
            "cache": mock_cache,
            "downloader": mock_downloader,
            "job_store": mock_job_store,
            "settings": mock_settings,
            "store": mock_store,
        }


class TestSyncDefaultSmallJob:
    """async_mode=None with small estimate should return result directly."""

    @pytest.mark.asyncio()
    async def test_returns_sync_result(self, _mock_server_globals: Any) -> None:
        """Small strategy should run synchronously by default."""
        mock_result: dict[str, Any] = {
            "total_return_pct": 5.0,
            "cache_hit": False,
        }
        with patch(
            "src.server._run_sync_backtest",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await backtest_run_strategy(
                _strategy_json(),
                async_mode=None,
            )

        assert "job_id" not in result
        assert result["total_return_pct"] == 5.0


class TestAutoAsyncLargeJob:
    """async_mode=None with high estimate should auto-submit async."""

    @pytest.mark.asyncio()
    async def test_returns_job_id(self, _mock_server_globals: Any) -> None:
        """Large strategy should trigger auto-async."""
        # 50 symbols × 15 years × 0.5 = 375 >> threshold of 10
        strat = _strategy_json(
            symbols=[f"SYM{i}" for i in range(50)],
            start="2009-01-01",
            end="2024-01-01",
        )
        result = await backtest_run_strategy(strat, async_mode=None)

        assert "job_id" in result
        assert result["auto_async"] is True
        assert result["status"] == "queued"


class TestExplicitAsync:
    """async_mode=True should always return job_id."""

    @pytest.mark.asyncio()
    async def test_explicit_returns_job_id(
        self,
        _mock_server_globals: Any,
    ) -> None:
        """Explicit async returns job_id with auto_async=False."""
        result = await backtest_run_strategy(
            _strategy_json(),
            async_mode=True,
        )

        assert "job_id" in result
        assert result["auto_async"] is False
        assert result["status"] == "queued"


class TestForceSync:
    """async_mode=False should run sync even if estimate is high."""

    @pytest.mark.asyncio()
    async def test_force_sync_large_strategy(
        self,
        _mock_server_globals: Any,
    ) -> None:
        """Force sync returns result directly even for large strategies."""
        mock_result: dict[str, Any] = {"total_return_pct": 10.0, "cache_hit": False}
        strat = _strategy_json(
            symbols=[f"SYM{i}" for i in range(50)],
            start="2009-01-01",
            end="2024-01-01",
        )
        with patch(
            "src.server._run_sync_backtest",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await backtest_run_strategy(strat, async_mode=False)

        assert "job_id" not in result
        assert result["total_return_pct"] == 10.0


class TestAsyncResponseFields:
    """Async response should have all required contract fields."""

    @pytest.mark.asyncio()
    async def test_all_fields_present(self, _mock_server_globals: Any) -> None:
        """Check job_id, status, auto_async, estimated, poll, expires."""
        result = await backtest_run_strategy(
            _strategy_json(),
            async_mode=True,
        )

        required = {
            "job_id",
            "status",
            "auto_async",
            "estimated_seconds",
            "poll_after_seconds",
            "expires_at",
        }
        assert required.issubset(result.keys())
        assert isinstance(result["poll_after_seconds"], int)
        assert 5 <= result["poll_after_seconds"] <= 30


class TestCacheHitSkipsAsync:
    """Cached results bypass async regardless of async_mode."""

    @pytest.mark.asyncio()
    async def test_cache_hit_returns_directly(
        self,
        _mock_server_globals: Any,
    ) -> None:
        """Cache hit returns result even with async_mode=True."""
        mock_cached = MagicMock()
        mock_cached.to_dict.return_value = {"total_return_pct": 7.0}
        _mock_server_globals["cache"].get.return_value = mock_cached

        result = await backtest_run_strategy(
            _strategy_json(),
            async_mode=True,
        )

        assert result["cache_hit"] is True
        assert "job_id" not in result


class TestJobTtlCleanup:
    """Expired completed jobs should be evicted by get_job."""

    def test_expired_job_returns_none(self) -> None:
        """Job completed > ttl_seconds ago should return None."""
        store = JobStore()
        store._jobs["test_id"] = JobResult(
            job_id="test_id",
            status=JobStatus.COMPLETED,
            completed_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        )

        # TTL of 1 hour, job completed 2 hours ago → evicted
        result = store.get_job("test_id", ttl_seconds=3600)
        assert result is None

    def test_fresh_job_returned(self) -> None:
        """Job completed within TTL should be returned."""
        store = JobStore()
        store._jobs["test_id"] = JobResult(
            job_id="test_id",
            status=JobStatus.COMPLETED,
            completed_at=datetime.now(UTC).isoformat(),
        )

        result = store.get_job("test_id", ttl_seconds=3600)
        assert result is not None
        assert result.job_id == "test_id"
