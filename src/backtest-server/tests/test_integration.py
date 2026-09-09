"""Integration tests: full strategy → indicators → signals → backtest → metrics pipeline."""

from __future__ import annotations

from typing import Any

import polars as pl

from src.engine.backtester import BacktestConfig, run_backtest, run_multi_symbol_backtest
from src.engine.cache import BacktestCache, make_cache_key
from src.engine.indicators import compute_indicators
from src.engine.metrics import compute_metrics
from src.engine.signals import generate_signals
from src.models.backtest_result import BacktestResult
from src.models.strategy import (
    Condition,
    IndicatorConfig,
    Operand,
    PositionSizing,
    RiskManagement,
    RuleSet,
    StrategyDefinition,
)


class TestFullPipeline:
    """End-to-end: strategy definition through to BacktestResult."""

    def test_sma_crossover_pipeline(
        self,
        sample_ohlcv_df: pl.DataFrame,
        sma_indicator_configs: list[IndicatorConfig],
        sma_crossover_rules: tuple[RuleSet, RuleSet],
        default_position_sizing: PositionSizing,
        default_risk_management: RiskManagement,
    ) -> None:
        """Run SMA crossover from raw OHLCV through to metrics."""
        entry_rules, exit_rules = sma_crossover_rules

        # Step 1: Compute indicators
        df_with_indicators, warnings = compute_indicators(sample_ohlcv_df, sma_indicator_configs)
        assert "sma_fast" in df_with_indicators.columns
        assert "sma_slow" in df_with_indicators.columns

        # Step 2: Generate signals
        df_with_signals = generate_signals(df_with_indicators, entry_rules, exit_rules)
        assert "entry_signal" in df_with_signals.columns
        assert "exit_signal" in df_with_signals.columns

        # Step 3: Run backtest
        equity_df, trades = run_backtest(
            df_with_signals,
            default_position_sizing,
            default_risk_management,
            BacktestConfig(symbol="TEST"),
        )
        assert "date" in equity_df.columns
        assert "equity" in equity_df.columns
        assert len(equity_df) > 0

        # Step 4: Compute metrics
        result = compute_metrics(equity_df, trades, "SMA Crossover Test", ["TEST"])
        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "SMA Crossover Test"
        assert result.total_return_pct != 0.0 or result.total_trades == 0
        assert result.data_points_processed == len(equity_df)

    def test_rsi_strategy_pipeline(
        self,
        sample_ohlcv_df: pl.DataFrame,
        rsi_indicator_config: IndicatorConfig,
    ) -> None:
        """Run RSI overbought/oversold strategy through full pipeline."""
        indicators = [rsi_indicator_config]

        entry_rules = RuleSet(
            logic="AND",
            conditions=[
                _make_condition("rsi", "less_than", constant=30.0),
            ],
        )
        exit_rules = RuleSet(
            logic="OR",
            conditions=[
                _make_condition("rsi", "greater_than", constant=70.0),
            ],
        )

        position_sizing = PositionSizing(
            method="equal_weight", max_position_pct=25.0, max_positions=4
        )
        risk_management = RiskManagement(stop_loss_pct=3.0, take_profit_pct=10.0)

        # Full pipeline
        df_ind, _ = compute_indicators(sample_ohlcv_df, indicators)
        df_sig = generate_signals(df_ind, entry_rules, exit_rules)
        equity_df, trades = run_backtest(
            df_sig,
            position_sizing,
            risk_management,
            BacktestConfig(symbol="TEST"),
        )
        result = compute_metrics(equity_df, trades, "RSI Strategy", ["TEST"])

        assert isinstance(result, BacktestResult)
        assert result.sharpe_ratio is not None
        assert result.max_drawdown_pct <= 0.0
        # All trades should have valid exit reasons
        for trade in trades:
            assert trade.exit_reason in (
                "signal",
                "stop_loss",
                "trailing_stop",
                "take_profit",
                "eod_close",
                "time_stop",
                "end_of_backtest",
            )

    def test_from_dict_to_backtest(
        self,
        sample_strategy_dict: dict[str, Any],
        sample_ohlcv_df: pl.DataFrame,
    ) -> None:
        """Parse strategy dict, then run full backtest pipeline."""
        strategy = StrategyDefinition.from_dict(sample_strategy_dict)

        # Compute indicators from strategy definition
        df_ind, warnings = compute_indicators(sample_ohlcv_df, strategy.indicators)

        # Generate signals from strategy rules
        df_sig = generate_signals(df_ind, strategy.entry_rules, strategy.exit_rules)

        # Run backtest with strategy's position sizing and risk management
        sizing = strategy.position_sizing or PositionSizing(method="equal_weight")
        risk = strategy.risk_management or RiskManagement()
        equity_df, trades = run_backtest(df_sig, sizing, risk, BacktestConfig(symbol="AAPL"))

        result = compute_metrics(equity_df, trades, strategy.name, strategy.universe.symbols)

        assert result.strategy_name == "Test SMA Crossover"
        assert result.symbols == ["AAPL", "MSFT"]
        assert result.data_points_processed > 0

    def test_serialization_roundtrip(
        self,
        sample_ohlcv_df: pl.DataFrame,
        sma_indicator_configs: list[IndicatorConfig],
        sma_crossover_rules: tuple[RuleSet, RuleSet],
        default_position_sizing: PositionSizing,
        default_risk_management: RiskManagement,
    ) -> None:
        """BacktestResult should survive to_dict serialization."""
        entry_rules, exit_rules = sma_crossover_rules
        df_ind, _ = compute_indicators(sample_ohlcv_df, sma_indicator_configs)
        df_sig = generate_signals(df_ind, entry_rules, exit_rules)
        equity_df, trades = run_backtest(
            df_sig,
            default_position_sizing,
            default_risk_management,
            BacktestConfig(symbol="TEST"),
        )
        result = compute_metrics(equity_df, trades, "Roundtrip Test", ["TEST"])

        serialized = result.to_dict()

        assert "performance" in serialized
        assert "risk" in serialized
        assert "trading" in serialized
        assert serialized["strategy_name"] == "Roundtrip Test"
        assert serialized["performance"]["sharpe_ratio"] == result.sharpe_ratio


