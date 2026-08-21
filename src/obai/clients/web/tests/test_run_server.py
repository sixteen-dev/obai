"""How `obai web` hands the app to uvicorn, with and without --reload.

Reload cannot be given an app *object*: uvicorn's reloader re-imports the app
in a spawned subprocess, so it needs an import string plus ``factory=True``.
Getting that wrong fails only at runtime, in a dev-only code path that no
other test exercises.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from clients.web import server as server_module

_IMPORT_STRING = "clients.web.server:create_app"


@pytest.fixture
def uvicorn_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture the uvicorn.run call instead of binding a port.

    ``$HOME`` is redirected at the same time: run_server opens a log file
    under ``~/.obai/logs`` on every call, and these tests call it repeatedly.

    Returns:
        Dict populated with ``app`` and ``kwargs`` once run_server is called.
    """
    import uvicorn

    monkeypatch.setenv("HOME", str(tmp_path))
    captured: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return captured


class TestRunServerDefault:
    """Without --reload the app object is passed straight through."""

    def test_passes_an_app_instance(self, uvicorn_call: dict[str, Any]) -> None:
        """The default launch path — used by setup.sh — must stay unchanged."""
        server_module.run_server(port=8099)

        assert uvicorn_call["app"] is not None
        assert not isinstance(uvicorn_call["app"], str)
        assert uvicorn_call["kwargs"]["port"] == 8099

    def test_does_not_enable_reload(self, uvicorn_call: dict[str, Any]) -> None:
        """Reload is opt-in; production launches must never watch files."""
        server_module.run_server(port=8099)

        assert not uvicorn_call["kwargs"].get("reload", False)


class TestRunServerReload:
    """--reload must use the import-string + factory form."""

    def test_passes_an_import_string_not_an_instance(self, uvicorn_call: dict[str, Any]) -> None:
        """A live app object silently breaks uvicorn's reloader."""
        server_module.run_server(port=8099, reload=True)

        assert uvicorn_call["app"] == _IMPORT_STRING

    def test_marks_the_target_as_a_factory(self, uvicorn_call: dict[str, Any]) -> None:
        """create_app is a factory; without this uvicorn treats it as an app."""
        server_module.run_server(port=8099, reload=True)

        assert uvicorn_call["kwargs"]["factory"] is True
        assert uvicorn_call["kwargs"]["reload"] is True

    def test_watches_exactly_the_source_packages(self, uvicorn_call: dict[str, Any]) -> None:
        """Each watched dir must be a real source package, by name."""
        server_module.run_server(port=8099, reload=True)

        watched = [Path(d) for d in uvicorn_call["kwargs"]["reload_dirs"]]
        assert [d.name for d in watched] == list(server_module._RELOAD_PACKAGES)
        for directory in watched:
            assert directory.is_dir(), f"watched path does not exist: {directory}"
            assert (directory / "__init__.py").is_file(), f"not a package: {directory}"

    def test_does_not_watch_the_virtualenv(self, uvicorn_call: dict[str, Any]) -> None:
        """Watching the packages' parent would pull in .venv.

        That directory holds two orders of magnitude more .py files than the
        source tree, so a `uv sync` in another terminal would tear the hub
        down and pay a full MCP re-init for a dependency change.
        """
        server_module.run_server(port=8099, reload=True)

        for directory in uvicorn_call["kwargs"]["reload_dirs"]:
            resolved = Path(directory).resolve()
            assert not (resolved / ".venv").exists(), f"{resolved} contains .venv"
            assert ".venv" not in resolved.parts

    def test_static_assets_do_not_trigger_a_reload(self, uvicorn_call: dict[str, Any]) -> None:
        """CSS/JS/HTML live under clients/, so the filter must reject them.

        Asserted through uvicorn's own FileFilter rather than by inspecting
        our kwargs, because the filter — not this call — is what decides.
        """
        from uvicorn.config import Config
        from uvicorn.supervisors.watchfilesreload import FileFilter

        server_module.run_server(port=8099, reload=True)
        kwargs = uvicorn_call["kwargs"]
        file_filter = FileFilter(
            Config(
                uvicorn_call["app"],
                factory=kwargs["factory"],
                reload=True,
                reload_dirs=kwargs["reload_dirs"],
            )
        )
        static = Path(server_module._STATIC_DIR)

        assert not file_filter(static / "style.css")
        assert not file_filter(static / "app.js")
        assert not file_filter(static / "index.html")
        assert file_filter(Path(server_module.__file__))

    def test_import_string_actually_resolves(self) -> None:
        """The reload subprocess re-imports by this exact string."""
        from uvicorn.importer import import_from_string

        factory = import_from_string(_IMPORT_STRING)
        assert factory is server_module.create_app
