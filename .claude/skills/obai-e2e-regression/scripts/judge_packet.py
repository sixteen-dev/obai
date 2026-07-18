#!/usr/bin/env python3
"""Deterministically judge one OBaI E2E packet against its case contract.

This judge intentionally does not use an LLM.  It handles hard invariants and
classifies uncertainty; qualitative financial quality remains a separate
semantic-review concern.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

PASS_VERDICTS = frozenset({"pass", "pass_degraded"})
PRODUCT_VERDICTS = frozenset({"fail_product", "needs_semantic_review"})
INCONCLUSIVE_VERDICTS = frozenset(
    {"inconclusive_provider", "inconclusive_harness", "inconclusive_missing_evidence"}
)
ALL_VERDICTS = PASS_VERDICTS | PRODUCT_VERDICTS | INCONCLUSIVE_VERDICTS

# This regex runs over free response text, which for a financial tool routinely
# NARRATES third-party outages ("traders were unable to connect to Robinhood",
# "Cloudflare's servers were temporarily unavailable"). Provider-outage prose is
# therefore indistinguishable from a legitimate answer, so we do NOT match
# code-less outage vocabulary here — OBaI's own provider failures are caught
# structurally by the isError/status span backstop. In text we match only
# machine-unambiguous markers: a code in real HTTP context, or the original
# textual signals. A bare number is excluded because it collides with financial
# figures ("S&P 500", "504-session", "429 open positions", "403(b)").
_HTTP_ERR_CODE = r"(?:401|403|408|425|429|500|502|503|504)"
# Separator: whitespace/punctuation/quotes only (\x22 is a double quote), so
# '"status_code": 504' matches but a code trailing English words does not.
_HTTP_LABEL_SEP = r"[\s:=#/.,()\x22'\[\]-]{0,5}"
_HTTP_STATUS_FAILURE = (
    r"(?:\bhttps?\b(?:/\d(?:\.\d)?)?|\bstatus[_ ]?code\b|"
    r"\bfailed\s+with\s+status\b|\berror\s+code\b)"
    # \b before the code: an http version digit ("http/1.1") must not fuse onto
    # the status number ("http/1.1504") when the separator is zero-width.
    + _HTTP_LABEL_SEP + r"\b" + _HTTP_ERR_CODE + r"\b"
    r"|\b" + _HTTP_ERR_CODE + r"\s+"
    r"(?:unauthorized|forbidden|request\s+timeout|too\s+many\s+requests|"
    r"internal\s+server\s+error|bad\s+gateway|service\s+unavailable|gateway\s+timeout)\b"
)
PROVIDER_FAILURE_RE = re.compile(
    r"(?:" + _HTTP_STATUS_FAILURE + r"|"
    r"permission denied|not entitled|"
    r"rate[ -]?limit|quota exceeded|provider (?:is )?unavailable|service unavailable|"
    r"provider.{0,30}(?:error|failed|failure|exploded|denied|timeout)|"
    r"(?:missing|invalid|incorrect|expired) api key|"
    r"api key (?:missing|invalid|incorrect|expired|required)|"
    r"authentication failed|upstream (?:error|timeout)|"
    r"connection (?:error|failed|refused|reset)|insufficient[_ ]quota|"
    r"internal server error|too many requests|"
    r"model\b.{0,80}\b(?:does not exist|not found|no access)|do not have access)",
    re.IGNORECASE,
)
REFUSAL_RE = re.compile(
    r"\b(?:cannot|can't|unable to|unsupported|not supported|refuse|won't|will not)\b",
    re.IGNORECASE,
)
DATA_UNAVAILABLE_RE = re.compile(
    r"\b(?:no (?:current )?data|data (?:is )?unavailable|no results?|no matching|"
    r"no directly resolving|could not retrieve|couldn't retrieve|not available)\b",
    re.IGNORECASE,
)
SPECIALIST_ERROR_RE = re.compile(
    r"\b(?:specialist error|tool error|provider error|execution error|status.?error)\b",
    re.IGNORECASE,
)
ASYNC_JOB_ID_RE = re.compile(
    r"\bjob[_ ]?id\b\s*[:=]\s*`?"
    r"([A-Za-z0-9](?:[A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?)`?",
    re.IGNORECASE,
)
ASYNC_PENDING_STATUSES = frozenset({"queued", "running", "pending", "in_progress"})
ASYNC_FAILURE_STATUSES = frozenset({"failed", "cancelled", "expired", "not_found"})
STRUCTURED_ERROR_STATUSES = frozenset({"error", "failed", "failure"})
MAX_STRUCTURED_OUTPUT_JSON_CHARS = 65_536
SKILL_LINE_RE = re.compile(r"^-\s+([A-Za-z0-9_.:-]+):\s+\w+", re.MULTILINE)
_MISSING = object()

# These patterns intentionally cover only claims with a reliable lexical
# signature.  Claims requiring arithmetic, source lineage, or cross-turn state
# must arrive as structured evidence; otherwise the verdict is semantic review
# or missing evidence, never pass.
REQUIRED_CLAIM_PATTERNS: dict[str, re.Pattern[str]] = {
    "quote_timestamp": re.compile(
        r"(?:quote|price).{0,30}timestamp|timestamp.{0,30}(?:quote|price)", re.I | re.S
    ),
    "underlying_timestamp": re.compile(r"underlying.{0,30}timestamp", re.I | re.S),
    "publication_timestamp": re.compile(
        r"publish(?:ed|ed at|cation).{0,30}(?:time|timestamp)", re.I | re.S
    ),
    "publication_date": re.compile(r"publication date|published (?:on|at)", re.I),
    "direct_source_url": re.compile(r"https?://[^\s)>]+", re.I),
    "reporting_currency": re.compile(r"report(?:ed|ing) currency|currency\s*[:=]", re.I),
    "reported_currency": re.compile(r"report(?:ed|ing) currency|currency\s*[:=]", re.I),
    "fiscal_period": re.compile(r"fiscal (?:period|year|quarter)|FY\s*20\d{2}", re.I),
    "occ_symbol": re.compile(r"\b[A-Z]{1,6}\d{6}[CP]\d{8}\b|OCC symbol", re.I),
    "occ_symbols": re.compile(r"\b[A-Z]{1,6}\d{6}[CP]\d{8}\b|OCC symbols", re.I),
    "job_id": re.compile(r"\bjob[_ ]?id\b", re.I),
    "artifact_id": re.compile(r"\bartifact[_ ]?id\b", re.I),
    "fingerprint": re.compile(r"\bfingerprint\b", re.I),
    "product_id": re.compile(r"\bproduct[_ ]?id\b", re.I),
    "source_quality": re.compile(r"\bsource[_ -]?quality\b", re.I),
    "blocking_quality_warning": re.compile(r"\bblocking[_ -]?quality[_ -]?warning\b", re.I),
    "incomplete_range": re.compile(
        r"incomplete (?:range|interval|candle)|future (?:range|interval|candle)", re.I
    ),
    "symbol_not_found": re.compile(
        r"symbol.{0,20}(?:not found|invalid|unknown)|(?:invalid|unknown) symbol", re.I | re.S
    ),
    "live_order_refusal": re.compile(
        r"(?:cannot|can't|will not|unable to).{0,40}(?:live )?order", re.I | re.S
    ),
    "supported_paper_fallback": re.compile(
        r"paper (?:simulation|trade|entry|checklist|alternative)", re.I
    ),
    "bid": re.compile(r"\bbid\b", re.I),
    "ask": re.compile(r"\bask\b", re.I),
    "spread_bps": re.compile(r"spread.{0,15}(?:bps|basis points)", re.I | re.S),
    "missing_pct": re.compile(r"\bmissing[_ ]?pct\b|missing percentage", re.I),
    "gap_ranges": re.compile(r"\bgap[_ ]?ranges?\b", re.I),
    "slug": re.compile(r"\bslug\b", re.I),
    "earnings_date": re.compile(r"earnings date|reports? (?:on|after|before)", re.I),
    "breakeven": re.compile(r"break[- ]?even", re.I),
    "max_gain": re.compile(r"max(?:imum)? (?:gain|profit)", re.I),
    "max_loss": re.compile(r"max(?:imum)? loss", re.I),
    "scenario_grid": re.compile(r"scenario (?:grid|table)|underlying price scenarios", re.I),
}

FORBIDDEN_CLAIM_PATTERNS: dict[str, re.Pattern[str]] = {
    "artifact_exported": re.compile(
        r"artifact.{0,50}(?:was |successfully )?(?:exported|created|generated)", re.I | re.S
    ),
    "future_candle_complete": re.compile(
        r"(?:future|tomorrow(?:'s)?).{0,80}(?:candle|interval|range).{0,80}(?:complete|full coverage)",
        re.I | re.S,
    ),
    "live_order_placed": re.compile(
        r"(?:live )?(?:buy|sell|market|limit)?\s*order.{0,30}(?:placed|submitted|executed|filled)",
        re.I | re.S,
    ),
    "new_backtest_job": re.compile(
        r"(?:ran|created|started|submitted).{0,30}new backtest", re.I | re.S
    ),
    "memory_reconstructed_artifact": re.compile(
        r"reconstruct(?:ed|ing)?.{0,30}(?:artifact|memory)", re.I | re.S
    ),
    "shared_expiry_payoff": re.compile(
        r"shared[- ]expiry.{0,30}(?:payoff|max(?:imum)? (?:gain|loss)|breakeven)", re.I | re.S
    ),
}

FINANCIAL_SPECIALIST_TOOLS = frozenset(
    {
        "market_data_analysis",
        "fundamentals_analysis",
        "events_news_analysis",
        "options_analysis",
        "portfolio_analysis",
        "research_analysis",
        "strategy_analysis",
        "prediction_market_analysis",
        "crypto_analysis",
        "screener_lookup",
    }
)


@dataclass
class JudgeResult:
    case_id: str
    verdict: str
    expected_outcome: str
    observed_outcome: str
    checks_passed: list[str] = field(default_factory=list)
    checks_failed: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    observed_tools: list[str] = field(default_factory=list)
    observed_skills: list[str] = field(default_factory=list)
    unexecuted_assertions: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AsyncContext:
    """The evidence view and terminal classification for an async case."""

    packet: dict[str, Any]
    early_verdict: str | None = None
    reason: str = ""
    forced_outcome: str | None = None


def _dig(value: object, path: str) -> object:
    """Resolve a conservative dot/bracket path without executing expressions."""
    if not path:
        return value
    tokens = re.findall(r"[^.\[\]]+", path)
    current = value
    for token in tokens:
        if isinstance(current, dict):
            if token not in current:
                return _MISSING
            current = current[token]
        elif isinstance(current, list) and token.isdigit():
            index = int(token)
            if index >= len(current):
                return _MISSING
            current = current[index]
        else:
            return _MISSING
    return current


def _present(value: object) -> bool:
    if value is _MISSING or value is None:
        return False
    if isinstance(value, (str, list, tuple, dict, set)):
        return bool(value)
    return True


def _explicit_structured_error_output(output: object) -> dict[str, Any] | None:
    """Return only an explicit top-level structured error payload.

    MCP-style ``isError: true`` (plus the snake-case spelling) and exact failure
    status values are unambiguous. Generic ``error`` keys and prose containing
    the word "error" are deliberately ignored because financial results often
    report tracking/error metrics. JSON strings are parsed only when they are a
    small object, never executed or recursively searched.
    """
    payload: object = output
    if isinstance(output, str):
        candidate = output.strip()
        if (
            not candidate.startswith("{")
            or not candidate.endswith("}")
            or len(candidate) > MAX_STRUCTURED_OUTPUT_JSON_CHARS
        ):
            return None
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    if payload.get("isError") is True or payload.get("is_error") is True:
        return payload
    status = payload.get("status")
    if isinstance(status, str) and status.strip().casefold() in STRUCTURED_ERROR_STATUSES:
        return payload
    return None


def _span_error_evidence(span: dict[str, Any]) -> tuple[str, object] | None:
    error_info = span.get("error_info")
    if _present(error_info):
        return "error_info", error_info
    structured = _explicit_structured_error_output(span.get("output", _MISSING))
    if structured is not None:
        return "structured_output", structured
    return None


def _serialise_error(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True)


def _stdout_json(packet: dict[str, Any]) -> dict[str, Any]:
    cli = packet.get("cli")
    if not isinstance(cli, dict):
        return {}
    stdout = cli.get("stdout_json")
    return stdout if isinstance(stdout, dict) else {}


def _response_text(packet: dict[str, Any]) -> str:
    stdout = _stdout_json(packet)
    error = stdout.get("error")
    error_message = error.get("message") if isinstance(error, dict) else None
    for candidate in (
        packet.get("final_response"),
        packet.get("response"),
        stdout.get("response"),
        error_message,
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return ""


def _authoritative_spans(packet: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    trace = packet.get("trace")
    if not isinstance(trace, dict) or "spans" not in trace:
        return False, []
    raw = trace.get("spans")
    if not isinstance(raw, list):
        return True, []
    spans = [span for span in raw if isinstance(span, dict)]
    return True, sorted(spans, key=lambda span: str(span.get("start_time") or ""))


def _tool_calls(packet: dict[str, Any]) -> list[dict[str, Any]]:
    has_raw_spans, raw_spans = _authoritative_spans(packet)
    if has_raw_spans:
        return raw_spans
    candidates: list[object] = [
        _stdout_json(packet).get("tool_calls"),
        packet.get("tool_calls"),
        _dig(packet, "evidence.tool_calls"),
    ]
    for candidate in candidates:
        if isinstance(candidate, list) and candidate:
            calls: list[dict[str, Any]] = []
            for item in candidate:
                if isinstance(item, str):
                    calls.append({"tool": item})
                elif isinstance(item, dict):
                    calls.append(item)
            if calls:
                return calls
    return []


def _call_name(call: dict[str, Any]) -> str | None:
    for key in ("tool", "name", "tool_name", "span_name"):
        value = call.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _observed_tools(packet: dict[str, Any]) -> tuple[list[str], bool]:
    has_raw_spans, raw_spans = _authoritative_spans(packet)
    if has_raw_spans:
        names = [name for span in raw_spans if (name := _call_name(span))]
        return names, bool(raw_spans)
    explicit = packet.get("observed_tools")
    if isinstance(explicit, list) and all(isinstance(item, str) for item in explicit):
        return list(explicit), True
    calls = _tool_calls(packet)
    names = [name for call in calls if (name := _call_name(call))]
    return names, bool(calls)


def _skill_name_from_call(call: dict[str, Any]) -> str | None:
    if _call_name(call) != "load_skill":
        return None
    # ``input`` may be double-JSON-encoded (the skill name arrives backslash
    # -escaped and defeats the string regex); the ``load_skill`` ``output``
    # carries the authoritative, unescaped ``skill_name`` for a completed load.
    for container in (
        call,
        call.get("arguments"),
        call.get("args"),
        call.get("input"),
        call.get("output"),
    ):
        if isinstance(container, dict):
            for key in ("skill_name", "skill", "name"):
                value = container.get(key)
                if isinstance(value, str) and value != "load_skill":
                    return value
        elif isinstance(container, str):
            match = re.search(r'"skill_name"\s*:\s*"([^"]+)"', container)
            if match:
                return match.group(1)
    return None


def _observed_skills(packet: dict[str, Any]) -> tuple[list[str], bool]:
    has_raw_spans, raw_spans = _authoritative_spans(packet)
    if has_raw_spans:
        names = [name for span in raw_spans if (name := _skill_name_from_call(span))]
        return list(dict.fromkeys(names)), bool(raw_spans)
    explicit = packet.get("observed_skills")
    if isinstance(explicit, list) and all(isinstance(item, str) for item in explicit):
        return list(explicit), True

    calls = _tool_calls(packet)
    names = [name for call in calls if (name := _skill_name_from_call(call))]
    curated = _dig(packet, "trace.curated")
    if isinstance(curated, str) and curated:
        names.extend(SKILL_LINE_RE.findall(curated))
    # Stable de-duplication keeps the first observed order.
    unique = list(dict.fromkeys(names))
    evidence_available = bool(unique) or any(_call_name(call) == "load_skill" for call in calls)
    return unique, evidence_available


def _assertion_config(case: dict[str, Any]) -> dict[str, Any]:
    nested = case.get("assertions")
    config = dict(nested) if isinstance(nested, dict) else {}
    for assertion_name in ("required_text", "forbidden_text", "required_evidence"):
        if assertion_name not in config and assertion_name in case:
            config[assertion_name] = case[assertion_name]
    return config


def _structured_check(packet: dict[str, Any], groups: Iterable[str], name: str) -> object:
    for group in groups:
        value = _dig(packet, f"evidence.{group}.{name}")
        if value is not _MISSING:
            if isinstance(value, dict) and "passed" in value:
                return value["passed"]
            return value
        value = _dig(packet, f"{group}.{name}")
        if value is not _MISSING:
            if isinstance(value, dict) and "passed" in value:
                return value["passed"]
            return value
    return _MISSING


def _record_structured_boolean(
    result: JudgeResult,
    *,
    label: str,
    value: object,
    missing_is_evidence: bool = True,
) -> None:
    if value is _MISSING:
        if missing_is_evidence:
            result.missing_evidence.append(label)
        else:
            result.unexecuted_assertions.append(label)
    elif value is True:
        result.checks_passed.append(f"{label} passed")
    elif value is False:
        result.checks_failed.append(f"{label} failed")
    else:
        result.missing_evidence.append(f"{label} (non-boolean result)")


def _declared_and_applied_provider_fault(case: dict[str, Any], packet: dict[str, Any]) -> bool:
    """Require positive harness proof before treating a provider failure as expected."""
    faults = case.get("faults")
    if not isinstance(faults, list) or not faults:
        return False
    declared: set[str] = set()
    for fault in faults:
        if isinstance(fault, str):
            declared.add(fault)
        elif isinstance(fault, dict):
            for key in ("id", "name", "provider", "type"):
                value = fault.get(key)
                if isinstance(value, str):
                    declared.add(value)
    applied_raw = packet.get("applied_faults")
    if applied_raw is None:
        applied_raw = _dig(packet, "harness.applied_faults")
    if not isinstance(applied_raw, list):
        return False
    applied: set[str] = set()
    for fault in applied_raw:
        if isinstance(fault, str):
            applied.add(fault)
        elif isinstance(fault, dict):
            for key in ("id", "name", "provider", "type"):
                value = fault.get(key)
                if isinstance(value, str):
                    applied.add(value)
    return bool(declared & applied)


def _matches(text: str, spec: object) -> tuple[bool, str]:
    if isinstance(spec, str):
        return spec.casefold() in text.casefold(), repr(spec)
    if not isinstance(spec, dict):
        return False, repr(spec)
    case_sensitive = spec.get("case_sensitive") is True
    flags = 0 if case_sensitive else re.IGNORECASE
    if isinstance(spec.get("regex"), str):
        pattern = spec["regex"]
        try:
            return bool(re.search(pattern, text, flags)), f"/{pattern}/"
        except re.error:
            return False, f"invalid regex /{pattern}/"
    needle = spec.get("text")
    if isinstance(needle, str):
        haystack = text if case_sensitive else text.casefold()
        expected = needle if case_sensitive else needle.casefold()
        return expected in haystack, repr(needle)
    return False, repr(spec)


def _first_occurrences_ordered(expected: list[str], observed: list[str]) -> bool:
    """Require required tools' first invocations to occur in declared order.

    Unrelated leading calls remain valid, but a later required specialist cannot
    run early and hide that ordering violation behind a duplicate later call.
    """
    try:
        positions = [observed.index(item) for item in expected]
    except ValueError:
        return False
    return all(before < after for before, after in zip(positions, positions[1:]))


def _sequence_satisfied(expected: object, observed: list[str]) -> bool:
    if not isinstance(expected, list) or not expected:
        return True
    if all(isinstance(item, str) for item in expected):
        return _first_occurrences_ordered(expected, observed)

    positions: dict[str, list[int]] = {}
    for index, tool in enumerate(observed):
        positions.setdefault(tool, []).append(index)
    for relation in expected:
        before: str | None = None
        after: str | None = None
        if isinstance(relation, dict):
            before = relation.get("before")
            after = relation.get("after")
        elif (
            isinstance(relation, list)
            and len(relation) == 2
            and all(isinstance(item, str) for item in relation)
        ):
            before, after = relation
        if not before or not after:
            return False
        if before not in positions or after not in positions:
            return False
        if min(positions[before]) >= min(positions[after]):
            return False
    return True


def _error_blob(packet: dict[str, Any], response: str) -> str:
    parts = [response]
    for path in (
        "cli.stderr",
        "error_info",
        "trace.error_info",
        "trace.raw.error_info",
        "evidence.error_info",
        "followup.cli.stderr",
    ):
        value = _dig(packet, path)
        if value is not _MISSING and value is not None:
            parts.append(value if isinstance(value, str) else json.dumps(value, sort_keys=True))
    spans = _dig(packet, "trace.spans")
    if isinstance(spans, list):
        for span in spans:
            if not isinstance(span, dict):
                continue
            error = span.get("error_info")
            if error:
                parts.append(error if isinstance(error, str) else json.dumps(error, sort_keys=True))
    return "\n".join(parts)


def _observed_outcome(case: dict[str, Any], packet: dict[str, Any], response: str) -> str:
    """Classify only structured failures and explicitly declared degraded branches.

    Financial answers routinely contain scoped caveats such as "bid/ask is not
    available" or "this sample cannot be called exhaustive." Treating any such
    phrase as a refusal turns honest, successful answers into false failures.
    Natural-language degraded outcomes are therefore recognized only when the
    case contract explicitly permits that branch; hard text assertions and the
    offline semantic review still determine whether the branch is genuine.
    """
    stdout = _stdout_json(packet)
    if stdout.get("guardrail_rejected") is True or packet.get("guardrail_rejected") is True:
        return "hub_reject"
    errors = _error_blob(packet, "")
    if errors.strip() and SPECIALIST_ERROR_RE.search(errors):
        return "specialist_error"
    declared_outcomes = {str(case.get("expected_outcome", "success"))}
    alternatives = case.get("acceptable_outcomes")
    if isinstance(alternatives, list):
        declared_outcomes.update(value for value in alternatives if isinstance(value, str))
    degraded_patterns = case.get("degraded_outcome_patterns")
    if isinstance(degraded_patterns, dict):
        for outcome in ("data_unavailable", "partial_refusal"):
            pattern = degraded_patterns.get(outcome)
            if outcome in declared_outcomes and isinstance(pattern, str) and pattern:
                if re.search(pattern, response):
                    return outcome
    if "data_unavailable" in declared_outcomes and DATA_UNAVAILABLE_RE.search(response):
        return "data_unavailable"
    if "partial_refusal" in declared_outcomes and REFUSAL_RE.search(response):
        return "partial_refusal"
    return "success"


def _normalise_async_status(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _extract_async_job_ids(text: str) -> list[str]:
    return list(dict.fromkeys(ASYNC_JOB_ID_RE.findall(text or "")))


def _contains_exact_token(text: str, token: str) -> bool:
    """Match a job ID without accepting a prefix of a different job ID."""
    if not text or not token:
        return False
    pattern = rf"(?<![A-Za-z0-9._:-]){re.escape(token)}(?![A-Za-z0-9._:-])"
    return re.search(pattern, text) is not None


def _has_hard_outcome_assertions(case: dict[str, Any]) -> bool:
    assertions = _assertion_config(case)
    return bool(assertions.get("required_text") or assertions.get("forbidden_text"))


def _poll_packet(packet: dict[str, Any], poll: dict[str, Any]) -> dict[str, Any]:
    """Overlay the last poll's result evidence while retaining root metadata."""
    selected = dict(packet)
    for key in ("cli", "trace", "final_response", "response", "evidence"):
        if key in poll:
            selected[key] = poll[key]
    # The root harness completed the orchestration; the selected poll's own CLI
    # and trace now determine whether its evidence was captured successfully.
    selected["harness_status"] = "completed"
    selected["harness_exit_code"] = 0
    return selected


