"""Crypto store job-listing tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.storage import CryptoStore


def _result(product_id: str, template: str) -> dict[str, Any]:
    return {
        "strategy_template": template,
        "data_config": {
            "product_id": product_id,
            "timeframe": "1D",
            "start": "2025-06-02T00:00:00+00:00",
            "end": "2026-06-01T00:00:00+00:00",
        },
    }


async def test_list_jobs_returns_identifying_summaries(tmp_path: Path) -> None:
    store = CryptoStore(str(tmp_path / "crypto.db"))
    try:
        await store.store_job(
            "crypto_bt_aaa111", status="completed", result=_result("BTC-USD", "spot_trend_follow")
        )
        await store.store_job(
            "crypto_bt_bbb222",
            status="completed",
            result=_result("SOL-USD", "spot_mean_reversion"),
        )

        jobs = await store.list_jobs(limit=10)

        assert {j["job_id"] for j in jobs} == {"crypto_bt_aaa111", "crypto_bt_bbb222"}
        sol = next(j for j in jobs if j["job_id"] == "crypto_bt_bbb222")
        assert sol["product_id"] == "SOL-USD"
        assert sol["timeframe"] == "1D"
        assert sol["strategy_template"] == "spot_mean_reversion"
        assert sol["start"] == "2025-06-02T00:00:00+00:00"
        # Newest first: created_at must be non-increasing.
        timestamps = [j["created_at"] for j in jobs]
        assert timestamps == sorted(timestamps, reverse=True)
    finally:
        store.close()


async def test_list_jobs_respects_limit(tmp_path: Path) -> None:
    store = CryptoStore(str(tmp_path / "crypto.db"))
    try:
        await store.store_job(
            "crypto_bt_aaa111", status="completed", result=_result("BTC-USD", "t")
        )
        await store.store_job(
            "crypto_bt_bbb222", status="completed", result=_result("ETH-USD", "t")
        )

        assert len(await store.list_jobs(limit=1)) == 1
    finally:
        store.close()


async def test_list_jobs_rejects_nonpositive_limit(tmp_path: Path) -> None:
    store = CryptoStore(str(tmp_path / "crypto.db"))
    try:
        with pytest.raises(ValueError, match="limit must be positive"):
            await store.list_jobs(limit=0)
    finally:
        store.close()
