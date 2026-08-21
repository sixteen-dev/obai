#!/usr/bin/env python3
"""Run the paid OBaI E2E regression gate once per hub model/effort combo.

Safety defaults mirror the gate this orchestrates:
  * no mode flag means dry-run (zero paid calls, nothing written),
  * ``--execute`` requires an explicit per-combo model-request cap,
  * the inherited environment must not pin a hub model or effort, and
  * an existing session manifest is refused, never overwritten.

The gate itself owns preflight, snapshotting, budget accounting, and judging.
This orchestrator only pins one hub configuration per run, records the
source-tree digest that proves the runs are comparable, and stops the session
the moment a combo fails.
"""

# This is a standalone operator CLI like the gate's own run_suite.py: its
# progress and plan output belongs on stdout/stderr, not in structlog.
# ruff: noqa: T201

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
E2E_SCRIPT_DIR = SKILL_DIR.parent / "obai-e2e-regression" / "scripts"
REPO_ROOT = SKILL_DIR.parents[2]
# The gate's scripts import each other flat ("from preflight import ..."), so
# their directory has to be importable before the local imports below. Tests
# get the same insertion from conftest; a direct CLI run does not.
if str(E2E_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(E2E_SCRIPT_DIR))

from core_agents.hub_settings import (  # noqa: E402
    HUB_MODELS,
    HUB_REASONING_EFFORTS,
    HubSettingsStore,
)
from preflight import effective_regression_environment  # noqa: E402
from run_one import _hash_tree, runtime_source_paths  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

DEFAULT_RUN_SUITE = E2E_SCRIPT_DIR / "run_suite.py"
SESSION_MANIFEST_NAME = "benchmark_session.json"
RESULTS_NAME = "results.json"
SCHEMA_VERSION = 1
MAX_COMBOS = 8
TIER_CHOICES = ("smoke", "core")
MODEL_ENV = "ORCHESTRATOR_MODEL"
EFFORT_ENV = "ORCHESTRATOR_REASONING_EFFORT"
# Any of these can shadow the per-combo hub pin, so an inherited one is fatal.
BANNED_ENV_SUFFIXES = ("_MODEL", "_REASONING_EFFORT", "_VERBOSITY")
STATUS_PLANNED = "planned"
STATUS_RUNNING = "running"
STATUS_COMPLETE = "complete"
STATUS_FAILED = "failed"
ACTION_FRESH = "fresh"
ACTION_RESUME = "resume"
ACTION_SKIP = "skip"
EXIT_SUCCESS = 0
EXIT_COMBO_FAILURE = 1
EXIT_CONFIGURATION = 2
RUN_SUITE_EXIT_CONFIGURATION = 2
EMPTY_COMBOS_MESSAGE = "--combos must name at least one <model>:<effort> spec"


class BenchmarkError(RuntimeError):
    """A validation or environment failure; the CLI reports it and exits 2."""


@dataclass(frozen=True)
class Combo:
    """One hub configuration under test."""

    model: str
    effort: str

    @property
    def spec(self) -> str:
        """Return the user-facing ``<model>:<effort>`` spec."""
        return f"{self.model}:{self.effort}"

    @property
    def dir_name(self) -> str:
        """Return this combo's run-directory name under the session dir."""
        return f"{self.model}@{self.effort}"


