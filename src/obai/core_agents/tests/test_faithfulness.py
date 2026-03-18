"""Unit tests for faithfulness and completeness scorers.

All tests mock litellm to avoid real API calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from evaluation.scorers.faithfulness import (
    CompletenessJudgment,
    CompletenessScorer,
    ExtractedNumber,
    FaithfulnessJudgment,
    FaithfulnessScorer,
    OmittedDataPoint,
    UnfaithfulClaim,
    _extract_numbers,
    _extract_numbers_from_tool_responses,
    _format_tool_outputs_detailed,
    _match_number,
    _score_numeric,
)

# ---------------------------------------------------------------------------
# Pydantic model tests
# ---------------------------------------------------------------------------


class TestPydanticModels:
    """Test Pydantic model validation for judge structured output."""

    def test_faithfulness_judgment_valid(self) -> None:
        """Verify valid FaithfulnessJudgment round-trips."""
        fj = FaithfulnessJudgment(
            faithful=True,
            unfaithful_claims=[],
            score=0.95,
            reasoning="All claims supported.",
        )
        assert fj.faithful is True
        assert fj.score == 0.95

    def test_faithfulness_judgment_with_claims(self) -> None:
        """Verify FaithfulnessJudgment with unfaithful claims."""
        fj = FaithfulnessJudgment(
            faithful=False,
            unfaithful_claims=[
                UnfaithfulClaim(
                    claim="AAPL is at $200",
                    reasoning="Tool shows $182.50",
                    severity="high",
                ),
            ],
            score=0.3,
            reasoning="Price mismatch.",
        )
        assert len(fj.unfaithful_claims) == 1
        assert fj.unfaithful_claims[0].severity == "high"

    def test_faithfulness_judgment_score_bounds(self) -> None:
        """Verify score field accepts any float (LLM output not constrained)."""
        fj = FaithfulnessJudgment(
            faithful=True,
            score=1.5,
            reasoning="too high",
        )
        assert fj.score == 1.5

    def test_completeness_judgment_valid(self) -> None:
        """Verify valid CompletenessJudgment round-trips."""
        cj = CompletenessJudgment(
            complete=True,
            omitted_data=[],
            coverage_score=0.9,
            reasoning="All data used.",
        )
        assert cj.complete is True
        assert cj.coverage_score == 0.9

    def test_completeness_judgment_with_omissions(self) -> None:
        """Verify CompletenessJudgment with omitted data points."""
        cj = CompletenessJudgment(
            complete=False,
            omitted_data=[
                OmittedDataPoint(
                    data_point="52-week high",
                    source_tool="get_quote",
                    relevance="Context for current price",
                    severity="medium",
                ),
            ],
            coverage_score=0.6,
            reasoning="Missing range context.",
        )
        assert len(cj.omitted_data) == 1
        assert cj.omitted_data[0].severity == "medium"

    def test_completeness_judgment_score_bounds(self) -> None:
        """Verify coverage_score field accepts any float (LLM output not constrained)."""
        cj = CompletenessJudgment(
            complete=True,
            coverage_score=-0.1,
            reasoning="too low",
        )
        assert cj.coverage_score == -0.1


# ---------------------------------------------------------------------------
# Number extraction tests
# ---------------------------------------------------------------------------


class TestExtractNumbers:
    """Test _extract_numbers from response text."""

    def test_price_extraction(self) -> None:
        """Extract dollar-prefixed prices."""
        nums = _extract_numbers("AAPL is trading at $182.50 today.")
        assert len(nums) == 1
        assert nums[0].value == 182.50
        assert nums[0].is_price is True

    def test_percentage_extraction(self) -> None:
        """Extract percentage values."""
        nums = _extract_numbers("The P/E ratio change is 2.3% year-over-year.")
        assert any(n.is_percentage and n.value == 2.3 for n in nums)

    def test_comma_numbers(self) -> None:
        """Extract numbers with comma separators."""
        nums = _extract_numbers("Market cap is $2,450,000.")
        assert any(n.value == 2450000.0 for n in nums)

    def test_no_double_counting(self) -> None:
        """Ensure $182.50 doesn't also appear as plain 182.50."""
        nums = _extract_numbers("Price is $182.50 today.")
        values = [n.value for n in nums]
        assert values.count(182.50) == 1

    def test_empty_string(self) -> None:
        """Empty string yields empty list."""
        assert _extract_numbers("") == []

    def test_text_without_numbers(self) -> None:
        """Text with no numbers yields empty list."""
        assert _extract_numbers("The stock is performing well.") == []

    def test_multiple_types(self) -> None:
        """Extract mixed price, percentage, and plain numbers."""
        text = "AAPL at $182.50, up 2.3%, volume 45000000"
        nums = _extract_numbers(text)
        values = {n.value for n in nums}
        assert 182.50 in values
        assert 2.3 in values
        assert 45000000.0 in values


