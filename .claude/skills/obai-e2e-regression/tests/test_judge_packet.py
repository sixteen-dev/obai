from __future__ import annotations

import pytest
from judge_packet import PROVIDER_FAILURE_RE, REFUSAL_RE, _skill_name_from_call, judge_packet


@pytest.mark.parametrize(
    "text",
    [
        # A status code counts as a provider failure only in machine-unambiguous
        # HTTP context (the forms OBaI serializers emit: "failed with status <n>",
        # the "status_code" field), plus the original textual outage signals.
        "HTTP 429",
        "HTTP/1.1 503",
        "status code: 500",
        "error code 502",
        "429 Too Many Requests",
        "503 Service Unavailable",
        "504 Gateway Timeout",
        "API request failed with status 504",
        "Request failed with status 504",
        '{"isError": true, "error": "API request failed with status 504", "status_code": 504}',
        '{"isError": true, "error": "FMP API server error", "status_code": 500}',
        '{"isError": true, "error": "FMP API temporarily unavailable", "status_code": 502}',
        "FMP API service unavailable - please try again later",
        "Authentication failed - invalid API key",
        "Rate limit exceeded - please try again later",
        "provider exploded",
        "Connection error.",
        "Incorrect API key provided",
        "insufficient_quota",
        "Internal Server Error",
        "Too Many Requests",
    ],
)
def test_provider_failure_re_matches_genuine_http_errors(text: str) -> None:
    assert PROVIDER_FAILURE_RE.search(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        # Bare status-looking numbers in ordinary financial prose are not errors.
        "the tool returned a mismatched 504-session start date",
        "S&P 500 index rose 1%",
        "429 shares were priceable",
        "500 stocks in the sample",
        "status of the 500 companies",
        # A legitimate no-data answer must not be misread as a provider outage.
        "This ETF may not be supported or data may be temporarily unavailable",
        # Ordinary financial prose a bare "status <code>" match would flag.
        "Screening status: 500 matches found; here are the top ranked names.",
        "Portfolio status: 429 open positions across 12 sectors.",
        "Account status 403(b) rollover is complete and settled.",
        "Options data unavailable: this equity has no listed options.",
        # A financial answer NARRATING a third-party outage is not OBaI's own
        # provider failing; matching outage vocabulary in prose is unreliable.
        "Thousands of retail traders were unable to connect to Robinhood during"
        " the meme-stock frenzy, though positions settled by close.",
        "Cloudflare reported its servers were temporarily unavailable for ~30"
        " minutes; affected names (NET, DDOG) recovered intraday.",
        "The exchange's matching engine timed out briefly during the open.",
        "Non-professional access forbidden for this exchange's colo feed.",
        "During the open the matching-engine server error rate briefly rose.",
    ],
)
def test_provider_failure_re_ignores_financial_lookalike_numbers(text: str) -> None:
    assert PROVIDER_FAILURE_RE.search(text) is None


def test_financial_session_count_does_not_force_inconclusive_provider() -> None:
    # RC-5: "504-session" (504 trading sessions) must not read as an HTTP 504.
    packet = _packet(
        "As of 2026-07-15, the tool returned a mismatched 504-session start date; AAPL is 210."
    )
    result = judge_packet(_case(), packet)

    assert result.verdict == "pass"


def test_relayed_provider_outage_with_status_is_not_scored_as_pass() -> None:
    # A success case whose final response relays a code-bearing outage
    # ("failed with status 504") must be quarantined, never passed.
    packet = _packet(
        "As of today, position summary follows. "
        "Note from the data source: API request failed with status 504."
    )
    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_provider"


def test_codeless_provider_outage_span_is_not_scored_as_pass() -> None:
    # Code-less outage messages are caught structurally by the isError span
    # backstop, not by text matching, so they still never score as a pass.
    packet = _packet("As of today, AAPL is 210.")
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"isError": True, "error": "FMP API temporarily unavailable"},
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
        },
    ]
    result = judge_packet(_case(), packet)

    assert result.verdict != "pass"


def test_data_unavailable_temporarily_unavailable_is_not_a_provider_outage() -> None:
    # "temporarily unavailable" without a provider token is a legitimate
    # no-data answer, not an outage, so the degraded branch must stand.
    case = _case(
        expected_outcome="data_unavailable",
        degraded_outcome_patterns={"data_unavailable": r"(?i)temporarily unavailable|no data"},
        assertions={"required_text": ["as of"], "required_evidence": ["trace.id"]},
    )
    packet = _packet(
        "As of today, this ETF may not be supported or data may be temporarily unavailable."
    )
    result = judge_packet(case, packet)

    assert result.verdict != "inconclusive_provider"


