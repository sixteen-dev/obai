"""Canonical Coinbase spot market models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


def utc_now_iso() -> str:
    """Return current UTC time as ISO string."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Candle:
    """Canonical OHLCV candle."""

    product_id: str
    start: datetime
    low: float
    high: float
    open: float
    close: float
    volume: float

    @property
    def start_ts(self) -> int:
        """Unix timestamp in seconds."""
        return int(self.start.timestamp())

    def to_dict(self) -> dict[str, Any]:
        """Serialize candle."""
        return {
            "product_id": self.product_id,
            "start": self.start.isoformat(),
            "start_ts": self.start_ts,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    @classmethod
    def from_coinbase(cls, product_id: str, raw: dict[str, Any]) -> Candle:
        """Build a candle from Coinbase public candle payload."""
        start_ts = int(raw["start"])
        return cls(
            product_id=product_id.upper(),
            start=datetime.fromtimestamp(start_ts, UTC),
            low=float(raw["low"]),
            high=float(raw["high"]),
            open=float(raw["open"]),
            close=float(raw["close"]),
            volume=float(raw["volume"]),
        )


@dataclass
class Coverage:
    """Time-series coverage manifest."""

    start: str
    end: str
    # The end of the window actually assessed. Bars whose period has not
    # elapsed cannot be judged, so a request reaching past the last closed
    # bar is evaluated over less than it asked for. Without this, ``end``
    # echoed the request and a truncated assessment was indistinguishable
    # from a complete one.
    evaluated_end: str
    expected_intervals: int
    returned_intervals: int
    missing_intervals: int
    missing_pct: float
    gap_ranges: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize coverage."""
        return {
            "start": self.start,
            "end": self.end,
            "evaluated_end": self.evaluated_end,
            "expected_intervals": self.expected_intervals,
            "returned_intervals": self.returned_intervals,
            "missing_intervals": self.missing_intervals,
            "missing_pct": self.missing_pct,
            "gap_ranges": self.gap_ranges,
        }


@dataclass
class SourceQuality:
    """Canonical provenance and quality block returned by crypto tools."""

    primary_source: Literal["coinbase"] = "coinbase"
    venue: Literal["coinbase"] = "coinbase"
    provider: Literal["Coinbase Advanced Trade"] = "Coinbase Advanced Trade"
    source_type: Literal["execution_venue", "research_only"] = "execution_venue"
    cost_tier: Literal["free"] = "free"
    product_id: str | None = None
    retrieved_at: str = field(default_factory=utc_now_iso)
    latest_observation_at: str | None = None
    freshness_seconds: float | None = None
    coverage: Coverage | None = None
    limitations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    blocking_quality_warning: bool = False
    execution_grade: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize source-quality block."""
        return {
            "primary_source": self.primary_source,
            "venue": self.venue,
            "provider": self.provider,
            "product_id": self.product_id,
            "source_type": self.source_type,
            "retrieved_at": self.retrieved_at,
            "latest_observation_at": self.latest_observation_at,
            "freshness_seconds": self.freshness_seconds,
            "coverage": self.coverage.to_dict() if self.coverage else None,
            "cost_tier": self.cost_tier,
            "limitations": self.limitations,
            "warnings": self.warnings,
            "blocking_quality_warning": self.blocking_quality_warning,
            "execution_grade": self.execution_grade,
        }


@dataclass(frozen=True)
class Product:
    """Coinbase product metadata used by v1."""

    product_id: str
    product_type: str
    base_currency_id: str
    quote_currency_id: str
    status: str | None = None
    price_increment: str | None = None
    base_increment: str | None = None
    quote_increment: str | None = None
    base_min_size: str | None = None
    quote_min_size: str | None = None
    base_max_size: str | None = None
    quote_max_size: str | None = None
    trading_disabled: bool = False
    is_disabled: bool = False
    cancel_only: bool = False
    limit_only: bool = False
    post_only: bool = False
    auction_mode: bool = False
    view_only: bool = False

    @classmethod
    def from_coinbase(cls, raw: dict[str, Any]) -> Product:
        """Build from Coinbase product payload."""
        return cls(
            product_id=str(raw["product_id"]).upper(),
            product_type=str(raw.get("product_type") or ""),
            base_currency_id=str(raw.get("base_currency_id") or raw.get("base_currency") or ""),
            quote_currency_id=str(raw.get("quote_currency_id") or raw.get("quote_currency") or ""),
            status=raw.get("status"),
            price_increment=raw.get("price_increment"),
            base_increment=raw.get("base_increment"),
            quote_increment=raw.get("quote_increment"),
            base_min_size=raw.get("base_min_size"),
            quote_min_size=raw.get("quote_min_size"),
            base_max_size=raw.get("base_max_size"),
            quote_max_size=raw.get("quote_max_size"),
            trading_disabled=bool(raw.get("trading_disabled", False)),
            is_disabled=bool(raw.get("is_disabled", False)),
            cancel_only=bool(raw.get("cancel_only", False)),
            limit_only=bool(raw.get("limit_only", False)),
            post_only=bool(raw.get("post_only", False)),
            auction_mode=bool(raw.get("auction_mode", False)),
            view_only=bool(raw.get("view_only", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize product."""
        return {
            "product_id": self.product_id,
            "product_type": self.product_type,
            "base_currency_id": self.base_currency_id,
            "quote_currency_id": self.quote_currency_id,
            "status": self.status,
            "price_increment": self.price_increment,
            "base_increment": self.base_increment,
            "quote_increment": self.quote_increment,
            "base_min_size": self.base_min_size,
            "quote_min_size": self.quote_min_size,
            "base_max_size": self.base_max_size,
            "quote_max_size": self.quote_max_size,
            "trading_disabled": self.trading_disabled,
            "is_disabled": self.is_disabled,
            "cancel_only": self.cancel_only,
            "limit_only": self.limit_only,
            "post_only": self.post_only,
            "auction_mode": self.auction_mode,
            "view_only": self.view_only,
        }
