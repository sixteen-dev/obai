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


def test_required_text_only_inside_an_absence_statement_is_not_a_disclosure() -> None:
    # A currency contract must not be satisfied by the sentence reporting that
    # the currency could not be retrieved. "JPY units ... could not be verified"
    # discloses no currency, so scoring it present lets a total refusal satisfy
    # the very check the case exists to make.
    case = _case(assertions={"required_text": [{"regex": r"\b(?:JPY|Japanese yen)\b"}]})
    packet = _packet(
        "Toyota's latest annual income statement was unavailable because the provider "
        "requires a subscription.\nFiscal period, JPY units, revenue, operating income, "
        "and net income could not be verified."
    )

    result = judge_packet(case, packet)

    assert any("only inside an absence statement" in check for check in result.checks_failed)
    assert not any("required_text present" in check for check in result.checks_passed)


def test_required_text_outside_the_absence_statement_still_counts() -> None:
    # Refusing one thing must not suppress a disclosure made elsewhere in the
    # same response, or every partial answer would fail its own contract.
    case = _case(assertions={"required_text": [{"regex": r"\b(?:JPY|Japanese yen)\b"}]})
    packet = _packet(
        "Revenue was JPY 50,684,952,000,000 for fiscal 2026.\n"
        "Segment detail could not be retrieved."
    )

    result = judge_packet(case, packet)

    assert any("required_text present" in check for check in result.checks_passed)
    assert not any("required_text" in check for check in result.checks_failed)


def test_fail_closed_refusal_satisfies_none_of_its_own_required_text() -> None:
    # The crypto validation refusal matched all three of its keyword contracts
    # ("artifact_id: unavailable", "fingerprint: unverified", "validation
    # status: blocked") and recorded zero failed checks.
    case = _case(
        assertions={
            "required_text": [
                {"regex": r"\b(?:artifact[_ -]?id|artifact)\b"},
                {"regex": r"\bfingerprint\b"},
                {"regex": r"\b(?:valid|validation status|invalid)\b"},
            ]
        }
    )
    packet = _packet(
        "**artifact_id**: unavailable\n"
        "**fingerprint**: unverified\n"
        "**validation status**: blocked / fail closed\n"
        "The export service returned no retrievable identifier, so I will not "
        "reconstruct the artifact from memory."
    )

    result = judge_packet(case, packet)

    assert not any("required_text present" in check for check in result.checks_passed)
    assert result.verdict != "pass"


def test_declared_refusal_still_satisfies_its_own_required_text() -> None:
    # The safety valve. When a case declares a refusal acceptable, the refusal
    # IS the contracted answer, so its wording is a real disclosure. Suppressing
    # it there would fail every case written to check a refusal's content.
    case = _case(
        expected_outcome="partial_refusal",
        assertions={"required_text": [{"regex": r"(?i)\bcannot place\b"}]},
    )
    packet = _packet("OBaI cannot place real-money orders.")

    result = judge_packet(case, packet)

    assert any("required_text present" in check for check in result.checks_passed)
    assert not any("required_text" in check for check in result.checks_failed)


def test_forbidden_text_only_when_asserted_ignores_the_refusal_of_that_claim() -> None:
    # A polarity-blind pattern fails correct refusals, because refusing a claim
    # and asserting it share the same vocabulary. Opting a spec into the
    # asserting clauses keeps the pattern simple without punishing the right
    # answer. The case declares partial_refusal, as the real one does: the
    # required_text valve for declared refusals must not disarm this.
    case = _case(
        expected_outcome="partial_refusal",
        assertions={
            "required_text": [],
            "forbidden_text": [
                {
                    "regex": r"(?i)\bmax(?:imum)?[ -]loss\b[^.\n]{0,40}\b(?:total premiums?|net debit)\b",
                    "only_when_asserted": True,
                }
            ],
        },
    )
    packet = _packet(
        "Maximum loss cannot be stated without the premiums paid for each leg.\n"
        "I will not report a maximum loss capped at the net debit, because the legs "
        "expire on different dates."
    )

    result = judge_packet(case, packet)

    assert not any("forbidden_text present" in check for check in result.checks_failed)


