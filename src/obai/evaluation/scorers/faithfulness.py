"""Faithfulness and completeness scorers for MCP ground-truth verification.

FaithfulnessScorer verifies that the agent's response accurately reflects
what the MCP APIs returned (two-phase: deterministic numeric + LLM semantic).

CompletenessScorer verifies the agent used all relevant data from tool
outputs to answer the query.

Requires: opik, openai (AsyncOpenAI pointed at Anthropic's API)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from typing import Any, Literal

import opik
from pydantic import BaseModel, Field

from evaluation.scorers._llm_client import DEFAULT_JUDGE_MODEL, structured_completion

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tolerances for numeric matching
# ---------------------------------------------------------------------------
_PRICE_TOLERANCE = 0.01
_PERCENT_TOLERANCE = 0.1
_DEFAULT_TOLERANCE = 0.01
# Relative tolerance for large numbers (volumes, market caps).
# "56.29M" (56,290,000) vs 56,290,673 = 0.001% difference.
_LARGE_NUMBER_RELATIVE_TOLERANCE = 0.005  # 0.5%
_LARGE_NUMBER_THRESHOLD = 1000.0

# ---------------------------------------------------------------------------
# Regex patterns for number extraction
# ---------------------------------------------------------------------------
_PRICE_MAGNITUDE_PATTERN = re.compile(
    r"(-?)\$\s*(\d{1,7}(?:,\d{3})*(?:\.\d{1,4})?)\s*([KMBT])\b",
    re.IGNORECASE,
)
_PRICE_PATTERN = re.compile(r"(-?)\$\s*(\d{1,7}(?:,\d{3})*(?:\.\d{1,4})?)")
_PERCENT_PATTERN = re.compile(r"(-?\d{1,5}(?:\.\d{1,4})?)\s*%")
_GENERAL_NUMBER_PATTERN = re.compile(
    r"(?<![\dA-Za-z])([+-]?(?:\d{1,15}(?:,\d{3})*(?:\.\d{1,6})?|\.\d{1,6})"
    r"(?:[eE][+-]?\d{1,3})?)"
    r"(?![\dA-Za-z%$])(?!\.\d)"
)
# Magnitude suffix pattern: "56.29M", "1.2B", "250K", "1.5T"
_MAGNITUDE_PATTERN = re.compile(r"([+-]?\d{1,6}(?:\.\d{1,4})?)\s*([KMBT])\b", re.IGNORECASE)
_SUFFIX_MULTIPLIER = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
# Patterns to blank out before number extraction (dates, timestamps, IDs)
_DATE_TIMESTAMP_PATTERN = re.compile(
    r"\d{4}[-/]\d{2}[-/]\d{2}"  # 2026-02-19, 2026/02/19
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"  # optional time HH:MM or HH:MM:SS
    r"(?:\s*(?:UTC|EST|PST|ET|PT|GMT|[A-Z]{2,4}))?"  # optional timezone
)
# Markdown headings ("## 4. Section") and numbered lists ("1. Item")
_HEADING_LIST_PATTERN = re.compile(
    r"(?:^#{1,6}\s+)\d{1,3}\."  # headings: ## 4. Title
    r"|(?:^\s*)\d{1,3}\.\s",  # numbered lists: 1. Item
    re.MULTILINE,
)


# ===================================================================
# Pydantic models — deterministic phase
# ===================================================================


class ExtractedNumber(BaseModel):
    """A number extracted from the agent's response text."""

    value: float
    raw_text: str
    context: str
    is_percentage: bool = False
    is_price: bool = False
    semantic_label: str | None = None
    position: int | None = None


class NumericMatchResult(BaseModel):
    """Result of matching one extracted number against tool outputs."""

    extracted: ExtractedNumber
    matched: bool
    matched_value: float | None = None
    source_tool: str | None = None
    tolerance_used: float | None = None
    field_constrained: bool = False


class NumericAccuracyResult(BaseModel):
    """Aggregate result of deterministic numeric checking."""

    total_numbers: int
    matched_numbers: int
    unmatched_numbers: int
    accuracy: float
    details: list[NumericMatchResult]


# ===================================================================
# Pydantic models — LLM judge structured output
# ===================================================================


Severity = Literal["low", "medium", "high"]


class UnfaithfulClaim(BaseModel):
    """A claim in the response not supported by tool outputs."""

    claim: str
    reasoning: str
    severity: Severity


class FaithfulnessJudgment(BaseModel):
    """LLM judge response for faithfulness evaluation."""

    faithful: bool
    unfaithful_claims: list[UnfaithfulClaim] = Field(default_factory=list)
    score: float = Field(description="Score from 0.0 to 1.0")
    reasoning: str


class OmittedDataPoint(BaseModel):
    """A relevant data point from tool outputs that was omitted."""

    data_point: str
    source_tool: str
    relevance: str
    severity: Severity


class CompletenessJudgment(BaseModel):
    """LLM judge response for completeness evaluation."""

    complete: bool
    omitted_data: list[OmittedDataPoint] = Field(default_factory=list)
    coverage_score: float = Field(description="Score from 0.0 to 1.0")
    reasoning: str


# ===================================================================
# LLM prompts
# ===================================================================