def test_skill_name_read_from_double_encoded_span() -> None:
    # Real trace shape: ``input`` is double-JSON-encoded (skill name escaped),
    # while ``output`` carries the authoritative unescaped name. The escaped
    # input alone must not hide a skill that actually loaded.
    span = {
        "name": "load_skill",
        "input": '{"input": "{\\"skill_name\\":\\"obai-research-routing\\"}"}',
        "output": '{"status": "loaded", "skill_name": "obai-research-routing"}',
    }
    assert _skill_name_from_call(span) == "obai-research-routing"


@pytest.mark.parametrize(
    "text",
    [
        # A refusal is recognized regardless of the verb's inflection. The
        # product opens a graceful in-band refusal with "Refused:", so matching
        # only the bare stem "refuse" would misread it as an ordinary success.
        "Refused: the legs have different expirations.",
        "I am refusing that invalid aggregate.",
        "The specialist refuses to model both legs at one expiry.",
        "This is a refusal, not a computed payoff.",
        "I cannot compute a shared-expiry profile.",
        "That request is unsupported for mixed expirations.",
    ],
)
def test_refusal_re_matches_refusal_inflections(text: str) -> None:
    assert REFUSAL_RE.search(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        # Ordinary financial prose that merely contains the substring must not
        # be read as a refusal.
        "The fund is diversified across sectors.",
        "Confused signals aside, the trend is up.",
    ],
)
def test_refusal_re_ignores_non_refusal_prose(text: str) -> None:
    assert REFUSAL_RE.search(text) is None


def _case(**overrides: object) -> dict:
    return {
        "id": "T1",
        "expected_outcome": "success",
        "expected_tools": ["market_data_analysis"],
        "expected_skills": ["obai-market-data-routing"],
        "assertions": {
            "required_text": ["as of"],
            "forbidden_text": ["guaranteed return"],
            "required_evidence": ["trace.id"],
        },
        **overrides,
    }


def _packet(
    response: str = "As of 2026-07-15, AAPL is 210.",
    tools: list[str] | None = None,
    curated: str = "--- SKILL LOADS (1) ---\n- obai-market-data-routing: loaded",
    **overrides: object,
) -> dict:
    tool_names = tools if tools is not None else ["load_skill", "market_data_analysis"]
    packet = {
        "id": "T1",
        "cli": {
            "exit_code": 0,
            "timed_out": False,
            "stdout_json": {
                "response": response,
                "tool_calls": [{"tool": tool} for tool in tool_names],
                "guardrail_rejected": False,
            },
        },
        "trace": {"id": "trace-123", "curated": curated},
    }
    packet.update(overrides)
    return packet


def test_successful_contract_passes() -> None:
    result = judge_packet(_case(), _packet())

    assert result.verdict == "pass"
    assert result.checks_failed == []


