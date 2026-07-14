"""Lifecycle commands for the OBaI CLI — start, stop, restart, upgrade.

OBaI ships as a git checkout, not a package: the `obai` CLI is installed
editable from `<repo>/src/obai`, so the running module lives inside the
checkout. These commands locate that checkout and drive the existing
`setup.sh` / `teardown.sh` scripts (the single source of truth for Docker
orchestration, CLI reinstall, and the web UI) instead of reimplementing them.

Two checkout shapes are distinguished for `upgrade` only:

* ``managed`` — installed via the one-liner ``install.sh`` at a
  script-controlled location and pinned to a release branch. A clean,
  strictly-behind branch is fast-forwarded to origin.
* ``source`` — a developer's own clone on an arbitrary branch. Never reset,
  reclone, or stash: only a clean, strictly-behind current branch is
  fast-forwarded; anything else refuses with guidance.

`start`, `stop`, and `restart` behave identically for both shapes.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, NoReturn

import typer

# --- Constants ---

_SETUP_SCRIPT = "setup.sh"
_TEARDOWN_SCRIPT = "teardown.sh"
_REPO_MARKERS = ("docker-compose.yml", "VERSION", _SETUP_SCRIPT)
_MANIFEST_PATH = Path.home() / ".obai" / "install-manifest.json"
_DEFAULT_MANAGED_SRC = "~/.local/share/obai"
_MAX_PARENT_WALK = 8
_INSTALLER_HINT = (
    "curl -fsSL https://raw.githubusercontent.com/sixteen-dev/obai/main/install.sh | bash"
)


class RepoNotFoundError(RuntimeError):
    """Raised when the OBaI source checkout cannot be located."""


# --- Failure + process helpers ---


def _fail(message: str) -> NoReturn:
    """Print an error to stderr and exit non-zero."""
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _run(cmd: list[str], cwd: Path) -> int:
    """Run a command with inherited stdio and return its exit code.

    Output is never captured — lifecycle commands are interactive and the
    underlying scripts print colored progress the user should see live.
    """
    logging.getLogger(__name__).debug("running %s in %s", cmd, cwd)
    completed = subprocess.run(cmd, cwd=str(cwd), check=False)  # noqa: S603
    return completed.returncode


def _run_script(repo_root: Path, script: str) -> None:
    """Execute a lifecycle shell script, raising on a non-zero exit."""
    script_path = repo_root / script
    if not script_path.is_file():
        _fail(f"{script} not found in {repo_root} — the checkout looks incomplete.")
    code = _run(["bash", str(script_path)], repo_root)
    if code != 0:
        raise typer.Exit(code)


# --- Repo discovery ---


def _has_markers(path: Path) -> bool:
    """True when every repo marker file is present under path."""
    return all((path / marker).exists() for marker in _REPO_MARKERS)


def _walk_up_for_markers(start: Path) -> Path | None:
    """Walk up from start (bounded) looking for the repo markers."""
    current = start.resolve()
    for _ in range(_MAX_PARENT_WALK):
        if _has_markers(current):
            return current
        if current.parent == current:
            return None
        current = current.parent
    return None


def find_repo_root() -> Path:
    """Locate the OBaI source checkout.

    Resolution order: ``OBAI_REPO`` env override, the installed module's
    location (editable install), then the current directory.

    Returns:
        Absolute path to the checkout root.

    Raises:
        RepoNotFoundError: When no checkout can be located.
    """
    override = os.environ.get("OBAI_REPO")
    if override:
        candidate = Path(override).expanduser()
        if _has_markers(candidate):
            return candidate.resolve()
        raise RepoNotFoundError(
            f"OBAI_REPO={override} is not an OBaI checkout (missing {', '.join(_REPO_MARKERS)})."
        )

    from_module = _walk_up_for_markers(Path(__file__).parent)
    if from_module is not None:
        return from_module

    from_cwd = _walk_up_for_markers(Path.cwd())
    if from_cwd is not None:
        return from_cwd

    raise RepoNotFoundError(
        "Could not find the OBaI source checkout. The start/stop/restart/upgrade "
        "commands need the git checkout that setup.sh installed from. Set "
        "OBAI_REPO=/path/to/obai, or run the command from inside the repo."
    )


def _resolve_repo() -> Path:
    """find_repo_root, converting RepoNotFoundError into a clean CLI error."""
    try:
        return find_repo_root()
    except RepoNotFoundError as exc:
        _fail(str(exc))


# --- Install-mode detection ---


def _read_manifest() -> dict[str, Any] | None:
    """Read the install manifest written by setup.sh, or None if absent/bad."""
    if not _MANIFEST_PATH.is_file():
        return None
    try:
        parsed = json.loads(_MANIFEST_PATH.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logging.getLogger(__name__).warning(
            "Ignoring unreadable install manifest %s: %s", _MANIFEST_PATH, exc
        )
        return None
    return parsed if isinstance(parsed, dict) else None


def _mode_from_manifest(repo_resolved: Path) -> str | None:
    """Return the install mode from the manifest if it names repo_resolved."""
    manifest = _read_manifest()
    if not manifest:
        return None
    manifest_repo = manifest.get("repo")
    if not manifest_repo:
        return None
    if Path(manifest_repo).expanduser().resolve() != repo_resolved:
        return None
    return "managed" if manifest.get("managed") else "source"


def detect_install_mode(repo_root: Path) -> str:
    """Classify the checkout as ``managed`` or ``source``.

    Prefers the setup.sh-written manifest; falls back to the installer's
    fixed default location for installs predating the manifest. Defaults to
    the conservative ``source`` when unsure.

    Args:
        repo_root: The resolved checkout root.

    Returns:
        Either ``"managed"`` or ``"source"``.
    """
    resolved = repo_root.resolve()
    from_manifest = _mode_from_manifest(resolved)
    if from_manifest is not None:
        return from_manifest

    default_src = Path(os.environ.get("OBAI_SRC") or _DEFAULT_MANAGED_SRC).expanduser()
    if resolved == default_src.resolve():
        return "managed"
    return "source"


# --- Git helpers ---


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git command in repo_root, capturing text output."""
    cmd = ["git", "-C", str(repo_root), *args]
    return subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_out(repo_root: Path, *args: str) -> str:
    """Run a git command and return trimmed stdout, failing on error."""
    result = _git(repo_root, *args)
    if result.returncode != 0:
        _fail(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _current_branch(repo_root: Path) -> str:
    """Return the checked-out branch name (or ``HEAD`` when detached)."""
    return _git_out(repo_root, "rev-parse", "--abbrev-ref", "HEAD")


def _version_at(repo_root: Path, ref: str) -> str:
    """Read the VERSION file at a git ref, or ``unknown`` if unavailable."""
    result = _git(repo_root, "show", f"{ref}:VERSION")
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _is_dirty(repo_root: Path) -> bool:
    """True when the working tree has uncommitted changes."""
    result = _git(repo_root, "status", "--porcelain")
    if result.returncode != 0:
        _fail(f"git status failed: {result.stderr.strip()}")
    return bool(result.stdout.strip())


def _upgrade_status(repo_root: Path, branch: str) -> str:
    """Compare HEAD to origin/branch.

    Returns one of ``no_remote``, ``up_to_date``, ``behind``, ``ahead``,
    or ``diverged``.
    """
    remote = f"origin/{branch}"
    if _git(repo_root, "rev-parse", "--verify", "--quiet", remote).returncode != 0:
        return "no_remote"
    head = _git_out(repo_root, "rev-parse", "HEAD")
    upstream = _git_out(repo_root, "rev-parse", remote)
    if head == upstream:
        return "up_to_date"
    if _git(repo_root, "merge-base", "--is-ancestor", head, upstream).returncode == 0:
        return "behind"
    if _git(repo_root, "merge-base", "--is-ancestor", upstream, head).returncode == 0:
        return "ahead"
    return "diverged"


# --- Command implementations ---


def run_start() -> None:
    """Bring up Docker services and the web UI via setup.sh."""
    _run_script(_resolve_repo(), _SETUP_SCRIPT)


def run_stop() -> None:
    """Stop all services (containers, web UI). Images and data are preserved."""
    _run_script(_resolve_repo(), _TEARDOWN_SCRIPT)


def run_restart() -> None:
    """Stop everything, then start it back up."""
    repo_root = _resolve_repo()
    _run_script(repo_root, _TEARDOWN_SCRIPT)
    _run_script(repo_root, _SETUP_SCRIPT)


def run_upgrade(*, assume_yes: bool) -> None:
    """Pull the latest code for the current branch and restart on it."""
    repo_root = _resolve_repo()
    branch = _current_branch(repo_root)
    if branch == "HEAD":
        _fail(
            "Detached HEAD (a pinned version/tag is checked out). Check out a branch "
            "first — e.g. `git checkout main` — then run `obai upgrade`."
        )
    mode = detect_install_mode(repo_root)
    if mode == "source":
        typer.secho(
            f"Source checkout on '{branch}' — your work is never reset, only fast-forwarded.",
            fg=typer.colors.YELLOW,
        )
    fetch = _git(repo_root, "fetch", "--quiet", "origin")
    if fetch.returncode != 0:
        _fail(f"git fetch failed: {fetch.stderr.strip()}")
    status = _upgrade_status(repo_root, branch)
    _dispatch_upgrade(repo_root, branch, mode, status, assume_yes=assume_yes)


def _dispatch_upgrade(
    repo_root: Path,
    branch: str,
    mode: str,
    status: str,
    *,
    assume_yes: bool,
) -> None:
    """Route the upgrade to the right action for (mode, status)."""
    if status == "up_to_date":
        typer.echo(f"Already on the latest '{branch}'. Nothing to upgrade.")
        return
    if status == "no_remote":
        _fail(
            f"Branch '{branch}' has no origin/{branch} to upgrade from. "
            "Run `obai restart` to restart the current version."
        )
    if status == "ahead":
        typer.echo(
            f"Local '{branch}' is ahead of origin — nothing to pull. "
            "Run `obai restart` to restart the current version."
        )
        return
    if status == "diverged":
        _fail_diverged(branch, mode)
    _apply_upgrade(repo_root, branch, assume_yes=assume_yes)


def _fail_diverged(branch: str, mode: str) -> NoReturn:
    """Refuse to upgrade a diverged checkout, with mode-appropriate guidance."""
    if mode == "managed":
        _fail(
            f"Checkout has diverged from origin/{branch} (likely a force-push). "
            f"Re-run the installer to recover:\n  {_INSTALLER_HINT}"
        )
    _fail(
        f"Your branch '{branch}' has diverged from origin/{branch}. Resolve it in "
        "git (merge or rebase), then run `obai restart`."
    )


def _apply_upgrade(repo_root: Path, branch: str, *, assume_yes: bool) -> None:
    """Fast-forward a clean, strictly-behind branch and re-run setup."""
    if _is_dirty(repo_root):
        _fail(
            "Uncommitted changes in the checkout — commit or stash them, then re-run "
            "`obai upgrade`. (Run `obai restart` to restart without upgrading.)"
        )
    current_v = _version_at(repo_root, "HEAD")
    target_v = _version_at(repo_root, f"origin/{branch}")
    if not assume_yes:
        _confirm_upgrade(branch, current_v, target_v)
    typer.echo(f"Upgrading '{branch}': {current_v} -> {target_v} ...")
    _git_out(repo_root, "checkout", "-B", branch, f"origin/{branch}")
    _run_script(repo_root, _SETUP_SCRIPT)


def _confirm_upgrade(branch: str, current_v: str, target_v: str) -> None:
    """Prompt before pulling and restarting; abort cleanly on decline."""
    typer.echo(f"Upgrade '{branch}': {current_v} -> {target_v}")
    typer.echo(
        "This pulls the latest code, re-pulls Docker images at the new version, "
        "and restarts the services and web UI."
    )
    if not typer.confirm("Proceed?"):
        typer.echo("Aborted.")
        raise typer.Exit(0)
