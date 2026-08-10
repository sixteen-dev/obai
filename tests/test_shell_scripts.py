"""Guards for the shell scripts that bootstrap an install.

`setup.sh` runs under `set -euo pipefail`, so a command that fails in an
unguarded assignment kills the run on the spot — the user sees one stderr line
and the remaining steps silently never happen. These tests cover the failure
modes that shape, since a broken installer is invisible to every Python suite
in the repo.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHELL_SCRIPTS = ("setup.sh", "install.sh", "teardown.sh")

# `mktemp TEMPLATE` requires at least three trailing X's on GNU coreutils.
# BSD/macOS `mktemp -t PREFIX` accepts a bare prefix, so a template written
# on macOS can fail on every Linux install and still look fine to its author.
_MKTEMP_CALL = re.compile(r"\bmktemp\b[^\n)]*")
_MIN_X = "XXX"


def _script_paths() -> list[Path]:
    """Return the bootstrap scripts that exist in the repo.

    Returns:
        Existing shell script paths.
    """
    return [REPO_ROOT / name for name in SHELL_SCRIPTS if (REPO_ROOT / name).is_file()]


def _mktemp_calls(script: Path) -> list[str]:
    """Extract the mktemp invocations a script actually executes.

    Comment lines are skipped: prose about mktemp is not a call, and the
    comment explaining this very requirement would otherwise match.

    Args:
        script: Shell script to scan.

    Returns:
        Every mktemp invocation found outside comments.
    """
    return [
        call
        for line in script.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#")
        for call in _MKTEMP_CALL.findall(line)
    ]


@pytest.mark.parametrize("script", _script_paths(), ids=lambda p: p.name)
def test_mktemp_templates_have_enough_placeholder_xs(script: Path) -> None:
    """Every mktemp template must satisfy GNU's three-X minimum."""
    offenders = [call for call in _mktemp_calls(script) if _MIN_X not in call]
    assert not offenders, (
        f"{script.name}: mktemp template(s) without at least '{_MIN_X}' — "
        f'GNU mktemp exits 1 with "too few X\'s in template", and under '
        f"`set -e` that aborts the install: {offenders}"
    )


@pytest.mark.parametrize("script", _script_paths(), ids=lambda p: p.name)
def test_scripts_parse(script: Path) -> None:
    """A syntax error would abort the install before it printed anything."""
    result = subprocess.run(
        ["bash", "-n", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"{script.name} failed bash -n:\n{result.stderr}"


@pytest.mark.parametrize("script", _script_paths(), ids=lambda p: p.name)
def test_mktemp_templates_actually_run(script: Path) -> None:
    """Run each template through the real mktemp on this platform.

    The static check above cannot catch a template that is malformed some
    other way, and mktemp is the only authority on what it accepts.
    """
    for call in _mktemp_calls(script):
        result = subprocess.run(
            ["bash", "-c", f'p="$({call})" && rm -f "$p"'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"{script.name}: `{call}` fails on this platform:\n{result.stderr}"
        )
