"""Tests for async contract: tri-state async_mode, response fields, TTL cleanup.

NOTE: Tests in TestSyncDefaultSmallJob through TestCacheHitSkipsAsync are skipped
because the server was refactored to remove module-level globals (_cache, _downloader,
_job_store, _settings, _data_store). The test fixture needs a full rewrite to match
the current server architecture. JobStore TTL tests still work (no server dependency).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest

from src import server
from src.engine.indicators import indicator_stack_versions
from src.engine.metrics import compute_metrics
from src.jobs import JobResult, JobStatus, JobStore
from src.models.strategy import (
    FILL_MODEL,
    FILL_TIMING,
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
    _compute_train_test_split,
    _execute_strategy,
    _finalize_backtest_response,
    _required_warmup_bars,
    _state,
    _warmup_calendar_days,
    _warmup_fetch_start,
    backtest_run_strategy_tool,
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


SKIP_REASON = (
    "Server refactored: module-level globals (_cache, _downloader, etc.) removed. "
    "Test fixture needs rewrite to match current architecture."
)


@pytest.mark.skip(reason=SKIP_REASON)
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
            result = await backtest_run_strategy_tool(
                _strategy_json(),
                async_mode=None,
            )

        assert "job_id" not in result
        assert result["total_return_pct"] == 5.0


@pytest.mark.skip(reason=SKIP_REASON)
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
        result = await backtest_run_strategy_tool(strat, async_mode=None)

        assert "job_id" in result
        assert result["auto_async"] is True
        assert result["status"] == "queued"


@pytest.mark.skip(reason=SKIP_REASON)
class TestExplicitAsync:
    """async_mode=True should always return job_id."""

    @pytest.mark.asyncio()
    async def test_explicit_returns_job_id(
        self,
        _mock_server_globals: Any,
    ) -> None:
        """Explicit async returns job_id with auto_async=False."""
        result = await backtest_run_strategy_tool(
            _strategy_json(),
            async_mode=True,
        )

        assert "job_id" in result
        assert result["auto_async"] is False
        assert result["status"] == "queued"


@pytest.mark.skip(reason=SKIP_REASON)
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
            result = await backtest_run_strategy_tool(strat, async_mode=False)

        assert "job_id" not in result
        assert result["total_return_pct"] == 10.0


@pytest.mark.skip(reason=SKIP_REASON)
class TestAsyncResponseFields:
    """Async response should have all required contract fields."""

    @pytest.mark.asyncio()
    async def test_all_fields_present(self, _mock_server_globals: Any) -> None:
        """Check job_id, status, auto_async, estimated, poll, expires."""
        result = await backtest_run_strategy_tool(
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


@pytest.mark.skip(reason=SKIP_REASON)
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

        result = await backtest_run_strategy_tool(
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


class TestTrainTestSplitBoundary:
    """The return into the first test bar belongs to the test slice."""

    def test_boundary_return_is_counted_in_the_test_period(self) -> None:
        """A jump across the split must not vanish between the two slices."""
        strategy = _make_strategy(start="2024-01-01", end="2024-12-31")
        strategy.data_config.train_end_date = "2024-06-30"
        equity_df = pl.DataFrame(
            {
                "date": [date(2024, 6, 28), date(2024, 6, 30), date(2024, 7, 1), date(2024, 7, 2)],
                "equity": [1000.0, 1000.0, 1100.0, 1100.0],
            }
        )

        split = _compute_train_test_split(equity_df, [], strategy, 0.0, "assumed_zero")

        assert split is not None
        assert split["test"]["performance"]["total_return_pct"] == 10.0
        assert split["train"]["performance"]["total_return_pct"] == 0.0

    def test_test_period_starts_at_the_carried_baseline_bar(self) -> None:
        """The baseline bar is kept, so the test period is reported from the boundary."""
        strategy = _make_strategy(start="2024-01-01", end="2024-12-31")
        strategy.data_config.train_end_date = "2024-06-30"
        equity_df = pl.DataFrame(
            {
                "date": [date(2024, 6, 28), date(2024, 6, 30), date(2024, 7, 1), date(2024, 7, 2)],
                "equity": [1000.0, 1000.0, 1100.0, 1100.0],
            }
        )

        split = _compute_train_test_split(equity_df, [], strategy, 0.0, "assumed_zero")

        assert split is not None
        assert split["test"]["period"] == "2024-06-30 to 2024-07-02"
        assert split["test"]["data_points_processed"] == 3


_PORTFOLIO_STRATEGY: dict[str, Any] = {
    "name": "One symbol portfolio",
    "universe": {"symbols": ["A"], "benchmark": None},
    "data_config": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
    "indicators": [],
    "entry_rules": {
        "conditions": [
            {
                "left": {"indicator": "close"},
                "operator": "greater_than",
                "right": {"constant": 0},
            }
        ]
    },
    "exit_rules": {
        "conditions": [
            {"left": {"indicator": "close"}, "operator": "less_than", "right": {"constant": 0}}
        ]
    },
    "position_sizing": {
        "method": "equal_weight",
        "max_position_pct": 50,
        "max_positions": 5,
        "allocation_mode": "portfolio",
    },
    "execution_config": {"initial_capital": 1000, "commission_pct": 0, "slippage_pct": 0},
}


class TestAllocationModeRouting:
    """The requested allocation mode decides the engine, not the symbol count."""

    async def test_one_symbol_portfolio_request_runs_the_portfolio_engine(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A universe of one must not silently downgrade to the independent engine.

        The single-symbol branch ran first, so an explicit shared-capital
        request came back with no portfolio result and different quantity
        semantics: proportional notional instead of discrete shares.
        """
        frame = pl.DataFrame(
            {
                "date": [date(2024, 1, d) for d in (2, 3, 4, 5)],
                "open": [100.0, 100.0, 200.0, 200.0],
                "high": [100.0, 100.0, 200.0, 200.0],
                "low": [100.0, 100.0, 200.0, 200.0],
                "close": [100.0, 100.0, 200.0, 200.0],
                "volume": [1_000_000] * 4,
            }
        )
        downloader = AsyncMock()
        downloader.ensure_data.return_value = {"A": frame}
        monkeypatch.setattr(_state, "downloader", downloader)

        result = await _execute_strategy(StrategyDefinition.from_dict(_PORTFOLIO_STRATEGY))

        assert result.portfolio_result is not None
        # Two whole shares bought at 100 out of a 200 equal-weight slot, marked
        # at 200: 800 cash + 400 held. The independent engine reports 1500.
        assert result.equity_df["equity"][-1] == 1200.0


