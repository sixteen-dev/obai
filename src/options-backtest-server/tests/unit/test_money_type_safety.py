"""mypy rejects every money-type misuse: invariant 2 lives in the type checker (ADR 0001 §2)."""

import re
from pathlib import Path

import pytest
from mypy import api as mypy_api

SERVICE_DIR = Path(__file__).resolve().parents[2]
CASES = Path(__file__).resolve().parent / "type_misuse_cases.py"
EXPECT_COMMENT = re.compile(r"#\s*expect:\s*(?P<code>[a-z-]+)\s*$")
ERROR_LINE = re.compile(r"^(?P<path>.+?):(?P<line>\d+): error: .*\[(?P<code>[a-z-]+)\]$")


def _expected_errors() -> set[tuple[int, str]]:
    expected = set()
    for number, line in enumerate(CASES.read_text(encoding="utf-8").splitlines(), start=1):
        match = EXPECT_COMMENT.search(line)
        if match:
            expected.add((number, match["code"]))
    return expected


def _reported_errors(stdout: str) -> set[tuple[int, str]]:
    reported = set()
    for line in stdout.splitlines():
        if ": error: " not in line:
            continue
        match = ERROR_LINE.match(line)
        assert match, f"unparseable mypy error: {line}"
        assert Path(match["path"]).name == CASES.name, line
        reported.add((int(match["line"]), match["code"]))
    return reported


def test_mypy_rejects_exactly_the_marked_misuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(SERVICE_DIR)  # the service's mypy_path is relative to the working directory
    expected = _expected_errors()

    stdout, stderr, status = mypy_api.run(
        [
            "--config-file",
            str(SERVICE_DIR / "pyproject.toml"),
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            "--no-error-summary",
            str(CASES),
        ]
    )

    assert stderr == ""
    assert {code for _, code in expected} == {"operator", "arg-type"}
    assert _reported_errors(stdout) == expected, stdout
    assert status == 1
