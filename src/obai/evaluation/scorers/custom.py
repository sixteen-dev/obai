"""Custom scorers for OBaI multi-agent evaluation.

These scorers validate OBaI-specific behaviors:
- Correct specialist agent routing
- Proper sequencing for dependency queries
- Response quality for financial data
- Strategy artifact structure for build/backtest workflows

Uses @opik.track() decorated functions as scorers (simpler than class-based).
"""

from __future__ import annotations

import json
import logging
import re
from json import JSONDecodeError, loads
from typing import Any

import opik
from pydantic import BaseModel, Field

from evaluation.metrics.sequencing import validate_sequence
from evaluation.scorers._llm_client import DEFAULT_JUDGE_MODEL, structured_completion

logger = logging.getLogger(__name__)

_STRATEGY_TERMINAL_PREFIX = "__TERMINAL_TOOL_OUTPUT__:strategy_analysis:"
_STRATEGY_SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Verdict", re.compile(r"(?im)^\s*#{2,6}\s*(?:\d+\.\s*)?Verdict\b")),
    (
        "Strategy Summary",
        re.compile(r"(?im)^\s*#{2,6}\s*(?:\d+\.\s*)?Strategy Summary\b"),
    ),
    (
        "Backtest Evidence",
        re.compile(r"(?im)^\s*#{2,6}\s*(?:\d+\.\s*)?Backtest Evidence\b"),
    ),
    (
        "Iteration Summary",
        re.compile(r"(?im)^\s*#{2,6}\s*(?:\d+\.\s*)?Iteration Summary\b"),
    ),
    (
        "Engine Compatibility",
        re.compile(r"(?im)^\s*#{2,6}\s*(?:\d+\.\s*)?Engine Compatibility\b"),
    ),
    (
        "Final Strategy JSON",
        re.compile(r"(?im)^\s*#{2,6}\s*(?:\d+\.\s*)?Final Strategy JSON\b"),
    ),
    ("Risk Notes", re.compile(r"(?im)^\s*#{2,6}\s*(?:\d+\.\s*)?Risk Notes\b")),
    (
        "Next Actions",
        re.compile(r"(?im)^\s*#{2,6}\s*(?:\d+\.\s*)?Next Actions\b"),
    ),
    (
        "Handoff Metadata",
        re.compile(r"(?im)^\s*#{2,6}\s*(?:\d+\.\s*)?Handoff Metadata\b"),
    ),
]
_STRATEGY_PENDING_FIELDS = ("Status", "Job ID", "Estimated Time", "Next User Action")
_MAX_STRATEGY_EVIDENCE_CHARS = 12000


def _parse_strategy_json_block(response: str) -> tuple[bool, str | None]:
    """Return whether the final strategy JSON block exists and parses."""
    json_section = re.search(
        r"(?ims)^\s*#{2,6}\s*(?:\d+\.\s*)?Final Strategy JSON\b(?P<body>.*)$",
        response,
    )
    if not json_section:
        return False, "missing Final Strategy JSON section"

    body = json_section.group("body")
    block_match = re.search(r"(?s)```json\s*(?P<json>\{.*?\})\s*```", body)
    if not block_match:
        return False, "missing fenced json block"

    try:
        parsed = loads(block_match.group("json"))
    except JSONDecodeError as exc:
        return False, f"invalid strategy json: {exc.msg}"

    if not isinstance(parsed, dict):
        return False, "strategy json must decode to an object"

    return True, None


def _normalize_text(text: str) -> str:
    """Normalize whitespace for robust text comparison."""
    return re.sub(r"\s+", " ", text).strip()


def _extract_strategy_section(response: str, section_name: str) -> str | None:
    """Extract one named strategy section body from the response."""
    pattern = next(
        (pattern for name, pattern in _STRATEGY_SECTION_PATTERNS if name == section_name),
        None,
    )
    if pattern is None:
        return None

    match = pattern.search(response)
    if match is None:
        return None

    start = match.end()
    next_starts: list[int] = []
    for other_name, other_pattern in _STRATEGY_SECTION_PATTERNS:
        if other_name == section_name:
            continue
        other_match = other_pattern.search(response, start)
        if other_match is not None:
            next_starts.append(other_match.start())

    end = min(next_starts) if next_starts else len(response)
    return response[start:end].strip()


