"""Build the OBaI knowledge-base SQLite index from markdown corpus files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CORPUS = ROOT / "corpus"
PRIVATE_CORPUS = ROOT / "corpus_private"
DEFAULT_DB = ROOT / "corpus.db"

REQUIRED_STRATEGY_FIELDS = {
    "entry_type",
    "id",
    "canonical_name",
    "one_line",
    "category",
    "asset_classes",
    "known_failure_modes",
    "when_to_consider",
    "when_to_avoid",
    "engine_fit",
}
REQUIRED_CONCEPT_FIELDS = {
    "entry_type",
    "id",
    "canonical_name",
    "category",
    "definition",
    "when_it_matters",
}
VALID_ENTRY_TYPES = {"strategy", "concept"}
VALID_ENGINE_FIT = {"native", "approximate", "reference_only"}


def parse_markdown(path: Path) -> tuple[dict[str, Any], str, dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n(.*)$", text, flags=re.DOTALL)
    if not match:
        raise ValueError(f"{path}: missing YAML frontmatter")
    frontmatter = yaml.safe_load(match.group(1)) or {}
    body = match.group(2)
    sections = extract_sections(body)
    return frontmatter, body, sections


def extract_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.MULTILINE))
    for index, match in enumerate(matches):
        title = match.group(1).strip().casefold()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        sections[title] = body[start:end].strip()
    return sections


def validate_entry(path: Path, frontmatter: dict[str, Any], sections: dict[str, str]) -> None:
    entry_type = frontmatter.get("entry_type")
    if entry_type not in VALID_ENTRY_TYPES:
        raise ValueError(f"{path}: invalid entry_type {entry_type!r}")

    required = REQUIRED_STRATEGY_FIELDS if entry_type == "strategy" else REQUIRED_CONCEPT_FIELDS
    missing = [field for field in sorted(required) if not frontmatter.get(field)]
    if missing:
        raise ValueError(f"{path}: missing required frontmatter fields: {', '.join(missing)}")

    if entry_type == "strategy":
        engine_fit = frontmatter.get("engine_fit")
        if engine_fit not in VALID_ENGINE_FIT:
            raise ValueError(f"{path}: invalid engine_fit {engine_fit!r}")
        if engine_fit in {"approximate", "reference_only"} and not frontmatter.get(
            "approximation_notes"
        ):
            raise ValueError(f"{path}: approximation_notes required for {engine_fit}")
        for section in ["thesis", "signal intuition"]:
            if not sections.get(section):
                raise ValueError(f"{path}: missing body section ## {section.title()}")


def iter_entry_paths() -> list[Path]:
    roots = [PUBLIC_CORPUS]
    # Private corpus is opt-in by presence: include only when the maintainer's
    # gitignored corpus_private/ tree is present in the build context.
    if PRIVATE_CORPUS.exists() and any(PRIVATE_CORPUS.rglob("*.md")):
        roots.append(PRIVATE_CORPUS)
    paths: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.md"):
            if "_drafts" in path.parts:
                continue
            paths.append(path)
    return sorted(paths)


def schema_sql() -> str:
    return """
PRAGMA foreign_keys = ON;

CREATE TABLE corpus_entries (
    id TEXT PRIMARY KEY,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('strategy', 'concept')),
    canonical_name TEXT NOT NULL,
    category TEXT NOT NULL,
    one_line TEXT,
    body_markdown TEXT NOT NULL,
    body_thesis TEXT,
    body_signal_intuition TEXT,
    body_construction TEXT,
    body_notes TEXT,
    typical_holding_period TEXT,
    when_to_consider TEXT,
    when_to_avoid TEXT,
    engine_fit TEXT CHECK (
        engine_fit IS NULL OR engine_fit IN ('native', 'approximate', 'reference_only')
    ),
    approximation_notes TEXT,
    definition TEXT,
    when_it_matters TEXT,
    source_file_path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_entries_type ON corpus_entries(entry_type);