def parse_combos(raw: str) -> list[Combo]:
    """Parse a ``--combos`` value into validated combos.

    Args:
        raw: Comma-separated ``<model>:<effort>`` specs.

    Returns:
        The combos in the order the user listed them.

    Raises:
        BenchmarkError: The list is empty, longer than ``MAX_COMBOS``,
            duplicated, malformed, or outside the shipped hub whitelist.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise BenchmarkError(EMPTY_COMBOS_MESSAGE)
    tokens = [token.strip() for token in raw.split(",") if token.strip()]
    if not tokens:
        raise BenchmarkError(EMPTY_COMBOS_MESSAGE)
    if len(tokens) > MAX_COMBOS:
        message = f"at most {MAX_COMBOS} combos per session, got {len(tokens)}"
        raise BenchmarkError(message)
    combos = [parse_combo(token) for token in tokens]
    specs = [combo.spec for combo in combos]
    duplicates = sorted({spec for spec in specs if specs.count(spec) > 1})
    if duplicates:
        message = f"duplicate combos are refused: {', '.join(duplicates)}"
        raise BenchmarkError(message)
    return combos


def parse_combo(token: str) -> Combo:
    """Parse and whitelist-check one ``<model>:<effort>`` spec.

    Args:
        token: A single combo spec.

    Returns:
        The parsed combo.

    Raises:
        BenchmarkError: The spec is malformed or names an unknown model/effort.
    """
    model, separator, effort = token.partition(":")
    if not separator or not model or not effort or ":" in effort:
        message = f"combo {token!r} must be <model>:<effort>"
        raise BenchmarkError(message)
    if model not in HUB_MODELS:
        message = f"unknown hub model {model!r}; valid: {', '.join(HUB_MODELS)}"
        raise BenchmarkError(message)
    if effort not in HUB_REASONING_EFFORTS:
        message = f"unknown reasoning effort {effort!r}; valid: {', '.join(HUB_REASONING_EFFORTS)}"
        raise BenchmarkError(message)
    return Combo(model=model, effort=effort)


def banned_environment_keys(env: Mapping[str, str]) -> list[str]:
    """Return the sorted inherited keys that could shadow the hub pin.

    Args:
        env: The effective environment the gate would run under.

    Returns:
        Every key ending in a banned suffix, sorted.
    """
    return sorted(key for key in env if key.endswith(BANNED_ENV_SUFFIXES))


def scrubbed_environment() -> dict[str, str]:
    """Resolve the gate's effective environment and refuse hub-pinning keys.

    ``ValueError`` is caught, not just its ``CredentialConfigurationError``
    subclass: the resolver also raises plain ``ValueError`` for a malformed or
    empty ``OPIK_URL``, and letting that escape would report a config typo as
    an unhandled traceback with exit 1 — the status reserved for a paid combo
    that failed.

    Returns:
        The effective environment, guaranteed free of model/effort/verbosity
        overrides, ready to receive this orchestrator's own injected pair.

    Raises:
        BenchmarkError: The environment cannot be resolved, or it already pins
            a model, reasoning effort, or verbosity.
    """
    try:
        effective = effective_regression_environment()
    except ValueError as exc:
        message = f"cannot resolve the regression environment: {exc}"
        raise BenchmarkError(message) from exc
    offenders = banned_environment_keys(effective)
    if offenders:
        message = (
            "the inherited environment already pins hub behaviour: "
            f"{', '.join(offenders)}. Unset these (shell and ~/.obai/.env) so every "
            "combo is pinned only by this orchestrator"
        )
        raise BenchmarkError(message)
    return effective


def child_environment(base: Mapping[str, str], combo: Combo) -> dict[str, str]:
    """Build one combo's child environment from the scrubbed base.

    Args:
        base: The scrubbed effective environment.
        combo: The hub configuration to pin for this run.

    The base already carries the rest of this process's environment (the
    resolver copies ``os.environ``), so nothing else needs forwarding.

    Returns:
        A new mapping with this combo's hub pair injected.
    """
    env = dict(base)
    env[MODEL_ENV] = combo.model
    env[EFFORT_ENV] = combo.effort
    return env


def resolve_incumbent(store: HubSettingsStore) -> Combo:
    """Resolve the shipped hub configuration before any injection happens.

    Args:
        store: The hub settings store to read (``~/.obai/settings.json``).

    Returns:
        The incumbent combo, from the file when present or the shipped default.

    Raises:
        BenchmarkError: The settings file exists but cannot be validated.
    """
    try:
        settings = store.load()
    except ValueError as exc:
        message = f"cannot read hub settings at {store.path}: {exc}"
        raise BenchmarkError(message) from exc
    return Combo(model=settings.hub_model, effort=settings.hub_reasoning_effort)


def compute_source_digest(repo_root: Path) -> str:
    """Digest the runtime source tree the gate binds into every fingerprint.

    Args:
        repo_root: The OBaI repository root.

    Returns:
        The sha256 hex digest of the tracked source, prompt, and config inputs.

    Raises:
        BenchmarkError: ``repo_root`` is not a directory.
    """
    if not repo_root.is_dir():
        message = f"repo root {repo_root} is not a directory"
        raise BenchmarkError(message)
    return _hash_tree(runtime_source_paths(repo_root), root=repo_root)


def new_session_manifest(
    *,
    combos: Sequence[Combo],
    tier: str,
    max_api_calls: int,
    incumbent: Combo,
) -> dict[str, Any]:
    """Build a fresh session manifest with every combo still planned.

    Args:
        combos: The combos to run, in order.
        tier: The gate tier each combo runs.
        max_api_calls: The per-combo between-case model-request cap.
        incumbent: The hub configuration resolved before injection.

    Returns:
        The manifest structure, ready to be written.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": str(uuid.uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "tier": tier,
        "max_api_calls_per_combo": max_api_calls,
        "incumbent": {"model": incumbent.model, "effort": incumbent.effort},
        "incumbent_included": incumbent in combos,
        "combos": [
            {
                "model": combo.model,
                "effort": combo.effort,
                "run_dir": combo.dir_name,
                "status": STATUS_PLANNED,
                "source_digest": None,
            }
            for combo in combos
        ],
    }