# ---------------------------------------------------------------------------
# Tool response number extraction tests
# ---------------------------------------------------------------------------


class TestExtractNumbersFromToolResponses:
    """Test recursive extraction from tool response dicts."""

    def test_nested_dict(self) -> None:
        """Extract numbers from nested dict structure."""
        tool_calls = [
            {
                "tool_name": "get_quote",
                "response": {"price": 182.50, "change": -1.2},
            },
        ]
        nums = _extract_numbers_from_tool_responses(tool_calls)
        values = [v for _, v in nums]
        assert 182.50 in values
        assert -1.2 in values

    def test_lists(self) -> None:
        """Extract numbers from lists inside response."""
        tool_calls = [
            {
                "tool_name": "get_history",
                "response": {"prices": [100.0, 101.5, 102.0]},
            },
        ]
        nums = _extract_numbers_from_tool_responses(tool_calls)
        values = [v for _, v in nums]
        assert 100.0 in values
        assert 101.5 in values

    def test_string_numbers(self) -> None:
        """Extract numbers stored as strings in response."""
        tool_calls = [
            {
                "tool_name": "get_fundamentals",
                "response": {"pe_ratio": "25.4", "market_cap": "$1,500,000"},
            },
        ]
        nums = _extract_numbers_from_tool_responses(tool_calls)
        values = [v for _, v in nums]
        assert 25.4 in values
        assert 1500000.0 in values

    def test_empty_response(self) -> None:
        """No response yields empty list."""
        tool_calls = [{"tool_name": "test", "response": None}]
        assert _extract_numbers_from_tool_responses(tool_calls) == []

    def test_skips_booleans(self) -> None:
        """Boolean values should not be extracted as numbers."""
        tool_calls = [
            {
                "tool_name": "test",
                "response": {"active": True, "price": 50.0},
            },
        ]
        nums = _extract_numbers_from_tool_responses(tool_calls)
        values = [v for _, v in nums]
        assert 50.0 in values
        # True (1.0) should not be in there
        assert len(values) == 1


# ---------------------------------------------------------------------------
# Number matching tests
# ---------------------------------------------------------------------------


class TestMatchNumber:
    """Test _match_number tolerance matching."""

    def test_exact_match(self) -> None:
        """Exact value matches."""
        extracted = ExtractedNumber(
            value=182.50,
            raw_text="$182.50",
            context="at $182.50",
            is_price=True,
        )
        tool_nums = [("get_quote", 182.50)]
        result = _match_number(extracted, tool_nums)
        assert result.matched is True
        assert result.source_tool == "get_quote"

    def test_within_tolerance(self) -> None:
        """Match within price tolerance (±$0.01)."""
        extracted = ExtractedNumber(
            value=182.50,
            raw_text="$182.50",
            context="at $182.50",
            is_price=True,
        )
        tool_nums = [("get_quote", 182.51)]
        result = _match_number(extracted, tool_nums)
        assert result.matched is True

    def test_percent_conversion(self) -> None:
        """Match 2.3% in response against 0.023 in API (×100 conversion)."""
        extracted = ExtractedNumber(
            value=2.3,
            raw_text="2.3%",
            context="up 2.3%",
            is_percentage=True,
        )
        tool_nums = [("get_quote", 0.023)]
        result = _match_number(extracted, tool_nums)
        assert result.matched is True

    def test_no_match(self) -> None:
        """No match when value is far from all tool numbers."""
        extracted = ExtractedNumber(
            value=999.99,
            raw_text="$999.99",
            context="at $999.99",
            is_price=True,
        )
        tool_nums = [("get_quote", 182.50)]
        result = _match_number(extracted, tool_nums)
        assert result.matched is False

    def test_empty_tool_numbers(self) -> None:
        """No match when tool numbers list is empty."""
        extracted = ExtractedNumber(
            value=100.0,
            raw_text="100.0",
            context="100.0",
        )
        result = _match_number(extracted, [])
        assert result.matched is False


# ---------------------------------------------------------------------------
# Score numeric (deterministic phase) tests
# ---------------------------------------------------------------------------


