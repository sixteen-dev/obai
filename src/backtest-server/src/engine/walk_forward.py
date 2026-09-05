"""Walk-forward validation engine for robust out-of-sample testing.

Runs the existing backtester multiple times across expanding time windows
to detect overfitting and measure strategy consistency.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from statistics import mean, stdev
from typing import Any

from ..logging_config import get_logger
from ..models.strategy import StrategyDefinition, WalkForwardResult, WindowResult

logger = get_logger(__name__)

_MIN_SEGMENTS = 2  # At least 1 train segment + 1 test segment


def generate_windows(
    start_date: str,
    end_date: str,
    n_windows: int,
) -> list[tuple[str, str, str, str]]:
    """Generate expanding walk-forward validation windows.

    Divides the date range into n_windows+1 equal segments. The first segment
    is the minimum training period. Each subsequent window expands the training
    set by one segment and tests on the next segment.

    Args:
        start_date: Start of the full date range (YYYY-MM-DD).
        end_date: End of the full date range (YYYY-MM-DD).
        n_windows: Number of walk-forward windows to generate.

    Returns:
        List of (train_start, train_end, test_start, test_end) tuples.

    Raises:
        ValueError: If the date range is too short for the requested windows.

    """
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    total_days = (end - start).days

    min_segments = n_windows + 1
    min_required_days = min_segments * 365
    if total_days < min_required_days:
        max_windows = max((total_days // 365) - 1, 0)
        msg = (
            f"Walk-forward requires >= {min_required_days} days for {n_windows} windows, "
            f"but date range is only {total_days} days "
            f"({start_date} to {end_date}). "
            f"Maximum windows for this range: {max_windows}"
        )
        raise ValueError(msg)

    segment_days = total_days / min_segments
    windows: list[tuple[str, str, str, str]] = []

    for i in range(n_windows):
        train_start = start
        # Train covers segments 0..i+1 (i.e., i+1 segments)
        train_end_dt = start + timedelta(days=int(segment_days * (i + 1)))
        # Ensure train_end doesn't exceed end_date - 1 segment
        train_end_str = train_end_dt.isoformat()

        # Test covers segment i+1..i+2
        test_start_dt = train_end_dt + timedelta(days=1)
        test_end_dt = start + timedelta(days=int(segment_days * (i + 2)))
        # Last window snaps to end_date
        if i == n_windows - 1:
            test_end_dt = end

        windows.append(
            (
                train_start.isoformat(),
                train_end_str,
                test_start_dt.isoformat(),
                test_end_dt.isoformat(),
            )
        )

    return windows


async def walk_forward_validate(
    strategy_json: str,
    n_windows: int,
    run_backtest_fn: Callable[[str], Awaitable[dict[str, Any]]],
) -> WalkForwardResult:
    """Run walk-forward validation across expanding time windows.

    For each window, runs the backtest twice: once on the training period
    and once on the test period. Collects metrics and computes aggregate
    statistics to detect overfitting.

    Args:
        strategy_json: JSON string of the strategy definition.
        n_windows: Number of walk-forward windows.
        run_backtest_fn: Async callable that takes a strategy JSON string
            and returns a BacktestResult dict.

    Returns:
        WalkForwardResult with per-window and aggregate metrics.

    Raises:
        ValueError: If the strategy or date range is invalid.

    """
    strategy_dict = json.loads(strategy_json)
    # Resolve through the same parser the per-window backtests use, so the
    # reported assumptions are the ones actually applied — defaults included —
    # rather than a restatement of whatever the caller happened to send.
    resolved_strategy = StrategyDefinition.from_dict(strategy_dict).to_dict()
    execution_config = resolved_strategy["execution_config"]
    data_config = strategy_dict.get("data_config", {})
    start_date = data_config.get("start_date", "")
    end_date = data_config.get("end_date", "")

    windows = generate_windows(start_date, end_date, n_windows)
    start_time = time.monotonic()

    async def _run_window(
        idx: int,
        train_start: str,
        train_end: str,
        test_start: str,
        test_end: str,
    ) -> WindowResult:
        logger.info(
            "walk_forward_window",
            window=idx + 1,
            total=n_windows,
            train=f"{train_start} to {train_end}",
            test=f"{test_start} to {test_end}",
        )

        train_strategy = _modify_strategy_dates(strategy_dict, train_start, train_end)
        test_strategy = _modify_strategy_dates(strategy_dict, test_start, test_end)

        # Train and test within a window are independent — run concurrently
        train_result, test_result = await asyncio.gather(
            run_backtest_fn(json.dumps(train_strategy)),
            run_backtest_fn(json.dumps(test_strategy)),
        )
        train_metrics = _extract_metrics(train_result)
        test_metrics = _extract_metrics(test_result)

        if train_metrics.get("_failed") or test_metrics.get("_failed"):
            logger.warning(
                "walk_forward_window_failed",
                window=idx + 1,
                train_failed=bool(train_metrics.get("_failed")),
                test_failed=bool(test_metrics.get("_failed")),
                train_error=train_metrics.get("error"),
                test_error=test_metrics.get("error"),
            )

        return WindowResult(
            window_id=idx + 1,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            train_metrics=train_metrics,
            test_metrics=test_metrics,
        )

    # All windows are independent — run concurrently
    window_results = list(
        await asyncio.gather(*[_run_window(i, *w) for i, w in enumerate(windows)])
    )

    elapsed = time.monotonic() - start_time
    return _compute_aggregates(
        window_results,
        elapsed,
        execution_config=execution_config,
        strategy=resolved_strategy,
    )


def _modify_strategy_dates(
    strategy_dict: dict[str, Any],
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Create a copy of strategy dict with modified date range.

    Removes train_end_date since each window is either fully train or
    fully test.

    Args:
        strategy_dict: Original strategy definition dict.
        start_date: New start date.
        end_date: New end date.

    Returns:
        Modified strategy dict (shallow copy with new data_config).

    """
    modified = dict(strategy_dict)
    modified["data_config"] = {
        **strategy_dict.get("data_config", {}),
        "start_date": start_date,
        "end_date": end_date,
        "train_end_date": None,
    }
    return modified