def write_session_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    """Rewrite the session manifest atomically.

    Args:
        path: Destination manifest path.
        manifest: The full manifest structure to persist.

    Raises:
        BenchmarkError: The manifest cannot be serialized or written.
    """
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(path)
    except (OSError, TypeError, ValueError) as exc:
        message = f"cannot write session manifest {path}: {exc}"
        raise BenchmarkError(message) from exc
    finally:
        # Only clean up a temp file that was actually created: unlinking one
        # that never existed would raise over the BenchmarkError being handled.
        if tmp_path.is_file():
            tmp_path.unlink(missing_ok=True)


def load_session_manifest(path: Path) -> dict[str, Any]:
    """Load and shape-check an existing session manifest.

    Args:
        path: The manifest path.

    Returns:
        The stored manifest.

    Raises:
        BenchmarkError: The file is unreadable, malformed, or a foreign schema.
    """
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        message = f"cannot read session manifest {path}: {exc}"
        raise BenchmarkError(message) from exc
    if not isinstance(stored, dict) or stored.get("schema_version") != SCHEMA_VERSION:
        message = f"session manifest {path} is not schema_version {SCHEMA_VERSION}"
        raise BenchmarkError(message)
    if not isinstance(stored.get("combos"), list):
        message = f"session manifest {path} has no combos list"
        raise BenchmarkError(message)
    return stored


def validate_resume_manifest(
    stored: Mapping[str, Any],
    *,
    combos: Sequence[Combo],
    tier: str,
    max_api_calls: int,
) -> None:
    """Refuse to resume a session whose plan differs from this invocation.

    Args:
        stored: The manifest loaded from disk.
        combos: The combos requested now.
        tier: The tier requested now.
        max_api_calls: The per-combo cap requested now.

    Raises:
        BenchmarkError: The stored plan and the requested plan disagree.
    """
    stored_specs = [f"{entry.get('model')}:{entry.get('effort')}" for entry in stored["combos"]]
    wanted_specs = [combo.spec for combo in combos]
    if stored_specs != wanted_specs:
        message = (
            f"cannot resume: stored combos {stored_specs} differ from requested {wanted_specs}"
        )
        raise BenchmarkError(message)
    if stored.get("tier") != tier:
        message = f"cannot resume: stored tier {stored.get('tier')!r} != {tier!r}"
        raise BenchmarkError(message)
    if stored.get("max_api_calls_per_combo") != max_api_calls:
        message = (
            "cannot resume: stored --max-api-calls-per-combo "
            f"{stored.get('max_api_calls_per_combo')!r} != {max_api_calls!r}"
        )
        raise BenchmarkError(message)


def prepare_session_manifest(
    *,
    path: Path,
    combos: Sequence[Combo],
    tier: str,
    max_api_calls: int,
    incumbent: Combo,
    resume_session: bool,
) -> dict[str, Any]:
    """Return the manifest to drive this session, refusing silent overwrites.

    Args:
        path: The session manifest path.
        combos: The combos requested now.
        tier: The tier requested now.
        max_api_calls: The per-combo cap requested now.
        incumbent: The hub configuration resolved before injection.
        resume_session: Whether the caller asked to resume.

    Returns:
        A fresh manifest, or the validated stored one when resuming.

    Raises:
        BenchmarkError: A manifest exists without ``--resume-session``, or the
            stored plan does not match this invocation.
    """
    if not path.exists():
        return new_session_manifest(
            combos=combos,
            tier=tier,
            max_api_calls=max_api_calls,
            incumbent=incumbent,
        )
    if not resume_session:
        message = f"{path} already exists; pass --resume-session or choose a fresh --session-dir"
        raise BenchmarkError(message)
    stored = load_session_manifest(path)
    validate_resume_manifest(stored, combos=combos, tier=tier, max_api_calls=max_api_calls)
    return stored