FAITHFULNESS_SYSTEM_PROMPT = """\
You are an expert evaluator for a multi-agent financial research assistant.
Your task is to identify claims in the agent's response that are NOT supported \
by the tool outputs (ground truth data from MCP APIs).

Focus on:
- Factual claims about numbers, prices, ratios, percentages
- Directional claims ("bullish", "bearish", "up", "down") that contradict the data
- Claims about company status, sector, or classification not in the data
- Invented context or analysis not derivable from the tool outputs

Do NOT flag:
- General financial knowledge or definitions (e.g., "P/E ratio measures...")
- Reasonable interpretive commentary clearly labeled as opinion
- Formatting differences or rounding (handled by deterministic checks)

Be strict. If a claim cannot be verified from the tool outputs, flag it."""

FAITHFULNESS_USER_TEMPLATE = """\
<user_query>
{query}
</user_query>

<agent_response>
{response}
</agent_response>

<tool_outputs_ground_truth>
{tool_outputs}
</tool_outputs_ground_truth>

Identify all unfaithful claims. If the response is fully faithful, \
set faithful=true and leave unfaithful_claims empty."""

COMPLETENESS_SYSTEM_PROMPT = """\
You are an expert evaluator for a multi-agent financial research assistant.
Your task is to identify relevant data points from the tool outputs that the \
agent FAILED to include in its response to the user's query.

Consider:
- The user's query determines what's "relevant" — not all data needs reporting
- Key metrics directly answering the question should always be included
- Supporting context (e.g., 52-week range when asked about price) is nice but lower severity
- Raw internal IDs, timestamps, or metadata are NOT relevant omissions

Severity levels:
- high: A data point that directly answers part of the user's question was omitted
- medium: A data point that provides important context was omitted
- low: A supplementary data point was omitted"""

COMPLETENESS_USER_TEMPLATE = """\
<user_query>
{query}
</user_query>

<agent_response>
{response}
</agent_response>

<tool_outputs_all_data>
{tool_outputs}
</tool_outputs_all_data>

Identify data points from tool outputs that are relevant to the query but \
were omitted from the response. If the response is complete, set complete=true \
and leave omitted_data empty."""


# ===================================================================
# Numeric extraction and matching (deterministic phase)
# ===================================================================


def _extract_context(text: str, start: int, end: int, window: int = 20) -> str:
    """Extract surrounding context for a match.

    Args:
        text: Full text.
        start: Match start position.
        end: Match end position.
        window: Characters of context on each side.

    Returns:
        Context string around the match.
    """
    ctx_start = max(0, start - window)
    ctx_end = min(len(text), end + window)
    return text[ctx_start:ctx_end]


_NUMERIC_LABEL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "maximum_loss",
        re.compile(
            r"\b(?:maximum|max)[_ -]?loss\b|\bloss\s+is\s+(?:capped|limited)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "maximum_profit",
        re.compile(r"\b(?:maximum|max)[_ -]?(?:profit|gain)\b", re.IGNORECASE),
    ),
    ("breakeven", re.compile(r"\bbreak[_ -]?even\b", re.IGNORECASE)),
    (
        "strike",
        re.compile(r"\b(?:strike(?:[_ -]?price)?|exercise[_ -]?price)\b", re.IGNORECASE),
    ),
    (
        "spot",
        re.compile(r"\b(?:spot(?:[_ -]?price)?|underlying(?:[_ -]?price)?)\b", re.IGNORECASE),
    ),
    (
        "price",
        re.compile(
            r"\b(?:price|last|close|closed|closing)[_ -]?(?:price)?\b|"
            r"\b(?:is|trades?|trading)\s+at\b",
            re.IGNORECASE,
        ),
    ),
    ("bid", re.compile(r"\bbid(?:[_ -]?price)?\b", re.IGNORECASE)),
    ("ask", re.compile(r"\bask(?:[_ -]?price)?\b", re.IGNORECASE)),
    ("spread", re.compile(r"\bspread\b", re.IGNORECASE)),
    ("premium", re.compile(r"\b(?:premium|debit|cost)\b", re.IGNORECASE)),
    ("credit", re.compile(r"\bcredit\b", re.IGNORECASE)),
    ("delta", re.compile(r"\bdelta\b", re.IGNORECASE)),
    ("theta", re.compile(r"\btheta\b", re.IGNORECASE)),
    ("gamma", re.compile(r"\bgamma\b", re.IGNORECASE)),
    ("vega", re.compile(r"\bvega\b", re.IGNORECASE)),
    (
        "implied_volatility",
        re.compile(r"\b(?:implied[_ -]?volatility|IV)\b", re.IGNORECASE),
    ),
    ("pe_ratio", re.compile(r"\b(?:P\s*/\s*E|PE)[_ -]?(?:ratio)?\b", re.IGNORECASE)),
    ("market_cap", re.compile(r"\bmarket[_ -]?cap(?:italization)?\b", re.IGNORECASE)),
    ("revenue", re.compile(r"\brevenue\b", re.IGNORECASE)),
    ("yield", re.compile(r"\byield\b", re.IGNORECASE)),
    ("rate", re.compile(r"\brate\b", re.IGNORECASE)),
    ("volume", re.compile(r"\bvolume\b", re.IGNORECASE)),
    ("size", re.compile(r"\bsize\b", re.IGNORECASE)),
    (
        "return",
        re.compile(
            r"\b(?:return|change|performance|up|down|rose|fell|gained|lost)\b",
            re.IGNORECASE,
        ),
    ),
)


