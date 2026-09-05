"""Shared pytest fixtures for the OBaI agent service.

Isolates tests from the developer's own machine. ``AgentConfig`` resolves the
hub model and reasoning effort through ``~/.obai/settings.json``, so without
this every test that constructs a config would read whatever the person
running it last picked in the web UI — and fail if that value happens to be
one their environment cannot serve.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core_agents import hub_settings


@pytest.fixture(autouse=True)
def isolate_hub_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the hub settings file at an empty temp dir for every test.

    An absent file means "use the shipped defaults", which is what tests
    should assert against. Tests that need a specific stored value construct
    their own ``HubSettingsStore(path=...)``.

    Args:
        tmp_path: Per-test temporary directory.
        monkeypatch: Fixture used to redirect the settings path.
    """
    monkeypatch.setattr(
        hub_settings,
        "default_hub_settings_path",
        lambda: tmp_path / "settings.json",
    )
