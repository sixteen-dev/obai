"""Opt-in contract for the optional options-backtest server (ADR 0004 §3-§5).

Runs the real `setup.sh`, `teardown.sh` and `install.sh` with a directory of stub commands
first on PATH. Each stub logs its argv instead of acting, so a test sees every
`docker compose` call, health probe and Web UI launch a run makes, plus what it
wrote to `~/.obai/.env`. Nothing real is touched: HOME, OBAI_HOME and TMPDIR
live under `tmp_path`, and every command in the scripts with an effect outside
them (docker, curl, uv, obai, pgrep, ...) resolves to a stub.
"""

from __future__ import annotations

import os
import re
import shlex
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPOSE_FILE = str(REPO_ROOT / "docker-compose.yml")
SERVICE = "options-backtest-server"
PROFILE = ("--profile", "options-backtest")
OPT_IN_KEY = "ENABLE_OPTIONS_STRATEGY"
WITH = "--with-options-backtest"
WITHOUT = "--without-options-backtest"
HEALTH_URL = "http://localhost:8012/health/ready"
# The stub `docker ps` reports this container as left by an earlier opt-in.
LEFTOVER = {"STUB_LEFTOVER_CONTAINER": "0123456789ab"}
RUN_TIMEOUT_S = 60

# `docker compose` options that take a value and come before the subcommand.
_COMPOSE_VALUE_OPTIONS = frozenset({"-p", "-f", "--profile"})

# One body per stubbed command; the shared header logs argv to "$log" first.
_STUB_BODIES: dict[str, str] = {
    # setup.sh step 1 parses `version --short`. Failing `pull` sends setup.sh
    # down its build fallback, so one run exercises pull, build and up. The
    # leftover-container query answers with $STUB_LEFTOVER_CONTAINER.
    "docker": """case "$*" in
    "compose version --short") echo 5.0.1 ;;
    *" pull") exit 1 ;;
    "ps -aq "*) [ -z "${STUB_LEFTOVER_CONTAINER:-}" ] || echo "$STUB_LEFTOVER_CONTAINER" ;;
esac""",
    "python3": "echo 3.12",
    "git": "echo stub-branch",
    # No running Web UI, so teardown.sh never reaches `kill`.
    "pgrep": "exit 1",
    "curl": "exit 0",
    "uv": "exit 0",
    "sleep": "exit 0",
    # The Web UI inherits setup.sh's environment; record the opt-in it sees.
    "obai": 'printf \'obai-env %q\\n\' "${ENABLE_OPTIONS_STRATEGY-unset}" >> "$log"',
}


@dataclass(frozen=True)
class Sandbox:
    """Temp HOME plus the stub directory and the log its stubs append to."""

    root: Path
    stubs: Path
    log: Path

    @property
    def home(self) -> Path:
        """HOME for the script under test."""
        return self.root / "home"

    @property
    def env_file(self) -> Path:
        """The persisted opt-in file, `$OBAI_HOME/.env`."""
        return self.home / ".obai" / ".env"


@dataclass(frozen=True)
class Run:
    """Outcome of one script run: exit code, combined output, logged calls."""

    returncode: int
    output: str
    calls: list[list[str]]


def _stub_script(log: Path, body: str) -> str:
    """Build a stub that logs its name and shell-quoted argv, then runs `body`.

    Args:
        log: File every stub appends one line per invocation to.
        body: Stub-specific bash run after logging.

    Returns:
        The stub's script text.
    """
    # `printf ' %q'` with no arguments still prints one empty `''`, hence the guard.
    # The line is built first and appended by one printf, so one write: setup.sh
    # backgrounds the Web UI, and stubs logging at once must not interleave.
    header = (
        'line="${0##*/}"; [ "$#" -eq 0 ] || line+="$(printf \' %q\' "$@")"\n'
        'printf \'%s\\n\' "$line" >> "$log"'
    )
    return f"#!/usr/bin/env bash\nlog={shlex.quote(str(log))}\n{header}\n{body}\n"


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    """Create the stub directory, the call log and an empty HOME."""
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    log.touch()
    for name, body in _STUB_BODIES.items():
        stub = stubs / name
        stub.write_text(_stub_script(log, body), encoding="utf-8")
        stub.chmod(0o755)
    (tmp_path / "home").mkdir()
    (tmp_path / "tmp").mkdir()
    return Sandbox(root=tmp_path, stubs=stubs, log=log)