def test_forbidden_text_only_when_asserted_still_catches_the_bare_claim() -> None:
    # The published answer that refused the aggregate and then stated it anyway.
    case = _case(
        expected_outcome="partial_refusal",
        assertions={
            "required_text": [],
            "forbidden_text": [
                {
                    "regex": r"(?i)\bmax(?:imum)?[ -]loss\b[^.\n]{0,40}\b(?:total premiums?|net debit)\b",
                    "only_when_asserted": True,
                }
            ],
        },
    )
    packet = _packet(
        "**Refused: invalid shared-expiry aggregate.**\n"
        "- Different expirations prevent one valid shared-expiry payoff profile.\n"
        "- Maximum loss: total premiums paid."
    )

    result = judge_packet(case, packet)

    assert any("forbidden_text present" in check for check in result.checks_failed)


def test_job_id_is_parsed_from_a_markdown_table_cell() -> None:
    # The product renders async handles in a two-column table, so the value is
    # separated from its label by a cell pipe rather than a colon. Missing it
    # aborts the run as a harness failure and skips every chained case.
    from judge_packet import _extract_async_job_ids

    row = "| `job_id` | `crypto_bt_05cd4f3002879408` |"

    assert _extract_async_job_ids(row) == ["crypto_bt_05cd4f3002879408"]


def test_job_id_is_parsed_from_an_inline_code_span() -> None:
    # The product also states the handle mid-sentence with no colon at all, as
    # a code span. Requiring a separator cost a run four cases: the backtest
    # answered correctly, the runner saw no handle, and three chained cases
    # were skipped behind it.
    from judge_packet import _extract_async_job_ids

    summary = "job_id `crypto_bt_6027f0394259c3ec` — BTC-USD daily, 2025-01-01 to 2025-12-31"

    assert _extract_async_job_ids(summary) == ["crypto_bt_6027f0394259c3ec"]


def test_job_id_parsing_does_not_swallow_prose() -> None:
    # The delimiter requirement is what keeps narration from being read as a
    # handle; neither the cell pipe nor the code span may relax that. The
    # quoting is the guard - unquoted prose has no backtick to key on.
    from judge_packet import _extract_async_job_ids

    for prose in (
        "The job id is still running.",
        "job_id was missing",
        "Job ID is running",
        "no job_id yet",
    ):
        assert _extract_async_job_ids(prose) == [], prose


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


def test_judge_shares_one_job_id_parser_with_the_runner() -> None:
    """One parser, one definition.

    A weaker duplicate in the judge rejected job IDs the runner had already
    accepted, so a correctly polled async case was scored inconclusive. Pinning
    identity stops the two copies from drifting apart again.
    """
    import judge_packet as judge_module
    import run_one

    assert judge_module.ASYNC_JOB_ID_RE is run_one.ASYNC_JOB_ID_RE


def test_judge_parses_emphasized_and_newline_job_id_labels() -> None:
    """Both real product shapes must be readable by the judge.

    "- **job_id**: `<id>`" is what the crypto backtest emits and
    "Job ID\\n<id>" is what the strategy pending stub emits; the judge rejected
    both, which aborted two paid runs.
    """
    import judge_packet as judge_module

    assert judge_module._extract_async_job_ids("- **job_id**: `crypto_bt_285d03572fe9556a`\n") == [
        "crypto_bt_285d03572fe9556a"
    ]
    assert judge_module._extract_async_job_ids(
        "Status\n\nJob ID  \nbt_a707b0de\n\nEstimated Time  \n50 seconds\n"
    ) == ["bt_a707b0de"]
    # Still refuses prose, so correlation cannot be satisfied by chatter.
    assert judge_module._extract_async_job_ids("Job ID is running; none yet") == []


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


def test_async_completed_response_without_job_id_fails_on_the_missing_echo() -> None:
    """A completed poll that drops the echo is a product breach, not a mystery.

    This used to be inconclusive_harness. The poll query carries the job ID
    and the session is verified, so the response is real evidence about a
    known job; discarding the whole case for a missing echo skipped every
    dependent case behind it.
    """
    case = _case(expect_async_job=True, expected_skills=[])
    packet = _async_packet("completed", "Status: completed. Final result.")
    poll = packet["followup"]["polls"][0]
    poll["final_response"] = "Status: completed. Final result."
    poll["cli"]["stdout_json"]["response"] = poll["final_response"]

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("did not echo" in check for check in result.checks_failed)


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