class TestExecutionContractDisclosure:
    """A stored result must say how its orders were assumed to fill."""

    def test_sync_response_carries_the_fill_timing_and_fill_model(self) -> None:
        """Fill conventions belong in the payload, not only in the repo's docs.

        Costs apply to signal exits but not to stop, target, or forced exits,
        which flatters a stop-heavy strategy. A reader of the result could not
        tell, so the conventions are named alongside the metrics.
        """
        equity_df = pl.DataFrame(
            {
                "date": [date(2024, 1, 2), date(2024, 1, 3)],
                "equity": [1000.0, 1100.0],
            }
        )
        result = compute_metrics(equity_df, [], "Test", ["AAPL"])

        response = _finalize_backtest_response(
            result,
            strategy=_make_strategy(),
            cache_hit=False,
            portfolio_result=None,
        )

        assert response["fill_timing"] == FILL_TIMING
        assert response["fill_model"] == FILL_MODEL


async def test_intraday_result_reports_the_raw_price_basis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An intraday result must disclose the basis its prices sit on.

    Intraday bars are stored unadjusted while daily bars are dividend
    adjusted, so the payload has to say which one the numbers came from
    instead of leaving the agent to assert it from memory.
    """
    strategy = _make_strategy()
    strategy.data_config.timeframe = "5min"
    exec_result = server._ExecutionResult(
        equity_df=pl.DataFrame(
            {"date": [date(2024, 1, 2), date(2024, 1, 3)], "equity": [10_000.0, 10_000.0]}
        ),
        trades=[],
        warnings=[],
        warmup_bars={"AAPL": 0},
    )
    fmp_client = AsyncMock()
    fmp_client.get_risk_free_rate_with_source.return_value = (0.0, "assumed_zero")
    monkeypatch.setattr(server._state, "fmp_client", fmp_client)
    monkeypatch.setattr(server._state, "cache", MagicMock())
    monkeypatch.setattr(server, "_execute_strategy", AsyncMock(return_value=exec_result))

    response = await server._run_sync_backtest(strategy, "cache-key")

    assert response["price_basis"] == "raw"
    assert response["dependency_versions"] == indicator_stack_versions()


class TestWarmupPlannerBounds:
    """The warm-up planner's caps exist to bound the fetch; prove they hold."""

    @staticmethod
    def _sma(length: int) -> list[IndicatorConfig]:
        """Return one SMA config with the given lookback."""
        return [IndicatorConfig(id="sma", type="SMA", params={"length": length})]

    def test_lookback_is_capped(self) -> None:
        """A pathological period must clamp to the declared bar cap."""
        assert _required_warmup_bars(self._sma(5000)).bars == 2000

    def test_no_indicators_need_no_preroll(self) -> None:
        """A strategy with no lookback indicators fetches no pre-roll."""
        assert _required_warmup_bars([]).bars == 0

    def test_daily_lookback_converts_to_calendar_days(self) -> None:
        """Daily bars convert one-for-one before the weekend margin."""
        assert _warmup_calendar_days(self._sma(200), "daily") == 418

    def test_intraday_lookback_shrinks_with_bars_per_day(self) -> None:
        """The same lookback spans far fewer calendar days on 5-minute bars."""
        assert _warmup_calendar_days(self._sma(200), "5min") == 7

    def test_calendar_conversion_applies_the_bar_cap_first(self) -> None:
        """The cap bounds the fetch, so it must bind before the day conversion."""
        assert _warmup_calendar_days(self._sma(5000), "daily") == 4200


