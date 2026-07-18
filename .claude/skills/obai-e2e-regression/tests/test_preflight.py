from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import preflight
import pytest


def test_opik_config_uses_obai_names_with_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPIK_URL_OVERRIDE", "http://legacy:5173/api")
    monkeypatch.setenv("OPIK_PROJECT_NAME", "legacy-project")
    monkeypatch.delenv("OPIK_URL", raising=False)
    monkeypatch.delenv("OPIK_OBAI_PROJECT_NAME", raising=False)

    assert preflight.configured_opik_url() == "http://legacy:5173"
    assert preflight.configured_opik_project() == "legacy-project"
    normalized = preflight.effective_regression_environment()
    assert normalized["OPIK_URL"] == "http://legacy:5173"
    assert normalized["OPIK_URL_OVERRIDE"] == "http://legacy:5173/api"
    assert normalized["OPIK_OBAI_PROJECT_NAME"] == "legacy-project"

    monkeypatch.setenv("OPIK_URL", "http://primary:5173/api")
    monkeypatch.setenv("OPIK_OBAI_PROJECT_NAME", "primary-project")

    assert preflight.configured_opik_url() == "http://primary:5173"
    assert preflight.configured_opik_project() == "primary-project"
    normalized = preflight.effective_regression_environment()
    assert normalized["OPIK_URL"] == "http://primary:5173"
    assert normalized["OPIK_URL_OVERRIDE"] == "http://primary:5173/api"


def test_opik_config_uses_cli_managed_env_when_process_env_is_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Preflight and the paid CLI resolve the same ~/.obai/.env settings."""
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in (
        "OPIK_URL",
        "OPIK_URL_OVERRIDE",
        "OPIK_OBAI_PROJECT_NAME",
        "OPIK_PROJECT_NAME",
    ):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".obai" / ".env"
    env_file.parent.mkdir()
    env_file.write_text("OPIK_URL=http://cli-file:5173/api\nOPIK_OBAI_PROJECT_NAME=cli-project\n")

    assert preflight.configured_opik_url() == "http://cli-file:5173"
    assert preflight.configured_opik_project() == "cli-project"
    normalized = preflight.effective_regression_environment()
    assert normalized["OPIK_URL"] == "http://cli-file:5173"
    assert normalized["OPIK_URL_OVERRIDE"] == "http://cli-file:5173/api"
    assert normalized["OPIK_OBAI_PROJECT_NAME"] == "cli-project"


def test_check_opik_reports_empty_project_as_preflight_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project validation must not escape as a traceback or reach the network."""
    monkeypatch.setenv("OPIK_OBAI_PROJECT_NAME", "  ")

    assert "must be non-empty" in (preflight.check_opik() or "")


def test_opik_url_rejects_an_ambiguous_nested_api_path() -> None:
    """A health check cannot normalize a URL differently from trace delivery."""
    with pytest.raises(ValueError, match="exactly /api"):
        preflight.normalize_opik_url("http://opik:5173/api/v1")


@pytest.mark.parametrize(
    "base_env",
    [
        {
            "OPIK_URL_OVERRIDE": "http://legacy:5173/api",
            "OPIK_PROJECT_NAME": "legacy-project",
        },
        {
            "OPIK_URL": "http://primary:5173/api",
            "OPIK_OBAI_PROJECT_NAME": "primary-project",
        },
    ],
)
def test_normalized_subprocess_environment_matches_real_agent_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    base_env: dict[str, str],
) -> None:
    """The paid runtime consumes the same base URL/project that preflight checks."""
    from core_agents.config import AgentConfig

    normalized = preflight.effective_regression_environment(
        env_file=tmp_path / "missing.env",
        base_env=base_env,
    )
    for key in (
        "OPIK_URL",
        "OPIK_URL_OVERRIDE",
        "OPIK_OBAI_PROJECT_NAME",
        "OPIK_PROJECT_NAME",
    ):
        monkeypatch.setenv(key, normalized[key])

    config = AgentConfig(specialist_model="offline")

    assert config.opik_url == normalized["OPIK_URL"]
    assert config.opik_project == normalized["OPIK_OBAI_PROJECT_NAME"]
    assert normalized["OPIK_URL_OVERRIDE"] == f"{config.opik_url}/api"


