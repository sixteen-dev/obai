from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))


@pytest.fixture(autouse=True)
def isolate_obai_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own ~/.obai out of these tests.

    Several tests build a real ``AgentConfig`` or hash the ``~/.obai`` files
    the gate binds. Both would otherwise read the machine's live settings,
    credentials, and preferences — so a value the person running the suite
    picked in the web UI could fail tests that have nothing to do with it.

    Args:
        tmp_path: Per-test temporary directory used as ``$HOME``.
        monkeypatch: Fixture used to redirect ``$HOME``.
    """
    home = tmp_path / "home"
    (home / ".obai").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
