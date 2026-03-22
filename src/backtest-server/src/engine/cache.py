"""Disk-based backtest result caching with TTL expiry."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from ..logging_config import get_logger
from ..models.backtest_result import BacktestResult

logger = get_logger(__name__)


class BacktestCache:
    """Cache backtest results as JSON files keyed by strategy hash.

    Cache key = hash of strategy definition + data fingerprint.
    Data fingerprint includes symbol list, date range, and Parquet
    file modification timestamps.
    """

    def __init__(
        self,
        cache_dir: str,
        ttl_hours: float = 24.0,
    ) -> None:
        """Initialize cache.

        Args:
            cache_dir: Directory for cached JSON files.
            ttl_hours: Time-to-live in hours before cache entries expire.

        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_hours * 3600

    def get(self, cache_key: str) -> BacktestResult | None:
        """Retrieve a cached result if it exists and is not expired.

        Args:
            cache_key: Deterministic hash string for the strategy.

        Returns:
            BacktestResult if cache hit, None if miss or expired.

        """
        path = self._key_path(cache_key)
        if not path.exists():
            return None

        if self._is_expired(path):
            path.unlink(missing_ok=True)
            logger.info("cache_expired", key=cache_key)
            return None

        try:
            data = json.loads(path.read_text())
            logger.info("cache_hit", key=cache_key)
            return BacktestResult.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(
                "cache_read_failed",
                key=cache_key,
                error=str(exc),
            )
            path.unlink(missing_ok=True)
            return None

    def put(self, cache_key: str, result: BacktestResult) -> None:
        """Store a backtest result in the cache.

        Args:
            cache_key: Deterministic hash string for the strategy.
            result: BacktestResult to cache.

        """
        path = self._key_path(cache_key)
        data = result.to_dict()
        path.write_text(json.dumps(data, indent=2))
        logger.info("cache_stored", key=cache_key)

    def clear(self, cache_key: str | None = None) -> int:
        """Clear cached results.

        Args:
            cache_key: If provided, clear only that specific entry.
                       If None, clear all cached results.

        Returns:
            Number of cache entries cleared.

        """
        if cache_key is not None:
            path = self._key_path(cache_key)
            if path.exists():
                path.unlink()
                logger.info("cache_cleared", key=cache_key)
                return 1
            return 0

        count = 0
        for path in self.cache_dir.glob("*.json"):
            path.unlink()
            count += 1

        logger.info("cache_cleared_all", count=count)
        return count

    def _key_path(self, cache_key: str) -> Path:
        """Get file path for a cache key."""
        return self.cache_dir / f"{cache_key}.json"

    def _is_expired(self, path: Path) -> bool:
        """Check if a cached file has exceeded its TTL."""
        age = time.time() - path.stat().st_mtime
        return age > self.ttl_seconds


def build_data_fingerprint(
    symbols: list[str],
    start_date: str,
    end_date: str,
    data_mtimes: dict[str, float],
    timeframe: str = "daily",
) -> str:
    """Build a data fingerprint string for cache key generation.

    Design doc: Phase 1.6 — renamed from parquet_mtimes to data_mtimes,
    added timeframe to prevent daily/intraday cache collisions.

    Args:
        symbols: Sorted list of symbols.
        start_date: Start date string.
        end_date: End date string.
        data_mtimes: Dict of symbol → last refreshed timestamp.
        timeframe: Bar timeframe (daily, 1hour, 15min, 5min).

    Returns:
        Deterministic fingerprint string.

    """
    sorted_symbols = sorted(symbols)
    parts: list[str] = [
        f"timeframe={timeframe}",
        f"symbols={','.join(sorted_symbols)}",
        f"start={start_date}",
        f"end={end_date}",
    ]
    for sym in sorted_symbols:
        mtime = data_mtimes.get(sym, 0.0)
        parts.append(f"{sym}={mtime:.0f}")
    return "|".join(parts)


def make_cache_key(
    strategy_key: str,
    data_fingerprint: str,
) -> str:
    """Combine strategy key and data fingerprint into a cache key.

    Uses Python's built-in hash for speed. Collisions are acceptable
    since the worst case is a cache miss (re-computation), not
    incorrect results.

    Args:
        strategy_key: From StrategyDefinition.cache_key().
        data_fingerprint: From build_data_fingerprint().

    Returns:
        Hex string suitable as a filename.

    """
    combined = f"{strategy_key}|{data_fingerprint}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]
