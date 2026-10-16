"""setup.sh installs the obai CLI at the versions in src/obai/uv.lock.

`uv tool install` ignores the lockfile, so without constraints the CLI takes the
newest release of every dependency, including majors no test has run against.
"""

import re
from pathlib import Path

SETUP = Path(__file__).resolve().parents[1] / "setup.sh"


def test_every_cli_tool_install_is_constrained_to_the_lockfile() -> None:
    """Each `uv tool install` passes the constraints exported from the frozen lock."""
    text = SETUP.read_text()
    installs = re.findall(r"uv tool install --reinstall[^\n>]*", text)

    assert len(installs) == 2
    assert all('--constraints "$LOCK_CONSTRAINTS"' in line for line in installs)
    assert 'uv export --project "$OBAI_SRC" --frozen --no-dev' in text
