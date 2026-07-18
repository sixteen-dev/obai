from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from lint_cases import lint_suite


def _suite(*cases: dict, **suite_config: object) -> dict:
    payload: dict[str, object] = {"test_cases": list(cases)}
    if suite_config:
        payload["suite"] = suite_config
    return payload


def _case(
    case_id: str, query: str = "Analyze AAPL using the supplied fixture.", **extra: object
) -> dict:
    return {
        "id": case_id,
        "feature": f"feature_{case_id.lower()}",
        "query": query,
        "tier": "core",
        "expected_outcome": "success",
        "estimated_api_calls": 3,
        **extra,
    }


def codes(issues: list) -> set[str]:
    return {issue.code for issue in issues}


def test_valid_deterministic_core_case_has_no_errors() -> None:
    issues = lint_suite(_suite(_case("A1", date_policy="frozen", data_hash="sha256:abc")))

    assert not [issue for issue in issues if issue.severity == "error"]


def test_duplicate_ids_and_queries_are_errors() -> None:
    suite = _suite(
        _case("A1", "Analyze AAPL now."),
        _case("A1", "  analyze   AAPL now.  "),
    )

    issues = lint_suite(suite)

    assert {"duplicate-id", "duplicate-query"} <= codes(issues)


def test_case_ids_cannot_escape_run_directory() -> None:
    issues = lint_suite(_suite(_case("../outside")))

    assert "invalid-id-format" in codes(issues)


def test_expired_live_case_is_an_error() -> None:
    suite = _suite(
        _case(
            "L1",
            "Find the latest unresolved market.",
            tier="live",
            date_policy="live",
            timezone="America/New_York",
            max_age_seconds=900,
            expires_on="2026-06-27",
        )
    )

    issues = lint_suite(suite, as_of=date(2026, 7, 15))

    assert "expired-case" in codes(issues)


def test_relative_query_without_clock_contract_warns() -> None:
    issues = lint_suite(
        _suite(_case("R1", "What is AAPL trading at today?", date_policy="relative"))
    )

    assert "missing-timezone" in codes(issues)
    assert "missing-max-age" not in codes(issues)


def test_unknown_iana_timezone_is_an_error() -> None:
    issues = lint_suite(
        _suite(
            _case(
                "R1",
                "What is AAPL trading at today?",
                date_policy="relative",
                timezone="Mars/Olympus_Mons",
            )
        )
    )

    assert "invalid-timezone" in codes(issues)


def test_invalid_enums_and_cost_fields_are_errors() -> None:
    issues = lint_suite(
        _suite(
            _case(
                "X1",
                tier="gold",
                date_policy="sometimes",
                expected_outcome="probably",
                estimated_api_calls=-1,
            )
        )
    )

    assert {
        "invalid-tier",
        "invalid-date-policy",
        "invalid-expected-outcome",
        "invalid-api-cost",
    } <= codes(issues)


def test_conditional_outcomes_are_schema_validated() -> None:
    valid = lint_suite(
        _suite(_case("C1", acceptable_outcomes=["partial_refusal", "data_unavailable"]))
    )
    invalid = lint_suite(_suite(_case("C1", acceptable_outcomes=["maybe"])))

    assert "invalid-acceptable-outcomes" not in codes(valid)
    assert "invalid-acceptable-outcomes" in codes(invalid)


def test_case_specific_degraded_patterns_are_strictly_validated() -> None:
    valid = lint_suite(
        _suite(
            _case(
                "C1",
                acceptable_outcomes=["data_unavailable"],
                degraded_outcome_patterns={"data_unavailable": r"(?i)no trade"},
            )
        )
    )
    invalid = lint_suite(
        _suite(
            _case(
                "C1",
                degraded_outcome_patterns={
                    "partial_refusal": "(",
                    "success": "anything",
                },
            )
        )
    )

    assert not {
        "invalid-degraded-outcome-key",
        "undeclared-degraded-outcome",
        "invalid-degraded-outcome-regex",
    } & codes(valid)
    assert {
        "invalid-degraded-outcome-key",
        "undeclared-degraded-outcome",
        "invalid-degraded-outcome-regex",
    } <= codes(invalid)