def test_forbidden_false_pass_language_is_product_failure() -> None:
    case = _case(
        assertions={
            "required_text": ["blocking_quality_warning"],
            "forbidden_text": ["artifact was exported", "completed successfully"],
        }
    )
    packet = _packet(
        "Backtest completed successfully; blocking_quality_warning=false and artifact was exported."
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("forbidden_text" in failure for failure in result.checks_failed)


def test_provider_permission_failure_is_inconclusive_not_pass() -> None:
    result = judge_packet(
        _case(), _packet("Options data unavailable: API returned 403 permission denied.")
    )

    assert result.verdict == "inconclusive_provider"


def test_benign_api_returned_wording_is_not_a_provider_failure() -> None:
    result = judge_packet(_case(), _packet("As of today, the API returned an AAPL price of 210."))

    assert result.verdict == "pass"


def test_missing_required_evidence_is_distinct() -> None:
    packet = _packet()
    packet["trace"] = {"id": None, "curated": None}

    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_missing_evidence"


def test_expected_partial_refusal_can_pass_degraded() -> None:
    case = _case(
        expected_outcome="partial_refusal",
        expected_tools=[],
        expected_skills=[],
        assertions={
            "required_text": ["cannot place live orders", "paper simulation"],
            "forbidden_text": ["order placed"],
        },
    )
    packet = _packet("I cannot place live orders. Here is a paper simulation checklist.", tools=[])

    result = judge_packet(case, packet)

    assert result.verdict == "pass_degraded"


def test_typographic_hyphen_does_not_break_required_text() -> None:
    # The product renders markdown with non-breaking hyphens (U+2011). An
    # assertion written with an ASCII hyphen must still match "warm-up",
    # "out-of-sample" and similar terms so a correct answer is not false-failed.
    case = _case(
        expected_tools=[],
        expected_skills=[],
        assertions={
            "required_text": [
                {"regex": r"\bwarm[- ]?up\b"},
                {"regex": r"\bout[- ]of[- ]sample\b"},
            ]
        },
    )
    packet = _packet(
        "Each test fold uses a fully warm‑up 200-day SMA; out‑of‑sample Sharpe is positive.",
        tools=[],
    )

    result = judge_packet(case, packet)

    assert result.verdict == "pass"
    assert not any("required_text missing" in failure for failure in result.checks_failed)


def test_typographic_dash_still_triggers_forbidden_text() -> None:
    # Dash folding must not let a forbidden payoff number hide behind a
    # non-breaking hyphen.
    case = _case(
        expected_tools=[],
        expected_skills=[],
        assertions={"forbidden_text": [{"regex": r"\bmax[- ]profit\b[^.\n]{0,10}\$?\d"}]},
    )
    packet = _packet("The max‑profit is $250 at expiry.", tools=[])

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("forbidden_text present" in failure for failure in result.checks_failed)


def test_inflected_refusal_is_degraded_not_success() -> None:
    # The product opens its graceful refusal with "Refused:"; the observed
    # outcome must be partial_refusal, not a silent success.
    case = _case(
        expected_outcome="partial_refusal",
        acceptable_outcomes=["specialist_error"],
        expected_tools=[],
        expected_skills=[],
        assertions={"required_text": ["different expir"]},
    )
    packet = _packet(
        "Refused: the legs have different expirations, so a shared-expiry profile is invalid.",
        tools=[],
    )

    result = judge_packet(case, packet)

    assert result.observed_outcome == "partial_refusal"
    assert result.verdict == "pass_degraded"


@pytest.mark.parametrize(
    "response",
    [
        "The provider timestamp is not available; the quote is otherwise complete.",
        "This capped sample cannot be called a complete ranking; five rows were returned.",
        "Causation cannot be established, but the timestamped catalysts are listed below.",
    ],
)
def test_success_caveats_are_not_misclassified_as_whole_request_refusals(
    response: str,
) -> None:
    case = _case(
        expected_outcome="success",
        expected_tools=[],
        expected_skills=[],
        assertions={},
    )

    result = judge_packet(case, _packet(response, tools=[]))

    assert result.observed_outcome == "success"
    assert result.verdict == "pass"


def test_declared_conditional_refusal_is_degraded_not_false_failure() -> None:
    case = _case(
        expected_tools=[],
        expected_skills=[],
        acceptable_outcomes=["partial_refusal"],
        assertions={"required_text": ["cannot export"]},
    )

    result = judge_packet(case, _packet("I cannot export because eligibility failed.", tools=[]))

    assert result.verdict == "pass_degraded"


@pytest.mark.parametrize(
    ("response", "outcome", "pattern"),
    [
        (
            "Eligibility is not established; no artifact was created.",
            "partial_refusal",
            r"(?i)\beligibility (?:is |was )?not established\b",
        ),
        (
            "No trade because quotes are stale.",
            "data_unavailable",
            r"(?i)(?:^|[.!?]\s+)no[- ]trade\b|\bquotes? (?:are |were )?stale\b",
        ),
    ],
)
def test_case_specific_degraded_branch_is_not_misclassified_as_success(
    response: str,
    outcome: str,
    pattern: str,
) -> None:
    """A fail-closed parent branch must prevent an inapplicable paid child."""
    case = _case(
        expected_tools=[],
        expected_skills=[],
        acceptable_outcomes=[outcome],
        degraded_outcome_patterns={outcome: pattern},
        assertions={"required_text": [{"regex": pattern}]},
    )

    result = judge_packet(case, _packet(response, tools=[]))

    assert result.observed_outcome == outcome
    assert result.verdict == "pass_degraded"


def test_declared_no_directly_resolving_market_is_data_unavailable() -> None:
    case = _case(
        expected_tools=[],
        expected_skills=[],
        acceptable_outcomes=["data_unavailable"],
        assertions={"required_text": ["no directly resolving market"]},
    )

    result = judge_packet(
        case,
        _packet(
            "As of 2026-07-15T14:00:00Z, no directly resolving market exists.",
            tools=[],
        ),
    )

    assert result.observed_outcome == "data_unavailable"
    assert result.verdict == "pass_degraded"


def test_wrong_tool_sequence_is_product_failure() -> None:
    case = _case(
        expected_tools=["market_data_analysis", "events_news_analysis"],
        expected_sequence=["market_data_analysis", "events_news_analysis"],
    )
    packet = _packet(
        tools=["load_skill", "events_news_analysis", "market_data_analysis"],
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("sequence" in failure for failure in result.checks_failed)


def test_later_duplicate_cannot_hide_first_occurrence_sequence_violation() -> None:
    case = _case(
        expected_tools=["market_data_analysis", "events_news_analysis"],
        expected_sequence=["market_data_analysis", "events_news_analysis"],
    )
    packet = _packet(
        tools=[
            "load_skill",
            "events_news_analysis",
            "market_data_analysis",
            "events_news_analysis",
        ],
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("sequence" in failure for failure in result.checks_failed)


def test_unrelated_leading_tool_does_not_violate_string_sequence() -> None:
    case = _case(
        expected_tools=["market_data_analysis", "events_news_analysis"],
        expected_sequence=["market_data_analysis", "events_news_analysis"],
    )
    packet = _packet(
        tools=[
            "load_skill",
            "telemetry_helper",
            "market_data_analysis",
            "events_news_analysis",
        ],
    )

    result = judge_packet(case, packet)

    assert result.verdict == "pass"


def test_relation_sequence_keeps_first_occurrence_semantics() -> None:
    case = _case(
        expected_tools=["market_data_analysis", "events_news_analysis"],
        expected_sequence=[{"before": "market_data_analysis", "after": "events_news_analysis"}],
    )
    packet = _packet(
        tools=[
            "load_skill",
            "events_news_analysis",
            "market_data_analysis",
            "events_news_analysis",
        ],
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("sequence" in failure for failure in result.checks_failed)


def test_unexpected_specialist_is_visible_for_semantic_review() -> None:
    packet = _packet(tools=["load_skill", "market_data_analysis", "research_analysis"])

    result = judge_packet(_case(), packet)

    assert result.verdict == "needs_semantic_review"
    assert "routing.unexpected_specialist:research_analysis" in result.unexecuted_assertions


def test_declared_extra_specialist_is_not_flagged() -> None:
    case = _case(allowed_extras=["research_analysis"])
    packet = _packet(tools=["load_skill", "market_data_analysis", "research_analysis"])

    result = judge_packet(case, packet)

    assert result.verdict == "pass"


def test_allowed_extra_calls_still_count_against_specialist_ceiling() -> None:
    case = _case(
        expected_skills=[],
        allowed_extras=["research_analysis"],
        cost={"max_specialist_calls": 2},
    )
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"price": 210},
            "error_info": None,
        },
        {
            "name": "research_analysis",
            "output": {"summary": "first pass"},
            "error_info": None,
        },
        {
            "name": "research_analysis",
            "output": {"summary": "duplicate pass"},
            "error_info": None,
        },
    ]

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("specialist call ceiling" in failure for failure in result.checks_failed)


def test_specialist_ceiling_requires_authoritative_span_evidence() -> None:
    case = _case(cost={"max_specialist_calls": 1})

    result = judge_packet(case, _packet())

    assert result.verdict == "inconclusive_missing_evidence"
    assert "specialist_call_count:initial" in result.missing_evidence


def test_specialist_ceiling_ignores_nested_non_outer_spans() -> None:
    case = _case(
        expected_skills=[],
        cost={"max_specialist_calls": 1},
    )
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"price": 210},
            "error_info": None,
        },
        {"name": "llm", "type": "llm", "output": {"response": "nested"}},
        {"name": "provider_http_request", "output": {"status": 200}},
    ]

    result = judge_packet(case, packet)

    assert result.verdict == "pass"