def _async_context(case: dict[str, Any], packet: dict[str, Any]) -> AsyncContext:
    """Validate async continuity and select only the final deterministic evidence.

    Async stubs are not answers.  A completed case is judged against the final
    poll, while unfinished polling is a harness inconclusive and terminal job
    failure is a product failure unless the case explicitly contracts for a
    specialist error with hard text assertions.
    """
    if case.get("expect_async_job") is not True:
        return AsyncContext(packet=packet)

    followup = packet.get("followup")
    if not isinstance(followup, dict):
        return AsyncContext(
            packet=packet,
            early_verdict="inconclusive_harness",
            reason="async case has no follow-up evidence",
        )

    initial_job_ids = _extract_async_job_ids(_response_text(packet))
    job_id = followup.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        return AsyncContext(
            packet=packet,
            early_verdict="inconclusive_harness",
            reason="async follow-up job_id is missing",
        )
    if initial_job_ids != [job_id]:
        return AsyncContext(
            packet=packet,
            early_verdict="inconclusive_harness",
            reason=(
                "initial and follow-up job IDs do not match unambiguously: "
                f"initial={initial_job_ids!r}, followup={job_id!r}"
            ),
        )

    polls_raw = followup.get("polls")
    polls = polls_raw if isinstance(polls_raw, list) else []
    initial_terminal = followup.get("initial_terminal") is True
    if initial_terminal and polls:
        return AsyncContext(
            packet=packet,
            early_verdict="inconclusive_harness",
            reason="initial-terminal async evidence unexpectedly contains follow-up polls",
        )
    if not initial_terminal and not polls:
        return AsyncContext(
            packet=packet,
            early_verdict="inconclusive_harness",
            reason="async case has no poll evidence",
        )

    selected = packet
    if polls:
        for index, raw_poll in enumerate(polls):
            if not isinstance(raw_poll, dict):
                return AsyncContext(
                    packet=packet,
                    early_verdict="inconclusive_harness",
                    reason=f"async poll {index} is not an evidence object",
                )
            poll_query = raw_poll.get("query")
            marked_query = raw_poll.get("marked_query")
            query_text = poll_query if isinstance(poll_query, str) else ""
            marked_text = marked_query if isinstance(marked_query, str) else ""
            if not (
                _contains_exact_token(query_text, job_id)
                or _contains_exact_token(marked_text, job_id)
            ):
                return AsyncContext(
                    packet=packet,
                    early_verdict="inconclusive_harness",
                    reason=f"async poll {index} is not correlated to job {job_id!r}",
                )

            declared_response_ids = raw_poll.get("response_job_ids")
            if isinstance(declared_response_ids, list) and declared_response_ids != [job_id]:
                return AsyncContext(
                    packet=packet,
                    early_verdict="inconclusive_harness",
                    reason=(
                        f"async poll {index} did not return exactly job ID {job_id!r}: "
                        f"{declared_response_ids!r}"
                    ),
                )
            if raw_poll.get("job_id_matches") is False:
                return AsyncContext(
                    packet=packet,
                    early_verdict="inconclusive_harness",
                    reason=f"async poll {index} explicitly failed job-ID correlation",
                )
            response_ids = _extract_async_job_ids(_response_text(raw_poll))
            if response_ids != [job_id]:
                return AsyncContext(
                    packet=packet,
                    early_verdict="inconclusive_harness",
                    reason=(
                        f"async poll {index} response did not echo exactly job ID "
                        f"{job_id!r}: {response_ids!r}"
                    ),
                )
        selected = _poll_packet(packet, polls[-1])

    if followup.get("timed_out") is True or followup.get("poll_limit_reached") is True:
        return AsyncContext(
            packet=selected,
            early_verdict="inconclusive_harness",
            reason="async job did not reach a terminal state within the polling budget",
        )
    if followup.get("evidence_complete") is not True:
        return AsyncContext(
            packet=selected,
            early_verdict="inconclusive_harness",
            reason="async follow-up evidence is incomplete",
        )

    status = _normalise_async_status(followup.get("status"))
    if status in ASYNC_PENDING_STATUSES:
        return AsyncContext(
            packet=selected,
            early_verdict="inconclusive_harness",
            reason=f"async job remains {status}",
        )
    if polls:
        final_poll_status = _normalise_async_status(polls[-1].get("status"))
        if final_poll_status != status:
            return AsyncContext(
                packet=selected,
                early_verdict="inconclusive_harness",
                reason=(
                    "async follow-up status disagrees with its final poll: "
                    f"followup={status!r}, poll={final_poll_status!r}"
                ),
            )

    if status == "completed":
        return AsyncContext(packet=selected)
    if status in ASYNC_FAILURE_STATUSES:
        if case.get("expected_outcome") == "specialist_error" and _has_hard_outcome_assertions(
            case
        ):
            return AsyncContext(packet=selected, forced_outcome="specialist_error")
        return AsyncContext(
            packet=selected,
            early_verdict="fail_product",
            reason=f"async job reached terminal failure status {status}",
        )
    return AsyncContext(
        packet=selected,
        early_verdict="inconclusive_harness",
        reason=f"async follow-up has invalid terminal status {status!r}",
    )


