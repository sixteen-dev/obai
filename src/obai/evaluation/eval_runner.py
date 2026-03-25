"""Evaluation runner for OBaI multi-agent system.

This module provides the evaluation infrastructure using Opik metrics.
It converts OBaI traces to the format expected by scorers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from openai import APIStatusError

from evaluation.scorers._llm_client import DEFAULT_JUDGE_MODEL
from evaluation.scorers.builtin import get_builtin_scorers
from evaluation.scorers.custom import (
    EfficiencyScorer,
    ResponseQualityScorer,
    SequenceScorer,
    StrategyContractScorer,
    StrategyDecisionScorer,
    StrategyGroundingScorer,
    ToolOrchestrationScorer,
)
from evaluation.scorers.faithfulness import CompletenessScorer, FaithfulnessScorer
from evaluation.scorers.llm_judge import LLMJudgeScorer
from evaluation.trace.types import Trace

logger = logging.getLogger(__name__)


def _needs_strategy_contract_scorer(query_type: str) -> bool:
    """Return True for strategy build/backtest flows with final-artifact expectations."""
    return (
        query_type.startswith("strategy_direct_")
        or query_type.startswith("strategy_design_")
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
) -> list[TestCase]:
    """Load test cases from a YAML file.

    Args:
        path: Path to YAML file. Uses built-in suite.yaml if None.
        category: Filter to a single category letter (e.g. "A").

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

    raw = yaml.safe_load(yaml_path.read_text())
    if not isinstance(raw, dict) or "test_cases" not in raw:
        msg = f"Invalid YAML: expected top-level 'test_cases' key in {yaml_path}"
        raise ValueError(msg)

    cases: list[TestCase] = []
    for entry in raw["test_cases"]:
        # Skip multi-turn (Category E) — they have 'turns' instead of 'query'
        if "turns" in entry:
            logger.warning("Skipping multi-turn test %s (not yet supported)", entry.get("id", "?"))
            continue

        if "query" not in entry or "id" not in entry or "category" not in entry:
            logger.warning("Skipping invalid test case (missing query/id/category): %s", entry)
            continue

        tc = TestCase(
            id=entry["id"],
            query=entry["query"],
            category=entry["category"],
            query_type=entry.get("query_type", "general"),
            expected_tools=entry.get("expected_tools", []),
            expected_sequence=entry.get("expected_sequence"),
            description=entry.get("description", ""),
            expect_rejection=entry.get("expect_rejection", False),
            smoke=entry.get("smoke", False),
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
    tool_outputs = []
    has_tool_errors = False

    for tc in trace.tool_calls:
        if tc.response:
            tool_outputs.append(f"{tc.tool_name}: {tc.response}")
        if tc.error:
            has_tool_errors = True

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
        if isinstance(response, dict) and response.get("isError"):
            has_tool_errors = True
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

    return {
        # Core fields
        "query": trace.query,
        "response": trace.final_response or "",
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
                "latency_ms": tc.latency_ms,
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
        judge_model: str = DEFAULT_JUDGE_MODEL,
    ) -> None:
        """Initialize the evaluator.

        Args:
            use_builtin_scorers: Include Opik built-in scorers.
            judge_model: Anthropic model ID for LLM-based scorers.
        """
        self.use_builtin_scorers = use_builtin_scorers
        self.judge_model = judge_model
        self._scorers: list[Any] = []

    def _build_scorers(self, test_case: TestCase) -> list[Any]:
        """Build scorer list for a specific test case.

        Args:
            test_case: The test case to build scorers for.

        Returns:
            List of configured scorers.
        """
        scorers: list[Any] = []

        # Built-in Opik scorers (LLM-based, use Haiku by default)
        if self.use_builtin_scorers:
            scorers.extend(get_builtin_scorers())

        # Tool orchestration (always)
        if test_case.expected_tools:
            scorers.append(
                ToolOrchestrationScorer(
                    expected_tools=test_case.expected_tools,
                    allow_extra=True,
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
        scorers.append(
            ResponseQualityScorer(
                min_length=30,
                require_numbers=True,
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
        if self.use_builtin_scorers:
            scorers.append(LLMJudgeScorer(model_id=self.judge_model))
            if _needs_strategy_contract_scorer(test_case.query_type):
                scorers.append(StrategyDecisionScorer(model_id=self.judge_model))

        # Ground-truth verification scorers (async, LLM-based)
        if self.use_builtin_scorers:
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