def _run(
    sandbox: Sandbox,
    script: str,
    *args: str,
    extra_env: dict[str, str] | None = None,
    piped: bool = False,
) -> Run:
    """Run a repo script against the sandbox and collect what it did.

    The environment is built from scratch so a key exported in the developer's
    shell (including the opt-in itself) cannot leak into the run. PATH is the
    stub directory followed by the platform default search path only.

    Args:
        sandbox: Stubs, log and HOME to run against.
        script: Script name relative to the repo root.
        *args: Arguments passed to the script.
        extra_env: Variables added on top of the minimal environment.
        piped: Run it as `curl … | bash -s -- ARGS` does: the script text
            arrives on stdin through a pipe instead of as a file argument.

    Returns:
        The exit code, combined stdout/stderr and every logged call.
    """
    env = {
        "PATH": f"{sandbox.stubs}{os.pathsep}{os.defpath}",
        "HOME": str(sandbox.home),
        "OBAI_HOME": str(sandbox.home / ".obai"),
        "TMPDIR": str(sandbox.root / "tmp"),
        "OPENAI_API_KEY": "sk-test",
        "FMP_API_KEY": "fmp-test",
        **(extra_env or {}),
    }
    command = ["bash", str(REPO_ROOT / script), *args]
    script_text = None
    if piped:
        command = ["bash", "-s", "--", *args]
        script_text = (REPO_ROOT / script).read_text(encoding="utf-8")
    result = subprocess.run(
        command,
        env=env,
        cwd=sandbox.root,
        input=script_text,
        stdin=None if piped else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=RUN_TIMEOUT_S,
        check=False,
    )
    lines = sandbox.log.read_text(encoding="utf-8").splitlines()
    return Run(result.returncode, result.stdout, [shlex.split(line) for line in lines])


def _setup(
    sandbox: Sandbox,
    *args: str,
    seed: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> Run:
    """Run `setup.sh --skip-opik` after optionally pre-seeding `~/.obai/.env`.

    Args:
        sandbox: Sandbox to run in.
        *args: Extra setup.sh flags.
        seed: Exact bytes to write to the env file first; None leaves it absent.
        extra_env: Variables added to the run's environment (see `_run`).

    Returns:
        The run outcome.
    """
    if seed is not None:
        sandbox.env_file.parent.mkdir(parents=True)
        sandbox.env_file.write_text(seed, encoding="utf-8")
    return _run(sandbox, "setup.sh", "--skip-opik", *args, extra_env=extra_env)


def _compose_calls(run: Run) -> list[list[str]]:
    """Return the arguments after `docker compose` of every compose call."""
    return [call[2:] for call in run.calls if call[:2] == ["docker", "compose"]]


def _subcommand(args: list[str]) -> str:
    """Return the compose subcommand, skipping the value options before it."""
    index = 0
    while args[index] in _COMPOSE_VALUE_OPTIONS:
        index += 2
    return args[index]


def _has_pair(args: list[str], option: str, value: str) -> bool:
    """Whether `option value` appear adjacent in `args`."""
    return any(args[i : i + 2] == [option, value] for i in range(len(args) - 1))


def _project_calls(run: Run, subcommand: str) -> list[list[str]]:
    """Compose calls against project `obai` (not Opik's) with `subcommand`."""
    return [
        args
        for args in _compose_calls(run)
        if _has_pair(args, "-p", "obai") and _subcommand(args) == subcommand
    ]


def _opt_in_lines(sandbox: Sandbox) -> list[str]:
    """Every line of the env file that sets the opt-in key."""
    if not sandbox.env_file.exists():
        return []
    text = sandbox.env_file.read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith(f"{OPT_IN_KEY}=")]


def _web_ui_opt_in(run: Run) -> list[str]:
    """The opt-in value each Web UI launch saw in its environment."""
    return [call[1] for call in run.calls if call[0] == "obai-env"]


def _case_flags(script: str) -> set[str]:
    """Long flags a script's argument `case` statement accepts."""
    text = (REPO_ROOT / script).read_text(encoding="utf-8")
    block = re.search(r'case "\$arg" in\n(.*?)\n\s*esac', text, re.DOTALL)
    assert block is not None, f'{script}: no `case "$arg" in` block'
    labels = re.findall(r"^\s*([-\w|]+)\)", block.group(1), re.MULTILINE)
    return {flag for label in labels for flag in label.split("|") if flag.startswith("--")}


# --- The harness itself ------------------------------------------------------


