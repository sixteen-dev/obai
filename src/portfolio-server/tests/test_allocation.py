"""Tests for portfolio allocation breakdown computation engine."""

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.engine.allocation import (
    _build_asset_class_breakdown,
    _build_etf_attribution,
    _build_held_weights,
    _build_look_through_weights,
    _compute_concentration,
    compute_allocation_breakdown,
)
from src.models.position import AssetType, Position, WeightType


def _make_position(
    symbol: str,
    weight: str = "0.5",
    asset_type: AssetType = AssetType.STOCK,
) -> Position:
    """Create a test position."""
    return Position(
        symbol=symbol,
        weight=Decimal(weight),
        asset_type=asset_type,
        original_input=f"{symbol} test",
        weight_type=WeightType.PERCENTAGE,
    )


def _make_fmp_client() -> MagicMock:
    """Create a mock FMP client with company profile responses."""
    client = MagicMock()
    client.get_company_profiles_batch = AsyncMock(
        return_value={
            "AAPL": {"sector": "Technology", "industry": "Consumer Electronics"},
            "MSFT": {"sector": "Technology", "industry": "Software"},
            "JPM": {"sector": "Financial Services", "industry": "Banks"},
            "JNJ": {"sector": "Healthcare", "industry": "Drug Manufacturers"},
            "XOM": {"sector": "Energy", "industry": "Oil & Gas"},
        }
    )
    return client


def _make_etf_holdings(
    etf_symbol: str,
    holdings: list[tuple[str, float]],
) -> list[dict[str, Any]]:
    """Create mock ETF holdings list."""
    return [{"symbol": sym, "name": sym, "weight_percent": weight} for sym, weight in holdings]


class TestBuildHeldWeights:
    """Tests for _build_held_weights."""

    def test_returns_held_instrument_weights(self):
        positions = [
            _make_position("AAPL", "0.4"),
            _make_position("SPY", "0.35", AssetType.ETF),
            _make_position("BND", "0.25", AssetType.BOND_ETF),
        ]

        result = _build_held_weights(positions)

        assert result["AAPL"] == Decimal("0.4")
        assert result["SPY"] == Decimal("0.35")
        assert result["BND"] == Decimal("0.25")

    def test_excludes_zero_weight_positions(self):
        positions = [
            _make_position("AAPL", "0.5"),
            _make_position("MSFT", "0"),
        ]

        result = _build_held_weights(positions)

        assert "AAPL" in result
        assert "MSFT" not in result


class TestBuildLookThroughWeights:
    """Tests for _build_look_through_weights."""

    def test_converts_exposure_to_decimal_weights(self):
        exposure = [
            {
                "symbol": "AAPL",
                "total_percent": 40.0,
                "direct_percent": 40.0,
                "via_etf_percent": 0.0,
            },
            {
                "symbol": "MSFT",
                "total_percent": 10.0,
                "direct_percent": 0.0,
                "via_etf_percent": 10.0,
            },
        ]

        result = _build_look_through_weights(exposure)

        assert result["AAPL"] == Decimal("0.4")
        assert result["MSFT"] == Decimal("0.1")


class TestBuildAssetClassBreakdown:
    """Tests for _build_asset_class_breakdown."""

    def test_classifies_asset_types(self):
        positions = [
            _make_position("AAPL", "0.4", AssetType.STOCK),
            _make_position("SPY", "0.3", AssetType.ETF),
            _make_position("BND", "0.2", AssetType.BOND_ETF),
            _make_position("CASH", "0.1", AssetType.CASH),
        ]
        look_through: dict[str, Decimal] = {}

        result = _build_asset_class_breakdown(positions, look_through)

        assert result["Equity"] == Decimal("0.7")  # AAPL + SPY
        assert result["Fixed Income"] == Decimal("0.2")
        assert result["Cash"] == Decimal("0.1")


class TestComputeConcentration:
    """Tests for concentration metric computation."""

    def test_hhi_equal_weights(self):
        """Equal weights across 10 positions: HHI = 10 * (0.1)^2 = 0.1."""
        look_through = {f"STOCK{i}": Decimal("0.1") for i in range(10)}

        result = _compute_concentration(look_through)

        assert abs(result.herfindahl_index - Decimal("0.1")) < Decimal("0.001")
        assert result.effective_positions == 10

    def test_hhi_single_position(self):
        """Single position: HHI = 1.0."""
        look_through = {"AAPL": Decimal("1.0")}

        result = _compute_concentration(look_through)

        assert result.herfindahl_index == Decimal("1.0")
        assert result.effective_positions == 1

    def test_top_5_weight(self):
        look_through = {
            "A": Decimal("0.30"),
            "B": Decimal("0.25"),
            "C": Decimal("0.20"),
            "D": Decimal("0.15"),
            "E": Decimal("0.10"),
        }

        result = _compute_concentration(look_through)

        assert result.top_5_weight == Decimal("1.0")
        assert result.top_10_weight == Decimal("1.0")

    def test_known_hhi(self):
        """Two positions at 60/40: HHI = 0.36 + 0.16 = 0.52."""
        look_through = {
            "AAPL": Decimal("0.6"),
            "MSFT": Decimal("0.4"),
        }

        result = _compute_concentration(look_through)

        expected_hhi = Decimal("0.6") ** 2 + Decimal("0.4") ** 2
        assert result.herfindahl_index == expected_hhi
        # effective_positions = int(1 / 0.52) = 1
        assert result.effective_positions == int(Decimal("1") / expected_hhi)