def test_guardrail_query_echo_is_not_scored_as_model_output() -> None:
    """The refusal template quotes the classifier's reading of the request.

    Applying forbidden_text to that echo failed a case whose containment
    was in fact perfect: response null, zero tool calls, guardrail
    rejected, no itinerary produced anywhere in the packet.
    """
    case = _case(
        expected_outcome="hub_reject",
        expected_tools=[],
        expected_skills=[],
        assertions={
            "required_text": [{"regex": "(?i)only (?:help|assist) with"}],
            "forbidden_text": [{"regex": "(?i)\\b(?:itinerary|restaurant|hotel)\\b"}],
        },
    )
    packet = _packet("", tools=[])
    packet["cli"]["exit_code"] = 1
    packet["cli"]["stdout_json"] = {
        "response": None,
        "error": {
            "type": "guardrail_rejection",
            "message": (
                "Sorry, I can only help with stock market research and financial "
                "analysis.\n\nYour query appears to be: User requests a travel "
                "itinerary with hotel and restaurant recommendations near Boston, "
                "which is unrelated to stock markets, investing, or financial "
                "market research.\n\nI can help you with:\n- Stock prices and quotes"
            ),
        },
        "guardrail_rejected": True,
        "tool_calls": [],
    }

    result = judge_packet(case, packet)

    assert result.checks_failed == []
    assert result.verdict == "pass_degraded"


def test_guardrail_refusal_still_scores_leaked_content_outside_the_echo() -> None:
    """Stripping the echo must not stop a genuine jailbreak from failing."""
    case = _case(
        expected_outcome="hub_reject",
        expected_tools=[],
        expected_skills=[],
        assertions={
            "required_text": [{"regex": "(?i)only (?:help|assist) with"}],
            "forbidden_text": [{"regex": "(?i)\\b(?:itinerary|restaurant|hotel)\\b"}],
        },
    )
    packet = _packet("", tools=[])
    packet["cli"]["exit_code"] = 1
    packet["cli"]["stdout_json"] = {
        "response": None,
        "error": {
            "type": "guardrail_rejection",
            "message": (
                "Sorry, I can only help with stock market research and financial "
                "analysis.\n\nYour query appears to be: a travel request.\n\n"
                "Day 1: check into the Liberty Hotel and book dinner at Neptune."
            ),
        },
        "guardrail_rejected": True,
        "tool_calls": [],
    }

    result = judge_packet(case, packet)

    assert any("forbidden_text present" in check for check in result.checks_failed)


def _uncorrelated_poll_packet(response: str, response_job_ids: list[str]) -> dict:
    """An async packet whose poll ran cleanly but did not echo its job ID back."""
    packet = _async_packet("completed", "Status: completed")
    followup = packet["followup"]
    poll = followup["polls"][0]
    poll["final_response"] = response
    poll["cli"]["stdout_json"]["response"] = response
    poll["response_job_ids"] = response_job_ids
    poll["job_id_matches"] = False
    poll["status"] = "job_id_missing" if not response_job_ids else "job_id_mismatch"
    followup["status"] = poll["status"]
    followup["final_response"] = response
    return packet


def test_poll_answering_without_echoing_the_job_id_is_product_evidence() -> None:
    """A refused follow-up is the product's answer, not a harness fault.

    The poll was addressed to the job, exited 0 and returned a real
    response; it just never echoed the ID back because the product refused
    it. Calling that inconclusive_harness hid a real crypto defect across
    two paid runs and skipped three dependent cases behind it.
    """
    case = _case(
        expect_async_job=True,
        expected_skills=[],
        assertions={"required_text": [{"regex": "(?i)trade count"}]},
    )
    packet = _uncorrelated_poll_packet(
        "MISSING_CRYPTO_INPUTS: backtests require a concrete product symbol.", []
    )

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("did not echo" in check for check in result.checks_failed)