def _numeric_clause_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    """Return local prose/table-cell bounds without splitting decimal points."""

    def is_boundary(index: int) -> bool:
        char = text[index]
        if char in ",;\n|!?":
            return True
        if char != ".":
            return False
        return not (
            index > 0
            and index + 1 < len(text)
            and text[index - 1].isdigit()
            and text[index + 1].isdigit()
        )

    left = start
    while left > 0 and not is_boundary(left - 1):
        left -= 1
    right = end
    while right < len(text) and not is_boundary(right):
        right += 1
    return left, right


def _infer_response_numeric_label(text: str, start: int, end: int) -> str | None:
    """Infer a financial field label inside the number's own clause/cell."""
    clause_start, clause_end = _numeric_clause_bounds(text, start, end)
    prefix = text[clause_start:start]
    candidates: list[tuple[int, int, int, int, str]] = []
    for priority, (label, pattern) in enumerate(_NUMERIC_LABEL_PATTERNS):
        for match in pattern.finditer(prefix):
            tail = prefix[match.end() :]
            distance = len(tail)
            # "Price is $100" is a stronger binding than a closer label
            # following the value ("$100 up 2%"). A bare label immediately
            # before a value is equally strong. Longer unbound prefixes may be
            # postfix labels for the preceding value ("$100 spot and $105 strike").
            strong_prefix = distance <= 2 or re.fullmatch(
                r"\s*(?:is|was|are|were|at|of|=|:)\s*", tail, re.IGNORECASE
            )
            candidates.append((0 if strong_prefix else 2, distance, 0, priority, label))
    suffix = text[end:clause_end]
    for priority, (label, pattern) in enumerate(_NUMERIC_LABEL_PATTERNS):
        for match in pattern.finditer(suffix):
            # A later metric's label must not be borrowed by this value.
            if re.search(r"[+-]?\$?\s*\d", suffix[: match.start()]):
                continue
            # In "$100 up 2%", the directional word binds the following
            # percentage, not the preceding quote.
            if label == "return" and re.search(r"[+-]?\$?\s*\d", suffix[match.end() :]):
                continue
            candidates.append((1, match.start(), 1, priority, label))
    return min(candidates)[4] if candidates else None


_PAIRED_LABELS = (
    ("spot", "strike"),
    ("bid", "ask"),
    ("maximum_profit", "maximum_loss"),
)
_PAIR_NUMBER = r"(?:[A-Z]{3}\s*)?[+-]?\$?\s*\d{1,15}(?:,\d{3})*(?:\.\d{1,6})?"


