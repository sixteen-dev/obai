"""Tests for user-settable hub model and reasoning effort."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from core_agents import hub_settings as hub_settings_module
from core_agents.hub_settings import (
    HUB_MODELS,
    HUB_REASONING_EFFORTS,
    HubSettings,
    HubSettingsStore,
)

# ---------------------------------------------------------------------------
# HubSettings model
# ---------------------------------------------------------------------------


class TestHubSettings:
    """Test HubSettings Pydantic model."""

    def test_defaults_match_shipped_config(self) -> None:
        """Defaults are the values the hub ships with today."""
        settings = HubSettings()
        assert settings.hub_model == "gpt-5.6-sol"
        assert settings.hub_reasoning_effort == "medium"

    def test_choice_tuples_match_the_literals(self) -> None:
        """The UI/CLI choice lists are the same set the model validates."""
        assert HUB_MODELS == ("gpt-5.6-sol", "gpt-5.6-terra")
        assert HUB_REASONING_EFFORTS == ("medium", "high", "xhigh", "max")

    def test_every_offered_effort_is_accepted_by_the_installed_sdk(self) -> None:
        """The SDK builds the request; offering a tier it rejects is a crash.

        ``max`` landed in openai 2.45.0. Offering it against an older SDK
        fails inside ``Reasoning(...)`` during hub construction — an
        unreadable traceback at ASGI startup, not a config error.
        """
        from openai.types.shared.reasoning_effort import ReasoningEffort

        unsupported = [e for e in HUB_REASONING_EFFORTS if e not in str(ReasoningEffort)]
        assert not unsupported, (
            f"offered but rejected by the installed openai SDK: {unsupported}. "
            f"Raise the openai floor in src/obai/pyproject.toml."
        )

    def test_every_choice_is_accepted(self) -> None:
        """Nothing offered in the UI can fail validation.

        Goes through ``model_validate`` rather than the constructor because
        the choice tuples are ``tuple[str, ...]``: the point of the test is
        that these runtime strings validate, which is also the path the
        settings file and the PATCH body take.
        """
        for model in HUB_MODELS:
            for effort in HUB_REASONING_EFFORTS:
                settings = HubSettings.model_validate(
                    {"hub_model": model, "hub_reasoning_effort": effort}
                )
                assert settings.hub_model == model
                assert settings.hub_reasoning_effort == effort

    def test_json_round_trip(self) -> None:
        """Serialize to JSON and back, all fields preserved."""
        settings = HubSettings(hub_model="gpt-5.6-terra", hub_reasoning_effort="max")
        restored = HubSettings.model_validate_json(settings.model_dump_json())
        assert restored == settings

    def test_unlisted_model_rejected(self) -> None:
        """A model outside the two supported choices raises."""
        with pytest.raises(ValueError):
            HubSettings(hub_model="gpt-5.6-luna")  # type: ignore[arg-type]

    def test_minimal_effort_rejected(self) -> None:
        """`minimal` is rejected by every gpt-5.6 model at request time."""
        with pytest.raises(ValueError):
            HubSettings(hub_reasoning_effort="minimal")  # type: ignore[arg-type]

    def test_unknown_key_rejected(self) -> None:
        """extra=forbid keeps typos from silently doing nothing."""
        with pytest.raises(ValueError):
            HubSettings(hub_modle="gpt-5.6-sol")  # type: ignore[call-arg]

    def test_effort_the_sdk_cannot_accept_is_rejected_with_guidance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Simulate an SDK too old for the stored tier.

        This is what a stale environment does — the app previously died deep
        inside ``Reasoning(...)`` during hub construction. It must instead say
        which tier, which SDK version, and what to do.
        """
        monkeypatch.setattr(
            hub_settings_module,
            "_sdk_reasoning_efforts",
            lambda: frozenset({"low", "medium", "high", "xhigh"}),
        )

        with pytest.raises(ValueError, match="openai") as excinfo:
            HubSettings.model_validate({"hub_reasoning_effort": "max"})

        message = str(excinfo.value)
        assert "max" in message
        assert "2.45.0" in message

    def test_supported_effort_still_passes_under_the_same_guard(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard must not reject tiers the SDK does support."""
        monkeypatch.setattr(
            hub_settings_module,
            "_sdk_reasoning_efforts",
            lambda: frozenset({"low", "medium", "high", "xhigh"}),
        )

        assert HubSettings.model_validate({"hub_reasoning_effort": "xhigh"})


# ---------------------------------------------------------------------------
# HubSettingsStore
# ---------------------------------------------------------------------------


class TestHubSettingsStore:
    """Test file-backed HubSettingsStore (all use tmp_path)."""

    def test_load_returns_defaults_when_missing(self, tmp_path: Path) -> None:
        """An absent file is the normal state for a fresh or upgraded install."""
        store = HubSettingsStore(path=tmp_path / "settings.json")
        assert store.load() == HubSettings()

    def test_load_returns_defaults_when_empty(self, tmp_path: Path) -> None:
        """A zero-byte file is treated as absent, not as corruption."""
        path = tmp_path / "settings.json"
        path.write_text("", encoding="utf-8")
        assert HubSettingsStore(path=path).load() == HubSettings()

    def test_save_then_load_round_trip(self, tmp_path: Path) -> None:
        """save() then load() preserves both fields."""
        path = tmp_path / "settings.json"
        store = HubSettingsStore(path=path)

        original = HubSettings(hub_model="gpt-5.6-terra", hub_reasoning_effort="xhigh")
        store.save(original)
        assert store.load() == original

    def test_save_creates_parent_directory(self, tmp_path: Path) -> None:
        """save() creates ~/.obai when it does not exist yet."""
        path = tmp_path / "nested" / "deep" / "settings.json"
        HubSettingsStore(path=path).save(HubSettings())
        assert path.exists()

    def test_changes_persist_across_store_instances(self, tmp_path: Path) -> None:
        """The web UI and CLI are separate processes reading one file."""
        path = tmp_path / "settings.json"
        HubSettingsStore(path=path).save(HubSettings(hub_model="gpt-5.6-terra"))
        assert HubSettingsStore(path=path).load().hub_model == "gpt-5.6-terra"

    def test_corrupt_json_raises(self, tmp_path: Path) -> None:
        """A corrupt file fails loud rather than silently reverting the tier.

        Falling back to defaults would quietly move the user off the model
        they picked and bill them at a different tier with only a log line.
        """
        path = tmp_path / "settings.json"
        path.write_text("{invalid json!!!", encoding="utf-8")

        with pytest.raises(ValueError, match=str(path)):
            HubSettingsStore(path=path).load()

    def test_invalid_value_raises(self, tmp_path: Path) -> None:
        """A hand-edited unsupported model fails loud, naming the file."""
        path = tmp_path / "settings.json"
        path.write_text('{"hub_model": "gpt-4-turbo"}', encoding="utf-8")

        with pytest.raises(ValueError, match=str(path)):
            HubSettingsStore(path=path).load()

    def test_partial_file_fills_remaining_defaults(self, tmp_path: Path) -> None:
        """Writing one key leaves the other at its default."""
        path = tmp_path / "settings.json"
        path.write_text('{"hub_reasoning_effort": "high"}', encoding="utf-8")

        loaded = HubSettingsStore(path=path).load()
        assert loaded.hub_reasoning_effort == "high"
        assert loaded.hub_model == "gpt-5.6-sol"

    def test_save_is_atomic_leaving_no_temp_files(self, tmp_path: Path) -> None:
        """A crash mid-write must not leave a half-written settings file."""
        path = tmp_path / "settings.json"
        HubSettingsStore(path=path).save(HubSettings())
        assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]

    @pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc/self/fd is Linux-only")
    def test_save_closes_its_temp_file_descriptor(self, tmp_path: Path) -> None:
        """Saving must not leak the descriptor mkstemp opens.

        The web UI settings modal and `obai config` both write through this
        method inside long-lived processes, so a per-save leak accumulates
        until the process hits its descriptor limit.
        """
        store = HubSettingsStore(path=tmp_path / "settings.json")
        store.save(HubSettings())
        before = len(os.listdir("/proc/self/fd"))

        for _ in range(20):
            store.save(HubSettings())

        assert len(os.listdir("/proc/self/fd")) == before
