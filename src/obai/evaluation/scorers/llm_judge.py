"""LLM-as-judge rubric scorer for multi-dimensional evaluation.

Evaluates agent responses across 5 quality dimensions (1-5 scale each)
using AsyncOpenAI (Anthropic endpoint) with Pydantic structured output.

Dimensions:
    - Factual Accuracy: Are stated facts correct and grounded in tool outputs?
    - Completeness: Does the response fully address the user's query?
    - Clarity: Is the response well-structured and easy to understand?
    - Tool Use Quality: Were the right tools called with appropriate arguments?
    - Reasoning Soundness: Is the agent's reasoning logical and coherent?
"""

from __future__ import annotations

import json
import logging
from typing import Any

import opik
from pydantic import BaseModel, Field

from evaluation.scorers._llm_client import DEFAULT_JUDGE_MODEL, structured_completion

logger = logging.getLogger(__name__)

MAX_TOOL_OUTPUT_CHARS = 4000
MAX_ARG_CHARS = 200

RUBRIC_DIMENSIONS: list[str] = [
    "factual_accuracy",
    "completeness",
    "clarity",
    "tool_use_quality",
    "reasoning_soundness",
]

DEFAULT_THRESHOLDS: dict[str, int] = {dim: 3 for dim in RUBRIC_DIMENSIONS}

SYSTEM_PROMPT = """\
You are an expert evaluator for a multi-agent financial research assistant.
Score the agent's response on each dimension using a 1-5 scale.

## Scoring Anchors

### Factual Accuracy
- 1: Multiple incorrect facts or fabricated data
- 3: Mostly correct with minor inaccuracies
- 5: All facts are accurate and grounded in tool outputs

### Completeness
- 1: Fails to address the query or missing critical information
- 3: Addresses the main question but misses some relevant details
- 5: Thoroughly addresses all aspects of the query

### Clarity
- 1: Disorganized, confusing, or incoherent response
- 3: Understandable but could be better structured
- 5: Well-organized, concise, and easy to follow

### Tool Use Quality
- 1: Wrong tools called, unnecessary calls, or missing critical tools
- 3: Correct tools called but suboptimal arguments or ordering
- 5: Optimal tool selection, arguments, and sequencing

### Reasoning Soundness
- 1: Illogical conclusions or contradictory statements
- 3: Generally sound reasoning with minor gaps
- 5: Clear, logical reasoning chain from evidence to conclusion

Provide a brief reasoning for each score."""

USER_PROMPT_TEMPLATE = """\
<user_query>
{query}
</user_query>

<agent_response>
{response}
</agent_response>

<tool_calls>
{tool_calls}
</tool_calls>

<tool_results_ground_truth>
{tool_outputs}
</tool_results_ground_truth>

Score each dimension 1-5 with reasoning."""


class DimensionScore(BaseModel):
    """Score for a single evaluation dimension."""

    score: int = Field(ge=1, le=5, description="Score from 1 to 5")
    reasoning: str


class RubricResponse(BaseModel):
    """Full rubric response from the LLM judge."""

    factual_accuracy: DimensionScore
    completeness: DimensionScore
    clarity: DimensionScore
    tool_use_quality: DimensionScore
    reasoning_soundness: DimensionScore


def _format_tool_calls(tool_calls: list[dict[str, Any]]) -> str:
    """Format tool calls for the judge prompt.

    Args:
        tool_calls: List of tool call dicts from trace.

    Returns:
        Formatted string with tool name, agent, args, latency.
    """
    if not tool_calls:
        return "(no tool calls)"

    lines: list[str] = []
    for tc in tool_calls:
        name = tc.get("tool_name", "unknown")
        agent = tc.get("agent_name", "unknown")
        latency = tc.get("latency_ms")
        raw_args = tc.get("args", {})

        args_str = json.dumps(raw_args, default=str)
        if len(args_str) > MAX_ARG_CHARS:
            args_str = args_str[:MAX_ARG_CHARS] + "..."

        latency_str = f", latency={latency:.0f}ms" if latency else ""
        lines.append(f"- {name} (agent={agent}{latency_str}): {args_str}")

    return "\n".join(lines)


def _build_success_result(
    rubric: RubricResponse,
    thresholds: dict[str, int],
) -> dict[str, Any]:
    """Build result dict from a successful rubric response.

    Args:
        rubric: Parsed rubric response from LLM judge.
        thresholds: Minimum score per dimension to pass.

    Returns:
        Dict with per-dimension scores, average, and pass/fail.
    """
    result: dict[str, Any] = {}
    scores: list[int] = []

    for dim_name in RUBRIC_DIMENSIONS:
        dim_score: DimensionScore = getattr(rubric, dim_name)
        threshold = thresholds.get(dim_name, 3)
        passed = dim_score.score >= threshold
        scores.append(dim_score.score)

        result[dim_name] = {
            "score": dim_score.score,
            "reasoning": dim_score.reasoning,
            "threshold": threshold,
            "passed": passed,
        }

    average = sum(scores) / len(scores) if scores else 0.0
    all_passed = all(result[d]["passed"] for d in RUBRIC_DIMENSIONS)

    result["average_score"] = round(average, 2)
    result["rubric_pass"] = all_passed

    return result


