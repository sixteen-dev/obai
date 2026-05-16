"""Tests for the three corpus MCP tools."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import Settings


def _seed_corpus_db(db_path: Path) -> None:
    """Build a minimal corpus.db with 2 strategies + 1 concept for tests."""
    schema = """
    CREATE TABLE corpus_entries (
        id TEXT PRIMARY KEY,
        entry_type TEXT NOT NULL,
        canonical_name TEXT NOT NULL,
        category TEXT NOT NULL,
        one_line TEXT,
        body_thesis TEXT,
        body_signal_intuition TEXT,
        body_construction TEXT,
        body_notes TEXT,
        typical_holding_period TEXT,
        when_to_consider TEXT,
        when_to_avoid TEXT,
        engine_fit TEXT,
        approximation_notes TEXT,
        definition TEXT,
        when_it_matters TEXT,
        source_file_path TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE corpus_aliases (
        entry_id TEXT NOT NULL, alias TEXT NOT NULL,
        PRIMARY KEY (entry_id, alias)
    );
    CREATE TABLE corpus_asset_classes (
        entry_id TEXT NOT NULL, asset_class TEXT NOT NULL,
        PRIMARY KEY (entry_id, asset_class)
    );
    CREATE TABLE corpus_failure_modes (
        entry_id TEXT NOT NULL, failure_mode TEXT NOT NULL, ordinal INTEGER NOT NULL,
        PRIMARY KEY (entry_id, ordinal)
    );
    CREATE TABLE corpus_signal_inputs (
        entry_id TEXT NOT NULL, signal_input TEXT NOT NULL,
        PRIMARY KEY (entry_id, signal_input)
    );
    CREATE TABLE corpus_papers (
        entry_id TEXT NOT NULL, title TEXT NOT NULL, authors TEXT NOT NULL,
        year INTEGER, venue TEXT, url TEXT,
        PRIMARY KEY (entry_id, title)
    );
    CREATE TABLE corpus_related_strategies (
        concept_id TEXT NOT NULL, strategy_id TEXT NOT NULL,
        PRIMARY KEY (concept_id, strategy_id)
    );
    CREATE VIRTUAL TABLE corpus_entries_fts USING fts5(
        id UNINDEXED, entry_type UNINDEXED,
        canonical_name, aliases, one_line, definition,
        body_thesis, body_signal_intuition,
        when_to_consider, when_to_avoid, engine_fit, approximation_notes, when_it_matters,
        tokenize='porter unicode61'
    );
    """
    conn = sqlite3.connect(db_path)
    conn.executescript(schema)

    # Strategy 1: a momentum entry
    conn.execute(
        "INSERT INTO corpus_entries VALUES "
        "('test_momentum_12_1', 'strategy', 'Cross-Sectional Momentum (12-1)', 'momentum', "
        "'Rank stocks by 12-month return excluding most recent month.', "
        "'Investor underreaction creates persistent return continuation.', "
        "'Compute 12-1 return per stock, long top decile, short bottom.', "
        "'rank stocks; long top decile, short bottom', "
        "'Replication evidence widely documented.', "
        "'monthly', "
        "'Broad equity universes with meaningful cross-section.', "
        "'Low-dispersion regimes, narrow universes.', "
        "'approximate', "
        "'Approximate via fixed universe; engine has no native ranking.', "
        "NULL, NULL, "
        "'test/path.md', 'hash1', '2026-01-01T00:00:00Z')"
    )
    conn.execute("INSERT INTO corpus_aliases VALUES ('test_momentum_12_1', '12-1 momentum')")
    conn.execute("INSERT INTO corpus_asset_classes VALUES ('test_momentum_12_1', 'equities')")
    conn.execute(
        "INSERT INTO corpus_failure_modes VALUES ('test_momentum_12_1', 'Momentum crashes', 1)"
    )
    conn.execute(
        "INSERT INTO corpus_failure_modes VALUES ('test_momentum_12_1', 'Low-dispersion drift', 2)"
    )
    conn.execute(
        "INSERT INTO corpus_signal_inputs VALUES ('test_momentum_12_1', 'daily close prices')"
    )
    conn.execute(
        "INSERT INTO corpus_papers VALUES "
        "('test_momentum_12_1', 'Returns to Buying Winners and Selling Losers', "
        "'[\"Jegadeesh\",\"Titman\"]', 1993, 'Journal of Finance', NULL)"
    )

    # Strategy 2: a vol entry
    conn.execute(
        "INSERT INTO corpus_entries VALUES "
        "('test_vix_basis', 'strategy', 'VIX Futures Basis Trade', 'vol', "
        "'Short front VIX futures when basis is positive; long when negative.', "
        "'VIX futures do not satisfy rational expectations.', "
        "'Compute basis; trade direction of expected convergence.', "
        "NULL, "
        "'5-day hold standard.', "
        "'weekly', "
        "'Moderate VIX levels.', 'Macro event windows.', "
        "'approximate', "
        "'Use VXX proxy short.', "
        "NULL, NULL, "
        "'test/path2.md', 'hash2', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO corpus_failure_modes VALUES ('test_vix_basis', 'VIX regime shift', 1)"
    )

    # Concept 1: contango
    conn.execute(
        "INSERT INTO corpus_entries VALUES "
        "('test_contango', 'concept', 'Contango', 'regimes', "
        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
        "'Futures curve where deferred contracts trade above near-dated.', "
        "'VIX futures (chronic contango); long positions bleed carry.', "
        "'test/contango.md', 'hash3', '2026-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO corpus_related_strategies VALUES ('test_contango', 'test_vix_basis')"
    )

    # Populate FTS5 (denormalized as build_index.py would)
    conn.execute(
        "INSERT INTO corpus_entries_fts (id, entry_type, canonical_name, aliases, one_line, "
        "definition, body_thesis, body_signal_intuition, when_to_consider, when_to_avoid, "
        "engine_fit, approximation_notes, when_it_matters) VALUES "
        "('test_momentum_12_1', 'strategy', 'Cross-Sectional Momentum (12-1)', '12-1 momentum', "
        "'Rank stocks by 12-month return excluding most recent month.', '', "
        "'Investor underreaction creates persistent return continuation.', "
        "'Compute 12-1 return per stock, long top decile, short bottom.', "
        "'Broad equity universes with meaningful cross-section.', "
        "'Low-dispersion regimes, narrow universes.', "
        "'approximate', 'Approximate via fixed universe.', '')"
    )
    conn.execute(
        "INSERT INTO corpus_entries_fts (id, entry_type, canonical_name, aliases, one_line, "
        "definition, body_thesis, body_signal_intuition, when_to_consider, when_to_avoid, "
        "engine_fit, approximation_notes, when_it_matters) VALUES "
        "('test_vix_basis', 'strategy', 'VIX Futures Basis Trade', '', "
        "'Short front VIX futures when basis is positive; long when negative.', '', "
        "'VIX futures do not satisfy rational expectations.', '', "
        "'Moderate VIX levels.', 'Macro event windows.', "
        "'approximate', 'Use VXX proxy short.', '')"
    )
    conn.execute(
        "INSERT INTO corpus_entries_fts (id, entry_type, canonical_name, aliases, one_line, "
        "definition, body_thesis, body_signal_intuition, when_to_consider, when_to_avoid, "
        "engine_fit, approximation_notes, when_it_matters) VALUES "
        "('test_contango', 'concept', 'Contango', '', '', "
        "'Futures curve where deferred contracts trade above near-dated.', '', '', '', "
        "'', '', '', 'VIX futures (chronic contango); long positions bleed carry.')"
    )

    conn.commit()
    conn.close()


@pytest.fixture
def corpus_settings(tmp_path: Path) -> Settings:
    """Settings pointing at a freshly-seeded test corpus.db."""
    db = tmp_path / "test_corpus.db"
    _seed_corpus_db(db)
    return Settings(corpus_db_path=db, port=18012)


class TestSearchCorpus:
    def test_returns_results_for_keyword(self, corpus_settings: Settings) -> None:
        from src.tools.corpus import search_corpus

        with patch("src.tools.corpus.get_settings", return_value=corpus_settings):
            payload = search_corpus(query="momentum")

        assert payload["count"] >= 1
        ids = [r["id"] for r in payload["results"]]
        assert "test_momentum_12_1" in ids

    def test_entry_type_filter(self, corpus_settings: Settings) -> None:
        from src.tools.corpus import search_corpus

        with patch("src.tools.corpus.get_settings", return_value=corpus_settings):
            payload = search_corpus(entry_type="concept", limit=10)

        assert all(r["entry_type"] == "concept" for r in payload["results"])
        assert any(r["id"] == "test_contango" for r in payload["results"])

    def test_category_filter(self, corpus_settings: Settings) -> None:
        from src.tools.corpus import search_corpus

        with patch("src.tools.corpus.get_settings", return_value=corpus_settings):
            payload = search_corpus(category="vol", limit=10)

        ids = [r["id"] for r in payload["results"]]
        assert ids == ["test_vix_basis"]

    def test_asset_class_filter(self, corpus_settings: Settings) -> None:
        from src.tools.corpus import search_corpus

        with patch("src.tools.corpus.get_settings", return_value=corpus_settings):
            payload = search_corpus(asset_class="equities", limit=10)

        assert {r["id"] for r in payload["results"]} == {"test_momentum_12_1"}

    def test_empty_query_returns_sorted(self, corpus_settings: Settings) -> None:
        from src.tools.corpus import search_corpus

        with patch("src.tools.corpus.get_settings", return_value=corpus_settings):
            payload = search_corpus(limit=10)

        # canonical_name ASC: "Contango" < "Cross-Sectional Momentum..." < "VIX Futures..."
        names = [r["canonical_name"] for r in payload["results"]]
        assert names == sorted(names)


class TestGetCorpusEntry:
    def test_returns_full_strategy(self, corpus_settings: Settings) -> None:
        from src.tools.corpus import get_corpus_entry

        with patch("src.tools.corpus.get_settings", return_value=corpus_settings):
            payload = get_corpus_entry("test_momentum_12_1")

        assert payload["entry_type"] == "strategy"
        assert payload["canonical_name"] == "Cross-Sectional Momentum (12-1)"
        assert "Thesis" in payload["body"]
        assert "Signal intuition" in payload["body"]
        assert "12-1 momentum" in payload["aliases"]
        assert payload["engine_fit"] == "approximate"
        assert len(payload["references"]) == 1
        assert payload["references"][0]["authors"] == ["Jegadeesh", "Titman"]

    def test_returns_full_concept(self, corpus_settings: Settings) -> None:
        from src.tools.corpus import get_corpus_entry

        with patch("src.tools.corpus.get_settings", return_value=corpus_settings):
            payload = get_corpus_entry("test_contango")

        assert payload["entry_type"] == "concept"
        assert payload["related_strategies"] == ["test_vix_basis"]
        assert "deferred contracts" in payload["definition"]

    def test_unknown_id_returns_error(self, corpus_settings: Settings) -> None:
        from src.tools.corpus import get_corpus_entry

        with patch("src.tools.corpus.get_settings", return_value=corpus_settings):
            payload = get_corpus_entry("nonexistent_id")

        assert "error" in payload
        assert payload["entry_id"] == "nonexistent_id"


class TestListCategories:
    def test_groups_by_entry_type(self, corpus_settings: Settings) -> None:
        from src.tools.corpus import list_categories

        with patch("src.tools.corpus.get_settings", return_value=corpus_settings):
            payload = list_categories()

        strat_cats = {c["category"]: c["count"] for c in payload["strategies"]}
        concept_cats = {c["category"]: c["count"] for c in payload["concepts"]}
        assert strat_cats == {"momentum": 1, "vol": 1}
        assert concept_cats == {"regimes": 1}


class TestErrorPaths:
    def test_db_missing_returns_error(self, tmp_path: Path) -> None:
        from src.tools.corpus import list_categories

        broken = Settings(corpus_db_path=tmp_path / "missing.db", port=18013)
        with patch("src.tools.corpus.get_settings", return_value=broken):
            payload = list_categories()

        assert "error" in payload
        assert "not found" in payload["error"]
