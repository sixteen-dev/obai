"""The /api/settings surface: hub model + reasoning effort over HTTP.

These settings are user-owned: the endpoint writes the file every hub reads on
startup and hot-applies it to the running one, an exported ORCHESTRATOR_*
variable outranks it, and a hand-broken file must fail loudly rather than
quietly reverting the user to another billing tier. Every one of those is a way
the endpoint could lie to the settings modal, so each gets a test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from clients.web.server import _hub_settings_payload, create_app
from core_agents.config import get_config, reset_config
from core_agents.hub_settings import (
    HUB_MODELS,
    HUB_REASONING_EFFORTS,
    HubSettings,
    HubSettingsStore,
)

_ENV_VARS = (
    "ORCHESTRATOR_MODEL",
    "orchestrator_model",
    "ORCHESTRATOR_REASONING_EFFORT",
    "orchestrator_reasoning_effort",
)


@asynccontextmanager
async def _noop_lifespan(app: Any) -> AsyncIterator[None]:
    """Skip the 30-60s hub init; /api/settings never touches app.state."""
    app.state.ready = False
    yield


@pytest.fixture
def settings_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Isolate the settings file and the config singleton from the real home.

    ``Path.home()`` follows ``$HOME``, so pointing it at ``tmp_path`` covers
    the store AgentConfig builds internally as well as the one injected into
    the app. Both ORCHESTRATOR_* casings are cleared because AgentConfig reads
    the environment case-insensitively and a developer export would otherwise
    decide the assertions.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    reset_config()
    yield tmp_path / "settings.json"
    reset_config()


def _client(path: Path) -> TestClient:
    app = create_app(hub_settings_store=HubSettingsStore(path=path))
    app.router.lifespan_context = _noop_lifespan
    return TestClient(app)


class TestGetSettings:
    """GET /api/settings reports saved, running, choices, and env overrides."""

    def test_defaults_when_no_file_exists(self, settings_path: Path) -> None:
        with _client(settings_path) as client:
            body = client.get("/api/settings").json()

        assert body["saved"] == {"hub_model": "gpt-5.6-terra", "hub_reasoning_effort": "max"}
        assert body["restart_required"] is False
        assert not settings_path.exists()

    def test_choices_come_from_the_validated_literals(self, settings_path: Path) -> None:
        with _client(settings_path) as client:
            body = client.get("/api/settings").json()

        assert body["choices"]["hub_model"] == list(HUB_MODELS)
        assert body["choices"]["hub_reasoning_effort"] == list(HUB_REASONING_EFFORTS)

    def test_reports_saved_values(self, settings_path: Path) -> None:
        HubSettingsStore(path=settings_path).save(
            HubSettings(hub_model="gpt-5.6-terra", hub_reasoning_effort="xhigh")
        )

        with _client(settings_path) as client:
            body = client.get("/api/settings").json()

        assert body["saved"] == {"hub_model": "gpt-5.6-terra", "hub_reasoning_effort": "xhigh"}

    def test_running_values_are_reported_separately(self, settings_path: Path) -> None:
        """Saving does not restart the hub, so the two can legitimately differ."""
        with _client(settings_path) as client:
            # The first GET materializes the config singleton at the defaults,
            # standing in for a hub that booted before the user changed anything.
            client.get("/api/settings")
            client.patch("/api/settings", json={"hub_model": "gpt-5.6-sol"})
            body = client.get("/api/settings").json()

        assert body["saved"]["hub_model"] == "gpt-5.6-sol"
        assert body["running"]["hub_model"] == "gpt-5.6-terra"
        assert body["restart_required"] is True

    def test_corrupt_file_error_still_carries_the_choices(self, settings_path: Path) -> None:
        """The modal repairs a broken file by saving a complete pair of values.

        It cannot build that request with two empty dropdowns, so the error
        body has to carry the choices too.
        """
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{not json at all", encoding="utf-8")

        with _client(settings_path) as client:
            res = client.get("/api/settings")

        assert res.status_code == 500
        assert res.json()["choices"] == {
            "hub_model": list(HUB_MODELS),
            "hub_reasoning_effort": list(HUB_REASONING_EFFORTS),
        }

    def test_corrupt_file_returns_clean_json_error(self, settings_path: Path) -> None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{not json at all", encoding="utf-8")

        with _client(settings_path) as client:
            res = client.get("/api/settings")

        assert res.status_code == 500
        assert str(settings_path) in res.json()["error"]

    def test_invalid_saved_value_returns_clean_json_error(self, settings_path: Path) -> None:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text('{"hub_model": "gpt-4-turbo"}', encoding="utf-8")

        with _client(settings_path) as client:
            res = client.get("/api/settings")

        assert res.status_code == 500
        assert "hub_model" in res.json()["error"]


class TestEnvOverrides:
    """An exported ORCHESTRATOR_* beats the file, so the API must say so."""

    def test_override_is_reported_and_suppresses_restart_flag(
        self, settings_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ORCHESTRATOR_MODEL", "gpt-5.6-sol")
        reset_config()

        with _client(settings_path) as client:
            body = client.get("/api/settings").json()

        assert body["saved"]["hub_model"] == "gpt-5.6-terra"
        assert body["running"]["hub_model"] == "gpt-5.6-sol"
        assert body["env_overrides"]["hub_model"] == "gpt-5.6-sol"
        # Restarting would not close this gap — only unsetting the export does.
        assert body["restart_required"] is False
        assert body["env_vars"]["hub_model"] == "ORCHESTRATOR_MODEL"

    def test_lowercase_export_is_detected(
        self, settings_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AgentConfig reads env case-insensitively; the report must match."""
        monkeypatch.setenv("orchestrator_reasoning_effort", "max")
        reset_config()

        with _client(settings_path) as client:
            body = client.get("/api/settings").json()

        assert body["env_overrides"]["hub_reasoning_effort"] == "max"

    def test_no_overrides_reported_when_env_is_clean(self, settings_path: Path) -> None:
        with _client(settings_path) as client:
            body = client.get("/api/settings").json()

        assert body["env_overrides"] == {"hub_model": None, "hub_reasoning_effort": None}