def test_timeout_is_harness_inconclusive() -> None:
    packet = _packet()
    packet["cli"]["timed_out"] = True

    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_harness"


def test_async_case_without_follow_up_is_inconclusive_by_default() -> None:
    # An async-required case with no follow-up evidence must never be scored as
    # a pass: the initial stub is not an answer.
    case = _case(
        expect_async_job=True,
        expected_tools=[],
        expected_skills=[],
        assertions={"required_text": ["walk-forward"]},
    )

    result = judge_packet(case, _packet("Synchronous walk-forward result.", tools=[]))

    assert result.verdict == "inconclusive_harness"


def test_async_job_optional_accepts_synchronous_completion() -> None:
    # When a job may complete synchronously, a complete in-band answer is a real
    # result and is judged against the response, not treated as missing async
    # evidence.
    case = _case(
        expect_async_job=True,
        async_job_optional=True,
        expected_tools=[],
        expected_skills=[],
        assertions={"required_text": ["walk-forward"]},
    )

    result = judge_packet(case, _packet("Synchronous walk-forward result.", tools=[]))

    assert result.verdict != "inconclusive_harness"
    assert result.observed_outcome == "success"


def test_forbidden_skill_is_product_failure() -> None:
    case = _case(expected_skills_absent=["obai-stock-synthesis"])
    packet = _packet(
        curated=(
            "--- SKILL LOADS (2) ---\n"
            "- obai-market-data-routing: loaded\n"
            "- obai-stock-synthesis: loaded"
        )
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"


def test_unexecuted_symbolic_claim_requires_semantic_review() -> None:
    case = _case(assertions={"required_claims": ["evidence_vs_inference"]})

    result = judge_packet(case, _packet())

    assert result.verdict == "needs_semantic_review"
    assert "required_claims:evidence_vs_inference" in result.unexecuted_assertions


def test_structured_claim_evidence_executes_symbolic_claim() -> None:
    case = _case(
        expected_tools=[],
        expected_skills=[],
        assertions={
            "required_claims": ["job_id"],
            "forbidden_claims": ["artifact_exported"],
        },
    )
    packet = _packet(tools=[])
    packet["evidence"] = {"claims": {"job_id": True, "artifact_exported": False}}

    result = judge_packet(case, packet)

    assert result.verdict == "pass"


def test_registered_forbidden_claim_catches_artifact_export() -> None:
    case = _case(
        expected_tools=[],
        expected_skills=[],
        assertions={"forbidden_claims": ["artifact_exported"]},
    )

    result = judge_packet(
        case, _packet("The paper-ledger artifact was exported successfully.", tools=[])
    )

    assert result.verdict == "fail_product"


def test_forbidden_call_is_checked_from_tool_evidence() -> None:
    case = _case(
        expected_tools=[],
        expected_skills=[],
        assertions={"forbidden_calls": ["crypto_strategy_export_artifact"]},
    )
    result = judge_packet(
        case,
        _packet(tools=["crypto_analysis", "crypto_strategy_export_artifact"]),
    )

    assert result.verdict == "fail_product"


def test_numeric_checker_without_structured_result_is_missing_evidence() -> None:
    case = _case(
        expected_tools=[],
        expected_skills=[],
        assertions={"numeric_checker": "collar_payoff", "numeric_tolerance": {"usd": 1.0}},
    )

    result = judge_packet(case, _packet(tools=[]))

    assert result.verdict == "inconclusive_missing_evidence"


def test_data_unavailable_does_not_convert_403_to_expected_pass() -> None:
    case = _case(
        expected_outcome="data_unavailable",
        expected_tools=[],
        expected_skills=[],
        assertions={"required_text": ["unavailable"]},
    )

    result = judge_packet(
        case, _packet("Data unavailable: provider returned 403 permission denied.", tools=[])
    )

    assert result.verdict == "inconclusive_provider"


def test_expected_hub_reject_can_use_cli_exit_one() -> None:
    case = _case(
        expected_outcome="hub_reject",
        expected_tools=[],
        expected_skills=[],
        assertions={"required_text": ["financial questions"]},
    )
    packet = _packet("I can only help with financial questions.", tools=[])
    packet["cli"]["exit_code"] = 1
    packet["cli"]["stdout_json"]["guardrail_rejected"] = True

    result = judge_packet(case, packet)

    assert result.verdict == "pass_degraded"


def test_raw_empty_spans_cannot_fall_back_to_cli_tool_claims() -> None:
    packet = _packet()
    packet["trace"]["spans"] = []

    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_missing_evidence"
    assert result.observed_tools == []


@pytest.mark.parametrize(
    "message",
    [
        "provider exploded",
        "Connection error.",
        "Incorrect API key provided",
        "Error code: 404 - The model gpt-X does not exist",
        "insufficient_quota",
        "Internal Server Error",
        "Too Many Requests",
    ],
)
def test_provider_error_in_expected_raw_specialist_span_is_inconclusive(message: str) -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "input": {"symbol": "AAPL"},
            "output": None,
            "error_info": {"message": message},
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_provider"


def test_nonprovider_error_in_expected_span_is_product_failure_for_success() -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": None,
            "error_info": {"message": "calculation crashed"},
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "fail_product"


@pytest.mark.parametrize(
    "output",
    [
        {"isError": True, "message": "specialist calculation exploded"},
        '{"isError": true, "message": "specialist calculation exploded"}',
        {"status": "failed", "message": "specialist calculation exploded"},
    ],
)
def test_explicit_structured_specialist_error_output_is_product_failure(
    output: object,
) -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": output,
            "error_info": None,
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
            "error_info": None,
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "fail_product"
    assert any("structured_output" in failure for failure in result.checks_failed)


def test_provider_structured_specialist_error_output_is_inconclusive() -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {
                "isError": True,
                "message": "provider returned 403 permission denied",
            },
            "error_info": None,
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
            "error_info": None,
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_provider"


@pytest.mark.parametrize(
    "output",
    [
        {"isError": False, "error": "tracking error was 2%"},
        {"error": "tracking error was 2%", "value": 210},
        "The historical tracking error was 2%.",
    ],
)
def test_non_error_output_is_not_rejected_for_error_words_or_keys(output: object) -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": output,
            "error_info": None,
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
            "error_info": None,
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "pass"


def test_nonprovider_error_in_optional_specialist_is_also_product_failure() -> None:
    case = _case(allowed_extras=["research_analysis"])
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"price": 210},
            "error_info": None,
        },
        {
            "name": "research_analysis",
            "output": None,
            "error_info": {"message": "citation validation crashed"},
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
        },
    ]

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"


