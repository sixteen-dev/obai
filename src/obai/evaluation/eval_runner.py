"""Evaluation runner for OBaI multi-agent system.

This module provides the evaluation infrastructure using Opik metrics.
It converts OBaI traces to the format expected by scorers.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from openai import APIStatusError

from core_agents.config import get_config
from evaluation.scorers.builtin import get_builtin_scorers
from evaluation.scorers.custom import (
    DatePolicyScorer,
    EfficiencyScorer,
    OutcomeContractScorer,
    PartialRefusalSemanticScorer,
    ResponseQualityScorer,
    SequenceScorer,
    StrategyContractScorer,
    StrategyDecisionScorer,
    StrategyGroundingScorer,
    ToolOrchestrationScorer,
)
from evaluation.scorers.faithfulness import CompletenessScorer, FaithfulnessScorer
from evaluation.scorers.llm_judge import LLMJudgeScorer
from evaluation.trace.types import EventType, Trace

logger = logging.getLogger(__name__)

_EXPECTED_OUTCOMES = frozenset(
    {"success", "hub_reject", "specialist_error", "data_unavailable", "partial_refusal"}
)
_DATE_POLICIES = frozenset({"frozen", "relative", "live"})
_CASE_FIELDS = frozenset(
    {
        "id",
        "query",
        "category",
        "query_type",
        "description",
        "expected_tools",
        "expected_sequence",
        "expect_rejection",
        "smoke",
        "expected_outcome",
        "expected_error_pattern",
        "expected_response_pattern",
        "forbidden_response_pattern",
        "date_policy",
        "max_age_seconds",
        "forbidden_tools",
        "allowed_partial_errors",
        "extended_only",
        "cost_class",
        "requires_builtin_scorers",
        # Recognized only so the loader can return its more specific unsupported
        # multi-turn diagnostic below.
        "turns",
    }
)
_ALLOWED_SCORER_SKIP_REASONS: dict[str, frozenset[str]] = {
    "DatePolicyScorer": frozenset(
        {
            "relative_window_requires_semantic_validation",
            "live_case_has_no_declared_max_age",
        }
    ),
    "StrategyDecisionScorer": frozenset(
        {
            "missing_strategy_artifact",
            "pending_strategy_response",
            "not_completed_strategy_artifact",
            "missing_strategy_tool_outputs",
        }
    ),
}
_NON_TEMPORAL_CURRENT_RE = re.compile(
    r"\bcurrent\s+(?:assets?|liabilit(?:y|ies)|ratio)\b",
    re.IGNORECASE,
)
_NON_TEMPORAL_YEAR_RE = re.compile(r"\bRussell\s+2000\b", re.IGNORECASE)
_UNMISTAKABLY_TEMPORAL_RE = re.compile(
    r"""
    \b(?:today|tonight|yesterday|tomorrow|now|currently|latest|newest|recent|recently|
        upcoming|fresh)\b
    |\b(?:real[- ]time|up[- ]to[- ]date|breaking\s+news)\b
    |\b(?:historical|history|over\s+time|year[- ]to[- ]date|ytd|52[- ]week)\b
    |\bcurrent\b
    |\b(?:as\s+of|most\s+recent(?:ly)?)\b
    |\b(?:last|past|next|this|previous)\s+
       (?:\d+\s+)?(?:completed\s+)?(?:scheduled\s+)?
       (?:calendar\s+|trading\s+)?
       (?:days?|weeks?|months?|quarters?|years?|decades?|sessions?|earnings|reports?|releases?|
          cycles?|seasons?|expirations?|expiries|fed\s+(?:move|decision)|fomc\s+decision)\b
    |\b(?:in|within)\s+
       (?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+
       (?:calendar\s+|trading\s+)?(?:days?|weeks?|months?|quarters?|years?)\b
    |\b(?:19|20)\d{2}-\d{2}-\d{2}\b
    |\b(?:in|during|since|through|from|for|year|fiscal(?:\s+year)?|fy)\s+
       (?:19|20)\d{2}\b
    |\b(?:synthetic|frozen)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)