class TestPatchSettings:
    """PATCH /api/settings validates through HubSettings before writing."""

    def test_saves_and_signals_restart(self, settings_path: Path) -> None:
        with _client(settings_path) as client:
            client.get("/api/settings")
            res = client.patch(
                "/api/settings",
                json={"hub_model": "gpt-5.6-sol", "hub_reasoning_effort": "high"},
            )

        assert res.status_code == 200
        body = res.json()
        assert body["saved"] == {"hub_model": "gpt-5.6-sol", "hub_reasoning_effort": "high"}
        assert body["restart_required"] is True
        assert HubSettingsStore(path=settings_path).load() == HubSettings(
            hub_model="gpt-5.6-sol", hub_reasoning_effort="high"
        )

    def test_partial_patch_keeps_the_other_field(self, settings_path: Path) -> None:
        HubSettingsStore(path=settings_path).save(
            HubSettings(hub_model="gpt-5.6-terra", hub_reasoning_effort="xhigh")
        )

        with _client(settings_path) as client:
            body = client.patch("/api/settings", json={"hub_model": "gpt-5.6-sol"}).json()

        assert body["saved"] == {"hub_model": "gpt-5.6-sol", "hub_reasoning_effort": "xhigh"}

    def test_saving_the_running_values_requires_no_restart(self, settings_path: Path) -> None:
        with _client(settings_path) as client:
            client.get("/api/settings")
            body = client.patch("/api/settings", json={"hub_model": "gpt-5.6-terra"}).json()

        assert body["restart_required"] is False

    def test_unsupported_model_rejected_with_400(self, settings_path: Path) -> None:
        with _client(settings_path) as client:
            res = client.patch("/api/settings", json={"hub_model": "gpt-4-turbo"})

        assert res.status_code == 400
        body = res.json()
        assert "hub_model" in body["error"]
        assert body["choices"]["hub_model"] == list(HUB_MODELS)
        assert not settings_path.exists()

    def test_unsupported_effort_rejected_with_400(self, settings_path: Path) -> None:
        with _client(settings_path) as client:
            res = client.patch("/api/settings", json={"hub_reasoning_effort": "minimal"})

        assert res.status_code == 400
        assert "hub_reasoning_effort" in res.json()["error"]

    def test_unknown_key_rejected_with_400(self, settings_path: Path) -> None:
        """extra=forbid: a typo must fail, not silently save nothing."""
        with _client(settings_path) as client:
            res = client.patch("/api/settings", json={"hub_modle": "gpt-5.6-sol"})

        assert res.status_code == 400
        assert "hub_modle" in res.json()["error"]
        assert not settings_path.exists()

    def test_wrong_type_rejected_with_400_not_500(self, settings_path: Path) -> None:
        with _client(settings_path) as client:
            res = client.patch("/api/settings", json={"hub_model": 7})

        assert res.status_code == 400

    def test_non_object_body_rejected_cleanly(self, settings_path: Path) -> None:
        """A malformed body is FastAPI's 422 JSON, never an unhandled 500."""
        with _client(settings_path) as client:
            res = client.patch("/api/settings", json=["gpt-5.6-terra"])

        assert res.status_code == 422
        assert not settings_path.exists()

    def test_complete_patch_repairs_a_corrupt_file(self, settings_path: Path) -> None:
        """A body naming every field needs no base, so it may overwrite.

        This is the modal's repair path — it always submits both fields.
        """
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text("{not json at all", encoding="utf-8")

        with _client(settings_path) as client:
            res = client.patch(
                "/api/settings",
                json={"hub_model": "gpt-5.6-terra", "hub_reasoning_effort": "high"},
            )

        assert res.status_code == 200
        assert HubSettingsStore(path=settings_path).load() == HubSettings(
            hub_model="gpt-5.6-terra", hub_reasoning_effort="high"
        )

    def test_partial_patch_over_a_corrupt_file_refuses(self, settings_path: Path) -> None:
        """Merging onto an unreadable base would silently reset the other field.

        Falling back to defaults here would move a user who had chosen
        ``max`` back to ``medium`` — a different billing tier — while
        returning 200 and reporting success.
        """
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text('{"hub_reasoning_effort": "max",}', encoding="utf-8")

        with _client(settings_path) as client:
            res = client.patch("/api/settings", json={"hub_model": "gpt-5.6-terra"})

        assert res.status_code == 409
        assert str(settings_path) in res.json()["error"]
        # The broken file is left exactly as it was, not half-rewritten.
        assert settings_path.read_text(encoding="utf-8") == '{"hub_reasoning_effort": "max",}'


