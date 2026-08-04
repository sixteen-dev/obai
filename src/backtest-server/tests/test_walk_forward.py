"""Tests for walk-forward validation engine."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock

import polars as pl
import pytest

from src import server
from src.engine.walk_forward import (
    _compute_aggregates,
    _extract_metrics,
    generate_windows,
    walk_forward_validate,
)
from src.models.strategy import StrategyDefinition, WalkForwardResult, WindowResult


class TestGenerateWindows:
    """Tests for generate_windows function."""

    def test_five_windows_seven_year_range(self) -> None:
        """Verify correct expanding windows for a 7-year range with 5 windows."""
        windows = generate_windows("2018-01-01", "2024-12-31", n_windows=5)

        assert len(windows) == 5

        # All windows should start at the same date (expanding)
        for train_start, _, _, _ in windows:
            assert train_start == "2018-01-01"

        # Train end should expand each window
        train_ends = [w[1] for w in windows]
        for i in range(1, len(train_ends)):
            assert train_ends[i] > train_ends[i - 1]

        # Test start should be day after train end
        for _, train_end, test_start, _ in windows:
            assert test_start > train_end

        # Last window's test_end should be the full end date
        assert windows[-1][3] == "2024-12-31"

    def test_insufficient_range_raises_error(self) -> None:
        """Verify ValueError for date ranges too short for requested windows."""
        with pytest.raises(ValueError, match="Walk-forward requires"):
            generate_windows("2023-01-01", "2024-06-01", n_windows=5)

    def test_insufficient_range_two_years_three_windows(self) -> None:
        """Two years is not enough for 3 windows (needs 4 years)."""
        with pytest.raises(ValueError, match="Walk-forward requires"):
            generate_windows("2022-01-01", "2024-01-01", n_windows=3)

    def test_boundary_dates_no_overlap(self) -> None:
        """Verify no date overlap between train end and test start."""
        windows = generate_windows("2018-01-01", "2024-12-31", n_windows=5)

        for _, train_end, test_start, _ in windows:
            assert test_start > train_end, (
                f"Train end {train_end} overlaps with test start {test_start}"
            )

    def test_adjacent_windows_no_gap(self) -> None:
        """Test windows should be adjacent (no large gaps between windows)."""
        windows = generate_windows("2018-01-01", "2024-12-31", n_windows=5)

        for i in range(1, len(windows)):
            prev_test_end = windows[i - 1][3]
            curr_train_end = windows[i][1]
            # Current train end must be >= previous test end (expanding)
            assert curr_train_end >= prev_test_end

    def test_three_windows_four_year_range(self) -> None:
        """Verify correct windows for minimum viable range."""
        windows = generate_windows("2020-01-01", "2024-01-01", n_windows=3)

        assert len(windows) == 3
        # All train starts should be 2020-01-01
        for train_start, _, _, _ in windows:
            assert train_start == "2020-01-01"

    def test_single_window(self) -> None:
        """Verify single window with 2-year range."""
        windows = generate_windows("2020-01-01", "2022-01-01", n_windows=1)

        assert len(windows) == 1
        assert windows[0][0] == "2020-01-01"
        assert windows[0][3] == "2022-01-01"


class TestComputeAggregates:
    """Tests for aggregate metric computation."""

    def test_aggregate_metrics_known_values(self) -> None:
        """Mock 3 windows with known sharpe/win_rate/drawdown, verify aggregates."""
        train_1 = {
            "sharpe_ratio": 1.5,
            "win_rate_pct": 60.0,
            "max_drawdown_pct": -10.0,
        }
        test_1 = {
            "sharpe_ratio": 0.8,
            "win_rate_pct": 55.0,
            "max_drawdown_pct": -15.0,
        }
        train_2 = {
            "sharpe_ratio": 1.2,
            "win_rate_pct": 58.0,
            "max_drawdown_pct": -12.0,
        }
        test_2 = {
            "sharpe_ratio": -0.3,
            "win_rate_pct": 45.0,
            "max_drawdown_pct": -20.0,
        }
        train_3 = {
            "sharpe_ratio": 1.0,
            "win_rate_pct": 55.0,
            "max_drawdown_pct": -8.0,
        }
        test_3 = {
            "sharpe_ratio": 0.5,
            "win_rate_pct": 52.0,
            "max_drawdown_pct": -18.0,
        }
        windows = [
            WindowResult(
                window_id=1,
                train_start="2018-01-01",
                train_end="2019-12-31",
                test_start="2020-01-01",
                test_end="2020-12-31",
                train_metrics=train_1,
                test_metrics=test_1,
            ),
            WindowResult(
                window_id=2,
                train_start="2018-01-01",
                train_end="2020-12-31",
                test_start="2021-01-01",
                test_end="2021-12-31",
                train_metrics=train_2,
                test_metrics=test_2,
            ),
            WindowResult(
                window_id=3,
                train_start="2018-01-01",
                train_end="2021-12-31",
                test_start="2022-01-01",
                test_end="2022-12-31",
                train_metrics=train_3,
                test_metrics=test_3,
            ),
        ]

        result = _compute_aggregates(windows, execution_config={}, total_runtime=42.0)

        assert result.n_windows == 3
        # mean([0.8, -0.3, 0.5]) = 1.0/3 ≈ 0.3333
        assert abs(result.mean_test_sharpe - (0.8 + (-0.3) + 0.5) / 3) < 0.001
        # mean([55.0, 45.0, 52.0]) ≈ 50.667
        assert abs(result.mean_test_win_rate - (55.0 + 45.0 + 52.0) / 3) < 0.01
        # mean([-15.0, -20.0, -18.0]) ≈ -17.667
        assert abs(result.mean_test_max_drawdown - (-15.0 + (-20.0) + (-18.0)) / 3) < 0.01
        assert result.total_runtime_seconds == 42.0

    def test_consistency_score_all_positive(self) -> None:
        """All windows with positive test Sharpe should yield 100% consistency."""
        windows = [
            WindowResult(
                window_id=i + 1,
                train_start="2018-01-01",
                train_end="2020-12-31",
                test_start="2021-01-01",
                test_end="2021-12-31",
                train_metrics={"sharpe_ratio": 1.0},
                test_metrics={"sharpe_ratio": sharpe},
            )
            for i, sharpe in enumerate([0.5, 1.2, 0.8, 0.3, 0.1])
        ]

        result = _compute_aggregates(windows, execution_config={}, total_runtime=10.0)

        assert result.consistency_score == 100.0

    def test_consistency_score_mixed(self) -> None:
        """2 out of 5 positive Sharpe windows should yield 40% consistency."""
        sharpes = [0.5, -0.3, -0.1, 0.2, -0.5]
        windows = [
            WindowResult(
                window_id=i + 1,
                train_start="2018-01-01",
                train_end="2020-12-31",
                test_start="2021-01-01",
                test_end="2021-12-31",
                train_metrics={"sharpe_ratio": 1.0},
                test_metrics={"sharpe_ratio": s},
            )
            for i, s in enumerate(sharpes)
        ]

        result = _compute_aggregates(windows, execution_config={}, total_runtime=10.0)

        assert result.consistency_score == 40.0

    def test_degradation_calculation(self) -> None:
        """Verify degradation = mean(train_sharpe - test_sharpe)."""
        windows = [
            WindowResult(
                window_id=1,
                train_start="2018-01-01",
                train_end="2019-12-31",
                test_start="2020-01-01",
                test_end="2020-12-31",
                train_metrics={"sharpe_ratio": 2.0},
                test_metrics={"sharpe_ratio": 1.0},
            ),
            WindowResult(
                window_id=2,
                train_start="2018-01-01",
                train_end="2020-12-31",
                test_start="2021-01-01",
                test_end="2021-12-31",
                train_metrics={"sharpe_ratio": 1.5},
                test_metrics={"sharpe_ratio": 0.5},
            ),
        ]

        result = _compute_aggregates(windows, execution_config={}, total_runtime=5.0)

        # mean([2.0-1.0, 1.5-0.5]) = mean([1.0, 1.0]) = 1.0
        assert abs(result.degradation - 1.0) < 0.001


class TestWalkForwardValidate:
    """Tests for the walk_forward_validate async function."""

    @pytest.fixture()
    def sample_strategy_json(self) -> str:
        """Return a valid strategy JSON for walk-forward testing."""
        return json.dumps(
            {
                "name": "WF Test Strategy",
                "universe": {"symbols": ["AAPL"], "benchmark": "SPY"},
                "data_config": {
                    "start_date": "2018-01-01",
                    "end_date": "2024-12-31",
                },
                "indicators": [
                    {"id": "sma_50", "type": "SMA", "params": {"length": 50}, "source": "close"},
                ],
                "entry_rules": {
                    "logic": "AND",
                    "conditions": [
                        {
                            "left": {"indicator": "sma_50"},
                            "operator": "greater_than",
                            "right": {"constant": 100.0},
                        },
                    ],
                },
                "exit_rules": {
                    "logic": "OR",
                    "conditions": [
                        {
                            "left": {"indicator": "sma_50"},
                            "operator": "less_than",
                            "right": {"constant": 100.0},
                        },
                    ],
                },
            }
        )

    async def test_calls_backtest_correct_number_of_times(
        self,
        sample_strategy_json: str,
    ) -> None:
        """Verify run_backtest_fn is called 2xN times (train + test per window)."""
        n_windows = 3
        mock_result: dict[str, Any] = {
            "performance": {
                "sharpe_ratio": 1.0,
                "sortino_ratio": 1.2,
                "total_return_pct": 15.0,
                "cagr_pct": 10.0,
            },
            "risk": {"max_drawdown_pct": -5.0},
            "trading": {
                "win_rate_pct": 55.0,
                "total_trades": 20,
                "profit_factor": 1.5,
            },
        }

        mock_fn = AsyncMock(return_value=mock_result)

        result = await walk_forward_validate(
            strategy_json=sample_strategy_json,
            n_windows=n_windows,
            run_backtest_fn=mock_fn,
        )

        assert mock_fn.call_count == n_windows * 2
        assert isinstance(result, WalkForwardResult)
        assert result.n_windows == n_windows
        assert len(result.windows) == n_windows

    async def test_serializes_execution_and_cost_assumptions(
        self,
        sample_strategy_json: str,
    ) -> None:
        """The stored payload must retain the assumptions the windows ran under.

        ``to_dict`` carried windows and aggregates only, so a later turn could
        not state slippage, commission, or starting capital without inventing
        them. The values must track the strategy, not be a fixed echo.
        """
        strategy = json.loads(sample_strategy_json)
        strategy["execution_config"] = {"slippage_pct": 0.25, "commission_pct": 0.05}
        mock_fn = AsyncMock(
            return_value={
                "performance": {
                    "sharpe_ratio": 1.0,
                    "sortino_ratio": 1.2,
                    "total_return_pct": 15.0,
                    "cagr_pct": 10.0,
                },
                "risk": {"max_drawdown_pct": -5.0},
                "trading": {"win_rate_pct": 55.0, "total_trades": 20, "profit_factor": 1.5},
            }
        )

        result = await walk_forward_validate(
            strategy_json=json.dumps(strategy),
            n_windows=2,
            run_backtest_fn=mock_fn,
        )

        assumptions = result.to_dict()["execution_config"]

        assert assumptions["slippage_pct"] == 0.25
        assert assumptions["commission_pct"] == 0.05
        assert assumptions["initial_capital"] == 100_000.0

    async def test_window_dates_passed_correctly(
        self,
        sample_strategy_json: str,
    ) -> None:
        """Verify each backtest call receives correct date ranges."""
        mock_result: dict[str, Any] = {
            "performance": {
                "sharpe_ratio": 0.5,
                "sortino_ratio": 0.6,
                "total_return_pct": 5.0,
                "cagr_pct": 3.0,
            },
            "risk": {"max_drawdown_pct": -3.0},
            "trading": {
                "win_rate_pct": 50.0,
                "total_trades": 10,
                "profit_factor": 1.0,
            },
        }

        called_strategies: list[dict[str, Any]] = []

        async def capture_fn(strategy_json: str) -> dict[str, Any]:
            called_strategies.append(json.loads(strategy_json))
            return mock_result

        await walk_forward_validate(
            strategy_json=sample_strategy_json,
            n_windows=3,
            run_backtest_fn=capture_fn,
        )

        # 6 calls total: 3 train + 3 test
        assert len(called_strategies) == 6

        # All should have train_end_date set to None (no split within windows)
        for strat in called_strategies:
            assert strat["data_config"]["train_end_date"] is None

        # First call should be the train period of window 1
        first_train = called_strategies[0]
        assert first_train["data_config"]["start_date"] == "2018-01-01"

    async def test_result_to_dict_serializable(
        self,
        sample_strategy_json: str,
    ) -> None:
        """Verify the WalkForwardResult can be serialized to JSON."""
        mock_result: dict[str, Any] = {
            "performance": {
                "sharpe_ratio": 1.0,
                "sortino_ratio": 1.0,
                "total_return_pct": 10.0,
                "cagr_pct": 8.0,
            },
            "risk": {"max_drawdown_pct": -5.0},
            "trading": {
                "win_rate_pct": 55.0,
                "total_trades": 15,
                "profit_factor": 1.3,
            },
        }
        mock_fn = AsyncMock(return_value=mock_result)

        result = await walk_forward_validate(
            strategy_json=sample_strategy_json,
            n_windows=3,
            run_backtest_fn=mock_fn,
        )

        result_dict = result.to_dict()
        # Should be JSON serializable
        json_str = json.dumps(result_dict)
        assert isinstance(json_str, str)
        assert result_dict["n_windows"] == 3
        assert len(result_dict["windows"]) == 3

    async def test_invalid_date_range_raises_error(self) -> None:
        """Verify ValueError for strategy with insufficient date range."""
        short_strategy = json.dumps(
            {
                "name": "Short Range",
                "universe": {"symbols": ["AAPL"], "benchmark": "SPY"},
                "data_config": {
                    "start_date": "2023-01-01",
                    "end_date": "2024-01-01",
                },
                "indicators": [
                    {"id": "sma_50", "type": "SMA", "params": {"length": 50}, "source": "close"},
                ],
                "entry_rules": {
                    "logic": "AND",
                    "conditions": [
                        {
                            "left": {"indicator": "sma_50"},
                            "operator": "greater_than",
                            "right": {"constant": 100.0},
                        },
                    ],
                },
                "exit_rules": {
                    "logic": "OR",
                    "conditions": [
                        {
                            "left": {"indicator": "sma_50"},
                            "operator": "less_than",
                            "right": {"constant": 100.0},
                        },
                    ],
                },
            }
        )

        mock_fn = AsyncMock()

        with pytest.raises(ValueError, match="Walk-forward requires"):
            await walk_forward_validate(
                strategy_json=short_strategy,
                n_windows=5,
                run_backtest_fn=mock_fn,
            )

        # Should not have called backtest at all
        mock_fn.assert_not_called()


class TestWindowResult:
    """Tests for WindowResult dataclass."""

    def test_to_dict(self) -> None:
        """Verify WindowResult serialization."""
        wr = WindowResult(
            window_id=1,
            train_start="2018-01-01",
            train_end="2019-12-31",
            test_start="2020-01-01",
            test_end="2020-12-31",
            train_metrics={"sharpe_ratio": 1.5},
            test_metrics={"sharpe_ratio": 0.8},
        )

        d = wr.to_dict()
        assert d["window_id"] == 1
        assert d["train_start"] == "2018-01-01"
        assert d["test_metrics"]["sharpe_ratio"] == 0.8


class TestWalkForwardResult:
    """Tests for WalkForwardResult dataclass."""

    def test_to_dict_rounding(self) -> None:
        """Verify WalkForwardResult serialization with rounding."""
        result = WalkForwardResult(
            windows=[],
            n_windows=5,
            mean_test_sharpe=0.33333333,
            std_test_sharpe=0.12345678,
            mean_test_win_rate=52.666666,
            mean_test_max_drawdown=-15.333333,
            consistency_score=60.0,
            degradation=0.45678901,
            total_runtime_seconds=42.123456,
            execution_config={"slippage_pct": 0.1, "commission_pct": 0.1},
        )

        d = result.to_dict()
        assert d["execution_config"] == {"slippage_pct": 0.1, "commission_pct": 0.1}
        assert d["mean_test_sharpe"] == 0.3333
        assert d["std_test_sharpe"] == 0.1235
        assert d["mean_test_win_rate"] == 52.67
        assert d["mean_test_max_drawdown"] == -15.33
        assert d["consistency_score"] == 60.0
        assert d["degradation"] == 0.4568
        assert d["total_runtime_seconds"] == 42.12
        assert d["failed_windows"] == 0


class TestExtractMetrics:
    """Tests for _extract_metrics error detection."""

    def test_error_result_marked_failed(self) -> None:
        """A result with 'error' key should be marked as failed."""
        result = _extract_metrics({"error": "data not found"})
        assert result["_failed"] is True
        assert result["error"] == "data not found"

    def test_missing_performance_marked_failed(self) -> None:
        """A result without 'performance' key should be marked as failed."""
        result = _extract_metrics({"some_other_key": 42})
        assert result["_failed"] is True

    def test_valid_result_not_failed(self) -> None:
        """A valid result should not have _failed flag."""
        result = _extract_metrics(
            {
                "performance": {"sharpe_ratio": 1.0},
                "risk": {},
                "trading": {},
            }
        )
        assert "_failed" not in result
        assert result["sharpe_ratio"] == 1.0


class TestFailedWindowHandling:
    """Tests for walk-forward handling of failed backtest windows."""

    def test_failed_window_excluded_from_aggregates(self) -> None:
        """Failed windows should be excluded from metric averages."""
        # Window 1: valid (sharpe=0.8)
        # Window 2: failed (both train and test)
        # Window 3: valid (sharpe=1.2)
        train_1 = {
            "sharpe_ratio": 1.5,
            "win_rate_pct": 60.0,
            "max_drawdown_pct": -10.0,
        }
        test_1 = {
            "sharpe_ratio": 0.8,
            "win_rate_pct": 55.0,
            "max_drawdown_pct": -15.0,
        }
        train_3 = {
            "sharpe_ratio": 1.0,
            "win_rate_pct": 58.0,
            "max_drawdown_pct": -8.0,
        }
        test_3 = {
            "sharpe_ratio": 1.2,
            "win_rate_pct": 52.0,
            "max_drawdown_pct": -12.0,
        }
        failed = {"_failed": True, "error": "data not found"}
        windows = [
            WindowResult(
                window_id=1,
                train_start="2018-01-01",
                train_end="2019-12-31",
                test_start="2020-01-01",
                test_end="2020-12-31",
                train_metrics=train_1,
                test_metrics=test_1,
            ),
            WindowResult(
                window_id=2,
                train_start="2018-01-01",
                train_end="2020-12-31",
                test_start="2021-01-01",
                test_end="2021-12-31",
                train_metrics=failed,
                test_metrics=failed,
            ),
            WindowResult(
                window_id=3,
                train_start="2018-01-01",
                train_end="2021-12-31",
                test_start="2022-01-01",
                test_end="2022-12-31",
                train_metrics=train_3,
                test_metrics=test_3,
            ),
        ]

        result = _compute_aggregates(windows, execution_config={}, total_runtime=30.0)

        assert result.n_windows == 3
        assert result.failed_windows == 1
        # Aggregates computed from 2 valid windows only
        # mean([0.8, 1.2]) = 1.0
        assert abs(result.mean_test_sharpe - 1.0) < 0.001
        # mean([55.0, 52.0]) = 53.5
        assert abs(result.mean_test_win_rate - 53.5) < 0.01
        # Consistency: 2/2 positive = 100%
        assert result.consistency_score == 100.0

    def test_all_windows_failed(self) -> None:
        """When all windows fail, aggregates should be zero."""
        windows = [
            WindowResult(
                window_id=i + 1,
                train_start="2018-01-01",
                train_end="2019-12-31",
                test_start="2020-01-01",
                test_end="2020-12-31",
                train_metrics={"_failed": True, "error": "data not found"},
                test_metrics={"_failed": True, "error": "data not found"},
            )
            for i in range(3)
        ]

        result = _compute_aggregates(windows, execution_config={}, total_runtime=10.0)

        assert result.n_windows == 3
        assert result.failed_windows == 3
        assert result.mean_test_sharpe == 0.0
        assert result.std_test_sharpe == 0.0
        assert result.mean_test_win_rate == 0.0
        assert result.mean_test_max_drawdown == 0.0
        assert result.consistency_score == 0.0
        assert result.degradation == 0.0

    async def test_walk_forward_with_error_results(self) -> None:
        """Walk-forward should handle backtest functions returning errors."""
        strategy_json = json.dumps(
            {
                "name": "Error Test",
                "universe": {"symbols": ["AAPL"], "benchmark": "SPY"},
                "data_config": {
                    "start_date": "2018-01-01",
                    "end_date": "2024-12-31",
                },
                "indicators": [
                    {"id": "sma_50", "type": "SMA", "params": {"length": 50}, "source": "close"},
                ],
                "entry_rules": {
                    "logic": "AND",
                    "conditions": [
                        {
                            "left": {"indicator": "sma_50"},
                            "operator": "greater_than",
                            "right": {"constant": 100.0},
                        },
                    ],
                },
                "exit_rules": {
                    "logic": "OR",
                    "conditions": [
                        {
                            "left": {"indicator": "sma_50"},
                            "operator": "less_than",
                            "right": {"constant": 100.0},
                        },
                    ],
                },
            }
        )

        call_count = 0

        async def mixed_fn(strat_json: str) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            # Fail every 3rd and 4th call (= window 2 train + test)
            if call_count in (3, 4):
                return {"error": "data not found"}
            return {
                "performance": {
                    "sharpe_ratio": 1.0,
                    "sortino_ratio": 1.2,
                    "total_return_pct": 15.0,
                    "cagr_pct": 10.0,
                },
                "risk": {"max_drawdown_pct": -5.0},
                "trading": {
                    "win_rate_pct": 55.0,
                    "total_trades": 20,
                    "profit_factor": 1.5,
                },
            }

        result = await walk_forward_validate(
            strategy_json=strategy_json,
            n_windows=3,
            run_backtest_fn=mixed_fn,
        )

        assert result.n_windows == 3
        assert result.failed_windows == 1
        # Aggregates should only be from 2 valid windows
        assert result.mean_test_sharpe == 1.0


class _StubDownloader:
    """Serves synthetic OHLCV slices for any requested range and symbol."""

    def __init__(self, series: pl.DataFrame) -> None:
        """Store the backing series and record requested fetch starts."""
        self._series = series
        self.requested_starts: list[str] = []

    async def ensure_data(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        timeframe: str = "daily",
    ) -> dict[str, pl.DataFrame]:
        """Return the backing series sliced to [start_date, end_date] per symbol."""
        self.requested_starts.append(start_date)
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        window = self._series.filter(pl.col("date").is_between(start, end))
        return {symbol: window for symbol in symbols}


class TestTestWindowWarmup:
    """Indicator warm-up guards walk-forward test windows from being signal-dead.

    Regression for accuracy.md §8: a short window with a long-lookback indicator
    must fetch a pre-roll before its start so the indicator is live across the
    whole window, instead of null for its first ``lookback`` bars.
    """

    _WINDOW_DAYS = 250
    _SMA_LENGTH = 200

    def _uptrend_series(self, series_start: date, end: date) -> pl.DataFrame:
        """Strictly increasing daily OHLCV so close > SMA on every live bar."""
        total = (end - series_start).days + 1
        dates = [series_start + timedelta(days=i) for i in range(total)]
        closes = [100.0 + i * 0.1 for i in range(total)]
        return pl.DataFrame(
            {
                "date": dates,
                "open": closes,
                "high": [c + 1.0 for c in closes],
                "low": [c - 1.0 for c in closes],
                "close": closes,
                "volume": [1_000_000] * total,
            }
        )

    def _strategy_json(self, start: str, end: str) -> str:
        """Long-lookback SMA strategy: enter while close > SMA, exit when below."""
        return json.dumps(
            {
                "name": "Warmup Window Test",
                "universe": {"symbols": ["TEST"], "benchmark": "SPY"},
                "data_config": {"start_date": start, "end_date": end},
                "indicators": [
                    {
                        "id": "sma_slow",
                        "type": "SMA",
                        "params": {"length": self._SMA_LENGTH},
                        "source": "close",
                    },
                ],
                "entry_rules": {
                    "logic": "AND",
                    "conditions": [
                        {
                            "left": {"indicator": "close"},
                            "operator": "greater_than",
                            "right": {"indicator": "sma_slow"},
                        },
                    ],
                },
                "exit_rules": {
                    "logic": "OR",
                    "conditions": [
                        {
                            "left": {"indicator": "close"},
                            "operator": "less_than",
                            "right": {"indicator": "sma_slow"},
                        },
                    ],
                },
            }
        )

    async def test_test_window_not_signal_dead(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A short window with SMA-200 must be live (in-market) for ~every bar."""
        requested_start = date(2021, 1, 1)
        requested_end = requested_start + timedelta(days=self._WINDOW_DAYS - 1)
        series = self._uptrend_series(requested_start - timedelta(days=500), requested_end)
        stub = _StubDownloader(series)
        monkeypatch.setattr(server._state, "downloader", stub)

        strategy = StrategyDefinition.from_dict(
            json.loads(self._strategy_json(requested_start.isoformat(), requested_end.isoformat()))
        )
        exec_result = await server._execute_strategy(strategy)

        equity = exec_result.equity_df["equity"].to_list()
        window_len = len(equity)
        # Out-of-position bars copy the prior equity exactly, so the bars that
        # moved are precisely the live (in-market) bars.
        active_bars = sum(1 for i in range(1, window_len) if equity[i] != equity[i - 1])

        assert window_len == self._WINDOW_DAYS
        # Warm-up primes SMA across the whole window → live for ~every bar.
        # Without it SMA is null for the first ~SMA_LENGTH bars, collapsing
        # active_bars to ~window_len - SMA_LENGTH (~50) — the signal-dead failure.
        assert active_bars >= 0.9 * window_len