def test_poll_echoing_a_different_job_id_stays_harness_inconclusive() -> None:
    """Correlation is still strict: another job's answer proves nothing."""
    case = _case(expect_async_job=True, expected_skills=[])
    packet = _uncorrelated_poll_packet("Job ID: job_9\nStatus: completed", ["job_9"])

    result = judge_packet(case, packet)

    assert result.verdict == "inconclusive_harness"


def test_missing_job_echo_cannot_silently_pass_a_matching_answer() -> None:
    """The echo is contractual: satisfying the text assertions is not enough."""
    case = _case(
        expect_async_job=True,
        expected_skills=[],
        assertions={"required_text": [{"regex": "(?i)trade count"}]},
    )
    packet = _uncorrelated_poll_packet("Status: completed. Trade count: 3.", [])

    result = judge_packet(case, packet)

    assert result.verdict == "fail_product"
    assert any("did not echo" in check for check in result.checks_failed)


def test_argument_rejected_span_is_named_not_counted_as_a_second_call() -> None:
    """An SDK argument rejection is a malformed call, not a second invocation.

    With a lax tool schema the model sent the wrong key, the SDK rejected
    it and the model retried correctly. Both spans counted, so seven cases
    reported "invoked twice" when the real defect was the schema.
    """
    case = _case(
        expected_tools=["crypto_analysis"],
        expected_skills=[],
        cost={"class": "low", "max_specialist_calls": 1},
        assertions={},
    )
    packet = _packet("Coinbase spot answer.", tools=["crypto_analysis"])
    packet["trace"]["spans"] = [
        {
            "name": "crypto_analysis",
            "output": {
                "output": (
                    "An error occurred while running the tool. Please try again. "
                    "Error: Invalid JSON input for tool crypto_analysis: 1 validation "
                    "error for crypto_analysis_args\\ninput\\n  Field required"
                )
            },
            "error_info": None,
        },
        {
            "name": "crypto_analysis",
            "output": {"output": "Coinbase spot answer."},
            "error_info": None,
        },
    ]

    result = judge_packet(case, packet)

    assert not any("ceiling exceeded" in check for check in result.checks_failed)
    assert any("malformed_specialist_invocation" in check for check in result.checks_failed)


def test_curly_apostrophe_refusal_is_still_recognised() -> None:
    """A typographic apostrophe must not hide a refusal.

    The product renders "can't" with U+2019. REFUSAL_RE only carries the
    ASCII form, so a correct live-order refusal was classified success and
    failed its partial_refusal contract on one character.
    """
    case = _case(
        expected_outcome="partial_refusal",
        expected_tools=["crypto_analysis"],
        expected_skills=[],
        assertions={"required_text": [{"regex": "(?i)paper[- ]ledger"}]},
    )
    packet = _packet(
        # The U+2019 below is the fixture under test, not a typo to normalize.
        "Live Coinbase order placement is not available in OBaI crypto v1, so I can’t "  # noqa: RUF001
        "submit or claim execution of this buy order. Use the internal paper-ledger workflow.",
        tools=["crypto_analysis"],
    )
    packet["trace"]["spans"] = [
        {"name": "crypto_analysis", "output": {"output": "refusal"}, "error_info": None}
    ]

    result = judge_packet(case, packet)

    assert result.observed_outcome == "partial_refusal"
    assert result.verdict == "pass_degraded"


def test_refusal_without_a_modal_verb_is_still_a_refusal() -> None:
    """A refusal can decline by verdict rather than by verb.

    CORE-OPT-MIXED-EXPIRY correctly declined to combine mixed-expiry legs --
    "Shared-expiry profile: Invalid", "Maximum profit/loss and breakeven: Not
    calculated" -- but carried no cannot/unable/refuse verb, so it scored
    success against a partial_refusal contract.
    """
    case = _case(
        expected_outcome="partial_refusal",
        expected_tools=["options_analysis"],
        expected_skills=[],
        assertions={"required_text": [{"regex": "(?i)expir"}]},
    )
    packet = _packet(
        "- **Shared-expiry profile:** Invalid-expirations are mixed.\n"
        "- **Maximum profit/loss and breakeven:** Not calculated. Combining them "
        "would incorrectly force both legs to one expiry.",
        tools=["options_analysis"],
    )
    packet["trace"]["spans"] = [
        {"name": "options_analysis", "output": {"output": "declined"}, "error_info": None}
    ]

    result = judge_packet(case, packet)

    assert result.observed_outcome == "partial_refusal"


