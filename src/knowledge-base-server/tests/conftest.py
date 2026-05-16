"""Shared fixtures for knowledge-base-server tests."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config import Settings


@pytest.fixture
def mock_settings(tmp_path: Path) -> Settings:
    """A Settings instance pointing at a temp corpus.db path that doesn't exist yet."""
    s = Settings(
        corpus_db_path=tmp_path / "corpus.db",
        port=18011,
        log_level="WARNING",
    )
    return s


@pytest.fixture
def mock_request() -> MagicMock:
    """Bare-minimum mock for Starlette request."""
    return MagicMock()
