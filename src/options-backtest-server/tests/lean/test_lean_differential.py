"""The LEAN differential: L1-L3 replayed natively in LEAN and reconciled exactly (ADR 0002 §12).

Each scenario is a golden scenario (L1 = G01, L2 = G02, L3 = G05) that our simulator runs
through ``tests/e2e/runner.py``; its dataset is exported (``export.py``), the run is replayed by
``replay/ReplayAlgorithm.cs`` and reconciled by ``reconcile.py``: every comparison's difference
is zero or exactly the one a known mismatch predicts, and any other difference fails.

The replay project compiles E1's probe sources in place; ``runner.build_algorithm`` builds a
copy of the project folder alone, so the probe folder's path reaches MSBuild through the
``OBAI_LEAN_PROBES`` environment variable of the build.

The data of ``docs/lean-differential.md`` is written per scenario to
``$LEAN_REPORT_DIR/{L1,L2,L3}.json`` when ``LEAN_REPORT_DIR`` is set, else to the test's
temporary directory: the golden id, the dataset's manifest id, our artifact digests, the LEAN
commit, .NET SDK version, export digests and every comparison with its prediction and verdict.
"""

import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from types import MappingProxyType
from typing import Final

import pytest
from e2e.runner import SCENARIOS_DIR, load_scenario, run_scenario

from options_backtest.data.store import read_dataset
from options_backtest.models.artifacts import ArtifactBundle

from .export import export
from .reconcile import REPLAY_TYPE_NAME, Comparison, reconcile, replay_input, unexplained
from .runner import LeanRun, LeanToolchain, build_algorithm, preflight, run_algorithm

pytestmark = pytest.mark.lean

LEAN_DIR: Final = Path(__file__).parent
SCENARIOS: Final = (("L1", "G01"), ("L2", "G02"), ("L3", "G05"))


@pytest.fixture(scope="module")
def toolchain() -> LeanToolchain:
    return preflight(os.environ)


@pytest.fixture(scope="module")
def replay_dll(toolchain: LeanToolchain, tmp_path_factory: pytest.TempPathFactory) -> Path:
    probes = {**toolchain.environment, "OBAI_LEAN_PROBES": str(LEAN_DIR / "probes")}
    build = replace(toolchain, environment=MappingProxyType(probes))
    return build_algorithm(build, LEAN_DIR / "replay", tmp_path_factory.mktemp("replay_build"))


def _scenario_path(golden_id: str) -> Path:
    paths = sorted(SCENARIOS_DIR.glob(f"{golden_id}_*.toml"))
    assert len(paths) == 1, f"{golden_id}: expected one scenario file, found {paths}"
    return paths[0]


def _write_report(
    path: Path,
    scenario: tuple[str, str],
    bundle: ArtifactBundle,
    run: LeanRun,
    comparisons: tuple[Comparison, ...],
) -> None:
    lean_id, golden_id = scenario
    report = {
        "scenario": lean_id,
        "golden": golden_id,
        "manifest_id": bundle.result.provenance.manifest_id,
        "artifact_digests": bundle.digests,
        "lean_commit": run.lean_commit,
        "sdk_version": run.sdk_version,
        "export_digests": run.export_digests,
        "comparisons": [
            {**asdict(comparison), "explained": comparison.explained} for comparison in comparisons
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str) + "\n", "utf-8")


@pytest.mark.parametrize(("lean_id", "golden_id"), SCENARIOS, ids=[lean for lean, _ in SCENARIOS])
def test_lean_replay_reconciles_exactly(
    lean_id: str, golden_id: str, toolchain: LeanToolchain, replay_dll: Path, tmp_path: Path
) -> None:
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    bundle = run_scenario(load_scenario(_scenario_path(golden_id)), dataset_dir)
    dataset = read_dataset(dataset_dir)

    run = run_algorithm(
        toolchain,
        replay_dll,
        type_name=REPLAY_TYPE_NAME,
        archives=export(dataset),
        algorithm_input=replay_input(bundle, dataset),
        workspace=tmp_path / "lean",
    )
    comparisons = reconcile(bundle, dataset, run.records)

    report_dir = Path(os.environ.get("LEAN_REPORT_DIR") or tmp_path)
    _write_report(report_dir / f"{lean_id}.json", (lean_id, golden_id), bundle, run, comparisons)
    failures = unexplained(comparisons)
    assert not failures, f"{lean_id} ({golden_id}) differs from LEAN:\n" + "\n".join(
        f"{c.check} {c.at} {c.subject}: ours {c.ours}, LEAN {c.lean}, "
        f"expected difference {c.expected_difference} {list(c.mismatches)}"
        for c in failures
    )