def _apply_paired_numeric_labels(text: str, results: list[ExtractedNumber]) -> None:
    """Bind ordered label pairs to ordered values in compact financial prose."""

    def assign_groups(
        match: re.Match[str], groups: tuple[tuple[str, str], tuple[str, str]]
    ) -> None:
        assigned_positions: set[int] = set()
        positioned_results: list[tuple[ExtractedNumber, int]] = [
            (item, item.position) for item in results if item.position is not None
        ]
        for group_name, label in groups:
            group_start, group_end = match.span(group_name)
            within_group = [
                (item, position)
                for item, position in positioned_results
                if position not in assigned_positions and group_start <= position < group_end
            ]
            nearest = min(
                within_group,
                key=lambda candidate: abs(candidate[1] - group_start),
                default=None,
            )
            if nearest is None:
                nearest = min(
                    (
                        (item, position)
                        for item, position in positioned_results
                        if position not in assigned_positions
                    ),
                    key=lambda candidate: abs(candidate[1] - group_start),
                    default=None,
                )
                if nearest is not None and abs(nearest[1] - group_start) > 6:
                    nearest = None
            if nearest is not None:
                nearest_item, nearest_position = nearest
                nearest_item.semantic_label = label
                assigned_positions.add(nearest_position)

    ordered_pairs = [ordered for pair in _PAIRED_LABELS for ordered in (pair, pair[::-1])]
    for first_label, second_label in ordered_pairs:
        label_patterns = {
            label: next(pattern for name, pattern in _NUMERIC_LABEL_PATTERNS if name == label)
            for label in (first_label, second_label)
        }
        first = label_patterns[first_label].pattern
        second = label_patterns[second_label].pattern
        if (first_label, second_label) == ("maximum_profit", "maximum_loss"):
            second = rf"(?:{second}|\bloss\b)"
        elif (first_label, second_label) == ("maximum_loss", "maximum_profit"):
            second = rf"(?:{second}|\b(?:profit|gain)\b)"
        pattern = re.compile(
            rf"(?:{first})\s*(?:and|/|&|,)\s*(?:{second})\s*"
            rf"(?:,?\s*respectively\s*,?)?\s*"
            rf"(?:are|were|is|=|:)?\s*(?:approximately|approx\.?|about|roughly)?\s*"
            rf"(?:\(\s*)?"
            rf"(?P<first>{_PAIR_NUMBER})\s*(?:and|/|&|,)\s*"
            rf"(?P<second>{_PAIR_NUMBER})",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            assign_groups(
                match,
                (("first", first_label), ("second", second_label)),
            )

        table_pattern = re.compile(
            rf"\|\s*(?:{first})\s*\|\s*(?:{second})\s*\|\s*\r?\n"
            rf"(?:\|\s*:?-{{3,}}:?\s*\|\s*:?-{{3,}}:?\s*\|\s*\r?\n)?"
            rf"\|\s*(?P<first>{_PAIR_NUMBER})\s*\|\s*"
            rf"(?P<second>{_PAIR_NUMBER})\s*\|",
            re.IGNORECASE,
        )
        for match in table_pattern.finditer(text):
            assign_groups(
                match,
                (("first", first_label), ("second", second_label)),
            )


def _is_accounting_negative(text: str, start: int, end: int) -> bool:
    """Return True when a currency value is enclosed in accounting parentheses."""
    return text[:start].rstrip().endswith("(") and text[end:].lstrip().startswith(")")


def _infer_source_numeric_label(path: str) -> str | None:
    without_indexes = re.sub(r"\[\d+\]", "", path)
    leaf = without_indexes.rsplit(".", 1)[-1]
    normalized = re.sub(r"[^a-z0-9]+", "_", leaf.lower())
    for label, pattern in _NUMERIC_LABEL_PATTERNS:
        if pattern.search(normalized.replace("_", " ")):
            return label
    return None


def _extract_numbers(text: str) -> list[ExtractedNumber]:
    """Extract all numbers from response text with context.

    Args:
        text: Agent response text.

    Returns:
        List of extracted numbers with metadata.
    """
    # Normalize typographic minus signs without changing offsets.
    text = text.translate(str.maketrans("−–﹣－", "----"))
    results: list[ExtractedNumber] = []
    seen_positions: set[int] = set()

    # Blank out dates/timestamps so their components aren't extracted as numbers
    text = _DATE_TIMESTAMP_PATTERN.sub(lambda m: " " * len(m.group(0)), text)
    # Blank out markdown heading numbers and numbered list prefixes
    text = _HEADING_LIST_PATTERN.sub(lambda m: " " * len(m.group(0)), text)

    # 0. Dollar + magnitude ($416.2B, $3.82T, $112.0B)
    for match in _PRICE_MAGNITUDE_PATTERN.finditer(text):
        pos = match.start()
        seen_positions.add(pos)
        sign = (
            -1.0
            if match.group(1) == "-" or _is_accounting_negative(text, pos, match.end())
            else 1.0
        )
        base = float(match.group(2).replace(",", ""))
        multiplier = _SUFFIX_MULTIPLIER[match.group(3).upper()]
        results.append(
            ExtractedNumber(
                value=sign * base * multiplier,
                raw_text=match.group(0),
                context=_extract_context(text, pos, match.end()),
                semantic_label=_infer_response_numeric_label(text, pos, match.end()),
                position=pos,
            )
        )

    # 1. Prices ($XXX.XX), optionally negative (-$XXX.XX)
    for match in _PRICE_PATTERN.finditer(text):
        pos = match.start()
        if any(abs(pos - s) < 5 for s in seen_positions):
            continue
        seen_positions.add(pos)
        sign = (
            -1.0
            if match.group(1) == "-" or _is_accounting_negative(text, pos, match.end())
            else 1.0
        )
        results.append(
            ExtractedNumber(
                value=sign * float(match.group(2).replace(",", "")),
                raw_text=match.group(0),
                context=_extract_context(text, pos, match.end()),
                is_price=True,
                semantic_label=_infer_response_numeric_label(text, pos, match.end()),
                position=pos,
            )
        )

    # 2. Percentages (XX.X%)
    for match in _PERCENT_PATTERN.finditer(text):
        pos = match.start()
        if any(abs(pos - s) < 5 for s in seen_positions):
            continue
        seen_positions.add(pos)
        results.append(
            ExtractedNumber(
                value=float(match.group(1).replace(",", "")),
                raw_text=match.group(0),
                context=_extract_context(text, pos, match.end()),
                is_percentage=True,
                semantic_label=_infer_response_numeric_label(text, pos, match.end()),
                position=pos,
            )
        )

    # 3. Magnitude suffixes (56.29M, 1.2B, 250K, 1.5T)
    for match in _MAGNITUDE_PATTERN.finditer(text):
        pos = match.start()
        if any(abs(pos - s) < 5 for s in seen_positions):
            continue
        seen_positions.add(pos)
        base = float(match.group(1))
        multiplier = _SUFFIX_MULTIPLIER[match.group(2).upper()]
        results.append(
            ExtractedNumber(
                value=base * multiplier,
                raw_text=match.group(0),
                context=_extract_context(text, pos, match.end()),
                semantic_label=_infer_response_numeric_label(text, pos, match.end()),
                position=pos,
            )
        )

    # 4. General numbers (not already captured)
    for match in _GENERAL_NUMBER_PATTERN.finditer(text):
        pos = match.start()
        if any(abs(pos - s) < 5 for s in seen_positions):
            continue
        seen_positions.add(pos)
        results.append(
            ExtractedNumber(
                value=float(match.group(1).replace(",", "")),
                raw_text=match.group(0),
                context=_extract_context(text, pos, match.end()),
                semantic_label=_infer_response_numeric_label(text, pos, match.end()),
                position=pos,
            )
        )

    _apply_paired_numeric_labels(text, results)
    return results


def _extract_numbers_from_tool_responses(
    tool_calls: list[dict[str, Any]],
) -> list[tuple[str, float]]:
    """Recursively extract all numeric values from tool response dicts.

    Handles both structured JSON responses (numeric fields) and
    formatted text strings (regex extraction for embedded numbers).

    Args:
        tool_calls: List of tool call dicts with 'response' and 'tool_name'.

    Returns:
        List of (tool_name, numeric_value) tuples.
    """
    numbers: list[tuple[str, float]] = []

    def _recurse(obj: Any, source_path: str) -> None:
        if isinstance(obj, bool):
            return
        if isinstance(obj, (int, float)):
            numbers.append((source_path, float(obj)))
        elif isinstance(obj, str):
            cleaned = obj.replace(",", "").replace("$", "").replace("%", "")
            # Handle financial magnitude suffixes (K, M, B, T)
            upper = cleaned.upper().rstrip()
            if upper and upper[-1] in _SUFFIX_MULTIPLIER:
                with contextlib.suppress(ValueError):
                    val = float(upper[:-1]) * _SUFFIX_MULTIPLIER[upper[-1]]
                    numbers.append((source_path, val))
                    return
            try:
                numbers.append((source_path, float(cleaned)))
            except ValueError:
                # String contains mixed text+numbers (e.g. agent
                # formatted responses like "AAPL — $255.78").
                # Fall back to regex extraction.
                for extracted in _extract_numbers(obj):
                    numbers.append((source_path, extracted.value))
        elif isinstance(obj, dict):
            for key, value in obj.items():
                _recurse(value, f"{source_path}.{key}")
        elif isinstance(obj, list):
            for index, item in enumerate(obj):
                _recurse(item, f"{source_path}[{index}]")

    for tc in tool_calls:
        tool_name = tc.get("tool_name", "unknown")
        response = tc.get("response")
        if response:
            _recurse(response, tool_name)

    return numbers


def _extract_semantic_fields_from_tool_responses(
    tool_calls: list[dict[str, Any]],
) -> dict[str, list[Any]]:
    """Collect labeled scalar tool fields, including non-numeric constraints."""
    fields: dict[str, list[Any]] = {}

    def _recurse(obj: Any, path: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                child_path = f"{path}.{key}"
                label = _infer_source_numeric_label(child_path)
                if label is not None and not isinstance(value, (dict, list)):
                    fields.setdefault(label, []).append(value)
                _recurse(value, child_path)
        elif isinstance(obj, list):
            for index, value in enumerate(obj):
                _recurse(value, f"{path}[{index}]")

    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        response = tool_call.get("response")
        if response is not None:
            _recurse(response, str(tool_call.get("tool_name", "unknown")))
    return fields


_NEGATIVE_MAGNITUDE_CONTEXT_RE = re.compile(
    r"\b(?:down|fell|fallen|declin(?:e|ed)|decreas(?:e|ed)|dropp?ed|lower|lost|loss)\b",
    re.IGNORECASE,
)


def _allows_negative_magnitude_match(extracted: ExtractedNumber, tool_value: float) -> bool:
    """Allow positive magnitudes only when prose explicitly supplies the minus sign."""
    return (
        extracted.value >= 0
        and tool_value < 0
        and (
            extracted.semantic_label == "maximum_loss"
            or (
                extracted.semantic_label == "return"
                and _NEGATIVE_MAGNITUDE_CONTEXT_RE.search(extracted.context) is not None
            )
        )
    )


def _match_number(
    extracted: ExtractedNumber,
    tool_numbers: list[tuple[str, float]],
    semantic_fields: dict[str, list[Any]] | None = None,
) -> NumericMatchResult:
    """Match an extracted number against tool response numbers.

    Args:
        extracted: A number from the agent's response.
        tool_numbers: All numbers from tool responses.
        semantic_fields: Structured fields used to prevent cross-metric value swaps.

    Returns:
        Match result with source tool and tolerance used.
    """
    if extracted.is_price:
        tolerance = _PRICE_TOLERANCE
    elif extracted.is_percentage:
        tolerance = _PERCENT_TOLERANCE
    else:
        tolerance = _DEFAULT_TOLERANCE

    candidates = tool_numbers
    field_constrained = False
    if extracted.semantic_label and semantic_fields and extracted.semantic_label in semantic_fields:
        field_constrained = True
        candidates = [
            (source, value)
            for source, value in tool_numbers
            if _infer_source_numeric_label(source) == extracted.semantic_label
        ]

    for tool_name, tool_value in candidates:
        if abs(extracted.value - tool_value) <= tolerance:
            return NumericMatchResult(
                extracted=extracted,
                matched=True,
                matched_value=tool_value,
                source_tool=tool_name,
                tolerance_used=tolerance,
                field_constrained=field_constrained,
            )
        # Check percentage conversion (2.3 in response vs 0.023 in API)
        if extracted.is_percentage and abs(extracted.value - tool_value * 100) <= tolerance:
            return NumericMatchResult(
                extracted=extracted,
                matched=True,
                matched_value=tool_value,
                source_tool=tool_name,
                tolerance_used=tolerance,
                field_constrained=field_constrained,
            )
        # Directional prose can legitimately report a negative source as a
        # positive magnitude ("down $5.95" or "maximum loss $200").  Do not
        # apply this generally: "change is +$5.95" must not match -$5.95.
        negative_magnitude = _allows_negative_magnitude_match(extracted, tool_value)
        percentage_magnitude_match = (
            extracted.is_percentage
            and abs(abs(extracted.value) - abs(tool_value * 100)) <= tolerance
        )
        direct_magnitude_match = abs(abs(extracted.value) - abs(tool_value)) <= tolerance
        if negative_magnitude and (percentage_magnitude_match or direct_magnitude_match):
            return NumericMatchResult(
                extracted=extracted,
                matched=True,
                matched_value=tool_value,
                source_tool=tool_name,
                tolerance_used=tolerance,
                field_constrained=field_constrained,
            )
        # Relative tolerance for large numbers (volumes, market caps)
        # where magnitude suffixes cause rounding (56.29M vs 56,290,673).
        if (
            abs(extracted.value) >= _LARGE_NUMBER_THRESHOLD
            and abs(tool_value) >= _LARGE_NUMBER_THRESHOLD
        ):
            denom = max(abs(extracted.value), abs(tool_value))
            if abs(extracted.value - tool_value) / denom <= _LARGE_NUMBER_RELATIVE_TOLERANCE:
                return NumericMatchResult(
                    extracted=extracted,
                    matched=True,
                    matched_value=tool_value,
                    source_tool=tool_name,
                    tolerance_used=_LARGE_NUMBER_RELATIVE_TOLERANCE,
                    field_constrained=field_constrained,
                )

    return NumericMatchResult(
        extracted=extracted,
        matched=False,
        tolerance_used=tolerance,
        field_constrained=field_constrained,
    )


def _score_numeric(
    response: str,
    tool_calls: list[dict[str, Any]],
) -> NumericAccuracyResult:
    """Deterministic phase: check numeric accuracy.

    Args:
        response: Agent's final response text.
        tool_calls: List of tool call dicts with responses.

    Returns:
        Numeric accuracy result.
    """
    extracted = _extract_numbers(response)
    tool_numbers = _extract_numbers_from_tool_responses(tool_calls)
    semantic_fields = _extract_semantic_fields_from_tool_responses(tool_calls)

    logger.debug(
        "Numeric check: %d numbers from response, %d from tools",
        len(extracted),
        len(tool_numbers),
    )
    for e in extracted:
        logger.debug(
            "  Response number: %s (value=%s, price=%s, pct=%s)",
            e.raw_text,
            e.value,
            e.is_price,
            e.is_percentage,
        )
    for tool_name, val in tool_numbers[:20]:
        logger.debug("  Tool number: %s = %s", tool_name, val)

    if not extracted:
        return NumericAccuracyResult(
            total_numbers=0,
            matched_numbers=0,
            unmatched_numbers=0,
            accuracy=1.0,
            details=[],
        )

    details = [_match_number(e, tool_numbers, semantic_fields) for e in extracted]
    matched = sum(1 for d in details if d.matched)

    return NumericAccuracyResult(
        total_numbers=len(extracted),
        matched_numbers=matched,
        unmatched_numbers=len(extracted) - matched,
        accuracy=matched / len(extracted),
        details=details,
    )


def _format_tool_outputs_detailed(
    tool_calls: list[dict[str, Any]],
) -> str:
    """Format tool calls with full responses for judge context.

    Args:
        tool_calls: List of tool call dicts.

    Returns:
        Formatted string with tool name, args, and response.
    """
    if not tool_calls:
        return ""

    parts: list[str] = []
    for tc in tool_calls:
        name = tc.get("tool_name", "unknown")
        args = tc.get("args", {})
        response = tc.get("response")
        args_str = json.dumps(args, default=str)
        resp_str = json.dumps(response, default=str) if response else "(no response)"
        parts.append(f"[{name}] args={args_str}\nresponse={resp_str}")

    return "\n\n".join(parts)


# ===================================================================
# LLM judge functions (async)
# ===================================================================


async def _faithfulness_llm_judge(
    response: str,
    query: str,
    tool_outputs: str,
    model_id: str,
) -> FaithfulnessJudgment | None:
    """LLM semantic faithfulness check.

    Args:
        response: Agent's response text.
        query: User's query.
        tool_outputs: Tool outputs text.
        model_id: Anthropic model ID.

    Returns:
        FaithfulnessJudgment or None on error.
    """
    user_prompt = FAITHFULNESS_USER_TEMPLATE.format(
        query=query,
        response=response,
        tool_outputs=tool_outputs,
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.debug("Faithfulness LLM judge skipped — ANTHROPIC_API_KEY not set")
        return None

    try:
        return await structured_completion(
            model=model_id,
            system=FAITHFULNESS_SYSTEM_PROMPT,
            user=user_prompt,
            response_model=FaithfulnessJudgment,
        )
    except Exception:
        logger.exception("Faithfulness LLM judge failed")
        return None


async def _completeness_llm_judge(
    response: str,
    query: str,
    tool_outputs: str,
    model_id: str,
) -> CompletenessJudgment | None:
    """LLM completeness check.

    Args:
        response: Agent's response text.
        query: User's query.
        tool_outputs: Tool outputs text.
        model_id: Anthropic model ID.

    Returns:
        CompletenessJudgment or None on error.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.debug("Completeness LLM judge skipped — ANTHROPIC_API_KEY not set")
        return None

    user_prompt = COMPLETENESS_USER_TEMPLATE.format(
        query=query,
        response=response,
        tool_outputs=tool_outputs,
    )

    try:
        return await structured_completion(
            model=model_id,
            system=COMPLETENESS_SYSTEM_PROMPT,
            user=user_prompt,
            response_model=CompletenessJudgment,
        )
    except Exception:
        logger.exception("Completeness LLM judge failed")
        return None


# ===================================================================
# Scorer input builder (shared by TUI, CLI, eval runner)
# ===================================================================

_MAX_RAW_OUTPUT = 4000


def build_scorer_input(
    response_text: str,
    inner_outputs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Build faithfulness scorer input from raw MCP tool outputs.

    Converts the inner_outputs captured by CentralHubAgent into the
    dict format expected by FaithfulnessScorer.score().

    Args:
        response_text: Agent's final response.
        inner_outputs: Raw MCP tool outputs from get_inner_tool_outputs().

    Returns:
        Scorer input dict, or None if no scorable data.
    """
    tool_outputs_parts: list[str] = []
    inner_tool_calls: list[dict[str, Any]] = []
    error_count = 0

    for inner in inner_outputs:
        specialist = inner.get("specialist", "unknown")
        tool_name = inner.get("tool_name", "unknown")
        raw_output = inner.get("output", "")

        logger.info(
            "build_scorer_input: specialist=%s tool=%s output_type=%s output_preview=%.200s",
            specialist,
            tool_name,
            type(raw_output).__name__,
            str(raw_output)[:200],
        )

        if specialist == "cache":
            continue

        response: dict[str, Any] | None = None
        if isinstance(raw_output, str):
            try:
                parsed = json.loads(raw_output)
                response = (
                    parsed if isinstance(parsed, dict) else {"raw": raw_output[:_MAX_RAW_OUTPUT]}
                )
            except (json.JSONDecodeError, TypeError):
                response = {"raw": raw_output[:_MAX_RAW_OUTPUT]}
        elif isinstance(raw_output, dict):
            response = raw_output

        # Skip errored tool calls — no ground truth to score against
        if isinstance(response, dict) and response.get("isError"):
            logger.info("build_scorer_input: skipping errored %s/%s", specialist, tool_name)
            error_count += 1
            continue

        tool_outputs_parts.append(f"{specialist}/{tool_name}: {raw_output}")
        inner_tool_calls.append(
            {
                "tool_name": f"{specialist}/{tool_name}",
                "args": {},
                "response": response,
                "latency_ms": 0,
                "agent_name": specialist,
            }
        )

    if not inner_tool_calls:
        return None

    logger.info(
        "build_scorer_input: %d valid calls, %d errors skipped",
        len(inner_tool_calls),
        error_count,
    )
    return {
        "response": response_text,
        "tool_calls": [],
        "inner_tool_calls": inner_tool_calls,
        "tool_outputs": "\n\n".join(tool_outputs_parts),
        "data_available": True,
    }


# ===================================================================
# Scorer class wrappers
# ===================================================================


class FaithfulnessScorer:
    """Two-phase faithfulness scorer: deterministic numeric + LLM semantic.

    Phase 1 (deterministic): Extract numbers from response, match against
    tool response values within tolerance. Fast, no API calls.

    Phase 2 (LLM): Ask judge to identify semantic unfaithfulness (claims
    not supported by tool outputs). Catches qualitative errors.

    Example:
        >>> scorer = FaithfulnessScorer()
        >>> result = await scorer.score(output=trace_output, query="AAPL price?")
        >>> result["faithfulness_pass"]
        True
    """

    def __init__(
        self,
        model_id: str = DEFAULT_JUDGE_MODEL,
        numeric_threshold: float = 0.9,
        skip_llm: bool = False,
    ) -> None:
        """Initialize faithfulness scorer.

        Args:
            model_id: Anthropic model ID for the LLM judge phase.
            numeric_threshold: Minimum numeric accuracy to pass (0.0-1.0).
            skip_llm: If True, only run deterministic phase (faster).
        """
        self.model_id = model_id
        self.numeric_threshold = numeric_threshold
        self.skip_llm = skip_llm

    @opik.track(name="faithfulness_score")  # type: ignore[untyped-decorator]
    async def score(
        self,
        output: dict[str, Any],
        query: str = "",
    ) -> dict[str, Any]:
        """Score faithfulness of agent output against tool responses.

        Args:
            output: Model output dict from trace_to_scorer_input().
            query: Original user query.

        Returns:
            Dict with numeric_accuracy, semantic_faithfulness, and
            faithfulness_pass. Returns skipped=True when tools returned
            errors (no ground truth to compare against).
        """
        # Skip when tool calls returned errors — no ground truth available
        if not output.get("data_available", True):
            return {
                "skipped": True,
                "skip_reason": "tool_errors",
                "numeric_accuracy": None,
                "numeric_pass": None,
                "semantic_faithful": None,
                "semantic_score": None,
                "faithfulness_pass": None,
            }

        response = output.get("response", "")
        tool_calls = output.get("tool_calls", [])
        inner_tool_calls = output.get("inner_tool_calls", [])
        tool_outputs = output.get("tool_outputs", "")

        # Phase 1 uses inner MCP calls (structured JSON) for precise
        # numeric matching. Falls back to outer calls if none captured.
        numeric_ground_truth = inner_tool_calls if inner_tool_calls else tool_calls

        # Phase 1: Deterministic numeric check
        numeric_result = _score_numeric(response, numeric_ground_truth)
        numeric_pass = numeric_result.accuracy >= self.numeric_threshold
        numeric_critical_conflicts = sum(
            1
            for detail in numeric_result.details
            if detail.field_constrained and not detail.matched
        )
        numeric_critical_pass = numeric_critical_conflicts == 0

        result: dict[str, Any] = {
            "numeric_accuracy": numeric_result.accuracy,
            "numeric_total": numeric_result.total_numbers,
            "numeric_matched": numeric_result.matched_numbers,
            "numeric_unmatched": numeric_result.unmatched_numbers,
            "numeric_pass": numeric_pass,
            "numeric_critical_pass": numeric_critical_pass,
            "numeric_critical_conflicts": numeric_critical_conflicts,
            "numeric_details": [d.model_dump() for d in numeric_result.details if not d.matched],
        }

        # Phase 2: LLM semantic check
        # Always use tool_outputs text — it aggregates ALL specialist
        # responses (outer + inner). Inner capture may miss some
        # specialists, so tool_outputs is the reliable comprehensive source.
        if self.skip_llm:
            result["semantic_faithful"] = None
            result["semantic_score"] = None
            result["semantic_reasoning"] = ""
            result["unfaithful_claims"] = []
            result["faithfulness_pass"] = numeric_pass
        else:
            judgment = await _faithfulness_llm_judge(
                response=response,
                query=query,
                tool_outputs=tool_outputs,
                model_id=self.model_id,
            )

            if judgment:
                result["semantic_faithful"] = judgment.faithful
                result["semantic_score"] = judgment.score
                result["semantic_reasoning"] = judgment.reasoning
                result["unfaithful_claims"] = [c.model_dump() for c in judgment.unfaithful_claims]
                # Use the continuous score for pass/fail — the boolean
                # `faithful` field can contradict the score (e.g. score=1.0
                # but faithful=False), so the score is more reliable.
                # The semantic judge may validate legitimate derived values,
                # but it cannot override a deterministic contradiction against
                # the same labeled source field (for example swapped spot and
                # strike, or a fabricated quote).
                result["faithfulness_pass"] = judgment.score >= 0.8 and numeric_critical_pass
            else:
                result["semantic_faithful"] = None
                result["semantic_score"] = None
                result["semantic_reasoning"] = ""
                result["unfaithful_claims"] = []
                result["faithfulness_pass"] = False
                result["error"] = "LLM judge call failed"

        return result


class CompletenessScorer:
    """LLM-based completeness scorer.

    Verifies the agent used all relevant data from tool outputs to
    answer the user's query. Identifies omitted data points.

    Example:
        >>> scorer = CompletenessScorer()
        >>> result = await scorer.score(output=trace_output, query="AAPL price?")
        >>> result["completeness_pass"]
        True
    """

    def __init__(
        self,
        model_id: str = DEFAULT_JUDGE_MODEL,
        coverage_threshold: float = 0.7,
    ) -> None:
        """Initialize completeness scorer.

        Args:
            model_id: Anthropic model ID for the judge.
            coverage_threshold: Minimum coverage score to pass (0.0-1.0).
        """
        self.model_id = model_id
        self.coverage_threshold = coverage_threshold

    @opik.track(name="completeness_score")  # type: ignore[untyped-decorator]
    async def score(
        self,
        output: dict[str, Any],
        query: str = "",
    ) -> dict[str, Any]:
        """Score completeness of agent output.

        Args:
            output: Model output dict from trace_to_scorer_input().
            query: Original user query.

        Returns:
            Dict with coverage_score, omitted_data, and completeness_pass.
            Returns skipped=True when tools returned errors.
        """
        # Skip when tool calls returned errors — no data to check coverage against
        if not output.get("data_available", True):
            return {
                "skipped": True,
                "skip_reason": "tool_errors",
                "complete": None,
                "coverage_score": None,
                "completeness_pass": None,
            }

        response = output.get("response", "")
        tool_outputs = output.get("tool_outputs", "")

        # Use tool_outputs text (aggregated from all specialists).
        # Inner capture may miss some specialists, so tool_outputs
        # is the reliable comprehensive source for the LLM judge.
        judgment = await _completeness_llm_judge(
            response=response,
            query=query,
            tool_outputs=tool_outputs,
            model_id=self.model_id,
        )

        if judgment:
            high_severity = sum(1 for o in judgment.omitted_data if o.severity == "high")
            return {
                "complete": judgment.complete,
                "coverage_score": judgment.coverage_score,
                # Coverage score is the LLM's overall assessment.
                # High omission count is diagnostic — a single debatable
                # "high" label shouldn't veto an otherwise strong result.
                "completeness_pass": judgment.coverage_score >= self.coverage_threshold,
                "omitted_count": len(judgment.omitted_data),
                "omitted_high_severity": high_severity,
                "omitted_data": [o.model_dump() for o in judgment.omitted_data],
                "reasoning": judgment.reasoning,
            }

        return {
            "complete": None,
            "coverage_score": None,
            "completeness_pass": False,
            "omitted_count": None,
            "omitted_high_severity": None,
            "omitted_data": [],
            "reasoning": "",
            "error": "LLM judge call failed",
        }