class TestOriginGuardCoversSettings:
    """The write path inherits OriginGuardMiddleware; verify, do not assume."""

    def test_cross_origin_patch_rejected(self, settings_path: Path) -> None:
        with _client(settings_path) as client:
            res = client.patch(
                "/api/settings",
                json={"hub_model": "gpt-5.6-terra"},
                headers={"origin": "https://evil.com"},
            )

        assert res.status_code == 403
        assert not settings_path.exists()

    def test_local_origin_patch_allowed(self, settings_path: Path) -> None:
        with _client(settings_path) as client:
            res = client.patch(
                "/api/settings",
                json={"hub_model": "gpt-5.6-terra"},
                headers={"origin": "http://127.0.0.1:8090"},
            )

        assert res.status_code == 200


class _FakeHub:
    """Minimal stand-in for CentralHubAgent's live-retune surface."""

    def __init__(self, config: Any) -> None:
        self.config = config
        self.applied: list[tuple[str, str]] = []

    def apply_hub_settings(self, *, model: str, reasoning_effort: str) -> None:
        self.applied.append((model, reasoning_effort))
        self.config.orchestrator_model = model
        self.config.orchestrator_reasoning_effort = reasoning_effort


def _ready_client(path: Path) -> tuple[TestClient, _FakeHub]:
    """Build a client whose app has a ready hub wired behind a real bridge.

    Uses the production ``HubBridge`` so the lock-and-delegate path under
    test is the one that ships; only the hub itself is faked.
    """
    from clients.web.hub_bridge import HubBridge

    hub = _FakeHub(get_config())
    app = create_app(hub_settings_store=HubSettingsStore(path=path))

    @asynccontextmanager
    async def _ready_lifespan(a: Any) -> AsyncIterator[None]:
        a.state.bridge = HubBridge(hub)  # type: ignore[arg-type]
        a.state.ready = True
        yield

    app.router.lifespan_context = _ready_lifespan
    return TestClient(app), hub