def scorer_skip_is_allowed(scorer_name: str, score_data: dict[str, Any]) -> bool:
    """Return whether a scorer's exact N/A reason is part of its contract."""
    if score_data.get("skipped") is not True:
        return False
    skip_reason = score_data.get("skip_reason")
    if not isinstance(skip_reason, str) or skip_reason not in _ALLOWED_SCORER_SKIP_REASONS.get(
        scorer_name, frozenset()
    ):
        return False
    if scorer_name == "DatePolicyScorer":
        expected_metadata = {
            "relative_window_requires_semantic_validation": (
                "relative",
                "semantic_relative_window_required",
            ),
            "live_case_has_no_declared_max_age": (
                "live",
                "semantic_live_freshness_required",
            ),
        }
        date_policy, contract_scope = expected_metadata[skip_reason]
        return (
            score_data.get("date_policy") == date_policy
            and score_data.get("contract_scope") == contract_scope
            and score_data.get("date_policy_pass") is None
        )
    # StrategyDecisionScorer emits only this minimal shape when it is genuinely
    # inapplicable. A contradictory pass/fail payload must never be neutralized.
    return set(score_data) == {"skipped", "skip_reason"}


def _query_requires_date_policy(query: Any) -> bool:
    """Return whether a query has an unmistakable evaluation-time dependency.

    This intentionally catches only strong lexical anchors. Broader concepts such
    as "momentum" may be temporal in context, but treating them as mandatory would
    reject legitimate timeless and deterministic test fixtures.
    "Current assets/liabilities/ratio" are accounting terms, not freshness asks.
    """
    if not isinstance(query, str):
        return False
    normalized = _NON_TEMPORAL_CURRENT_RE.sub("", query)
    normalized = _NON_TEMPORAL_YEAR_RE.sub("", normalized)
    return _UNMISTAKABLY_TEMPORAL_RE.search(normalized) is not None


def _validated_case_metadata(
    entry: dict[str, Any], *, case_id: str
) -> tuple[str, str | None, str | None, str | None, str | None]:
    """Validate deterministic evaluation metadata before any cost filtering."""
    expect_rejection = entry.get("expect_rejection", False)
    if not isinstance(expect_rejection, bool):
        raise ValueError(f"Test case {case_id}: expect_rejection must be a boolean")

    declared_outcome = entry.get("expected_outcome")
    if declared_outcome is None:
        expected_outcome = "hub_reject" if expect_rejection else "success"
    elif isinstance(declared_outcome, str) and declared_outcome in _EXPECTED_OUTCOMES:
        expected_outcome = declared_outcome
    else:
        allowed = ", ".join(sorted(_EXPECTED_OUTCOMES))
        raise ValueError(f"Test case {case_id}: expected_outcome must be one of {allowed}")
    if expect_rejection and expected_outcome != "hub_reject":
        raise ValueError(
            f"Test case {case_id}: expect_rejection=true conflicts with "
            f"expected_outcome={expected_outcome!r}"
        )

    expected_error_pattern = entry.get("expected_error_pattern")
    if expected_outcome == "specialist_error":
        if not isinstance(expected_error_pattern, str) or not expected_error_pattern.strip():
            raise ValueError(
                f"Test case {case_id}: specialist_error requires a non-empty expected_error_pattern"
            )
        try:
            re.compile(expected_error_pattern)
        except re.error as exc:
            raise ValueError(
                f"Test case {case_id}: expected_error_pattern is invalid: {exc}"
            ) from exc
    elif expected_error_pattern is not None:
        raise ValueError(
            f"Test case {case_id}: expected_error_pattern is only valid for specialist_error"
        )

    expected_response_pattern = entry.get("expected_response_pattern")
    forbidden_response_pattern = entry.get("forbidden_response_pattern")
    if expected_outcome == "partial_refusal":
        for field_name, pattern in (
            ("expected_response_pattern", expected_response_pattern),
            ("forbidden_response_pattern", forbidden_response_pattern),
        ):
            if not isinstance(pattern, str) or not pattern.strip():
                raise ValueError(
                    f"Test case {case_id}: partial_refusal requires a non-empty {field_name}"
                )
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"Test case {case_id}: invalid {field_name}: {exc}") from exc
    elif expected_response_pattern is not None or forbidden_response_pattern is not None:
        raise ValueError(
            f"Test case {case_id}: response patterns are only valid for partial_refusal"
        )

    date_policy = entry.get("date_policy")
    if date_policy is not None and (
        not isinstance(date_policy, str) or date_policy not in _DATE_POLICIES
    ):
        allowed = ", ".join(sorted(_DATE_POLICIES))
        raise ValueError(f"Test case {case_id}: date_policy must be one of {allowed}")
    if (
        date_policy is None
        and expected_outcome != "hub_reject"
        and _query_requires_date_policy(entry.get("query"))
    ):
        raise ValueError(
            f"Test case {case_id}: date_policy is required for an explicitly temporal query"
        )
    return (
        expected_outcome,
        date_policy,
        expected_error_pattern,
        expected_response_pattern,
        forbidden_response_pattern,
    )


