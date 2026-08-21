"""Offline tests for broader-evaluation outcome and date contracts."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import typer
import yaml

from evaluation import cli, experiment
from evaluation.eval_runner import (
    OBaIEvaluator,
    load_test_cases,
    trace_to_scorer_input,
)
from evaluation.eval_runner import TestCase as EvalTestCase
from evaluation.scorers import DatePolicyScorer, OutcomeContractScorer
from evaluation.scorers.custom import (
    EfficiencyScorer,
    PartialRefusalJudgment,
    PartialRefusalSemanticScorer,
    ToolOrchestrationScorer,
)
from evaluation.trace.types import ToolCallSummary, Trace

NOW = datetime(2026, 7, 16, 18, 30, tzinfo=UTC)


def _trace(
    response: str,
    *,
    guardrail_passed: bool | None = True,
    tool_error: str | None = None,
    tool_response: dict[str, Any] | None = None,
    tool_name: str = "options_analysis",
    agent_name: str = "Options Agent",
    inner_tool_outputs: list[dict[str, Any]] | None = None,
) -> Trace:
    tool_calls = []
    if tool_error is not None or tool_response is not None:
        tool_calls.append(
            ToolCallSummary(
                tool_name=tool_name,
                args={},
                response=tool_response,
                error=tool_error,
                latency_ms=12,
                timestamp=NOW,
                agent_name=agent_name,
            )
        )
    return Trace(
        trace_id="trace-1",
        query="test query",
        model="offline",
        start_time=NOW,
        end_time=NOW,
        final_response=response,
        guardrail_passed=guardrail_passed,
        tool_calls=tool_calls,
        inner_tool_outputs=inner_tool_outputs or [],
    )


def _outcome(expected: str, trace: Trace) -> dict[str, Any]:
    expected_error_pattern = (
        "validation|mixed expirations" if expected == "specialist_error" else None
    )
    return OutcomeContractScorer(
        expected_outcome=expected,
        expected_error_pattern=expected_error_pattern,
        expected_response_pattern=(
            r"(?i)(?:cannot|unsupported|unable|refus)" if expected == "partial_refusal" else None
        ),
        forbidden_response_pattern=(
            r"(?i)(?:completed|exported)" if expected == "partial_refusal" else None
        ),
    ).score(trace_to_scorer_input(trace))


def _scored_result(scores: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Build one evaluator-shaped result with scorer-set completeness binding."""
    return {
        "_required_scorers": list(scores),
        "expected_scorers": list(scores),
        "scores": scores,
        **extra,
    }


def _deterministic_gate_scores(*, outcome_pass: bool = True) -> dict[str, Any]:
    """Return the complete scorer payload for a no-builtin general case."""
    return {
        "OutcomeContractScorer": {"outcome_pass": outcome_pass},
        "ResponseQualityScorer": {"quality_pass": True},
        "EfficiencyScorer": {"within_budget": True},
    }


@pytest.mark.parametrize(
    ("expected", "trace", "observed"),
    [
        ("success", _trace("A complete result as of 2026-07-16."), "success"),
        ("hub_reject", _trace("Rejected.", guardrail_passed=False), "hub_reject"),
        (
            "specialist_error",
            _trace(
                "Validation failed because the position has mixed expirations.",
                tool_error="ValidationError: mixed expirations",
            ),
            "specialist_error",
        ),
        (
            "data_unavailable",
            _trace(
                "No matching contracts were returned.",
                inner_tool_outputs=[
                    {
                        "specialist": "Options Agent",
                        "tool_name": "get_chain",
                        "output": {"results": []},
                    }
                ],
            ),
            "data_unavailable",
        ),
        (
            "partial_refusal",
            _trace("I cannot access Binance derivatives, but I can provide Coinbase spot data."),
            "partial_refusal",
        ),
    ],
)
def test_outcome_contract_distinguishes_supported_outcomes(
    expected: str,
    trace: Trace,
    observed: str,
) -> None:
    """Each required outcome has a distinct deterministic evidence path."""
    result = _outcome(expected, trace)

    assert result["observed_outcome"] == observed
    assert result["outcome_pass"] is True


def test_partial_refusal_language_fallback_is_only_used_when_declared() -> None:
    """Scoped refusal prose cannot downgrade a success contract by itself."""
    trace = _trace("I cannot access Binance derivatives, but Coinbase spot is supported.")

    assert _outcome("partial_refusal", trace)["observed_outcome"] == "partial_refusal"
    assert _outcome("success", trace)["observed_outcome"] == "success"


def test_partial_refusal_regexes_are_diagnostics_not_the_semantic_gate() -> None:
    """Structured outcome evidence is separate from semantic refusal correctness."""
    case = next(case for case in load_test_cases(include_extended=True) if case.id == "I7")
    scorer = OutcomeContractScorer(
        expected_outcome=case.expected_outcome,
        expected_tools=case.expected_tools,
        expected_response_pattern=case.expected_response_pattern,
        forbidden_response_pattern=case.forbidden_response_pattern,
    )
    tool_response = {"status": "partial_refusal"}
    contradicted = scorer.score(
        trace_to_scorer_input(
            _trace(
                "I completed the Binance derivatives analysis and exported a paper artifact.",
                tool_response=tool_response,
                tool_name="crypto_analysis",
                agent_name="Crypto Agent",
            )
        )
    )
    honest = scorer.score(
        trace_to_scorer_input(
            _trace(
                "Binance derivatives and the requested 10x leverage are unsupported, and "
                "I will block the artifact export rather than invent on-chain flows. I can "
                "offer Coinbase spot analysis instead.",
                tool_response=tool_response,
                tool_name="crypto_analysis",
                agent_name="Crypto Agent",
            )
        )
    )
    fabricated_numbers = scorer.score(
        trace_to_scorer_input(
            _trace(
                "Binance derivatives are unsupported, so artifact export is blocked; "
                "Coinbase spot is the supported alternative. Binance funding is 0.10%, "
                "DeFi TVL is $1 billion, and whale flows are $5 million.",
                tool_response=tool_response,
                tool_name="crypto_analysis",
                agent_name="Crypto Agent",
            )
        )
    )
    alternative_wording = scorer.score(
        trace_to_scorer_input(
            _trace(
                "Binance derivatives and on-chain data are outside this system's "
                "supported scope; artifact export is disabled; Coinbase spot remains "
                "available.",
                tool_response=tool_response,
                tool_name="crypto_analysis",
                agent_name="Crypto Agent",
            )
        )
    )

    assert contradicted["outcome_pass"] is True
    assert contradicted["forbidden_response_pattern_match"] is True
    assert fabricated_numbers["outcome_pass"] is True
    assert fabricated_numbers["forbidden_response_pattern_match"] is True
    assert alternative_wording["outcome_pass"] is True
    assert alternative_wording["response_pattern_match"] is False
    assert honest["outcome_pass"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "judgment", "expected"),
    [
        (
            "Binance and on-chain data are outside this system's supported scope; "
            "artifact export is disabled; Coinbase spot remains available.",
            PartialRefusalJudgment(
                all_unsupported_scopes_refused=True,
                no_unsupported_results_claimed=True,
                requested_side_effect_blocked=True,
                supported_alternative_only=True,
                reasoning="Every unsupported scope and side effect is refused.",
            ),
            True,
        ),
        (
            "Binance is unsupported, but funding = 1 bp and the full analysis follows.",
            PartialRefusalJudgment(
                all_unsupported_scopes_refused=False,
                no_unsupported_results_claimed=False,
                requested_side_effect_blocked=False,
                supported_alternative_only=False,
                reasoning="The answer fabricates a refused result.",
            ),
            False,
        ),
    ],
)
async def test_partial_refusal_semantic_scorer_is_the_authoritative_gate(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
    judgment: PartialRefusalJudgment,
    expected: bool,
) -> None:
    """Alternative wording and regex bypasses are decided by semantic review."""
    captured: dict[str, str] = {}

    async def fake_completion(**kwargs: Any) -> PartialRefusalJudgment:
        captured["user"] = str(kwargs["user"])
        return judgment

    monkeypatch.setattr(
        "evaluation.scorers.custom.structured_completion",
        fake_completion,
    )
    scorer = PartialRefusalSemanticScorer(model_id="offline")

    result = await scorer.score(
        output={"response": response, "tool_calls": [], "inner_tool_calls": []},
        query="Refuse unsupported Binance/on-chain analysis and block export.",
    )

    assert result["partial_refusal_semantic_pass"] is expected
    assert response in captured["user"]


