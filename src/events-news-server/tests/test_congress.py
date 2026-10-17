"""Tests for the congressional trade-disclosure dataset and tool."""

import json
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import httpx
import pytest

from src import server
from src.clients.congress_dataset import (
    MAX_LIMIT,
    CongressDataset,
    CongressDatasetError,
    TradeFilters,
)
from src.config import Settings
from src.response_utils import MAX_RESPONSE_CHARS
from src.tools.congress import get_congress_trades

_BASE_URL = "https://hf.test"
_REPO = "owner/congress"
_COMMIT_A = "a" * 40
_COMMIT_B = "b" * 40
_EVENTS_PATH = "data/political_trade_events/2026-00000-of-00001.parquet"
_SNAPSHOT_PATH = f"/datasets/{_REPO}/resolve/main/snapshot.json"

# The upstream columns the loader projects, with their Parquet types.
_COLUMNS: dict[str, str] = {
    "displayName": "VARCHAR",
    "memberId": "VARCHAR",
    "chamber": "VARCHAR",
    "owner": "VARCHAR",
    "action": "VARCHAR",
    "partialSale": "BOOLEAN",
    "ticker": "VARCHAR",
    "assetDescription": "VARCHAR",
    "assetTypeLabel": "VARCHAR",
    "transactionDate": "DATE",
    "availableAt": "TIMESTAMP",
    "firstAvailableAt": "TIMESTAMP",
    "supersededAt": "TIMESTAMP",
    "version": "INTEGER",
    "amountLow": "DOUBLE",
    "amountHigh": "DOUBLE",
    "sourceUrl": "VARCHAR",
}


def _today_et() -> date:
    return datetime.now(ZoneInfo("America/New_York")).date()


def _available_at(filed: date) -> str:
    """End of the US Eastern filing day as upstream stores it: naive UTC.

    03:59:59.999 UTC the next day falls on the filing day in both EDT and EST.
    """
    return f"{filed + timedelta(days=1)} 03:59:59.999"


def _row(**overrides: Any) -> dict[str, Any]:
    filed = _today_et() - timedelta(days=2)
    row: dict[str, Any] = {
        "displayName": "Jane Q. Member",
        "memberId": "M000001",
        "chamber": "house",
        "owner": "spouse",
        "action": "purchase",
        "partialSale": False,
        "ticker": "NVDA",
        "assetDescription": "NVIDIA Corporation - Common Stock (NVDA)",
        "assetTypeLabel": "Stock",
        "transactionDate": str(filed - timedelta(days=20)),
        "availableAt": _available_at(filed),
        "firstAvailableAt": _available_at(filed),
        "supersededAt": None,
        "version": 1,
        "amountLow": 1001.0,
        "amountHigh": 15000.0,
        "sourceUrl": "https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/2026/1.pdf",
    }
    row.update(overrides)
    return row


def _parquet(path: Path, rows: list[dict[str, Any]], omit: str | None = None) -> bytes:
    """Write ``rows`` as an upstream-shaped Parquet file and return its bytes."""
    names = [name for name in _COLUMNS if name != omit]
    conn = duckdb.connect()
    try:
        conn.execute(f"CREATE TABLE t ({', '.join(f'{n} {_COLUMNS[n]}' for n in names)})")
        placeholders = ", ".join("?" for _ in names)
        conn.executemany(
            f"INSERT INTO t VALUES ({placeholders})", [[r[n] for n in names] for r in rows]
        )
        conn.execute(f"COPY t TO '{path}' (FORMAT parquet)")
    finally:
        conn.close()
    return path.read_bytes()


def _snapshot(schema_version: int = 2, paths: tuple[str, ...] = (_EVENTS_PATH,)) -> dict[str, Any]:
    return {
        "schemaVersion": schema_version,
        "generatedAt": "2026-09-24T03:02:40.125Z",
        "tables": {
            "political_trade_events": {
                "manifests": [{"year": 2026, "files": [{"publicPath": p} for p in paths]}]
            }
        },
    }


