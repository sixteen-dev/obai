"""Native LEAN runs of the differential harness (ADR 0002 §12 as amended by §16 decision 3).

The toolchain is a LEAN checkout built at a pinned commit (``LEAN_ROOT``) and a .NET SDK
(``DOTNET_ROOT``, else ``dotnet`` on ``PATH``), both outside the repository. ``preflight`` skips
the calling test when either is missing and records the LEAN commit and the SDK version.
``build_algorithm`` compiles a C# project against the built LEAN assemblies in a temporary
directory. ``run_algorithm`` lays out a workspace, runs the launcher and returns the algorithm's
records with the export digests:

- ``workspace/data/``: copies of ``$LEAN_ROOT/Data/market-hours`` and ``Data/symbol-properties``
  plus the export, whose sha256 per archive is recorded;
- ``workspace/results/``: LEAN's results folder, holding ``replay_input.json`` (written here),
  ``replay.jsonl`` (written by the algorithm) and LEAN's own result files;
- ``workspace/lean.log``: the launcher's stdout and stderr.

The launcher runs as ``dotnet QuantConnect.Lean.Launcher.dll --config config.json`` with cwd
``$LEAN_ROOT/Launcher/bin/Debug`` and the checkout's config.json unmodified; the settings the
harness needs are LEAN's own command-line overrides (``LeanArgumentParser``). Every process runs
in its own session under a 600 s timeout, and its process group is killed in ``finally``.

An algorithm's contract (the file names of ADR 0002 §12 step 4): read ``replay_input.json`` from
LEAN's results folder (``Globals.ResultsDestinationFolder``) and append one JSON object per line
to ``replay.jsonl`` there, the last of kind ``"end"`` (written in ``OnEndOfAlgorithm``).
A run counts only when LEAN reports it ``Completed`` without a runtime error and that end record
exists; anything else raises ``LeanRunError`` with the tail of the log.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import signal
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Final

import pytest

from .export import LeanArchive, archive_bytes

LEAN_TIMEOUT_S: Final = 600
LAUNCHER_DIR: Final = Path("Launcher/bin/Debug")
LAUNCHER_DLL: Final = LAUNCHER_DIR / "QuantConnect.Lean.Launcher.dll"
INPUT_NAME: Final = "replay_input.json"
OUTPUT_NAME: Final = "replay.jsonl"
REFERENCE_DATA: Final = ("market-hours", "symbol-properties")
"""Folders of ``$LEAN_ROOT/Data`` copied into every data folder."""
_QUERY_TIMEOUT_S: Final = 60
_LOG_TAIL_LINES: Final = 40
_DOTNET_QUIET: Final = MappingProxyType(
    {
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_NOLOGO": "1",
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
    }
)

type JsonRecord = dict[str, Any]


class LeanRunError(RuntimeError):
    """A LEAN build or run failed; the message ends with the tail of its log."""


@dataclass(frozen=True, slots=True)
class LeanToolchain:
    """A located, versioned LEAN build and .NET SDK.

    Attributes:
        lean_root: The LEAN checkout.
        dotnet: The ``dotnet`` executable.
        environment: Environment of every child process: the caller's plus telemetry opt-outs.
        lean_commit: ``git rev-parse HEAD`` of the checkout.
        sdk_version: ``dotnet --version``.

    """

    lean_root: Path
    dotnet: Path
    environment: Mapping[str, str]
    lean_commit: str
    sdk_version: str

    @property
    def launcher_dir(self) -> Path:
        """Return the launcher's build folder, the cwd of every run."""
        return self.lean_root / LAUNCHER_DIR


@dataclass(frozen=True, slots=True)
class LeanRun:
    """The provenance and output of one LEAN run.

    Attributes:
        lean_commit: LEAN commit the run used.
        sdk_version: .NET SDK version the run used.
        export_digests: (archive path, sha256 of its bytes) per exported archive, in order.
        records: The algorithm's JSON records in output order; the last is the end record.
        log: The launcher's log file.

    """

    lean_commit: str
    sdk_version: str
    export_digests: tuple[tuple[str, str], ...]
    records: tuple[JsonRecord, ...]
    log: Path