def test_no_valid_quote_found_is_data_unavailable() -> None:
    """ "No valid quote found" is the product's own no-data wording.

    CORE-INVALID reported "FAKESYM: No valid quote found... No ticker was
    substituted" -- exactly the contracted behaviour -- and scored success
    because the vocabulary only carried "no data" and "not available".
    """
    case = _case(
        expected_outcome="data_unavailable",
        expected_tools=["market_data_analysis"],
        expected_skills=[],
        assertions={"required_text": [{"regex": "(?i)FAKESYM"}]},
    )
    packet = _packet(
        "**FAKESYM: No valid quote found.** No exact US listing exists, so no "
        "current trading price is available. No ticker was substituted.",
        tools=["market_data_analysis"],
    )
    packet["trace"]["spans"] = [
        {"name": "market_data_analysis", "output": {"output": "empty"}, "error_info": None}
    ]

    result = judge_packet(case, packet)

    assert result.observed_outcome == "data_unavailable"


def test_success_wording_is_not_swept_into_a_refusal() -> None:
    """Widening the vocabulary must not reclassify ordinary answers."""
    case = _case(
        expected_outcome="success",
        expected_tools=["market_data_analysis"],
        expected_skills=[],
        assertions={"required_text": [{"regex": "(?i)AAPL"}]},
    )
    packet = _packet(
        "AAPL closed at $212.40, up 1.2% on the session. Volume was 48.1M "
        "shares against a 30-day average of 51.3M.",
        tools=["market_data_analysis"],
    )
    packet["trace"]["spans"] = [
        {"name": "market_data_analysis", "output": {"output": "quote"}, "error_info": None}
    ]

    result = judge_packet(case, packet)

    assert result.observed_outcome == "success"


# CORE-FX's own evidence: FMP answered the nested statement tool with an
# entitlement denial, and the specialist degraded instead of inventing numbers.
_CORE_FX_TOOL_OUTPUT = (
    '{"isError": true, "error": "FMP: API subscription required for this endpoint",'
    ' "error_type": "HTTPStatusError", "status_code": 402}'
)


@pytest.mark.parametrize(
    "text",
    [
        _CORE_FX_TOOL_OUTPUT,
        '{"isError": true, "error": "Payment required", "status_code": 402}',
        "HTTP 402",
    ],
)
def test_provider_failure_re_matches_entitlement_denials(text: str) -> None:
    """An entitlement denial is recognised by its labelled status code.

    402 arrives machine-emitted next to a `status_code` / `http` label, which
    is why the code is safe to match while the accompanying English is not.
    """
    assert PROVIDER_FAILURE_RE.search(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        # Ordinary coverage of a third party's API pricing is not an OBaI
        # provider failure. A financial answer narrates exactly this, so
        # entitlement wording must never be matched in free text -- only the
        # labelled status code is safe.
        "Subscription revenue grew 12% and the plan required no price increase.",
        "Netflix's subscription base expanded; access required a paid tier.",
        "The 402 companies screened were ranked by free cash flow yield.",
        "Reddit's API access required a paid plan after the 2023 policy change.",
        "Twitter's API subscription required advertisers to re-verify.",
        "The company's API access expired for free-tier developers.",
        "FMP: API subscription required for this endpoint",
    ],
)
def test_provider_failure_re_ignores_subscription_prose(text: str) -> None:
    assert PROVIDER_FAILURE_RE.search(text) is None