def _run_sma_pipeline(
    ohlcv_df: pl.DataFrame,
    indicator_configs: list[IndicatorConfig],
    rules: tuple[RuleSet, RuleSet],
    sizing: PositionSizing,
    risk: RiskManagement,
) -> BacktestResult:
    """Run full SMA pipeline and return metrics result."""
    entry_rules, exit_rules = rules
    df_ind, _ = compute_indicators(ohlcv_df, indicator_configs)
    df_sig = generate_signals(df_ind, entry_rules, exit_rules)
    equity_df, trades = run_backtest(df_sig, sizing, risk, BacktestConfig(symbol="TEST"))
    return compute_metrics(equity_df, trades, "Cache Test", ["TEST"])


class TestCacheIntegration:
    """Verify caching works end-to-end with real backtest results."""

    def test_cache_stores_and_retrieves_result(
        self,
        tmp_path: object,
        sample_ohlcv_df: pl.DataFrame,
        sma_indicator_configs: list[IndicatorConfig],
        sma_crossover_rules: tuple[RuleSet, RuleSet],
    ) -> None:
        """Run backtest, cache result, retrieve from cache — should match."""
        sizing = PositionSizing(method="equal_weight", max_position_pct=20.0, max_positions=5)
        risk = RiskManagement(stop_loss_pct=5.0, take_profit_pct=15.0)
        original = _run_sma_pipeline(
            sample_ohlcv_df, sma_indicator_configs, sma_crossover_rules, sizing, risk
        )

        # Store in cache
        cache = BacktestCache(str(tmp_path))
        cache_key = make_cache_key("strategy_hash", "data_fingerprint")
        cache.put(cache_key, original)

        # Retrieve from cache
        cached = cache.get(cache_key)
        assert cached is not None
        assert cached.strategy_name == original.strategy_name
        assert cached.sharpe_ratio == original.sharpe_ratio
        assert cached.total_trades == original.total_trades
        assert cached.total_return_pct == original.total_return_pct

    def test_strategy_cache_key_determinism(
        self,
        sample_strategy_dict: dict[str, Any],
    ) -> None:
        """Same strategy dict should produce identical cache keys."""
        s1 = StrategyDefinition.from_dict(sample_strategy_dict)
        s2 = StrategyDefinition.from_dict(sample_strategy_dict)

        assert s1.cache_key() == s2.cache_key()

        # Different strategy should have different key
        modified = {**sample_strategy_dict, "name": "Different Strategy"}
        s3 = StrategyDefinition.from_dict(modified)
        assert s1.cache_key() != s3.cache_key()


class TestMultiSymbolIntegration:
    """Multi-symbol backtest pipeline integration."""

    def test_multi_symbol_full_pipeline(
        self,
        sample_ohlcv_df: pl.DataFrame,
        sma_indicator_configs: list[IndicatorConfig],
        sma_crossover_rules: tuple[RuleSet, RuleSet],
        default_position_sizing: PositionSizing,
        default_risk_management: RiskManagement,
    ) -> None:
        """Run multi-symbol backtest through full pipeline."""
        entry_rules, exit_rules = sma_crossover_rules

        # Prepare two "symbols" using the same data (offset prices)
        df_aapl_ind, _ = compute_indicators(sample_ohlcv_df, sma_indicator_configs)
        df_aapl_sig = generate_signals(df_aapl_ind, entry_rules, exit_rules)

        # Create MSFT data with different prices
        df_msft = sample_ohlcv_df.with_columns(
            (pl.col("close") * 1.5).alias("close"),
            (pl.col("open") * 1.5).alias("open"),
            (pl.col("high") * 1.5).alias("high"),
            (pl.col("low") * 1.5).alias("low"),
        )
        df_msft_ind, _ = compute_indicators(df_msft, sma_indicator_configs)
        df_msft_sig = generate_signals(df_msft_ind, entry_rules, exit_rules)

        symbol_dfs = {"AAPL": df_aapl_sig, "MSFT": df_msft_sig}
        equity_df, trades = run_multi_symbol_backtest(
            symbol_dfs, default_position_sizing, default_risk_management
        )

        result = compute_metrics(equity_df, trades, "Multi-Symbol Test", ["AAPL", "MSFT"])

        assert result.symbols == ["AAPL", "MSFT"]
        assert result.data_points_processed == len(equity_df)
        # Should have trades from potentially both symbols
        symbols_traded = {t.symbol for t in trades}
        assert symbols_traded.issubset({"AAPL", "MSFT"})


def _make_condition(
    indicator: str,
    operator: str,
    *,
    constant: float | None = None,
    right_indicator: str | None = None,
) -> Any:
    """Create a Condition for test setup."""
    right = Operand(indicator=right_indicator) if right_indicator else Operand(constant=constant)
    return Condition(
        left=Operand(indicator=indicator),
        operator=operator,
        right=right,
    )