class TestLiveApply:
    """A saved change retunes the running hub — no terminal restart.

    Asking someone to go back to a terminal after clicking Save in a web UI
    is the failure this path exists to prevent, so the assertions are about
    the running hub, not just the file.
    """

    def test_patch_applies_to_the_running_hub(self, settings_path: Path) -> None:
        client, hub = _ready_client(settings_path)
        with client:
            body = client.patch(
                "/api/settings",
                json={"hub_model": "gpt-5.6-terra", "hub_reasoning_effort": "xhigh"},
            ).json()

        assert hub.applied == [("gpt-5.6-terra", "xhigh")]
        assert body["running"] == body["saved"]
        assert body["restart_required"] is False

    def test_env_pinned_fields_are_not_applied(
        self, settings_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env outranks the file; hot-applying would break that precedence.

        The pinned value is deliberately the non-default one, and the saved
        value the default: if the code fell back to the shipped default
        instead of the env-resolved running value, the two would be
        indistinguishable and this test would pass over the bug.
        """
        monkeypatch.setenv("ORCHESTRATOR_MODEL", "gpt-5.6-terra")
        reset_config()
        client, hub = _ready_client(settings_path)
        with client:
            client.patch(
                "/api/settings",
                json={"hub_model": "gpt-5.6-sol", "hub_reasoning_effort": "high"},
            )

        # Effort moved; the env-pinned model kept the exported value.
        assert hub.applied == [("gpt-5.6-terra", "high")]

    def test_nothing_is_applied_before_the_hub_is_ready(self, settings_path: Path) -> None:
        """Mid-init there is no agent to retune; the file is enough."""
        with _client(settings_path) as client:
            response = client.patch("/api/settings", json={"hub_model": "gpt-5.6-terra"})

        assert response.status_code == 200
        assert HubSettingsStore(path=settings_path).load().hub_model == "gpt-5.6-terra"


class TestPendingApplyReporting:
    """A change queued behind a live query must not be reported as a restart.

    ``restart_required`` drives the modal's "restart OBaI" line and the
    sidebar's pending badge. Telling someone to restart when the change is
    already queued to apply by itself sends them to a terminal for nothing.
    """

    def test_pending_apply_suppresses_the_restart_flag(self, settings_path: Path) -> None:
        # Materialize the config singleton before the save, standing in for a
        # hub that booted on the defaults.
        get_config()
        store = HubSettingsStore(path=settings_path)
        store.save(HubSettings(hub_model="gpt-5.6-terra", hub_reasoning_effort="xhigh"))

        # Saved differs from running (the config singleton is at its defaults),
        # which is exactly the state that otherwise reads as "restart needed".
        queued = _hub_settings_payload(store, pending_apply=True)
        stuck = _hub_settings_payload(store, pending_apply=False)

        assert queued["saved"] != queued["running"]
        assert queued["pending_apply"] is True
        assert queued["restart_required"] is False
        assert stuck["restart_required"] is True
