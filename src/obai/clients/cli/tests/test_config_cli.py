"""Tests for the `obai config` hub-settings commands.

Covers `set-model`, `set-effort`, and the provenance block `show` prints:
happy paths, rejection of unknown values, the warning that fires when an
env var outranks the file, and the clean error a corrupt settings file
produces instead of a traceback.

The store is redirected at `core_agents.hub_settings.default_hub_settings_path`
so no test touches the real ~/.obai/settings.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clients.cli.chat import cli

runner = CliRunner()


def _row(output: str, label: str) -> str:
    """Return the `config show` line for a hub setting, without its padding."""
    matches = [line for line in output.splitlines() if line.strip().startswith(label)]
    assert len(matches) == 1, f"expected one {label!r} row, got {matches}"
    return " ".join(matches[0].split())


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the hub settings store at tmp_path with a clean environment."""
    path = tmp_path / "settings.json"
    monkeypatch.setattr(
        "core_agents.hub_settings.default_hub_settings_path",
        lambda: path,
    )
    monkeypatch.delenv("ORCHESTRATOR_MODEL", raising=False)
    monkeypatch.delenv("ORCHESTRATOR_REASONING_EFFORT", raising=False)
    return path


# --- set-model / set-effort happy paths ---


def test_set_model_writes_file_and_says_when_it_applies(settings_file: Path) -> None:
    """A valid model is persisted, echoed with its path, and scoped to new clients."""
    result = runner.invoke(cli, ["config", "set-model", "gpt-5.6-terra"])

    assert result.exit_code == 0
    assert json.loads(settings_file.read_text())["hub_model"] == "gpt-5.6-terra"
    assert "gpt-5.6-terra" in result.output
    assert str(settings_file) in result.output
    # "obai restart" would rebuild the Docker stack for a two-field change.
    assert "obai restart" not in result.output
    assert "relaunch a running obai chat/tui" in result.output


def test_set_effort_writes_file_and_says_when_it_applies(settings_file: Path) -> None:
    """A valid effort is persisted, echoed with its path, and scoped to new clients."""
    result = runner.invoke(cli, ["config", "set-effort", "xhigh"])

    assert result.exit_code == 0
    assert json.loads(settings_file.read_text())["hub_reasoning_effort"] == "xhigh"
    assert "xhigh" in result.output
    assert str(settings_file) in result.output
    # "obai restart" would rebuild the Docker stack for a two-field change.
    assert "obai restart" not in result.output
    assert "relaunch a running obai chat/tui" in result.output


def test_set_model_preserves_the_stored_effort(settings_file: Path) -> None:
    """Setting one field must not reset the other to its default."""
    assert runner.invoke(cli, ["config", "set-effort", "max"]).exit_code == 0

    result = runner.invoke(cli, ["config", "set-model", "gpt-5.6-terra"])

    assert result.exit_code == 0
    stored = json.loads(settings_file.read_text())
    assert stored == {"hub_model": "gpt-5.6-terra", "hub_reasoning_effort": "max"}


# --- Invalid values ---


def test_set_model_rejects_unknown_model(settings_file: Path) -> None:
    """An unsupported model exits 1, lists the valid ones, and writes nothing."""
    result = runner.invoke(cli, ["config", "set-model", "gpt-5.6-luna"])

    assert result.exit_code == 1
    assert "Unknown model: gpt-5.6-luna" in result.output
    assert "gpt-5.6-sol" in result.output
    assert "gpt-5.6-terra" in result.output
    assert not settings_file.exists()


def test_set_effort_rejects_unknown_effort(settings_file: Path) -> None:
    """An unsupported effort exits 1, lists the valid ones, and writes nothing."""
    result = runner.invoke(cli, ["config", "set-effort", "minimal"])

    assert result.exit_code == 1
    assert "Unknown reasoning effort: minimal" in result.output
    assert "medium, high, xhigh, max" in result.output
    assert not settings_file.exists()


# --- Env var outranks the file ---