def _get_strategy_passthrough_artifact(output: dict[str, Any]) -> str | None:
    """Extract raw strategy terminal artifact from inner tool outputs."""
    artifact: str | None = None
    for inner in output.get("inner_tool_calls", []):
        if inner.get("tool_name") != "Strategy Agent/strategy_passthrough":
            continue
        response = inner.get("response")
        if isinstance(response, dict):
            raw = response.get("raw")
            if isinstance(raw, str) and raw.strip():
                artifact = raw
    return artifact


def _get_strategy_evidence_payload(output: dict[str, Any]) -> str:
    """Format strategy-related inner tool outputs for LLM judgment."""
    blocks: list[str] = []
    for inner in output.get("inner_tool_calls", []):
        if inner.get("agent_name") != "Strategy Agent":
            continue
        tool_name = inner.get("tool_name", "unknown")
        if tool_name == "Strategy Agent/strategy_passthrough":
            continue
        response = inner.get("response")
        if isinstance(response, dict):
            rendered = json.dumps(response, indent=2, default=str)
        else:
            rendered = str(response)
        blocks.append(f'<tool name="{tool_name}">\n{rendered}\n</tool>')

    evidence = "\n\n".join(blocks)
    if len(evidence) > _MAX_STRATEGY_EVIDENCE_CHARS:
        return evidence[:_MAX_STRATEGY_EVIDENCE_CHARS] + "\n...(truncated)"
    return evidence


def _strategy_section_positions(response: str) -> tuple[list[str], bool]:
    """Return missing sections and whether present sections are in order."""
    positions: list[tuple[str, int]] = []
    missing: list[str] = []

    for name, pattern in _STRATEGY_SECTION_PATTERNS:
        match = pattern.search(response)
        if match is None:
            missing.append(name)
            continue
        positions.append((name, match.start()))

    ordered = all(positions[idx][1] < positions[idx + 1][1] for idx in range(len(positions) - 1))
    return missing, ordered


@opik.track()  # type: ignore[untyped-decorator]  # opik doesn't preserve type sig
def tool_orchestration_scorer(
    output: dict[str, Any],
    expected_tools: list[str],
    allow_extra: bool = True,
) -> dict[str, Any]:
    """Validate that the correct specialist agents were called.

    Args:
        output: Model output containing tool_calls list.
        expected_tools: List of expected specialist tool names.
        allow_extra: If True, extra tool calls don't fail the check.

    Returns:
        Dict with:
            - correct_tools (bool): True if all expected tools were called
            - missing_tools (list): Tools that should have been called
            - extra_tools (list): Unexpected tools that were called
            - precision (float): Fraction of actual calls that were expected
            - recall (float): Fraction of expected calls that were made
    """
    tool_calls = output.get("tool_calls", [])
    actual_tools = [tc.get("tool_name", tc.get("name", "")) for tc in tool_calls]

    expected_set = set(expected_tools)
    actual_set = set(actual_tools)

    missing = list(expected_set - actual_set)
    extra = list(actual_set - expected_set)

    # Calculate metrics
    recall = len(expected_set & actual_set) / len(expected_set) if expected_set else 1.0
    precision = len(expected_set & actual_set) / len(actual_set) if actual_set else 1.0

    # Correct if no missing, and either allow_extra or no extra
    correct = len(missing) == 0 and (allow_extra or len(extra) == 0)

    return {
        "correct_tools": correct,
        "missing_tools": missing,
        "extra_tools": extra,
        "precision": precision,
        "recall": recall,
    }


@opik.track()  # type: ignore[untyped-decorator]  # opik doesn't preserve type sig
def sequence_scorer(
    output: dict[str, Any],
    expected_sequence: list[str],
    strict: bool = False,
) -> dict[str, Any]:
    """Validate that agents were called in the correct order.

    For queries with dependencies (e.g., "What's Palantir trading at?"),
    certain agents must be called before others (screener before market_data).

    Args:
        output: Model output containing tool_calls list.
        expected_sequence: Required order of tool calls.
        strict: If True, require exact sequence. If False, only check ordering.

    Returns:
        Dict with:
            - correct_sequence (bool): True if sequence is valid
            - out_of_order (list): Pairs of (should_be_before, should_be_after)
            - missing (list): Required calls that weren't made
            - reason (str|None): Explanation if incorrect
    """
    tool_calls = output.get("tool_calls", [])
    actual_sequence = [tc.get("tool_name", tc.get("name", "")) for tc in tool_calls]

    result = validate_sequence(
        actual_sequence=actual_sequence,
        expected_sequence=expected_sequence,
        strict=strict,
    )

    return {
        "correct_sequence": result.is_correct,
        "out_of_order": result.out_of_order,
        "missing": result.missing,
        "reason": result.reason,
    }