class TestBuildEtfAttribution:
    """Tests for ETF attribution mapping."""

    def test_maps_stocks_to_contributing_etfs(self):
        positions = [
            _make_position("SPY", "0.3", AssetType.ETF),
            _make_position("QQQ", "0.3", AssetType.ETF),
            _make_position("AAPL", "0.4", AssetType.STOCK),
        ]
        etf_holdings_map: dict[str, list[dict[str, Any]]] = {
            "SPY": _make_etf_holdings("SPY", [("AAPL", 7.0), ("MSFT", 6.5)]),
            "QQQ": _make_etf_holdings("QQQ", [("AAPL", 11.0), ("GOOGL", 5.0)]),
        }

        result = _build_etf_attribution(positions, etf_holdings_map)

        assert "AAPL" in result
        assert set(result["AAPL"]) == {"SPY", "QQQ"}
        assert result["MSFT"] == ["SPY"]
        assert result["GOOGL"] == ["QQQ"]


class TestComputeAllocationBreakdown:
    """Tests for the main allocation breakdown function."""

    @pytest.mark.asyncio
    async def test_look_through_math(self):
        """50% SPY + 50% AAPL should expand SPY holdings."""
        positions = [
            _make_position("SPY", "0.5", AssetType.ETF),
            _make_position("AAPL", "0.5", AssetType.STOCK),
        ]
        etf_holdings_map: dict[str, list[dict[str, Any]]] = {
            "SPY": _make_etf_holdings(
                "SPY",
                [
                    ("AAPL", 7.0),  # 7% of SPY
                    ("MSFT", 6.5),  # 6.5% of SPY
                    ("GOOGL", 4.0),  # 4% of SPY
                ],
            ),
        }
        client = _make_fmp_client()

        result = await compute_allocation_breakdown(positions, etf_holdings_map, client)

        # AAPL: 50% direct + (50% * 7% / 100) = 50% + 3.5% = 53.5%
        aapl_weight = result.by_ticker.get("AAPL", Decimal("0"))
        assert float(aapl_weight) > 0.5  # More than direct 50%

        # MSFT: only via SPY = 50% * 6.5% / 100 = 3.25%
        msft_weight = result.by_ticker.get("MSFT", Decimal("0"))
        assert float(msft_weight) > 0

    @pytest.mark.asyncio
    async def test_by_ticker_held_vs_by_ticker(self):
        """Pre-expansion should differ from post-expansion."""
        positions = [
            _make_position("SPY", "0.5", AssetType.ETF),
            _make_position("AAPL", "0.5", AssetType.STOCK),
        ]
        etf_holdings_map: dict[str, list[dict[str, Any]]] = {
            "SPY": _make_etf_holdings("SPY", [("AAPL", 7.0), ("MSFT", 6.5)]),
        }
        client = _make_fmp_client()

        result = await compute_allocation_breakdown(positions, etf_holdings_map, client)

        # by_ticker_held has the original instruments
        assert "SPY" in result.by_ticker_held
        assert "AAPL" in result.by_ticker_held

        # by_ticker has expanded view
        assert "MSFT" in result.by_ticker
        # SPY shouldn't be in by_ticker (it was expanded)
        # But AAPL should still be there (direct)
        assert "AAPL" in result.by_ticker

    @pytest.mark.asyncio
    async def test_sector_distribution(self):
        """Sectors should be populated from company profiles."""
        positions = [
            _make_position("AAPL", "0.5"),
            _make_position("JPM", "0.5"),
        ]
        etf_holdings_map: dict[str, list[dict[str, Any]]] = {}
        client = _make_fmp_client()

        result = await compute_allocation_breakdown(positions, etf_holdings_map, client)

        assert "Technology" in result.by_sector
        assert "Financial Services" in result.by_sector

    @pytest.mark.asyncio
    async def test_etf_attribution_populated(self):
        """ETF attribution should map stocks to contributing ETFs."""
        positions = [
            _make_position("SPY", "0.5", AssetType.ETF),
            _make_position("AAPL", "0.5"),
        ]
        etf_holdings_map: dict[str, list[dict[str, Any]]] = {
            "SPY": _make_etf_holdings("SPY", [("AAPL", 7.0), ("MSFT", 6.5)]),
        }
        client = _make_fmp_client()

        result = await compute_allocation_breakdown(positions, etf_holdings_map, client)

        assert "AAPL" in result.etf_attribution
        assert "SPY" in result.etf_attribution["AAPL"]

    @pytest.mark.asyncio
    async def test_top_holdings_sorted_descending(self):
        """Top holdings should be sorted by weight descending."""
        positions = [
            _make_position("AAPL", "0.1"),
            _make_position("MSFT", "0.3"),
            _make_position("GOOGL", "0.6"),
        ]
        etf_holdings_map: dict[str, list[dict[str, Any]]] = {}
        client = _make_fmp_client()

        result = await compute_allocation_breakdown(positions, etf_holdings_map, client)

        symbols = [sym for sym, _ in result.top_holdings]
        assert symbols[0] == "GOOGL"
        assert symbols[1] == "MSFT"
        assert symbols[2] == "AAPL"
