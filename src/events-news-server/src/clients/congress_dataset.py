"""Congressional trade disclosures from the public Hugging Face dataset.

Upstream (``austin-starks/congressional-stock-trades``) extracts the official
House Clerk and Senate eFD periodic transaction reports and publishes the
result as Parquet. This client downloads the published event table and queries
it in memory; it does no extraction of its own.

The data is not append-only: upstream migrations rewrite historical year files
and amendments supersede earlier rows, so each new upstream commit is loaded in
full rather than merged into the previous one.
"""

import asyncio
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import duckdb
import httpx

from ..config import Settings
from ..logging_config import get_logger, log_api_call

logger = get_logger(__name__)

MAX_DAYS = 3650
# Real rows serialize to ~500 characters (widest seen: 680). 50 keeps even a
# page of the widest rows inside the 40k response budget, so truncate_response
# never drops rows behind a count that claims them.
MAX_LIMIT = 50

_EXPECTED_SCHEMA_VERSION = 2
_EVENTS_TABLE = "political_trade_events"
_MAX_FILES = 40
_HTTP_TIMEOUT_SECONDS = 30.0
_BIOGUIDE_ID = re.compile(r"[A-Z]\d{6}")

# DuckDB sizes itself from host RAM by default, not from the container limit.
_DUCKDB_SETTINGS = ("SET memory_limit = '256MB'", "SET threads = 2")

# The explicit projection pins the upstream schema: a renamed or dropped column
# fails the load with a binder error instead of reaching the model as nulls.
# availableAt/firstAvailableAt hold the end of the US Eastern filing day as a
# naive UTC TIMESTAMP, so they are tagged as UTC before converting to Eastern.
# CREATE OR REPLACE is atomic: a failed load leaves the previous table intact.
_LOAD_SQL = """
CREATE OR REPLACE TABLE congress_events AS
SELECT
    displayName AS member,
    memberId AS member_id,
    chamber,
    owner,
    action,
    partialSale AS partial_sale,
    ticker,
    assetDescription AS asset,
    assetTypeLabel AS asset_type,
    transactionDate AS transaction_date,
    CAST(timezone('America/New_York', timezone('UTC', availableAt)) AS DATE)
        AS disclosure_date,
    CAST(timezone('America/New_York', timezone('UTC', firstAvailableAt)) AS DATE)
        AS first_disclosure_date,
    disclosure_date - transactionDate AS lag_days,
    amountLow AS amount_low,
    amountHigh AS amount_high,
    version > 1 AS amended,
    sourceUrl AS source_url,
    replace(upper(ticker), '-', '.') AS ticker_key
FROM read_parquet(?)
WHERE supersededAt IS NULL
"""

_SELECT_TRADES = (
    "SELECT * EXCLUDE (ticker_key) FROM congress_events WHERE {where} "
    "ORDER BY disclosure_date DESC, member, transaction_date DESC, source_url LIMIT ?"
)
_COUNT_TRADES = "SELECT count(*) FROM congress_events WHERE {where}"
_WINDOW_CLAUSE = (
    "disclosure_date >= CAST(timezone('America/New_York', now()) AS DATE) - CAST(? AS INTEGER)"
)

Chamber = Literal["house", "senate"]


class CongressDatasetError(Exception):
    """Raised when the published snapshot does not have the expected layout."""


@dataclass(frozen=True)
class TradeFilters:
    """Validated filters for one congressional-trades query.

    Attributes:
        ticker: Ticker to match; share-class dash and dot forms are equivalent.
        member: Bioguide ID (exact) or member-name fragment (case-insensitive).
        chamber: Restrict to 'house' or 'senate'.
        days: Look-back window in days on the disclosure date.
        limit: Maximum rows to return.
    """

    ticker: str | None = None
    member: str | None = None
    chamber: Chamber | None = None
    days: int = 90
    limit: int = 25

    def __post_init__(self) -> None:
        """Reject out-of-range values before any query runs.

        Raises:
            ValueError: If a filter is blank or outside its allowed range.
        """
        if not 1 <= self.days <= MAX_DAYS:
            raise ValueError(f"days must be between 1 and {MAX_DAYS}, got {self.days}")
        if not 1 <= self.limit <= MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_LIMIT}, got {self.limit}")
        if self.chamber not in (None, "house", "senate"):
            raise ValueError(f"chamber must be 'house' or 'senate', got {self.chamber!r}")
        for name, value in (("ticker", self.ticker), ("member", self.member)):
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank")


@dataclass(frozen=True)
class TradePage:
    """One page of matching trades plus the count of every match."""

    total_matched: int
    trades: list[dict[str, Any]]