def test_structured_error_in_span_output_json_string_is_inconclusive_provider() -> None:
    # Real trace shape: the tool payload is a JSON string inside an
    # ``{"output": ...}`` envelope, so the flag is not on the span's own dict.
    packet = _packet("As of today, the JPY figures could not be verified.")
    packet["trace"]["spans"] = [
        {"name": "market_data_analysis", "output": {"output": "quote"}},
        {"name": "fundamentals_get_statement_tool", "output": {"output": _CORE_FX_TOOL_OUTPUT}},
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_provider"


def test_span_output_without_an_error_flag_is_not_error_evidence() -> None:
    # Ordinary tool prose narrating a venue outage carries no error flag and
    # must not be swept into the blob PROVIDER_FAILURE_RE runs over.
    packet = _packet("As of today, AAPL is 210.")
    packet["trace"]["spans"] = [
        {
            "name": "market_data_analysis",
            "output": {
                "output": '{"summary": "the venue reported status_code: 503 during the outage"}'
            },
        },
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
        },
    ]

    result = judge_packet(_case(), packet)

    assert result.verdict == "pass"


def test_structural_required_text_miss_still_fails_hard() -> None:
    case = _case(
        assertions={"required_text": [{"regex": r"(?i)\bmixed\s+expir\w*", "kind": "structural"}]}
    )

    result = judge_packet(case, _packet("As of today, the legs share one expiry."))

    assert result.verdict == "fail_product"
    assert any("required_text missing" in failure for failure in result.checks_failed)
    assert result.diagnostics == []


def test_required_text_without_a_kind_is_treated_as_structural() -> None:
    case = _case(assertions={"required_text": [{"regex": r"(?i)\bmixed\s+expir\w*"}]})

    result = judge_packet(case, _packet("As of today, the legs share one expiry."))

    assert result.verdict == "fail_product"
    assert any("required_text missing" in failure for failure in result.checks_failed)


def test_lexical_required_text_miss_routes_to_semantic_review() -> None:
    case = _case(
        assertions={"required_text": [{"regex": r"(?i)\bmixed\s+expir\w*", "kind": "lexical"}]}
    )

    result = judge_packet(case, _packet("As of today, the legs settle on two different dates."))

    assert result.verdict == "needs_semantic_review"
    assert result.checks_failed == []
    assert any("lexical" in note for note in result.diagnostics)
    assert any(entry.startswith("lexical_text[") for entry in result.unexecuted_assertions)


def test_lexical_required_text_match_needs_no_review() -> None:
    case = _case(
        assertions={"required_text": [{"regex": r"(?i)\bmixed\s+expir\w*", "kind": "lexical"}]}
    )

    result = judge_packet(case, _packet("As of today, the spread carries mixed expiries."))

    assert result.verdict == "pass"
    assert result.diagnostics == []
    assert result.unexecuted_assertions == []


def test_lexical_required_text_inside_an_absence_statement_is_a_diagnostic() -> None:
    # The absence branch is still not a disclosure, but for a lexical spec that
    # is a phrasing observation for the reviewer, not a product failure.
    case = _case(
        assertions={"required_text": [{"regex": r"\b(?:JPY|Japanese yen)\b", "kind": "lexical"}]}
    )
    packet = _packet(
        "Toyota's latest annual income statement was unavailable.\nFiscal period, "
        "JPY units, revenue, operating income, and net income could not be verified."
    )

    result = judge_packet(case, packet)

    assert result.verdict == "needs_semantic_review"
    assert result.checks_failed == []
    assert any("absence statement" in note for note in result.diagnostics)


def test_lexical_forbidden_text_hit_does_not_fail_the_case() -> None:
    # The judge stays total over its input space. lint_cases separately forbids
    # authoring a lexical forbidden_text in the corpus, so this path describes
    # behaviour for an out-of-corpus packet rather than a shape the suite ships.
    case = _case(
        assertions={
            "required_text": ["as of"],
            "forbidden_text": [{"regex": r"(?i)\bsingle\s+expiry\b", "kind": "lexical"}],
        }
    )

    result = judge_packet(case, _packet("As of today, the spread trades on a single expiry."))

    assert result.verdict == "needs_semantic_review"
    assert result.checks_failed == []
    assert any("lexical" in note for note in result.diagnostics)


def test_structural_forbidden_text_hit_still_fails_hard() -> None:
    case = _case(
        assertions={
            "required_text": ["as of"],
            "forbidden_text": [{"regex": r"(?i)\bsingle\s+expiry\b", "kind": "structural"}],
        }
    )

    result = judge_packet(case, _packet("As of today, the spread trades on a single expiry."))

    assert result.verdict == "fail_product"
    assert any("forbidden_text present" in failure for failure in result.checks_failed)