def test_nonprovider_error_in_required_skill_load_is_product_failure() -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"price": 210},
            "error_info": None,
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": None,
            "error_info": {"message": "skill loading crashed"},
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "fail_product"
    assert any("required skill load failed" in failure for failure in result.checks_failed)


def test_provider_error_in_required_skill_load_is_inconclusive() -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"price": 210},
            "error_info": None,
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": None,
            "error_info": {"message": "provider returned 403 permission denied"},
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_provider"


def test_explicit_structured_required_skill_error_output_is_product_failure() -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"price": 210},
            "error_info": None,
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"isError": True, "message": "skill loading exploded"},
            "error_info": None,
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "fail_product"
    assert any("structured_output" in failure for failure in result.checks_failed)


def test_empty_required_skill_load_output_does_not_count_as_loaded() -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"price": 210},
            "error_info": None,
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": None,
            "error_info": None,
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_missing_evidence"
    assert "skill_output:obai-market-data-routing" in result.missing_evidence


def test_recovered_nonprovider_hub_error_is_not_a_blanket_failure() -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"price": 210},
            "error_info": None,
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
            "error_info": None,
        },
        {
            "name": "obai_hub",
            "output": None,
            "error_info": {"message": "routing attempt crashed before retry"},
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "pass"


def test_empty_expected_specialist_output_is_missing_evidence() -> None:
    packet = _packet()
    packet["trace"]["spans"] = [
        {"name": "market_data_analysis", "output": None, "error_info": None},
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_missing_evidence"


def _async_packet(status: str, final_response: str, *, job_id: str = "job_1") -> dict:
    packet = _packet("Job ID: job_1\nStatus: queued")
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"job_id": "job_1", "status": "queued"},
            "error_info": None,
        }
    ]
    correlated_response = f"{final_response}\nJob ID: {job_id}"
    packet["followup"] = {
        "job_id": job_id,
        "status": status,
        "timed_out": status == "pending",
        "poll_limit_reached": status == "pending",
        "evidence_complete": True,
        "polls": [
            {
                "query": f"Check job {job_id}",
                "status": status,
                "final_response": correlated_response,
                "cli": {
                    "exit_code": 0,
                    "timed_out": False,
                    "stdout_json": {"response": correlated_response, "tool_calls": []},
                    "stderr": "",
                },
                "trace": {
                    "id": "followup-trace",
                    "spans": [
                        {
                            "name": "market_data_analysis",
                            "output": {"answer": correlated_response},
                            "error_info": None,
                        }
                    ],
                },
            }
        ],
    }
    return packet


