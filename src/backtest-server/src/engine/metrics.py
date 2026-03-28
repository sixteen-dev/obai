"""Performance and risk metric computation for backtest results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from ..models.backtest_result import BacktestResult
from .backtester import Trade
from .portfolio_backtester import PortfolioBacktestResult, PortfolioTradeRecord

TRADING_DAYS_PER_YEAR = 252
RISK_FREE_RATE = 0.0  # Can be parameterized later
MIN_DATA_POINTS = 2  # Minimum data points for statistical calculations

# Phase 3.6: timeframe-dependent annualization factors
BARS_PER_YEAR: dict[str, int] = {
    "daily": 252,
    "1hour": 252 * 7,  # ~1,764 — validate against actual FMP bar count
    "15min": 252 * 26,  # ~6,552
    "5min": 252 * 78,  # ~19,656
}

# Bars per trading day — for data quality expected row calculation
BARS_PER_DAY: dict[str, int] = {
    "daily": 1,
    "1hour": 7,
    "15min": 26,
    "5min": 78,
}


@dataclass
class _DrawdownInfo:
    """Drawdown analysis results."""

    max_drawdown_pct: float
    max_drawdown_start: str
    max_drawdown_end: str


def compute_metrics(  # noqa: PLR0913
    equity_df: pl.DataFrame,
    trades: list[Trade],
    strategy_name: str,
    symbols: list[str],
    benchmark_df: pl.DataFrame | None = None,
    symbol_dfs: dict[str, pl.DataFrame] | None = None,
    requested_start: str = "",
    requested_end: str = "",
    timeframe: str = "daily",
    risk_free_rate: float = RISK_FREE_RATE,
) -> BacktestResult:
    """Compute all performance and risk metrics from backtest results.

    Args:
        equity_df: DataFrame with date and equity columns.
        trades: List of completed trades.
        strategy_name: Name of the strategy.
        symbols: List of symbols traded.
        benchmark_df: Optional benchmark equity curve (date, close).
        symbol_dfs: Raw OHLCV DataFrames per symbol (for data quality).
        requested_start: Requested start date (YYYY-MM-DD).
        requested_end: Requested end date (YYYY-MM-DD).
        timeframe: Bar timeframe for annualization (Phase 3.6).
        risk_free_rate: Annual risk-free rate (default 0.0).

    Returns:
        BacktestResult with all metrics computed.

    """
    bars_per_year = BARS_PER_YEAR.get(timeframe, TRADING_DAYS_PER_YEAR)

    equity = equity_df["equity"].to_numpy().astype(np.float64)
    dates = equity_df["date"].to_list()
    returns = _compute_daily_returns(equity)

    dd = _compute_drawdown(equity, dates)
    cagr = _compute_cagr(equity, dates)
    trading = _compute_trading_stats(trades, timeframe=timeframe)
    bench = _compute_bench_metrics(returns, dates, benchmark_df, bars_per_year, risk_free_rate)

    result = _build_result(
        strategy_name,
        symbols,
        dates,
        equity,
        returns,
        cagr,
        dd,
        trading,
        bench,
        bars_per_year,
        timeframe,
        risk_free_rate,
    )
    result.data_quality = _compute_data_quality(
        symbol_dfs or {},
        requested_start,
        requested_end,
        timeframe,
    )
    return result


def _build_result(  # noqa: PLR0913
    strategy_name: str,
    symbols: list[str],
    dates: list[Any],
    equity: np.ndarray[Any, np.dtype[np.float64]],
    returns: np.ndarray[Any, np.dtype[np.float64]],
    cagr: float,
    dd: _DrawdownInfo,
    trading: _TradingStats,
    bench: _BenchmarkStats,
    bars_per_year: int = TRADING_DAYS_PER_YEAR,
    timeframe: str = "daily",
    risk_free_rate: float = RISK_FREE_RATE,
) -> BacktestResult:
    """Build BacktestResult from computed components."""
    total_return = (
        float(equity[-1] / equity[0] - 1) * 100 if len(equity) >= MIN_DATA_POINTS else 0.0
    )
    sharpe = _compute_sharpe(returns, bars_per_year, risk_free_rate)
    sortino = _compute_sortino(returns, bars_per_year, risk_free_rate)
    calmar = abs(cagr / dd.max_drawdown_pct) if dd.max_drawdown_pct != 0 else 0.0
    volatility = _annualized_volatility(returns, bars_per_year)
    var_95 = float(np.percentile(returns, 5)) * 100 if len(returns) > 0 else 0.0
    downside = _compute_downside_deviation(returns, bars_per_year)

    return BacktestResult(
        strategy_name=strategy_name,
        symbols=symbols,
        period=_format_period(dates),
        total_return_pct=round(total_return, 2),
        cagr_pct=round(cagr, 2),
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        calmar_ratio=round(calmar, 4),
        max_drawdown_pct=dd.max_drawdown_pct,
        max_drawdown_start=dd.max_drawdown_start,
        max_drawdown_end=dd.max_drawdown_end,
        annualized_volatility_pct=round(volatility, 2),
        var_95_pct=round(var_95, 4),
        downside_deviation_pct=round(downside, 4),
        total_trades=trading.total_trades,
        win_rate_pct=trading.win_rate_pct,
        profit_factor=trading.profit_factor,
        avg_trade_return_pct=trading.avg_trade_return_pct,
        avg_holding_days=trading.avg_holding_days,
        avg_holding_minutes=trading.avg_holding_minutes,
        max_consecutive_losses=trading.max_consecutive_losses,
        timeframe=timeframe,
        benchmark_symbol=bench.symbol,
        benchmark_return_pct=bench.return_pct,
        benchmark_cagr_pct=bench.cagr_pct,
        alpha_pct=bench.alpha_pct,
        beta=bench.beta,
        information_ratio=bench.information_ratio,
        yearly_returns=_compute_yearly_returns(
            pl.DataFrame({"date": dates, "equity": equity.tolist()}),
        ),
        data_points_processed=len(equity),
    )


@dataclass
class _TradingStats:
    """Trading statistics bundle."""

    total_trades: int
    win_rate_pct: float
    profit_factor: float
    avg_trade_return_pct: float
    avg_holding_days: float
    max_consecutive_losses: int
    avg_holding_minutes: float = 0.0


@dataclass
class _BenchmarkStats:
    """Benchmark comparison metrics."""

    symbol: str
    return_pct: float
    cagr_pct: float
    alpha_pct: float
    beta: float
    information_ratio: float


def _compute_daily_returns(
    equity: np.ndarray[Any, np.dtype[np.float64]],
) -> np.ndarray[Any, np.dtype[np.float64]]:
    """Compute daily percentage returns from equity curve."""
    returns = np.diff(equity) / equity[:-1]
    return returns.astype(np.float64)


def _format_period(dates: list[Any]) -> str:
    """Format date range as period string."""
    if not dates:
        return ""
    return f"{_to_iso(dates[0])} to {_to_iso(dates[-1])}"


def _to_iso(val: Any) -> str:
    """Convert date value to ISO string."""
    if isinstance(val, date):
        return val.isoformat()
    return str(val)


def _annualized_volatility(
    returns: np.ndarray[Any, np.dtype[np.float64]],
    bars_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Compute annualized volatility (percentage)."""
    if len(returns) < MIN_DATA_POINTS:
        return 0.0
    std = float(np.std(returns, ddof=1))
    return float(std * np.sqrt(bars_per_year) * 100)


