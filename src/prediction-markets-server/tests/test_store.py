"""Tests for storage.store.PredictionStore — upsert + read paths."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.storage.db import PredictionDuckDBManager
from src.storage.store import (
    MarketRow,
    MetaRow,
    PredictionStore,
    PriceRow,
    TokenRow,
    TradeRow,
    build_trade_key,
)


@pytest.fixture
def store() -> PredictionStore:
    """In-memory store."""
    return PredictionStore(manager=PredictionDuckDBManager(db_path=":memory:"))


def _now() -> datetime:
    return datetime(2026, 5, 16, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_upsert_market_dedupes_on_condition_id(store: PredictionStore) -> None:
    """Two upserts with same condition_id leave one row with the latest fields."""
    row1 = MarketRow(
        condition_id="0xabc",
        question="Will X happen?",
        last_refreshed=_now(),
        volume=100.0,
    )
    row2 = MarketRow(
        condition_id="0xabc",
        question="Will X happen?",
        last_refreshed=_now(),
        volume=250.0,
    )
    await store.upsert_market(row1)
    await store.upsert_market(row2)
    fetched = store.get_market("0xabc")
    assert fetched is not None
    assert fetched["volume"] == 250.0
    count_row = store.manager.conn.execute(
        "SELECT count(*) FROM pm_markets WHERE condition_id = ?", ["0xabc"]
    ).fetchone()
    assert count_row is not None
    assert count_row[0] == 1
    store.close()


@pytest.mark.asyncio
async def test_upsert_price_rows_dedupes_on_pk(store: PredictionStore) -> None:
    """Same (token, timestamp, fidelity, source) ⇒ price update, not duplicate row."""
    ts = _now()
    rows = [
        PriceRow(
            token_id="tok1",
            condition_id="0xabc",
            timestamp=ts,
            price=0.42,
            fidelity_minutes=60,
            source="clob_prices_history",
            fetched_at=ts,
        ),
        PriceRow(
            token_id="tok1",
            condition_id="0xabc",
            timestamp=ts,
            price=0.55,
            fidelity_minutes=60,
            source="clob_prices_history",
            fetched_at=ts,
        ),
    ]
    await store.upsert_price_rows(rows)
    result = store.manager.conn.execute(
        "SELECT count(*), max(price) FROM pm_price_history WHERE token_id = ?", ["tok1"]
    ).fetchone()
    assert result == (1, 0.55)
    store.close()


@pytest.mark.asyncio
async def test_upsert_tokens_writes_all(store: PredictionStore) -> None:
    """Bulk token upsert writes every row."""
    rows = [
        TokenRow(token_id="t1", condition_id="0xc", outcome_index=0, outcome_label="Yes"),
        TokenRow(token_id="t2", condition_id="0xc", outcome_index=1, outcome_label="No"),
    ]
    await store.upsert_tokens(rows)
    n = store.manager.conn.execute(
        "SELECT count(*) FROM pm_tokens WHERE condition_id = ?", ["0xc"]
    ).fetchone()
    assert n is not None
    assert n[0] == 2
    store.close()


def test_build_trade_key_onchain_uses_log_index() -> None:
    """On-chain fills compose source:tx:log_index:asset_id."""
    row = TradeRow(
        source="onchain",
        condition_id="0xcafe",
        fetched_at=_now(),
        transaction_hash="0xABC",
        log_index=42,
        asset_id="0xToken",
    )
    assert build_trade_key(row) == "onchain:0xabc:42:0xtoken"


def test_build_trade_key_dataapi_uses_full_composite() -> None:
    """Data API rows (no log_index) compose the full §8.4 string with raw price/size strings."""
    row = TradeRow(
        source="data_api",
        condition_id="0xCafe",
        fetched_at=_now(),
        transaction_hash="0xTx",
        asset_id="0xToken",
        timestamp=_now(),
        price=0.42,
        size=100.0,
        side="BUY",
        wallet="0xWallet",
        price_string="0.420",
        size_string="100.00",
    )
    key = build_trade_key(row)
    parts = key.split(":")
    # 9 fields per §8.4 Data API formula: source:tx:asset:condition:ts:side:price:size:wallet
    assert len(parts) == 9
    assert parts[0] == "data_api"
    assert parts[1] == "0xtx"
    assert parts[2] == "0xtoken"
    assert parts[3] == "0xcafe"
    # parts[4] is epoch seconds — exact value depends on _now() but must be digit-only
    assert parts[4].isdigit()
    assert parts[5] == "buy"
    assert parts[6] == "0.420"
    assert parts[7] == "100.00"
    assert parts[8] == "0xwallet"


def test_build_trade_key_raises_when_unkeyable() -> None:
    """No transaction_hash, no source_trade_id ⇒ ValueError, not silent skip."""
    row = TradeRow(source="data_api", condition_id="0xc", fetched_at=_now())
    with pytest.raises(ValueError, match="cannot key"):
        build_trade_key(row)


@pytest.mark.asyncio
async def test_upsert_trades_skips_unkeyable(store: PredictionStore) -> None:
    """Mixed batch: keyable rows land, unkeyable are skipped without raising."""
    rows = [
        TradeRow(
            source="data_api",
            condition_id="0xc",
            fetched_at=_now(),
            transaction_hash="0xtx1",
            asset_id="0xa",
            timestamp=_now(),
            side="BUY",
            price=0.5,
            size=1.0,
        ),
        TradeRow(source="data_api", condition_id="0xc", fetched_at=_now()),  # unkeyable
    ]
    written = await store.upsert_trades(rows)
    assert written == 1
    count = store.manager.conn.execute("SELECT count(*) FROM pm_trades").fetchone()
    assert count is not None
    assert count[0] == 1
    store.close()


@pytest.mark.asyncio
async def test_update_meta_and_get_coverage(store: PredictionStore) -> None:
    """update_meta writes through; get_price_history_coverage reads it back."""
    row = MetaRow(
        entity_type="price_history",
        entity_id="tok1",
        source="clob_prices_history",
        last_refreshed=_now(),
        first_timestamp=_now(),
        last_timestamp=_now(),
        row_count=42,
        fidelity_minutes=60,
        quality_flags="sparse_short_horizon_history",
    )
    await store.update_meta(row)
    coverage = store.get_price_history_coverage("tok1", 60, "clob_prices_history")
    assert coverage is not None
    assert coverage["row_count"] == 42
    assert coverage["quality_flags"] == "sparse_short_horizon_history"
    store.close()


def test_get_market_returns_none_when_missing(store: PredictionStore) -> None:
    """get_market should return None — not raise — when the row is absent."""
    store.ensure_connected()
    assert store.get_market("0xmissing") is None
    store.close()
