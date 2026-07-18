"""Offline tests for the extended evaluation-corpus cost gate."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import opik
import pytest
import yaml
from typer.testing import CliRunner

from evaluation import cli, experiment
from evaluation.eval_runner import TestCase as EvalTestCase
from evaluation.eval_runner import load_test_cases
from evaluation.experiment import (
    _filter_test_cases,
    _validate_builtin_scorer_requirements,
    _validate_semantic_scorer_credentials,
)


def _write_suite(path: Path) -> None:
    """Write one standard and one extended case to ``path``."""
    path.write_text(
        yaml.safe_dump(
            {
                "test_cases": [
                    {
                        "id": "A1",
                        "query": "Standard case",
                        "category": "A",
                        "expected_outcome": "success",
                        "date_policy": "frozen",
                        "extended_only": False,
                        "cost_class": "low",
                    },
                    {
                        "id": "I1",
                        "query": "Extended case",
                        "category": "I",
                        "expected_outcome": "success",
                        "date_policy": "live",
                        "extended_only": True,
                        "requires_builtin_scorers": True,
                        "cost_class": "high",
                    },
                ]
            },
            sort_keys=False,
        )
    )


def test_loader_excludes_extended_by_default_and_preserves_metadata(tmp_path: Path) -> None:
    """The default loader omits extended rows without dropping core metadata."""
    suite = tmp_path / "suite.yaml"
    _write_suite(suite)

    cases = load_test_cases(path=suite)

    assert [case.id for case in cases] == ["A1"]
    assert cases[0].to_dataset_row() == {
        "test_id": "A1",
        "query": "Standard case",
        "category": "A",
        "expected_tools": [],
        "expected_sequence": [],
        "query_type": "general",
        "description": "",
        "expect_rejection": False,
        "expected_outcome": "success",
        "expected_error_pattern": None,
        "expected_response_pattern": None,
        "forbidden_response_pattern": None,
        "date_policy": "frozen",
        "max_age_seconds": None,
        "forbidden_tools": [],
        "allowed_partial_errors": [],
        "extended_only": False,
        "cost_class": "low",
        "requires_builtin_scorers": True,
    }


def test_explicit_id_cannot_select_extended_case_without_opt_in(tmp_path: Path) -> None:
    """ID filtering cannot resurrect a case removed by the cost gate."""
    suite = tmp_path / "suite.yaml"
    _write_suite(suite)

    default_cases = load_test_cases(path=suite)
    opted_in_cases = load_test_cases(path=suite, include_extended=True)

    assert _filter_test_cases(default_cases, ids=["I1"]) == []
    selected = _filter_test_cases(opted_in_cases, ids=["I1"])
    assert [case.id for case in selected] == ["I1"]
    assert selected[0].to_dataset_row()["expected_outcome"] == "success"
    assert selected[0].to_dataset_row()["date_policy"] == "live"
    assert selected[0].to_dataset_row()["extended_only"] is True
    assert selected[0].to_dataset_row()["cost_class"] == "high"
    assert selected[0].to_dataset_row()["requires_builtin_scorers"] is True


def test_extended_accuracy_case_refuses_routing_only_mode(tmp_path: Path) -> None:
    """Cost-saving mode cannot advertise semantic accuracy coverage."""
    suite = tmp_path / "suite.yaml"
    _write_suite(suite)
    selected = load_test_cases(path=suite, include_extended=True)

    with pytest.raises(ValueError, match="shallow routing"):
        _validate_builtin_scorer_requirements(selected, no_builtin=True)

    _validate_builtin_scorer_requirements(selected, no_builtin=False)


def test_semantic_judge_key_is_required_before_paid_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A missing cross-family judge key must fail before an OBaI query spends."""
    suite = tmp_path / "suite.yaml"
    _write_suite(suite)
    cases = load_test_cases(path=suite)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        _validate_semantic_scorer_credentials(cases, no_builtin=False)

    monkeypatch.setenv("ANTHROPIC_API_KEY", "judge-key")
    _validate_semantic_scorer_credentials(cases, no_builtin=False)

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _validate_semantic_scorer_credentials(cases, no_builtin=True)


