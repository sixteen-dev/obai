"""Sequencing validation for OBaI multi-agent queries.

This module validates that agents and tools are called in the correct order
for queries with dependencies. For example:
- "What's Palantir trading at?" requires: screener → market_data
- "AAPL options near the money" requires: market_data → options

OpenAI Evals can't validate sequences because it only sees output_tools,
not the order they were called.
"""

from dataclasses import dataclass


@dataclass
class SequenceResult:
    """Result of sequence validation."""

    is_correct: bool
    expected_sequence: list[str]
    actual_sequence: list[str]
    missing: list[str]
    extra: list[str]
    out_of_order: list[tuple[str, str]]  # (expected_before, expected_after)
    reason: str | None = None


def validate_sequence(
    actual_sequence: list[str],
    expected_sequence: list[str],
    strict: bool = True,
) -> SequenceResult:
    """Validate that agents/tools were called in the correct order.

    Args:
        actual_sequence: Actual order of calls (e.g., ["screener", "market_data"]).
        expected_sequence: Expected order of calls.
        strict: If True, require exact sequence. If False, only check ordering.

    Returns:
        SequenceResult with validation details.

    Example:
        >>> result = validate_sequence(
        ...     actual_sequence=["market_data", "screener"],
        ...     expected_sequence=["screener", "market_data"],
        ... )
        >>> result.is_correct
        False
        >>> result.out_of_order
        [("screener", "market_data")]
    """
    # Check for missing items
    missing = [item for item in expected_sequence if item not in actual_sequence]

    # Check for extra items (only relevant if strict)
    extra = [item for item in actual_sequence if item not in expected_sequence]

    # Check ordering
    out_of_order: list[tuple[str, str]] = []

    for i, expected_item in enumerate(expected_sequence[:-1]):
        next_expected = expected_sequence[i + 1]

        # Find positions in actual
        if expected_item in actual_sequence and next_expected in actual_sequence:
            actual_pos = actual_sequence.index(expected_item)
            next_actual_pos = actual_sequence.index(next_expected)

            if actual_pos > next_actual_pos:
                out_of_order.append((expected_item, next_expected))

    # Determine if correct
    if strict:
        # Exact match required
        is_correct = (
            actual_sequence == expected_sequence and len(missing) == 0 and len(out_of_order) == 0
        )
    else:
        # Only check that expected items are present and in order
        is_correct = len(missing) == 0 and len(out_of_order) == 0

    # Build reason
    reason: str | None = None
    if not is_correct:
        if missing:
            reason = f"Missing: {missing}"
        elif out_of_order:
            pairs = [f"{a} should come before {b}" for a, b in out_of_order]
            reason = f"Out of order: {'; '.join(pairs)}"
        elif extra and strict:
            reason = f"Unexpected calls: {extra}"

    return SequenceResult(
        is_correct=is_correct,
        expected_sequence=expected_sequence,
        actual_sequence=actual_sequence,
        missing=missing,
        extra=extra,
        out_of_order=out_of_order,
        reason=reason,
    )


# Common dependency sequences
DEPENDENCY_SEQUENCES: dict[str, list[str]] = {
    # Ticker lookup queries
    "ticker_then_price": ["screener", "market_data"],
    "ticker_then_fundamentals": ["screener", "fundamentals"],
    # Options queries that need current price
    "price_then_options": ["market_data", "options"],
    # Move explanation queries
    "price_then_news": ["market_data", "events_news"],
    # Screening then analysis
    "screen_then_analyze": ["screener", "fundamentals"],
}


def get_expected_sequence(query_type: str) -> list[str] | None:
    """Get expected sequence for a query type.

    Args:
        query_type: Type of query (e.g., "ticker_then_price").

    Returns:
        Expected sequence or None if no dependency.
    """
    return DEPENDENCY_SEQUENCES.get(query_type)