@dataclass(frozen=True)
class _Snapshot:
    commit: str
    generated_at: str
    data_as_of: str | None


class CongressDataset:
    """In-memory copy of the upstream congressional trade-events table.

    Owns one in-memory DuckDB connection for the life of the server; ``close``
    releases it. Refreshes are lazy: ``ensure_fresh`` asks upstream for its
    current commit at most once per ``congress_refresh_check_seconds`` and
    reloads only when the commit changed.
    """

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Open the in-memory database; nothing is downloaded until first use.

        Args:
            settings: Settings naming the Hugging Face host, repo, and interval.
            transport: Optional httpx transport, for tests.
            clock: Monotonic clock in seconds, for tests.
        """
        base_url = settings.huggingface_base_url.rstrip("/")
        self.repo_url = f"{base_url}/datasets/{settings.congress_dataset_repo}"
        self._check_seconds = settings.congress_refresh_check_seconds
        self._transport = transport
        self._clock = clock
        self._lock = asyncio.Lock()
        self._conn = duckdb.connect(":memory:")
        for statement in _DUCKDB_SETTINGS:
            self._conn.execute(statement)
        self._snapshot: _Snapshot | None = None
        self._checked_at: float | None = None
        self._refresh_error: str | None = None

    def close(self) -> None:
        """Close the DuckDB connection."""
        self._conn.close()

    async def ensure_fresh(self) -> None:
        """Load or refresh the table if the check interval has elapsed.

        Raises:
            httpx.HTTPError: If the first load cannot reach upstream.
            duckdb.Error: If the first load cannot read the published files.
            CongressDatasetError: If the first snapshot has an unexpected layout.
        """
        async with self._lock:
            now = self._clock()
            if self._checked_at is not None and now - self._checked_at < self._check_seconds:
                return
            await self._refresh_or_keep_stale()
            self._checked_at = now

    def query(self, filters: TradeFilters) -> TradePage:
        """Return the newest-disclosed trades that match ``filters``.

        Args:
            filters: Validated query filters.

        Returns:
            Up to ``filters.limit`` trades, newest disclosure first, and the
            total number of matches.

        Raises:
            RuntimeError: If no snapshot has been loaded yet.
        """
        self._require_snapshot()
        where, params = _where_clause(filters)
        count_row = self._conn.execute(_COUNT_TRADES.format(where=where), params).fetchone()
        if count_row is None:
            raise RuntimeError("congress trade count query returned no row")
        cursor = self._conn.execute(_SELECT_TRADES.format(where=where), [*params, filters.limit])
        columns = [column[0] for column in cursor.description]
        trades = [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        return TradePage(total_matched=int(count_row[0]), trades=trades)

    def status(self) -> dict[str, Any]:
        """Describe the loaded snapshot and whether its last refresh failed.

        Returns:
            Commit, upstream generation time, newest disclosure date, and
            staleness of the data being served.

        Raises:
            RuntimeError: If no snapshot has been loaded yet.
        """
        snapshot = self._require_snapshot()
        return {
            "commit": snapshot.commit,
            "generated_at": snapshot.generated_at,
            "data_as_of": snapshot.data_as_of,
            "stale": self._refresh_error is not None,
            "refresh_error": self._refresh_error,
        }

    async def _refresh_or_keep_stale(self) -> None:
        """Refresh from upstream; keep serving the loaded snapshot on failure.

        With nothing loaded the error propagates, and because the caller then
        skips recording the check, the next call retries. Once a snapshot
        exists, a failure is logged and surfaced through ``status()``, and the
        next attempt waits a full interval so an outage does not stall calls.
        """
        try:
            await self._refresh()
            self._refresh_error = None
        except (httpx.HTTPError, duckdb.Error, CongressDatasetError, ValueError) as e:
            if self._snapshot is None:
                raise
            self._refresh_error = f"{type(e).__name__}: {e}"
            logger.exception("congress_refresh_failed_serving_stale", commit=self._snapshot.commit)

    async def _refresh(self) -> None:
        """Load the upstream commit in full when it differs from the loaded one."""
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT_SECONDS, transport=self._transport, follow_redirects=True
        ) as client:
            snapshot, commit = await self._fetch_snapshot(client)
            if self._snapshot is not None and commit == self._snapshot.commit:
                return
            paths = _event_file_paths(snapshot)
            await self._load(client, commit, paths)
        self._snapshot = _Snapshot(
            commit=commit,
            generated_at=str(snapshot.get("generatedAt", "")),
            data_as_of=self._newest_disclosure(),
        )
        logger.info("congress_dataset_loaded", commit=commit, files=len(paths))

    async def _fetch_snapshot(self, client: httpx.AsyncClient) -> tuple[dict[str, Any], str]:
        """Fetch snapshot.json and the commit it was served from."""
        log_api_call(logger, "huggingface", "resolve/main/snapshot.json")
        response = await client.get(f"{self.repo_url}/resolve/main/snapshot.json")
        response.raise_for_status()
        # huggingface.co's own resolve response carries the commit; a redirect
        # to its cache or CDN may not, so read it from the first hop.
        origin = response.history[0] if response.history else response
        commit = origin.headers.get("x-repo-commit")
        if not commit:
            raise CongressDatasetError("snapshot.json response has no x-repo-commit header")
        snapshot = response.json()
        if not isinstance(snapshot, dict):
            raise CongressDatasetError("snapshot.json is not a JSON object")
        version = snapshot.get("schemaVersion")
        if version != _EXPECTED_SCHEMA_VERSION:
            raise CongressDatasetError(f"unsupported snapshot schemaVersion {version!r}")
        return snapshot, commit

    async def _load(self, client: httpx.AsyncClient, commit: str, paths: list[str]) -> None:
        """Download the commit's event files and rebuild the table from them."""
        log_api_call(logger, "huggingface", "resolve", {"commit": commit, "files": len(paths)})
        with tempfile.TemporaryDirectory(prefix="congress-events-") as tmp:
            local_files: list[str] = []
            for index, public_path in enumerate(paths):
                response = await client.get(f"{self.repo_url}/resolve/{commit}/{public_path}")
                response.raise_for_status()
                target = Path(tmp) / f"{index:03d}.parquet"
                target.write_bytes(response.content)
                local_files.append(str(target))
            self._conn.execute(_LOAD_SQL, [local_files])

    def _newest_disclosure(self) -> str | None:
        """Return the newest disclosure date in the loaded table, if any."""
        row = self._conn.execute(
            "SELECT CAST(max(disclosure_date) AS VARCHAR) FROM congress_events"
        ).fetchone()
        return None if row is None else row[0]

    def _require_snapshot(self) -> _Snapshot:
        if self._snapshot is None:
            raise RuntimeError("congress dataset is not loaded; call ensure_fresh() first")
        return self._snapshot


