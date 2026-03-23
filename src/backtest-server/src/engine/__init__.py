"""Backtest engine - indicators, signals, backtester, metrics, and caching."""

from .backtester import BacktestConfig, Trade, run_backtest, run_multi_symbol_backtest
from .indicators import compute_indicators, get_supported_indicators
from .metrics import compute_metrics
from .signals import generate_signals
from .walk_forward import generate_windows, walk_forward_validate

__all__ = [
    "BacktestConfig",
    "Trade",
    "compute_indicators",
    "compute_metrics",
    "generate_signals",
    "generate_windows",
    "get_supported_indicators",
    "run_backtest",
    "run_multi_symbol_backtest",
    "walk_forward_validate",
]
