"""Prediction market relay validation.

Checks that hub synthesis does not hallucinate Polymarket identifiers.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_TERMINAL_PREDICTION_MARKER = "__TERMINAL_TOOL_OUTPUT__:prediction_market_analysis:"
_POLYMARKET_URL_RE = re.compile(r"https://(?:www\.)?polymarket\.com/[^\s)\]\}<>'\"`]+")
_CONDITION_ID_RE = re.compile(r"\b0x[a-fA-F0-9]{40,}\b")
_JSON_SLUG_RE = re.compile(r"""["']slug["']\s*:\s*["']([^"']+)["']""", re.IGNORECASE)
_LINE_SLUG_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?"
    r"(?:[A-Za-z][A-Za-z ]{0,30}\s+)?`?slug`?\s*:\s*"
    r"`?([A-Za-z0-9][A-Za-z0-9._-]{1,})`?"
)


# --- Validation ---


def validate_prediction_relay(
    hub_text: str,
    passthrough: str,
    allowed_context: str = "",
) -> bool:
    """Check whether the hub faithfully relayed prediction output.

    The hub must not introduce durable Polymarket identifiers (URLs,
    slugs) that were absent from the specialist output.  Omitting
    identifiers is allowed — the hub may focus on a subset of markets.

    Args:
        hub_text: The hub LLM's final response text.
        passthrough: The raw prediction specialist output.
        allowed_context: Prior-session context intentionally injected into the
            hub prompt. Identifiers from this text are allowed in hub output.

    Returns:
        True if hub did not hallucinate identifiers.
    """
    if not hub_text.strip():
        return False

    clean = _strip_terminal_prediction_marker(passthrough)
    required = _extract_relay_identifiers(clean)
    actual = _extract_relay_identifiers(hub_text)
    allowed: dict[str, set[str]] = (
        _extract_relay_identifiers(allowed_context)
        if allowed_context
        else {
            "urls": set(),
            "slugs": set(),
            "explicit_slugs": set(),
            "condition_ids": set(),
        }
    )

    # Hub may focus on a subset of markets (e.g., a trade memo for the
    # best opportunity out of several analyzed).  Dropping identifiers is
    # valid editorial judgement.  Log for diagnostics but do not reject.
    missing_urls = required["urls"] - actual["urls"]
    if missing_urls:
        logger.info("Relay note: hub omitted URLs (focus/summary): %s", missing_urls)
    missing_slugs = required["slugs"] - actual["slugs"]
    if missing_slugs:
        logger.info("Relay note: hub omitted slugs (focus/summary): %s", missing_slugs)

    # Hub must not introduce durable Polymarket identifiers that were
    # not in the specialist output — that would be hallucination.
    invented_urls = actual["urls"] - required["urls"] - allowed["urls"]
    if invented_urls:
        logger.warning("Relay validation: hub invented URLs: %s", invented_urls)
        return False
    invented_slugs = actual["explicit_slugs"] - required["slugs"] - allowed["slugs"]
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