def _structured_error_message(response: dict[str, Any]) -> str | None:
    """Return an explicit tool-error message without inferring from prose."""
    error = response.get("error")
    if error:
        return str(error)
    status = str(response.get("status", "")).strip().lower().replace("-", "_")
    if (
        response.get("isError") is True
        or response.get("is_error") is True
        or status
        in {
            "error",
            "failed",
            "failure",
            "validation_error",
        }
    ):
        return str(response.get("message") or response.get("reason") or status or "tool error")
    return None


def _needs_strategy_contract_scorer(query_type: str) -> bool:
    """Return True for strategy build/backtest flows with final-artifact expectations."""
    return (
        query_type.startswith("strategy_direct_")
        or query_type.startswith("strategy_design_")
        or query_type.startswith("backtest_")
        or query_type == "strategy_large_universe"
    )


@dataclass
class TestCase:
    """A single test case for evaluation.

    Attributes:
        query: The user query to test.
        expected_tools: Expected specialist agents to be called.
        expected_sequence: Required call order (if dependency exists).
        query_type: Category of query for analysis.
        description: Human-readable description.
        id: Unique test case identifier (e.g. A1, B3).
        category: Test category letter (A-E).
        expect_rejection: Whether the guardrail should reject this query.
        smoke: Whether this test case is part of the smoke test subset.
        expected_outcome: Expected high-level result classification.
        expected_error_pattern: Case-specific regex oracle for a specialist error.
        expected_response_pattern: Required final-response oracle for a partial refusal.
        forbidden_response_pattern: Contradictory final-response oracle for a partial refusal.
        date_policy: Freshness contract for the case, if one is declared.
        max_age_seconds: Maximum accepted age for a live timestamp.
        forbidden_tools: Specialist routes that must not be invoked.
        allowed_partial_errors: Case-specific recoverable inner-call contracts.
        extended_only: Whether running the case requires explicit cost opt-in.
        cost_class: Relative execution-cost classification, if declared.
        requires_builtin_scorers: Whether semantic/LLM scorers are mandatory.
    """

    query: str
    expected_tools: list[str] = field(default_factory=list)
    expected_sequence: list[str] | None = None
    query_type: str = "general"
    description: str = ""
    id: str = ""
    category: str = ""
    expect_rejection: bool = False
    smoke: bool = False
    expected_outcome: str = "success"
    expected_error_pattern: str | None = None
    expected_response_pattern: str | None = None
    forbidden_response_pattern: str | None = None
    date_policy: str | None = None
    max_age_seconds: int | None = None
    forbidden_tools: list[str] = field(default_factory=list)
    allowed_partial_errors: list[dict[str, str]] = field(default_factory=list)
    extended_only: bool = False
    cost_class: str | None = None
    requires_builtin_scorers: bool = False

    def to_dataset_row(self) -> dict[str, Any]:
        """Convert to dataset row format.

        Note: Opik reserves ``id`` for its own UUID. Our test case ID
        is stored as ``test_id`` to avoid collisions.
        """
        return {
            "test_id": self.id,
            "query": self.query,
            "category": self.category,
            "expected_tools": self.expected_tools,
            "expected_sequence": self.expected_sequence or [],
            "query_type": self.query_type,
            "description": self.description,
            "expect_rejection": self.expect_rejection,
            "expected_outcome": self.expected_outcome,
            "expected_error_pattern": self.expected_error_pattern,
            "expected_response_pattern": self.expected_response_pattern,
            "forbidden_response_pattern": self.forbidden_response_pattern,
            "date_policy": self.date_policy,
            "max_age_seconds": self.max_age_seconds,
            "forbidden_tools": self.forbidden_tools,
            "allowed_partial_errors": self.allowed_partial_errors,
            "extended_only": self.extended_only,
            "cost_class": self.cost_class,
            "requires_builtin_scorers": self.requires_builtin_scorers,
        }


def _default_suite_path() -> Path:
    """Return path to the built-in suite.yaml.

    Returns:
        Path to evaluation/test_cases/suite.yaml.
    """
    return Path(__file__).parent / "test_cases" / "suite.yaml"


