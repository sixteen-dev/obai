"""Pure transformations from API payloads into storage row dataclasses.

Each function is intentionally I/O-free: callers (downloader.py) handle the
HTTP + DB writes, normalizers just translate shapes. This keeps the rules
auditable and unit-testable without mocks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..storage import (
    MarketRow,
    PriceRow,
    TokenRow,
    TradeRow,
    infer_resolution,
)


def gamma_market_to_rows(
    payload: dict[str, Any],
    fetched_at: datetime,
) -> tuple[MarketRow, list[TokenRow]]:
    """Convert a normalized Gamma market dict into a MarketRow + list of TokenRows.

    Resolution fields are filled by infer_resolution() so the rule lives in
    one place (storage/resolution.py) and the normalizer cannot drift from
    the calibration/backtest definition.

    Args:
        payload: Output of GammaClient._normalize_market.
        fetched_at: Timestamp to stamp as last_refreshed.

    Returns:
        (MarketRow, list[TokenRow])

    Raises:
        ValueError: If the payload lacks a condition_id or question.

    """
    condition_id = (payload.get("condition_id") or "").strip()
    question = (payload.get("question") or "").strip()
    if not condition_id:
        msg = "Gamma payload missing condition_id"
        raise ValueError(msg)
    if not question:
        msg = f"Gamma payload {condition_id!r} missing question"
        raise ValueError(msg)

    resolution = infer_resolution(payload)

    market = MarketRow(
        condition_id=condition_id,
        question=question,
        last_refreshed=fetched_at,
        slug=_str_or_none(payload.get("slug")),
        description=_str_or_none(payload.get("description")),
        category=_str_or_none(payload.get("category")),
        event_slug=_str_or_none(payload.get("event_slug")),
        event_title=_str_or_none(payload.get("event_title")),
        start_date=_parse_iso(payload.get("start_date")),
        end_date=_parse_iso(payload.get("end_date")),
        closed_time=_parse_iso(payload.get("closed_time")),
        active=_bool_or_none(payload.get("active")),
        closed=_bool_or_none(payload.get("closed")),
        accepting_orders=_bool_or_none(payload.get("accepting_orders")),
        volume=_float_or_none(payload.get("volume")),
        volume_24h=_float_or_none(payload.get("volume_24h")),
        liquidity=_float_or_none(payload.get("liquidity")),
        resolution_source=_str_or_none(payload.get("resolution_source")),
        uma_resolution_status=_str_or_none(payload.get("uma_resolution_status")),
        winning_outcome=resolution.winning_outcome,
        resolution_status=resolution.resolution_status,
        resolution_method=resolution.resolution_method,
        resolution_confidence=resolution.resolution_confidence,
    )

    tokens = _tokens_from_payload(condition_id, payload)
    return market, tokens


def clob_history_to_price_rows(
    *,
    token_id: str,
    condition_id: str,
    history: list[dict[str, Any]],
    fidelity_minutes: int,
    source: str,
    fetched_at: datetime,
) -> list[PriceRow]:
    """Convert a CLOB price-history history array into PriceRows.

    Points lacking a parseable timestamp or numeric price are dropped, not
    coerced. The caller logs the drop counts via _pm_meta quality_flags so
    sparse upstream data is visible downstream.

    Args:
        token_id: CLOB token (asset) ID the history is for.
        condition_id: Parent market condition ID.
        history: List of {timestamp, price} dicts from
            ClobClient.get_price_history.
        fidelity_minutes: Requested sample resolution in minutes.
        source: Source tag (e.g. "clob_prices_history").
        fetched_at: Timestamp for the fetched_at column.

    Returns:
        List of PriceRow values, in source order.

    """
    rows: list[PriceRow] = []
    for point in history:
        ts = _epoch_to_dt(point.get("timestamp"))
        price = _float_or_none(point.get("price"))
        if ts is None or price is None:
            continue
        rows.append(
            PriceRow(
                token_id=token_id,
                condition_id=condition_id,
                timestamp=ts,
                price=price,
                fidelity_minutes=fidelity_minutes,
                source=source,
                fetched_at=fetched_at,
            )
        )
    return rows


def data_api_trades_to_rows(
    *,
    condition_id: str,
    trades: list[dict[str, Any]],
    fetched_at: datetime,
    source: str = "data_api_trades",
) -> list[TradeRow]:
    """Convert normalized Data API trade dicts into TradeRows.

    The Data API does not expose log_index, so trade_key composition will
    take the Data API branch in build_trade_key (uses tx_hash + composite
    fields). We pass the raw price/size strings through when available so
    keys do not drift across re-fetches.

    Args:
        condition_id: Parent market condition ID.
        trades: List of normalized trades (DataClient._normalize_trade).
        fetched_at: Timestamp for the fetched_at column.
        source: Source tag (defaults to "data_api_trades").

    Returns:
        List of TradeRows. Unkeyable rows are still emitted; the store
        layer logs and skips them at insert time.

    """
    rows: list[TradeRow] = []
    for trade in trades:
        raw_id = trade.get("id")
        # The data_client folds transactionHash and a string id into a
        # single "id" field — treat 0x-prefixed values as transaction hashes,
        # and everything else as a source-specific id.
        tx_hash: str | None = None
        source_trade_id: str | None = None
        if isinstance(raw_id, str) and raw_id:
            if raw_id.startswith("0x"):
                tx_hash = raw_id
            else:
                source_trade_id = raw_id

        price = _float_or_none(trade.get("price"))
        size = _float_or_none(trade.get("size"))
        rows.append(
            TradeRow(
                source=source,
                condition_id=condition_id,
                fetched_at=fetched_at,
                source_trade_id=source_trade_id,
                transaction_hash=tx_hash,
                asset_id=_str_or_none(trade.get("asset_id")) or _str_or_none(trade.get("token_id")),
                timestamp=_epoch_to_dt(trade.get("timestamp")),
                price=price,
                size=size,
                side=_str_or_none(trade.get("side")),
                outcome=_str_or_none(trade.get("outcome")),
                wallet=_str_or_none(trade.get("wallet")),
                price_string=str(price) if price is not None else None,
                size_string=str(size) if size is not None else None,
            )
        )
    return rows


# -- helpers ------------------------------------------------------------------


def _tokens_from_payload(condition_id: str, payload: dict[str, Any]) -> list[TokenRow]:
    """Pair clob_token_ids with outcomes; bail if the arrays do not align."""
    raw_token_ids = payload.get("clob_token_ids")
    raw_outcomes = payload.get("outcomes")
    if not isinstance(raw_token_ids, list) or not isinstance(raw_outcomes, list):
        return []
    if len(raw_token_ids) != len(raw_outcomes):
        return []
    out: list[TokenRow] = []
    for idx, (tok, outcome) in enumerate(zip(raw_token_ids, raw_outcomes, strict=True)):
        if not isinstance(tok, str) or not tok.strip():
            continue
        if not isinstance(outcome, str) or not outcome.strip():
            continue
        out.append(
            TokenRow(
                token_id=tok.strip(),
                condition_id=condition_id,
                outcome_index=idx,
                outcome_label=outcome.strip(),
            )
        )
    return out


def _str_or_none(value: Any) -> str | None:
    """Trim and demote empty/non-string values to None."""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _float_or_none(value: Any) -> float | None:
    """Coerce numerics to float; return None for None/non-numeric inputs."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    """Coerce to bool when the input is a real bool; otherwise None."""
    if isinstance(value, bool):
        return value
    return None


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string into an aware UTC datetime, or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _epoch_to_dt(value: Any) -> datetime | None:
    """Coerce a CLOB epoch-seconds timestamp into a UTC datetime."""
    if value is None:
        return None
    try:
        epoch = int(value)
    except (TypeError, ValueError):
        return None
    if epoch <= 0:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc)