def test_i7_cannot_run_or_pass_without_its_semantic_refusal_judge() -> None:
    """The extended refusal case no longer relies on a regex denylist for green."""
    case = next(case for case in load_test_cases(include_extended=True) if case.id == "I7")
    evaluator = object.__new__(OBaIEvaluator)
    evaluator.use_builtin_scorers = True
    evaluator.judge_model = "offline"
    names = evaluator.expected_scorer_names(case)

    assert case.requires_builtin_scorers is True
    assert "PartialRefusalSemanticScorer" in names
    with pytest.raises(ValueError, match="shallow"):
        experiment._validate_builtin_scorer_requirements([case], no_builtin=True)

    score_payload = {
        "PartialRefusalSemanticScorer": {"partial_refusal_semantic_pass": False},
        "OutcomeContractScorer": {"outcome_pass": True},
        "ToolOrchestrationScorer": {"correct_tools": True},
        "ResponseQualityScorer": {"quality_pass": True},
        "EfficiencyScorer": {"within_budget": True},
    }
    result = {
        "_required_scorers": names,
        "expected_scorers": names,
        "scores": score_payload,
    }
    assert set(score_payload) == set(names)
    assert cli._test_case_passed(result) is False


def test_guardrail_rejection_cannot_pass_after_specialist_execution() -> None:
    """An off-topic turn must be rejected before any financial data access."""
    trace = _trace(
        "I can only help with financial questions.",
        guardrail_passed=False,
        tool_response={"price": 210},
        tool_name="market_data_analysis",
        agent_name="Market Data Agent",
    )

    result = OutcomeContractScorer(expected_outcome="hub_reject").score(
        trace_to_scorer_input(trace)
    )

    assert result["observed_outcome"] == "unsafe_hub_reject"
    assert result["outcome_pass"] is False


def test_no_data_prose_without_structured_empty_result_cannot_pass() -> None:
    """A scoped missing field is not whole-request data unavailability."""
    trace = _trace("Dividend data is unavailable, but the main quote is 210 as of 2026-07-16.")

    result = _outcome("data_unavailable", trace)

    assert result["observed_outcome"] == "success"
    assert result["outcome_pass"] is False


def test_structured_no_data_cannot_pass_when_final_answer_fabricates_success() -> None:
    """The user-visible answer must surface the specialist's no-data result."""
    trace = _trace(
        "FAKESYM is trading at $123.45 right now.",
        tool_error="Invalid symbol: FAKESYM was not found",
        tool_name="market_data_analysis",
        agent_name="Market Data Agent",
    )

    result = OutcomeContractScorer(
        expected_outcome="data_unavailable",
        expected_tools=["market_data_analysis"],
    ).score(trace_to_scorer_input(trace))

    assert result["observed_outcome"] == "data_unavailable_contradicted"
    assert result["outcome_pass"] is False


def test_structured_invalid_symbol_error_satisfies_declared_no_data() -> None:
    """Known no-match tool errors are data absence, not calculation failure."""
    trace = _trace(
        "No quote is available for that symbol.",
        tool_error="Invalid symbol: FAKESYM was not found",
    )

    result = _outcome("data_unavailable", trace)

    assert result["observed_outcome"] == "data_unavailable"
    assert result["outcome_pass"] is True


@pytest.mark.parametrize(
    "message",
    [
        "401 authentication failed",
        "403 permission denied by upstream",
        "429 rate limit exceeded",
        "provider request timed out",
        "Connection error.",
        "Incorrect API key provided",
        "Error code: 404 - The model gpt-X does not exist or you do not have access to it",
        "insufficient_quota",
        "Internal Server Error",
        "Too Many Requests",
    ],
)
def test_provider_failure_cannot_satisfy_specialist_error_contract(message: str) -> None:
    """Infrastructure failure is not evidence of expected input validation."""
    trace = _trace(
        "The strategy request failed.",
        tool_error=message,
        tool_name="strategy_analysis",
        agent_name="Strategy Agent",
    )

    result = OutcomeContractScorer(
        expected_outcome="specialist_error",
        expected_tools=["strategy_analysis"],
        expected_error_pattern=r"(?i)portfolio.{0,80}(daily|intraday)",
    ).score(trace_to_scorer_input(trace))

    assert result["observed_outcome"] == "infrastructure_error"
    assert result["outcome_pass"] is False


def test_wrong_specialist_error_cannot_satisfy_declared_validation_branch() -> None:
    """The right specialist failing for the wrong reason is not a valid oracle."""
    trace = _trace(
        "The strategy request failed.",
        tool_error="calculation engine crashed while computing returns",
        tool_name="strategy_analysis",
        agent_name="Strategy Agent",
    )

    result = OutcomeContractScorer(
        expected_outcome="specialist_error",
        expected_tools=["strategy_analysis"],
        expected_error_pattern=r"(?i)VWAP.{0,80}(daily|intraday)",
    ).score(trace_to_scorer_input(trace))

    assert result["observed_outcome"] == "unexpected_specialist_error"
    assert result["outcome_pass"] is False


@pytest.mark.parametrize(
    "tool_response",
    [
        {"is_error": True, "message": "Connection error."},
        {"status": "failure", "message": "provider failed"},
    ],
)
def test_structured_error_variants_cannot_green_success_contract(
    tool_response: dict[str, Any],
) -> None:
    """Broader tracing recognizes the same explicit error forms as E2E."""
    trace = _trace(
        "AAPL is $210 as of 2026-07-16.",
        tool_response=tool_response,
        tool_name="market_data_analysis",
        agent_name="Market Data Agent",
    )

    result = OutcomeContractScorer(
        expected_outcome="success",
        expected_tools=["market_data_analysis"],
    ).score(trace_to_scorer_input(trace))

    assert result["observed_outcome"] != "success"
    assert result["outcome_pass"] is False