def preflight(environ: Mapping[str, str]) -> LeanToolchain:
    """Locate the toolchain or skip the calling test, then record its versions.

    Args:
        environ: The environment to read (``os.environ`` in the tests).

    Returns:
        The toolchain.

    Raises:
        pytest.skip.Exception: If ``LEAN_ROOT`` is unset, its launcher is not built, or no
            ``dotnet`` is found under ``DOTNET_ROOT`` (when set) or on ``PATH``.
        LeanRunError: If the present toolchain cannot report its commit or version.

    """
    root_text = environ.get("LEAN_ROOT", "")
    if not root_text:
        pytest.skip("LEAN_ROOT is not set: the LEAN differential needs a built LEAN checkout")
    lean_root = Path(root_text)
    if not (lean_root / LAUNCHER_DLL).is_file():
        pytest.skip(f"no built LEAN launcher at {lean_root / LAUNCHER_DLL}")
    dotnet = _find_dotnet(environ)
    if dotnet is None:
        pytest.skip("no .NET SDK: set DOTNET_ROOT or put dotnet on PATH")
    environment = MappingProxyType({**environ, **_DOTNET_QUIET})
    git = shutil.which("git", path=environ.get("PATH", ""))
    if git is None:
        raise LeanRunError("git is not on PATH: the LEAN commit cannot be recorded")
    commit = _query([git, "-C", str(lean_root), "rev-parse", "HEAD"], environment)
    version = _query([str(dotnet), "--version"], environment)
    return LeanToolchain(lean_root, dotnet, environment, commit, version)


def build_algorithm(toolchain: LeanToolchain, project_dir: Path, build_dir: Path) -> Path:
    """Compile a C# algorithm project against the built LEAN assemblies.

    The project folder (one ``.csproj``, which references ``$(LeanBin)``) is copied to
    ``build_dir/src`` and built there in Release, so the repository never holds build output.

    Args:
        toolchain: The toolchain.
        project_dir: Folder with the ``.csproj`` and its sources.
        build_dir: Empty folder for the copy, the output and ``build.log``.

    Returns:
        The compiled ``<project name>.dll``.

    Raises:
        ValueError: If ``project_dir`` does not hold exactly one ``.csproj``.
        LeanRunError: If the build fails, times out or produces no assembly.

    """
    projects = sorted(project_dir.glob("*.csproj"))
    if len(projects) != 1:
        raise ValueError(f"{project_dir} must hold exactly one .csproj, found {projects}")
    source = build_dir / "src"
    shutil.copytree(project_dir, source, ignore=shutil.ignore_patterns("bin", "obj"))
    output = build_dir / "bin"
    args = [
        str(toolchain.dotnet),
        "build",
        str(source / projects[0].name),
        "--configuration",
        "Release",
        "--output",
        str(output),
        f"-p:LeanBin={toolchain.launcher_dir}",
        "-p:UseSharedCompilation=false",
        "-nodeReuse:false",
    ]
    _run(args, cwd=source, environment=toolchain.environment, log=build_dir / "build.log")
    dll = output / f"{projects[0].stem}.dll"
    if not dll.is_file():
        raise LeanRunError(f"the build of {projects[0].name} produced no {dll}")
    return dll


def run_algorithm(  # noqa: PLR0913 — the run's inputs, each passed explicitly
    toolchain: LeanToolchain,
    dll: Path,
    *,
    type_name: str,
    archives: Sequence[LeanArchive],
    algorithm_input: Mapping[str, object],
    workspace: Path,
) -> LeanRun:
    """Run a compiled algorithm on an export and return its records.

    Args:
        toolchain: The toolchain.
        dll: The compiled algorithm assembly.
        type_name: The algorithm class LEAN loads.
        archives: The export, written under the data folder.
        algorithm_input: Written as ``replay_input.json`` (sorted keys).
        workspace: Empty folder for the data folder, the results folder and the log.

    Returns:
        The run.

    Raises:
        LeanRunError: If the launcher fails or times out, LEAN does not report the algorithm
            ``Completed`` without a runtime error, or the output lacks its end record.
        ValueError: If an archive path is absolute or leaves the data folder.

    """
    data = workspace / "data"
    results = workspace / "results"
    digests = _write_data(toolchain, archives, data)
    results.mkdir(parents=True)
    (results / INPUT_NAME).write_text(json.dumps(algorithm_input, sort_keys=True), "utf-8")
    log = workspace / "lean.log"
    args = [
        str(toolchain.dotnet),
        LAUNCHER_DLL.name,
        "--config",
        "config.json",
        "--environment",
        "backtesting",
        "--algorithm-type-name",
        type_name,
        "--algorithm-language",
        "CSharp",
        "--algorithm-location",
        str(dll),
        "--data-folder",
        f"{data}/",
        "--results-destination-folder",
        str(results),
        "--close-automatically",
        "true",
    ]
    _run(args, cwd=toolchain.launcher_dir, environment=toolchain.environment, log=log)
    _require_completed(results / f"{type_name}.json", log)
    records = _read_records(results / OUTPUT_NAME, log)
    return LeanRun(toolchain.lean_commit, toolchain.sdk_version, digests, records, log)


