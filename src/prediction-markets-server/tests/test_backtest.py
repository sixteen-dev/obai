"""Unit tests for backtest calculation helpers and data quality handling.

Covers: _parse_window_seconds, _coerce_timestamp, _find_entry_index,
_find_forward_point, _summarize_window_moves, and edge cases in
backtest_prediction_setup.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.config import load_settings
from src.tools.backtest import (
    _coerce_timestamp,
    _find_entry_index,
    _find_forward_point,
    _parse_window_seconds,
    _summarize_window_moves,
    backtest_prediction_setup,
)


@pytest.fixture(autouse=True)
def _load_settings():
    load_settings()


# ── _parse_window_seconds ──────────────────────────────────────────


class TestParseWindowSeconds:
    def test_minutes(self):
        assert _parse_window_seconds("30m") == 30 * 60

    def test_hours(self):
        assert _parse_window_seconds("6h") == 6 * 3600

    def test_days(self):
        assert _parse_window_seconds("3d") == 3 * 86400

    def test_one_day(self):
        assert _parse_window_seconds("1d") == 86400

    def test_invalid_unit(self):
        assert _parse_window_seconds("5w") is None

    def test_non_numeric_value(self):
        assert _parse_window_seconds("abc") is None

    def test_empty_string(self):
        assert _parse_window_seconds("") is None

    def test_unit_only(self):
        assert _parse_window_seconds("h") is None

    def test_to_resolution_not_handled_here(self):
        """to_resolution is handled by _find_forward_point, not this function."""
        assert _parse_window_seconds("to_resolution") is None


# ── _coerce_timestamp ──────────────────────────────────────────────


class TestCoerceTimestamp:
    def test_int(self):
        assert _coerce_timestamp(1700000000) == 1700000000

    def test_float(self):
        assert _coerce_timestamp(1700000000.5) == 1700000000

    def test_string_digits(self):
        assert _coerce_timestamp("1700000000") == 1700000000

    def test_non_digit_string(self):
        assert _coerce_timestamp("2026-01-01") is None

    def test_none(self):
        assert _coerce_timestamp(None) is None

    def test_dict(self):
        assert _coerce_timestamp({"t": 123}) is None

    def test_empty_string(self):
        assert _coerce_timestamp("") is None


# ── _find_entry_index ──────────────────────────────────────────────


class TestFindEntryIndex:
    def test_finds_first_match_in_band(self):
        history = [
            {"timestamp": 100, "price": 0.20},
            {"timestamp": 200, "price": 0.45},
            {"timestamp": 300, "price": 0.48},
        ]
        assert _find_entry_index(history, 0.40, 0.50) == 1

    def test_exact_boundary_min(self):
        history = [{"timestamp": 100, "price": 0.40}]
        assert _find_entry_index(history, 0.40, 0.50) == 0

    def test_exact_boundary_max(self):
        history = [{"timestamp": 100, "price": 0.50}]
        assert _find_entry_index(history, 0.40, 0.50) == 0

    def test_no_match(self):
        history = [
            {"timestamp": 100, "price": 0.10},
            {"timestamp": 200, "price": 0.90},
        ]
        assert _find_entry_index(history, 0.40, 0.50) is None

    def test_empty_history(self):
        assert _find_entry_index([], 0.40, 0.50) is None

    def test_skips_missing_timestamp(self):
        history = [
            {"price": 0.45},  # no timestamp
            {"timestamp": 200, "price": 0.45},
        ]
        assert _find_entry_index(history, 0.40, 0.50) == 1

    def test_skips_missing_price(self):
        history = [
            {"timestamp": 100},  # no price
            {"timestamp": 200, "price": 0.45},
        ]
        assert _find_entry_index(history, 0.40, 0.50) == 1

    def test_skips_non_numeric_price(self):
        history = [
            {"timestamp": 100, "price": "bad"},
            {"timestamp": 200, "price": 0.45},
        ]
        assert _find_entry_index(history, 0.40, 0.50) == 1

    def test_full_band_zero_to_one(self):
        history = [{"timestamp": 100, "price": 0.55}]
        assert _find_entry_index(history, 0.0, 1.0) == 0


# ── _find_forward_point ────────────────────────────────────────────


class TestFindForwardPoint:
    @pytest.fixture
    def history(self):
        return [
            {"timestamp": 1000, "price": 0.20},
            {"timestamp": 2000, "price": 0.45},  # entry
            {"timestamp": 88400, "price": 0.60},  # ~1d later
            {"timestamp": 200000, "price": 0.80},
            {"timestamp": 300000, "price": 1.00},  # last
        ]

    def test_finds_1d_forward(self, history):
        result = _find_forward_point(history, entry_index=1, window="1d")
        assert result is not None
        assert result["price"] == 0.60

    def test_includes_actual_offset(self, history):
        result = _find_forward_point(history, entry_index=1, window="1d")
        assert result is not None
        assert result["_actual_offset"] == 88400 - 2000

    def test_to_resolution_returns_last(self, history):
        result = _find_forward_point(history, entry_index=1, window="to_resolution")
        assert result is not None
        assert result["price"] == 1.00

    def test_to_resolution_at_last_index(self, history):
        """Entry at last index — no forward points available."""
        result = _find_forward_point(history, entry_index=4, window="to_resolution")
        assert result is None

    def test_no_point_far_enough(self):
        history = [
            {"timestamp": 1000, "price": 0.45},
            {"timestamp": 1500, "price": 0.50},
        ]
        result = _find_forward_point(history, entry_index=0, window="1d")
        assert result is None

    def test_invalid_window_string(self, history):
        result = _find_forward_point(history, entry_index=1, window="bogus")
        assert result is None

    def test_missing_entry_timestamp(self):
        history = [
            {"price": 0.45},  # no timestamp
            {"timestamp": 200000, "price": 0.80},
        ]
        result = _find_forward_point(history, entry_index=0, window="1d")
        assert result is None

    def test_sparse_data_drift_detected(self):
        """If data gap is >2x the requested window, drift should be flagged."""
        history = [
            {"timestamp": 1000, "price": 0.45},
            # Next point is 5 days later — way past the 1d window
            {"timestamp": 1000 + 5 * 86400, "price": 0.80},
        ]
        result = _find_forward_point(history, entry_index=0, window="1d")
        assert result is not None
        assert result["_actual_offset"] == 5 * 86400


# ── _summarize_window_moves ────────────────────────────────────────


class TestSummarizeWindowMoves:
    def test_empty_list(self):
        result = _summarize_window_moves([])
        assert result["sample_size"] == 0
        assert result["avg_price_change"] is None
        assert result["positive_rate"] is None

    def test_single_positive_move(self):
        result = _summarize_window_moves([0.15])
        assert result["sample_size"] == 1
        assert result["avg_price_change"] == 0.15
        assert result["median_price_change"] == 0.15
        assert result["positive_rate"] == 1.0

    def test_single_negative_move(self):
        result = _summarize_window_moves([-0.10])
        assert result["positive_rate"] == 0.0

    def test_mixed_moves(self):
        result = _summarize_window_moves([0.20, -0.10, 0.05])
        assert result["sample_size"] == 3
        assert result["avg_price_change"] == 0.05
        assert result["positive_rate"] == round(2 / 3, 4)

    def test_all_zero_moves(self):
        result = _summarize_window_moves([0.0, 0.0])
        assert result["sample_size"] == 2
        assert result["avg_price_change"] == 0.0
        assert result["positive_rate"] == 0.0  # 0 is not > 0

    def test_median_calculation(self):
        result = _summarize_window_moves([0.10, 0.50, 0.20])
        assert result["median_price_change"] == 0.20

    def test_rounding(self):
        result = _summarize_window_moves([0.1, 0.2, 0.3])
        assert result["avg_price_change"] == 0.2
        # Verify no floating-point artifacts beyond 4 decimal places
        assert len(str(result["avg_price_change"]).split(".")[-1]) <= 4


# ── backtest_prediction_setup integration ──────────────────────────


def _mock_gamma_market(**overrides: object):
    base = {
        "condition_id": "0xabc",
        "question": "Will X happen?",
        "slug": "will-x-happen",
        "outcomes": ["Yes", "No"],
        "outcome_prices": [0.5, 0.5],
        "volume": 50000,
        "liquidity": 10000,
        "category": "crypto",
        "clob_token_ids": ["token_yes"],
        "active": False,
        "closed": True,
    }
    return {**base, **overrides}


class TestBacktestDataQuality:
    """Test backtest behavior with missing, malformed, or edge-case data."""

    @pytest.mark.asyncio
    async def test_no_markets_found(self):
        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup("test", category="crypto")

        assert result["sample_size"] == 0
        assert result["markets_scanned"] == 0

    @pytest.mark.asyncio
    async def test_market_below_volume_threshold(self):
        market = _mock_gamma_market(volume=50, liquidity=10000)

        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[market])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup("test", min_volume=1000, min_liquidity=500)

        assert result["markets_scanned"] == 1
        assert result["markets_after_filters"] == 0
        assert result["sample_size"] == 0

    @pytest.mark.asyncio
    async def test_market_with_no_clob_token_ids(self):
        market = _mock_gamma_market(clob_token_ids=[])

        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[market])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup("test", min_volume=0, min_liquidity=0)

        assert result["sample_size"] == 0

    @pytest.mark.asyncio
    async def test_market_with_insufficient_history(self):
        """Only 1 data point — not enough for entry + forward."""
        market = _mock_gamma_market()
        history = {"history": [{"timestamp": 1000, "price": 0.45}]}

        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[market])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_price_history = AsyncMock(return_value=history)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup(
                "test",
                min_volume=0,
                min_liquidity=0,
                price_threshold_min=0.4,
                price_threshold_max=0.5,
            )

        assert result["markets_with_price_history"] == 0
        assert result["sample_size"] == 0

    @pytest.mark.asyncio
    async def test_no_price_in_entry_band(self):
        """History has data but no point falls in the entry band."""
        market = _mock_gamma_market()
        history = {
            "history": [
                {"timestamp": 1000, "price": 0.10},
                {"timestamp": 2000, "price": 0.90},
            ]
        }

        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[market])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_price_history = AsyncMock(return_value=history)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup(
                "test",
                min_volume=0,
                min_liquidity=0,
                price_threshold_min=0.40,
                price_threshold_max=0.50,
            )

        assert result["markets_with_price_history"] == 1
        assert result["sample_size"] == 0

    @pytest.mark.asyncio
    async def test_resolution_detection_yes(self):
        """Final price > 0.95 → resolved YES."""
        market = _mock_gamma_market()
        history = {
            "history": [
                {"timestamp": 1000, "price": 0.45},
                {"timestamp": 100000, "price": 0.99},
            ]
        }

        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[market])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_price_history = AsyncMock(return_value=history)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup(
                "test",
                min_volume=0,
                min_liquidity=0,
                price_threshold_min=0.4,
                price_threshold_max=0.5,
                forward_windows=["to_resolution"],
            )

        assert result["resolved_yes"] == 1
        assert result["resolved_no"] == 0
        assert result["yes_resolution_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_resolution_detection_no(self):
        """Final price < 0.05 → resolved NO."""
        market = _mock_gamma_market()
        history = {
            "history": [
                {"timestamp": 1000, "price": 0.45},
                {"timestamp": 100000, "price": 0.01},
            ]
        }

        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[market])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_price_history = AsyncMock(return_value=history)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup(
                "test",
                min_volume=0,
                min_liquidity=0,
                price_threshold_min=0.4,
                price_threshold_max=0.5,
                forward_windows=["to_resolution"],
            )

        assert result["resolved_yes"] == 0
        assert result["resolved_no"] == 1
        assert result["yes_resolution_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_sparse_data_flags_drift(self):
        """When forward point is >2x the requested window, flag it."""
        market = _mock_gamma_market()
        # Only 3 points, huge gap between 2nd and 3rd
        history = {
            "history": [
                {"timestamp": 1000, "price": 0.45},
                # 5 days later — "1d" window should flag drift
                {"timestamp": 1000 + 5 * 86400, "price": 0.70},
                {"timestamp": 1000 + 10 * 86400, "price": 0.99},
            ]
        }

        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[market])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_price_history = AsyncMock(return_value=history)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup(
                "test",
                min_volume=0,
                min_liquidity=0,
                price_threshold_min=0.4,
                price_threshold_max=0.5,
                forward_windows=["1d"],
            )

        assert result["sample_size"] == 1
        example = result["examples"][0]
        assert example["data_points"] == 3
        fwd = example["forward_results"]["1d"]
        assert fwd["data_drift"] is True
        assert fwd["actual_elapsed_hours"] == round(5 * 24, 1)
        # Drifted sample excluded from aggregates
        assert result["window_stats"]["1d"]["sample_size"] == 0
        assert result["window_stats"]["1d"]["avg_price_change"] is None
        assert result["window_stats"]["1d"]["data_drift_count"] == 1

    @pytest.mark.asyncio
    async def test_dense_data_no_drift(self):
        """When forward point is close to the requested window, no drift flag."""
        market = _mock_gamma_market()
        history = {
            "history": [
                {"timestamp": 1000, "price": 0.45},
                {"timestamp": 1000 + 86400, "price": 0.55},  # exactly 1d
                {"timestamp": 1000 + 2 * 86400, "price": 0.99},
            ]
        }

        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[market])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_price_history = AsyncMock(return_value=history)
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup(
                "test",
                min_volume=0,
                min_liquidity=0,
                price_threshold_min=0.4,
                price_threshold_max=0.5,
                forward_windows=["1d"],
            )

        assert result["sample_size"] == 1
        fwd = result["examples"][0]["forward_results"]["1d"]
        assert "data_drift" not in fwd

    @pytest.mark.asyncio
    async def test_mixed_clean_and_drifted_samples_exclude_drift_from_aggregates(self):
        """Aggregate stats should use only clean matches while still counting drift."""
        clean_market = _mock_gamma_market(condition_id="0xclean", slug="clean-market")
        drift_market = _mock_gamma_market(condition_id="0xdrift", slug="drift-market")
        clean_history = {
            "history": [
                {"timestamp": 1000, "price": 0.45},
                {"timestamp": 1000 + 86400, "price": 0.55},
                {"timestamp": 1000 + 2 * 86400, "price": 0.99},
            ]
        }
        drift_history = {
            "history": [
                {"timestamp": 2000, "price": 0.45},
                {"timestamp": 2000 + 5 * 86400, "price": 0.70},
                {"timestamp": 2000 + 10 * 86400, "price": 0.99},
            ]
        }

        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[clean_market, drift_market])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.get_price_history = AsyncMock(side_effect=[clean_history, drift_history])
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup(
                "test",
                min_volume=0,
                min_liquidity=0,
                price_threshold_min=0.4,
                price_threshold_max=0.5,
                forward_windows=["1d"],
            )

        assert result["sample_size"] == 2
        stats = result["window_stats"]["1d"]
        assert stats["sample_size"] == 1
        assert stats["avg_price_change"] == 0.10
        assert stats["median_price_change"] == 0.10
        assert stats["positive_rate"] == 1.0
        assert stats["data_drift_count"] == 1

        clean_example, drift_example = result["examples"]
        assert "data_drift" not in clean_example["forward_results"]["1d"]
        assert drift_example["forward_results"]["1d"]["data_drift"] is True

    @pytest.mark.asyncio
    async def test_limitations_always_present(self):
        with (
            patch("src.tools.backtest.GammaClient") as MockGamma,
            patch("src.tools.backtest.ClobClient") as MockClob,
        ):
            gamma = AsyncMock()
            gamma.search_markets = AsyncMock(return_value=[])
            gamma.close = AsyncMock()
            MockGamma.return_value = gamma

            clob = AsyncMock()
            clob.close = AsyncMock()
            MockClob.return_value = clob

            result = await backtest_prediction_setup("test")

        assert len(result["limitations"]) == 5
        assert "descriptive" in result["limitations"][-1].lower()