def load_test_cases(
    path: Path | None = None,
    category: str | None = None,
    include_extended: bool = False,
) -> list[TestCase]:
    """Load test cases from a YAML file.

    Args:
        path: Path to YAML file. Uses built-in suite.yaml if None.
        category: Filter to a single category letter (e.g. "A").
        include_extended: Include cases marked ``extended_only: true``. These
            cases are excluded by default because they add billable coverage.

    Returns:
        List of TestCase objects.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If the YAML structure is invalid.
    """
    yaml_path = path or _default_suite_path()
    if not yaml_path.exists():
        msg = f"Test suite file not found: {yaml_path}"
        raise FileNotFoundError(msg)

    try:
        raw = yaml.safe_load(yaml_path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML syntax in {yaml_path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"Cannot read test suite {yaml_path}: {exc}") from exc
    if (
        not isinstance(raw, dict)
        or "test_cases" not in raw
        or not isinstance(raw["test_cases"], list)
    ):
        msg = f"Invalid YAML: expected top-level 'test_cases' key in {yaml_path}"
        raise ValueError(msg)

    cases: list[TestCase] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(raw["test_cases"]):
        if not isinstance(entry, dict):
            raise ValueError(f"Test case at index {index} must be a mapping")
        unknown_fields = sorted(
            (
                repr(field)
                for field in entry
                if not isinstance(field, str) or field not in _CASE_FIELDS
            )
        )
        if unknown_fields:
            raise ValueError(
                f"Test case at index {index}: unknown fields " + ", ".join(unknown_fields)
            )
        if "turns" in entry:
            raise ValueError(
                f"Test case at index {index}: multi-turn rows are not supported by this runner"
            )
        missing_fields = [field for field in ("id", "query", "category") if field not in entry]
        if missing_fields:
            raise ValueError(
                f"Test case at index {index}: missing required fields {missing_fields}"
            )
        for field_name in ("id", "query", "category"):
            value = entry[field_name]
            if not isinstance(value, str) or not value.strip():
                raise ValueError(
                    f"Test case at index {index}: {field_name} must be a non-empty string"
                )
        case_id = entry["id"].strip()
        query_type = entry.get("query_type", "general")
        if not isinstance(query_type, str) or not query_type.strip():
            raise ValueError(f"Test case {case_id}: query_type must be a non-empty string")
        query_type = query_type.strip()
        description = entry.get("description", "")
        if not isinstance(description, str):
            raise ValueError(f"Test case {case_id}: description must be a string")
        smoke = entry.get("smoke", False)
        if not isinstance(smoke, bool):
            raise ValueError(f"Test case {case_id}: smoke must be a boolean")
        extended_only = entry.get("extended_only", False)
        if not isinstance(extended_only, bool):
            raise ValueError(f"Test case {case_id}: extended_only must be a boolean")
        cost_class = entry.get("cost_class")
        if cost_class is not None and cost_class not in {"low", "medium", "high"}:
            raise ValueError(f"Test case {case_id}: cost_class must be low, medium, or high")
        (
            expected_outcome,
            date_policy,
            expected_error_pattern,
            expected_response_pattern,
            forbidden_response_pattern,
        ) = _validated_case_metadata(entry, case_id=case_id)
        normalized_case_id = case_id.casefold()
        if normalized_case_id in seen_ids:
            raise ValueError(f"Duplicate test case ID: {case_id}")
        seen_ids.add(normalized_case_id)
        requires_builtin_scorers = entry.get("requires_builtin_scorers", False)
        if not isinstance(requires_builtin_scorers, bool):
            raise ValueError(f"Test case {case_id}: requires_builtin_scorers must be a boolean")
        if requires_builtin_scorers and expected_outcome not in {"success", "partial_refusal"}:
            raise ValueError(
                f"Test case {case_id}: requires_builtin_scorers is only valid for success "
                "or partial_refusal"
            )
        # A successful financial answer cannot be verified by routing and text
        # shape alone; relative windows are one example requiring semantic date
        # interpretation. Such rows may not advertise a regression pass under
        # --no-builtin. Deterministic failure/rejection contracts remain usable
        # in that mode, including relative queries that return no data.
        requires_builtin_scorers = expected_outcome in {"success", "partial_refusal"}

        max_age_seconds = entry.get("max_age_seconds")
        if max_age_seconds is not None and (
            isinstance(max_age_seconds, bool)
            or not isinstance(max_age_seconds, int)
            or max_age_seconds <= 0
        ):
            raise ValueError(f"Test case {case_id}: max_age_seconds must be a positive integer")
        if max_age_seconds is not None and date_policy != "live":
            raise ValueError(
                f"Test case {case_id}: max_age_seconds is only valid with date_policy=live"
            )

        forbidden_tools = entry.get("forbidden_tools", [])
        if not isinstance(forbidden_tools, list) or not all(
            isinstance(tool, str) and tool.strip() for tool in forbidden_tools
        ):
            raise ValueError(f"Test case {case_id}: forbidden_tools must be a list of names")
        forbidden_tools = [tool.strip() for tool in forbidden_tools]
        expected_tools = entry.get("expected_tools", [])
        if not isinstance(expected_tools, list) or not all(
            isinstance(tool, str) and tool.strip() for tool in expected_tools
        ):
            raise ValueError(f"Test case {case_id}: expected_tools must be a list of names")
        expected_tools = [tool.strip() for tool in expected_tools]
        if len(set(expected_tools)) != len(expected_tools):
            raise ValueError(f"Test case {case_id}: expected_tools contains duplicates")
        if len(set(forbidden_tools)) != len(forbidden_tools):
            raise ValueError(f"Test case {case_id}: forbidden_tools contains duplicates")
        if set(forbidden_tools) & set(expected_tools):
            raise ValueError(f"Test case {case_id}: a tool cannot be both expected and forbidden")
        expected_sequence = entry.get("expected_sequence")
        if expected_sequence is not None and (
            not isinstance(expected_sequence, list)
            or not all(isinstance(tool, str) and tool.strip() for tool in expected_sequence)
        ):
            raise ValueError(f"Test case {case_id}: expected_sequence must be a list of tool names")
        if expected_sequence is not None:
            expected_sequence = [tool.strip() for tool in expected_sequence]
            if len(set(expected_sequence)) != len(expected_sequence):
                raise ValueError(f"Test case {case_id}: expected_sequence contains duplicates")
            undeclared_sequence_tools = sorted(set(expected_sequence) - set(expected_tools))
            if undeclared_sequence_tools:
                raise ValueError(
                    f"Test case {case_id}: expected_sequence tools must also be expected: "
                    + ", ".join(undeclared_sequence_tools)
                )

        allowed_partial_errors = entry.get("allowed_partial_errors", [])
        if not isinstance(allowed_partial_errors, list):
            raise ValueError(
                f"Test case {case_id}: allowed_partial_errors must be a list of contracts"
            )
        if allowed_partial_errors and expected_outcome != "success":
            raise ValueError(
                f"Test case {case_id}: allowed_partial_errors is only valid for success"
            )
        normalized_partial_errors: list[dict[str, str]] = []
        for contract_index, contract in enumerate(allowed_partial_errors):
            if not isinstance(contract, dict) or set(contract) != {
                "tool",
                "error_pattern",
                "response_pattern",
                "forbidden_response_pattern",
            }:
                raise ValueError(
                    f"Test case {case_id}: allowed_partial_errors[{contract_index}] must "
                    "contain exactly tool, error_pattern, response_pattern, and "
                    "forbidden_response_pattern"
                )
            if contract["tool"] not in expected_tools:
                raise ValueError(f"Test case {case_id}: recoverable-error tool must be expected")
            for pattern_name in (
                "error_pattern",
                "response_pattern",
                "forbidden_response_pattern",
            ):
                pattern = contract[pattern_name]
                if not isinstance(pattern, str) or not pattern.strip():
                    raise ValueError(
                        f"Test case {case_id}: {pattern_name} must be a non-empty regex"
                    )
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"Test case {case_id}: invalid {pattern_name}: {exc}") from exc
            normalized_partial_errors.append(
                {
                    "tool": contract["tool"],
                    "error_pattern": contract["error_pattern"],
                    "response_pattern": contract["response_pattern"],
                    "forbidden_response_pattern": contract["forbidden_response_pattern"],
                }
            )

        if extended_only and not include_extended:
            continue

        tc = TestCase(
            id=case_id,
            query=entry["query"].strip(),
            category=entry["category"].strip(),
            query_type=query_type,
            expected_tools=expected_tools,
            expected_sequence=expected_sequence,
            description=description,
            expect_rejection=entry.get("expect_rejection", False),
            smoke=smoke,
            expected_outcome=expected_outcome,
            expected_error_pattern=expected_error_pattern,
            expected_response_pattern=expected_response_pattern,
            forbidden_response_pattern=forbidden_response_pattern,
            date_policy=date_policy,
            max_age_seconds=max_age_seconds,
            forbidden_tools=forbidden_tools,
            allowed_partial_errors=normalized_partial_errors,
            extended_only=extended_only,
            cost_class=cost_class,
            requires_builtin_scorers=requires_builtin_scorers,
        )

        if category and tc.category.upper() != category.upper():
            continue

        cases.append(tc)

    return cases