def test_concurrent_stub_calls_each_log_one_whole_line(sandbox: Sandbox) -> None:
    """Stubs running at once (setup.sh backgrounds the Web UI) never garble the log."""
    calls = 40
    script = 'for i in {1..%d}; do curl -sf "http://127.0.0.1:$i" web --port "$i" & done; wait'
    subprocess.run(
        ["bash", "-c", script % calls],
        env={"PATH": f"{sandbox.stubs}{os.pathsep}{os.defpath}"},
        stdin=subprocess.DEVNULL,
        timeout=RUN_TIMEOUT_S,
        check=True,
    )

    logged = [shlex.split(line) for line in sandbox.log.read_text(encoding="utf-8").splitlines()]
    expected = [
        ["curl", "-sf", f"http://127.0.0.1:{i}", "web", "--port", str(i)]
        for i in range(1, calls + 1)
    ]
    assert sorted(logged) == sorted(expected)


# --- Default (opted out): nothing about the server is touched --------------


def test_default_setup_never_activates_the_profile(sandbox: Sandbox) -> None:
    """P1: no pull, build or up names the options-backtest profile."""
    run = _setup(sandbox)

    assert run.returncode == 0, run.output
    for subcommand in ("pull", "build", "up"):
        assert len(_project_calls(run, subcommand)) == 1, subcommand
    assert [args for args in _compose_calls(run) if "--profile" in args] == []
    assert _opt_in_lines(sandbox) == []


def test_default_setup_removes_a_leftover_container_after_up(sandbox: Sandbox) -> None:
    """`down`/--remove-orphans skip an inactive-profile container, so rm it by name."""
    run = _setup(sandbox, extra_env=LEFTOVER)

    compose = _compose_calls(run)
    removals = _project_calls(run, "rm")
    assert len(removals) == 1, run.output
    assert removals[0][-4:] == ["rm", "--stop", "--force", SERVICE]
    assert compose.index(removals[0]) > compose.index(_project_calls(run, "up")[0])


def test_default_setup_without_a_leftover_only_asks_docker(sandbox: Sandbox) -> None:
    """P1: with nothing to remove, the one call naming the server is a read-only query."""
    run = _setup(sandbox)

    assert run.returncode == 0, run.output
    assert _project_calls(run, "rm") == []
    named = [call for call in run.calls if any(SERVICE in arg for arg in call)]
    assert named == [
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=obai",
            "--filter",
            f"label=com.docker.compose.service={SERVICE}",
        ]
    ]


def test_removing_a_leftover_without_a_flag_says_to_relaunch(sandbox: Sandbox) -> None:
    """The first run after default-on code removes its container, so running UIs are stale."""
    run = _setup(sandbox, extra_env=LEFTOVER)

    assert run.returncode == 0, run.output
    assert "Options backtesting is now off." in run.output
    assert "obai restart" in run.output


def test_skip_mcp_opt_out_still_removes_the_container(sandbox: Sandbox) -> None:
    """P2 has no --skip-mcp exception: opting out leaves nothing running."""
    run = _setup(sandbox, "--skip-mcp", WITHOUT, seed=f"{OPT_IN_KEY}=true\n", extra_env=LEFTOVER)

    assert run.returncode == 0, run.output
    assert _project_calls(run, "up") == []
    assert len(_project_calls(run, "rm")) == 1
    assert _opt_in_lines(sandbox) == [f"{OPT_IN_KEY}=false"]


def test_default_setup_never_probes_the_server(sandbox: Sandbox) -> None:
    """No health probe hits port 8012 when the server was never started."""
    run = _setup(sandbox)

    probes = [call for call in run.calls if call[0] == "curl"]
    assert probes, "health checks should still probe the default servers"
    assert [call for call in probes if any(":8012" in arg for arg in call)] == []


def test_default_setup_writes_no_opt_in_line(sandbox: Sandbox) -> None:
    """P1: a run without a flag leaves no trace of the component in ~/.obai/.env."""
    run = _setup(sandbox, seed="OPENAI_API_KEY=sk-seeded\n")

    assert run.returncode == 0, run.output
    assert sandbox.env_file.read_text(encoding="utf-8") == "OPENAI_API_KEY=sk-seeded\n"


def test_default_summary_names_the_opt_in_flag(sandbox: Sandbox) -> None:
    """Opted out, the summary hints how to enable instead of listing the server."""
    run = _setup(sandbox)

    assert "obai start --with-options-backtest" in run.output
    assert "localhost:8012/mcp" not in run.output