def _expected_span_errors(
    packet: dict[str, Any],
    expected_tools: Iterable[object],
    result: JudgeResult,
) -> tuple[list[str], list[str]]:
    """Validate lossless expected-specialist spans and classify their errors."""
    has_raw_spans, spans = _authoritative_spans(packet)
    if not has_raw_spans or not spans:
        return [], []

    provider_errors: list[str] = []
    product_errors: list[str] = []
    for expected in expected_tools:
        if not isinstance(expected, str) or not expected:
            continue
        matching = [span for span in spans if _call_name(span) == expected]
        for index, span in enumerate(matching):
            error_evidence = _span_error_evidence(span)
            if error_evidence is not None:
                source, error = error_evidence
                serialized = _serialise_error(error)
                label = f"{expected}[{index}] {source}={serialized}"
                if PROVIDER_FAILURE_RE.search(serialized):
                    provider_errors.append(label)
                else:
                    product_errors.append(label)
                continue
            if not _present(span.get("output", _MISSING)):
                result.missing_evidence.append(f"specialist_output:{expected}[{index}]")
    return provider_errors, product_errors


def _additional_specialist_span_errors(
    packet: dict[str, Any], expected_tools: Iterable[object]
) -> tuple[list[str], list[str]]:
    """Classify errors from allowed or unexpected outer specialists too."""
    has_raw_spans, spans = _authoritative_spans(packet)
    if not has_raw_spans:
        return [], []
    expected = {tool for tool in expected_tools if isinstance(tool, str)}
    provider_errors: list[str] = []
    product_errors: list[str] = []
    for span in spans:
        name = _call_name(span)
        if name not in FINANCIAL_SPECIALIST_TOOLS or name in expected:
            continue
        error_evidence = _span_error_evidence(span)
        if error_evidence is None:
            continue
        source, error = error_evidence
        serialized = _serialise_error(error)
        label = f"{name} {source}={serialized}"
        if PROVIDER_FAILURE_RE.search(serialized):
            provider_errors.append(label)
        else:
            product_errors.append(label)
    return provider_errors, product_errors