@opik.track()  # type: ignore[untyped-decorator]  # opik doesn't preserve type sig
def response_quality_scorer(
    output: dict[str, Any],
    query: str,
    min_length: int = 50,
    require_numbers: bool = True,
    require_ticker: bool = False,
) -> dict[str, Any]:
    """Basic response quality checks (non-LLM).

    Validates structural aspects of the response without using an LLM.
    Useful for quick sanity checks before LLM-based evaluation.

    Args:
        output: Model output with response text.
        query: Original user query (unused but available for context).
        min_length: Minimum acceptable response length.
        require_numbers: If True, response must contain numeric data.
        require_ticker: If True, response must mention a stock ticker.

    Returns:
        Dict with quality metrics.
    """
    response = output.get("response", "")

    # Length check
    adequate_length = len(response) >= min_length

    # Numbers check (financial responses should have data)
    has_numbers = bool(re.search(r"\d+\.?\d*", response))
    numbers_ok = has_numbers if require_numbers else True

    # Ticker check (1-5 uppercase letters)
    has_ticker = bool(re.search(r"\b[A-Z]{1,5}\b", response))
    ticker_ok = has_ticker if require_ticker else True

    return {
        "adequate_length": adequate_length,
        "has_numbers": has_numbers,
        "has_ticker": has_ticker,
        "response_length": len(response),
        "quality_pass": adequate_length and numbers_ok and ticker_ok,
    }


@opik.track()  # type: ignore[untyped-decorator]  # opik doesn't preserve type sig
def efficiency_scorer(
    output: dict[str, Any],
    max_tool_calls: int = 5,
) -> dict[str, Any]:
    """Score the efficiency of agent execution.

    Checks for redundant tool calls and overall efficiency.

    Args:
        output: Model output with tool_calls and metrics.
        max_tool_calls: Maximum acceptable tool calls for query type.

    Returns:
        Dict with efficiency metrics.
    """
    tool_calls = output.get("tool_calls", [])
    total_calls = len(tool_calls)

    # Count unique tools
    tool_names = [tc.get("tool_name", tc.get("name", "")) for tc in tool_calls]
    unique_tools = list(set(tool_names))
    redundant_calls = total_calls - len(unique_tools)

    # Efficiency score (1.0 = perfect, lower = worse)
    efficiency = 1.0 if total_calls == 0 else len(unique_tools) / total_calls

    within_budget = total_calls <= max_tool_calls

    return {
        "total_calls": total_calls,
        "unique_tools": len(unique_tools),
        "redundant_calls": redundant_calls,
        "efficiency": efficiency,
        "within_budget": within_budget,
    }


