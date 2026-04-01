"""Tests for Corwin-Schultz bid-ask spread estimator."""

from __future__ import annotations

import numpy as np
import pytest

from src.engine.spread import estimate_spread_corwin_schultz


class TestCorwinSchultzBasic:
    """Basic behavior of the Corwin-Schultz estimator."""

    def test_constant_prices_give_zero_spread(self) -> None:
        """When high == low every bar, spread estimate should be 0."""
        n = 40
        prices = np.full(n, 100.0)
        result = estimate_spread_corwin_schultz(prices, prices, window=20)

        # First 20 values should be NaN
        assert np.all(np.isnan(result[:20]))
        # Remaining should be 0 (or very close)
        valid = result[20:]
        assert np.all(valid == pytest.approx(0.0, abs=1e-10))

    def test_first_window_values_are_nan(self) -> None:
        """First ``window`` values should always be NaN."""
        n = 50
        highs = np.random.default_rng(42).uniform(101, 105, n)
        lows = np.random.default_rng(42).uniform(95, 99, n)
        result = estimate_spread_corwin_schultz(highs, lows, window=20)
        assert np.all(np.isnan(result[:20]))

    def test_output_length_matches_input(self) -> None:
        """Output array should have same length as input."""
        n = 60
        highs = np.full(n, 105.0)
        lows = np.full(n, 95.0)
        result = estimate_spread_corwin_schultz(highs, lows, window=20)
        assert len(result) == n

    def test_short_series_returns_all_nan(self) -> None:
        """Series shorter than window+1 should return all NaN."""
        highs = np.array([105.0, 106.0, 104.0])
        lows = np.array([95.0, 94.0, 96.0])
        result = estimate_spread_corwin_schultz(highs, lows, window=20)
        assert np.all(np.isnan(result))


class TestCorwinSchultzProperties:
    """Property-based tests for spread estimation."""

    def test_spread_is_non_negative(self) -> None:
        """Spread estimates should never be negative (clamped at 0)."""
        rng = np.random.default_rng(123)
        n = 100
        highs = 100.0 + rng.uniform(0, 5, n)
        lows = 100.0 - rng.uniform(0, 5, n)
        result = estimate_spread_corwin_schultz(highs, lows, window=20)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= 0.0)

    def test_wider_spread_produces_larger_estimate(self) -> None:
        """Wider high-low range should produce larger spread estimate."""
        n = 60
        rng = np.random.default_rng(99)
        base = 100.0 + rng.normal(0, 0.5, n).cumsum()

        # Narrow range
        narrow_highs = base + 0.5
        narrow_lows = base - 0.5
        narrow = estimate_spread_corwin_schultz(narrow_highs, narrow_lows, window=20)

        # Wide range
        wide_highs = base + 5.0
        wide_lows = base - 5.0
        wide = estimate_spread_corwin_schultz(wide_highs, wide_lows, window=20)

        # Compare means of valid (non-NaN) estimates
        narrow_mean = float(np.nanmean(narrow))
        wide_mean = float(np.nanmean(wide))
        assert wide_mean > narrow_mean

    def test_spread_is_fraction_of_price(self) -> None:
        """Spread estimates should be a small fraction, not percentage or bps."""
        rng = np.random.default_rng(77)
        n = 100
        highs = 100.0 + rng.uniform(0.5, 2.0, n)
        lows = 100.0 - rng.uniform(0.5, 2.0, n)
        result = estimate_spread_corwin_schultz(highs, lows, window=20)
        valid = result[~np.isnan(result)]
        # Should be small fraction (< 0.1 = 10%), not 10+ (bps or pct)
        assert np.all(valid < 0.1)


class TestCorwinSchultzEdgeCases:
    """Edge cases and boundary conditions."""

    def test_window_equals_data_length(self) -> None:
        """When data length == window, only the last value is computed."""
        n = 20
        highs = np.full(n, 105.0)
        lows = np.full(n, 95.0)
        # window=20, n=20 → n < window + 1 → all NaN
        result = estimate_spread_corwin_schultz(highs, lows, window=20)
        assert np.all(np.isnan(result))

    def test_window_plus_one(self) -> None:
        """When data length == window + 1, exactly one valid value."""
        n = 21
        highs = np.full(n, 105.0)
        lows = np.full(n, 95.0)
        result = estimate_spread_corwin_schultz(highs, lows, window=20)
        assert np.sum(~np.isnan(result)) == 1

    def test_empty_arrays(self) -> None:
        """Empty input should return empty output."""
        result = estimate_spread_corwin_schultz(
            np.array([], dtype=np.float64),
            np.array([], dtype=np.float64),
            window=20,
        )
        assert len(result) == 0