def trace_to_scorer_input(trace: Trace) -> dict[str, Any]:
    """Convert OBaI Trace to format expected by scorers.

    Scorers expect a dict with specific keys. This function maps
    our Trace structure to that format.

    Args:
        trace: Completed execution trace.

    Returns:
        Dict compatible with scorer score() methods.
    """
    # Build tool outputs context (for hallucination checking)
    tool_outputs = [f"Evaluation trace started at {trace.start_time.isoformat()}"]
    has_tool_errors = False
    structured_errors: list[dict[str, Any]] = []

    for tc in trace.tool_calls:
        if tc.response:
            tool_outputs.append(f"{tc.tool_name}: {tc.response}")
        if tc.error:
            has_tool_errors = True
            tool_outputs.append(f"{tc.tool_name}: ERROR: {tc.error}")
            structured_errors.append(
                {
                    "source": "tool_call",
                    "tool_name": tc.tool_name,
                    "agent_name": tc.agent_name,
                    "message": tc.error,
                    "specialist": True,
                }
            )
        elif isinstance(tc.response, dict):
            response_error = _structured_error_message(tc.response)
            if response_error is not None:
                has_tool_errors = True
                tool_outputs.append(f"{tc.tool_name}: ERROR: {response_error}")
                structured_errors.append(
                    {
                        "source": "tool_response",
                        "tool_name": tc.tool_name,
                        "agent_name": tc.agent_name,
                        "message": response_error,
                        "specialist": True,
                    }
                )

    # Build inner MCP tool calls (raw data from specialist→MCP).
    # Skip cache entries — cached responses are prior agent output,
    # not ground-truth tool data, so using them for faithfulness
    # checking would be circular.
    inner_tool_calls: list[dict[str, Any]] = []
    for inner in trace.inner_tool_outputs:
        specialist = inner.get("specialist", "unknown")
        tool_name = inner.get("tool_name", "unknown")
        raw_output = inner.get("output", "")
        if specialist == "cache":
            continue
        response: dict[str, Any] | None = None
        if isinstance(raw_output, str):
            try:
                parsed = json.loads(raw_output)
                response = parsed if isinstance(parsed, dict) else {"raw": raw_output}
            except (json.JSONDecodeError, TypeError):
                response = {"raw": raw_output}
        elif isinstance(raw_output, dict):
            response = raw_output

        # Skip errored MCP calls — no ground truth to score against.
        # Still score valid calls rather than skipping everything.
        response_error = _structured_error_message(response) if isinstance(response, dict) else None
        if response_error is not None:
            has_tool_errors = True
            tool_outputs.append(f"{specialist}/{tool_name}: ERROR: {response_error}")
            structured_errors.append(
                {
                    "source": "inner_tool",
                    "tool_name": tool_name,
                    "agent_name": specialist,
                    "message": response_error,
                    "specialist": True,
                }
            )
            continue

        tool_outputs.append(f"{specialist}/{tool_name}: {raw_output}")
        inner_tool_calls.append(
            {
                "tool_name": f"{specialist}/{tool_name}",
                "args": {},
                "response": response,
                "latency_ms": 0,
                "agent_name": specialist,
            }
        )

    for event in trace.events:
        if event.event_type != EventType.ERROR:
            continue
        agent_name = getattr(event, "agent_name", None)
        message = getattr(event, "error_message", None)
        structured_errors.append(
            {
                "source": "trace_event",
                "agent_name": agent_name,
                "message": str(message or "trace execution error"),
                "specialist": bool(
                    agent_name
                    and str(agent_name).lower()
                    not in {"guardrail", "hub", "obai hub", "orchestrator"}
                ),
            }
        )
        tool_outputs.append(f"{agent_name or 'trace'}: ERROR: {message or 'trace execution error'}")

    return {
        # Core fields
        "query": trace.query,
        "response": trace.final_response or "",
        "guardrail_passed": trace.guardrail_passed,
        "trace_start_time": trace.start_time.isoformat(),
        "trace_end_time": trace.end_time.isoformat() if trace.end_time else None,
        "structured_errors": structured_errors,
        # Data availability — False only when ALL inner tool calls errored
        # (no valid ground truth at all). Partial errors are filtered out
        # above; remaining valid calls are still scored.
        "data_available": bool(inner_tool_calls) or not has_tool_errors,
        # Outer tool calls (hub→specialist) — for orchestration/efficiency
        "tool_calls": [
            {
                "tool_name": tc.tool_name,
                "args": tc.args,
                "response": tc.response,
                "error": tc.error,
                "latency_ms": tc.latency_ms,
                "timestamp": tc.timestamp.isoformat(),
                "agent_name": tc.agent_name,
            }
            for tc in trace.tool_calls
        ],
        # Inner tool calls (specialist→MCP) — raw ground truth
        "inner_tool_calls": inner_tool_calls,
        # Context for hallucination checking
        "tool_outputs": "\n\n".join(tool_outputs),
        # Metrics
        "specialists_called": trace.metrics.specialists_called,
        "call_sequence": trace.metrics.call_sequence,
        "total_tool_calls": trace.metrics.total_tool_calls,
        # Timing
        "total_ms": trace.metrics.timing.total_ms if trace.metrics.timing else None,
    }