def test_async_pending_or_poll_limit_is_harness_inconclusive() -> None:
    case = _case(expect_async_job=True, expected_skills=[])

    result = judge_packet(case, _async_packet("pending", "Status: pending"))

    assert result.verdict == "inconclusive_harness"


def test_async_failed_job_is_product_failure_for_success_case() -> None:
    case = _case(expect_async_job=True, expected_skills=[])

    result = judge_packet(case, _async_packet("failed", "Status: failed; calculation error."))

    assert result.verdict == "fail_product"


def test_async_failed_job_can_match_explicit_specialist_error_contract() -> None:
    case = _case(
        expect_async_job=True,
        expected_outcome="specialist_error",
        expected_skills=[],
        assertions={"required_text": ["calculation error"]},
    )

    result = judge_packet(case, _async_packet("failed", "Status: failed; calculation error."))

    assert result.verdict == "pass_degraded"


def test_async_completed_uses_last_poll_response_and_raw_trace() -> None:
    case = _case(
        expect_async_job=True,
        expected_skills=[],
        assertions={"required_text": ["final result"]},
    )

    result = judge_packet(
        case, _async_packet("completed", "Status: completed. Final result as of now.")
    )

    assert result.verdict == "pass"
    assert any("required_text present" in check for check in result.checks_passed)