class TestRequiredWarmupBars:
    """The pre-roll planner sizes the fetch from the catalog, chains included."""

    @staticmethod
    def _bars(config: IndicatorConfig) -> int:
        """Return the planned pre-roll for a single indicator."""
        return _required_warmup_bars([config]).bars

    def test_single_indicator_uses_exact_lookback(self) -> None:
        """A non-recursive indicator needs exactly its first-valid index."""
        assert self._bars(IndicatorConfig(id="s", type="SMA", params={"length": 200})) == 199
        assert self._bars(IndicatorConfig(id="d", type="STDDEV", params={"length": 20})) == 19
        assert self._bars(IndicatorConfig(id="m", type="MOM", params={"length": 10})) == 10

    def test_recursive_indicators_get_stabilization(self) -> None:
        """A seeded recursion is defined early but still carries its seed error."""
        assert self._bars(IndicatorConfig(id="a", type="ADX", params={"length": 14})) == 81
        assert self._bars(IndicatorConfig(id="t", type="TEMA", params={"length": 20})) == 171
        assert self._bars(IndicatorConfig(id="e", type="EMA", params={"length": 20})) == 57

    def test_chain_sums_along_the_source(self) -> None:
        """An indicator reading an indicator inherits its upstream warm-up."""
        chain = [
            IndicatorConfig(id="roc", type="ROC", params={"length": 1}),
            IndicatorConfig(id="sd", type="STDDEV", params={"length": 20}, source="roc"),
            IndicatorConfig(id="sma", type="SMA", params={"length": 100}, source="sd"),
        ]
        stacked = [
            IndicatorConfig(id="adx", type="ADX", params={"length": 14}),
            IndicatorConfig(id="ema", type="EMA", params={"length": 5}, source="adx"),
        ]

        assert _required_warmup_bars(chain).bars == 119
        assert _required_warmup_bars(stacked).bars == 93

    def test_dual_takes_the_larger_upstream(self) -> None:
        """Both series a two-input indicator reads have to be primed."""
        regressed = [
            IndicatorConfig(id="sma_50", type="SMA", params={"length": 50}),
            IndicatorConfig(
                id="beta", type="BETA", params={"length": 5, "second_source": "sma_50"}
            ),
        ]
        banded = [
            IndicatorConfig(id="bb", type="BBANDS", params={"length": 20}),
            IndicatorConfig(
                id="corr", type="CORREL", params={"length": 2, "second_source": "bb_upper"}
            ),
        ]

        assert _required_warmup_bars(regressed).bars == 54
        assert _required_warmup_bars(banded).bars == 20

    def test_a_suffixed_source_resolves_to_its_parent(self) -> None:
        """One band of a multi-output indicator carries that indicator's warm-up."""
        banded = [
            IndicatorConfig(id="bb", type="BBANDS", params={"length": 20}),
            IndicatorConfig(id="obv", type="OBV", source="bb_upper"),
        ]

        assert _required_warmup_bars(banded).bars == 19

    def test_parameter_free_indicators(self) -> None:
        """Indicators with no window still declare what history they need."""
        assert _required_warmup_bars([]).bars == 0
        assert self._bars(IndicatorConfig(id="o", type="OBV")) == 0
        assert self._bars(IndicatorConfig(id="v", type="VWAP")) == 0
        assert self._bars(IndicatorConfig(id="c", type="CDL_DOJI")) == 14
        assert self._bars(IndicatorConfig(id="p", type="SAR")) == 3

    def test_unplannable_config_plans_no_pre_roll(self) -> None:
        """An unvalidated config sizes no fetch instead of failing the run."""
        assert self._bars(IndicatorConfig(id="x", type="NOPE", params={"length": 30})) == 0
        # A wrongly typed period is planned from the catalog default (SMA 30).
        assert self._bars(IndicatorConfig(id="s", type="SMA", params={"length": "5000"})) == 29

    def test_cap_truncates_and_reports(self) -> None:
        """The cap bounds the fetch and the plan says what it cost."""
        long_ema = [IndicatorConfig(id="e", type="EMA", params={"length": 15600})]
        sma = [IndicatorConfig(id="s", type="SMA", params={"length": 200})]

        plan = _required_warmup_bars(long_ema)

        assert plan.required_bars == 46797
        assert plan.bars == 2000
        assert plan.truncated
        assert _required_warmup_bars(sma, cap=50).bars == 50

    async def test_execution_reports_a_truncated_pre_roll(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A capped pre-roll leaves the window unstabilized, so the run says so."""
        strategy = _make_strategy()
        strategy.indicators = [IndicatorConfig(id="sma", type="EMA", params={"length": 15600})]
        frame = pl.DataFrame(
            {
                "date": [date(2023, 1, d) for d in (2, 3, 4, 5)],
                "open": [100.0] * 4,
                "high": [101.0] * 4,
                "low": [99.0] * 4,
                "close": [100.0] * 4,
                "volume": [1_000_000] * 4,
            }
        )
        downloader = AsyncMock()
        downloader.ensure_data.return_value = {"AAPL": frame}
        monkeypatch.setattr(_state, "downloader", downloader)

        result = await _execute_strategy(strategy)

        assert any(
            "Warm-up pre-roll capped at 2000 bars" in warning and "46797 bars" in warning
            for warning in result.warnings
        )


_BENCHMARK_MOMENTUM_STRATEGY: dict[str, Any] = {
    "name": "Relative strength",
    "universe": {"symbols": ["AAA"], "benchmark": "SPY"},
    "data_config": {"start_date": "2024-02-01", "end_date": "2024-04-01"},
    "indicators": [
        {"id": "sma", "type": "SMA", "params": {"length": 12}, "source": "close"},
        {"id": "roc_sym", "type": "ROC", "params": {"length": 5}, "source": "close"},
        {
            "id": "roc_bench",
            "type": "ROC",
            "params": {"length": 5},
            "source": "benchmark_close",
        },
    ],
    "entry_rules": {
        "logic": "AND",
        "conditions": [
            {
                "left": {"indicator": "roc_sym"},
                "operator": "greater_than",
                "right": {"indicator": "roc_bench"},
            }
        ],
    },
    "exit_rules": {
        "logic": "OR",
        "conditions": [
            {
                "left": {"indicator": "roc_sym"},
                "operator": "less_than",
                "right": {"indicator": "roc_bench"},
            }
        ],
    },
}


def _rising_daily_frame(start: date, bars: int, first_close: float, step: float) -> pl.DataFrame:
    """Return a frame of consecutive daily bars rising by a fixed step."""
    closes = [first_close + step * i for i in range(bars)]
    return pl.DataFrame(
        {
            "date": [start + timedelta(days=i) for i in range(bars)],
            "open": closes,
            "high": [close + 1.0 for close in closes],
            "low": [close - 1.0 for close in closes],
            "close": closes,
            "volume": [1_000_000] * bars,
        }
    )


async def test_referenced_benchmark_is_fetched_from_the_warmup_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An indicator on the benchmark needs the same pre-roll as one on the symbol.

    The benchmark used to be resolved over the requested window only, after the
    symbol frames were trimmed. A rate of change over it would then have been
    undefined for exactly the bars the pre-roll exists to prime.
    """
    strategy = StrategyDefinition.from_dict(_BENCHMARK_MOMENTUM_STRATEGY)
    warmup_days = _warmup_calendar_days(strategy.indicators, "daily")
    fetch_start = _warmup_fetch_start(strategy.data_config.start_date, warmup_days)
    assert fetch_start < strategy.data_config.start_date

    frames = {
        "AAA": _rising_daily_frame(date.fromisoformat(fetch_start), 90, 100.0, 2.0),
        "SPY": _rising_daily_frame(date.fromisoformat(fetch_start), 90, 400.0, 1.0),
    }
    calls: list[tuple[list[str], str]] = []

    async def _ensure_data(
        symbols: list[str],
        start_date: str,
        end_date: str,
        timeframe: str,
    ) -> dict[str, pl.DataFrame]:
        calls.append((list(symbols), start_date))
        return {symbol: frames[symbol] for symbol in symbols}

    downloader = AsyncMock()
    downloader.ensure_data.side_effect = _ensure_data
    monkeypatch.setattr(_state, "downloader", downloader)

    result = await _execute_strategy(strategy)

    assert calls == [(["AAA"], fetch_start), (["SPY"], fetch_start)]
    assert "benchmark_close" in result.symbol_dfs["AAA"].columns
    assert result.benchmark_df is not None
    assert result.benchmark_df["date"].min() == date.fromisoformat(strategy.data_config.start_date)
    assert not any("benchmark_close" in warning for warning in result.warnings)


_ZERO_TRADE_STRATEGY: dict[str, Any] = {
    "name": "Never both at once",
    "universe": {"symbols": ["A"], "benchmark": None},
    "data_config": {"start_date": "2024-01-01", "end_date": "2024-12-31"},
    "indicators": [],
    "entry_rules": {
        "logic": "AND",
        "conditions": [
            {
                "left": {"indicator": "close"},
                "operator": "greater_than",
                "right": {"constant": 100.0},
            },
            {
                "left": {"indicator": "close"},
                "operator": "less_than",
                "right": {"constant": 90.0},
            },
        ],
    },
    "exit_rules": {"logic": "OR", "conditions": []},
    "position_sizing": {
        "method": "equal_weight",
        "max_position_pct": 100,
        "max_positions": 5,
        "allocation_mode": "independent",
    },
    "execution_config": {"initial_capital": 100_000, "commission_pct": 0, "slippage_pct": 0},
}

_ZERO_TRADE_DIAGNOSTICS: dict[str, Any] = {
    "bars": 5,
    "entry_signal_bars": 0,
    "exit_signal_bars": 0,
    "entry_conditions": [
        {"rule": "close greater_than 100.0", "true_bars": 2},
        {"rule": "close less_than 90.0", "true_bars": 2},
    ],
    "exit_conditions": [],
    "entries_skipped_by_reason": {},
}


class TestSignalDiagnostics:
    """A zero-trade result must say which predicate failed to fire."""

    async def test_zero_trade_result_names_the_predicate_that_never_fires(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Both predicates fire, their conjunction never does, nothing is skipped."""
        closes = [95.0, 101.0, 102.0, 88.0, 89.0]
        frame = pl.DataFrame(
            {
                "date": [date(2024, 1, d) for d in (2, 3, 4, 5, 8)],
                "open": closes,
                "high": [c + 1.0 for c in closes],
                "low": [c - 1.0 for c in closes],
                "close": closes,
                "volume": [1_000_000] * 5,
            }
        )
        downloader = AsyncMock()
        downloader.ensure_data.return_value = {"A": frame}
        monkeypatch.setattr(_state, "downloader", downloader)

        result = await _execute_strategy(StrategyDefinition.from_dict(_ZERO_TRADE_STRATEGY))

        assert result.signal_diagnostics == _ZERO_TRADE_DIAGNOSTICS
        assert result.trades == []

    def test_finalize_response_rehydrates_signal_diagnostics_from_extras(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A cache hit reports the same diagnostics block the miss path stored."""
        equity_df = pl.DataFrame(
            {"date": [date(2024, 1, 2), date(2024, 1, 3)], "equity": [1000.0, 1000.0]}
        )
        result = compute_metrics(equity_df, [], "Test", ["A"])
        cache_obj = MagicMock()
        cache_obj.get_extras.return_value = {"signal_diagnostics": _ZERO_TRADE_DIAGNOSTICS}
        monkeypatch.setattr(_state, "cache", cache_obj)

        response = _finalize_backtest_response(
            result,
            strategy=_make_strategy(),
            cache_hit=True,
            portfolio_result=None,
            cache_key="cache-key",
        )

        assert response["signal_diagnostics"] == _ZERO_TRADE_DIAGNOSTICS
