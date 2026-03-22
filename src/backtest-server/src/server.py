"""Backtest MCP Server — FastMCP entry point with 8 tool registrations."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .clients.fmp_client import FMPClient
from .config import Settings, load_settings
from .data.db import DuckDBManager
from .data.downloader import DataDownloader
from .data.store import DataStore
from .engine.backtester import BacktestConfig, run_backtest, run_multi_symbol_backtest
from .engine.cache import BacktestCache, build_data_fingerprint, make_cache_key
from .engine.indicators import INDICATOR_REGISTRY, compute_indicators, get_supported_indicators
from .engine.metrics import compute_metrics
from .engine.session import session_end as _session_end
from .engine.signals import generate_signals
from .jobs import JobStatus, JobStore
from .logging_config import configure_logging, get_logger
from .models.strategy import (
    BARS_PER_DAY,
    SUPPORTED_TIMEFRAMES,
    IndicatorConfig,
    StrategyDefinition,
)
from .response_utils import format_api_error

logger = get_logger(__name__)

mcp = FastMCP("backtest-server", version=__version__)

# Global state
_fmp_client: FMPClient | None = None
_db_manager: DuckDBManager | None = None
_data_store: DataStore | None = None
_downloader: DataDownloader | None = None
_cache: BacktestCache | None = None
_job_store: JobStore | None = None
_settings: Settings | None = None


def _get_fmp_client() -> FMPClient:
    if _fmp_client is None:
        msg = "FMP client not initialized"
        raise RuntimeError(msg)
    return _fmp_client


def _get_data_store() -> DataStore:
    if _data_store is None:
        msg = "Data store not initialized"
        raise RuntimeError(msg)
    return _data_store


def _get_downloader() -> DataDownloader:
    if _downloader is None:
        msg = "Downloader not initialized"
        raise RuntimeError(msg)
    return _downloader


def _get_cache() -> BacktestCache:
    if _cache is None:
        msg = "Cache not initialized"
        raise RuntimeError(msg)
    return _cache


def _get_job_store() -> JobStore:
    if _job_store is None:
        msg = "Job store not initialized"
        raise RuntimeError(msg)
    return _job_store


# --- Tool 1: Run Strategy Backtest ---


@mcp.tool(
    annotations={
        "title": "Run Strategy Backtest",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def backtest_run_strategy_tool(
    strategy_json: str,
    async_mode: bool | None = None,
) -> dict[str, Any]:
    """Run a backtest for a trading strategy definition.

    Checks cache first. Auto-downloads missing data from FMP.
    Returns metrics directly (sync) or job_id (async mode).

    Args:
        strategy_json: JSON string of the strategy definition.
        async_mode: None=server decides, True=force async, False=force sync.

    Returns:
        BacktestResult dict or job status with job_id.

    """
    try:
        strategy = StrategyDefinition.from_dict(json.loads(strategy_json))
        strategy.validate()
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        return {"isError": True, "error": f"Invalid strategy: {exc}"}

    cache = _get_cache()
    cache_key = _build_cache_key(strategy)
    cached = cache.get(cache_key)
    if cached is not None:
        result = cached.to_dict()
        result["cache_hit"] = True
        return result

    logger.info(
        "async_mode_decision",
        async_mode=async_mode,
        async_mode_type=type(async_mode).__name__,
    )

    if async_mode is True:
        return _submit_async_backtest(strategy, cache_key, explicit=True)

    estimated = _estimate_runtime(strategy)

    if async_mode is None and _settings is not None:
        threshold = _settings.auto_async_threshold_seconds
        logger.info(
            "auto_async_check",
            estimated=round(estimated, 2),
            threshold=threshold,
            will_async=estimated > threshold,
        )
        if estimated > threshold:
            return _submit_async_backtest(
                strategy,
                cache_key,
                explicit=False,
            )

    return await _run_sync_backtest(
        strategy,
        cache_key,
        estimated_seconds=estimated,
    )


# --- Tool 2: Get Job Status ---


@mcp.tool(
    annotations={
        "title": "Get Backtest Job Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def backtest_get_job_status_tool(
    job_id: str,
) -> dict[str, Any]:
    """Check the status of an async backtest job.

    Args:
        job_id: Job ID returned from backtest_run_strategy_tool.

    Returns:
        Job status with result if completed.

    """
    ttl = _settings.job_result_ttl_seconds if _settings else None
    job = _get_job_store().get_job(job_id, ttl_seconds=ttl)
    if job is None:
        return {"isError": True, "error": f"Job not found: {job_id}"}

    result: dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status.value,
    }

    if job.result is not None:
        result["result"] = job.result
    if job.error is not None:
        result["error"] = job.error
    if job.estimated_seconds is not None:
        result["estimated_seconds"] = job.estimated_seconds
    if job.expires_at is not None:
        result["expires_at"] = job.expires_at

    # Suggest next poll time for running jobs
    if job.status == JobStatus.RUNNING and job.estimated_seconds:
        elapsed = (datetime.now(UTC) - datetime.fromisoformat(job.created_at)).total_seconds()
        remaining = max(0.0, job.estimated_seconds - elapsed)
        result["poll_after_seconds"] = _compute_poll_delay(remaining)

    return result


# --- Tool 3: Get Supported Indicators ---


@mcp.tool(
    annotations={
        "title": "Get Supported Indicators",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def backtest_get_supported_indicators_tool() -> dict[str, Any]:
    """List all supported technical indicators with their parameters.

    Returns:
        Dictionary of indicator types with parameter details.

    """
    return get_supported_indicators()


# --- Tool 4: Download Data ---


@mcp.tool(
    annotations={
        "title": "Download OHLCV Data",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def backtest_download_data_tool(
    symbols: list[str],
    start_date: str,
    end_date: str,
    timeframe: str = "daily",
) -> dict[str, Any]:
    """Download OHLCV data from FMP and store in DuckDB.

    Args:
        symbols: List of stock ticker symbols.
        start_date: Start date in YYYY-MM-DD format.
        end_date: End date in YYYY-MM-DD format.
        timeframe: Bar timeframe (daily, 1hour, 15min, 5min).

    Returns:
        Summary of downloaded data per symbol.

    """
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return {"isError": True, "error": f"Unsupported timeframe '{timeframe}'"}

    downloader = _get_downloader()
    results: dict[str, Any] = {}

    for symbol in symbols:
        try:
            df = await downloader.download_symbol(
                symbol,
                start_date,
                end_date,
                timeframe=timeframe,
            )
            results[symbol] = {
                "rows": len(df),
                "timeframe": timeframe,
                "status": "downloaded",
            }
        except Exception as exc:
            logger.exception("download_failed", symbol=symbol)
            results[symbol] = {
                "status": "error",
                "error": format_api_error(exc),
            }

    return {"symbols": results}


# --- Tool 5: List Available Data ---


@mcp.tool(
    annotations={
        "title": "List Available Data",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def backtest_list_available_data_tool(
    symbols: list[str] | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    """Check what OHLCV data is stored locally.

    Design doc: Phase 2.4 — updated for per-timeframe data.

    Args:
        symbols: Optional filter list. If None, lists all.
        timeframe: Optional timeframe filter. If None, shows all timeframes.

    Returns:
        Available data per symbol with date ranges, grouped by timeframe.

    """
    store = _get_data_store()
    timeframes = [timeframe] if timeframe else sorted(SUPPORTED_TIMEFRAMES)

    result: dict[str, Any] = {}
    for tf in timeframes:
        available = store.list_available_symbols(timeframe=tf)
        if symbols is not None:
            available = [s for s in available if s in symbols]

        for sym in available:
            date_range = store.get_date_range(sym, timeframe=tf)
            if date_range:
                if sym not in result:
                    result[sym] = {}
                result[sym][tf] = {
                    "start_date": date_range[0].isoformat(),
                    "end_date": date_range[1].isoformat(),
                }

    return {"symbols": result, "total": len(result)}


# --- Tool 9: Manage Storage ---


@mcp.tool(
    annotations={
        "title": "Manage Data Storage",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def backtest_manage_storage_tool(  # noqa: PLR0911
    action: str,
    timeframe: str | None = None,
    older_than_days: int | None = None,
) -> dict[str, Any]:
    """Manage DuckDB data storage — check status or prune old data.

    Design doc: Phase 2.4.

    Args:
        action: "status" to report DB stats, "prune" to delete old data.
        timeframe: Timeframe to prune (required for prune action).
        older_than_days: Delete data older than this many days (required for prune).

    Returns:
        Storage status or prune results.

    """
    store = _get_data_store()

    if action == "status":
        db_size = store.db.db_size_bytes()
        max_size = _settings.max_db_size_gb * 1_073_741_824 if _settings else 5 * 1_073_741_824
        utilization = db_size / max_size if max_size > 0 else 0.0

        timeframe_stats: dict[str, Any] = {}
        for tf in sorted(SUPPORTED_TIMEFRAMES):
            syms = store.list_available_symbols(timeframe=tf)
            if syms:
                timeframe_stats[tf] = {
                    "symbols": len(syms),
                }

        return {
            "db_size_mb": round(db_size / 1_048_576, 2),
            "max_size_gb": _settings.max_db_size_gb if _settings else 5.0,
            "utilization_pct": round(utilization * 100, 1),
            "timeframes": timeframe_stats,
        }

    if action == "prune":
        if not timeframe:
            return {"isError": True, "error": "timeframe required for prune action"}
        if timeframe not in SUPPORTED_TIMEFRAMES:
            return {"isError": True, "error": f"Unsupported timeframe '{timeframe}'"}
        if not older_than_days or older_than_days <= 0:
            return {"isError": True, "error": "older_than_days must be positive"}

        cutoff = datetime.now() - timedelta(days=older_than_days)

        try:
            # Count rows to delete
            count_result = store.db.conn.execute(
                "SELECT COUNT(*) FROM ohlcv WHERE timeframe = $1 AND timestamp < $2",
                [timeframe, cutoff],
            ).fetchone()
            count = count_result[0] if count_result else 0

            # Delete old data + fix _meta — all under write lock in one transaction
            await store.db.execute_write_many(
                [
                    (
                        "DELETE FROM ohlcv WHERE timeframe = $1 AND timestamp < $2",
                        [timeframe, cutoff],
                    ),
                    # Remove _meta for symbols with no remaining rows
                    (
                        """DELETE FROM _meta
                        WHERE timeframe = $1
                          AND symbol NOT IN (
                              SELECT DISTINCT symbol FROM ohlcv WHERE timeframe = $1
                          )""",
                        [timeframe],
                    ),
                    # Update _meta for symbols that still have rows
                    (
                        """UPDATE _meta SET
                            first_timestamp = sub.min_ts,
                            last_timestamp = sub.max_ts,
                            row_count = sub.cnt,
                            last_refreshed = NOW()
                        FROM (
                            SELECT symbol,
                                   MIN(timestamp) as min_ts,
                                   MAX(timestamp) as max_ts,
                                   COUNT(*) as cnt
                            FROM ohlcv WHERE timeframe = $1
                            GROUP BY symbol
                        ) sub
                        WHERE _meta.symbol = sub.symbol AND _meta.timeframe = $1""",
                        [timeframe],
                    ),
                ]
            )
        except Exception as exc:
            logger.exception("prune_failed", timeframe=timeframe)
            return {"isError": True, "error": f"Prune failed: {format_api_error(exc)}"}

        return {
            "action": "prune",
            "timeframe": timeframe,
            "cutoff_date": cutoff.isoformat(),
            "rows_deleted": count,
        }

    return {"isError": True, "error": f"Unknown action: {action}. Use 'status' or 'prune'."}


# --- Tool 6: Get Trade Log ---


@mcp.tool(
    annotations={
        "title": "Get Trade Log",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def backtest_get_trade_log_tool(
    strategy_json: str,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Get paginated trade-by-trade details for a strategy.

    Args:
        strategy_json: JSON string of the strategy definition.
        limit: Number of trades to return.
        offset: Number of trades to skip.

    Returns:
        Paginated trade list with summary stats.

    """
    try:
        strategy = StrategyDefinition.from_dict(json.loads(strategy_json))
        strategy.validate()
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        return {"isError": True, "error": f"Invalid strategy: {exc}"}

    # Run the strategy (uses cache internally for metrics, but we need trades)
    exec_result = await _execute_strategy(strategy)
    trades = exec_result.trades

    total = len(trades)
    page = trades[offset : offset + limit]
    trade_dicts = [t.to_dict() for t in page]

    return {
        "trades": trade_dicts,
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": offset + limit < total,
    }


