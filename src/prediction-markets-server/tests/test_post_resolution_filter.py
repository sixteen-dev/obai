"""Regression: post-resolution price rows must not leak into analytics."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.storage import (
    MarketRow,
    PredictionDuckDBManager,
    PredictionStore,
    PriceRow,
    TokenRow,
)
from src.tools.calibration import _load_price_rows


def _aware(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_load_price_rows_filters_post_resolution_rows() -> None:
    """Rows after max_timestamp must be excluded at the DB layer."""
    store = PredictionStore(manager=PredictionDuckDBManager(db_path=":memory:"))
    store.ensure_connected()
    end_date = _aware(2026, 6, 1)
    await store.upsert_market(MarketRow(condition_id="0xc", question="Q?", last_refreshed=end_date))
    await store.upsert_tokens(
        [TokenRow(token_id="tYes", condition_id="0xc", outcome_index=0, outcome_label="Yes")]
    )
    rows_to_persist = [
        # Two pre-end samples — should survive the filter
        PriceRow(
            token_id="tYes",
            condition_id="0xc",
            timestamp=_aware(2026, 5, 30, 12),
            price=0.40,
            fidelity_minutes=60,
            source="clob_prices_history",
            fetched_at=end_date,
        ),
        PriceRow(
            token_id="tYes",
            condition_id="0xc",
            timestamp=_aware(2026, 5, 31, 12),
            price=0.55,
            fidelity_minutes=60,
            source="clob_prices_history",
            fetched_at=end_date,
        ),
        # Two post-end samples — should be dropped
        PriceRow(
            token_id="tYes",
            condition_id="0xc",
            timestamp=_aware(2026, 6, 1, 12),
            price=1.0,
            fidelity_minutes=60,
            source="clob_prices_history",
            fetched_at=end_date,
        ),
        PriceRow(
            token_id="tYes",
            condition_id="0xc",
            timestamp=_aware(2026, 6, 2, 0),
            price=1.0,
            fidelity_minutes=60,
            source="clob_prices_history",
            fetched_at=end_date,
        ),
    ]
    await store.upsert_price_rows(rows_to_persist)

    filtered = _load_price_rows(store, "tYes", "0xc", 60, max_timestamp=end_date)
    assert len(filtered) == 2
    assert max(r.timestamp for r in filtered) <= end_date
    assert all(r.price != 1.0 for r in filtered)

    # Sanity: without max_timestamp the loader returns everything.
    unfiltered = _load_price_rows(store, "tYes", "0xc", 60)
    assert len(unfiltered) == 4
    store.close()