def _required_skill_span_evidence(
    packet: dict[str, Any],
    expected_skills: Iterable[object],
    result: JudgeResult,
) -> tuple[set[str] | None, list[str], list[str]]:
    """Validate mandatory skill loads without blanket-failing hub retries.

    ``None`` for the successful-skill set means lossless spans are unavailable
    and the caller may use legacy curated evidence. With authoritative spans, a
    load counts only when it has nonempty output and no error. Empty output is
    missing evidence; explicit errors are provider-inconclusive or product
    failures according to their contents.
    """
    has_raw_spans, spans = _authoritative_spans(packet)
    if not has_raw_spans:
        return None, [], []

    required = {skill for skill in expected_skills if isinstance(skill, str)}
    successful: set[str] = set()
    provider_errors: list[str] = []
    product_errors: list[str] = []
    for span in spans:
        skill = _skill_name_from_call(span)
        if skill not in required:
            continue
        error_evidence = _span_error_evidence(span)
        if error_evidence is not None:
            source, error = error_evidence
            serialized = _serialise_error(error)
            label = f"{skill} {source}={serialized}"
            if PROVIDER_FAILURE_RE.search(serialized):
                provider_errors.append(label)
            else:
                product_errors.append(label)
            continue
        if not _present(span.get("output", _MISSING)):
            label = f"skill_output:{skill}"
            if label not in result.missing_evidence:
                result.missing_evidence.append(label)
            continue
        successful.add(skill)
    return successful, provider_errors, product_errors


