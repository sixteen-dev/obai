"""Data-quality flags for live option quotes.

Pure functions: no I/O, no clock, no settings. The tools pass in the current
time and the age bound, attach the flags, and leave every value as the
provider sent it -- a flag is a finding, never a correction.

The quote vocabulary and its precedence mirror the options-backtest engine's
``QuoteStatus`` (``options_backtest.data.records``), and the stale bound is
inclusive like its ``max_age_ns``, so a quote means the same thing on both
servers.
"""

from collections import Counter
from enum import StrEnum
from typing import Any

_NS_PER_SECOND = 1_000_000_000
_DELAYED = "DELAYED"


class QuoteStatus(StrEnum):
    """Validity of one bid/ask observation."""

    VALID = "valid"
    LOCKED = "locked"
    NO_BID = "no_bid"
    CROSSED = "crossed"
    ZERO_ASK = "zero_ask"
    NEGATIVE = "negative"
    MISSING = "missing"


# Statuses whose bid/ask still form a usable price (a midpoint means something).
_PRICED = frozenset({QuoteStatus.VALID, QuoteStatus.LOCKED, QuoteStatus.NO_BID})


def quote_status(
    bid: float | None,
    ask: float | None,
    *,
    bid_size: float | None,
    ask_size: float | None,
) -> QuoteStatus:
    """Classify a quote; checks run in order and the first match wins.

    Args:
        bid: Bid price, or None when absent.
        ask: Ask price, or None when absent.
        bid_size: Bid size, or None when absent.
        ask_size: Ask size, or None when absent.

    Returns:
        MISSING without both prices; NEGATIVE for any negative price or size;
        then ZERO_ASK, CROSSED (bid > ask), LOCKED (bid == ask), NO_BID
        (bid == 0); otherwise VALID.
    """
    if bid is None or ask is None:
        return QuoteStatus.MISSING
    sizes = [size for size in (bid_size, ask_size) if size is not None]
    if min(bid, ask) < 0 or any(size < 0 for size in sizes):
        return QuoteStatus.NEGATIVE
    if ask == 0:
        return QuoteStatus.ZERO_ASK
    if bid > ask:
        return QuoteStatus.CROSSED
    if bid == ask:
        return QuoteStatus.LOCKED
    if bid == 0:
        return QuoteStatus.NO_BID
    return QuoteStatus.VALID


def quote_flags(
    quote: dict[str, Any],
    *,
    now_ns: int,
    max_quote_age_s: int,
    bid_key: str = "bid",
    ask_key: str = "ask",
    time_key: str = "last_updated",
) -> list[str]:
    """Return the flags for one quote: its status and its age.

    Args:
        quote: Filtered quote dict.
        now_ns: Retrieval time, Unix nanoseconds.
        max_quote_age_s: Oldest quote age still current (inclusive).
        bid_key: Key holding the bid in ``quote``.
        ask_key: Key holding the ask in ``quote``.
        time_key: Key holding the quote time (Unix nanoseconds).

    Returns:
        ``quote_<status>`` unless VALID, then ``quote_stale`` past the bound or
        ``quote_time_unknown`` without a timestamp.

    Raises:
        ValueError: If max_quote_age_s is not positive.
    """
    if max_quote_age_s <= 0:
        raise ValueError(f"max_quote_age_s must be positive, got {max_quote_age_s}")
    flags: list[str] = []
    status = quote_status(
        quote.get(bid_key),
        quote.get(ask_key),
        bid_size=quote.get("bid_size"),
        ask_size=quote.get("ask_size"),
    )
    if status is not QuoteStatus.VALID:
        flags.append(f"quote_{status}")
    quoted_at = quote.get(time_key)
    if quoted_at is None:
        flags.append("quote_time_unknown")
    elif now_ns - quoted_at > max_quote_age_s * _NS_PER_SECOND:
        flags.append("quote_stale")
    return flags


def _below_intrinsic(contract: dict[str, Any]) -> bool:
    """True when the quote midpoint is under the option's intrinsic value."""
    quote = contract.get("last_quote") or {}
    bid, ask = quote.get("bid"), quote.get("ask")
    spot, strike = contract.get("underlying_price"), contract.get("strike_price")
    if spot is None or strike is None or bid is None or ask is None:
        return False
    status = quote_status(bid, ask, bid_size=quote.get("bid_size"), ask_size=quote.get("ask_size"))
    if status not in _PRICED:
        return False
    intrinsic = (
        max(strike - spot, 0.0)
        if contract.get("contract_type") == "put"
        else max(spot - strike, 0.0)
    )
    return bool((bid + ask) / 2 < intrinsic)


def contract_flags(contract: dict[str, Any], *, now_ns: int, max_quote_age_s: int) -> list[str]:
    """Return every flag for one filtered chain or contract snapshot.

    Args:
        contract: Contract dict from ``filter_option_chain_snapshot``.
        now_ns: Retrieval time, Unix nanoseconds.
        max_quote_age_s: Oldest quote age still current (inclusive).

    Returns:
        Flags in a fixed order: quote, underlying, intrinsic, Greeks.
    """
    quote = contract.get("last_quote")
    flags = (
        quote_flags(quote, now_ns=now_ns, max_quote_age_s=max_quote_age_s)
        if quote
        else ["quote_missing"]
    )
    if contract.get("underlying_price") is None:
        flags.append("underlying_missing")
    elif contract.get("underlying_timeframe") == _DELAYED:
        flags.append("underlying_delayed")
    if _below_intrinsic(contract):
        flags.append("below_intrinsic")
    greeks = contract.get("greeks") or {}
    if greeks.get("delta") is None or contract.get("implied_volatility") is None:
        flags.append("greeks_missing")
    return flags


def annotate_contracts(
    contracts: list[dict[str, Any]], *, now_ns: int, max_quote_age_s: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach ``quality_flags`` to flagged contracts and summarize the set.

    Clean contracts gain no field. The summary covers every contract passed
    in, so it stays true after the response is trimmed for size.

    Args:
        contracts: Filtered contracts; not modified.
        now_ns: Retrieval time, Unix nanoseconds.
        max_quote_age_s: Oldest quote age still current (inclusive).

    Returns:
        The annotated copies and a ``data_quality`` summary.
    """
    annotated: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for contract in contracts:
        flags = contract_flags(contract, now_ns=now_ns, max_quote_age_s=max_quote_age_s)
        counts.update(flags)
        annotated.append({**contract, "quality_flags": flags} if flags else dict(contract))
    summary = {
        "checked_at_ns": now_ns,
        "max_quote_age_seconds": max_quote_age_s,
        "contracts_checked": len(contracts),
        "contracts_flagged": sum(1 for contract in annotated if "quality_flags" in contract),
        "flag_counts": dict(sorted(counts.items())),
    }
    return annotated, summary