def read_results(run_dir: Path) -> dict[str, Any] | None:
    """Return a gate run's ``results.json``, or None when it was never written.

    Args:
        run_dir: The combo's gate run directory.

    Returns:
        The parsed summary, or None when the file is absent.

    Raises:
        BenchmarkError: The file exists but is unreadable or not a JSON object.
    """
    path = run_dir / RESULTS_NAME
    if not path.is_file():
        return None
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        message = f"cannot read {path}: {exc}"
        raise BenchmarkError(message) from exc
    if not isinstance(summary, dict):
        message = f"{path} is not a JSON object"
        raise BenchmarkError(message)
    return summary


def combo_action(run_dir: Path, *, resume_session: bool) -> str:
    """Decide whether a combo is skipped, resumed, or run fresh.

    An *incomplete* ``results.json`` is refused rather than resumed, which is
    a deliberate departure from the contract's "an incomplete combo dir
    resumes via ``--resume``": the gate publishes ``results.json``
    immutably and its ``--resume`` path replays a published summary instead of
    executing, so respawning it would return the same failure forever. Only a
    run killed before it published a summary can actually be resumed.

    Args:
        run_dir: The combo's gate run directory.
        resume_session: Whether the caller asked to resume.

    Returns:
        One of ``ACTION_SKIP``, ``ACTION_RESUME``, ``ACTION_FRESH``.

    Raises:
        BenchmarkError: An existing ``results.json`` cannot be read, or it is
            published but incomplete, which no resume can advance.
    """
    if not resume_session:
        return ACTION_FRESH
    summary = read_results(run_dir)
    if summary is not None and summary.get("complete") is True:
        return ACTION_SKIP
    if summary is not None:
        message = (
            f"{run_dir / RESULTS_NAME} is published but incomplete "
            f"(status={summary.get('status')!r}, abort_reason={summary.get('abort_reason')!r}). "
            "The gate's --resume replays a published summary instead of executing, and the "
            "file is immutable, so this combo cannot be advanced in place. Rerun the whole "
            "combo into a fresh --session-dir under new authorization"
        )
        raise BenchmarkError(message)
    if run_dir.is_dir() and any(run_dir.iterdir()):
        return ACTION_RESUME
    return ACTION_FRESH


def run_suite_argv(
    *,
    run_suite: Path,
    tier: str,
    max_api_calls: int,
    run_dir: Path,
    resume: bool,
) -> list[str]:
    """Build the exact gate invocation for one combo.

    Args:
        run_suite: Path to the gate's ``run_suite.py`` (a stub in tests).
        tier: The gate tier to run.
        max_api_calls: The between-case model-request cap.
        run_dir: The combo's run directory.
        resume: Whether to append the gate's own ``--resume``.

    Returns:
        The argv list to spawn.
    """
    argv = [
        sys.executable,
        str(run_suite),
        "--execute",
        "--tier",
        tier,
        "--max-api-calls",
        str(max_api_calls),
        "--run-dir",
        str(run_dir),
    ]
    if resume:
        argv.append("--resume")
    return argv


def spawn_run_suite(argv: Sequence[str], *, env: Mapping[str, str], cwd: Path) -> int:
    """Run one gate invocation, streaming its output to this process.

    No timeout is set on purpose: a core run legitimately takes hours, and the
    gate's own budget accounting is the cost backstop.

    Args:
        argv: The invocation built by :func:`run_suite_argv`.
        env: The child environment including this combo's hub pin.
        cwd: Working directory; the gate records git metadata from it and its
            children resolve ``uv run`` against it, so it must be the repo root.

    Returns:
        The child's exit status.
    """
    print(f"$ {' '.join(argv)}", flush=True)
    completed = subprocess.run(list(argv), env=dict(env), cwd=str(cwd), check=False)  # noqa: S603
    return completed.returncode


def combo_failure_reason(returncode: int, run_dir: Path) -> str | None:
    """Explain why a finished combo is unusable, or None when it is usable.

    Completion is read from ``results.json`` rather than the exit status. The
    gate exits 1 whenever any case is still ``needs_semantic_review`` — the
    expected pre-review state of every benchmark run — and 3 for provider or
    harness noise that still produces a complete run. Only a configuration or
    immutability exit (2) is fatal on its own.

    Args:
        returncode: The gate's exit status.
        run_dir: The combo's run directory.

    Returns:
        A human-readable failure reason, or None when the run completed.

    Raises:
        BenchmarkError: ``results.json`` exists but cannot be read.
    """
    if returncode == RUN_SUITE_EXIT_CONFIGURATION:
        return f"run_suite exited {returncode} (configuration or immutability error)"
    summary = read_results(run_dir)
    if summary is None:
        return f"run_suite exited {returncode} without writing {run_dir / RESULTS_NAME}"
    if summary.get("complete") is not True:
        missing = summary.get("missing_case_ids") or []
        return (
            f"run_suite exited {returncode} and did not complete "
            f"(status={summary.get('status')!r}, abort_reason={summary.get('abort_reason')!r}, "
            f"missing={len(missing)} case(s))"
        )
    return None