def test_core_budget_overrun_and_paid_repeat_are_rejected() -> None:
    issues = lint_suite(
        _suite(
            _case("C1", estimated_api_calls=7, repeat=3),
            core_max_api_calls=5,
        )
    )

    assert {"tier-budget-exceeded", "paid-repeat-disallowed"} <= codes(issues)


def test_cost_floor_includes_declared_optional_specialists() -> None:
    issues = lint_suite(
        _suite(
            _case(
                "C1",
                expected_tools=["market_data_analysis"],
                allowed_extras=["research_analysis"],
                cost={"max_specialist_calls": 1},
                estimated_api_calls=5,
            )
        )
    )

    assert "invalid-model-request-contract" in codes(issues)


def test_missing_chain_parent_is_an_error() -> None:
    issues = lint_suite(_suite(_case("C2", chain_from="C1")))

    assert "missing-chain-parent" in codes(issues)


def test_chain_outcome_gate_requires_parent_and_known_outcomes() -> None:
    invalid = lint_suite(_suite(_case("C1", chain_requires_parent_outcomes=["maybe"])))
    valid = lint_suite(
        _suite(
            _case("P1"),
            _case(
                "C1",
                "Follow up on prior result.",
                chain_from="P1",
                chain_requires_parent_outcomes=["success"],
            ),
        )
    )

    assert "invalid-chain-outcome-gate" in codes(invalid)
    assert "invalid-chain-outcome-gate" not in codes(valid)


def test_global_timezone_and_nested_freshness_satisfy_live_contract() -> None:
    suite = {
        "timezone": "America/New_York",
        "default_tier": "core",
        "suite_budgets": {"core": {"max_cases": 1, "max_estimated_api_calls": 3}},
        "test_cases": [
            _case(
                "L1",
                "Give the current AAPL quote.",
                date_policy="live",
                cost={"class": "low", "estimated_api_calls": 3},
                estimated_api_calls=None,
                freshness={"max_age_seconds": 900, "as_of_required": True},
            )
        ],
    }

    issues = lint_suite(suite)

    assert "missing-timezone" not in codes(issues)
    assert "missing-max-age" not in codes(issues)
    assert "invalid-api-cost" not in codes(issues)


def test_suite_budgets_validate_case_count_and_nested_api_cost() -> None:
    suite = {
        "default_tier": "core",
        "suite_budgets": {"core": {"max_cases": 1, "max_estimated_api_calls": 3}},
        "test_cases": [
            _case("C1", estimated_api_calls=None, cost={"estimated_api_calls": 2}),
            _case("C2", estimated_api_calls=None, cost={"estimated_api_calls": 2}),
        ],
    }

    issues = lint_suite(suite)

    assert {"tier-case-budget-exceeded", "tier-budget-exceeded"} <= codes(issues)


def test_exact_tier_policy_fails_open_downward_changes() -> None:
    issues = lint_suite(
        _suite(
            _case("C1", estimated_api_calls=3),
            enforce_exact_tier_budgets=True,
            core_max_cases=2,
            core_max_api_calls=6,
        )
    )

    assert {"tier-case-count-mismatch", "tier-budget-mismatch"} <= codes(issues)


def test_canonical_suite_rejects_deleted_core_case() -> None:
    cases_path = Path(__file__).resolve().parents[1] / "cases" / "cases.yaml"
    raw = yaml.safe_load(cases_path.read_text())
    raw["test_cases"] = [
        case for case in raw["test_cases"] if case.get("id") != "CORE-FX"
    ]

    issues = lint_suite(raw, as_of=date(2026, 7, 16))

    assert {"tier-case-count-mismatch", "tier-budget-mismatch"} <= codes(issues)


def test_unknown_assertion_key_is_an_error_not_silently_ignored() -> None:
    issues = lint_suite(_suite(_case("A1", assertions={"magic_financial_check": ["x"]})))

    assert "unsupported-assertion" in codes(issues)


def test_v2_assertion_vocabulary_is_schema_validated() -> None:
    issues = lint_suite(
        _suite(
            _case(
                "A1",
                assertions={
                    "required_claims": ["job_id"],
                    "forbidden_claims": ["artifact_exported"],
                    "forbidden_calls": ["crypto_strategy_export_artifact"],
                    "numeric_checker": "collar_payoff",
                    "numeric_tolerance": {"usd": 1.0},
                    "equality_across_turns": ["job_id"],
                    "conditional_invariants": ["eligible_implies_artifact_and_fingerprint"],
                },
            )
        )
    )

    assert "unsupported-assertion" not in codes(issues)