def test_partial_inner_failure_is_incomplete_without_case_recovery_oracle() -> None:
    """Mixed inner success/error cannot be guessed safe from generic prose."""
    trace = _trace(
        "AAPL is $999; dividend history is unavailable.",
        tool_response={"status": "ok"},
        tool_name="market_data_analysis",
        agent_name="Market Data Agent",
        inner_tool_outputs=[
            {
                "specialist": "Market Data Agent",
                "tool_name": "get_profile",
                "output": {"company": "Apple"},
            },
            {
                "specialist": "Market Data Agent",
                "tool_name": "get_quote",
                "output": {"status": "failure", "message": "quote lookup failed"},
            },
        ],
    )
    result = OutcomeContractScorer(
        expected_outcome="success",
        expected_tools=["market_data_analysis"],
    ).score(trace_to_scorer_input(trace))

    assert result["observed_outcome"] == "partial_success_unverified"
    assert "error" in result
    assert cli._test_case_passed(_scored_result({"OutcomeContractScorer": result})) is None


def test_i11_recovery_oracle_requires_honest_partial_coverage() -> None:
    """A declared partial batch recovery cannot claim 100%/complete coverage."""
    case = next(case for case in load_test_cases(include_extended=True) if case.id == "I11")
    scorer = OutcomeContractScorer(
        expected_outcome=case.expected_outcome,
        expected_tools=case.expected_tools,
        allowed_partial_errors=case.allowed_partial_errors,
    )

    def output(response: str) -> dict[str, Any]:
        return trace_to_scorer_input(
            _trace(
                response,
                tool_response={"status": "ok"},
                tool_name="market_data_analysis",
                agent_name="Market Data Agent",
                inner_tool_outputs=[
                    {
                        "specialist": "Market Data Agent",
                        "tool_name": "quote_batch_1",
                        "output": {"rows": [{"symbol": "AAPL", "change": 2.0}]},
                    },
                    {
                        "specialist": "Market Data Agent",
                        "tool_name": "quote_batch_3",
                        "output": {"status": "failure", "message": "quote batch 3 failed"},
                    },
                ],
            )
        )

    honest = scorer.score(output("Batch 3 failed. Coverage was 90%; leaderboard incomplete."))
    dishonest = scorer.score(
        output("Failed chunks: none. Coverage: 100%. This is the complete leaderboard.")
    )
    appended_contradiction = scorer.score(
        output(
            "Batch 3 failed. Coverage was 90%; leaderboard incomplete. The full "
            "leaderboard includes every constituent with no omissions."
        )
    )

    assert honest["outcome_pass"] is True
    assert dishonest["observed_outcome"] == "partial_success_unverified"
    assert "error" in dishonest
    assert appended_contradiction["observed_outcome"] == "partial_success_unverified"


def test_d6_recovery_oracle_requires_the_corrected_ticker() -> None:
    """Acknowledging that AAPLL failed is not evidence that fallback resolved AAPL."""
    case = next(case for case in load_test_cases(include_extended=True) if case.id == "D6")
    response_pattern = case.allowed_partial_errors[0]["response_pattern"]

    assert re.search(response_pattern, "AAPLL is invalid and was not found") is None
    assert re.search(response_pattern, "AAPLL was invalid; I resolved it to AAPL") is not None


def test_partial_infrastructure_failure_remains_incomplete() -> None:
    """A recovery oracle cannot bless provider/auth failures."""
    case = next(case for case in load_test_cases(include_extended=True) if case.id == "I11")
    trace = _trace(
        "Batch 3 failed. Coverage was 90%; leaderboard incomplete.",
        tool_response={"status": "ok"},
        tool_name="market_data_analysis",
        agent_name="Market Data Agent",
        inner_tool_outputs=[
            {
                "specialist": "Market Data Agent",
                "tool_name": "quote_batch_1",
                "output": {"rows": [{"symbol": "AAPL"}]},
            },
            {
                "specialist": "Market Data Agent",
                "tool_name": "quote_batch_3",
                "output": {"status": "failure", "message": "429 rate limit exceeded"},
            },
        ],
    )
    result = OutcomeContractScorer(
        expected_outcome="success",
        expected_tools=case.expected_tools,
        allowed_partial_errors=case.allowed_partial_errors,
    ).score(trace_to_scorer_input(trace))

    assert result["observed_outcome"] == "infrastructure_error"
    assert "error" in result


def test_empty_ancillary_collection_does_not_override_populated_tool_data() -> None:
    """An empty side field cannot downgrade an otherwise populated response."""
    trace = _trace(
        "One matching contract was returned.",
        tool_response={"results": [{"symbol": "AAPL260918C00200000"}], "rows": []},
    )

    result = _outcome("success", trace)

    assert result["observed_outcome"] == "success"
    assert result["outcome_pass"] is True


def test_nested_not_found_does_not_override_a_valid_scalar_quote() -> None:
    """An optional nested miss cannot become the whole request's outcome."""
    trace = _trace(
        "AAPL is 210.",
        tool_response={
            "price": 210,
            "optional_dividend": {"status": "not_found"},
        },
    )

    result = _outcome("success", trace)

    assert result["observed_outcome"] == "success"
    assert result["outcome_pass"] is True


def test_ancillary_not_found_does_not_override_populated_tool_data() -> None:
    """Nested no-data status cannot erase a populated primary result."""
    trace = _trace(
        "One matching contract was returned.",
        tool_response={
            "results": [{"symbol": "AAPL260918C00200000"}],
            "optional_lookup": {"status": "not_found"},
        },
    )

    result = _outcome("success", trace)

    assert result["observed_outcome"] == "success"
    assert result["outcome_pass"] is True


def test_unrelated_specialist_not_found_cannot_satisfy_expected_no_data() -> None:
    """A side route's miss cannot stand in for the contracted specialist."""
    trace = _trace(
        "AAPL is 210; the unrelated screen found no rows.",
        tool_response={"symbol": "AAPL", "price": 210},
        tool_name="market_data_analysis",
        agent_name="Market Data Agent",
    )
    trace.tool_calls.append(
        ToolCallSummary(
            tool_name="screener_lookup",
            args={},
            response={"status": "not_found", "results": []},
            error=None,
            latency_ms=5,
            timestamp=NOW,
            agent_name="Screener Agent",
        )
    )

    result = OutcomeContractScorer(
        expected_outcome="data_unavailable",
        expected_tools=["market_data_analysis"],
    ).score(trace_to_scorer_input(trace))

    assert result["observed_outcome"] == "success"
    assert result["outcome_pass"] is False