def test_set_model_warns_when_env_var_is_set_but_still_saves(
    settings_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ORCHESTRATOR_MODEL outranks the file, so the save warns but still lands."""
    monkeypatch.setenv("ORCHESTRATOR_MODEL", "gpt-5.6-sol")

    result = runner.invoke(cli, ["config", "set-model", "gpt-5.6-terra"])

    assert result.exit_code == 0
    assert "Warning: ORCHESTRATOR_MODEL=gpt-5.6-sol" in result.output
    assert "outranks" in result.output
    assert json.loads(settings_file.read_text())["hub_model"] == "gpt-5.6-terra"


def test_set_effort_warns_when_env_var_is_set_but_still_saves(
    settings_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ORCHESTRATOR_REASONING_EFFORT outranks the file the same way."""
    monkeypatch.setenv("ORCHESTRATOR_REASONING_EFFORT", "high")

    result = runner.invoke(cli, ["config", "set-effort", "max"])

    assert result.exit_code == 0
    assert "Warning: ORCHESTRATOR_REASONING_EFFORT=high" in result.output
    assert json.loads(settings_file.read_text())["hub_reasoning_effort"] == "max"


def test_set_model_does_not_warn_without_the_env_var(settings_file: Path) -> None:
    """No env override means no warning noise on the happy path."""
    result = runner.invoke(cli, ["config", "set-model", "gpt-5.6-terra"])

    assert result.exit_code == 0
    assert "Warning" not in result.output


# --- Corrupt settings file ---


def test_set_model_reports_corrupt_settings_file(settings_file: Path) -> None:
    """Unparseable JSON exits 1 with a readable message and leaves the file alone."""
    settings_file.write_text("{not json")

    result = runner.invoke(cli, ["config", "set-model", "gpt-5.6-terra"])

    assert result.exit_code == 1
    assert "Invalid hub settings" in result.output
    assert "Traceback" not in result.output
    assert settings_file.read_text() == "{not json"


def test_set_effort_reports_invalid_settings_value(settings_file: Path) -> None:
    """Valid JSON holding an unsupported value is reported, not silently replaced."""
    settings_file.write_text(json.dumps({"hub_model": "gpt-5.6-nope"}))

    result = runner.invoke(cli, ["config", "set-effort", "high"])

    assert result.exit_code == 1
    assert "Invalid hub settings" in result.output
    assert "Traceback" not in result.output


def test_show_reports_corrupt_settings_file(settings_file: Path) -> None:
    """`show` surfaces the same clean error rather than a traceback."""
    settings_file.write_text("{not json")

    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 1
    assert "Invalid hub settings" in result.output
    assert "Traceback" not in result.output


# --- show provenance ---


def test_show_reports_shipped_defaults_without_a_settings_file(settings_file: Path) -> None:
    """With no file and no env vars, both values come from the shipped defaults."""
    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert _row(result.output, "hub model") == "hub model gpt-5.6-sol (from shipped default)"
    assert (
        _row(result.output, "reasoning effort") == "reasoning effort medium (from shipped default)"
    )


def test_show_reports_the_settings_file_as_the_source(settings_file: Path) -> None:
    """After a setter runs, both fields are attributed to the settings file."""
    assert runner.invoke(cli, ["config", "set-model", "gpt-5.6-terra"]).exit_code == 0

    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert str(settings_file) in result.output
    assert _row(result.output, "hub model") == "hub model gpt-5.6-terra (from settings file)"
    assert _row(result.output, "reasoning effort") == "reasoning effort medium (from settings file)"


def test_show_distinguishes_a_chosen_default_from_the_shipped_default(
    settings_file: Path,
) -> None:
    """A field written to the file reads as chosen even when it equals the default."""
    settings_file.write_text(json.dumps({"hub_model": "gpt-5.6-sol"}))

    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert _row(result.output, "hub model") == "hub model gpt-5.6-sol (from settings file)"
    assert (
        _row(result.output, "reasoning effort") == "reasoning effort medium (from shipped default)"
    )


def test_show_reports_env_vars_as_the_source(
    settings_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env vars win over the file, and `show` says so with the effective value."""
    settings_file.write_text(
        json.dumps({"hub_model": "gpt-5.6-terra", "hub_reasoning_effort": "max"})
    )
    monkeypatch.setenv("ORCHESTRATOR_MODEL", "gpt-5.6-sol")
    monkeypatch.setenv("ORCHESTRATOR_REASONING_EFFORT", "high")

    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert _row(result.output, "hub model") == "hub model gpt-5.6-sol (from env ORCHESTRATOR_MODEL)"
    assert (
        _row(result.output, "reasoning effort")
        == "reasoning effort high (from env ORCHESTRATOR_REASONING_EFFORT)"
    )


def test_show_flags_an_env_value_the_hub_will_reject(
    settings_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`show` is the documented way to diagnose "my change did not take".

    An env var is applied verbatim and outranks everything, so an unlisted
    value there is what the hub gets. `minimal` used to be accepted and no
    longer is, so anyone upgrading with it exported hits a hub that cannot
    build its config at all — `show` must not report that as healthy.
    """
    monkeypatch.setenv("ORCHESTRATOR_REASONING_EFFORT", "minimal")

    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert "Not an accepted value" in result.output
    assert "ORCHESTRATOR_REASONING_EFFORT='minimal'" in result.output
    assert "medium, high, xhigh, max" in result.output


def test_show_does_not_flag_accepted_values(settings_file: Path) -> None:
    """The warning must stay quiet for a healthy configuration."""
    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert "Not an accepted value" not in result.output


def test_show_matches_a_lowercase_env_var(
    settings_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pydantic-settings matches env case-insensitively; provenance must too."""
    monkeypatch.setenv("orchestrator_model", "gpt-5.6-terra")

    result = runner.invoke(cli, ["config", "show"])

    assert result.exit_code == 0
    assert (
        _row(result.output, "hub model") == "hub model gpt-5.6-terra (from env ORCHESTRATOR_MODEL)"
    )
