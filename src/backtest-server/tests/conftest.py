"""Shared test fixtures for backtest-server."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import polars as pl
import pytest

from src.engine.backtester import Trade
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


@pytest.fixture()
def sample_ohlcv_df() -> pl.DataFrame:
    """Create a synthetic OHLCV DataFrame for testing.

    Generates 252 trading days (~1 year) of data with a gentle uptrend
    and some noise for realistic testing.
    """
    n = 252
    rng = np.random.default_rng(42)
    base = 100.0

    # Generate prices with upward drift + noise
    returns = rng.normal(0.0003, 0.015, n)
    prices = base * np.cumprod(1 + returns)

    dates = [date(2023, 1, 3) + timedelta(days=i) for i in range(n)]
    highs = prices * (1 + rng.uniform(0.001, 0.02, n))
    lows = prices * (1 - rng.uniform(0.001, 0.02, n))
    opens = prices * (1 + rng.uniform(-0.005, 0.005, n))
    volumes = rng.integers(100000, 10000000, n)

    return pl.DataFrame(
        {
            "date": dates,
            "open": opens.tolist(),
            "high": highs.tolist(),
            "low": lows.tolist(),
            "close": prices.tolist(),
            "volume": volumes.tolist(),
        }
    )


@pytest.fixture()
def small_ohlcv_df() -> pl.DataFrame:
    """Create a small 10-row OHLCV DataFrame for deterministic tests."""
    dates = [date(2023, 1, i + 2) for i in range(10)]
    return pl.DataFrame(
        {
            "date": dates,
            "open": [100.0, 101.0, 103.0, 105.0, 104.0, 102.0, 100.0, 98.0, 99.0, 101.0],
            "high": [102.0, 104.0, 106.0, 107.0, 106.0, 104.0, 102.0, 100.0, 101.0, 103.0],
            "low": [99.0, 100.0, 102.0, 104.0, 102.0, 100.0, 98.0, 96.0, 97.0, 99.0],
            "close": [101.0, 103.0, 105.0, 104.0, 102.0, 100.0, 98.0, 99.0, 101.0, 102.0],
            "volume": [1000000] * 10,
        }
    )


@pytest.fixture()
def sma_indicator_configs() -> list[IndicatorConfig]:
    """SMA crossover indicator configuration."""
    return [
        IndicatorConfig(id="sma_fast", type="SMA", params={"length": 5}, source="close"),
        IndicatorConfig(id="sma_slow", type="SMA", params={"length": 20}, source="close"),
    ]


@pytest.fixture()
def rsi_indicator_config() -> IndicatorConfig:
    """RSI indicator configuration."""
    return IndicatorConfig(id="rsi", type="RSI", params={"length": 14}, source="close")


@pytest.fixture()
def sma_crossover_rules() -> tuple[RuleSet, RuleSet]:
    """SMA crossover entry/exit rules."""
    entry = RuleSet(
        logic="AND",
        conditions=[
            Condition(
                left=Operand(indicator="sma_fast"),
                operator="crosses_above",
                right=Operand(indicator="sma_slow"),
            ),
        ],
    )
    exit_rules = RuleSet(
        logic="OR",
        conditions=[
            Condition(
                left=Operand(indicator="sma_fast"),
                operator="crosses_below",
                right=Operand(indicator="sma_slow"),
            ),
        ],
    )
    return entry, exit_rules


@pytest.fixture()
def default_position_sizing() -> PositionSizing:
    """Create default position sizing config."""
    return PositionSizing(method="equal_weight", max_position_pct=20.0, max_positions=5)


@pytest.fixture()
def default_risk_management() -> RiskManagement:
    """Create default risk management config."""
    return RiskManagement(stop_loss_pct=5.0, take_profit_pct=15.0)


@pytest.fixture()
def sample_strategy_dict() -> dict[str, Any]:
    """Return a complete strategy definition as a dict."""
    return {
        "name": "Test SMA Crossover",
        "universe": {"symbols": ["AAPL", "MSFT"], "benchmark": "SPY"},
        "data_config": {
            "start_date": "2020-01-01",
            "end_date": "2024-12-31",
            "train_end_date": "2022-12-31",
        },
        "indicators": [
            {"id": "sma_fast", "type": "SMA", "params": {"length": 50}, "source": "close"},
            {"id": "sma_slow", "type": "SMA", "params": {"length": 200}, "source": "close"},
        ],
        "entry_rules": {
            "logic": "AND",
            "conditions": [
                {
                    "left": {"indicator": "sma_fast"},
                    "operator": "crosses_above",
                    "right": {"indicator": "sma_slow"},
                },
            ],
        },
        "exit_rules": {
            "logic": "OR",
            "conditions": [
                {
                    "left": {"indicator": "sma_fast"},
                    "operator": "crosses_below",
                    "right": {"indicator": "sma_slow"},
                },
            ],
        },
        "position_sizing": {
            "method": "equal_weight",
            "max_position_pct": 20.0,
            "max_positions": 5,
        },
        "risk_management": {
            "stop_loss_pct": 5.0,
            "take_profit_pct": 15.0,
        },
    }


@pytest.fixture()
def sample_trades() -> list[Trade]:
    """Create a list of sample trades for metric testing."""
    return [
        Trade("AAPL", "2023-01-10", 150.0, "2023-02-15", 165.0, 10.0, 36, "signal"),
        Trade("AAPL", "2023-03-01", 160.0, "2023-03-20", 152.0, -5.0, 19, "stop_loss"),
        Trade("AAPL", "2023-04-10", 155.0, "2023-05-15", 170.0, 9.68, 35, "signal"),
        Trade("AAPL", "2023-06-01", 168.0, "2023-06-10", 163.0, -2.98, 9, "stop_loss"),
        Trade("AAPL", "2023-07-01", 165.0, "2023-08-15", 180.0, 9.09, 45, "signal"),
    ]


@pytest.fixture()
def equity_df() -> pl.DataFrame:
    """Create a sample equity curve for metric testing (uptrending)."""
    n = 252
    base = 100_000.0
    # Deterministic uptrend: 0.05% daily + small noise
    rng = np.random.default_rng(42)
    returns = 0.0005 + rng.normal(0.0, 0.005, n)
    equity = base * np.cumprod(1 + returns)

    dates = [date(2023, 1, 3) + timedelta(days=i) for i in range(n)]
    return pl.DataFrame(
        {
            "date": dates,
            "equity": equity.tolist(),
        }
    )


@pytest.fixture()
def sample_strategy() -> StrategyDefinition:
    """Create a valid StrategyDefinition."""
    return StrategyDefinition(
        name="Test Strategy",
        universe=Universe(symbols=["AAPL"], benchmark="SPY"),
        data_config=DataConfig(
            start_date="2020-01-01",
            end_date="2024-12-31",
            train_end_date="2022-12-31",
        ),
        indicators=[
            IndicatorConfig(id="sma_50", type="SMA", params={"length": 50}),
        ],
        entry_rules=RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator="sma_50"),
                    operator="greater_than",
                    right=Operand(constant=100.0),
                ),
            ],
        ),
        exit_rules=RuleSet(
            logic="OR",
            conditions=[
                Condition(
                    left=Operand(indicator="sma_50"),
                    operator="less_than",
                    right=Operand(constant=100.0),
                ),
            ],
        ),
    )