def test_i13_requires_structured_specialist_error_not_refusal_prose() -> None:
    """I13 passes only when the options trace captures a real validation error."""
    i13 = next(case for case in load_test_cases(include_extended=True) if case.id == "I13")
    evaluator = object.__new__(OBaIEvaluator)
    evaluator.use_builtin_scorers = False
    evaluator.judge_model = "offline"

    outcome_scorers = [
        scorer
        for scorer in evaluator._build_scorers(i13)
        if isinstance(scorer, OutcomeContractScorer)
    ]

    assert len(outcome_scorers) == 1
    plain_refusal = trace_to_scorer_input(
        _trace("I cannot produce a shared-expiry profile for mixed expirations.")
    )
    structured_error = trace_to_scorer_input(
        _trace(
            "The options tool rejected the mixed-expiry aggregate.",
            tool_error="ValidationError: option legs must share an expiry",
        )
    )
    unrelated_error = trace_to_scorer_input(
        _trace(
            "Research failed, but options returned no structured validation error.",
            tool_error="Research provider failed",
            tool_name="research_analysis",
            agent_name="Research Agent",
        )
    )
    assert outcome_scorers[0].score(plain_refusal)["outcome_pass"] is False
    assert outcome_scorers[0].score(structured_error)["outcome_pass"] is True
    assert outcome_scorers[0].score(unrelated_error)["outcome_pass"] is False


def test_live_date_policy_requires_explicit_as_of_evidence() -> None:
    """A live market claim cannot pass on the trace timestamp alone."""
    scorer = DatePolicyScorer(date_policy="live", max_age_seconds=604800)

    missing = scorer.score(trace_to_scorer_input(_trace("AAPL is trading higher.")))
    present = scorer.score(
        trace_to_scorer_input(_trace("As of 2026-07-16 18:30 UTC, AAPL is higher."))
    )

    assert missing["date_policy_pass"] is False
    assert present["date_policy_pass"] is True


def test_live_date_policy_ignores_observed_content_date_before_fresh_as_of() -> None:
    """A narrative observation date cannot mask the quote's explicit as-of date."""
    result = DatePolicyScorer(date_policy="live", max_age_seconds=604800).score(
        trace_to_scorer_input(
            _trace(
                "We observed management updated guidance on January 15, 2024. "
                "As of July 16, 2026, the quote is $200."
            )
        )
    )

    assert result["date_policy_pass"] is True
    assert result["evidence_timestamp"] == "2026-07-16T00:00:00+00:00"


def test_relative_date_policy_is_explicitly_semantic_not_a_vacuous_pass() -> None:
    """A trace clock alone cannot prove that the requested relative window was used."""
    scorer = DatePolicyScorer(date_policy="relative")
    anchored = trace_to_scorer_input(
        _trace("Use the last four completed quarters and project five years forward.")
    )

    result = scorer.score(anchored)

    assert result["date_policy_pass"] is None
    assert result["skipped"] is True
    assert result["contract_scope"] == "semantic_relative_window_required"
    assert result["trace_anchor"] == NOW.isoformat()


def test_live_date_without_declared_sla_is_semantic_not_a_false_pass() -> None:
    """A generic seven-day heuristic must not certify an unspecified live SLA."""
    result = DatePolicyScorer(date_policy="live").score(
        trace_to_scorer_input(_trace("As of 2026-07-10, AAPL was 210."))
    )

    assert result["date_policy_pass"] is None
    assert result["skipped"] is True
    assert result["contract_scope"] == "semantic_live_freshness_required"


def test_live_date_ignores_unrelated_specialist_and_ancillary_timestamps() -> None:
    """A fresh side-route/article timestamp cannot certify an undated quote."""
    trace = _trace(
        "AAPL is 210.",
        tool_response={"price": 210, "ancillary": {"retrieved_at": NOW.isoformat()}},
        tool_name="market_data_analysis",
        agent_name="Market Data Agent",
    )
    trace.tool_calls.append(
        ToolCallSummary(
            tool_name="screener_lookup",
            args={},
            response={"provider_timestamp": NOW.isoformat()},
            error=None,
            latency_ms=5,
            timestamp=NOW,
            agent_name="Screener Agent",
        )
    )

    result = DatePolicyScorer(
        date_policy="live",
        expected_tools=["market_data_analysis"],
        max_age_seconds=900,
    ).score(trace_to_scorer_input(trace))

    assert result["date_policy_pass"] is False
    assert result["evidence_source"] is None


def test_quote_timestamp_dominates_fresh_retrieval_time() -> None:
    """Fetching stale quote content now does not make the quote current."""
    trace = _trace(
        "Retrieved at 2026-07-16T18:30:00Z. Quote as of 2025-01-02: AAPL $210.",
        tool_response={
            "price": 210,
            "retrieved_at": NOW.isoformat(),
            "quote_timestamp": "2025-01-02T12:00:00Z",
        },
        tool_name="market_data_analysis",
        agent_name="Market Data Agent",
    )

    result = DatePolicyScorer(
        date_policy="live",
        expected_tools=["market_data_analysis"],
        max_age_seconds=900,
    ).score(trace_to_scorer_input(trace))

    assert result["date_policy_pass"] is False
    assert result["evidence_field"] == "quote_timestamp"


def test_live_date_policy_accepts_structured_provider_timestamp() -> None:
    """A dated provider timestamp in tool evidence satisfies the live contract."""
    scorer = DatePolicyScorer(date_policy="live", max_age_seconds=604800)
    trace = _trace(
        "The latest quote is 210.",
        tool_response={"price": 210, "provider_timestamp": "2026-07-16T18:30:00Z"},
    )

    result = scorer.score(trace_to_scorer_input(trace))

    assert result["date_policy_pass"] is True
    assert result["evidence_source"] == "tool_output"


@pytest.mark.parametrize(
    "trace",
    [
        _trace("As of 2001-01-01, AAPL is trading higher."),
        _trace(
            "The latest quote is 210.",
            tool_response={"price": 210, "provider_timestamp": "2001-01-01T12:00:00Z"},
        ),
    ],
)
def test_live_date_policy_rejects_obviously_stale_disclosures(trace: Trace) -> None:
    """A dated answer is not current merely because a timestamp exists."""
    result = DatePolicyScorer(date_policy="live", max_age_seconds=604800).score(
        trace_to_scorer_input(trace)
    )

    assert result["date_policy_pass"] is False
    assert "604800 seconds" in result["reason"]


@pytest.mark.parametrize(
    "trace",
    [
        _trace(
            "As of this report, the current quote is unavailable. "
            "Historical context: 2024-01-02 was the prior observation."
        ),
        _trace(
            "The latest quote is unavailable.",
            tool_response={"note": "timestamp pending", "history_date": "2024-01-02"},
        ),
    ],
)
def test_live_date_policy_rejects_unrelated_label_and_date(trace: Trace) -> None:
    """A label and unrelated date cannot create false freshness assurance."""
    result = DatePolicyScorer(date_policy="live", max_age_seconds=604800).score(
        trace_to_scorer_input(trace)
    )

    assert result["date_policy_pass"] is False
    assert result["contract_scope"] == "explicit_as_of_disclosure"