# --- Opting in ---------------------------------------------------------------


def test_opt_in_activates_the_profile_on_pull_build_and_up(sandbox: Sandbox) -> None:
    """With the flag, every pull, build and up carries the profile and no rm runs."""
    run = _setup(sandbox, WITH)

    assert run.returncode == 0, run.output
    for subcommand in ("pull", "build", "up"):
        calls = _project_calls(run, subcommand)
        assert len(calls) == 1, subcommand
        assert _has_pair(calls[0], *PROFILE), calls[0]
    assert _project_calls(run, "rm") == []


def test_opt_in_probes_the_server_and_lists_it(sandbox: Sandbox) -> None:
    """The server is health-checked like its siblings and named in the summary."""
    run = _setup(sandbox, WITH)

    assert ["curl", "-sf", HEALTH_URL] in run.calls
    assert "options-backtest http://localhost:8012/mcp" in run.output


def test_opt_in_persists_one_canonical_line_owner_only(sandbox: Sandbox) -> None:
    """The flag writes exactly `ENABLE_OPTIONS_STRATEGY=true`, mode 0600."""
    _setup(sandbox, WITH)

    assert _opt_in_lines(sandbox) == [f"{OPT_IN_KEY}=true"]
    assert stat.S_IMODE(sandbox.env_file.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("seed", "expected"),
    [
        (
            'OPENAI_API_KEY=sk-seeded\n# note\nTAVILY_API_KEY="a # b"\n',
            f'OPENAI_API_KEY=sk-seeded\n# note\nTAVILY_API_KEY="a # b"\n{OPT_IN_KEY}=true\n',
        ),
        (
            f"OPENAI_API_KEY=sk-seeded\n{OPT_IN_KEY}=false\nFMP_API_KEY=fmp-seeded\n",
            f"OPENAI_API_KEY=sk-seeded\n{OPT_IN_KEY}=true\nFMP_API_KEY=fmp-seeded\n",
        ),
        # A hand-edited file without a final newline: the append must not glue
        # the opt-in onto the last key, and the update must not drop that key.
        (
            "FMP_API_KEY=fmp-seeded",
            f"FMP_API_KEY=fmp-seeded\n{OPT_IN_KEY}=true\n",
        ),
        (
            f"{OPT_IN_KEY}=false\nFMP_API_KEY=fmp-seeded",
            f"{OPT_IN_KEY}=true\nFMP_API_KEY=fmp-seeded\n",
        ),
    ],
    ids=["append", "update-in-place", "append-no-final-newline", "update-no-final-newline"],
)
def test_opt_in_keeps_every_other_line(sandbox: Sandbox, seed: str, expected: str) -> None:
    """Other keys in a pre-seeded file survive byte-for-byte."""
    run = _setup(sandbox, WITH, seed=seed)

    assert run.returncode == 0, run.output
    assert sandbox.env_file.read_text(encoding="utf-8") == expected


def test_persisted_opt_in_applies_without_a_flag(sandbox: Sandbox) -> None:
    """`obai upgrade` passes no flag; the saved choice still activates the profile."""
    seed = f"{OPT_IN_KEY}=true\n"
    run = _setup(sandbox, seed=seed)

    assert run.returncode == 0, run.output
    assert _has_pair(_project_calls(run, "up")[0], *PROFILE)
    assert _project_calls(run, "rm") == []
    assert sandbox.env_file.read_text(encoding="utf-8") == seed


def test_opt_out_flag_flips_the_saved_choice_and_removes_the_container(
    sandbox: Sandbox,
) -> None:
    """P2: `--without` persists false, drops the profile and removes the container."""
    run = _setup(sandbox, WITHOUT, seed=f"{OPT_IN_KEY}=true\n", extra_env=LEFTOVER)

    assert run.returncode == 0, run.output
    assert _opt_in_lines(sandbox) == [f"{OPT_IN_KEY}=false"]
    assert [args for args in _compose_calls(run) if "--profile" in args] == []
    assert len(_project_calls(run, "rm")) == 1


def test_flipping_off_prints_how_to_reclaim_the_image(sandbox: Sandbox) -> None:
    """Only a run that just turned the server off names the image removal."""
    flipped = _setup(sandbox, WITHOUT, seed=f"{OPT_IN_KEY}=true\n")

    assert "docker image rm ghcr.io/sixteen-dev/obai/options-backtest-server:" in flipped.output
    assert "obai restart" in flipped.output


