"""Read-only SQLite access for the corpus.

Opens read-only connections per request (no pool — file-based DB is cheap).
Provides three core operations:
    * `search` — ranked summaries with optional filters; FTS5 when query given
    * `get_entry` — full record with body and references
    * `list_categories` — counts grouped by entry_type

Schema is owned by `scripts/build_index.py`; this module only reads.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from .models import (
    CategoryCount,
    ConceptEntry,
    ConceptSummary,
    CorpusCategoryIndex,
    CorpusEntry,
    CorpusEntrySummary,
    SeminalPaper,
    StrategyEntry,
    StrategySummary,
)

SUMMARY_TRUNCATE_CHARS = 280
TOP_FAILURE_MODES = 2


class CorpusDBError(RuntimeError):
    """Raised when the corpus DB is missing or unreadable."""


@contextmanager
def _open_readonly(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a read-only SQLite connection."""
    if not db_path.is_file():
        raise CorpusDBError(f"corpus.db not found at {db_path}")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _truncate(value: str | None, limit: int = SUMMARY_TRUNCATE_CHARS) -> str | None:
    if value is None:
        return None
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _fetch_aliases(conn: sqlite3.Connection, entry_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT alias FROM corpus_aliases WHERE entry_id = ? ORDER BY alias",
        (entry_id,),
    ).fetchall()
    return [row["alias"] for row in rows]


def _fetch_failure_modes(
    conn: sqlite3.Connection, entry_id: str, limit: int | None = None
) -> list[str]:
    sql = "SELECT failure_mode FROM corpus_failure_modes WHERE entry_id = ? ORDER BY ordinal"
    args: tuple[Any, ...] = (entry_id,)
    if limit is not None:
        sql += " LIMIT ?"
        args = (entry_id, limit)
    rows = conn.execute(sql, args).fetchall()
    return [row["failure_mode"] for row in rows]


def _fetch_asset_classes(conn: sqlite3.Connection, entry_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT asset_class FROM corpus_asset_classes WHERE entry_id = ?",
        (entry_id,),
    ).fetchall()
    return sorted(row["asset_class"] for row in rows)


def _fetch_signal_inputs(conn: sqlite3.Connection, entry_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT signal_input FROM corpus_signal_inputs WHERE entry_id = ?",
        (entry_id,),
    ).fetchall()
    return sorted(row["signal_input"] for row in rows)


def _fetch_papers(conn: sqlite3.Connection, entry_id: str) -> list[SeminalPaper]:
    rows = conn.execute(
        "SELECT title, authors, year, venue, url FROM corpus_papers "
        "WHERE entry_id = ? ORDER BY title",
        (entry_id,),
    ).fetchall()
    out: list[SeminalPaper] = []
    for row in rows:
        try:
            authors = json.loads(row["authors"]) if row["authors"] else []
        except json.JSONDecodeError:
            authors = []
        out.append(
            SeminalPaper(
                title=row["title"],
                authors=list(authors) if isinstance(authors, list) else [],
                year=row["year"],
                venue=row["venue"],
                url=row["url"],
            )
        )
    return out


def _fetch_related_strategies(conn: sqlite3.Connection, concept_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT strategy_id FROM corpus_related_strategies "
        "WHERE concept_id = ? ORDER BY strategy_id",
        (concept_id,),
    ).fetchall()
    return [row["strategy_id"] for row in rows]


def _body(row: sqlite3.Row) -> str:
    """Reassemble the markdown body from individual section columns."""
    sections: list[str] = []
    if row["body_thesis"]:
        sections.append(f"## Thesis\n{row['body_thesis']}")
    if row["body_signal_intuition"]:
        sections.append(f"## Signal intuition\n{row['body_signal_intuition']}")
    if row["body_construction"]:
        sections.append(f"## Construction sketch\n{row['body_construction']}")
    if row["body_notes"]:
        sections.append(f"## Notes\n{row['body_notes']}")
    return "\n\n".join(sections)


def _row_to_summary(conn: sqlite3.Connection, row: sqlite3.Row) -> CorpusEntrySummary:
    if row["entry_type"] == "strategy":
        return StrategySummary(
            id=row["id"],
            canonical_name=row["canonical_name"],
            category=row["category"],
            one_line=row["one_line"],
            when_to_consider=_truncate(row["when_to_consider"]),
            engine_fit=row["engine_fit"],
            approximation_notes=_truncate(row["approximation_notes"]),
            top_failure_modes=_fetch_failure_modes(conn, row["id"], limit=TOP_FAILURE_MODES),
        )
    return ConceptSummary(
        id=row["id"],
        canonical_name=row["canonical_name"],
        category=row["category"],
        definition=_truncate(row["definition"]),
        when_it_matters=_truncate(row["when_it_matters"]),
        related_strategies=_fetch_related_strategies(conn, row["id"]),
    )


