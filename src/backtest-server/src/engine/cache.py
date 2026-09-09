"""Disk-based backtest result caching with TTL expiry."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .. import __version__ as ENGINE_VERSION
from ..logging_config import get_logger
from ..models.backtest_result import BacktestResult
from .indicators import indicator_stack_versions

logger = get_logger(__name__)

# The indicator stack is part of what a cached number means: a wrapper or
# native-library upgrade can move a value under an unchanged strategy, so it
# keys the cache exactly as ENGINE_VERSION does.
INDICATOR_STACK_VERSIONS: dict[str, str] = indicator_stack_versions()


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

    def get_trades(self, cache_key: str) -> list[dict[str, object]] | None:
        """Retrieve cached trade-level details if present and not expired.

        Trades are stored in a sidecar file (``{key}.trades.json``) so the
        main BacktestResult JSON stays small and LLM-friendly. The trade
        log tool reads this sidecar to avoid re-running the whole strategy
        just to repaginate trades.
        """
        path = self._trades_path(cache_key)
        if not path.exists() or self._is_expired(path):
            return None
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, list):
                return None
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("trade_cache_read_failed", key=cache_key, error=str(exc))
            return None

    def put_trades(self, cache_key: str, trades: list[dict[str, object]]) -> None:
        """Write trades sidecar for later trade-log pagination."""
        path = self._trades_path(cache_key)
        path.write_text(json.dumps(trades))
        logger.info("trades_stored", key=cache_key, count=len(trades))

    def _trades_path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.trades.json"

    def get_extras(self, cache_key: str) -> dict[str, object] | None:
        """Retrieve cached train/test + portfolio_metrics blocks if present.

        Kept in a sidecar (``{key}.extras.json``) for the same reason as
        ``get_trades``: the main BacktestResult is the LLM-facing payload
        and shouldn't carry blocks that only the finalization layer adds
        on the miss path.
        """
        path = self._extras_path(cache_key)
        if not path.exists() or self._is_expired(path):
            return None
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                return None
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("extras_cache_read_failed", key=cache_key, error=str(exc))
            return None

    def put_extras(self, cache_key: str, extras: dict[str, object]) -> None:
        """Persist the train/test + portfolio_metrics finalization blocks."""
        path = self._extras_path(cache_key)
        path.write_text(json.dumps(extras, default=str))
        logger.info("extras_stored", key=cache_key, keys=sorted(extras.keys()))

    def _extras_path(self, cache_key: str) -> Path:
        return self.cache_dir / f"{cache_key}.extras.json"

    def clear(self, cache_key: str | None = None) -> int:
        """Clear cached results plus their trades/extras sidecars.

        A cache entry actually spans three files — the main BacktestResult
        JSON, the trades sidecar, and the extras sidecar. Clearing only
        the main file left ``get_trades`` / ``get_extras`` returning stale
        data for the same key. Unlink all three together.

        Args:
            cache_key: If provided, clear only that specific entry.
                       If None, clear all cached results.

        Returns:
            Number of cache entries cleared (counts the main file; the
            sidecars are companions to that entry).

        """
        if cache_key is not None:
            removed_main = False
            for path in (
                self._key_path(cache_key),
                self._trades_path(cache_key),
                self._extras_path(cache_key),
            ):
                if path.exists():
                    path.unlink()
                    if (
                        path.suffix == ".json"
                        and ".trades." not in path.name
                        and ".extras." not in path.name
                    ):
                        removed_main = True
            if removed_main:
                logger.info("cache_cleared", key=cache_key)
                return 1
            return 0

        count = 0
        for path in self.cache_dir.glob("*.json"):
            path.unlink()
            # Only count main entries (not trades/extras sidecars) so the
            # returned tally matches the number of distinct strategies
            # cleared, not the total file count.
            if ".trades." not in path.name and ".extras." not in path.name:
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
    added timeframe to prevent daily/intraday cache collisions. The engine
    version and the indicator stack versions are part of the key so a
    calculation change — in this repo or in the wrapper underneath it —
    invalidates entries instead of serving pre-change numbers until the TTL
    expires.

    Args:
        symbols: Sorted list of symbols.
        start_date: Start date string.
        end_date: End date string.
        data_mtimes: Dict of symbol → last refreshed timestamp. It may name a
            symbol outside the traded universe — the benchmark's bars shape the
            result too — and every one of them keys the fingerprint.
        timeframe: Bar timeframe (daily, 1hour, 15min, 5min).

    Returns:
        Deterministic fingerprint string.

    """
    sorted_symbols = sorted(symbols)
    deps = ";".join(
        f"{name}={version}" for name, version in sorted(INDICATOR_STACK_VERSIONS.items())
    )
    parts: list[str] = [
        f"engine={ENGINE_VERSION}",
        f"deps={deps}",
        f"timeframe={timeframe}",
        f"symbols={','.join(sorted_symbols)}",
        f"start={start_date}",
        f"end={end_date}",
    ]
    for sym in sorted(set(sorted_symbols) | set(data_mtimes)):
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