def _find_dotnet(environ: Mapping[str, str]) -> Path | None:
    """Return ``$DOTNET_ROOT/dotnet`` when ``DOTNET_ROOT`` is set, else ``dotnet`` on PATH."""
    root = environ.get("DOTNET_ROOT", "")
    if root:
        candidate = Path(root) / "dotnet"
        return candidate if candidate.is_file() else None
    found = shutil.which("dotnet", path=environ.get("PATH", ""))
    return Path(found) if found else None


def _query(args: list[str], environment: Mapping[str, str]) -> str:
    """Return a short command's stripped stdout; a nonzero exit raises."""
    completed = subprocess.run(  # noqa: S603 — a fixed argument list, no shell
        args,
        capture_output=True,
        text=True,
        env=dict(environment),
        timeout=_QUERY_TIMEOUT_S,
        check=False,
    )
    if completed.returncode != 0:
        raise LeanRunError(f"{args} exited {completed.returncode}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _write_data(
    toolchain: LeanToolchain, archives: Sequence[LeanArchive], data: Path
) -> tuple[tuple[str, str], ...]:
    """Copy LEAN's reference data, write the export and return its digests."""
    for name in REFERENCE_DATA:
        shutil.copytree(toolchain.lean_root / "Data" / name, data / name)
    digests: list[tuple[str, str]] = []
    for archive in archives:
        relative = PurePosixPath(archive.path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"archive path {archive.path!r} must stay inside the data folder")
        content = archive_bytes(archive)
        target = data.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        digests.append((archive.path, hashlib.sha256(content).hexdigest()))
    return tuple(digests)


def _run(args: Sequence[str], *, cwd: Path, environment: Mapping[str, str], log: Path) -> None:
    """Run one process in its own session, output to ``log``; kill its group in ``finally``."""
    with log.open("wb") as sink:
        process = subprocess.Popen(  # noqa: S603 — a fixed argument list, no shell
            args,
            cwd=cwd,
            env=dict(environment),
            stdout=sink,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=LEAN_TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            raise LeanRunError(f"{args[:2]} exceeded {LEAN_TIMEOUT_S} s{_tail(log)}") from e
        finally:
            _kill_group(process)
    if code != 0:
        raise LeanRunError(f"{args[:2]} exited {code}{_tail(log)}")


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """SIGKILL the process's group and reap its leader; an emptied group has nothing to kill."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return  # every process of the group has exited and been reaped
    process.wait()


def _require_completed(result_file: Path, log: Path) -> None:
    """Refuse a run LEAN did not report ``Completed`` or reported with a runtime error."""
    if not result_file.is_file():
        raise LeanRunError(f"LEAN wrote no {result_file.name}{_tail(log)}")
    state = json.loads(result_file.read_text("utf-8"))["state"]
    if state["Status"] != "Completed" or state["RuntimeError"]:
        raise LeanRunError(
            f"LEAN status {state['Status']}, runtime error {state['RuntimeError']!r}{_tail(log)}"
        )


def _read_records(output: Path, log: Path) -> tuple[JsonRecord, ...]:
    """Return the algorithm's records; a missing file or end record raises."""
    if not output.is_file():
        raise LeanRunError(f"the algorithm wrote no {output.name}{_tail(log)}")
    lines = output.read_text("utf-8").splitlines()
    records = tuple(json.loads(line) for line in lines)
    if not records or records[-1].get("kind") != "end":
        raise LeanRunError(f"{output.name} has no final end record{_tail(log)}")
    return records


def _tail(log: Path) -> str:
    lines = log.read_text("utf-8", errors="replace").splitlines()[-_LOG_TAIL_LINES:]
    return "\n--- " + str(log) + " (tail) ---\n" + "\n".join(lines)