def test_every_success_case_refuses_routing_only_green(tmp_path: Path) -> None:
    """Ordinary financial answers cannot pass on routing and response length alone."""
    suite = tmp_path / "success.yaml"
    suite.write_text(
        yaml.safe_dump(
            {
                "test_cases": [
                    {
                        "id": "A29",
                        "query": "Synthetic fixture strike 900; calculate the position",
                        "category": "A",
                        "expected_outcome": "success",
                        "date_policy": "frozen",
                    }
                ]
            }
        )
    )
    cases = load_test_cases(path=suite)

    assert cases[0].requires_builtin_scorers is True
    with pytest.raises(ValueError, match="shallow"):
        _validate_builtin_scorer_requirements(cases, no_builtin=True)


def test_loader_rejects_duplicate_and_invalid_core_fields(tmp_path: Path) -> None:
    """Rows cannot collapse in Opik or reach paid execution with malformed identity."""
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        yaml.safe_dump(
            {
                "test_cases": [
                    {"id": "DUP", "query": "one", "category": "A"},
                    {"id": "dup", "query": "two", "category": "A"},
                ]
            }
        )
    )
    with pytest.raises(ValueError, match="Duplicate"):
        load_test_cases(path=duplicate)

    for field, value in (("id", None), ("id", ""), ("query", 123), ("category", None)):
        malformed = tmp_path / f"invalid-{field}-{value!s}.yaml"
        row: dict[str, Any] = {"id": "A1", "query": "query", "category": "A"}
        row[field] = value
        malformed.write_text(yaml.safe_dump({"test_cases": [row]}))
        with pytest.raises(ValueError, match=field):
            load_test_cases(path=malformed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query_type", 123),
        ("expected_sequence", "market_data_analysis"),
        ("expected_sequence", [""]),
        ("smoke", "false"),
        ("extended_only", "false"),
        ("description", 123),
        ("cost_class", "unbounded"),
    ],
)
def test_loader_rejects_execution_and_cost_metadata_before_provider_setup(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    """Selection/scorer metadata cannot fail only after a paid query begins."""
    suite = tmp_path / f"invalid-{field}.yaml"
    row: dict[str, Any] = {
        "id": "A1",
        "query": "Use this static fixture",
        "category": "A",
        "expected_tools": ["market_data_analysis"],
    }
    row[field] = value
    suite.write_text(yaml.safe_dump({"test_cases": [row]}))

    with pytest.raises(ValueError, match=field):
        load_test_cases(path=suite, include_extended=True)


def test_loader_rejects_unknown_case_field_that_would_erase_an_assertion(
    tmp_path: Path,
) -> None:
    """A singular/plural typo cannot silently remove a forbidden-route contract."""
    suite = tmp_path / "unknown-field.yaml"
    suite.write_text(
        yaml.safe_dump(
            {
                "test_cases": [
                    {
                        "id": "D1",
                        "query": "Use this static fixture",
                        "category": "D",
                        "expected_outcome": "data_unavailable",
                        "forbidden_tool": ["market_data_analysis"],
                    }
                ]
            }
        )
    )

    with pytest.raises(ValueError, match=r"unknown fields.*forbidden_tool"):
        load_test_cases(path=suite, include_extended=True)


def test_option_strike_is_not_misclassified_as_a_year(tmp_path: Path) -> None:
    """A 2050 strike does not create an evaluation-time dependency."""
    suite = tmp_path / "strike.yaml"
    suite.write_text(
        yaml.safe_dump(
            {
                "test_cases": [
                    {
                        "id": "OPT",
                        "query": "Using the supplied static inputs, sell the 2050 strike call",
                        "category": "G",
                        "expected_outcome": "success",
                    }
                ]
            }
        )
    )

    assert load_test_cases(path=suite)[0].date_policy is None


def test_loader_normalizes_yaml_syntax_error_to_configuration_error(tmp_path: Path) -> None:
    """Malformed YAML is a configuration error, not a product failure."""
    suite = tmp_path / "broken.yaml"
    suite.write_text("test_cases: [\n  - id: A1\n    query: broken: mapping\n")

    with pytest.raises(ValueError, match="Invalid YAML syntax"):
        load_test_cases(path=suite)


@pytest.mark.parametrize("limit", [0, -1])
def test_non_positive_limit_is_rejected_before_full_corpus_execution(limit: int) -> None:
    """A limit typo cannot expand a surgical run to the full paid suite."""
    with pytest.raises(ValueError, match="positive"):
        _filter_test_cases(load_test_cases(), limit=limit)


def test_sync_dataset_isolates_selection_from_stale_named_dataset() -> None:
    """A stale base dataset cannot add rows to the selected experiment set."""

    class FakeDataset:
        def __init__(self, items: list[dict[str, Any]]) -> None:
            self.items = items

        def insert(self, items: list[dict[str, Any]]) -> None:
            self.items.extend(items)

    class FakeOpikClient:
        def __init__(self) -> None:
            self.datasets = {"shared": FakeDataset([{"test_id": "I1"}])}
            self.requested_names: list[str] = []

        def get_or_create_dataset(self, name: str) -> FakeDataset:
            self.requested_names.append(name)
            return self.datasets.setdefault(name, FakeDataset([]))

    client = FakeOpikClient()
    selected = [EvalTestCase(id="A1", query="Standard case", category="A")]

    dataset: Any = experiment.sync_dataset(cast("opik.Opik", client), "shared", selected)

    assert client.requested_names[0] != "shared"
    assert client.requested_names[0].startswith("shared-")
    assert [item["test_id"] for item in dataset.items] == ["A1"]
    assert [item["test_id"] for item in client.datasets["shared"].items] == ["I1"]


def test_run_experiment_threads_loader_opt_in_before_id_filter(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """Experiment loading applies opt-in before selecting requested IDs."""
    suite = tmp_path / "suite.yaml"
    _write_suite(suite)
    synced_ids: list[list[str]] = []
    opik_clients_created = 0

    def fake_load_test_cases(
        category: str | None = None,
        include_extended: bool = False,
    ) -> list[EvalTestCase]:
        return load_test_cases(
            path=suite,
            category=category,
            include_extended=include_extended,
        )

    def fake_sync_dataset(
        _client: Any,
        _dataset_name: str,
        test_cases: list[EvalTestCase],
    ) -> object:
        synced_ids.append([case.id for case in test_cases])
        return object()

    def fake_opik_client() -> object:
        nonlocal opik_clients_created
        opik_clients_created += 1
        return object()

    async def offline_query_runner(_query: str, _model: str, _verbose: bool) -> Any:
        raise AssertionError("The mocked experiment must not execute a query")

    monkeypatch.setattr(opik, "Opik", fake_opik_client)
    monkeypatch.setattr(experiment, "load_test_cases", fake_load_test_cases)
    monkeypatch.setattr(experiment, "sync_dataset", fake_sync_dataset)
    monkeypatch.setattr(experiment, "_run_single_experiment", lambda *_args: "offline")

    with pytest.raises(ValueError, match="include_extended=True"):
        experiment.run_experiment(
            query_runner=offline_query_runner,
            ids=["A1", "I1"],
        )

    assert opik_clients_created == 0

    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-key")
    experiment.run_experiment(
        query_runner=offline_query_runner,
        ids=["A1", "I1"],
        include_extended=True,
    )

    assert synced_ids == [["A1", "I1"]]
    assert opik_clients_created == 1


def test_public_experiment_entry_points_validate_judge_key_before_opik(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct callers cannot bypass the pre-spend semantic-credential boundary."""
    case = EvalTestCase(
        id="A1",
        query="test",
        category="A",
        expected_outcome="success",
        requires_builtin_scorers=True,
    )
    clients_created = 0

    def forbidden_opik_client() -> object:
        nonlocal clients_created
        clients_created += 1
        raise AssertionError("Opik must not initialize without the judge key")

    async def forbidden_query_runner(_query: str, _model: str, _verbose: bool) -> Any:
        raise AssertionError("A paid query must not start without the judge key")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(opik, "Opik", forbidden_opik_client)

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        experiment.run_evaluate_as_experiment(
            query_runner=forbidden_query_runner,
            test_cases=[case],
        )

    monkeypatch.setattr(experiment, "load_test_cases", lambda **_kwargs: [case])
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        experiment.run_experiment(query_runner=forbidden_query_runner)

    assert clients_created == 0


def test_experiment_cli_uses_configuration_exit_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing judge key is not reported as a captured product regression."""
    monkeypatch.setattr(
        cli,
        "run_experiment",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("ANTHROPIC_API_KEY missing")),
    )

    result = CliRunner().invoke(cli.app, ["experiment"])

    assert result.exit_code == 2, result.output
    assert "ANTHROPIC_API_KEY" in result.output


def test_list_tests_cli_requires_explicit_extended_opt_in(tmp_path: Path) -> None:
    """The list command exposes extended rows only with its explicit flag."""
    suite = tmp_path / "suite.yaml"
    _write_suite(suite)
    runner = CliRunner()

    default_result = runner.invoke(cli.app, ["list-tests", "--file", str(suite)])
    opted_in_result = runner.invoke(
        cli.app,
        ["list-tests", "--file", str(suite), "--include-extended"],
    )

    assert default_result.exit_code == 0, default_result.output
    assert "Standard case" in default_result.output
    assert "Extended case" not in default_result.output
    assert opted_in_result.exit_code == 0, opted_in_result.output
    assert "Standard case" in opted_in_result.output
    assert "Extended case" in opted_in_result.output


def test_evaluate_suite_cli_threads_extended_opt_in(monkeypatch: Any) -> None:
    """The evaluate suite command passes its explicit opt-in to the loader."""
    captured: dict[str, Any] = {}

    def fake_load_test_cases(**kwargs: Any) -> list[EvalTestCase]:
        captured.update(kwargs)
        return [EvalTestCase(id="I1", query="Extended case", category="I")]

    monkeypatch.setattr(cli, "load_test_cases", fake_load_test_cases)
    monkeypatch.setattr(
        cli,
        "run_evaluate_as_experiment",
        lambda **_kwargs: (
            "offline",
            [
                {
                    "test_id": "I1",
                    "expected_scorers": [
                        "OutcomeContractScorer",
                        "ResponseQualityScorer",
                        "EfficiencyScorer",
                    ],
                    "scores": {
                        "OutcomeContractScorer": {"outcome_pass": True},
                        "ResponseQualityScorer": {"quality_pass": True},
                        "EfficiencyScorer": {"within_budget": True},
                    },
                }
            ],
        ),
    )

    result = CliRunner().invoke(
        cli.app,
        ["evaluate", "--suite", "--include-extended", "--no-builtin"],
    )

    assert result.exit_code == 0, result.output
    assert captured["include_extended"] is True


def test_evaluate_suite_does_not_start_for_unapproved_extended_only_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """An all-extended file cannot reach Opik without the explicit opt-in."""
    suite = tmp_path / "extended-only.yaml"
    suite.write_text(
        yaml.safe_dump(
            {
                "test_cases": [
                    {
                        "id": "I1",
                        "query": "Extended case",
                        "category": "I",
                        "extended_only": True,
                    }
                ]
            }
        )
    )

    def fail_if_started(**_kwargs: Any) -> None:
        raise AssertionError("The evaluation pipeline must not start")

    monkeypatch.setattr(cli, "run_evaluate_as_experiment", fail_if_started)

    result = CliRunner().invoke(cli.app, ["evaluate", "--suite", "--file", str(suite)])

    assert result.exit_code == 2, result.output
    assert "require --include-extended" in result.output


def test_experiment_cli_threads_extended_opt_in(monkeypatch: Any) -> None:
    """The experiment command passes its explicit opt-in to the runner."""
    captured: dict[str, Any] = {}

    def fake_run_experiment(**kwargs: Any) -> list[str]:
        captured.update(kwargs)
        return ["offline"]

    monkeypatch.setattr(cli, "run_experiment", fake_run_experiment)

    result = CliRunner().invoke(cli.app, ["experiment", "--include-extended"])

    assert result.exit_code == 0, result.output
    assert captured["include_extended"] is True