def test_frozen_synthetic_oracle_and_revision_aware_provider_contract_are_accepted() -> None:
    issues = lint_suite(
        _suite(
            _case("O1", date_policy="frozen", oracle_id="black-scholes-synthetic-v1"),
            _case(
                "D1",
                "Historical provider contract query.",
                date_policy="frozen",
                data_contract_id="prices-2025-v1",
                provider_revisions_allowed=True,
            ),
        )
    )

    assert "unversioned-frozen-data" not in codes(issues)
    assert "provider-revision-policy-missing" not in codes(issues)


def test_frozen_provider_contract_requires_explicit_revision_policy() -> None:
    issues = lint_suite(
        _suite(_case("D1", date_policy="frozen", data_contract_id="prices-2025-v1"))
    )

    assert "provider-revision-policy-missing" in codes(issues)


def test_text_assertion_regex_is_compiled_during_lint() -> None:
    issues = lint_suite(_suite(_case("A1", assertions={"required_text": [{"regex": "("}]})))

    assert "invalid-text-assertion-regex" in codes(issues)


def test_async_case_requires_typed_prompt_wait_and_paid_poll_budget() -> None:
    missing = lint_suite(_suite(_case("ASYNC", expect_async_job=True)))
    assert "missing-async-contract" in codes(missing)

    valid = lint_suite(
        _suite(
            _case(
                "ASYNC",
                expect_async_job=True,
                expected_tools=["strategy_analysis"],
                estimated_api_calls=15,
                max_cli_turns=3,
                cost={"max_async_polls": 2},
                async_contract={
                    "job_type": "strategy_walk_forward",
                    "max_wait_seconds": 600,
                    "poll_prompt": "Check strategy job {job_id} and return stored results.",
                },
            )
        )
    )

    assert not [issue for issue in valid if issue.severity == "error"]


def test_async_cost_cannot_understate_budgeted_poll_turns() -> None:
    issues = lint_suite(
        _suite(
            _case(
                "ASYNC",
                expect_async_job=True,
                expected_tools=["crypto_analysis"],
                estimated_api_calls=2,
                max_cli_turns=3,
                cost={"max_async_polls": 2},
                async_contract={
                    "job_type": "crypto_backtest",
                    "max_wait_seconds": 600,
                    "poll_prompt": "Check crypto job {job_id}.",
                },
            )
        )
    )

    assert "model-requests-underestimated" in codes(issues)


def test_chain_child_of_degradable_parent_requires_outcome_gate() -> None:
    issues = lint_suite(
        _suite(
            _case("P1", acceptable_outcomes=["data_unavailable"]),
            _case("C1", chain_from="P1"),
        )
    )

    assert "chain-gate-required" in codes(issues)


def test_chain_child_gate_present_clears_the_requirement() -> None:
    issues = lint_suite(
        _suite(
            _case("P1", acceptable_outcomes=["data_unavailable"]),
            _case("C1", chain_from="P1", chain_requires_parent_outcomes=["success"]),
        )
    )

    assert "chain-gate-required" not in codes(issues)


def test_chain_child_of_success_only_parent_needs_no_gate() -> None:
    issues = lint_suite(
        _suite(
            _case("P1"),
            _case("C1", chain_from="P1"),
        )
    )

    assert "chain-gate-required" not in codes(issues)


def test_live_case_without_as_of_required_is_error() -> None:
    issues = lint_suite(
        _suite(
            _case(
                "L1",
                date_policy="live",
                timezone="America/New_York",
                max_age_seconds=900,
            )
        )
    )

    assert "live-missing-as-of-required" in codes(issues)


def test_live_case_with_as_of_required_clears_the_error() -> None:
    issues = lint_suite(
        _suite(
            _case(
                "L1",
                date_policy="live",
                timezone="America/New_York",
                freshness={"max_age_seconds": 900, "as_of_required": True},
            )
        )
    )

    assert "live-missing-as-of-required" not in codes(issues)
