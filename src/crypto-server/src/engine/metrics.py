"""Crypto backtest metric calculations."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

MIN_EQUITY_POINTS = 2


@dataclass(frozen=True)
class MetricResult:
    """Metric bundle plus warnings."""

    metrics: dict[str, Any]
    warnings: list[str]


@dataclass(frozen=True)
class EquityStats:
    """Derived equity-series inputs for metric calculations."""

    equity: np.ndarray
    returns: np.ndarray
    years: float
    periods_per_year: float
    start_equity: float
    end_equity: float


def compute_metrics(
    equity_curve: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> MetricResult:
    """Compute crypto-native performance metrics from an equity curve."""
    warnings: list[str] = []
    if len(equity_curve) < MIN_EQUITY_POINTS:
        return MetricResult(metrics={}, warnings=["not_enough_equity_points"])

    stats = _equity_stats(equity_curve)
    metrics: dict[str, Any] = {
        "starting_equity": stats.start_equity,
        "ending_equity": stats.end_equity,
        "total_return": (
            stats.end_equity / stats.start_equity - 1.0 if stats.start_equity else None
        ),
        **_risk_metrics(stats, warnings),
        **_trade_metrics(trades, stats.equity, warnings),
        "periods_per_year_effective": stats.periods_per_year,
        "elapsed_years": stats.years,
    }
    return MetricResult(metrics=metrics, warnings=warnings)


def _equity_stats(equity_curve: list[dict[str, Any]]) -> EquityStats:
    equity = np.array([float(point["equity"]) for point in equity_curve], dtype=float)
    start_dt = _parse_dt(str(equity_curve[0]["timestamp"]))
    end_dt = _parse_dt(str(equity_curve[-1]["timestamp"]))
    years = max((end_dt - start_dt).total_seconds() / (365.2425 * 24 * 3600), 1e-9)
    returns = equity[1:] / equity[:-1] - 1.0
    return EquityStats(
        equity=equity,
        returns=returns,
        years=years,
        periods_per_year=len(returns) / years,
        start_equity=float(equity[0]),
        end_equity=float(equity[-1]),
    )


def _risk_metrics(stats: EquityStats, warnings: list[str]) -> dict[str, float | None]:
    returns = stats.returns
    start_equity = stats.start_equity
    end_equity = stats.end_equity
    years = stats.years
    periods_per_year = stats.periods_per_year

    cagr = (end_equity / start_equity) ** (1.0 / years) - 1.0 if start_equity > 0 else None
    vol = _sample_std(returns) * math.sqrt(periods_per_year) if len(returns) > 1 else None
    mean_return = float(np.mean(returns)) if len(returns) else 0.0
    annualized_mean = mean_return * periods_per_year
    sharpe = _safe_ratio(annualized_mean, vol, "zero_volatility", warnings)
    downside = np.minimum(returns, 0.0)
    downside_dev = float(math.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
    sortino_denom = downside_dev * math.sqrt(periods_per_year)
    sortino = _safe_ratio(annualized_mean, sortino_denom, "zero_downside_deviation", warnings)
    max_drawdown = _max_drawdown(stats.equity)
    calmar = (
        _safe_ratio(cagr, abs(max_drawdown), "zero_drawdown", warnings)
        if cagr is not None
        else None
    )
    return {
        "cagr": cagr,
        "volatility": vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
    }


def _trade_metrics(
    trades: list[dict[str, Any]],
    equity: np.ndarray,
    warnings: list[str],
) -> dict[str, Any]:
    wins = [float(t["realized_pnl"]) for t in trades if float(t.get("realized_pnl", 0.0)) > 0]
    losses = [float(t["realized_pnl"]) for t in trades if float(t.get("realized_pnl", 0.0)) < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = _safe_ratio(gross_profit, gross_loss, "zero_gross_loss", warnings)
    traded_notional = sum(float(t.get("notional", 0.0)) for t in trades)
    mean_equity = float(np.mean(equity))
    turnover = _safe_ratio(traded_notional, mean_equity, "zero_mean_equity", warnings)
    trade_count = len(trades)
    return {
        "profit_factor": profit_factor,
        "turnover": turnover,
        "trade_count": trade_count,
        "hit_rate": (len(wins) / trade_count) if trade_count else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    }


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _sample_std(values: np.ndarray) -> float:
    if len(values) < MIN_EQUITY_POINTS:
        return 0.0
    return float(np.std(values, ddof=1))


def _max_drawdown(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    drawdown = equity / peak - 1.0
    return float(np.min(drawdown))


def _safe_ratio(
    numerator: float | None,
    denominator: float | None,
    warning: str,
    warnings: list[str],
) -> float | None:
    if numerator is None or denominator is None or denominator == 0.0:
        warnings.append(warning)
        return None
    return float(numerator / denominator)