def _async_prior_turn_span_issues(
    case: dict[str, Any],
    root_packet: dict[str, Any],
    result: JudgeResult,
) -> tuple[list[str], list[str]]:
    """Validate routing and execution evidence hidden by the final-poll overlay.

    The normal judge path validates the selected turn (the initial turn for an
    initial-terminal job, otherwise the final poll).  The hub's async follow-up
    contract requires the declared specialist and routing skill on every CLI
    turn, so this helper validates only the earlier turns and inspects every
    financial-specialist/required-skill error in them.
    """
    if case.get("expect_async_job") is not True:
        return [], []
    followup = root_packet.get("followup")
    if not isinstance(followup, dict) or followup.get("initial_terminal") is True:
        return [], []
    polls = followup.get("polls")
    if not isinstance(polls, list) or not polls:
        return [], []  # Async-context validation reports malformed evidence.

    assertions = _assertion_config(case)
    expected_tools = case.get("expected_tools", []) or assertions.get("required_tools", []) or []
    expected_specialists = {
        tool
        for tool in expected_tools
        if isinstance(tool, str) and tool in FINANCIAL_SPECIALIST_TOOLS
    }
    expected_skills = case.get("expected_skills", []) or assertions.get("required_skills", []) or []
    required_skills = {skill for skill in expected_skills if isinstance(skill, str) and skill}

    prior_turns: list[tuple[str, dict[str, Any]]] = [("initial", root_packet)]
    prior_turns.extend(
        (f"poll[{index}]", poll) for index, poll in enumerate(polls[:-1]) if isinstance(poll, dict)
    )
    provider_errors: list[str] = []
    product_errors: list[str] = []
    for turn_label, turn_packet in prior_turns:
        has_raw_spans, spans = _authoritative_spans(turn_packet)
        if not has_raw_spans or not spans:
            result.missing_evidence.append(f"async_turn_spans:{turn_label}")
            continue

        calls = [_call_name(span) for span in spans]
        for specialist in sorted(expected_specialists):
            if specialist in calls:
                result.checks_passed.append(
                    f"async {turn_label} expected specialist observed: {specialist}"
                )
            else:
                result.checks_failed.append(
                    f"async {turn_label} expected specialist missing: {specialist}"
                )

        successful_skills: set[str] = set()
        for index, span in enumerate(spans):
            name = _call_name(span)
            skill = _skill_name_from_call(span)
            is_relevant_specialist = name in FINANCIAL_SPECIALIST_TOOLS
            is_required_skill = skill in required_skills
            if not is_relevant_specialist and not is_required_skill:
                continue

            error_evidence = _span_error_evidence(span)
            if error_evidence is not None:
                source, error = error_evidence
                serialized = _serialise_error(error)
                kind = "specialist" if is_relevant_specialist else "required skill"
                identity = name if is_relevant_specialist else skill
                detail = (
                    f"async {turn_label} {kind} span failed: "
                    f"{identity}[{index}] {source}={serialized}"
                )
                if PROVIDER_FAILURE_RE.search(serialized):
                    provider_errors.append(detail)
                else:
                    product_errors.append(detail)
                continue

            if not _present(span.get("output", _MISSING)):
                if is_relevant_specialist:
                    result.missing_evidence.append(
                        f"async_specialist_output:{turn_label}:{name}[{index}]"
                    )
                else:
                    result.missing_evidence.append(
                        f"async_skill_output:{turn_label}:{skill}[{index}]"
                    )
                continue
            if is_required_skill and skill is not None:
                successful_skills.add(skill)

        for skill in sorted(required_skills):
            if skill in successful_skills:
                result.checks_passed.append(f"async {turn_label} required skill observed: {skill}")
            else:
                result.checks_failed.append(f"async {turn_label} required skill missing: {skill}")

    return provider_errors, product_errors