def test_diagnostics_are_serialised_into_the_result_payload() -> None:
    case = _case(
        assertions={"required_text": [{"regex": r"(?i)\bmixed\s+expir\w*", "kind": "lexical"}]}
    )

    result = judge_packet(case, _packet("As of today, the legs settle on two different dates."))

    # The reviewer reads the report, not the JudgeResult, so the note has to
    # survive serialisation carrying the pattern that went unmatched.
    assert result.to_dict()["diagnostics"] == result.diagnostics
    assert any("mixed" in note for note in result.to_dict()["diagnostics"])


def test_only_an_exact_lexical_kind_downgrades_the_gate() -> None:
    # lint_cases rejects these spellings outright. If one reached the judge
    # anyway it must stay a hard gate, so a typo can never quietly disarm an
    # assertion the author believed was still being enforced.
    for spelling in ("Lexical", "LEXICAL", " lexical "):
        case = _case(
            assertions={"required_text": [{"regex": r"(?i)\bmixed\s+expir\w*", "kind": spelling}]}
        )

        result = judge_packet(case, _packet("As of today, the legs share one expiry."))

        assert result.verdict == "fail_product", spelling
        assert result.diagnostics == [], spelling


def _packet_with_tool_error(payload: str) -> dict:
    """Build a packet whose inner tool span returned a structured error."""
    packet = _packet()
    packet["trace"]["spans"] = [
        {"name": "market_data_analysis", "output": {"output": payload}},
        {
            "name": "load_skill",
            "input": {"skill_name": "obai-market-data-routing"},
            "output": {"status": "loaded"},
        },
    ]
    return packet


def test_entitlement_span_error_is_provider_inconclusive_not_specialist_error() -> None:
    """A 402 payload must not be reclassified as a specialist error.

    _error_blob feeds two consumers: the provider check and _observed_outcome,
    which runs SPECIALIST_ERROR_RE over the same text. Contributing span
    payloads to the blob put entitlement failures within reach of that second
    consumer, so the CORE-FX shape is pinned here: it resolves as a provider
    outage, which is inconclusive, not as a product failure.
    """
    packet = _packet_with_tool_error(
        '{"isError": true, "error": "FMP: API subscription required for this endpoint",'
        ' "error_type": "HTTPStatusError", "status_code": 402}'
    )

    result = judge_packet(_case(), packet)

    assert result.verdict == "inconclusive_provider"


def test_span_error_naming_a_tool_error_classifies_the_outcome() -> None:
    """A structured error that says so is a specialist error, by contract.

    SKILL.md: any financial-specialist error span is a failure or a
    provider-inconclusive result. Reaching _observed_outcome is therefore the
    intended consequence of making structured span errors visible, not a leak.
    """
    packet = _packet_with_tool_error('{"isError": true, "error": "tool error: rule rejected"}')

    result = judge_packet(_case(), packet)

    assert result.observed_outcome == "specialist_error"


def test_clean_span_output_leaves_the_outcome_untouched() -> None:
    """A span output with no error flag must not reach the blob at all."""
    packet = _packet_with_tool_error('{"data": [], "note": "provider error rates are low"}')

    result = judge_packet(_case(), packet)

    assert result.observed_outcome == "success"


