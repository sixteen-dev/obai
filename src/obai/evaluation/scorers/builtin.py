"""Opik built-in metrics configured for OBaI evaluation.

Wraps Opik LLM-as-judge metrics in OBaI's scorer interface
(class with .score() returning dict). Each wrapper adapts Opik's
ScoreResult(value, name, reason) into our standard dict format.

Requires: pip install opik
"""

from __future__ import annotations

import json
import logging
from typing import Any

from opik.evaluation.metrics import (
    AgentTaskCompletionJudge,
    AgentToolCorrectnessJudge,
    AnswerRelevance,
)

logger = logging.getLogger(__name__)

BUILTIN_JUDGE_MODEL = "anthropic/claude-haiku-4-5-20251001"


def _format_agent_trace(output: dict[str, Any], query: str) -> str:
    """Format scorer output dict as a conversation trace string.

    Opik's AgentTaskCompletionJudge and AgentToolCorrectnessJudge only
    accept ``output: str``. This builds a readable trace with the user
    query, tool calls, and final response so the judge has full context.

    Args:
        output: Scorer output dict with response, tool_calls, etc.
        query: Original user query.

    Returns:
        Formatted trace string.
    """
    parts: list[str] = [f"User query: {query}"]

    tool_calls = output.get("tool_calls", [])
    if tool_calls:
        parts.append("\nTool calls:")
        for tc in tool_calls:
            name = tc.get("tool_name", "unknown")
            agent = tc.get("agent_name", "")
            args = json.dumps(tc.get("args", {}), default=str)
            parts.append(f"  - {name} (agent={agent}): {args[:200]}")

    response = output.get("response", "")
    if response:
        parts.append(f"\nAgent response:\n{response}")

    return "\n".join(parts)


class AnswerRelevanceScorer:
    """Rate response relevancy to the original query.

    Uses Opik's AnswerRelevance metric (LLM judge).
    Score is 0.0-1.0 where higher = more relevant.

    Args:
        model_id: LiteLLM model ID for the judge.
    """

    def __init__(self, model_id: str = BUILTIN_JUDGE_MODEL) -> None:
        """Initialize with judge model."""
        self._metric = AnswerRelevance(model=model_id, require_context=False)

    def score(self, output: dict[str, Any], query: str = "") -> dict[str, Any]:
        """Score response relevancy.

        Args:
            output: Model output with 'response' key.
            query: Original user query.

        Returns:
            Dict with relevant bool, score float, and reason.
        """
        result = self._metric.score(
            input=query,
            output=output.get("response", ""),
        )
        return {
            "relevant": result.value >= 0.5,
            "score": result.value,
            "reason": result.reason or "",
        }


class TaskCompletionScorer:
    """Assess whether the agent completed the user's task.

    Uses Opik's AgentTaskCompletionJudge metric (LLM judge).
    Formats a conversation trace string since the metric only
    accepts ``output: str``.

    Args:
        model_id: LiteLLM model ID for the judge.
    """

    def __init__(self, model_id: str = BUILTIN_JUDGE_MODEL) -> None:
        """Initialize with judge model."""
        self._metric = AgentTaskCompletionJudge(model=model_id)

    def score(self, output: dict[str, Any], query: str = "") -> dict[str, Any]:
        """Score task completion.

        Args:
            output: Model output with 'response' and 'tool_calls' keys.
            query: Original user query.

        Returns:
            Dict with task_completed bool, score float, and reason.
        """
        result = self._metric.score(
            output=_format_agent_trace(output, query),
        )
        return {
            "task_completed": result.value >= 0.5,
            "score": result.value,
            "reason": result.reason or "",
        }


class ToolCorrectnessScorer:
    """Assess whether the agent used tools correctly.

    Uses Opik's AgentToolCorrectnessJudge metric (LLM judge).
    Formats a conversation trace string since the metric only
    accepts ``output: str``.

    Args:
        model_id: LiteLLM model ID for the judge.
    """

    def __init__(self, model_id: str = BUILTIN_JUDGE_MODEL) -> None:
        """Initialize with judge model."""
        self._metric = AgentToolCorrectnessJudge(model=model_id)

    def score(self, output: dict[str, Any], query: str = "") -> dict[str, Any]:
        """Score tool correctness.

        Args:
            output: Model output with 'response' and 'tool_calls' keys.
            query: Original user query.

        Returns:
            Dict with tools_correct bool, score float, and reason.
        """
        result = self._metric.score(
            output=_format_agent_trace(output, query),
        )
        return {
            "tools_correct": result.value >= 0.5,
            "score": result.value,
            "reason": result.reason or "",
        }


def get_builtin_scorers(model_id: str = BUILTIN_JUDGE_MODEL) -> list[Any]:
    """Get all relevant built-in Opik scorers for OBaI evaluation.

    Args:
        model_id: LiteLLM model ID for LLM-based scorers.

    Returns:
        List of configured scorer instances.
    """
    return [
        AnswerRelevanceScorer(model_id),
        TaskCompletionScorer(model_id),
        ToolCorrectnessScorer(model_id),
    ]