def _row_to_entry(conn: sqlite3.Connection, row: sqlite3.Row) -> CorpusEntry:
    if row["entry_type"] == "strategy":
        return StrategyEntry(
            id=row["id"],
            canonical_name=row["canonical_name"],
            category=row["category"],
            aliases=_fetch_aliases(conn, row["id"]),
            one_line=row["one_line"] or "",
            asset_classes=_fetch_asset_classes(conn, row["id"]),
            typical_holding_period=row["typical_holding_period"],
            engine_fit=row["engine_fit"],
            approximation_notes=row["approximation_notes"],
            signal_inputs=_fetch_signal_inputs(conn, row["id"]),
            known_failure_modes=_fetch_failure_modes(conn, row["id"]),
            when_to_consider=row["when_to_consider"] or "",
            when_to_avoid=row["when_to_avoid"] or "",
            body=_body(row),
            references=_fetch_papers(conn, row["id"]),
        )
    return ConceptEntry(
        id=row["id"],
        canonical_name=row["canonical_name"],
        category=row["category"],
        aliases=_fetch_aliases(conn, row["id"]),
        definition=row["definition"] or "",
        when_it_matters=row["when_it_matters"] or "",
        related_strategies=_fetch_related_strategies(conn, row["id"]),
        body=_body(row),
        references=_fetch_papers(conn, row["id"]),
    )


def search(
    db_path: Path,
    query: str | None = None,
    entry_type: Literal["strategy", "concept"] | None = None,
    category: str | None = None,
    asset_class: str | None = None,
    limit: int = 10,
) -> list[CorpusEntrySummary]:
    """Search corpus entries with optional filters.

    When `query` is non-empty, FTS5 bm25 ranking is used. Otherwise, results are
    ordered by canonical_name ascending. All filters are AND-combined.
    """
    limit = max(1, min(limit, 100))
    base_clauses: list[str] = []
    args: list[Any] = []

    if entry_type:
        base_clauses.append("e.entry_type = ?")
        args.append(entry_type)
    if category:
        base_clauses.append("e.category = ?")
        args.append(category)
    if asset_class:
        base_clauses.append(
            "e.id IN (SELECT entry_id FROM corpus_asset_classes WHERE asset_class = ?)"
        )
        args.append(asset_class)

    where_sql = " AND ".join(base_clauses)
    if where_sql:
        where_sql = " AND " + where_sql

    with _open_readonly(db_path) as conn:
        if query:
            sql = (
                "SELECT e.* FROM corpus_entries_fts AS f "
                "JOIN corpus_entries AS e ON e.id = f.id "
                "WHERE corpus_entries_fts MATCH ?" + where_sql + " "
                "ORDER BY bm25(corpus_entries_fts) LIMIT ?"
            )
            rows = conn.execute(sql, [_sanitize_fts(query), *args, limit]).fetchall()
        else:
            sql = (
                "SELECT * FROM corpus_entries AS e WHERE 1=1" + where_sql + " "
                "ORDER BY canonical_name ASC LIMIT ?"
            )
            rows = conn.execute(sql, [*args, limit]).fetchall()
        return [_row_to_summary(conn, row) for row in rows]


def _sanitize_fts(query: str) -> str:
    """Escape FTS5 reserved characters so the user query is a literal phrase match.

    FTS5 query syntax treats double-quote, NEAR, AND, OR, NOT specially. We wrap
    the whole query in double quotes after escaping interior double quotes, so
    the search behaves as a phrase / token match rather than a query DSL.
    """
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


def get_entry(db_path: Path, entry_id: str) -> CorpusEntry | None:
    """Fetch a single entry by id; returns None if not found."""
    with _open_readonly(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM corpus_entries WHERE id = ?",
            (entry_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_entry(conn, row)


def list_categories(db_path: Path) -> CorpusCategoryIndex:
    """Return categories split by entry_type with counts."""
    with _open_readonly(db_path) as conn:
        rows = conn.execute(
            "SELECT entry_type, category, COUNT(*) AS n FROM corpus_entries "
            "GROUP BY entry_type, category ORDER BY entry_type, category"
        ).fetchall()
    strategies: list[CategoryCount] = []
    concepts: list[CategoryCount] = []
    for row in rows:
        target = strategies if row["entry_type"] == "strategy" else concepts
        target.append(CategoryCount(category=row["category"], count=row["n"]))
    return CorpusCategoryIndex(strategies=strategies, concepts=concepts)