def _build_error_result(error_msg: str) -> dict[str, Any]:
    """Build degraded result dict when LLM judge fails.

    Args:
        error_msg: Description of the error.

    Returns:
        Dict with same shape as success but score=None, passed=False.
    """
    result: dict[str, Any] = {}

    for dim_name in RUBRIC_DIMENSIONS:
        result[dim_name] = {
            "score": None,
            "reasoning": "",
            "threshold": DEFAULT_THRESHOLDS[dim_name],
            "passed": False,
        }

    result["average_score"] = None
    result["rubric_pass"] = False
    result["error"] = error_msg

    return result


@opik.track()  # type: ignore[untyped-decorator]  # opik doesn't preserve type sig
async def llm_judge_rubric_scorer(
    output: dict[str, Any],
    query: str,
    model_id: str = DEFAULT_JUDGE_MODEL,
    thresholds: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Score agent output using an LLM judge across 5 rubric dimensions.

    Args:
        output: Model output dict from trace_to_scorer_input().
        query: Original user query.
        model_id: Anthropic model ID for the judge.
        thresholds: Minimum score per dimension (default: 3 for all).

    Returns:
        Dict with per-dimension scores, average_score, and rubric_pass.
        Returns skipped=True when tools returned errors.
    """
    # Skip when tool calls returned errors — rubric dimensions like
    # factual_accuracy and completeness can't be scored without data
    if not output.get("data_available", True):
        result: dict[str, Any] = {"skipped": True, "skip_reason": "tool_errors"}
        for dim_name in RUBRIC_DIMENSIONS:
            result[dim_name] = {"score": None, "reasoning": "", "threshold": 3, "passed": None}
        result["average_score"] = None
        result["rubric_pass"] = None
        return result

    effective_thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    response_text = output.get("response", "")
    tool_calls = output.get("tool_calls", [])
    tool_outputs = output.get("tool_outputs", "")

    # Truncate tool_outputs to avoid context blowout
    if len(tool_outputs) > MAX_TOOL_OUTPUT_CHARS:
        tool_outputs = tool_outputs[:MAX_TOOL_OUTPUT_CHARS] + "\n...(truncated)"

    user_prompt = USER_PROMPT_TEMPLATE.format(
        query=query,
        response=response_text,
        tool_calls=_format_tool_calls(tool_calls),
        tool_outputs=tool_outputs,
    )

    try:
        rubric = await structured_completion(
            model=model_id,
            system=SYSTEM_PROMPT,
            user=user_prompt,
            response_model=RubricResponse,
            temperature=0.0,
        )
        return _build_success_result(rubric, effective_thresholds)

    except Exception as e:
        err = str(e)
        if "401" in err or "authentication" in err.lower() or "bearer" in err.lower():
            logger.error("LLM judge auth failed — set ANTHROPIC_API_KEY env var")
            return _build_error_result("Auth failed — set ANTHROPIC_API_KEY env var")
        logger.exception("LLM judge scorer failed")
        return _build_error_result("LLM judge call failed")


class LLMJudgeScorer:
    """Wrapper for llm_judge_rubric_scorer with configuration.

    Follows the same class-wrapper pattern as other OBaI scorers,
    but with an async score() method since it calls an LLM.

    Example:
        >>> scorer = LLMJudgeScorer(model_id="anthropic/claude-sonnet-4-5-20250929")
        >>> result = await scorer.score(output=trace_output, query="AAPL price?")
        >>> result["rubric_pass"]
        True
    """

    def __init__(
        self,
        model_id: str = DEFAULT_JUDGE_MODEL,
        thresholds: dict[str, int] | None = None,
    ) -> None:
        """Initialize with judge model and thresholds.

        Args:
            model_id: Anthropic model ID for the judge.
            thresholds: Minimum score per dimension to pass.
        """
        self.model_id = model_id
        self.thresholds = thresholds

    async def score(
        self,
        output: dict[str, Any],
        query: str = "",
    ) -> dict[str, Any]:
        """Score agent output using the LLM judge.

        Args:
            output: Model output dict from trace_to_scorer_input().
            query: Original user query.

        Returns:
            Dict with per-dimension scores, average_score, and rubric_pass.
        """
        result: dict[str, Any] = await llm_judge_rubric_scorer(
            output=output,
            query=query,
            model_id=self.model_id,
            thresholds=self.thresholds,
        )
        return result