def test_unchanged_choice_prints_no_flip_notes(sandbox: Sandbox) -> None:
    """A default run changed nothing, so it has nothing to relaunch or reclaim."""
    run = _setup(sandbox)

    assert "docker image rm" not in run.output
    assert "obai restart" not in run.output


@pytest.mark.parametrize(
    ("args", "seed", "expected"),
    [
        ((), None, "false"),
        ((WITH,), None, "true"),
        ((WITHOUT,), f"{OPT_IN_KEY}=true\n", "false"),
    ],
    ids=["default", "opt-in", "flag-overrides-loaded-file"],
)
def test_web_ui_starts_with_this_runs_choice(
    sandbox: Sandbox,
    args: tuple[str, ...],
    seed: str | None,
    expected: str,
) -> None:
    """The hub launched by this run routes by the choice this run made."""
    run = _setup(sandbox, *args, seed=seed)

    assert _web_ui_opt_in(run) == [expected]


# --- Invalid input fails before anything happens -----------------------------


@pytest.mark.parametrize("value", ["1", "yes", "TRUE", ""])
def test_non_canonical_saved_value_fails_before_any_docker_call(
    sandbox: Sandbox,
    value: str,
) -> None:
    """Only `true`/`false` are accepted; anything else names the file and the fix."""
    run = _setup(sandbox, seed=f"{OPT_IN_KEY}={value}\n")

    assert run.returncode == 1, run.output
    assert [call for call in run.calls if call[0] == "docker"] == []
    for fragment in (str(sandbox.env_file), WITH, WITHOUT):
        assert fragment in run.output


@pytest.mark.parametrize(
    "seed",
    [
        f"{OPT_IN_KEY}=true\n{OPT_IN_KEY}=false\n",
        f"  {OPT_IN_KEY}=false\nOPENAI_API_KEY=sk-seeded\n{OPT_IN_KEY}=true\n",
        f"{OPT_IN_KEY} = true\n",
        f"{OPT_IN_KEY} =true\n",
    ],
    ids=["duplicate", "indented-duplicate", "space-around-equals", "space-before-equals"],
)
def test_opt_in_lines_the_readers_would_split_on_fail_before_any_docker_call(
    sandbox: Sandbox,
    seed: str,
) -> None:
    """setup.sh takes the last line and needs `KEY=`; the CLI takes the first and allows spaces."""
    run = _setup(sandbox, seed=seed)

    assert run.returncode == 1, run.output
    assert [call for call in run.calls if call[0] == "docker"] == []
    for fragment in (str(sandbox.env_file), WITH, WITHOUT):
        assert fragment in run.output


def test_indented_canonical_line_is_read_alike_and_honoured(sandbox: Sandbox) -> None:
    """Every reader strips leading space, so one indented line is a valid opt-in."""
    run = _setup(sandbox, seed=f"  {OPT_IN_KEY}=true\n")

    assert run.returncode == 0, run.output
    assert _has_pair(_project_calls(run, "up")[0], *PROFILE)


def test_flag_rewrites_every_line_a_reader_takes_as_the_opt_in(sandbox: Sandbox) -> None:
    """The recovery flag leaves one canonical line where the first opt-in line was."""
    seed = f"\t{OPT_IN_KEY}=true\nOPENAI_API_KEY=sk-seeded\n{OPT_IN_KEY} = true\n"
    run = _setup(sandbox, WITHOUT, seed=seed)

    assert run.returncode == 0, run.output
    expected = f"{OPT_IN_KEY}=false\nOPENAI_API_KEY=sk-seeded\n"
    assert sandbox.env_file.read_text(encoding="utf-8") == expected


def test_non_canonical_exported_value_blames_the_shell_not_the_file(sandbox: Sandbox) -> None:
    """With no line in the file, the value can only have come from a shell export."""
    run = _setup(sandbox, extra_env={OPT_IN_KEY: "True"})

    assert run.returncode == 1, run.output
    assert f"in {sandbox.env_file}" not in run.output
    assert "exported in your shell" in run.output


def test_both_flags_fail(sandbox: Sandbox) -> None:
    """`--with` and `--without` together are ambiguous and abort the run."""
    run = _setup(sandbox, WITH, WITHOUT)

    assert run.returncode == 1, run.output
    assert [call for call in run.calls if call[0] == "docker"] == []
    assert not sandbox.env_file.exists()
    for flag in (WITH, WITHOUT):
        assert flag in run.output


