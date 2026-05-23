"""Typed upsert helpers for the prediction-markets DuckDB store.

Wraps PredictionDuckDBManager with dataclass row types and INSERT … ON
CONFLICT DO UPDATE statements. All writes funnel through the manager's
async write lock; reads use the bare connection because DuckDB MVCC makes
them safe to interleave.

This module owns row normalization for two reasons:
1. Keeps the trade_key composition rule (§8.4) in one place.
2. Lets callers stay schema-agnostic — they pass a dataclass, we serialize.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..logging_config import get_logger
from .db import PredictionDuckDBManager

logger = get_logger(__name__)


# -- Row dataclasses ----------------------------------------------------------


@dataclass
class MarketRow:
    """One row to upsert into pm_markets."""

    condition_id: str
    question: str
    last_refreshed: datetime
    slug: str | None = None
    description: str | None = None
    category: str | None = None
    event_slug: str | None = None
    event_title: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    closed_time: datetime | None = None
    active: bool | None = None
    closed: bool | None = None
    accepting_orders: bool | None = None
    volume: float | None = None
    volume_24h: float | None = None
    liquidity: float | None = None
    resolution_source: str | None = None
    uma_resolution_status: str | None = None
    winning_outcome: str | None = None
    resolution_status: str | None = None
    resolution_method: str | None = None
    resolution_confidence: float | None = None


@dataclass
class TokenRow:
    """One row to upsert into pm_tokens."""

    token_id: str
    condition_id: str
    outcome_index: int
    outcome_label: str


@dataclass
class PriceRow:
    """One row to upsert into pm_price_history."""

    token_id: str
    condition_id: str
    timestamp: datetime
    price: float
    fidelity_minutes: int
    source: str
    fetched_at: datetime


@dataclass
class TradeRow:
    """One row to upsert into pm_trades.

    ``trade_key`` is derived from the other fields via
    :func:`build_trade_key`; callers should set it explicitly only when
    they have a precomputed deterministic key.
    """

    source: str
    condition_id: str
    fetched_at: datetime
    trade_key: str | None = None
    source_trade_id: str | None = None
    transaction_hash: str | None = None
    log_index: int | None = None
    asset_id: str | None = None
    timestamp: datetime | None = None
    price: float | None = None
    size: float | None = None
    side: str | None = None
    outcome: str | None = None
    wallet: str | None = None
    # Raw price/size strings preserved before float coercion so the
    # Data API trade_key formula in §8.4 can use the exact API
    # representations rather than re-stringified floats.
    price_string: str | None = None
    size_string: str | None = None


@dataclass
class MetaRow:
    """One row to upsert into _pm_meta."""

    entity_type: str
    entity_id: str
    source: str
    last_refreshed: datetime
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    row_count: int | None = None
    fidelity_minutes: int = 0
    quality_flags: str | None = None


# -- Trade key composition (§8.4) ---------------------------------------------


def build_trade_key(row: TradeRow) -> str:
    """Compose a deterministic trade_key per §8.4.

    Rules:
        - On-chain fill rows (have log_index):
              source:transaction_hash:log_index:asset_id
        - Data API rows without log_index:
              source:transaction_hash:asset_id:condition_id:timestamp
              :side:price_string:size_string:wallet

    If transaction_hash is missing, source_trade_id substitutes. If both
    are missing the row is unkeyable and the caller must skip it (we
    raise here so the failure is loud, not silent).

    Args:
        row: TradeRow with raw fields populated.

    Returns:
        Composed deterministic trade key string.

    Raises:
        ValueError: When the row has neither transaction_hash nor
            source_trade_id (unkeyable per §8.4).

    """
    tx = _normalize_lower(row.transaction_hash) or _normalize_lower(row.source_trade_id)
    if not tx:
        msg = "Trade row has neither transaction_hash nor source_trade_id; cannot key"
        raise ValueError(msg)

    source = _normalize_lower(row.source) or ""
    asset_id = _normalize_lower(row.asset_id) or ""
    condition_id = _normalize_lower(row.condition_id) or ""

    if row.log_index is not None:
        return f"{source}:{tx}:{row.log_index}:{asset_id}"

    # Epoch seconds, not ISO — ISO timestamps contain colons which would collide
    # with the field separator and make the key ambiguous to dedupe.
    ts = str(int(row.timestamp.timestamp())) if row.timestamp is not None else ""
    side = _normalize_lower(row.side) or ""
    price_s = row.price_string or (str(row.price) if row.price is not None else "")
    size_s = row.size_string or (str(row.size) if row.size is not None else "")
    wallet = _normalize_lower(row.wallet) or ""
    return ":".join(
        [source, tx, asset_id, condition_id, ts, side, price_s, size_s, wallet],
    )


# -- Store --------------------------------------------------------------------


@dataclass
class PredictionStore:
    """High-level typed access layer over PredictionDuckDBManager."""

    manager: PredictionDuckDBManager
    _connected: bool = field(default=False, repr=False)

    def ensure_connected(self) -> None:
        """Open the underlying connection and initialize schema if needed."""
        if not self._connected:
            self.manager.connect()
            self._connected = True

    def close(self) -> None:
        """Close the underlying connection."""
        self.manager.close()
        self._connected = False

    # ---- markets ----------------------------------------------------------

    async def upsert_market(self, row: MarketRow) -> None:
        """Insert or update a single pm_markets row."""
        self.ensure_connected()
        await self.manager.execute_write(_UPSERT_MARKET_SQL, _market_params(row))

    # ---- tokens -----------------------------------------------------------

    async def upsert_tokens(self, rows: list[TokenRow]) -> None:
        """Insert or update multiple pm_tokens rows in one transaction."""
        if not rows:
            return
        self.ensure_connected()
        queries = [(_UPSERT_TOKEN_SQL, _token_params(r)) for r in rows]
        await self.manager.execute_write_many(queries)

    # ---- price history ----------------------------------------------------

    async def upsert_price_rows(self, rows: list[PriceRow]) -> int:
        """Insert or update price history rows. Returns count attempted."""
        if not rows:
            return 0
        self.ensure_connected()
        queries = [(_UPSERT_PRICE_SQL, _price_params(r)) for r in rows]
        await self.manager.execute_write_many(queries)
        return len(rows)

    # ---- trades -----------------------------------------------------------

    async def upsert_trades(self, rows: list[TradeRow]) -> int:
        """Insert or update trade rows. Auto-fills trade_key when absent.

        Rows that fail key construction are skipped and logged. The count
        returned is rows actually persisted, not rows received.
        """
        if not rows:
            return 0
        self.ensure_connected()
        prepared: list[tuple[str, list[Any] | None]] = []
        skipped = 0
        for r in rows:
            try:
                key = r.trade_key or build_trade_key(r)
            except ValueError:
                logger.warning(
                    "prediction_unkeyable_trade_skipped",
                    source=r.source,
                    condition_id=r.condition_id,
                )
                skipped += 1
                continue
            prepared.append((_UPSERT_TRADE_SQL, _trade_params(r, key)))
        if not prepared:
            return 0
        await self.manager.execute_write_many(prepared)
        if skipped:
            logger.info("prediction_unkeyable_trades_total", skipped=skipped)
        return len(prepared)

    # ---- meta -------------------------------------------------------------

    async def update_meta(self, row: MetaRow) -> None:
        """Insert or update a coverage metadata row."""
        self.ensure_connected()
        await self.manager.execute_write(_UPSERT_META_SQL, _meta_params(row))

    # ---- reads ------------------------------------------------------------

    def get_market(self, condition_id: str) -> dict[str, Any] | None:
        """Return a pm_markets row as a dict, or None if missing."""
        self.ensure_connected()
        result = self.manager.conn.execute(
            "SELECT * FROM pm_markets WHERE condition_id = ?",
            [condition_id],
        ).fetchone()
        if result is None:
            return None
        columns = [d[0] for d in self.manager.conn.description]
        return dict(zip(columns, result, strict=True))

    def get_price_history_coverage(
        self,
        token_id: str,
        fidelity_minutes: int,
        source: str,
    ) -> dict[str, Any] | None:
        """Return coverage metadata for a (token, fidelity, source) triple."""
        self.ensure_connected()
        result = self.manager.conn.execute(
            """
            SELECT first_timestamp, last_timestamp, row_count, last_refreshed,
                   quality_flags, fidelity_minutes
            FROM _pm_meta
            WHERE entity_type = 'price_history'
              AND entity_id = ?
              AND source = ?
              AND fidelity_minutes = ?
            """,
            [token_id, source, fidelity_minutes],
        ).fetchone()
        if result is None:
            return None
        # DuckDB TIMESTAMP columns surface as naive Python datetimes; promote
        # them to UTC-aware here so callers cannot accidentally mix aware and
        # naive datetimes (which raises TypeError when comparing or sorting).
        return {
            "first_timestamp": _as_utc(result[0]),
            "last_timestamp": _as_utc(result[1]),
            "row_count": result[2],
            "last_refreshed": _as_utc(result[3]),
            "quality_flags": result[4],
            "fidelity_minutes": result[5],
        }

    def db_size_gb(self) -> float:
        """Return the current DB file size in gibibytes."""
        return self.manager.db_size_gb()


# -- Helpers ------------------------------------------------------------------


def _normalize_lower(value: str | None) -> str | None:
    """Lowercase + strip; preserve None so the caller can distinguish absent vs empty."""
    if value is None:
        return None
    cleaned = value.strip().lower()
    return cleaned or None


# -- SQL ----------------------------------------------------------------------

_UPSERT_MARKET_SQL = """
INSERT INTO pm_markets (
    condition_id, slug, question, description, category, event_slug, event_title,
    start_date, end_date, closed_time, active, closed, accepting_orders,
    volume, volume_24h, liquidity, resolution_source, uma_resolution_status,
    winning_outcome, resolution_status, resolution_method, resolution_confidence,
    last_refreshed
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (condition_id) DO UPDATE SET
    slug = excluded.slug,
    question = excluded.question,
    description = excluded.description,
    category = excluded.category,
    event_slug = excluded.event_slug,
    event_title = excluded.event_title,
    start_date = excluded.start_date,
    end_date = excluded.end_date,
    closed_time = excluded.closed_time,
    active = excluded.active,
    closed = excluded.closed,
    accepting_orders = excluded.accepting_orders,
    volume = excluded.volume,
    volume_24h = excluded.volume_24h,
    liquidity = excluded.liquidity,
    resolution_source = excluded.resolution_source,
    uma_resolution_status = excluded.uma_resolution_status,
    winning_outcome = excluded.winning_outcome,
    resolution_status = excluded.resolution_status,
    resolution_method = excluded.resolution_method,
    resolution_confidence = excluded.resolution_confidence,
    last_refreshed = excluded.last_refreshed
"""

_UPSERT_TOKEN_SQL = """
INSERT INTO pm_tokens (token_id, condition_id, outcome_index, outcome_label)
VALUES (?, ?, ?, ?)
ON CONFLICT (token_id) DO UPDATE SET
    condition_id = excluded.condition_id,
    outcome_index = excluded.outcome_index,
    outcome_label = excluded.outcome_label
"""  # noqa: S105 — SQL constant, the "TOKEN" in the name is a table name, not a credential

_UPSERT_PRICE_SQL = """
INSERT INTO pm_price_history (
    token_id, condition_id, timestamp, price, fidelity_minutes, source, fetched_at
) VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (token_id, timestamp, fidelity_minutes, source) DO UPDATE SET
    condition_id = excluded.condition_id,
    price = excluded.price,
    fetched_at = excluded.fetched_at
"""

_UPSERT_TRADE_SQL = """
INSERT INTO pm_trades (
    trade_key, source, source_trade_id, transaction_hash, log_index, asset_id,
    condition_id, timestamp, price, size, side, outcome, wallet, fetched_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (trade_key) DO UPDATE SET
    source = excluded.source,
    source_trade_id = excluded.source_trade_id,
    transaction_hash = excluded.transaction_hash,
    log_index = excluded.log_index,
    asset_id = excluded.asset_id,
    condition_id = excluded.condition_id,
    timestamp = excluded.timestamp,
    price = excluded.price,
    size = excluded.size,
    side = excluded.side,
    outcome = excluded.outcome,
    wallet = excluded.wallet,
    fetched_at = excluded.fetched_at
"""

_UPSERT_META_SQL = """
INSERT INTO _pm_meta (
    entity_type, entity_id, source, first_timestamp, last_timestamp,
    row_count, fidelity_minutes, quality_flags, last_refreshed
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT (entity_type, entity_id, source, fidelity_minutes) DO UPDATE SET
    first_timestamp = excluded.first_timestamp,
    last_timestamp = excluded.last_timestamp,
    row_count = excluded.row_count,
    quality_flags = excluded.quality_flags,
    last_refreshed = excluded.last_refreshed
"""


def _as_utc(value: datetime | None) -> datetime | None:
    """Promote a naive datetime to UTC-aware; pass None through unchanged."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _market_params(r: MarketRow) -> list[Any]:
    """Pack a MarketRow into positional params matching _UPSERT_MARKET_SQL."""
    return [
        r.condition_id,
        r.slug,
        r.question,
        r.description,
        r.category,
        r.event_slug,
        r.event_title,
        r.start_date,
        r.end_date,
        r.closed_time,
        r.active,
        r.closed,
        r.accepting_orders,
        r.volume,
        r.volume_24h,
        r.liquidity,
        r.resolution_source,
        r.uma_resolution_status,
        r.winning_outcome,
        r.resolution_status,
        r.resolution_method,
        r.resolution_confidence,
        r.last_refreshed,
    ]


def _token_params(r: TokenRow) -> list[Any]:
    """Pack a TokenRow into positional params matching _UPSERT_TOKEN_SQL."""
    return [r.token_id, r.condition_id, r.outcome_index, r.outcome_label]


def _price_params(r: PriceRow) -> list[Any]:
    """Pack a PriceRow into positional params matching _UPSERT_PRICE_SQL."""
    return [
        r.token_id,
        r.condition_id,
        r.timestamp,
        r.price,
        r.fidelity_minutes,
        r.source,
        r.fetched_at,
    ]


def _trade_params(r: TradeRow, key: str) -> list[Any]:
    """Pack a TradeRow + derived key into positional params for the trade upsert."""
    return [
        key,
        r.source,
        r.source_trade_id,
        r.transaction_hash,
        r.log_index,
        r.asset_id,
        r.condition_id,
        r.timestamp,
        r.price,
        r.size,
        r.side,
        r.outcome,
        r.wallet,
        r.fetched_at,
    ]


def _meta_params(r: MetaRow) -> list[Any]:
    """Pack a MetaRow into positional params matching _UPSERT_META_SQL."""
    return [
        r.entity_type,
        r.entity_id,
        r.source,
        r.first_timestamp,
        r.last_timestamp,
        r.row_count,
        r.fidelity_minutes,
        r.quality_flags,
        r.last_refreshed,
    ]