CREATE INDEX idx_entries_category ON corpus_entries(category);

CREATE TABLE corpus_aliases (
    entry_id TEXT NOT NULL REFERENCES corpus_entries(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    PRIMARY KEY (entry_id, alias)
);

CREATE TABLE corpus_asset_classes (
    entry_id TEXT NOT NULL REFERENCES corpus_entries(id) ON DELETE CASCADE,
    asset_class TEXT NOT NULL,
    PRIMARY KEY (entry_id, asset_class)
);
CREATE INDEX idx_asset_classes ON corpus_asset_classes(asset_class);

CREATE TABLE corpus_failure_modes (
    entry_id TEXT NOT NULL REFERENCES corpus_entries(id) ON DELETE CASCADE,
    failure_mode TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (entry_id, ordinal)
);

CREATE TABLE corpus_signal_inputs (
    entry_id TEXT NOT NULL REFERENCES corpus_entries(id) ON DELETE CASCADE,
    signal_input TEXT NOT NULL,
    PRIMARY KEY (entry_id, signal_input)
);

CREATE TABLE corpus_papers (
    entry_id TEXT NOT NULL REFERENCES corpus_entries(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    authors TEXT NOT NULL,
    year INTEGER,
    venue TEXT,
    url TEXT,
    PRIMARY KEY (entry_id, title)
);

CREATE TABLE corpus_related_strategies (
    concept_id TEXT NOT NULL REFERENCES corpus_entries(id) ON DELETE CASCADE,
    strategy_id TEXT NOT NULL REFERENCES corpus_entries(id) ON DELETE CASCADE,
    PRIMARY KEY (concept_id, strategy_id)
);
CREATE INDEX idx_related_strategy ON corpus_related_strategies(strategy_id);

CREATE VIRTUAL TABLE corpus_entries_fts USING fts5(
    id UNINDEXED,
    entry_type UNINDEXED,
    canonical_name,
    aliases,
    one_line,
    definition,
    body_thesis,
    body_signal_intuition,
    when_to_consider,
    when_to_avoid,
    engine_fit,
    approximation_notes,
    when_it_matters,
    tokenize='porter unicode61'
);
"""


def build(db_path: Path, allow_empty: bool = False) -> tuple[int, int]:
    paths = iter_entry_paths()
    if not paths and not allow_empty:
        raise ValueError(
            "no corpus markdown files found under corpus/ (excluding _drafts/). "
            "Pass --allow-empty to build an empty corpus.db during bootstrap."
        )

    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_sql())
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    seen: set[str] = set()
    strategy_ids: set[str] = set()
    concept_links: list[tuple[str, str, Path]] = []

    for path in paths:
        frontmatter, body, sections = parse_markdown(path)
        validate_entry(path, frontmatter, sections)
        entry_id = str(frontmatter["id"])
        if entry_id in seen:
            raise ValueError(f"{path}: duplicate entry id {entry_id}")
        seen.add(entry_id)
        if frontmatter["entry_type"] == "strategy":
            strategy_ids.add(entry_id)

        rel_path = str(path.relative_to(ROOT))
        content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        conn.execute(
            """
            INSERT INTO corpus_entries (
                id, entry_type, canonical_name, category, one_line, body_markdown,
                body_thesis, body_signal_intuition, body_construction, body_notes,
                typical_holding_period, when_to_consider, when_to_avoid, engine_fit,
                approximation_notes, definition, when_it_matters, source_file_path,
                content_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                frontmatter["entry_type"],
                frontmatter["canonical_name"],
                frontmatter["category"],
                frontmatter.get("one_line"),
                body,
                sections.get("thesis"),
                sections.get("signal intuition"),
                sections.get("construction sketch"),
                sections.get("notes"),
                frontmatter.get("typical_holding_period"),
                frontmatter.get("when_to_consider"),
                frontmatter.get("when_to_avoid"),
                frontmatter.get("engine_fit"),
                frontmatter.get("approximation_notes"),
                frontmatter.get("definition"),
                frontmatter.get("when_it_matters"),
                rel_path,
                content_hash,
                now,
            ),
        )

        aliases = frontmatter.get("aliases") or []
        for alias in aliases:
            conn.execute(
                "INSERT OR IGNORE INTO corpus_aliases (entry_id, alias) VALUES (?, ?)",
                (entry_id, str(alias)),
            )

        for asset_class in frontmatter.get("asset_classes") or []:
            conn.execute(
                "INSERT OR IGNORE INTO corpus_asset_classes (entry_id, asset_class) VALUES (?, ?)",
                (entry_id, str(asset_class)),
            )

        for ordinal, failure_mode in enumerate(frontmatter.get("known_failure_modes") or []):
            conn.execute(
                """
                INSERT INTO corpus_failure_modes (entry_id, failure_mode, ordinal)
                VALUES (?, ?, ?)
                """,
                (entry_id, str(failure_mode), ordinal),
            )

        for signal_input in frontmatter.get("signal_inputs") or []:
            conn.execute(
                "INSERT OR IGNORE INTO corpus_signal_inputs (entry_id, signal_input) VALUES (?, ?)",
                (entry_id, str(signal_input)),
            )

        papers = frontmatter.get("seminal_papers") or frontmatter.get("references") or []
        for paper in papers:
            if not isinstance(paper, dict) or not paper.get("title"):
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO corpus_papers (entry_id, title, authors, year, venue, url)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    str(paper["title"]),
                    json.dumps(paper.get("authors") or []),
                    paper.get("year"),
                    paper.get("venue"),
                    paper.get("url"),
                ),
            )

        for strategy_id in frontmatter.get("related_strategies") or []:
            concept_links.append((entry_id, str(strategy_id), path))

        aliases_text = " ".join(str(alias) for alias in aliases)
        conn.execute(
            """
            INSERT INTO corpus_entries_fts (
                id, entry_type, canonical_name, aliases, one_line, definition,
                body_thesis, body_signal_intuition, when_to_consider, when_to_avoid,
                engine_fit, approximation_notes, when_it_matters
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_id,
                frontmatter["entry_type"],
                frontmatter["canonical_name"],
                aliases_text,
                frontmatter.get("one_line"),
                frontmatter.get("definition"),
                sections.get("thesis"),
                sections.get("signal intuition"),
                frontmatter.get("when_to_consider"),
                frontmatter.get("when_to_avoid"),
                frontmatter.get("engine_fit"),
                frontmatter.get("approximation_notes"),
                frontmatter.get("when_it_matters"),
            ),
        )

    for concept_id, strategy_id, path in concept_links:
        if strategy_id not in strategy_ids:
            raise ValueError(f"{path}: related strategy does not exist: {strategy_id}")
        conn.execute(
            """
            INSERT INTO corpus_related_strategies (concept_id, strategy_id)
            VALUES (?, ?)
            """,
            (concept_id, strategy_id),
        )

    conn.commit()
    strategy_count = conn.execute(
        "SELECT COUNT(*) FROM corpus_entries WHERE entry_type='strategy'"
    ).fetchone()[0]
    concept_count = conn.execute(
        "SELECT COUNT(*) FROM corpus_entries WHERE entry_type='concept'"
    ).fetchone()[0]
    conn.close()
    return int(strategy_count), int(concept_count)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Permit an empty corpus.db when no reviewed entries exist yet (bootstrap).",
    )
    args = parser.parse_args()
    strategy_count, concept_count = build(args.db_path, args.allow_empty)
    print(
        f"built {_display_path(args.db_path)} with "
        f"{strategy_count} strategy entries and {concept_count} concept entries"
    )


def _display_path(path: Path) -> str:
    """Render path relative to ROOT when possible, else absolute."""
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


if __name__ == "__main__":
    main()