def test_help_lists_every_flag_setup_accepts(sandbox: Sandbox) -> None:
    """`--help` cannot silently truncate a flag the parser accepts."""
    flags = _case_flags("setup.sh")
    run = _run(sandbox, "setup.sh", "--help")

    assert run.returncode == 0, run.output
    assert {WITH, WITHOUT} <= flags
    assert sorted(flag for flag in flags if flag not in run.output) == []


# --- Teardown ------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra_env",
    [{}, {OPT_IN_KEY: "false"}],
    ids=["bare", "opted-out-env"],
)
def test_teardown_always_includes_the_profile(
    sandbox: Sandbox,
    extra_env: dict[str, str],
) -> None:
    """Teardown reads no opt-in: its `down` always sees the optional service."""
    run = _run(sandbox, "teardown.sh", extra_env=extra_env)

    assert run.returncode == 0, run.output
    downs = _project_calls(run, "down")
    assert len(downs) == 1, run.calls
    assert _has_pair(downs[0], *PROFILE)
    assert _has_pair(downs[0], "-f", COMPOSE_FILE)


# --- install.sh forwards the flag to setup.sh (ADR 0004 §4) -------------------

# Every key install.sh would otherwise prompt for. All set, so no prompt reads
# stdin: in a piped install stdin carries the script itself.
_INSTALL_KEYS = {
    "MASSIVE_API_KEY": "massive-test",
    "TAVILY_API_KEY": "tavily-test",
    "EXA_API_KEY": "exa-test",
    "ANTHROPIC_API_KEY": "anthropic-test",
}

# The fake checkout's setup.sh also records the managed-install marker it inherits.
_FAKE_SETUP_BODY = 'printf \'setup-env OBAI_MANAGED=%q\\n\' "${OBAI_MANAGED-unset}" >> "$log"'


def _install(sandbox: Sandbox, *args: str, piped: bool = False) -> Run:
    """Run `install.sh` against an existing checkout whose setup.sh is a fake.

    The pre-created `.git/` sends install.sh down its update path (every `git`
    call is a stub), and the fake `setup.sh` logs the argv it was handed
    instead of setting anything up.

    Args:
        sandbox: Sandbox to run in.
        *args: install.sh arguments.
        piped: Deliver the script on stdin, as `curl … | bash -s -- ARGS` does.

    Returns:
        The run outcome.
    """
    src = sandbox.root / "obai-src"
    (src / ".git").mkdir(parents=True)
    fake_setup = src / "setup.sh"
    fake_setup.write_text(_stub_script(sandbox.log, _FAKE_SETUP_BODY), encoding="utf-8")
    fake_setup.chmod(0o755)
    extra_env = {"OBAI_SRC": str(src), **_INSTALL_KEYS}
    return _run(sandbox, "install.sh", *args, extra_env=extra_env, piped=piped)


def _setup_argvs(run: Run) -> list[list[str]]:
    """The argv of every call install.sh made to the checkout's setup.sh."""
    return [call[1:] for call in run.calls if call[0] == "setup.sh"]


@pytest.mark.parametrize(
    ("args", "piped"),
    [((WITH,), False), ((WITHOUT,), False), ((WITH,), True)],
    ids=["with", "without", "with-piped"],
)
def test_install_forwards_the_opt_in_flag_to_setup(
    sandbox: Sandbox,
    args: tuple[str, ...],
    piped: bool,
) -> None:
    """The flag reaches setup.sh verbatim; setup.sh alone persists and applies it."""
    run = _install(sandbox, *args, piped=piped)

    assert run.returncode == 0, run.output
    assert _setup_argvs(run) == [list(args)]
    assert ["setup-env", "OBAI_MANAGED=1"] in run.calls


def test_install_without_a_flag_passes_setup_no_arguments(sandbox: Sandbox) -> None:
    """No flag, so setup.sh applies the saved choice: off on a fresh install (P1)."""
    run = _install(sandbox)

    assert run.returncode == 0, run.output
    assert _setup_argvs(run) == [[]]
    assert ["setup-env", "OBAI_MANAGED=1"] in run.calls


def test_install_rejects_an_unknown_argument_before_doing_anything(sandbox: Sandbox) -> None:
    """A typo fails fast: no prerequisite check, clone, key prompt or setup."""
    run = _install(sandbox, "--bogus")

    assert run.returncode == 1, run.output
    assert run.calls == []
    assert not sandbox.env_file.exists()
    for fragment in ("--bogus", WITH, WITHOUT):
        assert fragment in run.output