def test_frozen_date_policy_does_not_require_current_as_of_evidence() -> None:
    """Frozen fixtures do not pretend to prove provider freshness."""
    result = DatePolicyScorer(date_policy="frozen").score(
        trace_to_scorer_input(_trace("The frozen fixture result is 42."))
    )

    assert result["date_policy_pass"] is True
    assert result["freshness_evidence_required"] is False
    assert result["contract_scope"] == "frozen_fixture_declaration"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_outcome", "maybe"),
        ("date_policy", "eventually"),
        ("requires_builtin_scorers", "yes"),
    ],
)
def test_loader_rejects_invalid_declared_metadata_even_when_extended_is_excluded(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    """Invalid metadata fails before the extended-cost filter can hide it."""
    suite = tmp_path / "invalid.yaml"
    entry = {
        "id": "I1",
        "query": "Extended case",
        "category": "I",
        "extended_only": True,
        field: value,
    }
    suite.write_text(yaml.safe_dump({"test_cases": [entry]}))

    with pytest.raises(ValueError, match=field):
        load_test_cases(path=suite)


def test_loader_requires_error_oracle_for_specialist_error(tmp_path: Path) -> None:
    """Expected specialist failures need a case-specific validation oracle."""
    suite = tmp_path / "missing-error-oracle.yaml"
    suite.write_text(
        yaml.safe_dump(
            {
                "test_cases": [
                    {
                        "id": "D1",
                        "query": "Trigger a deterministic validation error",
                        "category": "D",
                        "expected_tools": ["strategy_analysis"],
                        "expected_outcome": "specialist_error",
                        "date_policy": "frozen",
                    }
                ]
            }
        )
    )

    with pytest.raises(ValueError, match="expected_error_pattern"):
        load_test_cases(path=suite)


def test_loader_derives_hub_reject_from_legacy_expect_rejection(tmp_path: Path) -> None:
    """Legacy guardrail declarations receive the executable reject outcome."""
    suite = tmp_path / "guardrail.yaml"
    suite.write_text(
        yaml.safe_dump(
            {
                "test_cases": [
                    {
                        "id": "C1",
                        "query": "Write a poem",
                        "category": "C",
                        "expect_rejection": True,
                    }
                ]
            }
        )
    )

    [case] = load_test_cases(path=suite)

    assert case.expected_outcome == "hub_reject"


@pytest.mark.parametrize(
    "query",
    [
        "Show the latest AAPL quote",
        "Use a fresh real-time AAPL quote",
        "What happened to AAPL today?",
        "Use current Treasury yields",
        "Summarize recent TSLA catalysts",
        "When is AMZN's next earnings date?",
        "Where is SPY trading now?",
        "How did the market trade yesterday?",
        "Show AAPL's year-to-date return",
        "I plan to retire in five years",
        "Backtest AAPL from 2024-01-02 through 2025-12-31",
        "Synthetic fixture: spot is $100 and strike is $105",
    ],
)
def test_loader_requires_date_policy_for_unmistakably_temporal_queries(
    tmp_path: Path,
    query: str,
) -> None:
    """Strong date/freshness anchors cannot silently lose their contract."""
    suite = tmp_path / "temporal.yaml"
    suite.write_text(
        yaml.safe_dump({"test_cases": [{"id": "T1", "query": query, "category": "T"}]})
    )

    with pytest.raises(ValueError, match="date_policy is required"):
        load_test_cases(path=suite)


@pytest.mark.parametrize(
    "entry",
    [
        {
            "id": "C1",
            "query": "What happened in the market today?",
            "category": "C",
            "expect_rejection": True,
        },
        {
            "id": "C2",
            "query": "Give me today's football scores",
            "category": "C",
            "expected_outcome": "hub_reject",
        },
        {
            "id": "A1",
            "query": "Compare current assets with current liabilities and current ratio",
            "category": "A",
        },
        {
            "id": "E1",
            "query": "Use next-open execution for this strategy",
            "category": "E",
        },
        {
            "id": "A2",
            "query": "Screen the Russell 2000 for profitable companies",
            "category": "A",
        },
    ],
)
def test_loader_avoids_date_policy_false_positives(
    tmp_path: Path,
    entry: dict[str, Any],
) -> None:
    """Hub rejects and non-temporal finance terminology remain valid."""
    suite = tmp_path / "non_temporal.yaml"
    suite.write_text(yaml.safe_dump({"test_cases": [entry]}))

    [case] = load_test_cases(path=suite)

    assert case.date_policy is None


def test_builtin_suite_satisfies_temporal_metadata_validation() -> None:
    """Every active built-in case passes the loader's date-policy contract."""
    assert load_test_cases(include_extended=True)


def test_only_timeless_routing_and_capability_cases_omit_date_policy() -> None:
    """Implicitly current financial analyses cannot silently lose date semantics."""
    cases = load_test_cases(include_extended=True)

    assert {case.id for case in cases if case.date_policy is None} == {
        "A4",
        "A19",
        "C1",
        "C2",
        "C3",
        "C4",
        "C7",
        "E29",
        "E30",
        "E31",
        "E32",
    }


@pytest.mark.parametrize("case_id", ["G25", "G55"])
def test_deterministic_validation_rejections_declare_specialist_error(case_id: str) -> None:
    """Expected validation errors are evaluated as outcomes, not accidental failures."""
    cases = {case.id: case for case in load_test_cases(include_extended=True)}

    assert cases[case_id].expected_outcome == "specialist_error"
    assert cases[case_id].date_policy == "frozen"
    assert cases[case_id].expected_error_pattern


def test_all_specialist_error_cases_declare_specific_error_oracles() -> None:
    """No validation case may pass on an arbitrary specialist crash."""
    cases = load_test_cases(include_extended=True)

    specialist_error_cases = [case for case in cases if case.expected_outcome == "specialist_error"]

    assert {case.id for case in specialist_error_cases} == {"D9", "D10", "E16", "G25", "G55", "I13"}
    assert all(case.expected_error_pattern for case in specialist_error_cases)


@pytest.mark.parametrize(
    ("case_id", "expected_policy"),
    [
        ("A23", "live"),
        ("A5", "relative"),
        ("A29", "frozen"),
        ("G52", "frozen"),
        ("I14", "live"),
    ],
)
def test_builtin_suite_preserves_date_policy_semantics(
    case_id: str,
    expected_policy: str,
) -> None:
    """Point-in-time, rolling, and fixed cases retain distinct policies."""
    cases = {case.id: case for case in load_test_cases(include_extended=True)}

    assert cases[case_id].date_policy == expected_policy


def test_non_success_outcome_does_not_require_impossible_freshness_evidence() -> None:
    """A correct no-data result has no provider observation timestamp to score."""
    test_case = EvalTestCase(
        id="D1",
        query="What is FAKESYM trading at now?",
        category="D",
        expected_tools=["market_data_analysis"],
        expected_outcome="data_unavailable",
        date_policy="live",
    )
    evaluator = object.__new__(OBaIEvaluator)
    evaluator.use_builtin_scorers = False
    evaluator.judge_model = "offline"

    scorers = evaluator._build_scorers(test_case)

    assert not any(isinstance(scorer, DatePolicyScorer) for scorer in scorers)


def test_cli_and_experiment_aggregate_new_contract_scorers() -> None:
    """Outcome and date failures participate in every aggregate result."""
    result = _scored_result(
        {
            "OutcomeContractScorer": {"outcome_pass": False},
            "DatePolicyScorer": {"date_policy_pass": True},
        }
    )

    assert cli._test_case_passed(result) is False
    flat = experiment._flatten_scores(result)

    assert flat["outcome_pass"] == 0.0
    assert flat["date_policy_pass"] == 1.0
    assert flat["date_policy_applicable"] == 1.0
    assert "strategy_contract_pass" not in flat
    assert flat["strategy_contract_applicable"] == 0.0


def test_experiment_aggregates_strategy_contract_scorers() -> None:
    """Experiment mode must not discard deterministic strategy failures."""
    result = {
        "scores": {
            "StrategyContractScorer": {"contract_pass": False},
            "StrategyGroundingScorer": {"grounding_pass": True},
            "StrategyDecisionScorer": {"strategy_decision_pass": False},
        }
    }

    flat = experiment._flatten_scores(result)

    assert flat["strategy_contract_pass"] == 0.0
    assert flat["strategy_contract_applicable"] == 1.0
    assert flat["strategy_grounding_pass"] == 1.0
    assert flat["strategy_grounding_applicable"] == 1.0
    assert flat["strategy_decision_pass"] == 0.0
    assert flat["strategy_decision_applicable"] == 1.0


def test_forbidden_research_route_fails_boundary_case() -> None:
    """A required news call cannot hide an explicitly forbidden research detour."""
    case = next(case for case in load_test_cases() if case.id == "H18")
    scorer = ToolOrchestrationScorer(
        expected_tools=case.expected_tools,
        forbidden_tools=case.forbidden_tools,
    )
    result = scorer.score(
        {
            "tool_calls": [
                {"tool_name": "events_news_analysis"},
                {"tool_name": "research_analysis"},
            ]
        }
    )

    assert result["correct_tools"] is False
    assert result["forbidden_tools_called"] == ["research_analysis"]


def test_identical_repeat_is_redundant_but_distinct_arguments_are_not() -> None:
    """Efficiency rejects duplicate spend without banning valid specialist follow-ups."""
    scorer = EfficiencyScorer(max_tool_calls=5, penalize_redundant=True)
    duplicate = scorer.score(
        {
            "tool_calls": [
                {"tool_name": "market_data_analysis", "args": {"symbol": "AAPL"}},
                {"tool_name": "market_data_analysis", "args": {"symbol": "AAPL"}},
            ]
        }
    )
    distinct = scorer.score(
        {
            "tool_calls": [
                {"tool_name": "market_data_analysis", "args": {"symbol": "AAPL"}},
                {"tool_name": "market_data_analysis", "args": {"symbol": "MSFT"}},
            ]
        }
    )

    assert duplicate["redundant_calls"] == 1
    assert duplicate["within_budget"] is False
    assert distinct["redundant_calls"] == 0
    assert distinct["within_budget"] is True


def test_experiment_preserves_continuous_metric_values() -> None:
    """Flattening must not silently replace continuous scorer values with booleans."""
    flat = experiment._flatten_scores(
        {
            "scores": {
                "EfficiencyScorer": {"within_budget": True, "efficiency": 0.75},
                "AnswerRelevanceScorer": {"relevant": True, "score": 0.51},
                "LLMJudgeScorer": {"rubric_pass": True, "average_score": 3.6},
            }
        }
    )

    assert flat["efficiency_score"] == 0.75
    assert flat["relevance_score"] == 0.51
    assert flat["rubric_avg"] == 3.6


def test_experiment_optional_scorer_error_is_applicable_and_fail_closed() -> None:
    """N/A is neutral, but a scorer that ran and crashed is a real zero."""
    flat = experiment._flatten_scores({"scores": {"DatePolicyScorer": {"error": "boom"}}})

    assert flat["date_policy_applicable"] == 1.0
    assert flat["date_policy_pass"] == 0.0
    assert flat["evaluation_complete"] == 0.0
    assert flat["scorer_error"] == 1.0


def test_optional_extractor_omits_not_applicable_rows_from_metric_average() -> None:
    """N/A rows emit no score instead of a misleading zero or neutral one."""
    metric = experiment.ExtractorMetric(
        name="date_policy_pass",
        key="date_policy_pass",
        applicability_key="date_policy_applicable",
    )

    assert metric.score(date_policy_applicable=0.0) == []
    applicable = metric.score(date_policy_applicable=1.0, date_policy_pass=0.0)
    assert not isinstance(applicable, list)
    assert applicable.value == 0.0


def test_unexpected_success_row_semantic_skips_are_incomplete() -> None:
    """A success row cannot suppress its paid semantic judges with skipped=true."""
    result = _scored_result(
        {
            "OutcomeContractScorer": {"outcome_pass": True},
            "LLMJudgeScorer": {"skipped": True, "rubric_pass": None},
            "FaithfulnessScorer": {"skipped": True, "faithfulness_pass": None},
            "CompletenessScorer": {"skipped": True, "completeness_pass": None},
        }
    )

    assert cli._test_case_passed(result) is None
    flat = experiment._flatten_scores(result)
    assert flat["evaluation_complete"] == 0.0
    assert flat["scorer_error"] == 1.0
    assert flat["rubric_avg_applicable"] == 1.0
    assert flat["faithfulness_numeric_accuracy_applicable"] == 1.0
    assert flat["completeness_coverage_applicable"] == 1.0


def test_required_deterministic_scorer_cannot_skip_to_green() -> None:
    """Only explicit DatePolicy/strategy N-A contracts may skip."""
    result = _scored_result(
        {
            "OutcomeContractScorer": {"outcome_pass": True},
            "ResponseQualityScorer": {"skipped": True, "skip_reason": "not_applicable"},
        }
    )

    assert cli._test_case_passed(result) is None
    assert experiment._flatten_scores(result)["evaluation_complete"] == 0.0


def test_declared_relative_date_skip_remains_neutral() -> None:
    """The exact relative-date N-A reason delegates to mandatory semantic scoring."""
    result = _scored_result(
        {
            "OutcomeContractScorer": {"outcome_pass": True},
            "DatePolicyScorer": {
                "skipped": True,
                "skip_reason": "relative_window_requires_semantic_validation",
                "date_policy": "relative",
                "contract_scope": "semantic_relative_window_required",
                "date_policy_pass": None,
            },
        }
    )

    assert cli._test_case_passed(result) is True
    assert experiment._flatten_scores(result)["evaluation_complete"] == 1.0


def test_date_skip_reason_with_wrong_policy_metadata_is_incomplete() -> None:
    """A scorer bug cannot use a valid reason string to bypass a frozen/live SLA."""
    result = _scored_result(
        {
            "OutcomeContractScorer": {"outcome_pass": True},
            "DatePolicyScorer": {
                "skipped": True,
                "skip_reason": "relative_window_requires_semantic_validation",
                "date_policy": "frozen",
                "contract_scope": "semantic_relative_window_required",
                "date_policy_pass": None,
            },
        }
    )

    assert cli._test_case_passed(result) is None
    assert experiment._flatten_scores(result)["evaluation_complete"] == 0.0


def test_flattened_experiment_metrics_fail_closed_on_missing_expected_scorer() -> None:
    """Remote experiment metrics cannot look complete when a planned scorer vanished."""
    flat = experiment._flatten_scores(
        {
            "expected_scorers": ["OutcomeContractScorer", "ResponseQualityScorer"],
            "scores": {"OutcomeContractScorer": {"outcome_pass": True}},
        }
    )

    assert flat["evaluation_complete"] == 0.0
    assert flat["scorer_error"] == 1.0


def test_hub_reject_does_not_launch_irrelevant_semantic_judges() -> None:
    """Guardrail tripwires are judged by rejection evidence, not an empty answer rubric."""
    evaluator = object.__new__(OBaIEvaluator)
    evaluator.use_builtin_scorers = True
    evaluator.judge_model = "offline"
    case = EvalTestCase(query="Write a poem", expected_outcome="hub_reject")

    names = {type(scorer).__name__ for scorer in evaluator._build_scorers(case)}

    assert "OutcomeContractScorer" in names
    assert "LLMJudgeScorer" not in names
    assert "FaithfulnessScorer" not in names
    assert "CompletenessScorer" not in names


def test_cli_does_not_count_a_scorer_error_as_a_pass() -> None:
    """Missing judge evidence is an evaluation error, never a green case."""
    result = _scored_result(
        {
            "OutcomeContractScorer": {"outcome_pass": True},
            "DatePolicyScorer": {"error": "scorer crashed"},
        }
    )

    assert cli._test_case_passed(result) is None


@pytest.mark.parametrize(
    "scores",
    [
        {"ResponseQualityScorer": {"quality_pass": True}},
        {"OutcomeContractScorer": {}},
        {"OutcomeContractScorer": "not-a-dict"},
        {"OutcomeContractScorer": {"outcome_pass": 0}},
        {"OutcomeContractScorer": {"outcome_pass": "false"}},
    ],
)
def test_mandatory_outcome_verdict_must_be_present_literal_boolean(
    scores: dict[str, Any],
) -> None:
    """Missing or truthy/falsy malformed verdicts are incomplete, never green."""
    assert cli._test_case_passed(_scored_result(scores)) is None


def test_expected_scorer_set_cannot_be_truncated() -> None:
    """A partially collected success-row score set is incomplete, never green."""
    result = {
        "_required_scorers": [
            "OutcomeContractScorer",
            "DatePolicyScorer",
            "LLMJudgeScorer",
        ],
        "expected_scorers": [
            "OutcomeContractScorer",
        ],
        "scores": {"OutcomeContractScorer": {"outcome_pass": True}},
    }

    assert cli._test_case_passed(result) is None


def test_cli_explicit_contract_failure_precedes_another_scorer_error() -> None:
    """A captured product failure remains a failure despite another scorer error."""
    result = _scored_result(
        {
            "OutcomeContractScorer": {"outcome_pass": False},
            "DatePolicyScorer": {"error": "scorer crashed"},
        }
    )

    assert cli._test_case_passed(result) is False


def test_outcome_contract_cannot_skip_to_green() -> None:
    """The mandatory outcome oracle is never an optional/N-A scorer."""
    result = _scored_result({"OutcomeContractScorer": {"skipped": True, "outcome_pass": None}})

    assert cli._test_case_passed(result) is None


def test_legacy_guardrail_flag_cannot_bypass_scorer_contract() -> None:
    """Legacy top-level fields cannot make an unscored paid row green."""
    assert cli._test_case_passed({"guardrail_rejected": True}) is None


def test_evaluate_suite_exits_nonzero_for_contract_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A printed failed score must make the regression command fail."""
    case = EvalTestCase(id="T1", query="test", category="A")
    monkeypatch.setattr(cli, "print_banner", lambda: None)
    monkeypatch.setattr(cli, "load_test_cases", lambda **_kwargs: [case])
    monkeypatch.setattr(
        cli,
        "run_evaluate_as_experiment",
        lambda **_kwargs: (
            "offline",
            [
                {
                    "test_id": "T1",
                    "expected_scorers": list(_deterministic_gate_scores()),
                    "scores": _deterministic_gate_scores(outcome_pass=False),
                }
            ],
        ),
    )

    with pytest.raises(typer.Exit) as raised:
        cli.evaluate_cmd(suite=True, no_builtin=True)

    assert raised.value.exit_code == 1


def test_evaluate_suite_exits_incomplete_for_scorer_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scorer crash is an incomplete gate, never a successful process."""
    case = EvalTestCase(id="T1", query="test", category="A")
    monkeypatch.setattr(cli, "print_banner", lambda: None)
    monkeypatch.setattr(cli, "load_test_cases", lambda **_kwargs: [case])
    monkeypatch.setattr(
        cli,
        "run_evaluate_as_experiment",
        lambda **_kwargs: (
            "offline",
            [
                {
                    "test_id": "T1",
                    "expected_scorers": [
                        "OutcomeContractScorer",
                        "ResponseQualityScorer",
                        "EfficiencyScorer",
                    ],
                    "scores": {
                        "OutcomeContractScorer": {"outcome_pass": True},
                        "ResponseQualityScorer": {"error": "scorer crashed"},
                        "EfficiencyScorer": {"within_budget": True},
                    },
                }
            ],
        ),
    )

    with pytest.raises(typer.Exit) as raised:
        cli.evaluate_cmd(suite=True, no_builtin=True)

    assert raised.value.exit_code == 3


def test_paid_suite_missing_file_fails_before_falling_back(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A typo in --file cannot silently execute the built-in paid corpus."""
    called = False

    def forbidden_experiment(**_kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        nonlocal called
        called = True
        return "unexpected", []

    monkeypatch.setattr(cli, "print_banner", lambda: None)
    monkeypatch.setattr(cli, "run_evaluate_as_experiment", forbidden_experiment)

    with pytest.raises(typer.Exit) as raised:
        cli.evaluate_cmd(suite=True, file=tmp_path / "missing.yaml")

    assert raised.value.exit_code == 2
    assert called is False


def test_paid_suite_invalid_schema_is_configuration_exit_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Metadata validation errors cannot masquerade as product regressions."""
    monkeypatch.setattr(cli, "print_banner", lambda: None)
    monkeypatch.setattr(
        cli,
        "load_test_cases",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("bad metadata")),
    )

    with pytest.raises(typer.Exit) as raised:
        cli.evaluate_cmd(suite=True, no_builtin=True)

    assert raised.value.exit_code == 2


def test_paid_suite_runtime_value_error_is_incomplete_not_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A query-task crash after validation uses incomplete exit code three."""
    case = EvalTestCase(id="T1", query="test", category="A")
    monkeypatch.setattr(cli, "print_banner", lambda: None)
    monkeypatch.setattr(cli, "load_test_cases", lambda **_kwargs: [case])
    monkeypatch.setattr(
        cli,
        "run_evaluate_as_experiment",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("task crashed")),
    )

    with pytest.raises(typer.Exit) as raised:
        cli.evaluate_cmd(suite=True, no_builtin=True)

    assert raised.value.exit_code == 3


def test_missing_semantic_judge_key_fails_before_paid_experiment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The suite cannot spend on OBaI and discover a missing judge key afterward."""
    called = False
    case = EvalTestCase(
        id="T1",
        query="test",
        category="A",
        requires_builtin_scorers=True,
    )

    def forbidden_experiment(**_kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        nonlocal called
        called = True
        return "unexpected", []

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "print_banner", lambda: None)
    monkeypatch.setattr(cli, "load_test_cases", lambda **_kwargs: [case])
    monkeypatch.setattr(cli, "run_evaluate_as_experiment", forbidden_experiment)

    with pytest.raises(typer.Exit) as raised:
        cli.evaluate_cmd(suite=True)

    assert raised.value.exit_code == 2
    assert called is False


def test_ad_hoc_evaluation_checks_judge_key_before_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The non-suite evaluate path has the same pre-spend credential boundary."""
    called = False

    async def forbidden_query(**_kwargs: Any) -> Trace:
        nonlocal called
        called = True
        raise AssertionError("query must not start without judge credentials")

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "print_banner", lambda: None)
    monkeypatch.setattr(cli, "run_query_with_trace", forbidden_query)

    with pytest.raises(typer.Exit) as raised:
        cli.evaluate_cmd(query_text="What is AAPL trading at?")

    assert raised.value.exit_code == 2
    assert called is False


def test_paid_suite_result_cardinality_mismatch_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dropped task cannot be silently omitted or crash strict summary zipping."""
    cases = [
        EvalTestCase(id="T1", query="one", category="A"),
        EvalTestCase(id="T2", query="two", category="A"),
    ]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-test-key")
    monkeypatch.setattr(cli, "print_banner", lambda: None)
    monkeypatch.setattr(cli, "load_test_cases", lambda **_kwargs: cases)
    monkeypatch.setattr(
        cli,
        "run_evaluate_as_experiment",
        lambda **_kwargs: (
            "offline",
            [
                {
                    "test_id": "T1",
                    "expected_scorers": ["OutcomeContractScorer"],
                    "scores": {"OutcomeContractScorer": {"outcome_pass": True}},
                }
            ],
        ),
    )

    with pytest.raises(typer.Exit) as raised:
        cli.evaluate_cmd(suite=True)

    assert raised.value.exit_code == 3


def test_paid_suite_invalid_output_path_fails_before_experiment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unusable export destination is a pre-spend configuration failure."""
    called = False
    case = EvalTestCase(id="T1", query="test", category="A")

    def forbidden_experiment(**_kwargs: Any) -> tuple[str, list[dict[str, Any]]]:
        nonlocal called
        called = True
        return "unexpected", []

    monkeypatch.setattr(cli, "print_banner", lambda: None)
    monkeypatch.setattr(cli, "load_test_cases", lambda **_kwargs: [case])
    monkeypatch.setattr(cli, "run_evaluate_as_experiment", forbidden_experiment)

    with pytest.raises(typer.Exit) as raised:
        cli.evaluate_cmd(suite=True, no_builtin=True, export=Path("/"))

    assert raised.value.exit_code == 2
    assert called is False


def test_remote_dataset_contract_drift_is_rejected_before_query() -> None:
    """A same-ID mutable Opik row cannot substitute a different paid query."""
    case = EvalTestCase(id="T1", query="local query", category="A")
    remote_row = case.to_dataset_row()
    remote_row["query"] = "tampered remote query"
    query_called = False

    class FakeEvaluator:
        async def evaluate_trace(self, _trace: Trace, _case: EvalTestCase) -> dict[str, Any]:
            return {}

    async def query_runner(_query: str, _model: str | None, _verbose: bool) -> Trace:
        nonlocal query_called
        query_called = True
        return _trace("unexpected")

    task = experiment.make_verbose_experiment_task(
        FakeEvaluator(),  # type: ignore[arg-type]
        {case.id: case},
        query_runner,
        [],
    )

    with pytest.raises(RuntimeError, match="dataset contract drift"):
        task(remote_row)

    assert query_called is False


def test_empty_paid_selection_is_configuration_exit_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No selected row is not a captured product regression."""
    monkeypatch.setattr(cli, "print_banner", lambda: None)
    monkeypatch.setattr(cli, "load_test_cases", lambda **_kwargs: [])

    with pytest.raises(typer.Exit) as raised:
        cli.evaluate_cmd(suite=True, category="Z")

    assert raised.value.exit_code == 2


def test_cost_class_remains_metadata_not_a_fake_scorer() -> None:
    """Cost classification remains reporting metadata rather than correctness."""
    evaluator = object.__new__(OBaIEvaluator)
    evaluator.use_builtin_scorers = False
    evaluator.judge_model = "offline"
    case = EvalTestCase(
        query="A live quote",
        expected_outcome="success",
        date_policy="live",
        cost_class="high",
    )

    names = {type(scorer).__name__ for scorer in evaluator._build_scorers(case)}

    assert "OutcomeContractScorer" in names
    assert "DatePolicyScorer" in names
    assert "CostClassScorer" not in names


@pytest.mark.parametrize("case_id", ["I16", "I18", "I19", "I20"])
def test_extended_strategy_backtests_receive_artifact_and_grounding_checks(
    case_id: str,
) -> None:
    """Extended strategy accuracy cases cannot degrade to routing-only checks."""
    case = next(case for case in load_test_cases(include_extended=True) if case.id == case_id)
    evaluator = object.__new__(OBaIEvaluator)
    evaluator.use_builtin_scorers = False
    evaluator.judge_model = "offline"

    names = {type(scorer).__name__ for scorer in evaluator._build_scorers(case)}

    assert "StrategyContractScorer" in names
    assert "StrategyGroundingScorer" in names


@pytest.mark.parametrize(
    ("expected_outcome", "min_length"),
    [
        ("hub_reject", 0),
        ("specialist_error", 10),
        ("data_unavailable", 10),
        ("partial_refusal", 10),
    ],
)
def test_non_success_contracts_do_not_require_numeric_response_quality(
    expected_outcome: str,
    min_length: int,
) -> None:
    """Expected error/refusal responses are not forced to contain numbers."""
    evaluator = object.__new__(OBaIEvaluator)
    evaluator.use_builtin_scorers = False
    evaluator.judge_model = "offline"
    case = EvalTestCase(
        query="test",
        expected_outcome=expected_outcome,
        expected_error_pattern=("validation" if expected_outcome == "specialist_error" else None),
        expected_response_pattern=(
            r"cannot|unsupported" if expected_outcome == "partial_refusal" else None
        ),
        forbidden_response_pattern=(
            r"completed|exported" if expected_outcome == "partial_refusal" else None
        ),
    )

    quality = next(
        scorer
        for scorer in evaluator._build_scorers(case)
        if type(scorer).__name__ == "ResponseQualityScorer"
    )

    assert quality.require_numbers is False
    assert quality.min_length == min_length