def _extract_metrics(result: dict[str, Any]) -> dict[str, Any]:
    """Extract key metrics from a backtest result dict.

    Handles both flat and nested result formats. Detects error results
    and marks them as failed instead of defaulting to zero metrics.

    Args:
        result: BacktestResult.to_dict() output.

    Returns:
        Flat dict with key metric values. Contains ``_failed: True``
        and ``error`` if the backtest returned an error.

    """
    if "error" in result or "performance" not in result:
        return {"_failed": True, "error": result.get("error", "Unknown failure")}

    perf = result.get("performance", {})
    risk = result.get("risk", {})
    trading = result.get("trading", {})

    return {
        "sharpe_ratio": perf.get("sharpe_ratio", 0.0),
        "sortino_ratio": perf.get("sortino_ratio", 0.0),
        "total_return_pct": perf.get("total_return_pct", 0.0),
        "cagr_pct": perf.get("cagr_pct", 0.0),
        "max_drawdown_pct": risk.get("max_drawdown_pct", 0.0),
        "win_rate_pct": trading.get("win_rate_pct", 0.0),
        "total_trades": trading.get("total_trades", 0),
        "profit_factor": trading.get("profit_factor", 0.0),
        # None, not 0 — zero means the indicators ran unprimed, which is a real
        # and alarming finding. A backtest that simply did not report its
        # pre-roll must not be made to assert it.
        "warmup_bars": result.get("warmup_bars"),
        # Coverage gaps and unprimed indicators are what make a fold's numbers
        # unreliable; aggregates still count the fold, but the reader sees why.
        "warnings": result.get("warnings", []),
    }


def _compute_aggregates(
    window_results: list[WindowResult],
    total_runtime: float,
    *,
    execution_config: dict[str, Any],
    strategy: dict[str, Any],
) -> WalkForwardResult:
    """Compute aggregate statistics from per-window results.

    Failed windows (where train or test returned an error) are excluded
    from metric averages. The ``failed_windows`` count reports how many
    were dropped.

    Args:
        window_results: List of WindowResult from each walk-forward window.
        total_runtime: Total elapsed time in seconds.
        execution_config: Resolved execution and cost assumptions the windows
            ran under, carried into the result so they survive serialization.
        strategy: Resolved strategy definition the windows validated, carried
            for the same reason - a polled job must describe what it ran.

    Returns:
        WalkForwardResult with all aggregate metrics.

    """
    n = len(window_results)

    # Filter out failed windows
    valid_windows = [
        w
        for w in window_results
        if not w.test_metrics.get("_failed") and not w.train_metrics.get("_failed")
    ]
    failed_count = n - len(valid_windows)

    if not valid_windows:
        # All windows failed — return zeroed aggregates
        return WalkForwardResult(
            windows=window_results,
            n_windows=n,
            mean_test_sharpe=0.0,
            std_test_sharpe=0.0,
            mean_test_win_rate=0.0,
            mean_test_max_drawdown=0.0,
            consistency_score=0.0,
            degradation=0.0,
            total_runtime_seconds=total_runtime,
            execution_config=execution_config,
            strategy=strategy,
            failed_windows=failed_count,
        )

    v = len(valid_windows)
    test_sharpes = [w.test_metrics.get("sharpe_ratio", 0.0) for w in valid_windows]
    train_sharpes = [w.train_metrics.get("sharpe_ratio", 0.0) for w in valid_windows]
    test_win_rates = [w.test_metrics.get("win_rate_pct", 0.0) for w in valid_windows]
    test_drawdowns = [w.test_metrics.get("max_drawdown_pct", 0.0) for w in valid_windows]

    mean_test_sharpe = mean(test_sharpes) if test_sharpes else 0.0
    std_test_sharpe = stdev(test_sharpes) if len(test_sharpes) >= 2 else 0.0  # noqa: PLR2004
    mean_test_win_rate = mean(test_win_rates) if test_win_rates else 0.0
    mean_test_drawdown = mean(test_drawdowns) if test_drawdowns else 0.0

    positive_count = sum(1 for s in test_sharpes if s > 0)
    consistency = (positive_count / v * 100) if v > 0 else 0.0

    degradations = [train_sharpes[i] - test_sharpes[i] for i in range(v)]
    mean_degradation = mean(degradations) if degradations else 0.0

    return WalkForwardResult(
        windows=window_results,
        n_windows=n,
        mean_test_sharpe=mean_test_sharpe,
        std_test_sharpe=std_test_sharpe,
        mean_test_win_rate=mean_test_win_rate,
        mean_test_max_drawdown=mean_test_drawdown,
        consistency_score=consistency,
        degradation=mean_degradation,
        total_runtime_seconds=total_runtime,
        execution_config=execution_config,
        strategy=strategy,
        failed_windows=failed_count,
    )
