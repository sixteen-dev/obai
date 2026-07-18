"""Opik experiment-based evaluation for model comparison.

Bridges the existing OBaI eval pipeline with Opik's experiment tracking,
enabling side-by-side comparison of different model configurations.

Usage:
    # Single experiment
    python -m evaluation experiment --name "baseline" --limit 3

    # Compare current vs candidate models in one command
    python -m evaluation experiment --name "compare" --compare gpt-5.4 --limit 3
    python -m evaluation experiment --name "compare" --compare gpt-5.4,gpt-5.4-mini --limit 3
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

import opik
from opik.evaluation.metrics.base_metric import BaseMetric
from opik.evaluation.metrics.score_result import ScoreResult

from core_agents.config import get_config, reset_config
from evaluation.eval_runner import (
    OBaIEvaluator,
    TestCase,
    load_test_cases,
    scorer_skip_is_allowed,
)
from evaluation.trace.types import Trace

logger = logging.getLogger(__name__)

# Type alias for the async query runner function (cli.run_query_with_trace).
QueryRunner = Callable[[str, str, bool], Awaitable[Trace]]

# Score keys extracted from task output, mapped to human-readable metric names.
# Boolean keys are converted to float (1.0/0.0), numeric keys pass through.
_EXTRACTOR_KEYS: dict[str, str] = {
    "evaluation_complete": "evaluation_complete",
    "scorer_error": "scorer_error",
    "outcome_pass": "outcome_pass",
    "partial_refusal_semantic_pass": "partial_refusal_semantic_pass",
    "date_policy_pass": "date_policy_pass",
    "date_policy_applicable": "date_policy_applicable",
    "orchestration_pass": "orchestration_pass",
    "sequence_pass": "sequence_pass",
    "quality_pass": "quality_pass",
    "efficiency_pass": "efficiency_pass",
    "efficiency_score": "efficiency_score",
    "strategy_contract_pass": "strategy_contract_pass",
    "strategy_contract_applicable": "strategy_contract_applicable",
    "strategy_grounding_pass": "strategy_grounding_pass",
    "strategy_grounding_applicable": "strategy_grounding_applicable",
    "strategy_decision_pass": "strategy_decision_pass",
    "strategy_decision_applicable": "strategy_decision_applicable",
    "relevance_score": "relevance_score",
    "task_completion_pass": "task_completion_pass",
    "tool_correctness_pass": "tool_correctness_pass",
    "rubric_avg": "rubric_avg",
    "rubric_avg_applicable": "rubric_avg_applicable",
    "faithfulness_numeric_accuracy": "faithfulness_numeric_accuracy",
    "faithfulness_numeric_accuracy_applicable": "faithfulness_numeric_accuracy_applicable",
    "completeness_coverage": "completeness_coverage",
    "completeness_coverage_applicable": "completeness_coverage_applicable",
}

_OPTIONAL_SCORER_OUTPUTS: dict[str, str] = {
    "DatePolicyScorer": "date_policy_pass",
    "StrategyContractScorer": "strategy_contract_pass",
    "StrategyGroundingScorer": "strategy_grounding_pass",
    "StrategyDecisionScorer": "strategy_decision_pass",
    # Semantic scorers explicitly return skipped/N/A when the tool execution
    # produced no ground truth.  Their absent or skipped values must not become
    # false zeroes in experiment aggregates.
    "LLMJudgeScorer": "rubric_avg",
    "FaithfulnessScorer": "faithfulness_numeric_accuracy",
    "CompletenessScorer": "completeness_coverage",
}
_OPTIONAL_EXTRACTOR_APPLICABILITY: dict[str, str] = {
    pass_key: f"{pass_key.removesuffix('_pass')}_applicable"
    for pass_key in _OPTIONAL_SCORER_OUTPUTS.values()
}

# Maps scorer class names to the boolean pass key in their result dict.
# Mirrors _SCORER_PASS_KEYS from cli.py.
_SCORER_PASS_FIELDS: dict[str, str] = {
    "OutcomeContractScorer": "outcome_pass",
    "PartialRefusalSemanticScorer": "partial_refusal_semantic_pass",
    "DatePolicyScorer": "date_policy_pass",
    "ToolOrchestrationScorer": "correct_tools",
    "SequenceScorer": "correct_sequence",
    "ResponseQualityScorer": "quality_pass",
    "EfficiencyScorer": "within_budget",
    "StrategyContractScorer": "contract_pass",
    "StrategyGroundingScorer": "grounding_pass",
    "StrategyDecisionScorer": "strategy_decision_pass",
    "AnswerRelevanceScorer": "relevant",
    "TaskCompletionScorer": "task_completed",
    "ToolCorrectnessScorer": "tools_correct",
    "LLMJudgeScorer": "rubric_pass",
    "FaithfulnessScorer": "faithfulness_pass",
    "CompletenessScorer": "completeness_pass",
}

# Maps scorer class names to (extractor_key, data_key) for continuous scores.
# When present, these override the boolean pass/fail with a numeric value.
_SCORER_NUMERIC_FIELDS: dict[str, tuple[str, str]] = {
    "EfficiencyScorer": ("efficiency_score", "efficiency"),
    "AnswerRelevanceScorer": ("relevance_score", "score"),
    "LLMJudgeScorer": ("rubric_avg", "average_score"),
    "FaithfulnessScorer": ("faithfulness_numeric_accuracy", "numeric_accuracy"),
    "CompletenessScorer": ("completeness_coverage", "coverage_score"),
}

# Maps scorer pass fields to flat extractor key names.
_PASS_FIELD_TO_EXTRACTOR: dict[str, str] = {
    "outcome_pass": "outcome_pass",
    "partial_refusal_semantic_pass": "partial_refusal_semantic_pass",
    "date_policy_pass": "date_policy_pass",
    "correct_tools": "orchestration_pass",
    "correct_sequence": "sequence_pass",
    "quality_pass": "quality_pass",
    "within_budget": "efficiency_pass",
    "contract_pass": "strategy_contract_pass",
    "grounding_pass": "strategy_grounding_pass",
    "strategy_decision_pass": "strategy_decision_pass",
    "relevant": "relevance_score",
    "task_completed": "task_completion_pass",
    "tools_correct": "tool_correctness_pass",
    "rubric_pass": "rubric_avg",
    "faithfulness_pass": "faithfulness_numeric_accuracy",
    "completeness_pass": "completeness_coverage",
}


class ExtractorMetric(BaseMetric):
    """Thin metric that reads a pre-computed score from task output.

    Opik calls ``score(**{dataset_item | task_output})`` for each metric.
    This metric simply extracts a named key from the merged kwargs,
    defaulting to 0.0 if the key is absent.

    """

    def __init__(
        self,
        name: str,
        key: str,
        *,
        applicability_key: str | None = None,
    ) -> None:
        """Initialize extractor metric.

        Args:
            name: Metric name shown in Opik UI.
            key: Key to extract from task output dict.
            applicability_key: Optional row-level gate; N/A rows emit no score.
        """
        super().__init__(name=name, track=False)
        self._key = key
        self._applicability_key = applicability_key

    def score(self, **kwargs: Any) -> ScoreResult | list[ScoreResult]:
        """Extract pre-computed score value.

        Args:
            **kwargs: Merged dataset item + task output fields.

        Returns:
            ScoreResult with extracted value, or an empty list for an N/A row.
        """
        if self._applicability_key is not None and not kwargs.get(self._applicability_key, False):
            return []
        value = kwargs.get(self._key, 0.0)
        if isinstance(value, bool):
            value = 1.0 if value else 0.0
        return ScoreResult(name=self.name, value=float(value))


def _filter_test_cases(
    test_cases: list[TestCase],
    smoke: bool = False,
    ids: list[str] | None = None,
    limit: int | None = None,
) -> list[TestCase]:
    """Filter test cases by smoke flag, ID list, and/or sample limit.

    Args:
        test_cases: Full list of test cases.
        smoke: If True, only include test cases with ``smoke: true`` in YAML.
        ids: Optional list of test case IDs to include (e.g. ["A1", "A3", "B1"]).
        limit: Optional max number of test cases to run.

    Returns:
        Filtered list of test cases.
    """
    filtered = test_cases
    if smoke:
        filtered = [tc for tc in filtered if tc.smoke]
    if ids is not None:
        id_set = {test_id.strip().upper() for test_id in ids if test_id.strip()}
        if not id_set:
            raise ValueError("ids must contain at least one non-empty test ID")
        filtered = [tc for tc in filtered if tc.id.upper() in id_set]
    if limit is not None and limit <= 0:
        raise ValueError("limit must be a positive integer")
    if limit is not None:
        filtered = filtered[:limit]
    return filtered


def _validate_builtin_scorer_requirements(test_cases: list[TestCase], *, no_builtin: bool) -> None:
    """Fail before provider setup if selected contracts need semantic judges."""
    if not no_builtin:
        return
    required_ids = [case.id for case in test_cases if case.requires_builtin_scorers]
    if required_ids:
        joined = ", ".join(required_ids)
        raise ValueError(
            "--no-builtin would reduce selected financial-answer/refusal contracts to "
            "shallow routing/text checks; "
            f"rerun without --no-builtin or deselect: {joined}"
        )


def _validate_semantic_scorer_credentials(test_cases: list[TestCase], *, no_builtin: bool) -> None:
    """Reject missing judge credentials before any selected OBaI query is sent."""
    if no_builtin or not any(
        case.requires_builtin_scorers or case.expected_outcome in {"success", "partial_refusal"}
        for case in test_cases
    ):
        return
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise ValueError(
            "ANTHROPIC_API_KEY is required by the selected semantic scorers; "
            "configure it before starting this paid suite"
        )


def sync_dataset(
    client: opik.Opik,
    dataset_name: str,
    test_cases: list[TestCase],
) -> opik.api_objects.dataset.dataset.Dataset:
    """Sync test cases to an Opik dataset.

    Opik deduplicates by content hash, so re-inserts are idempotent. The
    effective dataset name includes a fingerprint of the exact selected rows.
    This prevents stale rows in a reused base dataset from escaping current
    category, ID, or extended-only filters.

    Args:
        client: Opik client instance.
        dataset_name: Base name for the Opik dataset. A selection fingerprint
            is appended before lookup or creation.
        test_cases: Test cases to sync.

    Returns:
        Opik Dataset object.
    """
    items = [tc.to_dataset_row() for tc in test_cases]
    selection_json = json.dumps(
        items,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    selection_hash = hashlib.sha256(selection_json.encode()).hexdigest()[:16]
    effective_name = f"{dataset_name}-{selection_hash}"
    dataset = client.get_or_create_dataset(name=effective_name)
    dataset.insert(items)
    logger.info("Synced %d test cases to dataset '%s'", len(items), effective_name)
    return dataset


def build_experiment_config() -> dict[str, Any]:
    """Read current AgentConfig and return metadata dict for the experiment.

    Returns:
        Dict with model configuration for Opik experiment metadata.
    """
    config = get_config()
    metadata: dict[str, Any] = {
        "orchestrator_model": config.orchestrator_model,
        "specialist_model": config.specialist_model,
        "guardrails_enabled": config.enable_guardrails,
    }
    for agent_type in [
        "market_data",
        "fundamentals",
        "events_news",
        "options",
        "screener",
        "portfolio",
        "research",
        "strategy",
    ]:
        override = getattr(config, f"{agent_type}_model", None)
        if override is not None:
            metadata[f"{agent_type}_model"] = override
    return metadata


def _flatten_scores(eval_result: dict[str, Any]) -> dict[str, float]:
    """Flatten nested scorer results into a flat dict of floats.

    Args:
        eval_result: Result dict from ``OBaIEvaluator.evaluate_trace()``.

    Returns:
        Flat dict mapping score key names to float values.
    """
    raw_scores = eval_result.get("scores", {})
    scores = raw_scores if isinstance(raw_scores, dict) else {}
    flat: dict[str, float] = {}
    expected_scorers = eval_result.get("expected_scorers")
    invalid_scorer_plan = not isinstance(raw_scores, dict) or (
        expected_scorers is not None
        and (
            not isinstance(expected_scorers, list)
            or not expected_scorers
            or not all(isinstance(name, str) and name for name in expected_scorers)
            or len(set(expected_scorers)) != len(expected_scorers)
            or set(scores) != set(expected_scorers)
        )
    )
    scorer_error = (
        bool(eval_result.get("aborted"))
        or invalid_scorer_plan
        or any(
            not isinstance(score_data, dict)
            or "error" in score_data
            or (
                score_data.get("skipped") is True
                and not scorer_skip_is_allowed(scorer_name, score_data)
            )
            for scorer_name, score_data in scores.items()
        )
    )
    flat["evaluation_complete"] = 0.0 if scorer_error else 1.0
    flat["scorer_error"] = 1.0 if scorer_error else 0.0

    for scorer_name, score_data in scores.items():
        if not isinstance(score_data, dict):
            continue
        if "error" in score_data:
            continue
        if score_data.get("skipped") is True:
            continue

        # Extract boolean pass/fail
        pass_field = _SCORER_PASS_FIELDS.get(scorer_name)
        if pass_field and pass_field in score_data:
            extractor_key = _PASS_FIELD_TO_EXTRACTOR.get(pass_field, pass_field)
            val = score_data[pass_field]
            flat[extractor_key] = 1.0 if val else 0.0

        # Extract numeric values (override boolean with continuous score)
        numeric_entry = _SCORER_NUMERIC_FIELDS.get(scorer_name)
        if numeric_entry:
            extractor_key, data_key = numeric_entry
            if data_key in score_data:
                raw = score_data[data_key]
                if isinstance(raw, (int, float)):
                    flat[extractor_key] = float(raw)

    # These scorers intentionally do not apply to every row. Global Opik
    # extractor metrics otherwise convert an absent key to a false zero. Emit
    # applicability explicitly; the extractor omits N/A rows entirely. If an
    # applicable scorer errored/omitted its verdict, keep it fail-closed at 0.
    for scorer_name, extractor_key in _OPTIONAL_SCORER_OUTPUTS.items():
        score_data = scores.get(scorer_name)
        applicable = isinstance(score_data, dict) and not scorer_skip_is_allowed(
            scorer_name, score_data
        )
        applicability_key = f"{extractor_key.removesuffix('_pass')}_applicable"
        flat[applicability_key] = 1.0 if applicable else 0.0
        if applicable and extractor_key not in flat:
            flat[extractor_key] = 0.0

    return flat


def make_experiment_task(
    evaluator: OBaIEvaluator,
    test_case_map: dict[str, TestCase],
    query_runner: QueryRunner,
) -> Any:
    """Return a sync task callable for ``opik.evaluate()``.

    The returned function reconstructs a TestCase from the dataset item,
    runs the async eval pipeline via ``asyncio.run()``, and returns a
    flat dict of scores.

    Args:
        evaluator: Configured OBaIEvaluator instance.
        test_case_map: Mapping of test case ID to TestCase object.
        query_runner: Async function to run a query and capture a trace.

    Returns:
        Callable[[dict], dict] suitable for ``opik.evaluate(task=...)``.
    """
    config = get_config()

    def task(dataset_item: dict[str, Any]) -> dict[str, Any]:
        """Run eval pipeline for a single dataset item.

        Args:
            dataset_item: Dict from Opik dataset row.

        Returns:
            Flat dict with response text and score values.
        """
        test_case = _validated_dataset_binding(dataset_item, test_case_map)
        query = test_case.query

        async def _run() -> dict[str, Any]:
            trace = await query_runner(query, config.orchestrator_model, False)
            result = await evaluator.evaluate_trace(trace, test_case)
            return {
                "response": trace.final_response or "",
                **_flatten_scores(result),
            }

        return asyncio.run(_run())

    return task


def _validated_dataset_binding(
    dataset_item: dict[str, Any],
    test_case_map: dict[str, TestCase],
) -> TestCase:
    """Bind a remote experiment row to its exact locally selected contract.

    Opik datasets are remote and mutable.  A same-ID stale row must be rejected
    before ``query_runner`` so it cannot spend on a different query and then be
    judged against the local case's expectations.
    """
    test_id = dataset_item.get("test_id")
    if not isinstance(test_id, str) or test_id not in test_case_map:
        raise RuntimeError(f"dataset row has unknown test_id: {test_id!r}")
    test_case = test_case_map[test_id]
    expected_row = test_case.to_dataset_row()
    mismatched = [
        key for key, expected in expected_row.items() if dataset_item.get(key) != expected
    ]
    if mismatched:
        raise RuntimeError(
            f"dataset contract drift for {test_id}: mismatched " + ", ".join(mismatched)
        )
    return test_case


def build_extractor_metrics() -> list[BaseMetric]:
    """Build list of ExtractorMetric instances for all score dimensions.

    Returns:
        List of ExtractorMetric instances.
    """
    return [
        ExtractorMetric(
            name=name,
            key=key,
            applicability_key=_OPTIONAL_EXTRACTOR_APPLICABILITY.get(key),
        )
        for key, name in _EXTRACTOR_KEYS.items()
    ]


def make_verbose_experiment_task(
    evaluator: OBaIEvaluator,
    test_case_map: dict[str, TestCase],
    query_runner: QueryRunner,
    collected_results: list[dict[str, Any]],
) -> Any:
    """Return a task callable that also accumulates full scorer results.

    Like ``make_experiment_task`` but stores the full (non-flattened) result
    dicts in ``collected_results`` for rich CLI output after the experiment.
    Safe because ``task_threads=1`` — no concurrent writes.

    Args:
        evaluator: Configured OBaIEvaluator instance.
        test_case_map: Mapping of test case ID to TestCase object.
        query_runner: Async function to run a query and capture a trace.
        collected_results: Mutable list to accumulate full result dicts.

    Returns:
        Callable[[dict], dict] suitable for ``opik.evaluate(task=...)``.
    """
    config = get_config()

    def task(dataset_item: dict[str, Any]) -> dict[str, Any]:
        """Run eval pipeline, store full results, return flattened scores."""
        test_case = _validated_dataset_binding(dataset_item, test_case_map)
        query = test_case.query

        async def _run() -> dict[str, Any]:
            trace = await query_runner(query, config.orchestrator_model, False)
            result = await evaluator.evaluate_trace(trace, test_case)
            result["test_id"] = test_case.id
            result["category"] = test_case.category
            collected_results.append(result)
            return {
                "response": trace.final_response or "",
                **_flatten_scores(result),
            }

        return asyncio.run(_run())

    return task


def run_evaluate_as_experiment(
    query_runner: QueryRunner,
    test_cases: list[TestCase],
    judge_model: str | None = None,
    no_builtin: bool = False,
    dataset_name: str = "obai-eval-suite",
) -> tuple[str, list[dict[str, Any]]]:
    """Run an evaluation suite as an Opik experiment, returning full results.

    This is the bridge between ``evaluate --suite`` and experiment tracking.
    The evaluation runs through ``opik.evaluate()`` so it creates a proper
    experiment with per-item traces, while also collecting the full scorer
    results for rich CLI output, export, and reports.

    Args:
        query_runner: Async function to run a query and capture a trace.
        test_cases: Test cases to evaluate.
        judge_model: LiteLLM model ID for LLM-based scorers.
        no_builtin: Skip Opik built-in (LLM-based) scorers.
        dataset_name: Base name for the selection-fingerprinted Opik dataset.

    Returns:
        Tuple of (experiment name, list of full result dicts).
    """
    _validate_builtin_scorer_requirements(test_cases, no_builtin=no_builtin)
    _validate_semantic_scorer_credentials(test_cases, no_builtin=no_builtin)
    client = opik.Opik()
    config = get_config()

    dataset = sync_dataset(client, dataset_name, test_cases)
    test_case_map = {tc.id: tc for tc in test_cases if tc.id}

    evaluator = OBaIEvaluator(
        use_builtin_scorers=not no_builtin,
        judge_model=judge_model,
    )

    collected_results: list[dict[str, Any]] = []
    task_fn = make_verbose_experiment_task(
        evaluator, test_case_map, query_runner, collected_results
    )
    metrics = build_extractor_metrics()
    exp_config = build_experiment_config()

    result = opik.evaluate(
        dataset=dataset,
        task=task_fn,
        scoring_metrics=metrics,
        experiment_config=exp_config,
        project_name=config.opik_project,
        task_threads=1,
    )

    exp_name = result.experiment_name or result.experiment_id
    return exp_name, collected_results


def _run_single_experiment(
    query_runner: QueryRunner,
    dataset: opik.api_objects.dataset.dataset.Dataset,
    test_case_map: dict[str, TestCase],
    experiment_name: str | None,
    judge_model: str | None,
    no_builtin: bool,
) -> str:
    """Run a single Opik experiment against a prepared dataset.

    Args:
        query_runner: Async function to run a query and capture a trace.
        dataset: Opik dataset with test cases.
        test_case_map: Mapping of test case ID to TestCase.
        experiment_name: Name for this experiment.
        judge_model: Anthropic model ID for LLM-based scorers (None = use config).
        no_builtin: Skip Opik built-in (LLM-based) scorers.

    Returns:
        Experiment name string.
    """
    evaluator = OBaIEvaluator(
        use_builtin_scorers=not no_builtin,
        judge_model=judge_model,
    )
    task_fn = make_experiment_task(evaluator, test_case_map, query_runner)
    metrics = build_extractor_metrics()
    exp_config = build_experiment_config()
    config = get_config()

    result = opik.evaluate(
        dataset=dataset,
        task=task_fn,
        scoring_metrics=metrics,
        experiment_name=experiment_name,
        experiment_config=exp_config,
        project_name=config.opik_project,
        task_threads=1,
    )
    return result.experiment_name or result.experiment_id


def _swap_models(
    orchestrator: str | None,
    specialist: str | None,
) -> dict[str, str | None]:
    """Set model env vars and reset config singleton.

    Args:
        orchestrator: Orchestrator model to set, or None to leave unchanged.
        specialist: Specialist model to set, or None to leave unchanged.

    Returns:
        Dict of original env var values for restoration.
    """
    originals: dict[str, str | None] = {
        "ORCHESTRATOR_MODEL": os.environ.get("ORCHESTRATOR_MODEL"),
        "SPECIALIST_MODEL": os.environ.get("SPECIALIST_MODEL"),
    }
    if orchestrator is not None:
        os.environ["ORCHESTRATOR_MODEL"] = orchestrator
    if specialist is not None:
        os.environ["SPECIALIST_MODEL"] = specialist
    reset_config()
    return originals


def _restore_models(originals: dict[str, str | None]) -> None:
    """Restore original model env vars and reset config singleton.

    Args:
        originals: Dict from ``_swap_models()`` with original values.
    """
    for key, value in originals.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    reset_config()


def run_experiment(
    query_runner: QueryRunner,
    experiment_name: str | None = None,
    category: str | None = None,
    smoke: bool = False,
    ids: list[str] | None = None,
    limit: int | None = None,
    judge_model: str | None = None,
    no_builtin: bool = False,
    dataset_name: str = "obai-eval-suite",
    compare_orchestrator: str | None = None,
    compare_specialist: str | None = None,
    include_extended: bool = False,
) -> list[str]:
    """Run one or two Opik experiments for model comparison.

    When ``compare_orchestrator`` is set, runs two experiments against the
    same dataset: one with current config (baseline), one with the candidate
    models. Both show up in the Opik Experiments tab for side-by-side comparison.

    Args:
        query_runner: Async function to run a query and capture a trace.
        experiment_name: Base name for the experiment(s).
        category: Filter test cases by category letter.
        smoke: If True, only run test cases marked ``smoke: true`` in suite.yaml.
        ids: Filter to specific test case IDs (e.g. ["A1", "A3"]).
        limit: Max number of test cases to run.
        judge_model: LiteLLM model ID for LLM-based scorers.
        no_builtin: Skip Opik built-in (LLM-based) scorers.
        dataset_name: Base name for the selection-fingerprinted Opik dataset.
        compare_orchestrator: Candidate orchestrator model for comparison.
        compare_specialist: Candidate specialist model for comparison.
        include_extended: Include extended-only cases. Defaults to False so
            explicit IDs cannot select billable extended cases without opt-in.

    Returns:
        List of experiment name strings (1 or 2).
    """
    # Load, filter, and sync test cases
    test_cases = load_test_cases(category=category, include_extended=include_extended)
    if ids is not None:
        requested_ids = {test_id.strip().upper() for test_id in ids if test_id.strip()}
        if not requested_ids:
            raise ValueError("ids must contain at least one non-empty test ID")
        available_ids = {test_case.id.upper() for test_case in test_cases}
        unavailable_ids = sorted(requested_ids - available_ids)
        if unavailable_ids:
            unavailable = ", ".join(unavailable_ids)
            msg = (
                f"Requested test IDs are unavailable: {unavailable}. "
                "Extended-only cases require include_extended=True "
                "(CLI: --include-extended)."
            )
            raise ValueError(msg)

    test_cases = _filter_test_cases(test_cases, smoke=smoke, ids=ids, limit=limit)
    if not test_cases:
        msg = (
            "No test cases matched the requested filters. "
            "Extended-only cases require include_extended=True."
        )
        raise ValueError(msg)

    _validate_builtin_scorer_requirements(test_cases, no_builtin=no_builtin)
    _validate_semantic_scorer_credentials(test_cases, no_builtin=no_builtin)

    client = opik.Opik()
    dataset = sync_dataset(client, dataset_name, test_cases)
    test_case_map = {tc.id: tc for tc in test_cases if tc.id}

    results: list[str] = []

    if compare_orchestrator is not None:
        # --- Comparison mode: run baseline then candidate ---
        base_name = experiment_name or "compare"
        baseline_name = f"{base_name}-baseline"
        candidate_name = f"{base_name}-candidate"

        # 1) Baseline: current config
        logger.info("Running baseline experiment: %s", baseline_name)
        name = _run_single_experiment(
            query_runner,
            dataset,
            test_case_map,
            baseline_name,
            judge_model,
            no_builtin,
        )
        results.append(name)

        # 2) Candidate: swap models, run, restore
        logger.info("Running candidate experiment: %s", candidate_name)
        originals = _swap_models(compare_orchestrator, compare_specialist)
        try:
            name = _run_single_experiment(
                query_runner,
                dataset,
                test_case_map,
                candidate_name,
                judge_model,
                no_builtin,
            )
            results.append(name)
        finally:
            _restore_models(originals)
    else:
        # --- Single experiment mode ---
        name = _run_single_experiment(
            query_runner,
            dataset,
            test_case_map,
            experiment_name,
            judge_model,
            no_builtin,
        )
        results.append(name)

    return results
