"""Tests for portfolio risk computation engine."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from src.engine.risk import (
    MIN_DATA_POINTS,
    _align_price_series,
    _compute_max_drawdown,
    _compute_returns,
    _resolve_weights,
    compute_correlation_matrix,
    compute_portfolio_risk,
)
from src.models.position import AssetType, Position, WeightType


def _make_position(
    symbol: str,
    weight: str = "0.5",
    asset_type: AssetType = AssetType.STOCK,
    weight_type: WeightType = WeightType.PERCENTAGE,
    shares: str | None = None,
    dollar_value: str | None = None,
) -> Position:
    """Create a test position."""
    return Position(
        symbol=symbol,
        weight=Decimal(weight),
        asset_type=asset_type,
        original_input=f"{symbol} test",
        weight_type=weight_type,
        shares=Decimal(shares) if shares else None,
        dollar_value=Decimal(dollar_value) if dollar_value else None,
    )


def _make_price_data(
    dates: list[str],
    closes: list[float],
) -> list[dict[str, Any]]:
    """Create mock price data dicts."""
    return [{"date": d, "close": c} for d, c in zip(dates, closes, strict=True)]


def _generate_dates(n: int) -> list[str]:
    """Generate sequential date strings."""
    return [f"2025-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n)]


def _make_fmp_client() -> MagicMock:
    """Create a mock FMP client."""
    client = MagicMock()
    client.get_risk_free_rate = AsyncMock(return_value=Decimal("0.045"))
    client.get_quotes_batch = AsyncMock(return_value={})
    client.get_historical_prices_multi = AsyncMock(return_value={})
    return client


class TestComputeReturns:
    """Tests for _compute_returns."""

    def test_simple_returns(self):
        prices = np.array([100.0, 110.0, 105.0, 115.0])
        returns = _compute_returns(prices)
        expected = np.array([0.1, -0.04545454, 0.0952381])
        np.testing.assert_allclose(returns, expected, rtol=1e-5)

    def test_constant_prices_zero_returns(self):
        prices = np.array([100.0, 100.0, 100.0])
        returns = _compute_returns(prices)
        np.testing.assert_allclose(returns, [0.0, 0.0])


class TestAlignPriceSeries:
    """Tests for _align_price_series."""

    def test_inner_join_alignment(self):
        dates = ["2025-01-01", "2025-01-02", "2025-01-03"]
        price_data = {
            "AAPL": _make_price_data(dates, [150.0, 155.0, 153.0]),
        }
        # Benchmark has extra date that AAPL doesn't
        bench_data = _make_price_data(
            ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04"],
            [400.0, 405.0, 403.0, 410.0],
        )

        aligned_dates, symbol_arrays, bench = _align_price_series(price_data, bench_data)

        assert len(aligned_dates) == 3
        assert "AAPL" in symbol_arrays
        assert len(bench) == 3

    def test_no_common_dates(self):
        price_data = {
            "AAPL": _make_price_data(["2025-01-01"], [150.0]),
        }
        bench_data = _make_price_data(["2025-01-02"], [400.0])

        aligned_dates, symbol_arrays, bench = _align_price_series(price_data, bench_data)
        assert len(aligned_dates) == 0


class TestComputeMaxDrawdown:
    """Tests for max drawdown computation."""

    def test_known_drawdown_pattern(self):
        # Price goes 100 -> 120 -> 90 -> 110 -> 100
        # Max drawdown: 120 -> 90 = -25%
        cumulative = np.array([1.0, 1.2, 0.9, 1.1, 1.0])
        dates = ["d1", "d2", "d3", "d4", "d5"]

        max_dd, current_dd, dd_start, dd_end = _compute_max_drawdown(cumulative, dates)

        assert abs(max_dd - (-0.25)) < 0.01
        assert dd_start == "d2"  # Peak
        assert dd_end == "d3"  # Trough

    def test_no_drawdown(self):
        cumulative = np.array([1.0, 1.1, 1.2, 1.3])
        dates = ["d1", "d2", "d3", "d4"]

        max_dd, current_dd, _, _ = _compute_max_drawdown(cumulative, dates)

        assert max_dd == 0.0
        assert current_dd == 0.0


class TestResolveWeights:
    """Tests for weight resolution."""

    @pytest.mark.asyncio
    async def test_percentage_weights_pass_through(self):
        positions = [
            _make_position("AAPL", "0.6"),
            _make_position("MSFT", "0.4"),
        ]
        client = _make_fmp_client()

        result = await _resolve_weights(positions, client)

        assert len(result) == 2
        assert abs(result[0][1] - 0.6) < 0.01
        assert abs(result[1][1] - 0.4) < 0.01
        # Should NOT have called get_quotes_batch
        client.get_quotes_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_shares_positions_fetch_quotes(self):
        positions = [
            _make_position("AAPL", "0", weight_type=WeightType.SHARES, shares="100"),
            _make_position("MSFT", "0", weight_type=WeightType.SHARES, shares="200"),
        ]
        client = _make_fmp_client()
        client.get_quotes_batch = AsyncMock(
            return_value={
                "AAPL": {"price": 150.0},
                "MSFT": {"price": 100.0},
            }
        )

        result = await _resolve_weights(positions, client)

        # AAPL: 100 * 150 = 15000, MSFT: 200 * 100 = 20000, total = 35000
        assert len(result) == 2
        aapl_weight = dict(result)["AAPL"]
        assert abs(aapl_weight - 15000 / 35000) < 0.01

    @pytest.mark.asyncio
    async def test_mixed_formats_rejected(self) -> None:
        """Mixed percentage + shares raises ValueError."""
        positions = [
            _make_position("AAPL", "0.4", weight_type=WeightType.PERCENTAGE),
            _make_position("MSFT", "0", weight_type=WeightType.SHARES, shares="100"),
        ]
        client = _make_fmp_client()
        with pytest.raises(ValueError, match="Mixed position formats"):
            await _resolve_weights(positions, client)


class TestComputePortfolioRisk:
    """Tests for the main risk computation function."""

    @pytest.mark.asyncio
    async def test_sharpe_calculation_with_known_returns(self):
        """Verify Sharpe ratio with controlled price data that has volatility."""
        dates = _generate_dates(60)
        # Add realistic volatility via seeded random noise
        np.random.seed(123)
        closes_stock = [100.0]
        for _ in range(59):
            closes_stock.append(closes_stock[-1] * (1 + np.random.normal(0.002, 0.015)))
        closes_bench = [400.0 * (1.0005**i) for i in range(60)]

        client = _make_fmp_client()
        client.get_historical_prices_multi = AsyncMock(
            return_value={
                "AAPL": _make_price_data(dates, closes_stock),
                "SPY": _make_price_data(dates, closes_bench),
            }
        )

        positions = [_make_position("AAPL", "1.0")]
        result = await compute_portfolio_risk(
            positions=positions,
            fmp_client=client,
            benchmark="SPY",
            risk_free_rate=0.045,
        )

        # With positive drift and realistic vol, Sharpe should be positive
        assert float(result.sharpe_ratio) > 0
        assert float(result.annualized_volatility) > 0
        assert float(result.daily_volatility) > 0

    @pytest.mark.asyncio
    async def test_beta_approximately_one_when_mirroring_benchmark(self):
        """Portfolio that mirrors benchmark should have beta ~1."""
        dates = _generate_dates(100)
        prices = [100.0 + i * 0.5 + np.sin(i * 0.1) * 5 for i in range(100)]

        client = _make_fmp_client()
        client.get_historical_prices_multi = AsyncMock(
            return_value={
                "AAPL": _make_price_data(dates, prices),
                "SPY": _make_price_data(dates, prices),  # Same prices = same returns
            }
        )

        positions = [_make_position("AAPL", "1.0")]
        result = await compute_portfolio_risk(
            positions=positions,
            fmp_client=client,
            benchmark="SPY",
            risk_free_rate=0.045,
        )

        assert abs(float(result.beta) - 1.0) < 0.05
        assert abs(float(result.r_squared) - 1.0) < 0.05

    @pytest.mark.asyncio
    async def test_max_drawdown_detection(self):
        """Verify drawdown detection with a known pattern."""
        dates = _generate_dates(50)
        # Goes up to 120, crashes to 80, recovers to 110
        prices = (
            [100.0 + i * 2 for i in range(10)]  # rise to ~120
            + [120.0 - i * 4 for i in range(10)]  # fall to ~80
            + [80.0 + i * 1 for i in range(30)]  # gradual recovery
        )

        client = _make_fmp_client()
        client.get_historical_prices_multi = AsyncMock(
            return_value={
                "AAPL": _make_price_data(dates, prices),
                "SPY": _make_price_data(dates, prices),
            }
        )

        positions = [_make_position("AAPL", "1.0")]
        result = await compute_portfolio_risk(
            positions=positions,
            fmp_client=client,
            benchmark="SPY",
            risk_free_rate=0.045,
        )

        # Max drawdown should be significant (price drops from ~120 to ~80)
        assert float(result.max_drawdown) < -0.1

    @pytest.mark.asyncio
    async def test_single_position(self):
        """Risk computation works with a single position."""
        dates = _generate_dates(60)
        closes = [100.0 * (1.001**i) for i in range(60)]

        client = _make_fmp_client()
        client.get_historical_prices_multi = AsyncMock(
            return_value={
                "AAPL": _make_price_data(dates, closes),
                "SPY": _make_price_data(dates, closes),
            }
        )

        positions = [_make_position("AAPL", "1.0")]
        result = await compute_portfolio_risk(
            positions=positions,
            fmp_client=client,
            benchmark="SPY",
            risk_free_rate=0.045,
        )

        assert float(result.annualized_volatility) >= 0
        assert result.data_start != ""
        assert result.data_end != ""

    @pytest.mark.asyncio
    async def test_insufficient_data_adds_warning(self):
        """When data is insufficient, warnings should be added."""
        dates = _generate_dates(10)  # Less than MIN_DATA_POINTS
        closes = [100.0 + i for i in range(10)]

        client = _make_fmp_client()
        client.get_historical_prices_multi = AsyncMock(
            return_value={
                "AAPL": _make_price_data(dates, closes),
                "SPY": _make_price_data(dates, closes),
            }
        )

        positions = [_make_position("AAPL", "1.0")]
        result = await compute_portfolio_risk(
            positions=positions,
            fmp_client=client,
            benchmark="SPY",
            risk_free_rate=0.045,
        )

        assert any(str(MIN_DATA_POINTS) in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_no_data_returns_empty_metrics(self):
        """When no price data available, return zeroed metrics with warnings."""
        client = _make_fmp_client()
        client.get_historical_prices_multi = AsyncMock(return_value={})

        positions = [_make_position("AAPL", "1.0")]
        result = await compute_portfolio_risk(
            positions=positions,
            fmp_client=client,
            benchmark="SPY",
            risk_free_rate=0.045,
        )

        assert float(result.annualized_volatility) == 0
        assert "Insufficient data" in " ".join(result.warnings)

    @pytest.mark.asyncio
    async def test_sortino_and_var_populated(self):
        """Verify optional metrics are populated."""
        dates = _generate_dates(60)
        # Mix of up and down days
        np.random.seed(42)
        closes = [100.0]
        for _ in range(59):
            closes.append(closes[-1] * (1 + np.random.normal(0.001, 0.02)))

        client = _make_fmp_client()
        client.get_historical_prices_multi = AsyncMock(
            return_value={
                "AAPL": _make_price_data(dates, closes),
                "SPY": _make_price_data(dates, closes),
            }
        )

        positions = [_make_position("AAPL", "1.0")]
        result = await compute_portfolio_risk(
            positions=positions,
            fmp_client=client,
            benchmark="SPY",
            risk_free_rate=0.045,
        )

        assert result.sortino_ratio is not None
        assert result.var_95 is not None
        assert result.calmar_ratio is not None

    @pytest.mark.asyncio
    async def test_cash_position_dampens_risk(self):
        """A 70% AAPL + 30% CASH portfolio should have ~70% of 100% AAPL vol.

        Cash contributes zero return and zero volatility. The risk engine must
        NOT drop cash and renormalize, which would overstate risk.
        """
        dates = _generate_dates(60)
        np.random.seed(99)
        closes = [100.0]
        for _ in range(59):
            closes.append(closes[-1] * (1 + np.random.normal(0.001, 0.02)))

        bench_closes = [400.0 * (1.0005**i) for i in range(60)]

        client = _make_fmp_client()
        client.get_historical_prices_multi = AsyncMock(
            return_value={
                "AAPL": _make_price_data(dates, closes),
                "SPY": _make_price_data(dates, bench_closes),
            }
        )

        # 100% AAPL portfolio (baseline)
        full_positions = [_make_position("AAPL", "1.0")]
        full_result = await compute_portfolio_risk(
            positions=full_positions,
            fmp_client=client,
            benchmark="SPY",
            risk_free_rate=0.045,
        )

        # Reset mock for second call
        client.get_historical_prices_multi = AsyncMock(
            return_value={
                "AAPL": _make_price_data(dates, closes),
                "SPY": _make_price_data(dates, bench_closes),
            }
        )

        # 70% AAPL + 30% CASH portfolio
        mixed_positions = [
            _make_position("AAPL", "0.7"),
            _make_position("CASH", "0.3", asset_type=AssetType.CASH),
        ]
        mixed_result = await compute_portfolio_risk(
            positions=mixed_positions,
            fmp_client=client,
            benchmark="SPY",
            risk_free_rate=0.045,
        )

        full_vol = float(full_result.annualized_volatility)
        mixed_vol = float(mixed_result.annualized_volatility)

        # Mixed portfolio vol should be ~70% of full portfolio vol
        ratio = mixed_vol / full_vol if full_vol > 0 else 0
        assert 0.60 < ratio < 0.80, (
            f"Expected vol ratio ~0.70, got {ratio:.4f} "
            f"(mixed={mixed_vol:.6f}, full={full_vol:.6f})"
        )

        # CASH should NOT appear in "No price data" warnings
        for warning in mixed_result.warnings:
            assert "CASH" not in warning, f"CASH appeared in warning: {warning}"


class TestComputeCorrelationMatrix:
    """Tests for correlation matrix computation."""

    @pytest.mark.asyncio
    async def test_perfect_correlation(self):
        """Identical price series should have correlation of 1."""
        dates = _generate_dates(60)
        closes = [100.0 + i * 0.5 for i in range(60)]

        client = _make_fmp_client()
        client.get_historical_prices_multi = AsyncMock(
            return_value={
                "AAPL": _make_price_data(dates, closes),
                "MSFT": _make_price_data(dates, closes),
            }
        )

        positions = [
            _make_position("AAPL", "0.5"),
            _make_position("MSFT", "0.5"),
        ]
        result = await compute_correlation_matrix(positions, client)

        assert len(result["matrix"]) == 2
        assert abs(result["matrix"][0][1] - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_insufficient_symbols(self):
        """Single symbol should return empty matrix with warning."""
        client = _make_fmp_client()
        client.get_historical_prices_multi = AsyncMock(
            return_value={
                "AAPL": _make_price_data(["2025-01-01"], [150.0]),
            }
        )

        positions = [_make_position("AAPL", "1.0")]
        result = await compute_correlation_matrix(positions, client)

        assert result["matrix"] == []
        assert len(result["warnings"]) > 0