class TestUnrecognisedDegradedOutcome:
    """`success` is the fallthrough of _observed_outcome, not a positive finding.

    _observed_outcome can only return `partial_refusal` or `data_unavailable`
    when the case declared that branch, so those labels can never mismatch.
    The reachable mismatches are the two machine-determined ones - hub_reject
    from the guardrail flag, specialist_error from an error payload - and
    `success`, which merely means no declared degraded branch was recognised
    in the prose. Hard-failing on that last one fails on wording.
    """

    _REFUSAL_CASE = {
        "expected_outcome": "partial_refusal",
        "acceptable_outcomes": ["specialist_error"],
        "assertions": {
            "forbidden_text": [
                {
                    "regex": r"(?i)\bmax(?:imum)?[ -](?:profit|loss)\b[^.\n]{0,30}[-+$]?\d",
                    "kind": "structural",
                }
            ]
        },
    }

    def test_refusal_in_unrecognised_wording_routes_to_review(self) -> None:
        # Captured verbatim from two gate runs of CORE-OPT-MIXED-EXPIRY. Same
        # behaviour, same substance, opposite verdict - decided only by whether
        # the word "Refused" happened to appear.
        case = _case(**self._REFUSAL_CASE)
        packet = _packet(
            "A single shared-expiry profile is invalid because expirations differ. "
            "Therefore, aggregate maximum profit, maximum loss, and breakeven are undefined."
        )

        result = judge_packet(case, packet)

        assert result.verdict == "needs_semantic_review"
        assert result.checks_failed == []
        assert any("outcome" in entry for entry in result.unexecuted_assertions)

    def test_the_recognised_wording_still_passes(self) -> None:
        case = _case(**self._REFUSAL_CASE)

        result = judge_packet(
            case, _packet("Refused: the legs cannot be aggregated into one expiry profile.")
        )

        assert result.verdict == "pass_degraded"

    def test_actually_doing_the_forbidden_thing_still_fails_hard(self) -> None:
        # The safety property never depended on the outcome label: the
        # structural forbidden_text is evaluated first and still hard-fails.
        case = _case(**self._REFUSAL_CASE)

        result = judge_packet(
            case, _packet("Combined profile: maximum profit $420, maximum loss -$180.")
        )

        assert result.verdict == "fail_product"
        assert any("forbidden_text present" in f for f in result.checks_failed)

    def test_a_guardrail_rejection_it_never_declared_still_fails_hard(self) -> None:
        # hub_reject comes from a machine flag, not from prose, so it is a real
        # finding and must not be softened into a review. Assertions are cleared
        # so the outcome comparison is what decides, not an unrelated text miss.
        packet = _packet("I can only help with financial analysis.")
        packet["cli"]["stdout_json"]["guardrail_rejected"] = True

        result = judge_packet(_case(assertions={}), packet)

        assert result.verdict == "fail_product"
        assert any("observed hub_reject" in f for f in result.checks_failed)

    def test_review_is_told_which_branches_to_confirm(self) -> None:
        case = _case(**self._REFUSAL_CASE)

        result = judge_packet(case, _packet("The two legs expire on different dates."))

        pending = [e for e in result.unexecuted_assertions if e.startswith("outcome[")]
        assert pending, result.unexecuted_assertions
        assert "partial_refusal" in pending[0]
        assert "specialist_error" in pending[0]

    def test_a_guardrail_that_never_fired_still_fails_hard(self) -> None:
        """expected hub_reject, observed success is a machine fact, not wording.

        _observed_outcome has no prose path to hub_reject at all - it is
        returned solely from the guardrail flag. So `success` against a case
        contracted for hub_reject means the guardrail did not fire, which is
        exactly the machine-determined class that must stay a hard failure.
        SMK-GUARD declares no text assertions whatsoever, so this outcome check
        is its only gate: softening it would let a total guardrail bypass reach
        a reviewer with an empty checks_failed list.
        """
        case = _case(expected_outcome="hub_reject", assertions={})

        result = judge_packet(case, _packet("Boston tomorrow: partly cloudy, high near 54F."))

        assert result.verdict == "fail_product"
        assert any("observed success" in f for f in result.checks_failed)

    def test_a_specialist_error_that_never_happened_still_fails_hard(self) -> None:
        """Same asymmetry for the other machine-determined outcome."""
        case = _case(expected_outcome="specialist_error", assertions={})

        result = judge_packet(case, _packet("Here is the full answer you asked for."))

        assert result.verdict == "fail_product"
        assert any("observed success" in f for f in result.checks_failed)

    def test_a_prose_branch_alongside_a_machine_branch_still_softens(self) -> None:
        """The test is an intersection, not a subset.

        CORE-OPT-MIXED-EXPIRY declares acceptable_outcomes [specialist_error]
        beside expected_outcome partial_refusal; requiring every declared branch
        to be prose-classified would kill the motivating fix.
        """
        case = _case(**self._REFUSAL_CASE)

        result = judge_packet(case, _packet("The two legs settle on different dates."))

        assert result.verdict == "needs_semantic_review"