def run_combo(
    *,
    combo: Combo,
    entry: dict[str, Any],
    manifest: Mapping[str, Any],
    manifest_path: Path,
    run_dir: Path,
    run_suite: Path,
    tier: str,
    max_api_calls: int,
    base_env: Mapping[str, str],
    repo_root: Path,
    resume: bool,
) -> str | None:
    """Run one combo end to end, recording every status transition.

    Args:
        combo: The hub configuration under test.
        entry: This combo's mutable manifest entry.
        manifest: The full session manifest, rewritten at each transition.
        manifest_path: Where the manifest is persisted.
        run_dir: The combo's run directory.
        run_suite: Path to the gate script.
        tier: The gate tier.
        max_api_calls: The per-combo between-case cap.
        base_env: The scrubbed environment to inject into.
        repo_root: Repository root, used for the digest and the child's cwd.
        resume: Whether to append the gate's ``--resume``.

    Returns:
        The failure reason, or None when the combo completed.
    """
    entry["source_digest"] = compute_source_digest(repo_root)
    entry["status"] = STATUS_RUNNING
    write_session_manifest(manifest_path, manifest)
    argv = run_suite_argv(
        run_suite=run_suite,
        tier=tier,
        max_api_calls=max_api_calls,
        run_dir=run_dir,
        resume=resume,
    )
    returncode = spawn_run_suite(argv, env=child_environment(base_env, combo), cwd=repo_root)
    reason = combo_failure_reason(returncode, run_dir)
    entry["status"] = STATUS_FAILED if reason else STATUS_COMPLETE
    write_session_manifest(manifest_path, manifest)
    return reason


def execute_session(
    *,
    combos: Sequence[Combo],
    tier: str,
    max_api_calls: int,
    session_dir: Path,
    run_suite: Path,
    resume_session: bool,
    base_env: Mapping[str, str],
    incumbent: Combo,
    repo_root: Path,
) -> int:
    """Run every combo sequentially, stopping at the first failure.

    Args:
        combos: The combos to run, in order.
        tier: The gate tier.
        max_api_calls: The per-combo between-case cap.
        session_dir: The session directory holding every run directory.
        run_suite: Path to the gate script.
        resume_session: Whether to resume an interrupted session.
        base_env: The scrubbed environment to inject into.
        incumbent: The hub configuration resolved before injection.
        repo_root: Repository root.

    Returns:
        0 when every combo completed, 1 when one failed and stopped the session.
    """
    manifest_path = session_dir / SESSION_MANIFEST_NAME
    manifest = prepare_session_manifest(
        path=manifest_path,
        combos=combos,
        tier=tier,
        max_api_calls=max_api_calls,
        incumbent=incumbent,
        resume_session=resume_session,
    )
    write_session_manifest(manifest_path, manifest)
    for index, combo in enumerate(combos):
        run_dir = session_dir / combo.dir_name
        action = combo_action(run_dir, resume_session=resume_session)
        entry = manifest["combos"][index]
        label = f"[{index + 1}/{len(combos)}] {combo.spec}"
        if action == ACTION_SKIP:
            entry["status"] = STATUS_COMPLETE
            write_session_manifest(manifest_path, manifest)
            print(f"{label}: already complete, skipping")
            continue
        print(f"{label}: {action} -> {run_dir}", flush=True)
        reason = run_combo(
            combo=combo,
            entry=entry,
            manifest=manifest,
            manifest_path=manifest_path,
            run_dir=run_dir,
            run_suite=run_suite,
            tier=tier,
            max_api_calls=max_api_calls,
            base_env=base_env,
            repo_root=repo_root,
            resume=action == ACTION_RESUME,
        )
        if reason is not None:
            print(f"ERROR: combo {combo.spec} failed: {reason}", file=sys.stderr)
            print(f"session stopped; manifest: {manifest_path}", file=sys.stderr)
            return EXIT_COMBO_FAILURE
    print(f"all {len(combos)} combos complete; manifest: {manifest_path}")
    return EXIT_SUCCESS


