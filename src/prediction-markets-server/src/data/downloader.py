"""On-demand history backfill orchestrator.

Coordinates GammaClient + ClobClient + DataClient + PredictionStore so that
historical analysis tools never call the upstream APIs directly. Each method
records its own cache_action so the caller can roll the per-entity decisions
up into the §15 response contract.

Resource ownership:
    The downloader does NOT own the HTTP clients or the store. It receives
    them constructed and is closed-out by its caller. This keeps the
    backfill orchestrator unit-testable with fake clients and lets the
    server own the global lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..clients.clob_client import ClobClient
from ..clients.data_client import DataClient
from ..clients.gamma_client import GammaClient
from ..logging_config import get_logger
from ..storage import (
    MarketRow,
    MetaRow,
    PredictionStore,
    TokenRow,
)
from .coverage import CacheAction, CacheDecision, classify_cache_action
from .normalizers import (
    clob_history_to_price_rows,
    data_api_trades_to_rows,
    gamma_market_to_rows,
)

logger = get_logger(__name__)

_CLOB_SOURCE = "clob_prices_history"
_DATA_TRADES_SOURCE = "data_api_trades"


@dataclass(frozen=True)
class MarketEnsureResult:
    """Outcome of ensure_market for one identifier."""

    market: MarketRow
    tokens: list[TokenRow]
    cache_action: CacheAction
    reason: str


@dataclass(frozen=True)
class PriceHistoryResult:
    """Outcome of ensure_price_history for one (token, fidelity, source)."""

    token_id: str
    condition_id: str
    fidelity_minutes: int
    source: str
    cache_action: CacheAction
    reason: str
    rows_written: int
    coverage_start: datetime | None
    coverage_end: datetime | None


@dataclass(frozen=True)
class TradesEnsureResult:
    """Outcome of ensure_trades for one condition_id."""

    condition_id: str
    cache_action: CacheAction
    rows_written: int


@dataclass
class HistoryDownloader:
    """Backfill orchestrator (see module docstring for ownership rules)."""

    gamma: GammaClient
    clob: ClobClient
    data_client: DataClient
    store: PredictionStore
    data_freshness_hours: int = 24

    async def ensure_market(self, identifier: str, *, now: datetime) -> MarketEnsureResult:
        """Backfill a single market's metadata + token mapping.

        Always refetches from Gamma so resolution fingerprint changes (e.g.
        UMA dispute re-resolution per §8.1) are picked up. The cache_action
        is "fetched" on first ever write, "refreshed" on subsequent writes.

        Args:
            identifier: slug, condition_id, or numeric ID.
            now: Current timestamp (passed explicitly for testability).

        Returns:
            MarketEnsureResult.

        """
        existed = self.store.get_market(identifier) is not None or _looks_like_condition_id(
            identifier
        )
        raw = await self.gamma.get_market(identifier)
        market, tokens = gamma_market_to_rows(raw, fetched_at=now)
        await self.store.upsert_market(market)
        await self.store.upsert_tokens(tokens)
        await self._record_market_meta(market, now)

        action: CacheAction = "refreshed" if existed else "fetched"
        return MarketEnsureResult(
            market=market,
            tokens=tokens,
            cache_action=action,
            reason="market metadata refreshed from gamma",
        )

    async def ensure_price_history(
        self,
        *,
        token_id: str,
        condition_id: str,
        fidelity_minutes: int,
        interval: str,
        now: datetime,
        max_history_points: int,
        requested_start: datetime | None = None,
        requested_end: datetime | None = None,
    ) -> PriceHistoryResult:
        """Backfill price history for one (token, fidelity) under the cap.

        Args:
            token_id: CLOB asset/token ID.
            condition_id: Parent market condition_id (for FK + meta).
            fidelity_minutes: Requested sampling resolution.
            interval: CLOB interval string (e.g. "max").
            now: Current timestamp.
            max_history_points: Hard cap (settings.prediction_max_history_points).
            requested_start: Optional lower bound for coverage check.
            requested_end: Optional upper bound for coverage check.

        Returns:
            PriceHistoryResult.

        Raises:
            ValueError: If the requested fetch would exceed max_history_points.

        """
        coverage = self.store.get_price_history_coverage(
            token_id=token_id,
            fidelity_minutes=fidelity_minutes,
            source=_CLOB_SOURCE,
        )
        decision = classify_cache_action(
            coverage=coverage,
            requested_fidelity=fidelity_minutes,
            requested_start=requested_start,
            requested_end=requested_end,
            freshness_hours=self.data_freshness_hours,
            now=now,
        )
        if decision.action == "cached":
            return self._cached_result(token_id, condition_id, fidelity_minutes, coverage, decision)

        rows_written = await self._fetch_and_store_price_history(
            token_id=token_id,
            condition_id=condition_id,
            fidelity_minutes=fidelity_minutes,
            interval=interval,
            now=now,
            max_history_points=max_history_points,
        )
        refreshed_coverage = (
            self.store.get_price_history_coverage(
                token_id=token_id,
                fidelity_minutes=fidelity_minutes,
                source=_CLOB_SOURCE,
            )
            or {}
        )
        return PriceHistoryResult(
            token_id=token_id,
            condition_id=condition_id,
            fidelity_minutes=fidelity_minutes,
            source=_CLOB_SOURCE,
            cache_action=decision.action,
            reason=decision.reason,
            rows_written=rows_written,
            coverage_start=refreshed_coverage.get("first_timestamp"),
            coverage_end=refreshed_coverage.get("last_timestamp"),
        )

    async def ensure_trades(
        self,
        *,
        condition_id: str,
        limit: int,
        now: datetime,
    ) -> TradesEnsureResult:
        """Backfill recent trades for one market via the Data API.

        Limit is bounded by the upstream DataClient (max 100 per request),
        so this is a single-shot fetch, not a paginated backfill.

        Args:
            condition_id: Market condition ID.
            limit: Max trades to fetch (1-100).
            now: Current timestamp.

        Returns:
            TradesEnsureResult with rows_written = rows actually persisted.

        """
        payload = await self.data_client.get_trades(condition_id, limit=limit)
        trades = payload.get("trades", []) if isinstance(payload, dict) else []
        if not isinstance(trades, list):
            trades = []
        rows = data_api_trades_to_rows(
            condition_id=condition_id,
            trades=trades,
            fetched_at=now,
        )
        written = await self.store.upsert_trades(rows)
        await self.store.update_meta(
            MetaRow(
                entity_type="trades",
                entity_id=condition_id,
                source=_DATA_TRADES_SOURCE,
                last_refreshed=now,
                row_count=written,
                fidelity_minutes=0,
            )
        )
        return TradesEnsureResult(
            condition_id=condition_id,
            cache_action="refreshed" if written else "fetched",
            rows_written=written,
        )

    # ---- helpers ----------------------------------------------------------

    async def _fetch_and_store_price_history(
        self,
        *,
        token_id: str,
        condition_id: str,
        fidelity_minutes: int,
        interval: str,
        now: datetime,
        max_history_points: int,
    ) -> int:
        """Hit CLOB, normalize, upsert; raise loudly on the cap."""
        payload = await self.clob.get_price_history(
            token_id,
            interval=interval,
            fidelity=fidelity_minutes,
        )
        history = payload.get("history", []) if isinstance(payload, dict) else []
        if not isinstance(history, list):
            history = []
        if len(history) > max_history_points:
            msg = (
                f"CLOB returned {len(history)} points for token {token_id}, "
                f"exceeding prediction_max_history_points ({max_history_points}). "
                "Narrow the requested interval/fidelity."
            )
            raise ValueError(msg)
        rows = clob_history_to_price_rows(
            token_id=token_id,
            condition_id=condition_id,
            history=history,
            fidelity_minutes=fidelity_minutes,
            source=_CLOB_SOURCE,
            fetched_at=now,
        )
        await self.store.upsert_price_rows(rows)
        if rows:
            first_ts = min(r.timestamp for r in rows)
            last_ts = max(r.timestamp for r in rows)
        else:
            first_ts = None
            last_ts = None
        await self.store.update_meta(
            MetaRow(
                entity_type="price_history",
                entity_id=token_id,
                source=_CLOB_SOURCE,
                last_refreshed=now,
                first_timestamp=first_ts,
                last_timestamp=last_ts,
                row_count=len(rows),
                fidelity_minutes=fidelity_minutes,
            )
        )
        return len(rows)

    async def _record_market_meta(self, market: MarketRow, now: datetime) -> None:
        """Stamp _pm_meta with the market's refresh time."""
        await self.store.update_meta(
            MetaRow(
                entity_type="market",
                entity_id=market.condition_id,
                source="gamma",
                last_refreshed=now,
                row_count=1,
                fidelity_minutes=0,
            )
        )

    def _cached_result(
        self,
        token_id: str,
        condition_id: str,
        fidelity_minutes: int,
        coverage: dict[str, Any] | None,
        decision: CacheDecision,
    ) -> PriceHistoryResult:
        """Bundle a no-op cache hit into the result dataclass."""
        cov = coverage or {}
        first = _ensure_aware(cov.get("first_timestamp"))
        last = _ensure_aware(cov.get("last_timestamp"))
        return PriceHistoryResult(
            token_id=token_id,
            condition_id=condition_id,
            fidelity_minutes=fidelity_minutes,
            source=_CLOB_SOURCE,
            cache_action=decision.action,
            reason=decision.reason,
            rows_written=0,
            coverage_start=first,
            coverage_end=last,
        )


def _looks_like_condition_id(value: str) -> bool:
    """Heuristic: 0x-prefixed hex strings are condition_ids the store may have."""
    return value.startswith("0x")


def _ensure_aware(value: Any) -> datetime | None:
    """Lift naive timestamps to UTC for downstream serialization."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