@pytest.mark.parametrize(
    ("primary", "fallback", "getter"),
    [
        ("OPIK_URL", "OPIK_URL_OVERRIDE", preflight.configured_opik_url),
        (
            "OPIK_OBAI_PROJECT_NAME",
            "OPIK_PROJECT_NAME",
            preflight.configured_opik_project,
        ),
    ],
)
@pytest.mark.parametrize("empty_value", ["", "   "])
def test_explicit_empty_primary_opik_setting_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    primary: str,
    fallback: str,
    getter: Callable[[], str],
    empty_value: str,
) -> None:
    """An explicit primary setting cannot silently fall through to another endpoint."""
    monkeypatch.setenv(primary, empty_value)
    monkeypatch.setenv(fallback, "http://fallback:5173" if "URL" in fallback else "fallback")

    with pytest.raises(ValueError, match=rf"{primary} must be non-empty"):
        getter()


@pytest.mark.parametrize(
    ("fallback", "getter"),
    [
        ("OPIK_URL_OVERRIDE", preflight.configured_opik_url),
        ("OPIK_PROJECT_NAME", preflight.configured_opik_project),
    ],
)
def test_explicit_empty_fallback_opik_setting_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    fallback: str,
    getter: Callable[[], str],
) -> None:
    """A configured legacy setting cannot be replaced by the default endpoint."""
    primary = "OPIK_URL" if "URL" in fallback else "OPIK_OBAI_PROJECT_NAME"
    monkeypatch.delenv(primary, raising=False)
    monkeypatch.setenv(fallback, "")

    with pytest.raises(ValueError, match=rf"{fallback} must be non-empty"):
        getter()


def test_whitespace_openai_key_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "   ")

    assert preflight.check_openai_key() is not None


def test_openai_key_is_discovered_from_cli_managed_env_without_exposure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    secret = "sk-cli-managed-secret"
    env_file = tmp_path / ".obai" / ".env"
    env_file.parent.mkdir()
    env_file.write_text(f"FMP_API_KEY=other\nOPENAI_API_KEY='{secret}'\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert preflight.check_openai_key(env_file=env_file) is None
    assert "OPENAI_API_KEY" not in preflight.os.environ


def test_openai_environment_value_has_same_precedence_as_obai_cli_loader(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=valid-file-secret\n")
    monkeypatch.setenv("OPENAI_API_KEY", "   ")

    problem = preflight.check_openai_key(env_file=env_file)

    assert problem is not None
    assert "valid-file-secret" not in problem


def test_effective_credential_identity_uses_environment_and_ignores_inactive_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=inactive-file-secret\n")
    monkeypatch.setenv("OPENAI_API_KEY", "active-environment-secret")

    first = preflight.effective_openai_credential_identity(env_file=env_file)
    env_file.write_text("OPENAI_API_KEY=changed-but-still-inactive\n")
    second = preflight.effective_openai_credential_identity(env_file=env_file)

    assert first == second
    assert first["origin"] == "environment"
    assert (
        first["digest_sha256"] != preflight.hashlib.sha256(b"active-environment-secret").hexdigest()
    )
    assert "active-environment-secret" not in str(first)
    assert "inactive-file-secret" not in str(first)


def test_effective_credential_identity_changes_with_effective_file_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file.write_text("OPENAI_API_KEY=file-secret-one\n")
    first = preflight.effective_openai_credential_identity(env_file=env_file)
    env_file.write_text("OPENAI_API_KEY=file-secret-two\n")
    second = preflight.effective_openai_credential_identity(env_file=env_file)

    assert first["origin"] == "cli_env_file"
    assert first["digest_sha256"] != second["digest_sha256"]
    assert "file-secret" not in str(first)


@pytest.mark.parametrize(
    "url",
    [
        "http://user:password@localhost:5173",
        "https://token@opik.example/api",
    ],
)
def test_opik_url_rejects_userinfo_without_echoing_it(url: str) -> None:
    with pytest.raises(ValueError, match="must not contain userinfo") as exc_info:
        preflight.normalize_opik_url(url)

    assert "password" not in str(exc_info.value)
    assert "token@" not in str(exc_info.value)


def test_redaction_removes_url_userinfo_query_secrets_and_bearer_tokens() -> None:
    raw = (
        "failed https://alice:pw@example.test/path?api_key=top-secret&safe=yes "
        "OPENAI_API_KEY=sk-raw Authorization: Bearer bearer-secret"
    )

    redacted = preflight.redact_sensitive_text(raw)

    assert "alice" not in redacted
    assert "pw" not in redacted
    assert "top-secret" not in redacted
    assert "sk-raw" not in redacted
    assert "bearer-secret" not in redacted
    assert "safe=yes" in redacted