def test_async_completed_poll_cannot_hide_initial_cli_failure() -> None:
    """A successful poll is not evidence that the paid root turn completed."""
    case = _case(
        expect_async_job=True,
        expected_skills=[],
        assertions={"required_text": ["final result"]},
    )
    packet = _async_packet("completed", "Status: completed. Final result as of now.")
    packet["harness_status"] = "cli_failed"
    packet["harness_exit_code"] = 3
    packet["cli"]["exit_code"] = 3

    result = judge_packet(case, packet)

    assert result.verdict == "inconclusive_harness"
    assert result.reason is not None
    assert "initial async turn" in result.reason


def test_async_initial_turn_must_dispatch_expected_specialist() -> None:
    case = _case(
        expect_async_job=True,
        expected_skills=[],
        assertions={"required_text": ["final result"]},
    )
    packet = _async_packet("completed", "Status: completed. Final result as of now.")
    packet["trace"]["spans"] = [
        {"name": "obai_hub", "output": {"job_id": "job_1"}, "error_info": None}
    ]

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("initial expected specialist missing" in failure for failure in result.checks_failed)


def test_async_initial_turn_must_successfully_load_required_skill() -> None:
    case = _case(
        expect_async_job=True,
        assertions={"required_text": ["final result"]},
    )
    packet = _async_packet("completed", "Status: completed. Final result as of now.")
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"job_id": "job_1"},
            "error_info": None,
        }
    ]
    packet["followup"]["polls"][0]["trace"]["spans"].append(
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
            "error_info": None,
        }
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("initial required skill missing" in failure for failure in result.checks_failed)


def test_async_intermediate_poll_must_dispatch_expected_specialist() -> None:
    case = _case(
        expect_async_job=True,
        expected_skills=[],
        assertions={"required_text": ["final result"]},
    )
    packet = _async_packet("completed", "Status: completed. Final result as of now.")
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"job_id": "job_1"},
            "error_info": None,
        }
    ]
    packet["followup"]["polls"].insert(
        0,
        {
            "query": "Check job job_1",
            "status": "running",
            "final_response": "Status: running\nJob ID: job_1",
            "cli": {
                "exit_code": 0,
                "timed_out": False,
                "stdout_json": {"response": "Status: running\nJob ID: job_1"},
                "stderr": "",
            },
            "trace": {
                "id": "running-trace",
                "spans": [
                    {"name": "obai_hub", "output": {"status": "running"}, "error_info": None}
                ],
            },
        },
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("poll[0] expected specialist missing" in failure for failure in result.checks_failed)


def test_async_intermediate_poll_must_successfully_load_required_skill() -> None:
    case = _case(
        expect_async_job=True,
        assertions={"required_text": ["final result"]},
    )
    packet = _async_packet("completed", "Status: completed. Final result as of now.")
    successful_load = {
        "name": "load_skill",
        "input": {"skill_name": "obai-market-data-routing"},
        "output": {"status": "loaded"},
        "error_info": None,
    }
    packet["trace"]["spans"].append(dict(successful_load))
    packet["followup"]["polls"][0]["trace"]["spans"].append(dict(successful_load))
    packet["followup"]["polls"].insert(
        0,
        {
            "query": "Check job job_1",
            "status": "running",
            "final_response": "Status: running\nJob ID: job_1",
            "cli": {
                "exit_code": 0,
                "timed_out": False,
                "stdout_json": {"response": "Status: running\nJob ID: job_1"},
                "stderr": "",
            },
            "trace": {
                "id": "running-trace",
                "spans": [
                    {
                        "name": "market_data_analysis",
                        "output": {"status": "running"},
                        "error_info": None,
                    }
                ],
            },
        },
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("poll[0] required skill missing" in failure for failure in result.checks_failed)


def test_async_initial_specialist_error_cannot_be_hidden_by_final_poll() -> None:
    case = _case(
        expect_async_job=True,
        expected_skills=[],
        assertions={"required_text": ["final result"]},
    )
    packet = _async_packet("completed", "Status: completed. Final result as of now.")
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": None,
            "error_info": {"message": "initial specialist crashed"},
        }
    ]

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("initial specialist span failed" in failure for failure in result.checks_failed)