def _enforce_specialist_call_ceiling(
    case: dict[str, Any],
    root_packet: dict[str, Any],
    result: JudgeResult,
) -> None:
    """Enforce the declared outer-specialist ceiling per CLI-turn trace.

    Exact ``FINANCIAL_SPECIALIST_TOOLS`` span names represent outer specialist
    invocations; nested LLM/provider spans have other names and are not counted.
    Async initial and poll traces are checked separately because the case cost
    contract defines ``max_specialist_calls`` per CLI turn, not cumulatively.
    Allowed extras are valid routes but still consume this ceiling.
    """
    cost = case.get("cost")
    if not isinstance(cost, dict):
        return
    ceiling = cost.get("max_specialist_calls")
    if isinstance(ceiling, bool) or not isinstance(ceiling, int) or ceiling < 0:
        return  # The offline schema linter rejects invalid declarations.

    turn_traces: list[tuple[str, object]] = [("initial", root_packet.get("trace"))]
    if case.get("expect_async_job") is True:
        followup = root_packet.get("followup")
        polls = followup.get("polls") if isinstance(followup, dict) else None
        if isinstance(polls, list):
            turn_traces.extend(
                (f"poll[{index}]", poll.get("trace") if isinstance(poll, dict) else None)
                for index, poll in enumerate(polls)
            )

    for label, raw_trace in turn_traces:
        spans = raw_trace.get("spans") if isinstance(raw_trace, dict) else None
        if not isinstance(spans, list) or not all(isinstance(span, dict) for span in spans):
            evidence_label = f"specialist_call_count:{label}"
            if evidence_label not in result.missing_evidence:
                result.missing_evidence.append(evidence_label)
            continue
        calls = [name for span in spans if (name := _call_name(span)) in FINANCIAL_SPECIALIST_TOOLS]
        if len(calls) > ceiling:
            result.checks_failed.append(
                "financial specialist call ceiling exceeded in "
                f"{label}: observed {len(calls)}, max {ceiling}, calls={calls!r}"
            )
        else:
            result.checks_passed.append(
                f"financial specialist call ceiling respected in {label}: "
                f"observed {len(calls)}, max {ceiling}"
            )


def _has_harness_failure(packet: dict[str, Any], expected_outcome: str) -> str | None:
    harness_status = packet.get("harness_status")
    if isinstance(harness_status, str) and harness_status not in {
        "completed",
        "guardrail_rejected",
    }:
        return f"harness status is {harness_status}"
    harness_exit_code = packet.get("harness_exit_code")
    if harness_exit_code not in (None, 0):
        return f"harness exited with {harness_exit_code}"
    cli = packet.get("cli")
    if not isinstance(cli, dict):
        return "packet has no cli evidence"
    if cli.get("timed_out") is True:
        return "CLI timed out"
    exit_code = cli.get("exit_code")
    if not isinstance(exit_code, int):
        return "CLI exit code is missing"
    if exit_code != 0:
        guardrail_rejected = (
            _stdout_json(packet).get("guardrail_rejected") is True
            or packet.get("guardrail_rejected") is True
        )
        if exit_code == 1 and guardrail_rejected and expected_outcome == "hub_reject":
            return None
        return f"CLI exited with {exit_code}"
    return None


