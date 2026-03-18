"""Tests for backtest result caching."""

from __future__ import annotations

import time
from pathlib import Path

from src.engine.cache import BacktestCache, build_data_fingerprint, make_cache_key
from src.models.backtest_result import BacktestResult


def _make_result(name: str = "Test Strategy") -> BacktestResult:
    """Create a minimal BacktestResult for testing."""
    return BacktestResult(
        strategy_name=name,
        symbols=["AAPL"],
        period="2020-01-01 to 2024-12-31",
        total_return_pct=50.0,
        cagr_pct=10.0,
        sharpe_ratio=1.5,
        sortino_ratio=2.0,
        calmar_ratio=1.2,
        max_drawdown_pct=-15.0,
        max_drawdown_start="2022-01-15",
        max_drawdown_end="2022-06-15",
        annualized_volatility_pct=18.0,
        var_95_pct=-2.5,
        downside_deviation_pct=12.0,
        total_trades=100,
        win_rate_pct=55.0,
        profit_factor=1.8,
        avg_trade_return_pct=0.5,
        avg_holding_days=15.0,
        max_consecutive_losses=4,
        benchmark_symbol="SPY",
        benchmark_return_pct=40.0,
        benchmark_cagr_pct=8.5,
        alpha_pct=1.5,
        beta=0.85,
        information_ratio=0.65,
    )


class TestBacktestCache:
    """Test disk-based result caching."""

    def test_put_and_get(self, tmp_path: object) -> None:
        """Stored result should be retrievable."""
        cache = BacktestCache(str(tmp_path))
        result = _make_result()

        cache.put("test_key", result)
        retrieved = cache.get("test_key")

        assert retrieved is not None
        assert retrieved.strategy_name == "Test Strategy"
        assert retrieved.sharpe_ratio == 1.5

    def test_cache_miss(self, tmp_path: object) -> None:
        """Missing key should return None."""
        cache = BacktestCache(str(tmp_path))
        assert cache.get("nonexistent") is None

    def test_ttl_expiry(self, tmp_path: object) -> None:
        """Expired entries should return None."""
        cache = BacktestCache(str(tmp_path), ttl_hours=0.0001)  # ~0.36 seconds
        result = _make_result()

        cache.put("expire_key", result)
        time.sleep(0.5)  # Wait for TTL to expire

        assert cache.get("expire_key") is None

    def test_clear_specific_key(self, tmp_path: object) -> None:
        """Clearing specific key should only remove that entry."""
        cache = BacktestCache(str(tmp_path))
        cache.put("key1", _make_result("Strategy A"))
        cache.put("key2", _make_result("Strategy B"))

        cleared = cache.clear("key1")

        assert cleared == 1
        assert cache.get("key1") is None
        assert cache.get("key2") is not None

    def test_clear_all(self, tmp_path: object) -> None:
        """Clearing without key should remove all entries."""
        cache = BacktestCache(str(tmp_path))
        cache.put("key1", _make_result())
        cache.put("key2", _make_result())

        cleared = cache.clear()

        assert cleared == 2
        assert cache.get("key1") is None
        assert cache.get("key2") is None

    def test_clear_nonexistent_key(self, tmp_path: object) -> None:
        """Clearing nonexistent key should return 0."""
        cache = BacktestCache(str(tmp_path))
        assert cache.clear("nope") == 0

    def test_corrupted_cache_file(self, tmp_path: object) -> None:
        """Corrupted cache file should return None and be cleaned up."""
        cache = BacktestCache(str(tmp_path))
        path = Path(str(tmp_path)) / "corrupt_key.json"
        path.write_text("not valid json {{{")

        result = cache.get("corrupt_key")
        assert result is None
        # Corrupted file should be deleted
        assert not path.exists()

    def test_cache_creates_directory(self, tmp_path: object) -> None:
        """Cache should create its directory if it doesn't exist."""
        cache_dir = Path(str(tmp_path)) / "subdir" / "cache"
        cache = BacktestCache(str(cache_dir))

        cache.put("test", _make_result())
        assert cache_dir.exists()

    def test_roundtrip_preserves_data(self, tmp_path: object) -> None:
        """Full roundtrip should preserve all data fields."""
        cache = BacktestCache(str(tmp_path))
        original = _make_result()
        original.yearly_returns = {"2020": 12.3, "2021": -4.5}
        original.symbol_returns = {"AAPL": 50.0}
        original.warnings = ["Survivorship bias"]

        cache.put("roundtrip", original)
        retrieved = cache.get("roundtrip")

        assert retrieved is not None
        assert retrieved.yearly_returns == {"2020": 12.3, "2021": -4.5}
        assert retrieved.symbol_returns == {"AAPL": 50.0}
        assert retrieved.warnings == ["Survivorship bias"]


class TestBuildDataFingerprint:
    """Test data fingerprint generation."""

    def test_deterministic(self) -> None:
        """Same inputs should produce same fingerprint."""
        fp1 = build_data_fingerprint(
            ["AAPL", "MSFT"], "2020-01-01", "2024-12-31", {"AAPL": 1000.0, "MSFT": 2000.0}
        )
        fp2 = build_data_fingerprint(
            ["AAPL", "MSFT"], "2020-01-01", "2024-12-31", {"AAPL": 1000.0, "MSFT": 2000.0}
        )
        assert fp1 == fp2

    def test_order_independent(self) -> None:
        """Symbol order should not affect fingerprint."""
        fp1 = build_data_fingerprint(
            ["MSFT", "AAPL"], "2020-01-01", "2024-12-31", {"AAPL": 1000.0, "MSFT": 2000.0}
        )
        fp2 = build_data_fingerprint(
            ["AAPL", "MSFT"], "2020-01-01", "2024-12-31", {"AAPL": 1000.0, "MSFT": 2000.0}
        )
        assert fp1 == fp2

    def test_different_mtimes_different_fp(self) -> None:
        """Different file modification times should produce different fingerprint."""
        fp1 = build_data_fingerprint(["AAPL"], "2020-01-01", "2024-12-31", {"AAPL": 1000.0})
        fp2 = build_data_fingerprint(["AAPL"], "2020-01-01", "2024-12-31", {"AAPL": 2000.0})
        assert fp1 != fp2


class TestMakeCacheKey:
    """Test cache key generation."""

    def test_deterministic(self) -> None:
        """Same inputs should produce same cache key."""
        k1 = make_cache_key("strategy_json", "data_fingerprint")
        k2 = make_cache_key("strategy_json", "data_fingerprint")
        assert k1 == k2

    def test_different_inputs_different_keys(self) -> None:
        """Different inputs should produce different keys."""
        k1 = make_cache_key("strategy_a", "fp_1")
        k2 = make_cache_key("strategy_b", "fp_1")
        assert k1 != k2

    def test_key_is_hex_string(self) -> None:
        """Cache key should be a 16-char hex string."""
        key = make_cache_key("test", "test")
        assert len(key) == 16
        # Should be valid hex
        int(key, 16)