def test_async_intermediate_unexpected_specialist_error_is_product_failure() -> None:
    case = _case(
        expect_async_job=True,
        expected_skills=[],
        assertions={"required_text": ["final result"]},
    )
    packet = _async_packet("completed", "Status: completed. Final result as of now.")
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"job_id": "job_1"},
            "error_info": None,
        }
    ]
    packet["followup"]["polls"].insert(
        0,
        {
            "query": "Check job job_1",
            "status": "running",
            "final_response": "Status: running\nJob ID: job_1",
            "cli": {
                "exit_code": 0,
                "timed_out": False,
                "stdout_json": {"response": "Status: running\nJob ID: job_1"},
                "stderr": "",
            },
            "trace": {
                "id": "running-trace",
                "spans": [
                    {
                        "name": "market_data_analysis",
                        "output": {"status": "running"},
                        "error_info": None,
                    },
                    {
                        "name": "research_analysis",
                        "output": None,
                        "error_info": {"message": "unexpected research crashed"},
                    },
                ],
            },
        },
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("poll[0] specialist span failed" in failure for failure in result.checks_failed)


def test_async_specialist_ceiling_is_enforced_per_turn_not_cumulatively() -> None:
    case = _case(
        expect_async_job=True,
        expected_skills=[],
        cost={"max_specialist_calls": 1},
        assertions={"required_text": ["final result"]},
    )
    packet = _async_packet("completed", "Status: completed. Final result as of now.")
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"job_id": "job_1"},
            "error_info": None,
        }
    ]

    result = judge_packet(case, packet)

    assert result.verdict == "pass"
    assert any("initial: observed 1, max 1" in check for check in result.checks_passed)
    assert any("poll[0]: observed 1, max 1" in check for check in result.checks_passed)


def test_async_specialist_ceiling_detects_poll_overrun() -> None:
    case = _case(
        expect_async_job=True,
        expected_skills=[],
        cost={"max_specialist_calls": 1},
        assertions={"required_text": ["final result"]},
    )
    packet = _async_packet("completed", "Status: completed. Final result as of now.")
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {"job_id": "job_1"},
            "error_info": None,
        }
    ]
    packet["followup"]["polls"][0]["trace"]["spans"].append(
        {
            "name": "market_data_analysis",
            "output": {"duplicate": True},
            "error_info": None,
        }
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("poll[0]: observed 2, max 1" in failure for failure in result.checks_failed)


def test_async_job_id_mismatch_is_harness_inconclusive() -> None:
    case = _case(expect_async_job=True, expected_skills=[])

    result = judge_packet(
        case,
        _async_packet("completed", "Status: completed. Final result as of now.", job_id="job_2"),
    )

    assert result.verdict == "inconclusive_harness"


def test_async_completed_response_without_job_id_is_harness_inconclusive() -> None:
    case = _case(expect_async_job=True, expected_skills=[])
    packet = _async_packet("completed", "Status: completed. Final result.")
    poll = packet["followup"]["polls"][0]
    poll["final_response"] = "Status: completed. Final result."
    poll["cli"]["stdout_json"]["response"] = poll["final_response"]

    result = judge_packet(case, packet)

    assert result.verdict == "inconclusive_harness"


def test_hub_reject_uses_structured_error_message_when_response_is_empty() -> None:
    case = _case(
        expected_outcome="hub_reject",
        expected_tools=[],
        expected_skills=[],
        assertions={"required_text": ["financial questions"]},
    )
    packet = _packet("", tools=[])
    packet["cli"]["exit_code"] = 1
    packet["cli"]["stdout_json"] = {
        "response": "",
        "error": {"message": "I can only help with financial questions."},
        "guardrail_rejected": True,
        "tool_calls": [],
    }

    result = judge_packet(case, packet)

    assert result.verdict == "pass_degraded"