def judge_packet(case: dict[str, Any], packet: dict[str, Any]) -> JudgeResult:
    root_packet = packet
    async_context = _async_context(case, root_packet)
    packet = async_context.packet
    case_id = str(case.get("id") or packet.get("id") or "unknown")
    expected_outcome = str(case.get("expected_outcome", "success"))
    response = _response_text(packet)
    observed_outcome = async_context.forced_outcome or _observed_outcome(case, packet, response)
    tools, tool_evidence = _observed_tools(packet)
    skills, skill_evidence = _observed_skills(packet)
    result = JudgeResult(
        case_id=case_id,
        verdict="inconclusive_harness",
        expected_outcome=expected_outcome,
        observed_outcome=observed_outcome,
        observed_tools=tools,
        observed_skills=skills,
    )

    if async_context.early_verdict:
        result.verdict = async_context.early_verdict
        result.reason = async_context.reason
        return result
    if case.get("expect_async_job") is True:
        initial_failure = _has_harness_failure(root_packet, expected_outcome)
        if initial_failure is not None:
            result.reason = f"initial async turn failed: {initial_failure}"
            return result
    if reason := _has_harness_failure(packet, expected_outcome):
        result.reason = reason
        return result
    if not response:
        result.reason = "CLI completed but no final response was captured"
        return result

    assertions = _assertion_config(case)
    for spec in assertions.get("required_text", []) or []:
        matched, label = _matches(response, spec)
        if matched:
            result.checks_passed.append(f"required_text present: {label}")
        else:
            result.checks_failed.append(f"required_text missing: {label}")
    for spec in assertions.get("forbidden_text", []) or []:
        matched, label = _matches(response, spec)
        if matched:
            result.checks_failed.append(f"forbidden_text present: {label}")
        else:
            result.checks_passed.append(f"forbidden_text absent: {label}")
    for path in assertions.get("required_evidence", []) or []:
        if not isinstance(path, str) or not path:
            result.checks_failed.append(f"invalid required_evidence path: {path!r}")
            continue
        if _present(_dig(packet, path)):
            result.checks_passed.append(f"required_evidence present: {path}")
        else:
            result.missing_evidence.append(path)

    structured_claims = _dig(packet, "evidence.claims")
    if not isinstance(structured_claims, dict):
        structured_claims = {}
    for claim in assertions.get("required_claims", []) or []:
        structured = structured_claims.get(claim, _MISSING)
        if structured is not _MISSING:
            _record_structured_boolean(
                result,
                label=f"required_claims:{claim}",
                value=structured,
            )
            continue
        pattern = REQUIRED_CLAIM_PATTERNS.get(claim)
        if pattern is None:
            result.unexecuted_assertions.append(f"required_claims:{claim}")
        elif pattern.search(response):
            result.checks_passed.append(f"required claim observed: {claim}")
        else:
            result.checks_failed.append(f"required claim missing: {claim}")

    for claim in assertions.get("forbidden_claims", []) or []:
        structured = structured_claims.get(claim, _MISSING)
        if structured is not _MISSING:
            if structured is True:
                result.checks_failed.append(f"forbidden claim observed: {claim}")
            elif structured is False:
                result.checks_passed.append(f"forbidden claim absent: {claim}")
            else:
                result.missing_evidence.append(f"forbidden_claims:{claim} (non-boolean result)")
            continue
        pattern = FORBIDDEN_CLAIM_PATTERNS.get(claim)
        if pattern is None:
            result.unexecuted_assertions.append(f"forbidden_claims:{claim}")
        elif pattern.search(response):
            result.checks_failed.append(f"forbidden claim observed: {claim}")
        else:
            result.checks_passed.append(f"forbidden claim absent: {claim}")

    checker = assertions.get("numeric_checker")
    if isinstance(checker, str) and checker:
        _record_structured_boolean(
            result,
            label=f"numeric_checker:{checker}",
            value=_structured_check(packet, ("numeric_checks",), checker),
        )
    for invariant in assertions.get("numeric_invariants", []) or []:
        _record_structured_boolean(
            result,
            label=f"numeric_invariants:{invariant}",
            value=_structured_check(packet, ("numeric_invariants", "invariants"), invariant),
        )
    for equality in assertions.get("equality_across_turns", []) or []:
        _record_structured_boolean(
            result,
            label=f"equality_across_turns:{equality}",
            value=_structured_check(packet, ("cross_turn_equalities", "equalities"), equality),
        )
    for invariant in assertions.get("conditional_invariants", []) or []:
        _record_structured_boolean(
            result,
            label=f"conditional_invariants:{invariant}",
            value=_structured_check(packet, ("conditional_invariants", "invariants"), invariant),
        )
    for index, instruction in enumerate(assertions.get("manual_assertions", []) or []):
        result.unexecuted_assertions.append(f"manual_assertions[{index}]:{instruction}")

    expected_tools = case.get("expected_tools", []) or assertions.get("required_tools", []) or []
    if expected_tools:
        if not tool_evidence:
            result.missing_evidence.append("tool_calls")
        else:
            for tool in expected_tools:
                if tool in tools:
                    result.checks_passed.append(f"expected tool observed: {tool}")
                else:
                    result.checks_failed.append(f"expected tool missing: {tool}")

    span_provider_errors, span_product_errors = _expected_span_errors(
        packet, expected_tools, result
    )
    extra_provider_errors, extra_product_errors = _additional_specialist_span_errors(
        packet, expected_tools
    )
    if span_provider_errors or extra_provider_errors:
        result.verdict = "inconclusive_provider"
        result.reason = (
            "financial specialist span failed because of a provider/auth error: "
            + (span_provider_errors or extra_provider_errors)[0]
        )
        return result
    if extra_product_errors:
        result.verdict = "fail_product"
        result.checks_failed.extend(
            f"additional specialist span failed: {error}" for error in extra_product_errors
        )
        result.reason = "an additional financial specialist execution failed"
        return result
    if span_product_errors:
        if expected_outcome == "specialist_error" and _has_hard_outcome_assertions(case):
            observed_outcome = "specialist_error"
            result.observed_outcome = observed_outcome
            result.checks_passed.append("expected specialist error observed in raw span evidence")
        else:
            result.verdict = "fail_product"
            result.checks_failed.extend(
                f"expected specialist span failed: {error}" for error in span_product_errors
            )
            result.reason = "an expected specialist execution failed"
            return result

    async_provider_errors, async_product_errors = _async_prior_turn_span_issues(
        case, root_packet, result
    )
    if async_provider_errors:
        result.verdict = "inconclusive_provider"
        result.reason = (
            "an earlier async turn failed because of a provider/auth error: "
            + async_provider_errors[0]
        )
        return result
    if async_product_errors:
        result.verdict = "fail_product"
        result.checks_failed.extend(async_product_errors)
        result.reason = "a specialist or required skill failed in an earlier async turn"
        return result

    _enforce_specialist_call_ceiling(case, root_packet, result)

    # The expected tool contract is exclusive for outer financial specialists;
    # allowed_extras records deliberate optional routes. An undeclared route is
    # deterministic evidence of extra cost, but its product appropriateness
    # still needs semantic review rather than an automatic hard failure.
    expected_tool_names = {tool for tool in expected_tools if isinstance(tool, str)}
    allowed_extras = {tool for tool in case.get("allowed_extras", []) if isinstance(tool, str)}
    unexpected_specialists = list(
        dict.fromkeys(
            tool
            for tool in tools
            if tool in FINANCIAL_SPECIALIST_TOOLS
            and tool not in expected_tool_names
            and tool not in allowed_extras
        )
    )
    result.unexecuted_assertions.extend(
        f"routing.unexpected_specialist:{tool}" for tool in unexpected_specialists
    )

    expected_skills = case.get("expected_skills", []) or assertions.get("required_skills", []) or []
    absent_skills = (
        case.get("expected_skills_absent", []) or assertions.get("forbidden_skills", []) or []
    )
    successful_required_skills, skill_provider_errors, skill_product_errors = (
        _required_skill_span_evidence(packet, expected_skills, result)
    )
    if expected_skills or absent_skills:
        if not skill_evidence:
            result.missing_evidence.append("skill_loads")
        else:
            for skill in expected_skills:
                skill_was_loaded = (
                    skill in skills
                    if successful_required_skills is None
                    else skill in successful_required_skills
                )
                if skill_was_loaded:
                    result.checks_passed.append(f"expected skill observed: {skill}")
                else:
                    result.checks_failed.append(f"expected skill missing: {skill}")
            for skill in absent_skills:
                if skill in skills:
                    result.checks_failed.append(f"forbidden skill observed: {skill}")
                else:
                    result.checks_passed.append(f"forbidden skill absent: {skill}")

    if skill_provider_errors:
        result.verdict = "inconclusive_provider"
        result.reason = (
            "required skill load failed because of a provider/auth error: "
            + skill_provider_errors[0]
        )
        return result
    if skill_product_errors:
        result.verdict = "fail_product"
        result.checks_failed.extend(
            f"required skill load failed: {error}" for error in skill_product_errors
        )
        result.reason = "a required skill load failed"
        return result

    expected_sequence = case.get("expected_sequence") or assertions.get("expected_sequence")
    if expected_sequence:
        if not tool_evidence:
            result.missing_evidence.append("tool_sequence")
        elif _sequence_satisfied(expected_sequence, tools):
            result.checks_passed.append("expected tool sequence observed")
        else:
            result.checks_failed.append(
                f"expected sequence not observed: {expected_sequence!r} in {tools!r}"
            )

    forbidden_calls = assertions.get("forbidden_calls", []) or []
    forbidden_tools = assertions.get("forbidden_tools", []) or []
    if forbidden_calls or forbidden_tools:
        if not tool_evidence:
            result.missing_evidence.append("forbidden_tool_calls")
        else:
            for tool in forbidden_calls:
                if tool in tools:
                    result.checks_failed.append(f"forbidden call observed: {tool}")
                else:
                    result.checks_passed.append(f"forbidden call absent: {tool}")
            for tool in forbidden_tools:
                if tool == "all_financial_specialists":
                    seen = sorted(FINANCIAL_SPECIALIST_TOOLS.intersection(tools))
                    if seen:
                        result.checks_failed.append(
                            f"forbidden financial specialist tool(s) observed: {seen}"
                        )
                    else:
                        result.checks_passed.append("all financial specialist tools absent")
                elif tool in tools:
                    result.checks_failed.append(f"forbidden tool observed: {tool}")
                else:
                    result.checks_passed.append(f"forbidden tool absent: {tool}")

    # Provider outages are infrastructure for normal success cases.  A case
    # explicitly testing data unavailability may treat the same response as its
    # expected degraded outcome.
    provider_failure = bool(PROVIDER_FAILURE_RE.search(_error_blob(packet, response)))
    provider_failure_expected = _declared_and_applied_provider_fault(case, packet)
    if provider_failure and not provider_failure_expected:
        result.verdict = "inconclusive_provider"
        result.reason = "provider/auth/rate-limit failure detected"
        return result

    if result.missing_evidence:
        result.verdict = "inconclusive_missing_evidence"
        result.reason = "required evidence was not captured"
        return result
    if result.checks_failed:
        result.verdict = "fail_product"
        result.reason = "one or more deterministic assertions failed"
        return result

    acceptable_outcomes = {expected_outcome}
    declared_alternatives = case.get("acceptable_outcomes")
    if isinstance(declared_alternatives, list):
        acceptable_outcomes.update(
            outcome for outcome in declared_alternatives if isinstance(outcome, str)
        )
    outcome_matches = observed_outcome in acceptable_outcomes
    if not outcome_matches:
        result.verdict = "fail_product"
        result.checks_failed.append(
            f"expected one of {sorted(acceptable_outcomes)}, observed {observed_outcome}"
        )
        result.reason = "response outcome did not match the case contract"
        return result

    if result.unexecuted_assertions:
        result.verdict = "needs_semantic_review"
        result.reason = "one or more declared assertions require semantic execution"
        return result

    if observed_outcome == "success":
        result.verdict = "pass"
        result.reason = "all deterministic success checks passed"
        return result

    has_outcome_assertions = bool(
        assertions.get("required_text") or assertions.get("forbidden_text")
    )
    if not has_outcome_assertions:
        result.verdict = "needs_semantic_review"
        result.reason = "degraded outcome matched by keywords but has no hard text contract"
        return result

    result.verdict = "pass_degraded"
    result.reason = f"accepted {observed_outcome} behavior was observed"
    return result


def _load_case(path: Path, case_id: str) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("test_cases"), list):
        raise ValueError("cases file must contain test_cases list")
    matches = [
        case for case in raw["test_cases"] if isinstance(case, dict) and case.get("id") == case_id
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one case {case_id!r}, found {len(matches)}")
    return matches[0]


def _exit_code(verdict: str) -> int:
    if verdict in PASS_VERDICTS:
        return 0
    if verdict in PRODUCT_VERDICTS:
        return 1
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--id", required=True, dest="case_id")
    parser.add_argument("--packet", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        case = _load_case(args.cases, args.case_id)
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
        if not isinstance(packet, dict):
            raise ValueError("packet root must be an object")
    except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    result = judge_packet(case, packet)
    payload = json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return _exit_code(result.verdict)


if __name__ == "__main__":
    raise SystemExit(main())