class TestScoreNumeric:
    """Test _score_numeric aggregate scoring."""

    def test_all_match(self) -> None:
        """All numbers from response found in tool outputs."""
        result = _score_numeric(
            "AAPL is at $182.50",
            [{"tool_name": "get_quote", "response": {"price": 182.50}}],
        )
        assert result.accuracy == 1.0
        assert result.matched_numbers == result.total_numbers

    def test_partial_match(self) -> None:
        """Some numbers match, some don't."""
        result = _score_numeric(
            "AAPL is at $182.50, target $999.00",
            [{"tool_name": "get_quote", "response": {"price": 182.50}}],
        )
        assert result.accuracy < 1.0
        assert result.unmatched_numbers > 0

    def test_no_numbers_in_response(self) -> None:
        """Response with no numbers gets perfect accuracy (nothing to check)."""
        result = _score_numeric(
            "The stock is performing well.",
            [{"tool_name": "get_quote", "response": {"price": 182.50}}],
        )
        assert result.accuracy == 1.0
        assert result.total_numbers == 0


# ---------------------------------------------------------------------------
# Async scorer tests (mocked litellm)
# ---------------------------------------------------------------------------


def _mock_faithfulness_response(judgment: FaithfulnessJudgment) -> MagicMock:
    """Create mock litellm response for faithfulness judge."""
    response = MagicMock()
    choice = MagicMock()
    choice.message.content = judgment.model_dump_json()
    response.choices = [choice]
    return response


def _mock_completeness_response(judgment: CompletenessJudgment) -> MagicMock:
    """Create mock litellm response for completeness judge."""
    response = MagicMock()
    choice = MagicMock()
    choice.message.content = judgment.model_dump_json()
    response.choices = [choice]
    return response


SAMPLE_OUTPUT: dict[str, Any] = {
    "response": "AAPL is trading at $182.50",
    "tool_calls": [
        {
            "tool_name": "get_quote",
            "args": {"ticker": "AAPL"},
            "response": {"price": 182.50, "change": -1.2},
        },
    ],
    "tool_outputs": "get_quote: price=182.50",
}


class TestFaithfulnessScorerAsync:
    """Test FaithfulnessScorer with mocked litellm."""

    @pytest.mark.asyncio
    async def test_full_flow_pass(self) -> None:
        """Both numeric and semantic pass → faithfulness_pass=True."""
        judgment = FaithfulnessJudgment(
            faithful=True,
            unfaithful_claims=[],
            score=0.95,
            reasoning="All good.",
        )
        mock_resp = _mock_faithfulness_response(judgment)
        scorer = FaithfulnessScorer(model_id="test/model")

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
            result = await scorer.score(output=SAMPLE_OUTPUT, query="AAPL price?")

        assert result["faithfulness_pass"] is True
        assert result["numeric_pass"] is True
        assert result["semantic_faithful"] is True

    @pytest.mark.asyncio
    async def test_numeric_only_fail(self) -> None:
        """Numeric fails but semantic passes → faithfulness_pass=True.

        Semantic judge is authoritative for pass/fail (it can handle derived
        values that deterministic numeric matching can't).
        """
        judgment = FaithfulnessJudgment(
            faithful=True,
            unfaithful_claims=[],
            score=0.9,
            reasoning="Semantically fine.",
        )
        mock_resp = _mock_faithfulness_response(judgment)

        # Response has a number ($999.99) not in tool outputs
        bad_output = {
            **SAMPLE_OUTPUT,
            "response": "AAPL is at $999.99",
        }
        scorer = FaithfulnessScorer(model_id="test/model", numeric_threshold=0.9)

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
            result = await scorer.score(output=bad_output, query="AAPL price?")

        assert result["numeric_pass"] is False
        # Semantic judge is authoritative — it says faithful, so pass
        assert result["faithfulness_pass"] is True

    @pytest.mark.asyncio
    async def test_semantic_only_fail(self) -> None:
        """Numeric passes but semantic fails → faithfulness_pass=False."""
        judgment = FaithfulnessJudgment(
            faithful=False,
            unfaithful_claims=[
                UnfaithfulClaim(
                    claim="AAPL is bullish",
                    reasoning="No directional data in tools",
                    severity="medium",
                ),
            ],
            score=0.4,
            reasoning="Directional claim unsupported.",
        )
        mock_resp = _mock_faithfulness_response(judgment)
        scorer = FaithfulnessScorer(model_id="test/model")

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
            result = await scorer.score(output=SAMPLE_OUTPUT, query="AAPL price?")

        assert result["numeric_pass"] is True
        assert result["semantic_faithful"] is False
        assert result["faithfulness_pass"] is False
        assert len(result["unfaithful_claims"]) == 1

    @pytest.mark.asyncio
    async def test_skip_llm(self) -> None:
        """skip_llm=True runs only deterministic phase."""
        scorer = FaithfulnessScorer(skip_llm=True)
        result = await scorer.score(output=SAMPLE_OUTPUT, query="AAPL price?")

        assert result["semantic_faithful"] is None
        assert result["semantic_score"] is None
        assert result["numeric_pass"] is True
        assert result["faithfulness_pass"] is True

    @pytest.mark.asyncio
    async def test_llm_error_degradation(self) -> None:
        """LLM failure → faithfulness_pass=False with error key."""
        scorer = FaithfulnessScorer(model_id="test/model")

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=RuntimeError("API down"))
            result = await scorer.score(output=SAMPLE_OUTPUT, query="AAPL price?")

        assert result["faithfulness_pass"] is False
        assert "error" in result