class OBaIEvaluator:
    """Evaluator for OBaI multi-agent system.

    This class orchestrates running queries through the agent system,
    capturing traces, and scoring them with Opik metrics.

    Example:
        >>> evaluator = OBaIEvaluator()
        >>> results = await evaluator.run_evaluation(test_cases)
    """

    def __init__(
        self,
        use_builtin_scorers: bool = True,
        judge_model: str | None = None,
    ) -> None:
        """Initialize the evaluator.

        Args:
            use_builtin_scorers: Include Opik built-in scorers.
            judge_model: Anthropic model ID for LLM-based scorers.
                Falls back to config EVAL_JUDGE_MODEL if not provided.
        """
        config = get_config()
        self.use_builtin_scorers = use_builtin_scorers
        self.judge_model = judge_model or config.eval_judge_model
        self._scorers: list[Any] = []

    def _build_scorers(self, test_case: TestCase) -> list[Any]:
        """Build scorer list for a specific test case.

        Args:
            test_case: The test case to build scorers for.

        Returns:
            List of configured scorers.
        """
        scorers: list[Any] = []

        # Built-in Opik scorers (LLM-based, use config model)
        if self.use_builtin_scorers and test_case.expected_outcome == "success":
            scorers.extend(get_builtin_scorers(get_config().eval_builtin_model))
        elif self.use_builtin_scorers and test_case.expected_outcome == "partial_refusal":
            scorers.append(PartialRefusalSemanticScorer(model_id=self.judge_model))

        # Declared metadata is an executable deterministic contract, not merely
        # experiment decoration.
        scorers.append(
            OutcomeContractScorer(
                expected_outcome=test_case.expected_outcome,
                expected_tools=test_case.expected_tools,
                expected_error_pattern=test_case.expected_error_pattern,
                expected_response_pattern=test_case.expected_response_pattern,
                forbidden_response_pattern=test_case.forbidden_response_pattern,
                allowed_partial_errors=test_case.allowed_partial_errors,
            )
        )
        # Freshness evidence is meaningful only when the declared outcome
        # returns financial data. A correct data-unavailable/refusal/error
        # response has no quote or filing timestamp to validate and must not be
        # turned into a false failure by the optional date scorer.
        if test_case.date_policy is not None and test_case.expected_outcome == "success":
            scorers.append(
                DatePolicyScorer(
                    date_policy=test_case.date_policy,
                    expected_tools=test_case.expected_tools,
                    max_age_seconds=test_case.max_age_seconds,
                )
            )

        # Tool orchestration (always)
        if test_case.expected_tools or test_case.forbidden_tools:
            scorers.append(
                ToolOrchestrationScorer(
                    expected_tools=test_case.expected_tools,
                    allow_extra=True,
                    forbidden_tools=test_case.forbidden_tools,
                )
            )

        # Sequence validation (if applicable)
        if test_case.expected_sequence:
            scorers.append(
                SequenceScorer(
                    expected_sequence=test_case.expected_sequence,
                    strict=False,
                )
            )

        # Response quality (non-LLM, always include)
        if test_case.expected_outcome == "success":
            min_response_length = 30
        elif test_case.expected_outcome == "hub_reject":
            min_response_length = 0
        else:
            min_response_length = 10
        scorers.append(
            ResponseQualityScorer(
                min_length=min_response_length,
                # The presence of an arbitrary digit is not evidence of
                # financial correctness and rejects valid symbol/name answers.
                # Exactness belongs to the semantic/numeric scorers.
                require_numbers=False,
            )
        )

        # Efficiency scorer
        scorers.append(
            EfficiencyScorer(
                max_tool_calls=5,
                penalize_redundant=True,
            )
        )

        # Strategy artifact contract (deterministic, only for build/backtest flows)
        if _needs_strategy_contract_scorer(test_case.query_type):
            scorers.append(StrategyContractScorer())
            scorers.append(StrategyGroundingScorer())

        # LLM-judge rubric scorer (multi-dimensional)
        if self.use_builtin_scorers and test_case.expected_outcome == "success":
            scorers.append(LLMJudgeScorer(model_id=self.judge_model))
            if _needs_strategy_contract_scorer(test_case.query_type):
                scorers.append(StrategyDecisionScorer(model_id=self.judge_model))

        # Ground-truth verification scorers (async, LLM-based)
        if self.use_builtin_scorers and test_case.expected_outcome == "success":
            scorers.append(
                FaithfulnessScorer(
                    model_id=self.judge_model,
                    numeric_threshold=0.9,
                )
            )
            scorers.append(
                CompletenessScorer(
                    model_id=self.judge_model,
                    coverage_threshold=0.7,
                )
            )

        return scorers

    def expected_scorer_names(self, test_case: TestCase) -> list[str]:
        """Return the locally configured scorer plan for one case.

        This is computed from the selected :class:`TestCase`, not from a
        remote dataset row or an evaluator result.  The CLI binds this trusted
        plan to collected results before deciding whether a paid suite passed.
        """
        return [type(scorer).__name__ for scorer in self._build_scorers(test_case)]

    async def evaluate_trace(
        self,
        trace: Trace,
        test_case: TestCase,
    ) -> dict[str, Any]:
        """Evaluate a single trace against a test case.

        Args:
            trace: Completed execution trace.
            test_case: The test case with expectations.

        Returns:
            Dict with all scorer results.
        """
        scorers = self._build_scorers(test_case)
        scorer_input = trace_to_scorer_input(trace)

        results: dict[str, Any] = {
            "query": trace.query,
            "query_type": test_case.query_type,
            "expected_scorers": [type(scorer).__name__ for scorer in scorers],
            "scores": {},
        }

        for scorer in scorers:
            scorer_name = type(scorer).__name__
            try:
                # Call score method with appropriate args
                if hasattr(scorer, "score"):
                    # Custom scorers (may be sync or async)
                    score_result = scorer.score(output=scorer_input, query=trace.query)
                    if asyncio.iscoroutine(score_result):
                        score_result = await score_result
                else:
                    # Built-in scorers may have different interface
                    score_result = scorer.score(scorer_input)

                results["scores"][scorer_name] = score_result
            except APIStatusError as e:
                logger.error("API error — aborting evaluation: %s", e)
                results["scores"][scorer_name] = {"error": str(e)}
                results["aborted"] = True
                results["abort_reason"] = str(e)
                return results
            except Exception as e:
                logger.exception(f"Scorer {scorer_name} failed")
                results["scores"][scorer_name] = {"error": str(e)}

        return results