def _event_file_paths(snapshot: dict[str, Any]) -> list[str]:
    """List the event-table Parquet paths named by the snapshot manifest.

    Args:
        snapshot: Parsed snapshot.json.

    Returns:
        Repo-relative Parquet paths.

    Raises:
        CongressDatasetError: If the manifest is malformed, empty, oversized,
            or names a file outside the event table.
    """
    try:
        manifests = snapshot["tables"][_EVENTS_TABLE]["manifests"]
        paths = [str(file["publicPath"]) for manifest in manifests for file in manifest["files"]]
    except (KeyError, TypeError) as e:
        raise CongressDatasetError(f"snapshot manifest is malformed: {e!r}") from e
    if not 1 <= len(paths) <= _MAX_FILES:
        raise CongressDatasetError(f"expected 1-{_MAX_FILES} event files, found {len(paths)}")
    prefix = f"data/{_EVENTS_TABLE}/"
    outside = [p for p in paths if not (p.startswith(prefix) and p.endswith(".parquet"))]
    if outside:
        raise CongressDatasetError(f"snapshot names files outside {prefix}: {outside}")
    return paths


def _where_clause(filters: TradeFilters) -> tuple[str, list[object]]:
    """Build a parameterized WHERE clause; only fixed fragments enter the SQL.

    Args:
        filters: Validated query filters.

    Returns:
        The clause text and its bound parameters, in order.
    """
    clauses = [_WINDOW_CLAUSE]
    params: list[object] = [filters.days]
    if filters.ticker is not None:
        clauses.append("ticker_key = ?")
        params.append(filters.ticker.strip().upper().replace("-", "."))
    if filters.member is not None:
        clause, value = _member_clause(filters.member)
        clauses.append(clause)
        params.append(value)
    if filters.chamber is not None:
        clauses.append("chamber = ?")
        params.append(filters.chamber)
    return " AND ".join(clauses), params


def _member_clause(member: str) -> tuple[str, str]:
    """Match a Bioguide ID exactly, anything else as a name fragment."""
    key = member.strip()
    if _BIOGUIDE_ID.fullmatch(key.upper()):
        return "member_id = ?", key.upper()
    return "contains(lower(member), ?)", key.lower()
