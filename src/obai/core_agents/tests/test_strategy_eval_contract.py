"""Unit tests for deterministic strategy evaluation contract scoring."""

import pytest

from evaluation.scorers.custom import (
    StrategyContractScorer,
    StrategyDecisionScorer,
    StrategyGroundingScorer,
)

_VALID_STRATEGY_RESPONSE = """
#### 1. Verdict
reject - Underperformed benchmark.

#### 2. Strategy Summary
- Hypothesis: test trend persistence.

#### 3. Backtest Evidence
- Sharpe: 0.50
- CAGR: 3.2%

#### 4. Iteration Summary
- Iteration 1 only.

#### 5. Engine Compatibility
- Compatibility status: fully_supported

#### 6. Final Strategy JSON
```json
{"name":"TestStrategy","universe":{"symbols":["AAPL"],"benchmark":"SPY"}}
```

#### 7. Risk Notes
- Benchmark underperformance.

#### 8. Next Actions
1. Reject and redesign.

#### 9. Handoff Metadata
- `deployment_readiness`: reject
""".strip()


class TestStrategyContractScorer:
    """Validate final strategy response contract checks."""

    def test_completed_strategy_artifact_passes(self) -> None:
        """A complete strategy artifact with JSON should pass."""
        scorer = StrategyContractScorer()

        result = scorer.score(output={"response": _VALID_STRATEGY_RESPONSE})

        assert result["contract_pass"] is True
        assert result["mode"] == "completed"
        assert result["json_valid"] is True
        assert result["missing_sections"] == []

    def test_missing_json_block_fails(self) -> None:
        """Completed artifacts without executable JSON should fail."""
        scorer = StrategyContractScorer()
        json_block = (
            "```json\n"
            '{"name":"TestStrategy","universe":{"symbols":["AAPL"],"benchmark":"SPY"}}\n'
            "```"
        )
        response = _VALID_STRATEGY_RESPONSE.replace(json_block, "No JSON emitted.")

        result = scorer.score(output={"response": response})

        assert result["contract_pass"] is False
        assert result["mode"] == "invalid"
        assert "json" in result["reason"].lower()

    def test_pending_strategy_response_passes(self) -> None:
        """Async pending responses should be treated as valid contract outputs."""
        scorer = StrategyContractScorer()
        response = """
Status: Pending
Job ID: job_123
Estimated Time: 18 seconds
Next User Action: Ask me to check status with this job ID.
""".strip()

        result = scorer.score(output={"response": response})

        assert result["contract_pass"] is True
        assert result["mode"] == "pending"

    def test_terminal_marker_leak_fails(self) -> None:
        """Internal relay markers should never appear in the final user response."""
        scorer = StrategyContractScorer()
        response = (
            "__TERMINAL_TOOL_OUTPUT__:strategy_analysis:completed\n\n" + _VALID_STRATEGY_RESPONSE
        )

        result = scorer.score(output={"response": response})

        assert result["contract_pass"] is False
        assert result["marker_leaked"] is True
        assert "marker" in result["reason"].lower()


class TestStrategyGroundingScorer:
    """Validate grounding against the raw strategy passthrough artifact."""

    def test_verbatim_artifact_passes(self) -> None:
        """Final response should pass when it preserves the raw artifact verbatim."""
        scorer = StrategyGroundingScorer()
        output = {
            "response": _VALID_STRATEGY_RESPONSE,
            "inner_tool_calls": [
                {
                    "tool_name": "Strategy Agent/strategy_passthrough",
                    "response": {"raw": _VALID_STRATEGY_RESPONSE},
                }
            ],
        }

        result = scorer.score(output=output)

        assert result["grounding_pass"] is True
        assert result["artifact_embedded_verbatim"] is True


class TestStrategyDecisionScorer:
    """Validate skip behavior for LLM-based strategy decision scoring."""

    @pytest.mark.asyncio
    async def test_skips_pending_strategy_response(self) -> None:
        """Pending responses should skip LLM decision judging."""
        scorer = StrategyDecisionScorer()
        pending = """
Status: Pending
Job ID: job_123
Estimated Time: 18 seconds
Next User Action: Ask me to check status with this job ID.
""".strip()
        output = {
            "response": pending,
            "inner_tool_calls": [
                {
                    "tool_name": "Strategy Agent/strategy_passthrough",
                    "response": {"raw": pending},
                    "agent_name": "Strategy Agent",
                }
            ],
        }

        result = await scorer.score(output=output)

        assert result["skipped"] is True
        assert result["skip_reason"] == "pending_strategy_response"

    @pytest.mark.asyncio
    async def test_skips_when_artifact_missing(self) -> None:
        """Missing raw strategy artifact should skip LLM decision judging."""
        scorer = StrategyDecisionScorer()

        result = await scorer.score(output={"response": _VALID_STRATEGY_RESPONSE})

        assert result["skipped"] is True
        assert result["skip_reason"] == "missing_strategy_artifact"

    def test_rewritten_artifact_fails(self) -> None:
        """Hub-rewritten summaries should fail grounding against the raw artifact."""
        scorer = StrategyGroundingScorer()
        rewritten = (
            "Headline: rejected strategy with weak returns.\n\n"
            "The tested strategy underperformed and is not recommended."
        )
        output = {
            "response": rewritten,
            "inner_tool_calls": [
                {
                    "tool_name": "Strategy Agent/strategy_passthrough",
                    "response": {"raw": _VALID_STRATEGY_RESPONSE},
                }
            ],
        }

        result = scorer.score(output=output)

        assert result["grounding_pass"] is False
        assert "artifact" in result["reason"].lower()

    def test_pending_status_must_be_preserved_verbatim(self) -> None:
        """Pending strategy responses should be preserved exactly."""
        scorer = StrategyGroundingScorer()
        pending = """
Status: Pending
Job ID: job_123
Estimated Time: 18 seconds
Next User Action: Ask me to check status with this job ID.
""".strip()
        output = {
            "response": pending,
            "inner_tool_calls": [
                {
                    "tool_name": "Strategy Agent/strategy_passthrough",
                    "response": {"raw": pending},
                }
            ],
        }

        result = scorer.score(output=output)

        assert result["grounding_pass"] is True
        assert result["artifact_embedded_verbatim"] is True
