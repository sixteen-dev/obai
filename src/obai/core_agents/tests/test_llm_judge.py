"""Unit tests for the LLM-judge rubric scorer.

All tests mock structured_completion to avoid real API calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from evaluation.scorers.llm_judge import (
    DEFAULT_THRESHOLDS,
    RUBRIC_DIMENSIONS,
    DimensionScore,
    LLMJudgeScorer,
    RubricResponse,
    _build_error_result,
    _build_success_result,
    _format_tool_calls,
    llm_judge_rubric_scorer,
)

# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestDimensionScore:
    """Test DimensionScore Pydantic validation."""

    def test_valid_scores(self):
        """Verify scores 1-5 are accepted."""
        for score_val in (1, 2, 3, 4, 5):
            ds = DimensionScore(score=score_val, reasoning="ok")
            assert ds.score == score_val

    def test_rejects_zero(self):
        """Verify score=0 raises ValidationError."""
        with pytest.raises(ValidationError):
            DimensionScore(score=0, reasoning="too low")

    def test_rejects_six(self):
        """Verify score=6 raises ValidationError."""
        with pytest.raises(ValidationError):
            DimensionScore(score=6, reasoning="too high")

    def test_rejects_negative(self):
        """Verify negative score raises ValidationError."""
        with pytest.raises(ValidationError):
            DimensionScore(score=-1, reasoning="negative")


class TestRubricResponse:
    """Test RubricResponse full parsing."""

    def _make_rubric_dict(self, **overrides):
        base = {dim: {"score": 4, "reasoning": f"{dim} is good"} for dim in RUBRIC_DIMENSIONS}
        base.update(overrides)
        return base

    def test_valid_full_parse(self):
        """Verify all 5 dimensions parse correctly."""
        data = self._make_rubric_dict()
        rubric = RubricResponse.model_validate(data)
        assert rubric.factual_accuracy.score == 4
        assert rubric.reasoning_soundness.reasoning == "reasoning_soundness is good"

    def test_missing_field_rejected(self):
        """Verify missing dimension raises ValidationError."""
        data = self._make_rubric_dict()
        del data["clarity"]
        with pytest.raises(ValidationError):
            RubricResponse.model_validate(data)

    def test_invalid_score_in_dimension(self):
        """Verify out-of-range score in a dimension raises ValidationError."""
        data = self._make_rubric_dict(clarity={"score": 10, "reasoning": "way too high"})
        with pytest.raises(ValidationError):
            RubricResponse.model_validate(data)


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestFormatToolCalls:
    """Test _format_tool_calls helper."""

    def test_empty_list(self):
        """Verify empty tool calls returns placeholder text."""
        result = _format_tool_calls([])
        assert result == "(no tool calls)"

    def test_normal_list(self):
        """Verify tool call formatting includes name, agent, args, latency."""
        calls = [
            {
                "tool_name": "get_quote",
                "agent_name": "market_data",
                "args": {"ticker": "AAPL"},
                "latency_ms": 123.4,
            },
        ]
        result = _format_tool_calls(calls)
        assert "get_quote" in result
        assert "market_data" in result
        assert "AAPL" in result
        assert "123ms" in result

    def test_arg_truncation(self):
        """Verify args longer than MAX_ARG_CHARS are truncated."""
        long_args = {"data": "x" * 300}
        calls = [
            {
                "tool_name": "big_tool",
                "agent_name": "test",
                "args": long_args,
            },
        ]
        result = _format_tool_calls(calls)
        assert "..." in result
        assert len(result) < 400


# ---------------------------------------------------------------------------
# Result builder tests
# ---------------------------------------------------------------------------


def _make_rubric(scores: dict[str, int] | None = None) -> RubricResponse:
    """Build a RubricResponse with given scores (default all 4)."""
    score_map = scores or {}
    dims = {}
    for dim in RUBRIC_DIMENSIONS:
        val = score_map.get(dim, 4)
        dims[dim] = DimensionScore(score=val, reasoning=f"{dim} scored {val}")
    return RubricResponse(**dims)


class TestBuildSuccessResult:
    """Test _build_success_result."""

    def test_all_pass(self):
        """Verify all dimensions pass with scores above threshold."""
        rubric = _make_rubric()
        result = _build_success_result(rubric, DEFAULT_THRESHOLDS)
        assert result["rubric_pass"] is True
        assert result["average_score"] == 4.0
        for dim in RUBRIC_DIMENSIONS:
            assert result[dim]["passed"] is True
            assert result[dim]["score"] == 4

    def test_one_fails(self):
        """Verify rubric_pass is False when one dimension is below threshold."""
        rubric = _make_rubric({"clarity": 2})
        result = _build_success_result(rubric, DEFAULT_THRESHOLDS)
        assert result["rubric_pass"] is False
        assert result["clarity"]["passed"] is False
        assert result["clarity"]["score"] == 2
        assert result["factual_accuracy"]["passed"] is True

    def test_custom_thresholds(self):
        """Verify custom thresholds override defaults."""
        rubric = _make_rubric({"clarity": 4})
        custom = {**DEFAULT_THRESHOLDS, "clarity": 5}
        result = _build_success_result(rubric, custom)
        assert result["clarity"]["passed"] is False
        assert result["clarity"]["threshold"] == 5
        assert result["rubric_pass"] is False

    def test_average_calculation(self):
        """Verify average_score is computed correctly across all dimensions."""
        scores = {
            "factual_accuracy": 5,
            "completeness": 3,
            "clarity": 4,
            "tool_use_quality": 2,
            "reasoning_soundness": 1,
        }
        rubric = _make_rubric(scores)
        result = _build_success_result(rubric, DEFAULT_THRESHOLDS)
        expected_avg = (5 + 3 + 4 + 2 + 1) / 5
        assert result["average_score"] == expected_avg


class TestBuildErrorResult:
    """Test _build_error_result."""

    def test_shape(self):
        """Verify error result has correct shape with all scores None."""
        result = _build_error_result("something broke")
        assert result["rubric_pass"] is False
        assert result["average_score"] is None
        assert result["error"] == "something broke"

        for dim in RUBRIC_DIMENSIONS:
            assert result[dim]["score"] is None
            assert result[dim]["passed"] is False
            assert result[dim]["threshold"] == DEFAULT_THRESHOLDS[dim]


# ---------------------------------------------------------------------------
# Async scorer tests (mocked structured_completion)
# ---------------------------------------------------------------------------

_SC_PATH = "evaluation.scorers.llm_judge.structured_completion"


class TestLLMJudgeScorerAsync:
    """Test the full async flow with mocked structured_completion."""

    @pytest.mark.asyncio
    async def test_full_flow(self):
        """Verify end-to-end scorer with mocked LLM returns correct scores."""
        rubric = _make_rubric()

        sample_output = {
            "response": "AAPL is trading at $150",
            "tool_calls": [
                {
                    "tool_name": "get_quote",
                    "agent_name": "market_data",
                    "args": {"ticker": "AAPL"},
                    "latency_ms": 100,
                }
            ],
            "tool_outputs": "get_quote: AAPL price is $150",
        }

        with patch(_SC_PATH, new_callable=AsyncMock, return_value=rubric):
            result = await llm_judge_rubric_scorer(
                output=sample_output,
                query="What is AAPL trading at?",
            )

        assert result["rubric_pass"] is True
        assert result["average_score"] == 4.0
        for dim in RUBRIC_DIMENSIONS:
            assert result[dim]["score"] == 4

    @pytest.mark.asyncio
    async def test_class_wrapper(self):
        """Verify LLMJudgeScorer class delegates to the opik-tracked scorer correctly."""
        rubric = _make_rubric({"completeness": 5, "clarity": 5})

        scorer = LLMJudgeScorer(model_id="test/model")

        with patch(_SC_PATH, new_callable=AsyncMock, return_value=rubric):
            result = await scorer.score(
                output={"response": "test", "tool_calls": [], "tool_outputs": ""},
                query="test query",
            )

        assert result["rubric_pass"] is True
        assert result["completeness"]["score"] == 5

    @pytest.mark.asyncio
    async def test_truncates_long_tool_outputs(self):
        """Verify tool_outputs exceeding 4000 chars are truncated in prompt."""
        rubric = _make_rubric()

        long_output = {
            "response": "test",
            "tool_calls": [],
            "tool_outputs": "x" * 10000,
        }

        with patch(_SC_PATH, new_callable=AsyncMock, return_value=rubric) as mock_sc:
            await llm_judge_rubric_scorer(
                output=long_output,
                query="test",
            )

            call_args = mock_sc.call_args
            user_msg = call_args.kwargs["user"]
            assert "...(truncated)" in user_msg


class TestLLMJudgeScorerError:
    """Test degraded result on LLM failure."""

    @pytest.mark.asyncio
    async def test_returns_error_result(self):
        """Verify LLM exception produces degraded error result."""
        sample_output = {
            "response": "test",
            "tool_calls": [],
            "tool_outputs": "",
        }

        with patch(_SC_PATH, new_callable=AsyncMock, side_effect=RuntimeError("API down")):
            result = await llm_judge_rubric_scorer(
                output=sample_output,
                query="test",
            )

        assert result["rubric_pass"] is False
        assert result["average_score"] is None
        assert "error" in result
        for dim in RUBRIC_DIMENSIONS:
            assert result[dim]["score"] is None
            assert result[dim]["passed"] is False

    @pytest.mark.asyncio
    async def test_class_wrapper_error(self):
        """Verify LLMJudgeScorer class handles LLM errors gracefully."""
        scorer = LLMJudgeScorer()

        with patch(_SC_PATH, new_callable=AsyncMock, side_effect=ValueError("bad response")):
            result = await scorer.score(
                output={"response": "", "tool_calls": [], "tool_outputs": ""},
            )

        assert result["rubric_pass"] is False
        assert result["error"] == "LLM judge call failed"