@opik.track()  # type: ignore[untyped-decorator]  # opik doesn't preserve type sig
def strategy_contract_scorer(
    output: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    """Validate the final delivered strategy response contract.

    This scorer is deterministic. It checks that strategy build/backtest
    flows return either:
    - a completed strategy artifact with all required sections and a valid JSON block, or
    - a pending async response with the required status fields.

    It scores the final user-visible response, not the raw specialist tool output.
    """
    del query  # Unused, kept for interface consistency.

    response = output.get("response", "")
    marker_leaked = _STRATEGY_TERMINAL_PREFIX in response

    if not response.strip():
        return {
            "mode": "invalid",
            "marker_leaked": marker_leaked,
            "missing_sections": ["response body"],
            "sections_in_order": False,
            "json_valid": False,
            "contract_pass": False,
            "reason": "empty response",
        }

    has_pending_fields = all(field in response for field in _STRATEGY_PENDING_FIELDS)
    missing_sections, sections_in_order = _strategy_section_positions(response)
    has_completed_shape = not missing_sections

    json_valid = False
    json_reason: str | None = None
    if has_completed_shape:
        json_valid, json_reason = _parse_strategy_json_block(response)

    if has_completed_shape and sections_in_order and json_valid and not marker_leaked:
        return {
            "mode": "completed",
            "marker_leaked": marker_leaked,
            "missing_sections": [],
            "sections_in_order": True,
            "json_valid": True,
            "contract_pass": True,
            "reason": None,
        }

    if has_pending_fields and not marker_leaked:
        return {
            "mode": "pending",
            "marker_leaked": marker_leaked,
            "missing_sections": [],
            "sections_in_order": True,
            "json_valid": False,
            "contract_pass": True,
            "reason": None,
        }

    reason_parts: list[str] = []
    if marker_leaked:
        reason_parts.append("terminal marker leaked into final response")
    if missing_sections:
        reason_parts.append(f"missing sections: {', '.join(missing_sections)}")
    if has_completed_shape and not sections_in_order:
        reason_parts.append("strategy sections out of order")
    if json_reason:
        reason_parts.append(json_reason)
    if not reason_parts and not has_pending_fields:
        reason_parts.append(
            "response is neither a completed strategy artifact nor a pending status update"
        )

    return {
        "mode": "invalid",
        "marker_leaked": marker_leaked,
        "missing_sections": missing_sections,
        "sections_in_order": sections_in_order,
        "json_valid": json_valid,
        "contract_pass": False,
        "reason": "; ".join(reason_parts),
    }


@opik.track()  # type: ignore[untyped-decorator]  # opik doesn't preserve type sig
def strategy_grounding_scorer(
    output: dict[str, Any],
    query: str,
) -> dict[str, Any]:
    """Validate that the final delivered strategy output preserves the raw artifact.

    Uses the raw `strategy_passthrough` artifact captured in traces as ground truth.
    This catches hub-side truncation, dropped JSON, altered verdicts, and rewritten
    metadata in the final response delivered to the user.
    """
    del query  # Unused, kept for interface consistency.

    response = output.get("response", "")
    artifact = _get_strategy_passthrough_artifact(output)
    if not artifact:
        return {
            "artifact_found": False,
            "artifact_embedded_verbatim": False,
            "verdict_matches": False,
            "json_block_matches": False,
            "metadata_matches": False,
            "grounding_pass": False,
            "reason": "missing raw strategy_passthrough artifact in trace",
        }

    response_norm = _normalize_text(response)
    artifact_norm = _normalize_text(artifact)
    artifact_embedded_verbatim = artifact_norm in response_norm

    artifact_is_pending = all(field in artifact for field in _STRATEGY_PENDING_FIELDS)
    if artifact_is_pending:
        return {
            "artifact_found": True,
            "artifact_embedded_verbatim": artifact_embedded_verbatim,
            "verdict_matches": False,
            "json_block_matches": False,
            "metadata_matches": False,
            "grounding_pass": artifact_embedded_verbatim,
            "reason": (
                None if artifact_embedded_verbatim else "pending strategy status was rewritten"
            ),
        }

    artifact_verdict = _extract_strategy_section(artifact, "Verdict")
    response_verdict = _extract_strategy_section(response, "Verdict")
    verdict_matches = bool(artifact_verdict and response_verdict) and (
        _normalize_text(artifact_verdict) == _normalize_text(response_verdict)
    )

    artifact_json = _extract_strategy_section(artifact, "Final Strategy JSON")
    response_json = _extract_strategy_section(response, "Final Strategy JSON")
    json_block_matches = bool(artifact_json and response_json) and (
        _normalize_text(artifact_json) == _normalize_text(response_json)
    )

    artifact_metadata = _extract_strategy_section(artifact, "Handoff Metadata")
    response_metadata = _extract_strategy_section(response, "Handoff Metadata")
    metadata_matches = bool(artifact_metadata and response_metadata) and (
        _normalize_text(artifact_metadata) == _normalize_text(response_metadata)
    )

    grounding_pass = artifact_embedded_verbatim or (
        verdict_matches and json_block_matches and metadata_matches
    )

    reason_parts: list[str] = []
    if not artifact_embedded_verbatim:
        reason_parts.append("raw strategy artifact not preserved verbatim")
    if not verdict_matches:
        reason_parts.append("verdict changed or missing")
    if not json_block_matches:
        reason_parts.append("final strategy json changed or missing")
    if not metadata_matches:
        reason_parts.append("handoff metadata changed or missing")

    return {
        "artifact_found": True,
        "artifact_embedded_verbatim": artifact_embedded_verbatim,
        "verdict_matches": verdict_matches,
        "json_block_matches": json_block_matches,
        "metadata_matches": metadata_matches,
        "grounding_pass": grounding_pass,
        "reason": None if grounding_pass else "; ".join(reason_parts),
    }


class StrategyDecisionDimension(BaseModel):
    """Score for one strategy decision-quality dimension."""

    score: int = Field(ge=1, le=5)
    reasoning: str


class StrategyDecisionJudgment(BaseModel):
    """Structured response for LLM strategy decision evaluation."""

    verdict_calibration: StrategyDecisionDimension
    evidence_use: StrategyDecisionDimension
    quant_honesty: StrategyDecisionDimension
    actionability: StrategyDecisionDimension
    overall_reasoning: str


_STRATEGY_DECISION_THRESHOLDS = {
    "verdict_calibration": 4,
    "evidence_use": 3,
    "quant_honesty": 4,
    "actionability": 3,
}

_STRATEGY_DECISION_SYSTEM_PROMPT = """\
You are evaluating the quality of a strategy/backtest recommendation produced by a
financial research agent.

Score the final delivered response, not the raw tool output. A rejected strategy can
score highly if the rejection is well justified.

Use the raw strategy artifact and strategy tool outputs as ground truth.

Score these dimensions from 1 to 5:
- verdict_calibration: Does the verdict fit the evidence?
- evidence_use: Does the response use the most relevant backtest evidence?
- quant_honesty: Does it clearly disclose approximation limits, benchmark issues,
  weak sample size, overfitting risk, or other important caveats?
- actionability: Are next steps specific and aligned with the verdict?

Be strict. Penalize:
- recommendations that are too optimistic for the evidence
- dropped caveats
- vague next steps
- invented claims not supported by the strategy evidence
"""

_STRATEGY_DECISION_USER_TEMPLATE = """\
<user_query>
{query}
</user_query>

<final_delivered_response>
{response}
</final_delivered_response>

<raw_strategy_artifact>
{artifact}
</raw_strategy_artifact>

<strategy_tool_outputs>
{evidence}
</strategy_tool_outputs>
"""


@opik.track()  # type: ignore[untyped-decorator]  # opik doesn't preserve type sig
async def strategy_decision_scorer(
    output: dict[str, Any],
    query: str,
    model_id: str = DEFAULT_JUDGE_MODEL,
    thresholds: dict[str, int] | None = None,
) -> dict[str, Any]:
    """LLM judge for strategy decision quality on completed artifacts."""
    artifact = _get_strategy_passthrough_artifact(output)
    response = output.get("response", "")

    if not artifact:
        return {"skipped": True, "skip_reason": "missing_strategy_artifact"}
    if all(field in artifact for field in _STRATEGY_PENDING_FIELDS):
        return {"skipped": True, "skip_reason": "pending_strategy_response"}
    if not _extract_strategy_section(response, "Verdict"):
        return {"skipped": True, "skip_reason": "not_completed_strategy_artifact"}

    evidence = _get_strategy_evidence_payload(output)
    if not evidence.strip():
        return {"skipped": True, "skip_reason": "missing_strategy_tool_outputs"}

    effective_thresholds = {
        **_STRATEGY_DECISION_THRESHOLDS,
        **(thresholds or {}),
    }
    user_prompt = _STRATEGY_DECISION_USER_TEMPLATE.format(
        query=query,
        response=response,
        artifact=artifact,
        evidence=evidence,
    )

    try:
        judgment = await structured_completion(
            model=model_id,
            system=_STRATEGY_DECISION_SYSTEM_PROMPT,
            user=user_prompt,
            response_model=StrategyDecisionJudgment,
            temperature=0.0,
        )
    except Exception as e:
        err = str(e)
        if "401" in err or "authentication" in err.lower() or "bearer" in err.lower():
            logger.error("Strategy decision scorer auth failed — set ANTHROPIC_API_KEY")
            return {"error": "Auth failed — set ANTHROPIC_API_KEY"}
        logger.exception("Strategy decision scorer failed")
        return {"error": "Strategy decision scorer call failed"}

    dimensions = {
        "verdict_calibration": judgment.verdict_calibration,
        "evidence_use": judgment.evidence_use,
        "quant_honesty": judgment.quant_honesty,
        "actionability": judgment.actionability,
    }

    result: dict[str, Any] = {}
    total = 0.0
    for name, dim in dimensions.items():
        threshold = effective_thresholds[name]
        total += dim.score
        result[name] = {
            "score": dim.score,
            "reasoning": dim.reasoning,
            "threshold": threshold,
            "passed": dim.score >= threshold,
        }

    average_score = total / len(dimensions)
    result["overall_reasoning"] = judgment.overall_reasoning
    result["average_score"] = average_score
    result["strategy_decision_pass"] = all(result[name]["passed"] for name in dimensions)
    return result


# Export class-like wrappers for scorer configuration
class ToolOrchestrationScorer:
    """Wrapper for tool_orchestration_scorer with config."""

    def __init__(self, expected_tools: list[str], allow_extra: bool = True) -> None:
        """Initialize with expected tools."""
        self.expected_tools = expected_tools
        self.allow_extra = allow_extra

    def score(self, output: dict[str, Any], query: str = "") -> dict[str, Any]:
        """Score method matching scorer interface."""
        result: dict[str, Any] = tool_orchestration_scorer(
            output=output,
            expected_tools=self.expected_tools,
            allow_extra=self.allow_extra,
        )
        return result


class SequenceScorer:
    """Wrapper for sequence_scorer with config."""

    def __init__(self, expected_sequence: list[str], strict: bool = False) -> None:
        """Initialize with expected sequence."""
        self.expected_sequence = expected_sequence
        self.strict = strict

    def score(self, output: dict[str, Any], query: str = "") -> dict[str, Any]:
        """Score method matching scorer interface."""
        result: dict[str, Any] = sequence_scorer(
            output=output,
            expected_sequence=self.expected_sequence,
            strict=self.strict,
        )
        return result


class ResponseQualityScorer:
    """Wrapper for response_quality_scorer with config."""

    def __init__(
        self,
        min_length: int = 50,
        require_numbers: bool = True,
        require_ticker: bool = False,
    ) -> None:
        """Initialize with quality requirements."""
        self.min_length = min_length
        self.require_numbers = require_numbers
        self.require_ticker = require_ticker

    def score(self, output: dict[str, Any], query: str = "") -> dict[str, Any]:
        """Score method matching scorer interface."""
        result: dict[str, Any] = response_quality_scorer(
            output=output,
            query=query,
            min_length=self.min_length,
            require_numbers=self.require_numbers,
            require_ticker=self.require_ticker,
        )
        return result


class EfficiencyScorer:
    """Wrapper for efficiency_scorer with config."""

    def __init__(self, max_tool_calls: int = 5, penalize_redundant: bool = True) -> None:
        """Initialize with efficiency requirements."""
        self.max_tool_calls = max_tool_calls
        self.penalize_redundant = penalize_redundant

    def score(self, output: dict[str, Any], query: str = "") -> dict[str, Any]:
        """Score method matching scorer interface."""
        result: dict[str, Any] = efficiency_scorer(
            output=output,
            max_tool_calls=self.max_tool_calls,
        )
        return result


class StrategyContractScorer:
    """Wrapper for strategy_contract_scorer."""

    def score(self, output: dict[str, Any], query: str = "") -> dict[str, Any]:
        """Score method matching scorer interface."""
        result: dict[str, Any] = strategy_contract_scorer(output=output, query=query)
        return result


class StrategyGroundingScorer:
    """Wrapper for strategy_grounding_scorer."""

    def score(self, output: dict[str, Any], query: str = "") -> dict[str, Any]:
        """Score method matching scorer interface."""
        result: dict[str, Any] = strategy_grounding_scorer(output=output, query=query)
        return result


class StrategyDecisionScorer:
    """Wrapper for strategy_decision_scorer."""

    def __init__(
        self,
        model_id: str = DEFAULT_JUDGE_MODEL,
        thresholds: dict[str, int] | None = None,
    ) -> None:
        """Initialize with judge model and decision thresholds."""
        self.model_id = model_id
        self.thresholds = thresholds

    async def score(self, output: dict[str, Any], query: str = "") -> dict[str, Any]:
        """Score method matching scorer interface."""
        result: dict[str, Any] = await strategy_decision_scorer(
            output=output,
            query=query,
            model_id=self.model_id,
            thresholds=self.thresholds,
        )
        return result
