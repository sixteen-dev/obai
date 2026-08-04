#!/usr/bin/env python3
"""Validate OBaI E2E case schema, temporal freshness, and model-cost policy.

The linter is deliberately offline.  It never resolves placeholders or calls a
provider; it verifies that each case declares enough policy for the runner and
judge to make safe, reproducible decisions.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

TIERS = frozenset({"core", "smoke", "extended", "live"})
DATE_POLICIES = frozenset({"live", "relative", "frozen"})
EXPECTED_OUTCOMES = frozenset(
    {"success", "partial_refusal", "specialist_error", "hub_reject", "data_unavailable"}
)
ASSERTION_KEYS = frozenset(
    {
        "required_text",
        "forbidden_text",
        "required_evidence",
        "required_tools",
        "required_skills",
        "forbidden_skills",
        "expected_sequence",
        "required_claims",
        "forbidden_claims",
        "numeric_invariants",
        "numeric_checker",
        "numeric_tolerance",
        "equality_across_turns",
        "conditional_invariants",
        "forbidden_calls",
        "forbidden_tools",
        "manual_assertions",
    }
)
RELATIVE_TIME_RE = re.compile(
    r"\b(today|tonight|tomorrow|yesterday|current|currently|latest|now|"
    r"next\s+(?:week|month|quarter|year)|last\s+complete)\b",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(20\d{2})\b")
CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


@dataclass(frozen=True)
class LintIssue:
    severity: str
    code: str
    message: str
    case_id: str | None = None
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _issue(
    issues: list[LintIssue],
    severity: str,
    code: str,
    message: str,
    case_id: str | None = None,
    field: str | None = None,
) -> None:
    issues.append(LintIssue(severity, code, message, case_id, field))


def _case_tier(case: dict[str, Any]) -> str:
    tier = case.get("tier") or case.get("test_tier")
    if isinstance(tier, str):
        return tier
    return "smoke" if case.get("smoke") is True else "extended"


def _api_cost(case: dict[str, Any]) -> int | None:
    value = case.get("estimated_api_calls")
    if value is None and isinstance(case.get("cost"), dict):
        value = case["cost"].get("estimated_api_calls")
    if value is None:
        value = case.get("api_cost", 1)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    repeat = case.get("repeat", 1)
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        return None
    return value * repeat


def _model_request_estimate_floor(case: dict[str, Any]) -> tuple[int, int] | None:
    """Return (declared turns, minimum planning estimate for typical routing)."""
    cost = case.get("cost") if isinstance(case.get("cost"), dict) else {}
    max_polls = cost.get("max_async_polls", 0) if case.get("expect_async_job") else 0
    if isinstance(max_polls, bool) or not isinstance(max_polls, int) or max_polls < 0:
        return None
    expected_turns = 1 + max_polls
    declared_turns = case.get("max_cli_turns", expected_turns)
    if (
        isinstance(declared_turns, bool)
        or not isinstance(declared_turns, int)
        or declared_turns != expected_turns
    ):
        return None
    expected_tools = case.get("expected_tools")
    allowed_extras = case.get("allowed_extras")
    budgeted_specialists = {
        tool
        for tools in (expected_tools, allowed_extras)
        if isinstance(tools, list)
        for tool in tools
        if isinstance(tool, str)
    }
    expected_specialists = len(budgeted_specialists)
    max_specialists = cost.get("max_specialist_calls", expected_specialists)
    if (
        isinstance(max_specialists, bool)
        or not isinstance(max_specialists, int)
        or max_specialists < expected_specialists
    ):
        return None
    # Per CLI turn: one guardrail request, at least two hub requests, one
    # additional hub request when mandatory skill loading is declared, and a
    # typical MCP specialist tool/final pair for every allowed specialist.
    per_turn = 3 + (1 if case.get("expected_skills") else 0) + (2 * max_specialists)
    return declared_turns, declared_turns * per_turn


def _normalized_query(query: str) -> str:
    return re.sub(r"\s+", " ", query).strip().casefold()


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _validate_assertions(case: dict[str, Any], issues: list[LintIssue], case_id: str) -> None:
    assertions = case.get("assertions")
    if assertions is None:
        return
    if not isinstance(assertions, dict):
        _issue(
            issues,
            "error",
            "invalid-assertions",
            "assertions must be a mapping",
            case_id,
            "assertions",
        )
        return
    for key in assertions:
        if key not in ASSERTION_KEYS:
            _issue(
                issues,
                "error",
                "unsupported-assertion",
                f"assertions.{key} has no registered linter/judge implementation",
                case_id,
                f"assertions.{key}",
            )
    for field in ("required_text", "forbidden_text"):
        value = assertions.get(field)
        if value is not None and not isinstance(value, list):
            _issue(
                issues,
                "error",
                "invalid-assertion-list",
                f"assertions.{field} must be a list",
                case_id,
                f"assertions.{field}",
            )
        elif isinstance(value, list):
            for index, spec in enumerate(value):
                spec_field = f"assertions.{field}[{index}]"
                if isinstance(spec, str):
                    continue
                if not isinstance(spec, dict):
                    _issue(
                        issues,
                        "error",
                        "invalid-text-assertion",
                        f"{spec_field} must be a string or text/regex mapping",
                        case_id,
                        spec_field,
                    )
                    continue
                allowed = {"text", "regex", "case_sensitive"}
                if field == "forbidden_text":
                    # Meaningful only here: required_text is already scored
                    # against the asserting clauses.
                    allowed.add("only_when_asserted")
                unknown = set(spec) - allowed
                text_value = spec.get("text")
                regex_value = spec.get("regex")
                has_one_pattern = (isinstance(text_value, str) and not regex_value) or (
                    isinstance(regex_value, str) and not text_value
                )
                if (
                    unknown
                    or not has_one_pattern
                    or ("case_sensitive" in spec and not isinstance(spec["case_sensitive"], bool))
                    or (
                        "only_when_asserted" in spec
                        and not isinstance(spec["only_when_asserted"], bool)
                    )
                ):
                    _issue(
                        issues,
                        "error",
                        "invalid-text-assertion",
                        f"{spec_field} must contain exactly one string text/regex and optional "
                        "boolean case_sensitive",
                        case_id,
                        spec_field,
                    )
                    continue
                if isinstance(regex_value, str):
                    try:
                        re.compile(regex_value)
                    except re.error as exc:
                        _issue(
                            issues,
                            "error",
                            "invalid-text-assertion-regex",
                            f"{spec_field} regex does not compile: {exc}",
                            case_id,
                            spec_field,
                        )
    required_evidence = assertions.get("required_evidence")
    if required_evidence is not None and (
        not isinstance(required_evidence, list)
        or not all(isinstance(item, str) and item for item in required_evidence)
    ):
        _issue(
            issues,
            "error",
            "invalid-assertion-list",
            "assertions.required_evidence must be a list of non-empty paths",
            case_id,
            "assertions.required_evidence",
        )
    for field in (
        "required_tools",
        "required_skills",
        "forbidden_skills",
        "required_claims",
        "forbidden_claims",
        "numeric_invariants",
        "equality_across_turns",
        "conditional_invariants",
        "forbidden_calls",
        "forbidden_tools",
        "manual_assertions",
    ):
        value = assertions.get(field)
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(item, str) for item in value)
        ):
            _issue(
                issues,
                "error",
                "invalid-assertion-list",
                f"assertions.{field} must be a list of strings",
                case_id,
                f"assertions.{field}",
            )
    checker = assertions.get("numeric_checker")
    if checker is not None and (not isinstance(checker, str) or not checker):
        _issue(
            issues,
            "error",
            "invalid-numeric-checker",
            "assertions.numeric_checker must be a registered checker name string",
            case_id,
            "assertions.numeric_checker",
        )
    tolerance = assertions.get("numeric_tolerance")
    if tolerance is not None and (
        not isinstance(tolerance, dict)
        or any(
            not isinstance(key, str)
            or isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value < 0
            for key, value in tolerance.items()
        )
    ):
        _issue(
            issues,
            "error",
            "invalid-numeric-tolerance",
            "assertions.numeric_tolerance must map names to non-negative numbers",
            case_id,
            "assertions.numeric_tolerance",
        )


def _validate_async_contract(case: dict[str, Any], issues: list[LintIssue], case_id: str) -> None:
    expects_async = case.get("expect_async_job") is True
    contract = case.get("async_contract")
    if not expects_async:
        if contract is not None:
            _issue(
                issues,
                "error",
                "orphan-async-contract",
                "async_contract requires expect_async_job: true",
                case_id,
                "async_contract",
            )
        return
    if not isinstance(contract, dict):
        _issue(
            issues,
            "error",
            "missing-async-contract",
            "async cases need a typed poll prompt and bounded wait contract",
            case_id,
            "async_contract",
        )
        return
    unknown = set(contract) - {"job_type", "poll_prompt", "max_wait_seconds"}
    if unknown:
        _issue(
            issues,
            "error",
            "unsupported-async-contract-field",
            f"unsupported async_contract field(s): {sorted(unknown)}",
            case_id,
            "async_contract",
        )
    if not isinstance(contract.get("job_type"), str) or not contract["job_type"].strip():
        _issue(
            issues,
            "error",
            "invalid-async-job-type",
            "async_contract.job_type must be a non-empty string",
            case_id,
            "async_contract.job_type",
        )
    prompt = contract.get("poll_prompt")
    if not isinstance(prompt, str) or prompt.count("{job_id}") != 1:
        _issue(
            issues,
            "error",
            "invalid-async-poll-prompt",
            "async_contract.poll_prompt must contain exactly one {job_id} placeholder",
            case_id,
            "async_contract.poll_prompt",
        )
    max_wait = contract.get("max_wait_seconds")
    if isinstance(max_wait, bool) or not isinstance(max_wait, int) or not 30 <= max_wait <= 600:
        _issue(
            issues,
            "error",
            "invalid-async-max-wait",
            "async_contract.max_wait_seconds must be an integer from 30 through 600",
            case_id,
            "async_contract.max_wait_seconds",
        )
    cost = case.get("cost") if isinstance(case.get("cost"), dict) else {}
    max_polls = cost.get("max_async_polls")
    if isinstance(max_polls, bool) or not isinstance(max_polls, int) or not 1 <= max_polls <= 2:
        _issue(
            issues,
            "error",
            "invalid-async-poll-budget",
            "cost.max_async_polls must explicitly budget one or two paid polls",
            case_id,
            "cost.max_async_polls",
        )
        return


def _detect_cycles(cases_by_id: dict[str, dict[str, Any]], issues: list[LintIssue]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(case_id: str, path: list[str]) -> None:
        if case_id in visited:
            return
        if case_id in visiting:
            cycle = path[path.index(case_id) :] + [case_id]
            _issue(
                issues,
                "error",
                "chain-cycle",
                f"chain_from cycle: {' -> '.join(cycle)}",
                case_id,
                "chain_from",
            )
            return
        visiting.add(case_id)
        parent = cases_by_id[case_id].get("chain_from")
        if isinstance(parent, str) and parent in cases_by_id:
            visit(parent, [*path, parent])
        visiting.remove(case_id)
        visited.add(case_id)

    for case_id in cases_by_id:
        visit(case_id, [case_id])


def lint_suite(raw: object, *, as_of: date | None = None) -> list[LintIssue]:
    """Return deterministic schema/freshness/cost findings for a parsed suite."""
    issues: list[LintIssue] = []
    if not isinstance(raw, dict):
        return [LintIssue("error", "invalid-root", "suite root must be a mapping")]

    cases = raw.get("test_cases")
    if not isinstance(cases, list):
        return [
            LintIssue(
                "error", "invalid-test-cases", "test_cases must be a list", field="test_cases"
            )
        ]

    nested_suite = raw.get("suite") if isinstance(raw.get("suite"), dict) else {}
    global_timezone = raw.get("timezone", nested_suite.get("timezone"))
    if global_timezone is not None and (
        not isinstance(global_timezone, str) or not global_timezone.strip()
    ):
        _issue(
            issues,
            "error",
            "invalid-timezone",
            "timezone must be a non-empty IANA timezone string",
            field="timezone",
        )
    elif isinstance(global_timezone, str):
        try:
            ZoneInfo(global_timezone)
        except ZoneInfoNotFoundError:
            _issue(
                issues,
                "error",
                "invalid-timezone",
                f"unknown IANA timezone {global_timezone!r}",
                field="timezone",
            )
    if as_of is None:
        if isinstance(global_timezone, str):
            try:
                as_of = datetime.now(tz=ZoneInfo(global_timezone)).date()
            except ZoneInfoNotFoundError:
                as_of = date.today()
        else:
            as_of = date.today()
    default_tier = raw.get("default_tier", nested_suite.get("default_tier"))
    if default_tier is not None and default_tier not in TIERS:
        _issue(
            issues,
            "error",
            "invalid-default-tier",
            f"default_tier must be one of {sorted(TIERS)}",
            field="default_tier",
        )

    seen_ids: dict[str, int] = {}
    seen_queries: dict[str, str] = {}
    cases_by_id: dict[str, dict[str, Any]] = {}
    tier_costs = {tier: 0 for tier in TIERS}

    for index, entry in enumerate(cases):
        if not isinstance(entry, dict):
            _issue(
                issues,
                "error",
                "invalid-case",
                f"test_cases[{index}] must be a mapping",
                field=f"test_cases[{index}]",
            )
            continue

        case_id_raw = entry.get("id")
        case_id = case_id_raw if isinstance(case_id_raw, str) and case_id_raw.strip() else None
        if case_id is None:
            _issue(
                issues,
                "error",
                "missing-id",
                "case id must be a non-empty string",
                field=f"test_cases[{index}].id",
            )
            case_id = f"<index:{index}>"
        else:
            if not CASE_ID_RE.fullmatch(case_id):
                _issue(
                    issues,
                    "error",
                    "invalid-id-format",
                    "case id must use only letters, digits, dot, underscore, or hyphen",
                    case_id,
                    "id",
                )
            if case_id in seen_ids:
                _issue(
                    issues,
                    "error",
                    "duplicate-id",
                    f"case id duplicates test_cases[{seen_ids[case_id]}]",
                    case_id,
                    "id",
                )
            else:
                seen_ids[case_id] = index
                cases_by_id[case_id] = entry

        for required in ("feature", "query"):
            value = entry.get(required)
            if not isinstance(value, str) or not value.strip():
                _issue(
                    issues,
                    "error",
                    f"missing-{required}",
                    f"{required} must be a non-empty string",
                    case_id,
                    required,
                )

        query = entry.get("query")
        if isinstance(query, str) and query.strip():
            normalized = _normalized_query(query)
            prior = seen_queries.get(normalized)
            if prior is not None:
                _issue(
                    issues,
                    "error",
                    "duplicate-query",
                    f"query duplicates case {prior}; keep the duplicate in the extended corpus instead",
                    case_id,
                    "query",
                )
            else:
                seen_queries[normalized] = case_id

        tier = _case_tier(entry)
        if tier not in TIERS:
            _issue(
                issues,
                "error",
                "invalid-tier",
                f"tier must be one of {sorted(TIERS)}",
                case_id,
                "tier",
            )

        expected_outcome = entry.get("expected_outcome", "success")
        if expected_outcome not in EXPECTED_OUTCOMES:
            _issue(
                issues,
                "error",
                "invalid-expected-outcome",
                f"expected_outcome must be one of {sorted(EXPECTED_OUTCOMES)}",
                case_id,
                "expected_outcome",
            )
        acceptable_outcomes = entry.get("acceptable_outcomes")
        if acceptable_outcomes is not None and (
            not isinstance(acceptable_outcomes, list)
            or not acceptable_outcomes
            or not all(outcome in EXPECTED_OUTCOMES for outcome in acceptable_outcomes)
        ):
            _issue(
                issues,
                "error",
                "invalid-acceptable-outcomes",
                f"acceptable_outcomes must be a non-empty list drawn from {sorted(EXPECTED_OUTCOMES)}",
                case_id,
                "acceptable_outcomes",
            )

        degraded_patterns = entry.get("degraded_outcome_patterns")
        if degraded_patterns is not None:
            declared_outcomes = {expected_outcome}
            if isinstance(acceptable_outcomes, list):
                declared_outcomes.update(
                    outcome for outcome in acceptable_outcomes if isinstance(outcome, str)
                )
            if not isinstance(degraded_patterns, dict):
                _issue(
                    issues,
                    "error",
                    "invalid-degraded-outcome-patterns",
                    "degraded_outcome_patterns must be a mapping",
                    case_id,
                    "degraded_outcome_patterns",
                )
            else:
                for outcome, pattern in degraded_patterns.items():
                    if outcome not in {"data_unavailable", "partial_refusal"}:
                        _issue(
                            issues,
                            "error",
                            "invalid-degraded-outcome-key",
                            "degraded patterns may only classify data_unavailable or "
                            "partial_refusal",
                            case_id,
                            "degraded_outcome_patterns",
                        )
                        continue
                    if outcome not in declared_outcomes:
                        _issue(
                            issues,
                            "error",
                            "undeclared-degraded-outcome",
                            f"degraded pattern outcome {outcome!r} is not declared",
                            case_id,
                            "degraded_outcome_patterns",
                        )
                    if not isinstance(pattern, str) or not pattern:
                        _issue(
                            issues,
                            "error",
                            "invalid-degraded-outcome-regex",
                            "degraded outcome patterns must be non-empty regex strings",
                            case_id,
                            "degraded_outcome_patterns",
                        )
                        continue
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        _issue(
                            issues,
                            "error",
                            "invalid-degraded-outcome-regex",
                            f"degraded outcome regex does not compile: {exc}",
                            case_id,
                            "degraded_outcome_patterns",
                        )

        date_policy = entry.get("date_policy")
        if date_policy is not None and date_policy not in DATE_POLICIES:
            _issue(
                issues,
                "error",
                "invalid-date-policy",
                f"date_policy must be one of {sorted(DATE_POLICIES)}",
                case_id,
                "date_policy",
            )

        has_relative_time = isinstance(query, str) and bool(RELATIVE_TIME_RE.search(query))
        if has_relative_time and date_policy not in {"live", "relative"}:
            _issue(
                issues,
                "warning",
                "undeclared-relative-time",
                "query uses relative/current time but date_policy is not live or relative",
                case_id,
                "date_policy",
            )
        injected_scenario = bool(
            entry.get("faults") or entry.get("fixture_id") or entry.get("fixture")
        )
        if (date_policy in {"live", "relative"} or has_relative_time) and not injected_scenario:
            case_timezone = entry.get("timezone") or global_timezone
            if not isinstance(case_timezone, str):
                _issue(
                    issues,
                    "warning",
                    "missing-timezone",
                    "live/relative cases should declare timezone",
                    case_id,
                    "timezone",
                )
            else:
                try:
                    ZoneInfo(case_timezone)
                except ZoneInfoNotFoundError:
                    _issue(
                        issues,
                        "error",
                        "invalid-timezone",
                        f"unknown IANA timezone {case_timezone!r}",
                        case_id,
                        "timezone",
                    )
            freshness = entry.get("freshness") if isinstance(entry.get("freshness"), dict) else {}
            max_age = entry.get("max_age_seconds", freshness.get("max_age_seconds"))
            if date_policy == "live" and (
                isinstance(max_age, bool) or not isinstance(max_age, int) or max_age <= 0
            ):
                _issue(
                    issues,
                    "warning",
                    "missing-max-age",
                    "live/relative cases should declare a positive max_age_seconds",
                    case_id,
                    "max_age_seconds",
                )
            if date_policy == "live" and freshness.get("as_of_required") is not True:
                _issue(
                    issues,
                    "error",
                    "live-missing-as-of-required",
                    "live cases must declare freshness.as_of_required: true so the primary "
                    "answer's freshness is enforced, not just checkpoint reuse",
                    case_id,
                    "freshness.as_of_required",
                )
            if isinstance(query, str):
                old_years = sorted(
                    {int(year) for year in YEAR_RE.findall(query) if int(year) < as_of.year}
                )
                if old_years:
                    _issue(
                        issues,
                        "warning",
                        "possibly-stale-year",
                        f"live/relative query contains prior year(s) {old_years}; verify this is a lookback",
                        case_id,
                        "query",
                    )
        if date_policy == "frozen":
            has_immutable_hash = any(
                isinstance(entry.get(field), str) and entry.get(field)
                for field in ("data_hash", "fixture_hash")
            )
            has_oracle = isinstance(entry.get("oracle_id"), str) and bool(entry.get("oracle_id"))
            has_provider_contract = isinstance(entry.get("data_contract_id"), str) and bool(
                entry.get("data_contract_id")
            )
            revisions_declared = entry.get("provider_revisions_allowed") is True
            if has_provider_contract and not revisions_declared and not has_immutable_hash:
                _issue(
                    issues,
                    "warning",
                    "provider-revision-policy-missing",
                    "fixed provider contracts must declare provider_revisions_allowed: true "
                    "unless immutable data is hash-pinned",
                    case_id,
                    "provider_revisions_allowed",
                )
            elif not (
                has_immutable_hash or has_oracle or (has_provider_contract and revisions_declared)
            ):
                _issue(
                    issues,
                    "warning",
                    "unversioned-frozen-data",
                    "frozen cases need data_hash/fixture_hash, oracle_id, or a revision-aware "
                    "data_contract_id",
                    case_id,
                    "date_policy",
                )

        expires_on = entry.get("expires_on")
        if expires_on is not None:
            expires = _parse_iso_date(expires_on)
            if expires is None:
                _issue(
                    issues,
                    "error",
                    "invalid-expiry",
                    "expires_on must be an ISO date (YYYY-MM-DD)",
                    case_id,
                    "expires_on",
                )
            elif expires < as_of:
                _issue(
                    issues,
                    "error",
                    "expired-case",
                    f"case expired on {expires.isoformat()} (as of {as_of.isoformat()})",
                    case_id,
                    "expires_on",
                )

        cost = _api_cost(entry)
        if cost is None:
            _issue(
                issues,
                "error",
                "invalid-api-cost",
                "estimated_api_calls must be >= 0 and repeat must be >= 1",
                case_id,
                "estimated_api_calls",
            )
        elif tier in tier_costs and not entry.get("disabled"):
            tier_costs[tier] += cost
        estimate_contract = _model_request_estimate_floor(entry)
        if estimate_contract is None:
            _issue(
                issues,
                "error",
                "invalid-model-request-contract",
                "max_cli_turns and specialist/poll ceilings must be internally consistent",
                case_id,
                "max_cli_turns",
            )
        elif isinstance(cost, int) and cost < estimate_contract[1]:
            _issue(
                issues,
                "error",
                "model-requests-underestimated",
                f"estimated_api_calls {cost} is below the minimum planning estimate "
                f"{estimate_contract[1]} for {estimate_contract[0]} CLI turn(s)",
                case_id,
                "estimated_api_calls",
            )

        repeat = entry.get("repeat", 1)
        if isinstance(repeat, int) and not isinstance(repeat, bool) and repeat > 1:
            _issue(
                issues,
                "error",
                "paid-repeat-disallowed",
                "the paid E2E runner executes each case once; keep repeated/stochastic "
                "coverage in src/obai/evaluation/test_cases/suite.yaml",
                case_id,
                "repeat",
            )

        chain_from = entry.get("chain_from")
        if chain_from is not None and not isinstance(chain_from, str):
            _issue(
                issues,
                "error",
                "invalid-chain-parent",
                "chain_from must be a case id string",
                case_id,
                "chain_from",
            )
        required_parent_outcomes = entry.get("chain_requires_parent_outcomes")
        if required_parent_outcomes is not None and (
            not isinstance(chain_from, str)
            or not isinstance(required_parent_outcomes, list)
            or not required_parent_outcomes
            or not all(outcome in EXPECTED_OUTCOMES for outcome in required_parent_outcomes)
        ):
            _issue(
                issues,
                "error",
                "invalid-chain-outcome-gate",
                "chain_requires_parent_outcomes needs chain_from and a non-empty outcome list",
                case_id,
                "chain_requires_parent_outcomes",
            )

        for field in (
            "expected_tools",
            "expected_skills",
            "expected_skills_absent",
            "allowed_extras",
        ):
            value = entry.get(field)
            if value is not None and (
                not isinstance(value, list) or not all(isinstance(item, str) for item in value)
            ):
                _issue(
                    issues,
                    "error",
                    "invalid-string-list",
                    f"{field} must be a list of strings",
                    case_id,
                    field,
                )
        _validate_assertions(entry, issues, case_id)
        _validate_async_contract(entry, issues, case_id)

    for case_id, case in cases_by_id.items():
        parent_id = case.get("chain_from")
        if not isinstance(parent_id, str):
            continue
        if parent_id not in cases_by_id:
            _issue(
                issues,
                "error",
                "missing-chain-parent",
                f"chain_from references unknown case {parent_id!r}",
                case_id,
                "chain_from",
            )
            continue
        parent_acceptable = cases_by_id[parent_id].get("acceptable_outcomes")
        parent_can_degrade = isinstance(parent_acceptable, list) and any(
            outcome != "success" for outcome in parent_acceptable
        )
        if parent_can_degrade and not case.get("chain_requires_parent_outcomes"):
            _issue(
                issues,
                "error",
                "chain-gate-required",
                f"parent {parent_id!r} declares non-success acceptable_outcomes; declare "
                "chain_requires_parent_outcomes so this child is skipped instead of run against "
                "a missing parent result",
                case_id,
                "chain_requires_parent_outcomes",
            )
    _detect_cycles(cases_by_id, issues)

    suite_config = raw.get("suite", {})
    if suite_config is not None and not isinstance(suite_config, dict):
        _issue(issues, "error", "invalid-suite-config", "suite must be a mapping", field="suite")
    elif isinstance(suite_config, dict):
        enforce_exact = suite_config.get("enforce_exact_tier_budgets", False)
        if not isinstance(enforce_exact, bool):
            _issue(
                issues,
                "error",
                "invalid-exact-tier-policy",
                "suite.enforce_exact_tier_budgets must be a boolean",
                field="suite.enforce_exact_tier_budgets",
            )
            enforce_exact = False
        tier_counts = {
            tier: sum(
                1
                for case in cases
                if isinstance(case, dict) and not case.get("disabled") and _case_tier(case) == tier
            )
            for tier in TIERS
        }
        for tier in TIERS:
            budget_key = f"{tier}_max_api_calls"
            budget = suite_config.get(budget_key)
            if budget is not None and (
                isinstance(budget, bool) or not isinstance(budget, int) or budget < 0
            ):
                _issue(
                    issues,
                    "error",
                    "invalid-tier-budget",
                    f"suite.{budget_key} must be a non-negative integer",
                    field=f"suite.{budget_key}",
                )
            elif isinstance(budget, int) and enforce_exact and tier_costs[tier] != budget:
                _issue(
                    issues,
                    "error",
                    "tier-budget-mismatch",
                    f"{tier} estimates {tier_costs[tier]} model requests; exact budget is {budget}",
                    field=f"suite.{budget_key}",
                )
            elif isinstance(budget, int) and tier_costs[tier] > budget:
                _issue(
                    issues,
                    "warning",
                    "tier-budget-exceeded",
                    f"{tier} estimates {tier_costs[tier]} model requests, over budget {budget}",
                    field=f"suite.{budget_key}",
                )
            max_cases_key = f"{tier}_max_cases"
            max_cases = suite_config.get(max_cases_key)
            if max_cases is None:
                continue
            if isinstance(max_cases, bool) or not isinstance(max_cases, int) or max_cases < 0:
                _issue(
                    issues,
                    "error",
                    "invalid-tier-case-budget",
                    f"suite.{max_cases_key} must be a non-negative integer",
                    field=f"suite.{max_cases_key}",
                )
            elif enforce_exact and tier_counts[tier] != max_cases:
                _issue(
                    issues,
                    "error",
                    "tier-case-count-mismatch",
                    f"{tier} contains {tier_counts[tier]} cases; exact count is {max_cases}",
                    field=f"suite.{max_cases_key}",
                )
            elif tier_counts[tier] > max_cases:
                _issue(
                    issues,
                    "warning",
                    "tier-case-budget-exceeded",
                    f"{tier} contains {tier_counts[tier]} cases, over budget {max_cases}",
                    field=f"suite.{max_cases_key}",
                )

    suite_budgets = raw.get("suite_budgets", {})
    if suite_budgets is not None and not isinstance(suite_budgets, dict):
        _issue(
            issues,
            "error",
            "invalid-suite-budgets",
            "suite_budgets must be a mapping",
            field="suite_budgets",
        )
    elif isinstance(suite_budgets, dict):
        tier_counts = {
            tier: sum(
                1
                for case in cases
                if isinstance(case, dict) and not case.get("disabled") and _case_tier(case) == tier
            )
            for tier in TIERS
        }
        for tier, raw_budget in suite_budgets.items():
            if tier not in TIERS:
                _issue(
                    issues,
                    "error",
                    "invalid-budget-tier",
                    f"unknown suite budget tier {tier!r}",
                    field=f"suite_budgets.{tier}",
                )
                continue
            if not isinstance(raw_budget, dict):
                _issue(
                    issues,
                    "error",
                    "invalid-tier-budget",
                    f"suite_budgets.{tier} must be a mapping",
                    field=f"suite_budgets.{tier}",
                )
                continue
            max_cases = raw_budget.get("max_cases")
            max_calls = raw_budget.get("max_estimated_api_calls")
            if isinstance(max_cases, bool) or not isinstance(max_cases, int) or max_cases < 0:
                _issue(
                    issues,
                    "error",
                    "invalid-tier-case-budget",
                    f"suite_budgets.{tier}.max_cases must be a non-negative integer",
                    field=f"suite_budgets.{tier}.max_cases",
                )
            elif tier_counts[tier] > max_cases:
                _issue(
                    issues,
                    "warning",
                    "tier-case-budget-exceeded",
                    f"{tier} contains {tier_counts[tier]} cases, over budget {max_cases}",
                    field=f"suite_budgets.{tier}.max_cases",
                )
            if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 0:
                _issue(
                    issues,
                    "error",
                    "invalid-tier-budget",
                    f"suite_budgets.{tier}.max_estimated_api_calls must be a non-negative integer",
                    field=f"suite_budgets.{tier}.max_estimated_api_calls",
                )
            elif tier_costs[tier] > max_calls:
                _issue(
                    issues,
                    "warning",
                    "tier-budget-exceeded",
                    f"{tier} estimates {tier_costs[tier]} model requests, over budget {max_calls}",
                    field=f"suite_budgets.{tier}.max_estimated_api_calls",
                )

    # Near duplicates are warnings, not errors: their distinct assertions may be intentional.
    unique_queries = list(seen_queries.items())
    for i, (query_a, id_a) in enumerate(unique_queries):
        for query_b, id_b in unique_queries[i + 1 :]:
            if len(query_a) < 40 or len(query_b) < 40:
                continue
            similarity = SequenceMatcher(None, query_a, query_b, autojunk=False).ratio()
            if similarity >= 0.94:
                _issue(
                    issues,
                    "warning",
                    "near-duplicate-query",
                    f"query is {similarity:.0%} similar to {id_a}; consider moving one to extended",
                    id_b,
                    "query",
                )

    return issues


def load_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path, help="Path to cases YAML")
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="Override the suite-timezone date (YYYY-MM-DD)",
    )
    parser.add_argument("--strict", action="store_true", help="Treat warnings as failures")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    try:
        raw = load_yaml(args.cases)
    except (OSError, yaml.YAMLError) as exc:
        print(f"ERROR: cannot load {args.cases}: {exc}", file=sys.stderr)
        return 2

    issues = lint_suite(raw, as_of=args.as_of)
    if args.json_output:
        print(json.dumps({"issues": [issue.to_dict() for issue in issues]}, indent=2))
    else:
        for issue in issues:
            location = f" [{issue.case_id}]" if issue.case_id else ""
            print(f"{issue.severity.upper()} {issue.code}{location}: {issue.message}")
        error_count = sum(issue.severity == "error" for issue in issues)
        warning_count = sum(issue.severity == "warning" for issue in issues)
        print(f"{error_count} error(s), {warning_count} warning(s)")

    if any(issue.severity == "error" for issue in issues):
        return 1
    if args.strict and issues:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
