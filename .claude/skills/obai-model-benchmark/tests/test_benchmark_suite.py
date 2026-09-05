"""Offline tests for the model-benchmark orchestrator.

Every test drives ``benchmark_suite.main`` against a stub ``--run-suite``
executable that records its argv, cwd, and injected environment and writes a
canned ``results.json``. No paid call, no network, no real gate run.
"""

# conftest.py puts this skill's scripts/ and the e2e gate's scripts/ on
# sys.path, which is what makes the flat imports below resolve.

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import benchmark_suite as bs
import pytest
from run_one import _hash_tree, runtime_source_paths

STUB_RUN_SUITE = '''#!/usr/bin/env python3
"""Stand-in for the e2e gate run_suite.py. Never makes a paid call."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--tier")
    parser.add_argument("--max-api-calls", type=int)
    parser.add_argument("--run-dir", type=Path)
    args, _unknown = parser.parse_known_args()

    run_dir = args.run_dir
    session_manifest = run_dir.parent / "benchmark_session.json"
    midrun = None
    if session_manifest.is_file():
        midrun = json.loads(session_manifest.read_text(encoding="utf-8"))
    tracked = ("ORCHESTRATOR_MODEL", "ORCHESTRATOR_REASONING_EFFORT", "UV_CACHE_DIR")
    entry = {
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "env": {key: os.environ.get(key) for key in tracked},
        "run_dir": str(run_dir),
        "midrun_session": midrun,
    }
    with Path(os.environ["STUB_RECORD"]).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\\n")

    exit_code = int(os.environ.get("STUB_EXIT", "0"))
    name = run_dir.name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "stub-" + name}), encoding="utf-8"
    )
    if name in os.environ.get("STUB_NO_RESULTS_FOR", "").split(","):
        return exit_code
    complete = name not in os.environ.get("STUB_INCOMPLETE_FOR", "").split(",")
    summary = {
        "schema_version": 1,
        "run_id": "stub-" + name,
        "mode": "execute",
        "status": "complete" if complete else "incomplete",
        "complete": complete,
        "abort_reason": None if complete else "stub abort",
        "missing_case_ids": [] if complete else ["STUB-1"],
        "between_case_model_request_limit": args.max_api_calls,
        "verdict_counts": {"pass": 1},
        "results": [],
        "exit_code": exit_code,
    }
    (run_dir / "results.json").write_text(json.dumps(summary), encoding="utf-8")
    print(json.dumps(summary))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
'''

SOL = "gpt-5.6-sol"
TERRA = "gpt-5.6-terra"
TWO_COMBOS = f"{SOL}:medium,{TERRA}:high"


@dataclass(frozen=True)
class StubSuite:
    """Paths of the stub gate script and its argv/env record file."""

    path: Path
    record: Path