def plan_lines(
    *,
    combos: Sequence[Combo],
    tier: str,
    session_dir: Path,
    max_api_calls: int | None,
    incumbent: Combo,
) -> list[str]:
    """Render the plan a dry run prints.

    Args:
        combos: The combos that would run, in order.
        tier: The gate tier.
        session_dir: The session directory that would be created.
        max_api_calls: The per-combo cap, or None when not supplied.
        incumbent: The hub configuration resolved before injection.

    Returns:
        The lines to print, in order.
    """
    per_combo = "not set (required for --execute)" if max_api_calls is None else str(max_api_calls)
    total = "not set" if max_api_calls is None else str(max_api_calls * len(combos))
    included = "yes" if incumbent in combos else "no"
    lines = [
        "Benchmark plan (dry run: no paid calls, nothing written)",
        f"  session dir:             {session_dir}",
        f"  tier:                    {tier}",
        f"  combos:                  {len(combos)}",
        f"  max-api-calls per combo: {per_combo}",
        f"  total between-case cap:  {total}",
        f"  incumbent:               {incumbent.spec} (included: {included})",
    ]
    lines.extend(
        f"  {position}. {combo.spec} -> {session_dir / combo.dir_name}"
        for position, combo in enumerate(combos, start=1)
    )
    return lines


def validate_mode(args: argparse.Namespace) -> None:
    """Guard the paid-execution flags before anything is resolved.

    Args:
        args: The parsed command line.

    Raises:
        BenchmarkError: A flag combination would spend money without an
            explicit cap, resume outside execution, or use a missing gate.
    """
    cap = args.max_api_calls_per_combo
    if args.execute and cap is None:
        message = (
            "--execute requires --max-api-calls-per-combo (the gate refuses paid "
            "execution without an explicit between-case cap)"
        )
        raise BenchmarkError(message)
    if cap is not None and cap < 1:
        message = f"--max-api-calls-per-combo must be positive, got {cap}"
        raise BenchmarkError(message)
    if args.resume_session and not args.execute:
        message = "--resume-session requires --execute"
        raise BenchmarkError(message)
    if not args.run_suite.is_file():
        message = f"--run-suite {args.run_suite} is not a file"
        raise BenchmarkError(message)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--combos",
        required=True,
        help="Comma-separated <model>:<effort> specs, 1-8, no duplicates",
    )
    parser.add_argument(
        "--tier",
        required=True,
        choices=TIER_CHOICES,
        help="Gate tier; the live tier is refused because it is a provider canary",
    )
    parser.add_argument("--session-dir", type=Path, required=True)
    parser.add_argument(
        "--max-api-calls-per-combo",
        type=int,
        help="Between-case model-request stop limit handed to each gate run",
    )
    parser.add_argument(
        "--resume-session",
        action="store_true",
        help="Skip completed combos and resume a partially executed one",
    )
    parser.add_argument("--run-suite", type=Path, default=DEFAULT_RUN_SUITE)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="Actually run the paid gate")
    mode.add_argument("--dry-run", action="store_true", help="Plan only; make zero paid calls")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Validate the request, then plan or execute the benchmark session.

    The environment scrub runs in dry-run too, though the contract scopes it
    to execution: the dry run exists to be believed before money is
    authorized, and a plan printed under an environment that would shadow
    every hub pin is a plan for a different benchmark.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        0 when every combo completed (or the plan printed), 1 when a combo
        failed, 2 on a validation or environment error.
    """
    args = build_parser().parse_args(argv)
    try:
        combos = parse_combos(args.combos)
        validate_mode(args)
        base_env = scrubbed_environment()
        incumbent = resolve_incumbent(HubSettingsStore())
        if not args.execute:
            plan = plan_lines(
                combos=combos,
                tier=args.tier,
                session_dir=args.session_dir,
                max_api_calls=args.max_api_calls_per_combo,
                incumbent=incumbent,
            )
            print("\n".join(plan))
            return EXIT_SUCCESS
        return execute_session(
            combos=combos,
            tier=args.tier,
            max_api_calls=args.max_api_calls_per_combo,
            session_dir=args.session_dir,
            run_suite=args.run_suite,
            resume_session=args.resume_session,
            base_env=base_env,
            incumbent=incumbent,
            repo_root=REPO_ROOT,
        )
    except BenchmarkError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_CONFIGURATION


if __name__ == "__main__":
    raise SystemExit(main())