class _FakeHub:
    """Serves snapshot.json and one Parquet file the way huggingface.co does."""

    def __init__(self, commit: str, parquet: bytes) -> None:
        self.commit = commit
        self.parquet = parquet
        self.snapshot = _snapshot()
        self.snapshot_status = 200
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if path == _SNAPSHOT_PATH and self.snapshot_status != 200:
            return httpx.Response(self.snapshot_status)
        if path == _SNAPSHOT_PATH:
            return httpx.Response(200, json=self.snapshot, headers={"x-repo-commit": self.commit})
        if path == f"/datasets/{_REPO}/resolve/{self.commit}/{_EVENTS_PATH}":
            return httpx.Response(200, content=self.parquet)
        return httpx.Response(404)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


DatasetFactory = Callable[..., CongressDataset]


@pytest.fixture
def make_dataset() -> Iterator[DatasetFactory]:
    created: list[CongressDataset] = []

    def factory(
        hub: _FakeHub, check_seconds: int = 3600, clock: Callable[[], float] | None = None
    ) -> CongressDataset:
        settings = Settings()
        settings.huggingface_base_url = _BASE_URL
        settings.congress_dataset_repo = _REPO
        settings.congress_refresh_check_seconds = check_seconds
        dataset = CongressDataset(
            settings, transport=httpx.MockTransport(hub.handler), clock=clock or _Clock()
        )
        created.append(dataset)
        return dataset

    yield factory
    for dataset in created:
        dataset.close()


async def _trades(dataset: CongressDataset, **filters: Any) -> dict[str, Any]:
    return await get_congress_trades(dataset, TradeFilters(**filters))


def _urls(result: dict[str, Any]) -> list[str]:
    return [trade["source_url"] for trade in result["trades"]]