# Pre-defined test cases for common query types
STANDARD_TEST_CASES: list[TestCase] = [
    # Simple single-agent queries
    TestCase(
        query="What is AAPL trading at?",
        expected_tools=["market_data_analysis"],
        query_type="price",
        description="Simple price query - single agent",
    ),
    TestCase(
        query="What's Tesla's P/E ratio?",
        expected_tools=["fundamentals_analysis"],
        query_type="fundamentals",
        description="Simple fundamentals query - single agent",
    ),
    TestCase(
        query="Any recent news on Microsoft?",
        expected_tools=["events_news_analysis"],
        query_type="news",
        description="Simple news query - single agent",
    ),
    # Multi-agent queries
    TestCase(
        query="What's NVDA trading at and what's its P/E?",
        expected_tools=["market_data_analysis", "fundamentals_analysis"],
        query_type="multi_domain",
        description="Multi-domain query - two agents",
    ),
    # Dependency queries (require sequencing)
    TestCase(
        query="What's Palantir trading at?",
        expected_tools=["screener_lookup", "market_data_analysis"],
        expected_sequence=["screener_lookup", "market_data_analysis"],
        query_type="ticker_lookup_then_price",
        description="Company name → ticker lookup → price",
    ),
    TestCase(
        query="What's Snowflake's P/E ratio?",
        expected_tools=["screener_lookup", "fundamentals_analysis"],
        expected_sequence=["screener_lookup", "fundamentals_analysis"],
        query_type="ticker_lookup_then_fundamentals",
        description="Company name → ticker lookup → fundamentals",
    ),
    # Options queries
    TestCase(
        query="Show me AAPL options expiring this month",
        expected_tools=["options_analysis"],
        query_type="options",
        description="Simple options query",
    ),
]