@pytest.fixture(autouse=True)
def clean_hub_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop ambient hub-pinning variables so the scrub gate is deterministic."""
    for key in list(os.environ):
        if key.endswith(("_MODEL", "_REASONING_EFFORT", "_VERBOSITY")):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def stub_suite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StubSuite:
    """Write the stub gate script and point it at a fresh record file."""
    script = tmp_path / "stub_run_suite.py"
    script.write_text(STUB_RUN_SUITE, encoding="utf-8")
    record = tmp_path / "stub_record.jsonl"
    monkeypatch.setenv("STUB_RECORD", str(record))
    return StubSuite(path=script, record=record)


def read_records(record: Path) -> list[dict]:
    """Return one dict per stub invocation, in invocation order."""
    if not record.is_file():
        return []
    return [json.loads(line) for line in record.read_text(encoding="utf-8").splitlines() if line]


def execute_argv(
    *,
    session_dir: Path,
    stub: StubSuite,
    combos: str = TWO_COMBOS,
    tier: str = "smoke",
    cap: str = "45",
    resume: bool = False,
) -> list[str]:
    """Build a full ``--execute`` argv for ``benchmark_suite.main``."""
    argv = [
        "--combos",
        combos,
        "--tier",
        tier,
        "--session-dir",
        str(session_dir),
        "--max-api-calls-per-combo",
        cap,
        "--run-suite",
        str(stub.path),
        "--execute",
    ]
    if resume:
        argv.append("--resume-session")
    return argv


def read_session(session_dir: Path) -> dict:
    """Load the session manifest written by the orchestrator."""
    return json.loads((session_dir / "benchmark_session.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# combo validation
# --------------------------------------------------------------------------


def test_parse_combos_accepts_valid_specs_in_order() -> None:
    combos = bs.parse_combos(f" {TERRA}:high , {SOL}:medium ")
    assert [combo.spec for combo in combos] == [f"{TERRA}:high", f"{SOL}:medium"]
    assert combos[0].dir_name == f"{TERRA}@high"


def test_parse_combos_rejects_unknown_model() -> None:
    with pytest.raises(bs.BenchmarkError, match="unknown hub model"):
        bs.parse_combos("gpt-4o:medium")


def test_parse_combos_rejects_unknown_effort() -> None:
    with pytest.raises(bs.BenchmarkError, match="unknown reasoning effort"):
        bs.parse_combos(f"{SOL}:blistering")


def test_parse_combos_rejects_duplicates() -> None:
    with pytest.raises(bs.BenchmarkError, match="duplicate"):
        bs.parse_combos(f"{SOL}:medium,{SOL}:medium")


def test_parse_combos_rejects_more_than_eight() -> None:
    specs = ",".join(
        f"{model}:{effort}"
        for model in (SOL, TERRA)
        for effort in ("medium", "high", "xhigh", "max")
    )
    with pytest.raises(bs.BenchmarkError, match="at most 8"):
        bs.parse_combos(specs + f",{SOL}:medium")


def test_parse_combos_rejects_empty() -> None:
    with pytest.raises(bs.BenchmarkError, match="at least one"):
        bs.parse_combos("  ")


def test_parse_combos_rejects_malformed_token() -> None:
    with pytest.raises(bs.BenchmarkError, match="<model>:<effort>"):
        bs.parse_combos(SOL)


def test_live_tier_is_refused_by_the_parser(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        bs.main(["--combos", TWO_COMBOS, "--tier", "live", "--session-dir", str(tmp_path)])
    assert excinfo.value.code == 2


def test_execute_requires_a_cap(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = bs.main(
        ["--combos", TWO_COMBOS, "--tier", "core", "--session-dir", str(tmp_path), "--execute"],
    )
    assert code == 2
    assert "--max-api-calls-per-combo" in capsys.readouterr().err


def test_resume_session_requires_execute(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = bs.main(
        [
            "--combos",
            TWO_COMBOS,
            "--tier",
            "core",
            "--session-dir",
            str(tmp_path),
            "--resume-session",
        ],
    )
    assert code == 2
    assert "--resume-session requires --execute" in capsys.readouterr().err


# --------------------------------------------------------------------------
# environment scrub
# --------------------------------------------------------------------------


def test_scrub_rejects_an_inherited_hub_pinning_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SUPERVISOR_MODEL", "gpt-4o")
    code = bs.main(["--combos", TWO_COMBOS, "--tier", "core", "--session-dir", str(tmp_path)])
    assert code == 2
    assert "SUPERVISOR_MODEL" in capsys.readouterr().err


def test_scrub_rejects_a_preset_injected_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ORCHESTRATOR_REASONING_EFFORT", "high")
    code = bs.main(["--combos", TWO_COMBOS, "--tier", "core", "--session-dir", str(tmp_path)])
    assert code == 2
    assert "ORCHESTRATOR_REASONING_EFFORT" in capsys.readouterr().err


def test_an_unusable_opik_url_is_an_environment_error_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPIK_URL", "localhost:5173")
    code = bs.main(["--combos", TWO_COMBOS, "--tier", "core", "--session-dir", str(tmp_path)])
    assert code == 2
    assert "regression environment" in capsys.readouterr().err


def test_scrub_also_catches_a_key_only_present_in_the_obai_env_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (Path(os.environ["HOME"]) / ".obai" / ".env").write_text(
        "ORCHESTRATOR_MODEL=gpt-5.6-terra\n", encoding="utf-8"
    )
    code = bs.main(["--combos", TWO_COMBOS, "--tier", "core", "--session-dir", str(tmp_path)])
    assert code == 2
    assert "ORCHESTRATOR_MODEL" in capsys.readouterr().err


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------


def test_dry_run_writes_nothing_and_prints_the_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    session = tmp_path / "session"
    code = bs.main(
        [
            "--combos",
            TWO_COMBOS,
            "--tier",
            "core",
            "--session-dir",
            str(session),
            "--max-api-calls-per-combo",
            "187",
        ],
    )
    out = capsys.readouterr().out
    assert code == 0
    assert not session.exists()
    assert f"{SOL}:medium" in out
    assert f"{TERRA}:high" in out
    assert "core" in out
    assert "187" in out
    assert "374" in out
    assert "incumbent" in out


def test_dry_run_without_a_cap_still_validates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = bs.main(
        ["--combos", TWO_COMBOS, "--tier", "smoke", "--session-dir", str(tmp_path / "s")],
    )
    assert code == 0
    assert "not set" in capsys.readouterr().out


def test_dry_run_reports_whether_the_incumbent_is_included(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (Path(os.environ["HOME"]) / ".obai" / "settings.json").write_text(
        json.dumps({"hub_model": TERRA, "hub_reasoning_effort": "high"}), encoding="utf-8"
    )
    code = bs.main(
        ["--combos", TWO_COMBOS, "--tier", "smoke", "--session-dir", str(tmp_path / "s")],
    )
    out = capsys.readouterr().out
    assert code == 0
    assert f"{TERRA}:high" in out
    assert "included: yes" in out


def test_dry_run_flags_an_absent_incumbent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = bs.main(
        [
            "--combos",
            f"{TERRA}:high,{TERRA}:xhigh",
            "--tier",
            "smoke",
            "--session-dir",
            str(tmp_path / "s"),
        ],
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "included: no" in out


# --------------------------------------------------------------------------
# execution
# --------------------------------------------------------------------------


def test_execute_spawns_the_exact_gate_invocation_per_combo(
    tmp_path: Path, stub_suite: StubSuite
) -> None:
    session = tmp_path / "session"
    code = bs.main(execute_argv(session_dir=session, stub=stub_suite))
    entries = read_records(stub_suite.record)
    assert code == 0
    assert [entry["argv"] for entry in entries] == [
        [
            "--execute",
            "--tier",
            "smoke",
            "--max-api-calls",
            "45",
            "--run-dir",
            str(session / f"{SOL}@medium"),
        ],
        [
            "--execute",
            "--tier",
            "smoke",
            "--max-api-calls",
            "45",
            "--run-dir",
            str(session / f"{TERRA}@high"),
        ],
    ]


def test_execute_injects_the_hub_pin_and_runs_from_the_repo_root(
    tmp_path: Path, stub_suite: StubSuite, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    session = tmp_path / "session"
    assert bs.main(execute_argv(session_dir=session, stub=stub_suite)) == 0
    entries = read_records(stub_suite.record)
    assert [entry["env"]["ORCHESTRATOR_MODEL"] for entry in entries] == [SOL, TERRA]
    assert [entry["env"]["ORCHESTRATOR_REASONING_EFFORT"] for entry in entries] == [
        "medium",
        "high",
    ]
    assert entries[0]["env"]["UV_CACHE_DIR"] == str(tmp_path / "uv-cache")
    assert {entry["cwd"] for entry in entries} == {str(bs.REPO_ROOT)}


def test_execute_writes_a_complete_session_manifest(tmp_path: Path, stub_suite: StubSuite) -> None:
    session = tmp_path / "session"
    # Includes the shipped incumbent (terra/max) so the manifest's
    # incumbent_included flag is exercised in its True state.
    combos = f"{SOL}:medium,{TERRA}:max"
    assert bs.main(execute_argv(session_dir=session, stub=stub_suite, combos=combos)) == 0
    manifest = read_session(session)
    assert manifest["schema_version"] == 1
    assert manifest["tier"] == "smoke"
    assert manifest["max_api_calls_per_combo"] == 45
    assert manifest["incumbent"] == {"model": TERRA, "effort": "max"}
    assert manifest["incumbent_included"] is True
    assert [combo["run_dir"] for combo in manifest["combos"]] == [
        f"{SOL}@medium",
        f"{TERRA}@max",
    ]
    assert [combo["status"] for combo in manifest["combos"]] == ["complete", "complete"]
    digests = {combo["source_digest"] for combo in manifest["combos"]}
    assert len(digests) == 1
    assert len(digests.pop()) == 64


def test_session_manifest_is_rewritten_at_every_transition(
    tmp_path: Path, stub_suite: StubSuite
) -> None:
    session = tmp_path / "session"
    assert bs.main(execute_argv(session_dir=session, stub=stub_suite)) == 0
    first, second = read_records(stub_suite.record)
    assert [combo["status"] for combo in first["midrun_session"]["combos"]] == [
        "running",
        "planned",
    ]
    assert [combo["status"] for combo in second["midrun_session"]["combos"]] == [
        "complete",
        "running",
    ]
    assert first["midrun_session"]["combos"][0]["source_digest"] is not None
    assert first["midrun_session"]["combos"][1]["source_digest"] is None
    assert list(session.glob("*.tmp")) == []


def test_execute_stops_the_session_on_a_failed_combo(
    tmp_path: Path,
    stub_suite: StubSuite,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("STUB_NO_RESULTS_FOR", f"{SOL}@medium")
    monkeypatch.setenv("STUB_EXIT", "3")
    session = tmp_path / "session"
    code = bs.main(execute_argv(session_dir=session, stub=stub_suite))
    assert code == 1
    assert len(read_records(stub_suite.record)) == 1
    assert [combo["status"] for combo in read_session(session)["combos"]] == [
        "failed",
        "planned",
    ]
    assert "results.json" in capsys.readouterr().err


def test_an_incomplete_run_is_a_failure_even_when_the_gate_exits_zero(
    tmp_path: Path, stub_suite: StubSuite, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_INCOMPLETE_FOR", f"{SOL}@medium")
    session = tmp_path / "session"
    assert bs.main(execute_argv(session_dir=session, stub=stub_suite)) == 1
    assert read_session(session)["combos"][0]["status"] == "failed"


def test_gate_exit_one_with_a_complete_run_is_success(
    tmp_path: Path, stub_suite: StubSuite, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_EXIT", "1")
    session = tmp_path / "session"
    assert bs.main(execute_argv(session_dir=session, stub=stub_suite)) == 0
    assert [combo["status"] for combo in read_session(session)["combos"]] == [
        "complete",
        "complete",
    ]


def test_gate_configuration_exit_is_always_a_failure(
    tmp_path: Path, stub_suite: StubSuite, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STUB_EXIT", "2")
    session = tmp_path / "session"
    assert bs.main(execute_argv(session_dir=session, stub=stub_suite)) == 1
    assert read_session(session)["combos"][0]["status"] == "failed"
    assert len(read_records(stub_suite.record)) == 1


def test_execute_refuses_an_existing_session_without_resume(
    tmp_path: Path, stub_suite: StubSuite, capsys: pytest.CaptureFixture[str]
) -> None:
    session = tmp_path / "session"
    assert bs.main(execute_argv(session_dir=session, stub=stub_suite)) == 0
    code = bs.main(execute_argv(session_dir=session, stub=stub_suite))
    assert code == 2
    assert "--resume-session" in capsys.readouterr().err


# --------------------------------------------------------------------------
# resume
# --------------------------------------------------------------------------


def test_resume_session_skips_complete_resumes_partial_and_runs_the_rest(
    tmp_path: Path, stub_suite: StubSuite
) -> None:
    session = tmp_path / "session"
    combos = f"{SOL}:medium,{TERRA}:high,{TERRA}:xhigh"
    argv = execute_argv(session_dir=session, stub=stub_suite, combos=combos)
    assert bs.main(argv) == 0
    session_id = read_session(session)["session_id"]
    # Simulate an interruption: combo 2 died before results.json, combo 3 never ran.
    (session / f"{TERRA}@high" / "results.json").unlink()
    for path in (session / f"{TERRA}@xhigh").iterdir():
        path.unlink()
    (session / f"{TERRA}@xhigh").rmdir()
    stub_suite.record.unlink()

    resume_argv = execute_argv(session_dir=session, stub=stub_suite, combos=combos, resume=True)
    assert bs.main(resume_argv) == 0
    entries = read_records(stub_suite.record)
    assert [Path(entry["run_dir"]).name for entry in entries] == [
        f"{TERRA}@high",
        f"{TERRA}@xhigh",
    ]
    assert "--resume" in entries[0]["argv"]
    assert "--resume" not in entries[1]["argv"]
    assert read_session(session)["session_id"] == session_id


def test_resume_refuses_a_combo_whose_incomplete_results_were_published(
    tmp_path: Path,
    stub_suite: StubSuite,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = tmp_path / "session"
    monkeypatch.setenv("STUB_INCOMPLETE_FOR", f"{SOL}@medium")
    assert bs.main(execute_argv(session_dir=session, stub=stub_suite)) == 1
    stub_suite.record.unlink()

    code = bs.main(execute_argv(session_dir=session, stub=stub_suite, resume=True))
    assert code == 2
    assert "fresh --session-dir" in capsys.readouterr().err
    assert read_records(stub_suite.record) == []


def test_resume_session_refuses_a_different_plan(
    tmp_path: Path, stub_suite: StubSuite, capsys: pytest.CaptureFixture[str]
) -> None:
    session = tmp_path / "session"
    assert bs.main(execute_argv(session_dir=session, stub=stub_suite)) == 0
    code = bs.main(
        execute_argv(session_dir=session, stub=stub_suite, combos=f"{SOL}:medium", resume=True),
    )
    assert code == 2
    assert "combos" in capsys.readouterr().err


def test_resume_session_refuses_a_different_cap(
    tmp_path: Path, stub_suite: StubSuite, capsys: pytest.CaptureFixture[str]
) -> None:
    session = tmp_path / "session"
    assert bs.main(execute_argv(session_dir=session, stub=stub_suite)) == 0
    code = bs.main(
        execute_argv(session_dir=session, stub=stub_suite, cap="46", resume=True),
    )
    assert code == 2
    assert "max-api-calls-per-combo" in capsys.readouterr().err


# --------------------------------------------------------------------------
# source-equality evidence
# --------------------------------------------------------------------------


def test_source_digest_matches_the_gate_helper(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "src" / "thing.py").write_text("x = 1\n", encoding="utf-8")
    expected = _hash_tree(runtime_source_paths(repo_root), root=repo_root)
    assert bs.compute_source_digest(repo_root) == expected
    assert len(expected) == 64


def test_source_digest_rejects_a_missing_repo_root(tmp_path: Path) -> None:
    with pytest.raises(bs.BenchmarkError, match="not a directory"):
        bs.compute_source_digest(tmp_path / "nope")


def test_the_default_run_suite_is_the_e2e_gate() -> None:
    assert bs.DEFAULT_RUN_SUITE.is_file()
    assert bs.DEFAULT_RUN_SUITE.name == "run_suite.py"
    assert str(bs.E2E_SCRIPT_DIR) in sys.path