class TestCompletenessScorerAsync:
    """Test CompletenessScorer with mocked litellm."""

    @pytest.mark.asyncio
    async def test_complete_pass(self) -> None:
        """Full coverage → completeness_pass=True."""
        judgment = CompletenessJudgment(
            complete=True,
            omitted_data=[],
            coverage_score=0.95,
            reasoning="All relevant data included.",
        )
        mock_resp = _mock_completeness_response(judgment)
        scorer = CompletenessScorer(model_id="test/model")

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
            result = await scorer.score(output=SAMPLE_OUTPUT, query="AAPL price?")

        assert result["completeness_pass"] is True
        assert result["coverage_score"] == 0.95
        assert result["omitted_count"] == 0

    @pytest.mark.asyncio
    async def test_high_severity_omission_with_high_coverage_passes(self) -> None:
        """High coverage (0.8 >= 0.7 threshold) passes even with high-severity omission.

        Coverage score from the LLM judge is authoritative for pass/fail.
        High-severity counts are diagnostic only.
        """
        judgment = CompletenessJudgment(
            complete=False,
            omitted_data=[
                OmittedDataPoint(
                    data_point="Current price",
                    source_tool="get_quote",
                    relevance="Directly answers the query",
                    severity="high",
                ),
            ],
            coverage_score=0.8,
            reasoning="Missed the price.",
        )
        mock_resp = _mock_completeness_response(judgment)
        scorer = CompletenessScorer(model_id="test/model", coverage_threshold=0.7)

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
            result = await scorer.score(output=SAMPLE_OUTPUT, query="AAPL price?")

        # Coverage score (0.8) exceeds threshold (0.7) → passes
        assert result["completeness_pass"] is True
        assert result["omitted_high_severity"] == 1

    @pytest.mark.asyncio
    async def test_low_severity_above_threshold_passes(self) -> None:
        """Low-severity omissions with coverage above threshold still pass."""
        judgment = CompletenessJudgment(
            complete=False,
            omitted_data=[
                OmittedDataPoint(
                    data_point="52-week high",
                    source_tool="get_quote",
                    relevance="Supplementary context",
                    severity="low",
                ),
            ],
            coverage_score=0.85,
            reasoning="Minor context omitted.",
        )
        mock_resp = _mock_completeness_response(judgment)
        scorer = CompletenessScorer(model_id="test/model", coverage_threshold=0.7)

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
            result = await scorer.score(output=SAMPLE_OUTPUT, query="AAPL price?")

        assert result["completeness_pass"] is True
        assert result["omitted_count"] == 1
        assert result["omitted_high_severity"] == 0

    @pytest.mark.asyncio
    async def test_llm_error_degradation(self) -> None:
        """LLM failure → completeness_pass=False with error key."""
        scorer = CompletenessScorer(model_id="test/model")

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(side_effect=RuntimeError("API down"))
            result = await scorer.score(output=SAMPLE_OUTPUT, query="AAPL price?")

        assert result["completeness_pass"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_below_threshold_no_high_severity_fails(self) -> None:
        """Low coverage without high-severity omissions still fails."""
        judgment = CompletenessJudgment(
            complete=False,
            omitted_data=[
                OmittedDataPoint(
                    data_point="Volume",
                    source_tool="get_quote",
                    relevance="Context",
                    severity="medium",
                ),
            ],
            coverage_score=0.5,
            reasoning="Low coverage.",
        )
        mock_resp = _mock_completeness_response(judgment)
        scorer = CompletenessScorer(model_id="test/model", coverage_threshold=0.7)

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
            result = await scorer.score(output=SAMPLE_OUTPUT, query="test")

        assert result["completeness_pass"] is False
        assert result["omitted_high_severity"] == 0

    @pytest.mark.asyncio
    async def test_falls_back_to_tool_outputs_when_no_tool_calls(self) -> None:
        """When tool_calls is empty, raw tool_outputs should be sent to judge."""
        judgment = CompletenessJudgment(
            complete=True,
            omitted_data=[],
            coverage_score=1.0,
            reasoning="ok",
        )
        mock_resp = _mock_completeness_response(judgment)
        scorer = CompletenessScorer(model_id="test/model")
        output = {
            "response": "AAPL is at $182",
            "tool_calls": [],
            "tool_outputs": "get_quote: price=182.50",
        }

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
            await scorer.score(output=output, query="price?")
            call_args = mock_litellm.acompletion.call_args
            user_msg = call_args.kwargs["messages"][1]["content"]
            assert "price=182.50" in user_msg


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestFormatToolOutputsDetailed:
    """Test _format_tool_outputs_detailed helper."""

    def test_empty_list(self) -> None:
        """Empty tool calls returns empty string (falsy for fallback)."""
        assert _format_tool_outputs_detailed([]) == ""

    def test_normal_formatting(self) -> None:
        """Tool call formatting includes name, args, response."""
        tool_calls = [
            {
                "tool_name": "get_quote",
                "args": {"ticker": "AAPL"},
                "response": {"price": 182.5},
            },
        ]
        result = _format_tool_outputs_detailed(tool_calls)
        assert "[get_quote]" in result
        assert "AAPL" in result
        assert "182.5" in result

    def test_missing_response(self) -> None:
        """Tool call with no response key shows placeholder."""
        tool_calls = [{"tool_name": "broken", "args": {}}]
        result = _format_tool_outputs_detailed(tool_calls)
        assert "(no response)" in result

    def test_missing_tool_name(self) -> None:
        """Tool call with no tool_name defaults to 'unknown'."""
        tool_calls = [{"args": {}, "response": {"x": 1}}]
        result = _format_tool_outputs_detailed(tool_calls)
        assert "[unknown]" in result


class TestMatchNumberEdgeCases:
    """Additional edge cases for _match_number tolerance."""

    def test_percent_within_tolerance_boundary(self) -> None:
        """2.3% should match 0.0224 (diff=0.06 after ×100 conversion)."""
        extracted = ExtractedNumber(
            value=2.3,
            raw_text="2.3%",
            context="up 2.3%",
            is_percentage=True,
        )
        result = _match_number(extracted, [("tool", 0.0224)])
        assert result.matched is True

    def test_percent_outside_tolerance(self) -> None:
        """2.3% should NOT match 0.027 (diff=0.4 > 0.1 tolerance)."""
        extracted = ExtractedNumber(
            value=2.3,
            raw_text="2.3%",
            context="up 2.3%",
            is_percentage=True,
        )
        result = _match_number(extracted, [("tool", 0.027)])
        assert result.matched is False


class TestFaithfulnessScorerEdgeCases:
    """Edge cases for FaithfulnessScorer."""

    @pytest.mark.asyncio
    async def test_empty_output_dict_skip_llm(self) -> None:
        """Empty output dict with skip_llm produces valid result."""
        scorer = FaithfulnessScorer(skip_llm=True)
        result = await scorer.score(output={}, query="test")
        assert result["numeric_accuracy"] == 1.0
        assert result["faithfulness_pass"] is True

    @pytest.mark.asyncio
    async def test_falls_back_to_tool_outputs_when_no_tool_calls(self) -> None:
        """When tool_calls is empty, raw tool_outputs is sent to judge."""
        judgment = FaithfulnessJudgment(
            faithful=True,
            unfaithful_claims=[],
            score=1.0,
            reasoning="ok",
        )
        mock_resp = _mock_faithfulness_response(judgment)
        scorer = FaithfulnessScorer(model_id="test/model")
        output = {
            "response": "AAPL is at $182",
            "tool_calls": [],
            "tool_outputs": "get_quote: price=182.50",
        }

        with patch("evaluation.scorers.faithfulness.litellm") as mock_litellm:
            mock_litellm.acompletion = AsyncMock(return_value=mock_resp)
            await scorer.score(output=output, query="price?")
            call_args = mock_litellm.acompletion.call_args
            user_msg = call_args.kwargs["messages"][1]["content"]
            assert "price=182.50" in user_msg