class TestLoading:
    """Snapshot download, commit pinning, and refresh policy."""

    async def test_loads_current_versions_and_flags_amendments(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        rows = [
            _row(sourceUrl="u/original", supersededAt="2026-09-20 03:59:59.999"),
            _row(sourceUrl="u/amended", version=2),
            _row(sourceUrl="u/aapl", ticker="AAPL"),
        ]
        dataset = make_dataset(_FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", rows)))

        result = await _trades(dataset, ticker="NVDA")

        assert _urls(result) == ["u/amended"]
        assert result["trades"][0]["amended"] is True
        assert result["snapshot"] == {
            "commit": _COMMIT_A,
            "generated_at": "2026-09-24T03:02:40.125Z",
            "data_as_of": str(_today_et() - timedelta(days=2)),
            "stale": False,
            "refresh_error": None,
        }

    async def test_downloads_are_pinned_to_the_snapshot_commit(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        hub = _FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", [_row()]))

        await _trades(make_dataset(hub))

        assert hub.requests == [
            _SNAPSHOT_PATH,
            f"/datasets/{_REPO}/resolve/{_COMMIT_A}/{_EVENTS_PATH}",
        ]

    async def test_no_upstream_call_within_the_check_interval(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        hub = _FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", [_row()]))
        dataset = make_dataset(hub, check_seconds=3600)

        await _trades(dataset)
        await _trades(dataset)

        assert len(hub.requests) == 2

    async def test_unchanged_commit_skips_the_download(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        hub = _FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", [_row()]))
        dataset = make_dataset(hub, check_seconds=0)

        await _trades(dataset)
        await _trades(dataset)

        assert hub.requests[2:] == [_SNAPSHOT_PATH]

    async def test_new_commit_replaces_the_whole_table(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        hub = _FakeHub(_COMMIT_A, _parquet(tmp_path / "a.parquet", [_row(ticker="NVDA")]))
        dataset = make_dataset(hub, check_seconds=0)
        await _trades(dataset)

        hub.commit = _COMMIT_B
        hub.parquet = _parquet(tmp_path / "b.parquet", [_row(ticker="AAPL")])
        result = await _trades(dataset)

        assert [trade["ticker"] for trade in result["trades"]] == ["AAPL"]
        assert result["snapshot"]["commit"] == _COMMIT_B

    async def test_first_load_failure_raises_and_the_next_call_retries(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        hub = _FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", [_row()]))
        hub.snapshot_status = 503
        dataset = make_dataset(hub, check_seconds=3600)

        with pytest.raises(httpx.HTTPStatusError):
            await _trades(dataset)
        hub.snapshot_status = 200
        result = await _trades(dataset)

        assert result["count"] == 1

    async def test_refresh_failure_after_a_load_serves_stale_and_backs_off(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        hub = _FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", [_row()]))
        clock = _Clock()
        dataset = make_dataset(hub, check_seconds=3600, clock=clock)
        await _trades(dataset)

        hub.snapshot_status = 503
        clock.now = 4000.0
        stale = await _trades(dataset)
        clock.now = 4001.0
        await _trades(dataset)

        assert stale["count"] == 1
        assert stale["snapshot"]["stale"] is True
        assert "503" in stale["snapshot"]["refresh_error"]
        assert hub.requests.count(_SNAPSHOT_PATH) == 2

    async def test_missing_upstream_column_fails_loud(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        parquet = _parquet(tmp_path / "e.parquet", [_row()], omit="sourceUrl")
        dataset = make_dataset(_FakeHub(_COMMIT_A, parquet))

        with pytest.raises(duckdb.BinderException):
            await _trades(dataset)

    @pytest.mark.parametrize(
        "snapshot",
        [
            _snapshot(schema_version=3),
            _snapshot(paths=()),
            _snapshot(paths=tuple(_EVENTS_PATH for _ in range(41))),
            _snapshot(paths=("data/political_filings/2026-00000-of-00001.parquet",)),
            {"schemaVersion": 2, "tables": {}},
        ],
        ids=["schema-version", "no-files", "too-many-files", "outside-table", "no-manifest"],
    )
    async def test_unexpected_snapshot_layout_fails_loud(
        self, tmp_path: Path, make_dataset: DatasetFactory, snapshot: dict[str, Any]
    ) -> None:
        hub = _FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", [_row()]))
        hub.snapshot = snapshot

        with pytest.raises(CongressDatasetError):
            await _trades(make_dataset(hub))


class TestQuery:
    """Filters, date semantics, and response shape."""

    async def test_ticker_matches_dash_and_dot_share_classes(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        rows = [_row(ticker="BRK.B", sourceUrl="u/dot"), _row(ticker="BRK-B", sourceUrl="u/dash")]
        dataset = make_dataset(_FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", rows)))

        for ticker in ("BRK-B", "brk.b"):
            result = await _trades(dataset, ticker=ticker)
            assert sorted(_urls(result)) == ["u/dash", "u/dot"], ticker

    async def test_disclosure_date_is_the_us_eastern_filing_day(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        # Upstream stores the end of the Eastern filing day in naive UTC:
        # 03:59:59.999 under EDT and 04:59:59.999 under EST. Reading the
        # timestamp as Eastern wall time would land on the next day.
        rows = [
            _row(
                availableAt="2026-09-23 03:59:59.999",
                firstAvailableAt="2026-09-23 03:59:59.999",
                transactionDate="2026-09-01",
                sourceUrl="u/edt",
            ),
            _row(
                availableAt="2026-01-15 04:59:59.999",
                firstAvailableAt="2026-01-15 04:59:59.999",
                transactionDate="2026-01-01",
                sourceUrl="u/est",
            ),
        ]
        dataset = make_dataset(_FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", rows)))

        result = await _trades(dataset, days=3650)

        dates = {t["source_url"]: (t["disclosure_date"], t["lag_days"]) for t in result["trades"]}
        assert dates == {"u/edt": ("2026-09-22", 21), "u/est": ("2026-01-14", 13)}

    async def test_window_counts_back_from_the_current_disclosure(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        today = _today_et()
        rows = [
            _row(
                transactionDate=str(today - timedelta(days=400)),
                availableAt=_available_at(today - timedelta(days=2)),
                sourceUrl="u/old-trade-new-filing",
            ),
            _row(
                transactionDate=str(today - timedelta(days=45)),
                availableAt=_available_at(today - timedelta(days=40)),
                sourceUrl="u/old-filing",
            ),
            _row(
                version=2,
                firstAvailableAt=_available_at(today - timedelta(days=60)),
                availableAt=_available_at(today - timedelta(days=1)),
                sourceUrl="u/amended-yesterday",
            ),
        ]
        dataset = make_dataset(_FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", rows)))

        result = await _trades(dataset, days=7)

        assert _urls(result) == ["u/amended-yesterday", "u/old-trade-new-filing"]
        assert result["trades"][0]["first_disclosure_date"] == str(today - timedelta(days=60))

    async def test_member_matches_bioguide_id_or_name_fragment(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        rows = [
            _row(displayName="Nancy Pelosi", memberId="P000197", sourceUrl="u/pelosi"),
            _row(displayName="Rick Scott", memberId="S001217", chamber="senate", sourceUrl="u/rs"),
            _row(displayName="Tim Scott", memberId="S001184", chamber="senate", sourceUrl="u/ts"),
        ]
        dataset = make_dataset(_FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", rows)))

        by_id = await _trades(dataset, member="p000197")
        by_name = await _trades(dataset, member="scott")
        by_chamber = await _trades(dataset, chamber="house")

        assert _urls(by_id) == ["u/pelosi"]
        assert sorted(_urls(by_name)) == ["u/rs", "u/ts"]
        assert _urls(by_chamber) == ["u/pelosi"]

    async def test_no_match_is_an_empty_result_not_an_error(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        dataset = make_dataset(_FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", [_row()])))

        result = await _trades(dataset, ticker="ZZZZ")

        assert (result["count"], result["total_available"], result["truncated"]) == (0, 0, False)
        assert result["trades"] == []

    async def test_limit_keeps_the_newest_disclosures_and_counts_all_matches(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        today = _today_et()
        rows = [
            _row(availableAt=_available_at(today - timedelta(days=d)), sourceUrl=f"u/{d}")
            for d in (5, 1, 4, 2, 3)
        ]
        dataset = make_dataset(_FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", rows)))

        result = await _trades(dataset, limit=2)

        assert _urls(result) == ["u/1", "u/2"]
        assert (result["count"], result["total_available"], result["truncated"]) == (2, 5, True)

    async def test_max_limit_response_fits_the_response_budget(
        self, tmp_path: Path, make_dataset: DatasetFactory
    ) -> None:
        wide = _row(
            displayName="Representative " + "X" * 40,
            assetDescription="A" * 228,  # longest upstream description
            assetTypeLabel="Municipal Security",
            sourceUrl="https://efdsearch.senate.gov/search/view/ptr/" + "f" * 36 + "/",
        )
        rows = [wide for _ in range(MAX_LIMIT)]
        dataset = make_dataset(_FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", rows)))

        result = await _trades(dataset, limit=MAX_LIMIT)

        assert result["count"] == MAX_LIMIT
        assert len(json.dumps(result, default=str)) <= MAX_RESPONSE_CHARS

    @pytest.mark.parametrize(
        "filters",
        [
            {"days": 0},
            {"days": 3651},
            {"limit": 0},
            {"limit": MAX_LIMIT + 1},
            {"chamber": "senators"},
            {"ticker": "  "},
            {"member": ""},
        ],
    )
    def test_out_of_range_filters_are_rejected(self, filters: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            TradeFilters(**filters)


class TestTool:
    """The registered MCP tool wraps failures in the shared error shape."""

    async def test_upstream_failure_becomes_an_error_payload(
        self,
        tmp_path: Path,
        make_dataset: DatasetFactory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        hub = _FakeHub(_COMMIT_A, _parquet(tmp_path / "e.parquet", [_row()]))
        hub.snapshot_status = 503
        monkeypatch.setattr(server, "_congress_dataset", make_dataset(hub))

        result = await server.events_news_get_congress_trades_tool(ticker="NVDA")

        assert result["isError"] is True
        assert result["error"].startswith("HuggingFace:")
