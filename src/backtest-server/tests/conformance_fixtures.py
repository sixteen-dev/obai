"""Deterministic golden fixtures for backtest-engine conformance tests."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl

from src.engine.backtester import Trade

OHLCV_CLOSES: list[float] = [
    100.0,
    101.0,
    102.0,
    101.0,
    103.0,
    104.0,
    106.0,
    105.0,
    107.0,
    108.0,
    110.0,
    109.0,
    111.0,
    113.0,
    112.0,
    114.0,
    116.0,
    115.0,
    117.0,
    119.0,
]

OHLCV_OPENS: list[float] = [
    99.0,
    100.0,
    101.0,
    102.0,
    102.0,
    103.0,
    105.0,
    106.0,
    106.0,
    107.0,
    109.0,
    110.0,
    110.0,
    112.0,
    113.0,
    113.0,
    115.0,
    116.0,
    116.0,
    118.0,
]

OHLCV_HIGHS: list[float] = [
    101.0,
    102.0,
    103.0,
    103.0,
    104.0,
    105.0,
    107.0,
    107.0,
    108.0,
    109.0,
    111.0,
    111.0,
    112.0,
    114.0,
    114.0,
    115.0,
    117.0,
    117.0,
    118.0,
    120.0,
]

OHLCV_LOWS: list[float] = [
    98.0,
    99.0,
    100.0,
    100.0,
    101.0,
    102.0,
    104.0,
    104.0,
    105.0,
    106.0,
    108.0,
    108.0,
    109.0,
    111.0,
    111.0,
    112.0,
    114.0,
    114.0,
    115.0,
    117.0,
]

OHLCV_VOLUMES: list[int] = [
    1000,
    1200,
    1100,
    1300,
    1250,
    1400,
    1500,
    1450,
    1600,
    1550,
    1700,
    1650,
    1800,
    1900,
    1750,
    1850,
    1950,
    2000,
    2100,
    2200,
]

INDICATOR_TAIL_GOLDEN: dict[str, list[float]] = {
    "sma_5": [111.8, 113.2, 114.0, 114.8, 116.2],
    "rsi_5": [81.2791734278, 85.766003479, 74.5924907881, 80.8346117848, 85.3374759136],
    "macd_macd": [1.431639191, 1.6958695114, 1.3336846957, 1.4423780192, 1.7037144892],
    "macd_signal": [1.4288126256, 1.5623410685, 1.4480128821, 1.4451954507, 1.5744549699],
    "macd_hist": [0.0028265654, 0.1335284429, -0.1143281864, -0.0028174314, 0.1292595193],
    "bb_upper": [115.2409301068, 116.6409301068, 116.8284271247, 118.2409301068, 119.6409301068],
    "bb_middle": [111.8, 113.2, 114.0, 114.8, 116.2],
    "bb_lower": [108.3590698932, 109.7590698932, 111.1715728753, 111.3590698932, 112.7590698932],
    "stoch_slowk": [79.9603174603, 79.9603174603, 79.9603174603, 79.9603174603, 79.9603174603],
    "stoch_slowd": [80.4894179894, 79.9603174603, 79.9603174603, 79.9603174603, 79.9603174603],
    "atr_5": [3.0, 3.0, 3.0, 3.0, 3.0],
    "obv": [11700.0, 13650.0, 11650.0, 13750.0, 15950.0],
}

PERIOD_RETURNS: list[float] = [
    0.012,
    -0.018,
    0.024,
    -0.011,
    0.017,
    -0.023,
    0.031,
    -0.007,
    0.014,
    -0.016,
    0.022,
    -0.009,
]

BENCHMARK_RETURNS: list[float] = [
    0.010,
    -0.012,
    0.018,
    -0.006,
    0.012,
    -0.015,
    0.020,
    -0.004,
    0.009,
    -0.010,
    0.015,
    -0.005,
]

METRIC_GOLDEN: dict[str, float | int | str] = {
    "total_return_pct": 3.46,
    "cagr_pct": 181.54,
    "sharpe_ratio": 2.4617,
    "sortino_ratio": 4.3339,
    "calmar_ratio": 78.9304,
    "max_drawdown_pct": -2.3,
    "max_drawdown_start": "2024-01-07",
    "max_drawdown_end": "2024-01-08",
    "annualized_volatility_pct": 29.9,
    "var_95_pct": -2.025,
    "downside_deviation_pct": 16.9826,
    "benchmark_return_pct": 3.16,
    "benchmark_cagr_pct": 157.65,
    "alpha_pct": -23.9745,
    "beta": 1.4965,
    "information_ratio": 0.8323,
    "win_rate_pct": 66.67,
    "profit_factor": 2.5771,
    "avg_trade_return_pct": 2.3896,
    "avg_holding_days": 1.7,
    "max_consecutive_losses": 1,
}


def golden_ohlcv_df() -> pl.DataFrame:
    """Return deterministic OHLCV data for indicator parity checks."""
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2) + timedelta(days=i) for i in range(len(OHLCV_CLOSES))],
            "open": OHLCV_OPENS,
            "high": OHLCV_HIGHS,
            "low": OHLCV_LOWS,
            "close": OHLCV_CLOSES,
            "volume": OHLCV_VOLUMES,
        }
    )


def golden_equity_df() -> pl.DataFrame:
    """Return an equity curve generated from PERIOD_RETURNS."""
    equity = [100_000.0]
    for ret in PERIOD_RETURNS:
        equity.append(float(equity[-1] * (1 + ret)))
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2) + timedelta(days=i) for i in range(len(equity))],
            "equity": equity,
        }
    )


def golden_benchmark_df() -> pl.DataFrame:
    """Return a benchmark price curve generated from BENCHMARK_RETURNS."""
    close = [100.0]
    for ret in BENCHMARK_RETURNS:
        close.append(float(close[-1] * (1 + ret)))
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2) + timedelta(days=i) for i in range(len(close))],
            "close": close,
            "symbol": ["SPY"] * len(close),
        }
    )


def golden_metric_trades() -> list[Trade]:
    """Return deterministic trade records for trade-stat conformance."""
    return [
        Trade("TST", "2024-01-03", 100.0, "2024-01-05", 106.0, 6.0, 2, "signal"),
        Trade(
            "TST",
            "2024-01-08",
            110.0,
            "2024-01-10",
            105.0,
            -4.5454545455,
            2,
            "stop_loss",
        ),
        Trade("TST", "2024-01-11", 105.0, "2024-01-12", 111.0, 5.7142857143, 1, "take_profit"),
    ]


def signal_df(  # noqa: PLR0913
    opens: list[float],
    closes: list[float],
    entries: list[bool],
    exits: list[bool],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[int] | None = None,
) -> pl.DataFrame:
    """Build deterministic OHLCV plus signal data."""
    n = len(opens)
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2) + timedelta(days=i) for i in range(n)],
            "open": opens,
            "high": highs
            if highs is not None
            else [max(o, c) + 1.0 for o, c in zip(opens, closes, strict=True)],
            "low": lows
            if lows is not None
            else [min(o, c) - 1.0 for o, c in zip(opens, closes, strict=True)],
            "close": closes,
            "volume": volumes if volumes is not None else [1_000_000] * n,
            "entry_signal": entries,
            "exit_signal": exits,
        }
    )


def null_safe_round_tail(values: list[Any], tail_size: int = 5) -> list[float | None]:
    """Round the last values in a numeric sequence while preserving nulls."""
    rounded: list[float | None] = []
    for value in values[-tail_size:]:
        if value is None:
            rounded.append(None)
        else:
            rounded.append(round(float(value), 10))
    return rounded


def random_walk_ohlcv(seed: int, n: int = 120) -> pl.DataFrame:
    """Build a deterministic daily random-walk OHLCV frame.

    Uses ``random.Random`` rather than numpy so the sequence is identical on
    every platform and the pinned values in the causality suite stay valid.
    Draw order is fixed: every close, then the opens, highs, lows and volumes.

    Args:
        seed: Seed for the pseudo-random generator.
        n: Number of daily bars to generate.

    Returns:
        Frame with date, open, high, low, close and volume columns.

    """
    rng = random.Random(seed)  # noqa: S311 — reproducible fixtures, not cryptography
    closes = [100.0]
    for _ in range(n - 1):
        closes.append(round(closes[-1] * (1 + rng.uniform(-0.03, 0.03)), 4))
    opens = [round(close * (1 + rng.uniform(-0.01, 0.01)), 4) for close in closes]
    pairs = list(zip(opens, closes, strict=True))
    highs = [round(max(o, c) * (1 + rng.uniform(0.0, 0.02)), 4) for o, c in pairs]
    lows = [round(min(o, c) * (1 - rng.uniform(0.0, 0.02)), 4) for o, c in pairs]
    volumes = [rng.randint(1000, 5000) for _ in range(n)]
    return pl.DataFrame(
        {
            "date": [date(2024, 1, 2) + timedelta(days=i) for i in range(n)],
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def _intraday_timestamps(sessions: int, bars: int) -> list[datetime]:
    """Return naive bar-start timestamps for consecutive intraday sessions."""
    stamps: list[datetime] = []
    for session in range(sessions):
        day = datetime(2024, 1, 2) + timedelta(days=session)
        opening = day.replace(hour=9, minute=30)
        stamps.extend(opening + timedelta(minutes=5 * b) for b in range(bars))
    return stamps


def random_walk_intraday(seed: int, sessions: int = 6, bars: int = 10) -> pl.DataFrame:
    """Build a deterministic 5-minute frame carrying its own entry/exit signals.

    Signals are drawn directly rather than derived from indicators so the
    intraday causality case exercises session handling, not indicator warm-up.
    Draw order is fixed: closes, opens, entry flags, exit flags.

    Args:
        seed: Seed for the pseudo-random generator.
        sessions: Number of consecutive sessions to generate.
        bars: Number of 5-minute bars per session.

    Returns:
        Frame with date, OHLCV and entry_signal/exit_signal columns.

    """
    rng = random.Random(seed)  # noqa: S311 — reproducible fixtures, not cryptography
    total = sessions * bars
    closes = [100.0]
    for _ in range(total - 1):
        closes.append(round(closes[-1] * (1 + rng.uniform(-0.01, 0.01)), 4))
    opens = [round(close * (1 + rng.uniform(-0.003, 0.003)), 4) for close in closes]
    entries = [rng.random() < 0.15 for _ in range(total)]
    exits = [rng.random() < 0.10 for _ in range(total)]
    pairs = list(zip(opens, closes, strict=True))
    return pl.DataFrame(
        {
            "date": _intraday_timestamps(sessions, bars),
            "open": opens,
            "high": [round(max(o, c) * 1.002, 4) for o, c in pairs],
            "low": [round(min(o, c) * 0.998, 4) for o, c in pairs],
            "close": closes,
            "volume": [1000] * total,
            "entry_signal": entries,
            "exit_signal": exits,
        }
    )