def _compute_trading_stats(
    trades: list[Trade],
    timeframe: str = "daily",
) -> _TradingStats:
    """Compute trading statistics from trade list."""
    if not trades:
        return _TradingStats(0, 0.0, 0.0, 0.0, 0.0, 0)

    wins = [t for t in trades if t.return_pct > 0]
    losses = [t for t in trades if t.return_pct <= 0]

    win_rate = len(wins) / len(trades) * 100
    gross_profit = sum(t.return_pct for t in wins)
    gross_loss = abs(sum(t.return_pct for t in losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    avg_ret = sum(t.return_pct for t in trades) / len(trades)
    avg_hold = sum(t.holding_days for t in trades) / len(trades)

    # Phase 3.6: compute avg holding minutes for intraday
    avg_hold_min = 0.0
    if timeframe != "daily":
        avg_hold_min = sum(getattr(t, "holding_minutes", 0) for t in trades) / len(trades)

    return _TradingStats(
        total_trades=len(trades),
        win_rate_pct=round(win_rate, 2),
        profit_factor=round(pf, 4),
        avg_trade_return_pct=round(avg_ret, 4),
        avg_holding_days=round(avg_hold, 1),
        max_consecutive_losses=_max_consecutive_losses(trades),
        avg_holding_minutes=round(avg_hold_min, 1),
    )


def _compute_bench_metrics(
    strategy_returns: np.ndarray[Any, np.dtype[np.float64]],
    dates: list[Any],
    benchmark_df: pl.DataFrame | None,
    bars_per_year: int = TRADING_DAYS_PER_YEAR,
    risk_free_rate: float = RISK_FREE_RATE,
) -> _BenchmarkStats:
    """Compute benchmark-relative metrics."""
    empty = _BenchmarkStats("", 0.0, 0.0, 0.0, 0.0, 0.0)
    if benchmark_df is None or benchmark_df.is_empty():
        return empty

    bench_eq = benchmark_df["close"].to_numpy().astype(np.float64)
    bench_ret = np.diff(bench_eq) / bench_eq[:-1]

    min_len = min(len(strategy_returns), len(bench_ret))
    strat_r = strategy_returns[:min_len]
    bench_r = bench_ret[:min_len]

    total = float(bench_eq[-1] / bench_eq[0] - 1) * 100
    cagr = _compute_cagr(bench_eq, dates)
    beta = _compute_beta(strat_r, bench_r)
    alpha = _compute_alpha(strat_r, bench_r, beta, bars_per_year, risk_free_rate)
    ir = _compute_information_ratio(strat_r, bench_r, bars_per_year)

    symbol = ""
    if "symbol" in benchmark_df.columns:
        sym_val = benchmark_df["symbol"][0]
        symbol = str(sym_val) if sym_val is not None else ""

    return _BenchmarkStats(
        symbol=symbol,
        return_pct=round(total, 2),
        cagr_pct=round(cagr, 2),
        alpha_pct=round(alpha, 4),
        beta=round(beta, 4),
        information_ratio=round(ir, 4),
    )


def _compute_yearly_returns(
    equity_df: pl.DataFrame,
) -> dict[str, float]:
    """Compute year-by-year returns."""
    if equity_df.is_empty():
        return {}

    df = equity_df.with_columns(
        pl.col("date").dt.year().alias("year"),
    )
    years = sorted(df["year"].unique().to_list())
    yearly: dict[str, float] = {}

    for year in years:
        year_data = df.filter(pl.col("year") == year)
        if year_data.is_empty():
            continue
        start_eq = year_data["equity"][0]
        end_eq = year_data["equity"][-1]
        if start_eq and start_eq > 0:
            ret = (end_eq / start_eq - 1) * 100
            yearly[str(year)] = round(ret, 2)

    return yearly


def _compute_cagr(
    equity: np.ndarray[Any, np.dtype[np.float64]],
    dates: list[Any],
) -> float:
    """Compute compound annual growth rate."""
    if len(equity) < MIN_DATA_POINTS or equity[0] <= 0:
        return 0.0

    years = _compute_years(dates)
    if years <= 0:
        return 0.0

    ratio = float(equity[-1] / equity[0])
    return float((ratio ** (1 / years) - 1) * 100)


def _compute_years(dates: list[Any]) -> float:
    """Compute number of years in a date range."""
    if len(dates) < MIN_DATA_POINTS:
        return 0.0

    first, last = dates[0], dates[-1]
    if isinstance(first, date) and isinstance(last, date):
        return (last - first).days / 365.25

    return len(dates) / TRADING_DAYS_PER_YEAR


def _compute_sharpe(
    returns: np.ndarray[Any, np.dtype[np.float64]],
    bars_per_year: int = TRADING_DAYS_PER_YEAR,
    risk_free_rate: float = RISK_FREE_RATE,
) -> float:
    """Compute annualized Sharpe ratio."""
    if len(returns) < MIN_DATA_POINTS:
        return 0.0
    std = float(np.std(returns, ddof=1))
    if std == 0:
        return 0.0
    bar_rf = risk_free_rate / bars_per_year
    excess = float(np.mean(returns)) - bar_rf
    return float(excess / std * np.sqrt(bars_per_year))


def _compute_sortino(
    returns: np.ndarray[Any, np.dtype[np.float64]],
    bars_per_year: int = TRADING_DAYS_PER_YEAR,
    risk_free_rate: float = RISK_FREE_RATE,
) -> float:
    """Compute annualized Sortino ratio.

    Uses per-bar downside std (not annualized) to avoid double-annualization.
    Formula: (mean_excess / per_bar_downside_std) * sqrt(bars_per_year).
    """
    if len(returns) < MIN_DATA_POINTS:
        return 0.0
    negative = returns[returns < 0]
    if len(negative) == 0:
        return 0.0
    downside_std = float(np.std(negative, ddof=1))
    if downside_std == 0:
        return 0.0
    bar_rf = risk_free_rate / bars_per_year
    excess = float(np.mean(returns)) - bar_rf
    return float(excess / downside_std * np.sqrt(bars_per_year))


def _compute_downside_deviation(
    returns: np.ndarray[Any, np.dtype[np.float64]],
    bars_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Compute annualized downside deviation (percentage)."""
    negative = returns[returns < 0]
    if len(negative) == 0:
        return 0.0
    dd = float(np.std(negative, ddof=1))
    return float(dd * np.sqrt(bars_per_year) * 100)


def _compute_drawdown(
    equity: np.ndarray[Any, np.dtype[np.float64]],
    dates: list[Any],
) -> _DrawdownInfo:
    """Compute maximum drawdown and its date range."""
    if len(equity) < MIN_DATA_POINTS:
        return _DrawdownInfo(0.0, "", "")

    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max

    trough_idx = int(np.argmin(drawdowns))
    peak_idx = int(np.argmax(equity[: trough_idx + 1]))
    max_dd = float(drawdowns[trough_idx]) * 100

    return _DrawdownInfo(
        max_drawdown_pct=round(max_dd, 2),
        max_drawdown_start=_to_iso(dates[peak_idx]),
        max_drawdown_end=_to_iso(dates[trough_idx]),
    )


def _compute_beta(
    strategy: np.ndarray[Any, np.dtype[np.float64]],
    benchmark: np.ndarray[Any, np.dtype[np.float64]],
) -> float:
    """Compute portfolio beta vs benchmark."""
    if len(strategy) < MIN_DATA_POINTS or len(benchmark) < MIN_DATA_POINTS:
        return 0.0
    bench_var = float(np.var(benchmark, ddof=1))
    if bench_var == 0:
        return 0.0
    covar = float(np.cov(strategy, benchmark, ddof=1)[0, 1])
    return covar / bench_var


def _compute_alpha(
    strategy: np.ndarray[Any, np.dtype[np.float64]],
    benchmark: np.ndarray[Any, np.dtype[np.float64]],
    beta: float,
    bars_per_year: int = TRADING_DAYS_PER_YEAR,
    risk_free_rate: float = RISK_FREE_RATE,
) -> float:
    """Compute Jensen's alpha (annualized, percentage)."""
    strat_mean = float(np.mean(strategy))
    bench_mean = float(np.mean(benchmark))
    bar_rf = risk_free_rate / bars_per_year
    bar_alpha = strat_mean - (bar_rf + beta * (bench_mean - bar_rf))
    return float(bar_alpha * bars_per_year * 100)


def _compute_information_ratio(
    strategy: np.ndarray[Any, np.dtype[np.float64]],
    benchmark: np.ndarray[Any, np.dtype[np.float64]],
    bars_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Compute information ratio (active return / tracking error)."""
    active = strategy - benchmark
    tracking_error = float(np.std(active, ddof=1))
    if tracking_error == 0:
        return 0.0
    mean_active = float(np.mean(active))
    return float(mean_active / tracking_error * np.sqrt(bars_per_year))


def _max_consecutive_losses(trades: list[Trade]) -> int:
    """Compute maximum consecutive losing trades."""
    max_streak = 0
    current_streak = 0
    for trade in trades:
        if trade.return_pct <= 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def _compute_data_quality(
    symbol_dfs: dict[str, pl.DataFrame],
    requested_start: str,
    requested_end: str,
    timeframe: str = "daily",
) -> dict[str, Any]:
    """Compute data quality metadata from in-memory DataFrames.

    Zero extra latency — data is already loaded for the backtest.

    Args:
        symbol_dfs: Raw OHLCV DataFrames per symbol.
        requested_start: Requested start date (YYYY-MM-DD).
        requested_end: Requested end date (YYYY-MM-DD).
        timeframe: Bar timeframe for expected row calculation.

    Returns:
        Dict with per-symbol and aggregate quality stats.

    """
    if not symbol_dfs:
        return {}

    per_symbol: dict[str, dict[str, Any]] = {}
    total_rows = 0
    total_expected = 0
    total_zero_prices = 0

    for symbol, df in symbol_dfs.items():
        rows = len(df)
        total_rows += rows

        # Expected bars (trading days * bars per day)
        expected = expected_trading_days(requested_start, requested_end, timeframe)
        total_expected += expected

        coverage = round(rows / expected * 100, 1) if expected > 0 else 0.0

        # Actual date range from the data
        actual_start = ""
        actual_end = ""
        if "date" in df.columns and rows > 0:
            first = df["date"].min()
            last = df["date"].max()
            if isinstance(first, date):
                actual_start = first.isoformat()
            if isinstance(last, date):
                actual_end = last.isoformat()

        # Count rows with zero or null close prices
        zero_prices = 0
        if "close" in df.columns and rows > 0:
            zero_prices = int(
                df.filter(pl.col("close").is_null() | (pl.col("close") == 0.0)).height
            )
        total_zero_prices += zero_prices

        per_symbol[symbol] = {
            "rows": rows,
            "expected_rows": expected,
            "actual_start": actual_start,
            "actual_end": actual_end,
            "coverage_pct": coverage,
            "zero_or_null_prices": zero_prices,
        }

    agg_coverage = round(total_rows / total_expected * 100, 1) if total_expected > 0 else 0.0

    return {
        "requested_start": requested_start,
        "requested_end": requested_end,
        "symbols": per_symbol,
        "total_rows": total_rows,
        "total_expected_rows": total_expected,
        "coverage_pct": agg_coverage,
        "total_zero_or_null_prices": total_zero_prices,
    }


def expected_trading_days(
    start_str: str,
    end_str: str,
    timeframe: str = "daily",
) -> int:
    """Estimate expected data points for a date range and timeframe.

    Args:
        start_str: Start date (YYYY-MM-DD).
        end_str: End date (YYYY-MM-DD).
        timeframe: Bar timeframe (Phase 3.6: multiplied by bars per day).

    Returns:
        Estimated data points (trading days × bars per day).

    """
    if not start_str or not end_str:
        return 0
    try:
        start = date.fromisoformat(start_str)
        end = date.fromisoformat(end_str)
    except ValueError:
        return 0
    calendar_days = (end - start).days
    trading_days = max(0, int(calendar_days * TRADING_DAYS_PER_YEAR / 365.25))
    bpd = BARS_PER_DAY.get(timeframe, 1)
    return trading_days * bpd


def compute_portfolio_metrics(  # noqa: PLR0913
    result: PortfolioBacktestResult,
    initial_capital: float,
    dates: list[Any],
    timeframe: str = "daily",
    strategy_name: str = "",
    symbols: list[str] | None = None,
) -> dict[str, Any]:
    """Compute metrics for a portfolio backtest result.

    Reuses compute_metrics() for standard metrics, then adds portfolio-specific
    metrics like capital utilization, turnover, and position count stats.

    Args:
        result: Portfolio backtest result.
        initial_capital: Starting capital amount.
        dates: List of dates corresponding to equity curve.
        timeframe: Bar timeframe for annualization.
        strategy_name: Name of the strategy.
        symbols: List of symbols traded.

    Returns:
        Dict with all standard and portfolio-specific metrics.

    """
    if not result.equity_curve or len(result.equity_curve) < MIN_DATA_POINTS:
        return {"isError": True, "error": "Insufficient data for metrics"}

    # Build Trade objects from PortfolioTradeRecords for compute_metrics
    trades = _convert_portfolio_trades(result.trades)

    equity_df = pl.DataFrame(
        {
            "date": dates[: len(result.equity_curve)],
            "equity": result.equity_curve,
        }
    )

    base_result = compute_metrics(
        equity_df=equity_df,
        trades=trades,
        strategy_name=strategy_name,
        symbols=symbols or [],
        timeframe=timeframe,
    )

    output = base_result.to_dict()
    output["portfolio_metrics"] = _compute_portfolio_specific(result, initial_capital)
    return output


def _convert_portfolio_trades(
    records: list[PortfolioTradeRecord],
) -> list[Trade]:
    """Convert PortfolioTradeRecords to Trade objects for compute_metrics.

    Args:
        records: Portfolio trade records.

    Returns:
        List of Trade objects.

    """
    return [
        Trade(
            symbol=r.symbol,
            entry_date=r.entry_date,
            entry_price=r.entry_price,
            exit_date=r.exit_date,
            exit_price=r.exit_price,
            return_pct=r.return_pct,
            holding_days=r.holding_days,
            exit_reason=r.exit_reason,
        )
        for r in records
    ]


def _compute_portfolio_specific(
    result: PortfolioBacktestResult,
    initial_capital: float,
) -> dict[str, Any]:
    """Compute portfolio-specific metrics.

    Args:
        result: Portfolio backtest result.
        initial_capital: Starting capital amount.

    Returns:
        Dict of portfolio-specific metrics.

    """
    equity = np.array(result.equity_curve, dtype=np.float64)
    pos_counts = np.array(result.daily_position_counts, dtype=np.float64)

    # Capital utilization: mean of (1 - cash/equity) across days
    # We approximate cash/equity as: when positions = 0, utilization = 0
    # Otherwise, utilization = 1 - (equity - invested) / equity
    # Since we don't track daily cash separately, use position counts
    cap_util = 0.0
    if len(equity) > 0 and len(result.trades) > 0:
        # Rough estimation: mean(position_count / max_observed)
        max_count = float(np.max(pos_counts)) if np.max(pos_counts) > 0 else 1.0
        cap_util = float(np.mean(pos_counts) / max_count * 100)

    # Turnover rate: sum of absolute trade PnL / mean equity
    mean_equity = float(np.mean(equity)) if len(equity) > 0 else initial_capital
    total_abs_pnl = sum(abs(t.pnl) for t in result.trades)
    turnover = total_abs_pnl / mean_equity if mean_equity > 0 else 0.0

    # Unique skipped symbols
    skipped_symbols = sorted({s["symbol"] for s in result.signals_skipped if "symbol" in s})

    return {
        "capital_utilization_pct": round(cap_util, 2),
        "turnover_rate": round(turnover, 4),
        "position_count_max": int(np.max(pos_counts)) if len(pos_counts) > 0 else 0,
        "position_count_avg": round(float(np.mean(pos_counts)), 2) if len(pos_counts) > 0 else 0.0,
        "signals_skipped_count": len(result.signals_skipped),
        "signals_skipped_symbols": skipped_symbols,
    }
