"""Golden end-to-end scenarios: one pytest id per file under ``scenarios/`` (ADR 0002 §11).

Each scenario runs strategy bytes → ``load_strategy`` → ``generate`` → ``write_dataset`` /
``read_dataset`` → ``resolve`` → ``run`` and compares every stated value exactly (§17 item 44).
Implementers never edit a scenario's ``[expected]``; a dispute is settled against the design text.
"""

from pathlib import Path

import pytest

from .runner import SCENARIOS_DIR, compare, load_scenario, observe, run_scenario

SCENARIO_PATHS = sorted(SCENARIOS_DIR.glob("*.toml"))


@pytest.mark.parametrize("path", SCENARIO_PATHS, ids=[path.stem for path in SCENARIO_PATHS])
def test_golden_scenario(path: Path, tmp_path: Path) -> None:
    scenario = load_scenario(path)
    bundle = run_scenario(scenario, tmp_path)
    mismatches = compare(scenario.expected, observe(bundle))
    assert not mismatches, f"{path.name}:\n" + "\n".join(mismatches)
