"""Tests for the lifecycle commands (start/stop/restart/upgrade helpers).

Covers the three risky areas of clients/cli/lifecycle.py:
* repo discovery (env override, walk-up, failure),
* managed-vs-source install classification,
* the upgrade decision table and its safety guards (never touch a dirty or
  diverged tree; fast-forward only a clean, strictly-behind branch).

Git plumbing (`_upgrade_status`, `_apply_upgrade`) is exercised against real
temporary repositories with a local bare "origin" so the states are genuine.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import typer

from clients.cli import lifecycle

# --- Git test scaffolding ---

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _run_git(cwd: Path, *args: str) -> None:
    """Run a git command that must succeed, quietly."""
    subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**_GIT_ENV, "PATH": _path_env()},
    )


def _path_env() -> str:
    import os

    return os.environ.get("PATH", "")


def _commit_version(repo: Path, version: str, message: str) -> None:
    """Write VERSION and commit it."""
    (repo / "VERSION").write_text(version + "\n")
    _run_git(repo, "add", "VERSION")
    _run_git(repo, "commit", "-m", message)


def _make_repo_with_origin(tmp_path: Path, name: str) -> Path:
    """Create a repo on `main` tracking a local bare origin, with one commit."""
    bare = tmp_path / f"{name}-origin.git"
    repo = tmp_path / name
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": _path_env()},
    )
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": _path_env()},
    )
    _run_git(repo, "remote", "add", "origin", str(bare))
    _commit_version(repo, "1.0.0", "initial")
    _run_git(repo, "branch", "-M", "main")
    _run_git(repo, "push", "-u", "origin", "main")
    return repo


def _advance_origin(tmp_path: Path, repo: Path, version: str) -> None:
    """Push a new commit to origin/main from a throwaway clone."""
    bare = tmp_path / f"{repo.name}-origin.git"
    clone = tmp_path / f"{repo.name}-clone"
    subprocess.run(
        ["git", "clone", str(bare), str(clone)],
        check=True,
        capture_output=True,
        text=True,
        env={"PATH": _path_env()},
    )
    _run_git(clone, "checkout", "-B", "main", "origin/main")
    _commit_version(clone, version, "remote advance")
    _run_git(clone, "push", "origin", "main")


# --- _has_markers / _walk_up_for_markers ---


def _make_checkout(root: Path) -> Path:
    """Create a directory tree that looks like an OBaI checkout."""
    root.mkdir(parents=True, exist_ok=True)
    for marker in lifecycle._REPO_MARKERS:
        (root / marker).write_text("x")
    return root


class TestMarkers:
    """Marker detection and bounded upward search."""

    def test_has_markers_true_when_all_present(self, tmp_path: Path) -> None:
        assert lifecycle._has_markers(_make_checkout(tmp_path / "repo"))

    def test_has_markers_false_when_one_missing(self, tmp_path: Path) -> None:
        root = _make_checkout(tmp_path / "repo")
        (root / lifecycle._REPO_MARKERS[0]).unlink()
        assert not lifecycle._has_markers(root)

    def test_walk_up_finds_root_from_nested_dir(self, tmp_path: Path) -> None:
        root = _make_checkout(tmp_path / "repo")
        nested = root / "src" / "obai" / "clients" / "cli"
        nested.mkdir(parents=True)
        assert lifecycle._walk_up_for_markers(nested) == root.resolve()

    def test_walk_up_returns_none_when_absent(self, tmp_path: Path) -> None:
        empty = tmp_path / "nowhere"
        empty.mkdir()
        assert lifecycle._walk_up_for_markers(empty) is None


# --- find_repo_root ---


class TestFindRepoRoot:
    """Resolution order and failure of find_repo_root."""

    def test_env_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = _make_checkout(tmp_path / "repo")
        monkeypatch.setenv("OBAI_REPO", str(root))
        assert lifecycle.find_repo_root() == root.resolve()

    def test_env_override_without_markers_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OBAI_REPO", str(tmp_path / "empty"))
        with pytest.raises(lifecycle.RepoNotFoundError):
            lifecycle.find_repo_root()

    def test_raises_when_nothing_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OBAI_REPO", raising=False)
        monkeypatch.setattr(lifecycle, "_walk_up_for_markers", lambda _start: None)
        with pytest.raises(lifecycle.RepoNotFoundError):
            lifecycle.find_repo_root()

    def test_resolve_repo_converts_error_to_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom() -> Path:
            raise lifecycle.RepoNotFoundError("nope")

        monkeypatch.setattr(lifecycle, "find_repo_root", _boom)
        with pytest.raises(typer.Exit):
            lifecycle._resolve_repo()


# --- detect_install_mode ---


class TestDetectInstallMode:
    """Managed vs source classification from the manifest and path fallback."""

    def _write_manifest(self, path: Path, managed: bool, repo: Path) -> None:
        import json

        path.write_text(json.dumps({"managed": managed, "repo": str(repo)}))

    def test_manifest_managed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        manifest = tmp_path / "manifest.json"
        self._write_manifest(manifest, managed=True, repo=repo)
        monkeypatch.setattr(lifecycle, "_MANIFEST_PATH", manifest)
        assert lifecycle.detect_install_mode(repo) == "managed"

    def test_manifest_source(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        manifest = tmp_path / "manifest.json"
        self._write_manifest(manifest, managed=False, repo=repo)
        monkeypatch.setattr(lifecycle, "_MANIFEST_PATH", manifest)
        assert lifecycle.detect_install_mode(repo) == "source"

    def test_path_fallback_managed_at_default_src(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "managed-src"
        repo.mkdir()
        monkeypatch.setattr(lifecycle, "_MANIFEST_PATH", tmp_path / "missing.json")
        monkeypatch.setenv("OBAI_SRC", str(repo))
        assert lifecycle.detect_install_mode(repo) == "managed"

    def test_defaults_to_source(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = tmp_path / "somewhere"
        repo.mkdir()
        monkeypatch.setattr(lifecycle, "_MANIFEST_PATH", tmp_path / "missing.json")
        monkeypatch.setenv("OBAI_SRC", str(tmp_path / "other"))
        assert lifecycle.detect_install_mode(repo) == "source"

    def test_corrupt_manifest_falls_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        repo.mkdir()
        manifest = tmp_path / "manifest.json"
        manifest.write_text("{not json")
        monkeypatch.setattr(lifecycle, "_MANIFEST_PATH", manifest)
        monkeypatch.setenv("OBAI_SRC", str(tmp_path / "other"))
        assert lifecycle.detect_install_mode(repo) == "source"


# --- _upgrade_status (real git) ---


class TestUpgradeStatus:
    """Genuine HEAD-vs-origin comparisons across a bare remote."""

    def test_up_to_date(self, tmp_path: Path) -> None:
        repo = _make_repo_with_origin(tmp_path, "utd")
        assert lifecycle._upgrade_status(repo, "main") == "up_to_date"

    def test_behind(self, tmp_path: Path) -> None:
        repo = _make_repo_with_origin(tmp_path, "behind")
        _advance_origin(tmp_path, repo, "1.1.0")
        _run_git(repo, "fetch", "origin")
        assert lifecycle._upgrade_status(repo, "main") == "behind"
        assert lifecycle._version_at(repo, "origin/main") == "1.1.0"

    def test_ahead(self, tmp_path: Path) -> None:
        repo = _make_repo_with_origin(tmp_path, "ahead")
        _commit_version(repo, "1.1.0", "local only")
        _run_git(repo, "fetch", "origin")
        assert lifecycle._upgrade_status(repo, "main") == "ahead"

    def test_diverged(self, tmp_path: Path) -> None:
        repo = _make_repo_with_origin(tmp_path, "div")
        _advance_origin(tmp_path, repo, "1.1.0")
        _commit_version(repo, "2.0.0", "conflicting local")
        _run_git(repo, "fetch", "origin")
        assert lifecycle._upgrade_status(repo, "main") == "diverged"

    def test_no_remote_branch(self, tmp_path: Path) -> None:
        repo = _make_repo_with_origin(tmp_path, "noremote")
        _run_git(repo, "checkout", "-b", "feature")
        assert lifecycle._upgrade_status(repo, "feature") == "no_remote"

    def test_is_dirty_and_current_branch(self, tmp_path: Path) -> None:
        repo = _make_repo_with_origin(tmp_path, "dirty")
        assert lifecycle._current_branch(repo) == "main"
        assert not lifecycle._is_dirty(repo)
        (repo / "VERSION").write_text("9.9.9\n")
        assert lifecycle._is_dirty(repo)


# --- _dispatch_upgrade decision table ---


class TestDispatchUpgrade:
    """Each (mode, status) routes to the correct action or refusal."""

    def _spy_apply(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool]]:
        calls: list[tuple[str, bool]] = []

        def _fake(repo_root: Path, branch: str, *, assume_yes: bool) -> None:
            calls.append((branch, assume_yes))

        monkeypatch.setattr(lifecycle, "_apply_upgrade", _fake)
        return calls

    def test_up_to_date_does_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls = self._spy_apply(monkeypatch)
        lifecycle._dispatch_upgrade(tmp_path, "main", "managed", "up_to_date", assume_yes=False)
        assert not calls
        assert "latest" in capsys.readouterr().out

    def test_ahead_does_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        calls = self._spy_apply(monkeypatch)
        lifecycle._dispatch_upgrade(tmp_path, "main", "source", "ahead", assume_yes=False)
        assert not calls
        assert "ahead" in capsys.readouterr().out

    def test_behind_applies(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._spy_apply(monkeypatch)
        lifecycle._dispatch_upgrade(tmp_path, "main", "managed", "behind", assume_yes=True)
        assert calls == [("main", True)]

    def test_no_remote_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self._spy_apply(monkeypatch)
        with pytest.raises(typer.Exit):
            lifecycle._dispatch_upgrade(tmp_path, "main", "source", "no_remote", assume_yes=True)

    def test_diverged_managed_mentions_installer(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._spy_apply(monkeypatch)
        with pytest.raises(typer.Exit):
            lifecycle._dispatch_upgrade(tmp_path, "main", "managed", "diverged", assume_yes=True)
        assert "install.sh" in capsys.readouterr().err

    def test_diverged_source_mentions_rebase(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._spy_apply(monkeypatch)
        with pytest.raises(typer.Exit):
            lifecycle._dispatch_upgrade(tmp_path, "dev", "source", "diverged", assume_yes=True)
        assert "diverged" in capsys.readouterr().err


# --- _apply_upgrade guards (real git) ---


class TestApplyUpgrade:
    """The fast-forward path refuses dirty trees and honours confirmation."""

    def test_dirty_tree_refuses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _make_repo_with_origin(tmp_path, "apply-dirty")
        (repo / "VERSION").write_text("dirty\n")
        ran: list[str] = []
        monkeypatch.setattr(lifecycle, "_run_script", lambda root, script: ran.append(script))
        with pytest.raises(typer.Exit):
            lifecycle._apply_upgrade(repo, "main", assume_yes=True)
        assert not ran

    def test_decline_confirmation_aborts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make_repo_with_origin(tmp_path, "apply-decline")
        _advance_origin(tmp_path, repo, "1.1.0")
        _run_git(repo, "fetch", "origin")
        ran: list[str] = []
        monkeypatch.setattr(lifecycle, "_run_script", lambda root, script: ran.append(script))
        monkeypatch.setattr(typer, "confirm", lambda _prompt: False)
        with pytest.raises(typer.Exit):
            lifecycle._apply_upgrade(repo, "main", assume_yes=False)
        assert not ran

    def test_fast_forwards_and_runs_setup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make_repo_with_origin(tmp_path, "apply-ff")
        _advance_origin(tmp_path, repo, "1.1.0")
        _run_git(repo, "fetch", "origin")
        ran: list[str] = []
        monkeypatch.setattr(lifecycle, "_run_script", lambda root, script: ran.append(script))
        lifecycle._apply_upgrade(repo, "main", assume_yes=True)
        assert ran == [lifecycle._SETUP_SCRIPT]
        assert (repo / "VERSION").read_text().strip() == "1.1.0"


class TestRunUpgradeGuards:
    """run_upgrade refuses states that would corrupt the checkout."""

    def test_detached_head_refuses(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = _make_repo_with_origin(tmp_path, "detached")
        _run_git(repo, "checkout", "--detach", "HEAD")
        ran: list[str] = []
        monkeypatch.setattr(lifecycle, "find_repo_root", lambda: repo)
        monkeypatch.setattr(lifecycle, "_run_script", lambda root, script: ran.append(script))
        with pytest.raises(typer.Exit):
            lifecycle.run_upgrade(assume_yes=True)
        assert not ran
