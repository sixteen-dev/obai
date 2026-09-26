"""Server settings: fleet variable names, loopback default, fixed deployment mode (ADR 0003)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from options_backtest.config import DEPLOYMENT_MODE, Settings

VARIABLES = ("TRANSPORT", "HOST", "PORT", "LOG_LEVEL", "DEPLOYMENT_MODE")


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run each test without the caller's variables and away from any ``.env`` file."""
    for name in VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)


def test_defaults_bind_loopback_on_the_reserved_port() -> None:
    settings = Settings()

    assert settings.transport == "streamable-http"
    assert settings.host == "127.0.0.1"
    assert settings.port == 8012
    assert settings.log_level == "INFO"


def test_environment_overrides_the_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "9123")

    assert Settings().port == 9123


def test_the_variables_have_no_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRANSPORT", "http")
    monkeypatch.setenv("HOST", "0.0.0.0")  # noqa: S104 — the container's bind address
    monkeypatch.setenv("LOG_LEVEL", "WARNING")

    settings = Settings()

    assert (settings.transport, settings.host, settings.log_level) == ("http", "0.0.0.0", "WARNING")  # noqa: S104


def test_the_dotenv_file_of_the_working_directory_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PORT=9555\n", encoding="utf-8")

    assert Settings().port == 9555


def test_a_lower_case_log_level_is_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")

    assert Settings().log_level == "DEBUG"


def test_an_unknown_log_level_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "LOUD")

    with pytest.raises(ValidationError, match="log_level"):
        Settings()


@pytest.mark.parametrize("transport", ["stdio", "sse", "websocket"])
def test_a_transport_the_server_cannot_run_raises(
    monkeypatch: pytest.MonkeyPatch, transport: str
) -> None:
    # stdio would carry the JSON logs on the protocol channel; sse cannot be stateless.
    monkeypatch.setenv("TRANSPORT", transport)

    with pytest.raises(ValidationError, match="transport"):
        Settings()


def test_the_deployment_mode_is_local_single_user_and_not_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEPLOYMENT_MODE", "hosted")

    assert DEPLOYMENT_MODE == "local_single_user"
    assert "deployment_mode" not in Settings.model_fields
    assert not hasattr(Settings(), "deployment_mode")