# --- Tool 7: Compare Strategies ---


@mcp.tool(
    annotations={
        "title": "Compare Strategies",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def backtest_compare_strategies_tool(
    strategies_json: list[str],
) -> dict[str, Any]:
    """Compare 2-5 strategies side-by-side.

    Runs only uncached strategies; retrieves cached results for others.

    Args:
        strategies_json: List of JSON strategy definition strings.

    Returns:
        Comparison table with all strategies' metrics.

    """
    max_compare = 5
    if len(strategies_json) > max_compare:
        return {
            "isError": True,
            "error": f"Maximum {max_compare} strategies for comparison",
        }

    results: list[dict[str, Any]] = []
    for strat_json in strategies_json:
        try:
            strategy = StrategyDefinition.from_dict(json.loads(strat_json))
            strategy.validate()
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            results.append({"isError": True, "error": str(exc)})
            continue

        cache = _get_cache()
        cache_key = _build_cache_key(strategy)
        cached = cache.get(cache_key)

        if cached is not None:
            entry = cached.to_dict()
            entry["cache_hit"] = True
            results.append(entry)
        else:
            entry_result = await _run_sync_backtest(strategy, cache_key)
            results.append(entry_result)

    return {"strategies": results, "count": len(results)}


# --- Tool 8: Clear Cache ---


@mcp.tool(
    annotations={
        "title": "Clear Backtest Cache",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def backtest_clear_cache_tool(
    strategy_json: str | None = None,
) -> dict[str, Any]:
    """Clear cached backtest results.

    Args:
        strategy_json: If provided, clears only that strategy's cache.
                       If None, clears all cached results.

    Returns:
        Number of cache entries cleared.

    """
    cache = _get_cache()

    if strategy_json is not None:
        try:
            strategy = StrategyDefinition.from_dict(
                json.loads(strategy_json),
            )
            cache_key = _build_cache_key(strategy)
            cleared = cache.clear(cache_key)
        except (json.JSONDecodeError, ValueError, KeyError) as exc:
            return {"isError": True, "error": str(exc)}
    else:
        cleared = cache.clear()

    return {"cleared": cleared}


# --- Internal Helpers ---


def _build_cache_key(strategy: StrategyDefinition) -> str:
    """Build cache key from strategy + data fingerprint."""
    store = _get_data_store()
    symbols = strategy.universe.symbols
    timeframe = strategy.data_config.timeframe
    mtimes: dict[str, float] = {}
    for sym in symbols:
        mtime = store.get_last_modified(sym, timeframe=timeframe)
        if mtime is not None:
            mtimes[sym] = mtime

    fingerprint = build_data_fingerprint(
        symbols=symbols,
        start_date=strategy.data_config.start_date,
        end_date=strategy.data_config.end_date,
        data_mtimes=mtimes,
        timeframe=timeframe,
    )
    return make_cache_key(strategy.cache_key(), fingerprint)


async def _run_sync_backtest(
    strategy: StrategyDefinition,
    cache_key: str,
    estimated_seconds: float | None = None,
) -> dict[str, Any]:
    """Run backtest synchronously and cache the result."""
    start_time = time.monotonic()

    try:
        exec_result = await _execute_strategy(strategy)
    except Exception as exc:
        logger.exception("backtest_failed")
        return {"isError": True, "error": format_api_error(exc)}

    result = compute_metrics(
        equity_df=exec_result.equity_df,
        trades=exec_result.trades,
        strategy_name=strategy.name,
        symbols=strategy.universe.symbols,
        benchmark_df=exec_result.benchmark_df,
        symbol_dfs=exec_result.symbol_dfs,
        requested_start=strategy.data_config.start_date,
        requested_end=strategy.data_config.end_date,
        timeframe=strategy.data_config.timeframe,
    )
    result.warnings = exec_result.warnings

    # Train/test split: compute separate metrics for each period
    train_test = _compute_train_test_split(
        exec_result.equity_df,
        exec_result.trades,
        strategy,
    )

    elapsed_ms = int((time.monotonic() - start_time) * 1000)
    result.execution_time_ms = elapsed_ms

    logger.info(
        "backtest_timing",
        estimated_seconds=estimated_seconds,
        actual_seconds=round(elapsed_ms / 1000, 2),
        symbol_count=len(strategy.universe.symbols),
        indicator_count=len(strategy.indicators),
    )

    _get_cache().put(cache_key, result)

    output = result.to_dict()
    output["cache_hit"] = False
    if train_test is not None:
        output["train_test_split"] = train_test

    # Surface critical warnings at top level so the agent can't miss them
    critical = [w for w in result.warnings if w.startswith(("CRITICAL", "DATA GAP"))]
    if critical:
        output["⚠️ DATA_WARNING"] = critical[0]

    return output


def _compute_train_test_split(
    equity_df: Any,
    trades: list[Any],
    strategy: StrategyDefinition,
) -> dict[str, Any] | None:
    """Compute separate train/test metrics if train_end_date is set.

    Phase 3.8: datetime-aware boundary for intraday. For intraday data,
    the split boundary is end-of-session on the train_end date. Trade
    bucketing uses parsed datetime comparison, not lexical strings.
    """
    train_end = strategy.data_config.get_train_end()
    if equity_df is None or not isinstance(equity_df, pl.DataFrame):
        return None

    timeframe = strategy.data_config.timeframe

    # For intraday, convert split boundary to session close datetime
    # so all bars on the split date are included in the train set
    if timeframe != "daily":
        train_end_dt = _session_end(train_end).replace(tzinfo=None)
        train_eq = equity_df.filter(pl.col("date") <= train_end_dt)
        test_eq = equity_df.filter(pl.col("date") > train_end_dt)
    else:
        train_eq = equity_df.filter(pl.col("date") <= train_end)
        test_eq = equity_df.filter(pl.col("date") > train_end)

    if train_eq.is_empty() or test_eq.is_empty():
        return None

    # Trade bucketing: for intraday, use the session-end datetime string
    # so trades on the split date are correctly included in train set.
    # ISO datetime strings ("2024-06-15T16:00:00") sort after date strings
    # ("2024-06-15"), so we must use the same format as Trade.exit_date.
    train_end_str = train_end_dt.isoformat() if timeframe != "daily" else train_end.isoformat()
    train_trades = [t for t in trades if t.exit_date <= train_end_str]
    test_trades = [t for t in trades if t.exit_date > train_end_str]

    train_result = compute_metrics(
        train_eq,
        train_trades,
        f"{strategy.name} (train)",
        strategy.universe.symbols,
        timeframe=timeframe,
    )
    test_result = compute_metrics(
        test_eq,
        test_trades,
        f"{strategy.name} (test)",
        strategy.universe.symbols,
        timeframe=timeframe,
    )

    return {
        "train_end_date": train_end_str,
        "train": train_result.to_dict(),
        "test": test_result.to_dict(),
    }


_LOW_COVERAGE_THRESHOLD = 0.5  # 50%
_CRITICAL_COVERAGE_THRESHOLD = 0.1  # 10%


def _check_data_coverage(
    symbol_dfs: dict[str, Any],
    requested_start: str,
    requested_end: str,
    timeframe: str,
) -> list[str]:
    """Check if downloaded data covers the requested range.

    Returns prominent warnings when FMP returned significantly less data
    than requested — e.g., FMP plan limits on intraday history.

    Args:
        symbol_dfs: Downloaded DataFrames per symbol.
        requested_start: Requested start date.
        requested_end: Requested end date.
        timeframe: Bar timeframe.

    Returns:
        List of warning strings (empty if coverage is adequate).

    """
    warnings: list[str] = []
    req_start = date.fromisoformat(requested_start)
    req_end = date.fromisoformat(requested_end)
    req_days = (req_end - req_start).days

    if req_days <= 0:
        return warnings

    for symbol, df in symbol_dfs.items():
        if df.is_empty() or "date" not in df.columns:
            warnings.append(
                f"DATA GAP: {symbol} — no {timeframe} data returned by FMP. "
                f"Check your FMP plan's intraday history limits."
            )
            continue

        actual_first = df["date"].min()
        actual_last = df["date"].max()

        # Extract dates for comparison
        if isinstance(actual_first, datetime):
            first_date = actual_first.date()
            last_date = actual_last.date()
        elif isinstance(actual_first, date):
            first_date = actual_first
            last_date = actual_last
        else:
            continue

        actual_days = (last_date - first_date).days
        coverage = actual_days / req_days if req_days > 0 else 1.0

        if coverage < _CRITICAL_COVERAGE_THRESHOLD:
            warnings.append(
                f"CRITICAL DATA GAP: {symbol} {timeframe} — requested {req_days} days "
                f"({requested_start} to {requested_end}) but FMP only returned "
                f"{actual_days} days ({first_date} to {last_date}), {len(df)} bars. "
                f"Coverage: {coverage:.0%}. Your FMP plan likely limits intraday history. "
                f"Results are NOT statistically meaningful."
            )
        elif coverage < _LOW_COVERAGE_THRESHOLD:
            warnings.append(
                f"LOW DATA COVERAGE: {symbol} {timeframe} — requested {req_days} days "
                f"but FMP returned {actual_days} days ({len(df)} bars). "
                f"Coverage: {coverage:.0%}. Consider shortening the date range."
            )

    return warnings


@dataclass
class _ExecutionResult:
    """Internal result bundle from strategy execution."""

    equity_df: Any
    trades: list[Any]
    warnings: list[str]
    benchmark_df: Any | None = None
    symbol_dfs: dict[str, Any] = field(default_factory=dict)


async def _execute_strategy(
    strategy: StrategyDefinition,
) -> _ExecutionResult:
    """Execute a full strategy: download → indicators → signals → backtest."""
    downloader = _get_downloader()
    timeframe = strategy.data_config.timeframe
    symbol_dfs = await downloader.ensure_data(
        symbols=strategy.universe.symbols,
        start_date=strategy.data_config.start_date,
        end_date=strategy.data_config.end_date,
        timeframe=timeframe,
    )

    if not symbol_dfs:
        msg = "No data available for any symbol"
        raise ValueError(msg)

    # Check data coverage — warn prominently if FMP returned far less than requested
    data_warnings = _check_data_coverage(
        symbol_dfs,
        strategy.data_config.start_date,
        strategy.data_config.end_date,
        timeframe,
    )

    # Load benchmark data if configured
    benchmark_df = None
    benchmark_sym = strategy.universe.benchmark
    if benchmark_sym and benchmark_sym not in symbol_dfs:
        bench_dfs = await downloader.ensure_data(
            symbols=[benchmark_sym],
            start_date=strategy.data_config.start_date,
            end_date=strategy.data_config.end_date,
            timeframe=timeframe,
        )
        if benchmark_sym in bench_dfs:
            benchmark_df = bench_dfs[benchmark_sym]

    config = BacktestConfig(
        slippage_pct=0.1,
        commission_pct=0.1,
        timeframe=timeframe,
    )

    all_warnings: list[str] = list(data_warnings)

    if len(symbol_dfs) == 1:
        symbol = next(iter(symbol_dfs))
        enriched, warnings = compute_indicators(
            symbol_dfs[symbol],
            strategy.indicators,
        )
        all_warnings.extend(warnings)
        signaled = generate_signals(
            enriched,
            strategy.entry_rules,
            strategy.exit_rules,
        )
        config.symbol = symbol
        eq, trades = run_backtest(
            signaled,
            strategy.position_sizing,
            strategy.risk_management,
            config,
        )
        return _ExecutionResult(
            equity_df=eq,
            trades=trades,
            warnings=all_warnings,
            benchmark_df=benchmark_df,
            symbol_dfs=symbol_dfs,
        )

    prepped: dict[str, Any] = {}
    for symbol, raw_df in symbol_dfs.items():
        enriched, warnings = compute_indicators(raw_df, strategy.indicators)
        all_warnings.extend(warnings)
        signaled = generate_signals(enriched, strategy.entry_rules, strategy.exit_rules)
        prepped[symbol] = signaled

    eq, trades = run_multi_symbol_backtest(
        prepped,
        strategy.position_sizing,
        strategy.risk_management,
        config,
    )
    return _ExecutionResult(
        equity_df=eq,
        trades=trades,
        warnings=all_warnings,
        benchmark_df=benchmark_df,
        symbol_dfs=symbol_dfs,
    )


def _submit_async_backtest(
    strategy: StrategyDefinition,
    cache_key: str,
    *,
    explicit: bool,
) -> dict[str, Any]:
    """Submit backtest as async job and return job_id.

    Args:
        strategy: Validated strategy definition.
        cache_key: Precomputed cache key.
        explicit: True if caller forced async, False if server auto-decided.

    Returns:
        Job submission response with polling hints.

    """
    job_store = _get_job_store()
    estimated = _estimate_runtime(strategy)

    async def _run() -> dict[str, Any]:
        return await _run_sync_backtest(
            strategy,
            cache_key,
            estimated_seconds=estimated,
        )

    ttl = _settings.job_result_ttl_seconds if _settings else 3600
    expires_at = (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat()

    job_id = job_store.submit_job(
        _run(),
        estimated_seconds=estimated,
        expires_at=expires_at,
    )

    return {
        "job_id": job_id,
        "status": "queued",
        "auto_async": not explicit,
        "estimated_seconds": estimated,
        "poll_after_seconds": _compute_poll_delay(estimated),
        "expires_at": expires_at,
    }


# --- Estimation Helpers ---


def _estimate_runtime(strategy: StrategyDefinition) -> float:
    """Estimate backtest runtime in seconds based on strategy complexity.

    Args:
        strategy: Validated strategy definition.

    Returns:
        Estimated runtime in seconds.

    """
    if _settings is None:
        # Fallback before bootstrap; shouldn't happen in production
        return float(len(strategy.universe.symbols) * 3)

    symbol_count = len(strategy.universe.symbols)
    start = date.fromisoformat(strategy.data_config.start_date)
    end = date.fromisoformat(strategy.data_config.end_date)
    year_span = (end - start).days / 365.25

    # Phase 4.2: multiply by bars-per-day for intraday
    bar_multiplier = BARS_PER_DAY.get(strategy.data_config.timeframe, 1)
    base = symbol_count * year_span * bar_multiplier * _settings.estimate_symbol_year_weight

    indicator_cost = (
        sum(_indicator_weight(ind) for ind in strategy.indicators)
        * _settings.estimate_indicator_weight
    )

    stale_count = _get_downloader().count_stale(
        strategy.universe.symbols,
        timeframe=strategy.data_config.timeframe,
    )
    download_penalty = stale_count * _settings.estimate_download_penalty

    return base + indicator_cost + download_penalty


def _indicator_weight(ind: IndicatorConfig) -> float:
    """Return complexity weight: 1.5 for multi-output, 1.0 for single.

    Args:
        ind: Indicator configuration.

    Returns:
        Weight multiplier for this indicator.

    """
    entry = INDICATOR_REGISTRY.get(ind.type.upper(), {})
    return 1.5 if entry.get("outputs") else 1.0


def _compute_poll_delay(estimated: float) -> int:
    """Compute recommended poll delay clamped to [5, 30] seconds.

    Args:
        estimated: Estimated remaining seconds.

    Returns:
        Recommended poll delay in seconds.

    """
    return max(5, min(int(estimated * 0.6), 30))


# --- Health Check ---


@mcp.custom_route("/health", methods=["GET"])
async def health_check(_request: Request) -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(
        {
            "status": "healthy",
            "server": "backtest-server",
            "version": __version__,
        },
    )


# --- Bootstrap & Main ---


def bootstrap() -> Settings:
    """Initialize all server components (sync).

    Design doc: Phase 1.5 — DuckDBManager replaces direct Parquet DataStore.
    """
    global _fmp_client, _db_manager, _data_store, _downloader, _cache, _job_store, _settings  # noqa: PLW0603

    logger.info("bootstrap_started", server="backtest-server")
    settings = load_settings()
    configure_logging(settings.log_level)

    _settings = settings
    _fmp_client = FMPClient(settings=settings)

    # DuckDB-backed data store (Phase 1.2 + 1.3)
    _db_manager = DuckDBManager(
        db_path=settings.duckdb_path,
        memory_limit=settings.duckdb_memory_limit,
    )
    _db_manager.connect()
    _data_store = DataStore(db=_db_manager)

    _downloader = DataDownloader(
        fmp_client=_fmp_client,
        data_store=_data_store,
        freshness_hours=settings.backtest_data_freshness_hours,
    )
    _cache = BacktestCache(
        cache_dir=settings.backtest_cache_dir,
        ttl_hours=settings.backtest_cache_ttl_hours,
    )
    _job_store = JobStore()

    logger.info(
        "bootstrap_complete",
        duckdb_path=settings.duckdb_path,
        cache_dir=settings.backtest_cache_dir,
    )
    return settings


async def main() -> None:
    """Start the backtest MCP server."""
    settings = bootstrap()

    cors_middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]

    try:
        await mcp.run_async(
            transport=settings.transport,
            host=settings.host,
            port=settings.port,
            path="/mcp",
            stateless_http=True,
            middleware=cors_middleware,
        )
    finally:
        # Graceful shutdown: checkpoint and close DuckDB (Phase 1.2)
        if _db_manager is not None:
            _db_manager.close()


if __name__ == "__main__":
    asyncio.run(main())
