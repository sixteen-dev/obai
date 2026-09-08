"""Unit tests for Opik tracing initialization.

Covers the environment defaults init_opik installs before handing control to
the Opik SDK, which decide where traces go and what leaves the machine.
"""

from __future__ import annotations

import os
import threading
import urllib.request
from typing import Any

import pytest

from core_agents import config as config_module
from core_agents.tracing import opik_init


@pytest.fixture(autouse=True)
def isolated_opik_init(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run init_opik against stubs so no server or Comet endpoint is touched.

    Clears the module's one-shot guard, stubs the health probe, the SDK's
    configure call, the Agents-SDK processor hook and the prompt-sync thread,
    and drops both env vars under test so each case starts from an unset state.
    """
    monkeypatch.setattr(opik_init, "_opik_initialized", False)
    monkeypatch.setattr(config_module, "_config", None, raising=False)
    monkeypatch.delenv("OPIK_ANALYTICS_ENABLE", raising=False)
    monkeypatch.delenv("OPIK_URL_OVERRIDE", raising=False)

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: None)
    monkeypatch.setattr(opik_init, "set_trace_processors", lambda _processors: None)
    # init_opik imports threading inside the function, so patch the module.
    monkeypatch.setattr(threading, "Thread", _NoopThread)

    import opik

    monkeypatch.setattr(opik, "configure", lambda **_kwargs: None)
    monkeypatch.setattr(
        "opik.integrations.openai.agents.OpikTracingProcessor",
        lambda *a, **k: object(),
    )


class _NoopThread:
    """Stands in for threading.Thread so the prompt sync never runs."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def start(self) -> None:
        """Do nothing — the real thread would call out to the Opik server."""


class TestAnalyticsDefault:
    """init_opik must keep a self-hosted deployment self-contained."""

    def test_analytics_are_disabled_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Opik 2.2.41 turned usage analytics on by default; we turn them back off.

        Left at the SDK default, a background thread posts feature-usage events
        and the workspace name to stats.comet.com — egress a self-hosted install
        never asked for.
        """
        assert opik_init.init_opik() is True
        assert os.environ["OPIK_ANALYTICS_ENABLE"] == "false"

    def test_an_explicit_analytics_setting_wins(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The default is a setdefault, so an operator can still opt in."""
        monkeypatch.setenv("OPIK_ANALYTICS_ENABLE", "true")

        assert opik_init.init_opik() is True
        assert os.environ["OPIK_ANALYTICS_ENABLE"] == "true"
