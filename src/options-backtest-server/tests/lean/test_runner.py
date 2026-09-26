"""The LEAN runner's preflight skips, never fails, without a toolchain (ADR 0002 §16 decision 3).

These run in the default suite: they need neither LEAN nor .NET.
"""

from pathlib import Path

import pytest

from .runner import LAUNCHER_DLL, preflight


def test_preflight_skips_without_lean_root() -> None:
    with pytest.raises(pytest.skip.Exception, match="LEAN_ROOT is not set"):
        preflight({"PATH": ""})


def test_preflight_skips_when_the_launcher_is_not_built(tmp_path: Path) -> None:
    with pytest.raises(pytest.skip.Exception, match="no built LEAN launcher"):
        preflight({"LEAN_ROOT": str(tmp_path), "PATH": ""})


def test_preflight_skips_without_a_dotnet_sdk(tmp_path: Path) -> None:
    launcher = tmp_path / LAUNCHER_DLL
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"")

    with pytest.raises(pytest.skip.Exception, match="no .NET SDK"):
        preflight({"LEAN_ROOT": str(tmp_path), "PATH": str(tmp_path)})


def test_preflight_skips_when_dotnet_root_holds_no_dotnet(tmp_path: Path) -> None:
    launcher = tmp_path / "lean" / LAUNCHER_DLL
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b"")
    environ = {"LEAN_ROOT": str(tmp_path / "lean"), "DOTNET_ROOT": str(tmp_path), "PATH": ""}

    with pytest.raises(pytest.skip.Exception, match="no .NET SDK"):
        preflight(environ)
