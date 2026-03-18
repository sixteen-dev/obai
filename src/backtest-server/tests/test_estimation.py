"""Tests for runtime estimation: _estimate_runtime, _indicator_weight, _compute_poll_delay."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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
from src.server import _compute_poll_delay, _estimate_runtime, _indicator_weight


def _make_strategy(
    symbols: list[str] | None = None,
    start: str = "2023-01-01",
    end: str = "2024-01-01",
    indicators: list[IndicatorConfig] | None = None,
) -> StrategyDefinition:
    """Build a minimal valid strategy for testing."""
    if indicators is None:
        indicators = [
            IndicatorConfig(id="sma", type="SMA", params={"length": 20}),
        ]
    return StrategyDefinition(
        name="Test",
        universe=Universe(symbols=symbols or ["AAPL"]),
        data_config=DataConfig(start_date=start, end_date=end),
        indicators=indicators,
        entry_rules=RuleSet(
            logic="AND",
            conditions=[
                Condition(
                    left=Operand(indicator=indicators[0].id),
                    operator="greater_than",
                    right=Operand(constant=100.0),
                ),
            ],
        ),
        exit_rules=RuleSet(
            logic="OR",
            conditions=[
                Condition(
                    left=Operand(indicator=indicators[0].id),
                    operator="less_than",
                    right=Operand(constant=100.0),
                ),
            ],
        ),
        position_sizing=PositionSizing(),
        risk_management=RiskManagement(),
    )


@pytest.fixture()
def _mock_estimation_deps() -> Any:  # noqa: ANN401
    """Patch server globals needed for estimation."""
    mock_settings = MagicMock()
    mock_settings.estimate_symbol_year_weight = 0.5
    mock_settings.estimate_indicator_weight = 0.1
    mock_settings.estimate_download_penalty = 2.0

    mock_downloader = MagicMock()
    mock_downloader.count_stale.return_value = 0

    with (
        patch("src.server._settings", mock_settings),
        patch("src.server._downloader", mock_downloader),
    ):
        yield {
            "settings": mock_settings,
            "downloader": mock_downloader,
        }


class TestSingleSymbolShortRange:
    """1 symbol, ~1 year, 1 indicator, all cached → low estimate."""

    def test_low_estimate(self, _mock_estimation_deps: Any) -> None:
        """Single symbol short range should estimate under threshold."""
        strategy = _make_strategy(
            symbols=["AAPL"],
            start="2023-01-01",
            end="2024-01-01",
        )
        result = _estimate_runtime(strategy)

        # 1 sym × ~1 year × 0.5 + 1 ind × 1.0 × 0.1 + 0 download = ~0.6
        assert result < 10.0
        assert result > 0.0


class TestManySymbolsLongRange:
    """50 symbols, 15 years → high estimate above threshold."""

    def test_high_estimate(self, _mock_estimation_deps: Any) -> None:
        """Large universe should estimate well above threshold."""
        strategy = _make_strategy(
            symbols=[f"SYM{i}" for i in range(50)],
            start="2009-01-01",
            end="2024-01-01",
        )
        result = _estimate_runtime(strategy)

        # 50 × ~15 × 0.5 = 375 + indicator cost
        assert result > 100.0


class TestMultiOutputIndicatorsIncreaseCost:
    """MACD/BBANDS weighted 1.5× vs SMA at 1.0×."""

    def test_multi_output_weight(self) -> None:
        """Multi-output indicators like MACD should weight 1.5."""
        sma_ind = IndicatorConfig(id="sma", type="SMA", params={"length": 20})
        macd_ind = IndicatorConfig(
            id="macd",
            type="MACD",
            params={"fast_length": 12, "slow_length": 26, "signal_length": 9},
        )
        bbands_ind = IndicatorConfig(
            id="bb",
            type="BBANDS",
            params={"length": 20},
        )

        assert _indicator_weight(sma_ind) == 1.0
        assert _indicator_weight(macd_ind) == 1.5
        assert _indicator_weight(bbands_ind) == 1.5

    def test_multi_output_increases_total(
        self,
        _mock_estimation_deps: Any,
    ) -> None:
        """Strategy with multi-output indicators should estimate higher."""
        sma_only = _make_strategy(
            indicators=[
                IndicatorConfig(id="sma1", type="SMA", params={"length": 20}),
                IndicatorConfig(id="sma2", type="SMA", params={"length": 50}),
            ],
        )
        mixed = _make_strategy(
            indicators=[
                IndicatorConfig(id="sma1", type="SMA", params={"length": 20}),
                IndicatorConfig(
                    id="macd",
                    type="MACD",
                    params={
                        "fast_length": 12,
                        "slow_length": 26,
                        "signal_length": 9,
                    },
                ),
            ],
        )

        est_sma = _estimate_runtime(sma_only)
        est_mixed = _estimate_runtime(mixed)
        assert est_mixed > est_sma


class TestUncachedSymbolsAddDownloadPenalty:
    """Stale data should increase estimate by count × download_penalty."""

    def test_stale_penalty(self, _mock_estimation_deps: Any) -> None:
        """3 stale symbols at 2.0 penalty each adds 6.0."""
        _mock_estimation_deps["downloader"].count_stale.return_value = 3
        strategy = _make_strategy(
            symbols=["A", "B", "C"],
            start="2023-01-01",
            end="2024-01-01",
        )

        result = _estimate_runtime(strategy)
        # 3 × 1 × 0.5 + indicator + 3 × 2.0 = 1.5 + 0.1 + 6.0 = 7.6
        assert result >= 6.0  # Download penalty dominates


class TestAllCachedNoDownloadPenalty:
    """Fresh data → zero download component."""

    def test_no_penalty(self, _mock_estimation_deps: Any) -> None:
        """All cached symbols should have no download penalty."""
        _mock_estimation_deps["downloader"].count_stale.return_value = 0

        strategy = _make_strategy(symbols=["AAPL"])
        with_penalty = _estimate_runtime(strategy)

        _mock_estimation_deps["downloader"].count_stale.return_value = 1
        without_cache = _estimate_runtime(strategy)

        assert without_cache > with_penalty
        assert without_cache - with_penalty == pytest.approx(2.0)


class TestPollDelayClamped:
    """_compute_poll_delay should stay in [5, 30] range."""

    def test_minimum_clamp(self) -> None:
        """Very small estimate should clamp to 5."""
        assert _compute_poll_delay(1.0) == 5

    def test_maximum_clamp(self) -> None:
        """Very large estimate should clamp to 30."""
        assert _compute_poll_delay(100.0) == 30

    def test_mid_range(self) -> None:
        """Mid-range estimate should be 60% of estimated."""
        # 20 * 0.6 = 12
        assert _compute_poll_delay(20.0) == 12

    def test_zero_estimate(self) -> None:
        """Zero estimate should clamp to 5."""
        assert _compute_poll_delay(0.0) == 5
