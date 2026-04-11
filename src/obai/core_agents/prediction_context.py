"""Extract and format prediction market context from MCP tool outputs.

Parses structured MCP tool results captured in _inner_tool_outputs
to build compact market identifier payloads for durable storage
and hub input injection.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_PREDICTION_SPECIALIST = "Prediction Markets Agent"
_TERMINAL_PREDICTION_MARKER = "__TERMINAL_TOOL_OUTPUT__:prediction_market_analysis:"
_POLYMARKET_URL_RE = re.compile(r"https://(?:www\.)?polymarket\.com/[^\s)\]\}<>'\"`]+")
_CONDITION_ID_RE = re.compile(r"\b0x[a-fA-F0-9]{40,}\b")
_JSON_SLUG_RE = re.compile(r"""["']slug["']\s*:\s*["']([^"']+)["']""", re.IGNORECASE)
_LINE_SLUG_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?"
    r"(?:[A-Za-z][A-Za-z ]{0,30}\s+)?`?slug`?\s*:\s*"
    r"`?([A-Za-z0-9][A-Za-z0-9._-]{1,})`?"
)

_MAX_CONTEXT_MARKETS = 8

# Tools that carry market identifiers worth extracting.
_MARKET_LIST_TOOLS = {"search_prediction_markets", "compare_prediction_markets"}
_SINGLE_MARKET_TOOLS = {"get_market_details", "get_market_snapshot"}
_EVENT_TOOLS = {"explore_trending_markets"}

# Fields not captured from tool output — skip silently.
_SKIP_TOOLS = {
    "prediction_passthrough",
    "get_price_history",
    "get_trade_flow",
    "get_top_holders",
    "get_trader_leaderboard",
    "get_wallet_activity",
    "get_wallet_profile",
    "backtest_prediction_setup",
}


def extract_prediction_context(
    inner_tool_outputs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Build a compact context payload from raw MCP tool outputs.

    Args:
        inner_tool_outputs: List of {specialist, tool_name, output} dicts
            captured during the prediction specialist's execution.

    Returns:
        Structured context dict with deduped market identifiers,
        or None if no prediction market data was found.
    """
    # Track markets by condition_id, preferring entries with more fields.
    market_by_cid: dict[str, dict[str, Any]] = {}

    for entry in inner_tool_outputs:
        if entry.get("specialist") != _PREDICTION_SPECIALIST:
            continue
        tool_name = entry.get("tool_name", "")
        if tool_name in _SKIP_TOOLS:
            continue

        parsed = _parse_tool_output(entry.get("output"))
        if parsed is None:
            continue

        raw_markets = _extract_markets_from_tool(tool_name, parsed)
        for raw in raw_markets:
            cid = raw.get("condition_id", "")
            if not cid:
                continue
            normalized = _normalize_market(raw)
            existing = market_by_cid.get(cid)
            if existing is None or len(normalized) > len(existing):
                market_by_cid[cid] = normalized

    markets = list(market_by_cid.values())

    if not markets:
        return None

    return {
        "type": "prediction_market_context",
        "venue": "polymarket",
        "markets": markets,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


def format_context_for_hub(contexts: list[dict[str, Any]]) -> str:
    """Render stored context payloads into a plain-text block for the hub.

    Deduplicates by condition_id across context entries and caps at 8 markets.

    Args:
        contexts: List of payload dicts from SessionContextStore.read_context().

    Returns:
        Plain-text context block, or empty string if no markets.
    """
    seen: set[str] = set()
    unique_markets: list[dict[str, Any]] = []

    for ctx in contexts:
        for market in ctx.get("markets", []):
            cid = market.get("condition_id", "")
            if cid and cid not in seen:
                seen.add(cid)
                unique_markets.append(market)
            if len(unique_markets) >= _MAX_CONTEXT_MARKETS:
                break
        if len(unique_markets) >= _MAX_CONTEXT_MARKETS:
            break

    if not unique_markets:
        return ""

    lines = [
        "## Prior Prediction Market Context",
        "",
        "These are identifiers from the current session only."
        " Use them only to disambiguate follow-ups.",
        "Refresh all prices, spreads, depth, liquidity, holder data,"
        " and wallet activity via prediction_market_analysis.",
        "",
    ]

    for i, market in enumerate(unique_markets, 1):
        lines.append(f"- Market {i}:")
        if "question" in market:
            lines.append(f"  - question: {market['question']}")
        if "slug" in market:
            lines.append(f"  - slug: {market['slug']}")
        if "market_url" in market:
            lines.append(f"  - market_url: {market['market_url']}")
        if "condition_id" in market:
            lines.append(f"  - condition_id: {market['condition_id']}")
        token_ids = market.get("token_ids")
        if token_ids:
            pairs = ", ".join(f"{k}={v}" for k, v in token_ids.items())
            lines.append(f"  - token_ids: {pairs}")
        if "end_date" in market:
            lines.append(f"  - end_date: {market['end_date']}")
        if market.get("neg_risk"):
            lines.append("  - neg_risk: true (affects order book interpretation)")
        if market.get("accepting_orders") is False:
            lines.append("  - accepting_orders: false (market closed for trading)")
        prices = market.get("last_known_prices", {})
        ts = prices.get("context_saved_at")
        if ts:
            lines.append(f"  - context saved at: {ts}")

    return "\n".join(lines)


# --- Validation ---


def validate_prediction_relay(hub_text: str, passthrough: str) -> bool:
    """Check whether the hub faithfully relayed prediction output.

    Checks identifier preservation, not full response structure. Prediction
    answers are intentionally dynamic, but durable Polymarket identifiers
    must not be dropped or invented by hub synthesis.

    Args:
        hub_text: The hub LLM's final response text.
        passthrough: The raw prediction specialist output.

    Returns:
        True if hub preserved all critical identifiers.
    """
    if not hub_text.strip():
        return False

    clean = _strip_terminal_prediction_marker(passthrough)
    required = _extract_relay_identifiers(clean)
    actual = _extract_relay_identifiers(hub_text)

    missing_urls = required["urls"] - actual["urls"]
    if missing_urls:
        logger.warning("Relay validation: hub dropped URLs: %s", missing_urls)
        return False
    missing_slugs = required["slugs"] - actual["slugs"]
    if missing_slugs:
        logger.warning("Relay validation: hub dropped slugs: %s", missing_slugs)
        return False
    missing_cids = required["condition_ids"] - actual["condition_ids"]
    if missing_cids:
        logger.warning("Relay validation: hub dropped condition_ids: %s", missing_cids)
        return False

    # Hub may rephrase dynamic analysis, but it must not introduce durable
    # Polymarket identifiers that were not in the specialist output.
    invented_urls = actual["urls"] - required["urls"]
    if invented_urls:
        logger.warning("Relay validation: hub invented URLs: %s", invented_urls)
        return False
    invented_slugs = actual["explicit_slugs"] - required["slugs"]
    if invented_slugs:
        logger.warning("Relay validation: hub invented slugs: %s", invented_slugs)
        return False
    return True


def _strip_terminal_prediction_marker(text: str) -> str:
    """Remove terminal wrapper from prediction output, if present."""
    idx = text.find(_TERMINAL_PREDICTION_MARKER)
    if idx == -1:
        return text

    after_marker = text[idx + len(_TERMINAL_PREDICTION_MARKER) :]
    separator = after_marker.find("\n\n")
    if separator == -1:
        return after_marker.lstrip("\n")
    return after_marker[separator + 2 :]


def _extract_relay_identifiers(text: str) -> dict[str, set[str]]:
    """Extract durable prediction-market identifiers from text."""
    urls = _extract_polymarket_urls(text)
    explicit_slugs = _extract_explicit_slugs(text)
    url_slugs = _extract_url_slugs(urls)
    return {
        "urls": urls,
        "slugs": explicit_slugs | url_slugs,
        "explicit_slugs": explicit_slugs,
        "condition_ids": set(_CONDITION_ID_RE.findall(text)),
    }


def _extract_polymarket_urls(text: str) -> set[str]:
    """Extract normalized Polymarket URLs from text."""
    urls: set[str] = set()
    for match in _POLYMARKET_URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        if url.endswith("/"):
            url = url[:-1]
        urls.add(url)
    return urls


def _extract_url_slugs(urls: set[str]) -> set[str]:
    """Extract event/market slug path components from Polymarket URLs."""
    slugs: set[str] = set()
    for url in urls:
        parsed = urlparse(url)
        if not parsed.netloc.endswith("polymarket.com"):
            continue
        parts = [part for part in parsed.path.split("/") if part]
        for prefix in ("event", "market"):
            if prefix in parts:
                index = parts.index(prefix)
                if index + 1 < len(parts):
                    slugs.add(parts[index + 1])
                break
    return slugs


def _extract_explicit_slugs(text: str) -> set[str]:
    """Extract slugs from explicit slug fields or lines."""
    slugs = {match.group(1).strip() for match in _JSON_SLUG_RE.finditer(text)}
    slugs.update(match.group(1).strip() for match in _LINE_SLUG_RE.finditer(text))
    return {slug.rstrip(".,;:!?") for slug in slugs if slug}


# --- Internal helpers ---


def _parse_tool_output(raw_output: Any) -> dict[str, Any] | None:
    """Safely parse MCP tool output (may be JSON string or dict)."""
    if isinstance(raw_output, dict):
        return raw_output
    if isinstance(raw_output, str):
        try:
            parsed = json.loads(raw_output)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def _extract_markets_from_tool(
    tool_name: str,
    parsed: dict[str, Any],
) -> list[dict[str, Any]]:
    """Dispatch market extraction by tool name."""
    if tool_name in _MARKET_LIST_TOOLS:
        return list(parsed.get("markets", []))

    if tool_name in _SINGLE_MARKET_TOOLS:
        return [parsed]

    if tool_name in _EVENT_TOOLS:
        markets: list[dict[str, Any]] = []
        for event in parsed.get("events", []):
            markets.extend(event.get("markets", []))
        return markets

    return []


def _normalize_market(raw: dict[str, Any]) -> dict[str, Any]:
    """Extract canonical fields from a raw market dict.

    Omits any field not present in the source — never invents data.
    """
    market: dict[str, Any] = {}

    # Direct fields
    for key in (
        "question",
        "slug",
        "market_url",
        "condition_id",
        "outcomes",
        "end_date",
        "active",
        "accepting_orders",
        "neg_risk",
    ):
        if key in raw and raw[key] is not None:
            market[key] = raw[key]

    # Token IDs: from clob_token_ids (list aligned with outcomes)
    # or from outcome_books (list of dicts with token_id)
    token_ids = _extract_token_ids(raw)
    if token_ids:
        market["token_ids"] = token_ids

    # Last known prices (stale-aware)
    prices = _extract_prices(raw)
    if prices:
        market["last_known_prices"] = prices

    return market


def _extract_token_ids(raw: dict[str, Any]) -> dict[str, str] | None:
    """Build outcome->token_id mapping from available sources."""
    outcomes = raw.get("outcomes", [])

    # Source 1: clob_token_ids (from get_market_details, search)
    clob_ids = raw.get("clob_token_ids")
    if clob_ids and outcomes and len(clob_ids) == len(outcomes):
        return {str(outcome): str(tid) for outcome, tid in zip(outcomes, clob_ids, strict=True)}

    # Source 2: outcome_books (from get_market_snapshot, compare)
    books = raw.get("outcome_books")
    if books and isinstance(books, list):
        result: dict[str, str] = {}
        for book in books:
            outcome = book.get("outcome", "")
            tid = book.get("token_id", "")
            if outcome and tid:
                result[str(outcome)] = str(tid)
        return result if result else None

    return None


def _extract_prices(raw: dict[str, Any]) -> dict[str, Any] | None:
    """Build last_known_prices from available price fields."""
    prices: dict[str, Any] = {}

    if "outcome_prices" in raw:
        prices["outcome_prices"] = raw["outcome_prices"]
    if "best_bid" in raw:
        prices["best_bid"] = raw["best_bid"]
    if "best_ask" in raw:
        prices["best_ask"] = raw["best_ask"]
    if "spread" in raw:
        prices["spread"] = raw["spread"]
    if "liquidity" in raw:
        prices["liquidity"] = raw["liquidity"]

    if not prices:
        return None

    prices["context_saved_at"] = datetime.now(timezone.utc).isoformat()
    return prices
