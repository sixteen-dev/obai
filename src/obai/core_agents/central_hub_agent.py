"""Central Hub Agent for routing queries to specialist agents.

This agent acts as the central hub for a team of 8 specialist agents:
    - Fundamentals Agent: Company financials and ratios
    - Market Data Agent: Prices and technical indicators
    - Events/News Agent: News, earnings, dividends
    - Options Agent: Options chains, Greeks, implied volatility (Massive)
    - Screener Agent: Stock screening and ticker discovery
    - Portfolio Agent: Portfolio parsing, risk preferences, ETF holdings
    - Strategy Agent: Trading strategy design, backtesting, optimization
    - Prediction Markets Agent: Polymarket analysis and trade ideas
    - Crypto Agent: Coinbase spot crypto data, backtests, and artifacts

The central hub uses the "agents-as-tools" pattern (not handoffs):
    1. Understands user intent
    2. Calls specialist agents as tools (central hub stays in control)
    3. Receives outputs from ALL called specialists
    4. Synthesizes comprehensive response from all tool outputs

Why agents-as-tools instead of handoffs:
    - Handoffs transfer control away; hub can't call multiple agents
    - Tools return output to hub; it can call ALL relevant specialists
    - Enables multi-domain queries: price + fundamentals + news in one response
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents import Agent, ModelSettings, Runner, Tool, function_tool
from agents.agent import AgentToolStreamEvent
from agents.items import ItemHelpers, MessageOutputItem
from agents.run import RunConfig
from agents.run_context import RunContextWrapper
from agents.sandbox import Manifest, SandboxAgent, SandboxPathGrant, SandboxRunConfig
from agents.sandbox.capabilities import CompactionModelInfo
from agents.sandbox.capabilities.skills import LocalDirLazySkillSource, Skills
from agents.sandbox.entries import LocalDir
from agents.sandbox.sandboxes.unix_local import UnixLocalSandboxClient
from agents.stream_events import RawResponsesStreamEvent, RunItemStreamEvent
from openai.types.responses import ResponseTextDeltaEvent
from openai.types.responses.response_create_params import ContextManagement
from openai.types.shared import Reasoning

if TYPE_CHECKING:
    from agents import Session
    from agents.guardrail import InputGuardrail

from .base_agent import BaseAgent
from .cache import QueryCache
from .config import ReasoningEffort, Verbosity, get_cache_config, get_config
from .crypto_agent import CryptoAgent
from .events_news_agent import EventsNewsAgent
from .fundamentals_agent import FundamentalsAgent
from .guardrails import create_input_guardrail
from .logging_config import configure_file_logging
from .market_data_agent import MarketDataAgent
from .mcp import clear_tool_cache
from .options_agent import OptionsAgent
from .portfolio_agent import PortfolioAgent
from .prediction_context import validate_prediction_relay
from .prediction_markets_agent import PredictionMarketsAgent
from .preferences import _store as _prefs_store
from .preferences import get_preferences, set_preferences
from .prompt_loader import load_prompt
from .research_agent import ResearchAgent
from .response_assembly import AnswerAccumulator
from .screener_agent import ScreenerAgent
from .strategy_agent import StrategyAgent
from .tracing import init_opik

logger = logging.getLogger(__name__)

HUB_SKILLS_DIR = Path(__file__).resolve().parent / "hub_skills"


@dataclass(frozen=True)
class StrategyPassthrough:
    """Strategy output that should bypass hub synthesis and go directly to the user.

    The strategy agent is a terminal author, not an evidence supplier.
    Its output is already a complete, structured decision artifact
    (verdict, evidence, JSON, handoff metadata) that the hub should
    not rewrite, summarize, or synthesize into its own response.
    """

    content: str
    kind: str  # marker label only: "completed", "pending", or "other"


@dataclass(frozen=True)
class PredictionPassthroughEvent:
    """Emitted by hub.run() when hub relay fails validation.

    Clients should render ``content`` as the final assistant response
    instead of the hub's synthesized text.
    """

    content: str


@dataclass(frozen=True)
class CryptoPassthroughEvent:
    """Emitted by hub.run() for terminal crypto specialist output."""

    content: str


@dataclass(frozen=True)
class StrategyPassthroughEvent:
    """Emitted by hub.run() for terminal strategy specialist output.

    Makes strategy relay deterministic like prediction/crypto: the client
    renders ``content`` verbatim so hub-authored preamble (e.g. retry
    narration after a handoff-format rejection) can never prefix the
    nine-section deliverable.
    """

    content: str


@dataclass
class CryptoPassthroughState:
    """Mutable run-scoped crypto passthrough holder.

    Agent SDK tool execution may run in a copied context. Mutating a shared
    holder preserves per-run isolation while keeping child-task writes visible
    to the parent stream loop.
    """

    content: str | None = None


# Module-level storage for strategy passthrough (reset per query)
_strategy_passthrough: StrategyPassthrough | None = None


def _set_strategy_passthrough(content: str, kind: str) -> None:
    """Store strategy output for passthrough to user."""
    global _strategy_passthrough
    _strategy_passthrough = StrategyPassthrough(content=content, kind=kind)
    logger.info("Strategy passthrough set (kind=%s, len=%d)", kind, len(content))
    # Capture full output for tracing/scoring since the hub only sees a stub
    _inner_tool_outputs.append(
        {
            "specialist": "Strategy Agent",
            "tool_name": "strategy_passthrough",
            "output": content,
        }
    )


def _get_strategy_passthrough() -> StrategyPassthrough | None:
    """Get stored strategy passthrough if set."""
    return _strategy_passthrough


def _clear_strategy_passthrough() -> None:
    """Reset strategy passthrough state (call between queries)."""
    global _strategy_passthrough
    _strategy_passthrough = None


# Prediction-market passthrough (same pattern as strategy)
_prediction_passthrough: str | None = None


def _set_prediction_passthrough(content: str) -> None:
    """Store prediction-market output for tracing and potential passthrough."""
    global _prediction_passthrough
    _prediction_passthrough = content
    logger.info("Prediction passthrough set (len=%d)", len(content))
    _inner_tool_outputs.append(
        {
            "specialist": "Prediction Markets Agent",
            "tool_name": "prediction_passthrough",
            "output": content,
        }
    )


def _clear_prediction_passthrough() -> None:
    """Reset prediction passthrough state (call between queries)."""
    global _prediction_passthrough
    _prediction_passthrough = None


_crypto_passthrough: ContextVar[CryptoPassthroughState | None] = ContextVar(
    "crypto_passthrough",
    default=None,
)


def _get_crypto_passthrough_state() -> CryptoPassthroughState:
    state = _crypto_passthrough.get()
    if state is None:
        state = CryptoPassthroughState()
        _crypto_passthrough.set(state)
    return state


def _set_crypto_passthrough(content: str) -> None:
    """Store crypto output in run-scoped context for passthrough."""
    _get_crypto_passthrough_state().content = content
    logger.info("Crypto passthrough set (len=%d)", len(content))
    _inner_tool_outputs.append(
        {
            "specialist": "Crypto Agent",
            "tool_name": "crypto_passthrough",
            "output": content,
        }
    )


def _get_crypto_passthrough() -> str | None:
    """Return run-scoped crypto passthrough output."""
    state = _crypto_passthrough.get()
    return state.content if state is not None else None


def _clear_crypto_passthrough() -> None:
    """Reset run-scoped crypto passthrough output."""
    _crypto_passthrough.set(CryptoPassthroughState())


def _is_completed_strategy_output(output: str) -> bool:
    """Detect if strategy output is a completed response (not error/missing)."""
    return "#### 1. Verdict" in output or "## Verdict" in output


def _extract_strategy_summary(output: str) -> str:
    """Extract compact follow-up context from a completed strategy output.

    Pulls the Verdict line and Handoff Metadata section so the hub
    has enough session context for follow-up questions without the
    full 2000-word output.
    """
    lines: list[str] = []

    # Extract verdict line (first non-empty line after "#### 1. Verdict")
    verdict_marker = "#### 1. Verdict"
    idx = output.find(verdict_marker)
    if idx != -1:
        after = output[idx + len(verdict_marker) :]
        for line in after.split("\n"):
            stripped = line.strip().lstrip("- ")
            if stripped:
                lines.append(f"Verdict: {stripped}")
                break

    # Extract Handoff Metadata section
    metadata_marker = "#### 9. Handoff Metadata"
    idx = output.find(metadata_marker)
    if idx != -1:
        after = output[idx + len(metadata_marker) :]
        for line in after.split("\n"):
            stripped = line.strip()
            if stripped.startswith("- "):
                lines.append(stripped)
            elif stripped.startswith("####") or stripped.startswith("##"):
                break

    return "\n".join(lines) if lines else ""


def _is_pending_strategy_output(output: str) -> bool:
    """Detect if strategy output is an async pending response."""
    return "Job ID" in output and "Estimated Time" in output


def _strategy_relay_kind(output: str) -> str:
    """Label relayed strategy output for the hub-visible marker.

    Purely descriptive. Every non-empty specialist response is relayed
    verbatim regardless of shape, so this only selects the marker suffix and
    never decides whether the relay happens.

    Args:
        output: Raw specialist response text.

    Returns:
        "completed" for the nine-section deliverable, "pending" for an async
        stub, otherwise "other".
    """
    if _is_completed_strategy_output(output):
        return "completed"
    if _is_pending_strategy_output(output):
        return "pending"
    return "other"


_STRATEGY_TOOL_DESCRIPTION = (
    "Design, backtest, and refine trading strategies. "
    "Acts as a quantitative analyst that uses provided market "
    "context (fundamentals, technicals, sentiment) to design "
    "informed strategies, then backtests and iterates. "
    "Use for strategy building, backtesting, or trading "
    "system questions. "
    "MANDATORY PRE-CONDITION: before calling this tool, you MUST first "
    "call load_skill('obai-strategy-routing') in the same turn. The "
    "skill body holds the routing rules that govern this call. Calling "
    "this tool without loading that skill first is incorrect. "
    "Pass `user_request` as the user's wording verbatim, never a rewrite or "
    "summary of it. Pass `universe` as the resolved tradable tickers, and "
    "`context` as Hub-resolved facts only — never the user's entry, exit, or "
    "risk rules, which belong in `user_request`. "
    "Do not call with unresolved critical inputs. "
    "This tool may return a finished user-facing deliverable. "
    "If it does, your final answer must be exactly the tool output. "
    "Do not summarize it, reformat it, or add commentary."
)
_RESEARCH_TOOL_DESCRIPTION = (
    "Deep company and thematic research via web sources. "
    "Use for qualitative, structural, or long-horizon questions "
    "requiring synthesis across multiple non-news sources, "
    "including business model analysis, leadership quality, "
    "product sentiment, competitive dynamics, and industry "
    "structure. Not for breaking news, earnings data, SEC "
    "filings, insider activity, valuation metrics, or live "
    "market data. Resolve company_name first when only a ticker "
    "is provided."
)
_TERMINAL_STRATEGY_OUTPUT_PREFIX = "__TERMINAL_TOOL_OUTPUT__:strategy_analysis:"
_TERMINAL_PREDICTION_PREFIX = "__TERMINAL_TOOL_OUTPUT__:prediction_market_analysis:"
_TERMINAL_CRYPTO_PREFIX = "__TERMINAL_TOOL_OUTPUT__:crypto_analysis:"


def _wrap_terminal_strategy_output(output: str, kind: str) -> str:
    """Wrap terminal strategy output in a rigid marker for the hub."""
    return f"{_TERMINAL_STRATEGY_OUTPUT_PREFIX}{kind}\n\n{output}"


def _wrap_terminal_prediction_output(output: str) -> str:
    """Wrap terminal prediction-market output with rendering control line."""
    control = (
        f"{_TERMINAL_PREDICTION_PREFIX}"
        "render=verbatim_relay; "
        "no_renaming=true; "
        "no_compression=true; "
        "no_section_restructuring=true; "
        "preserve_for_followup=slug,market_url; "
        "routing_key_priority=slug,market_url; "
        "hide_by_default=condition_id,token_id; "
        "lookup_ids_internal_only=true; "
        "no_new_polymarket_identifiers=true"
    )
    return f"{control}\n\n{output}"


def _wrap_terminal_crypto_output(output: str) -> str:
    """Wrap terminal crypto output with rendering control line."""
    control = (
        f"{_TERMINAL_CRYPTO_PREFIX}"
        "render=verbatim_relay; "
        "no_compression=true; "
        "preserve_for_followup=job_id,fingerprint,product_id; "
        "no_provider_switch=true; "
        "coinbase_spot_v1_only=true"
    )
    return f"{control}\n\n{output}"


_PREDICTION_TOOL_DESCRIPTION = (
    "Polymarket prediction market analysis. "
    "Use for market discovery, understanding, and comparison; "
    "executable pricing with bid/ask/spread/depth; "
    "trade flow and holder analysis; "
    "trader leaderboard and wallet tracing; "
    "manual trade thesis generation; "
    "and setup-based backtesting over resolved prediction markets. "
    "Route here for Polymarket, prediction market odds, "
    "YES/NO market pricing, event resolution, top traders on "
    "Polymarket, and prediction-market trade ideas. "
    "Do NOT route prediction-market backtests to strategy_analysis. "
    "This tool may return a finished user-facing deliverable; when it does, "
    "relay it according to the obai-prediction-market-routing skill."
)

_CRYPTO_TOOL_DESCRIPTION = (
    "Coinbase spot crypto specialist. "
    "Use for Coinbase-tradable crypto product lookup, OHLCV candles, "
    "order book snapshots, latest trade, best bid/ask, Coinbase spot "
    "strategy backtests, trade-log and job-status follow-ups, strategy "
    "artifact validation, and internal Coinbase paper-ledger artifact export. "
    "V1 supports Coinbase Advanced Trade public market-data endpoints only. "
    "Do not use for equities, options, Polymarket, DeFi, on-chain analysis, "
    "perpetuals, funding, open interest, liquidations, or live order placement. "
    "MANDATORY PRE-CONDITION: before calling this tool, call "
    "load_skill('obai-crypto-routing') in the same turn. "
    "This tool is a terminal author; relay its output according to the "
    "obai-crypto-routing skill."
)

_STRATEGY_OBJECTIVE_PATTERNS = (
    # Technical strategy families
    re.compile(
        r"\b(momentum|mean[- ]reversion|breakout|trend(?:[- ]following)?|volatility|multi[- ]factor|pairs?)\b",
        re.IGNORECASE,
    ),
    # Fundamental factor strategy families
    re.compile(
        r"\b(value|quality|growth|income|dividend|factor|rotation|swing|covered[- ]call|wheel)\b",
        re.IGNORECASE,
    ),
    # Passive holding families (buy-and-hold is a complete objective on its own,
    # not dependent on an incidental "dividend"/"income" token appearing nearby)
    re.compile(r"\bbuy[- ]and[- ]hold\b|\bbuy[- ]?&[- ]?hold\b", re.IGNORECASE),
    # Indicator names (implies technical strategy)
    re.compile(
        r"\b(sma|ema|wma|dema|tema|rsi|macd|bbands|atr|adx|stoch|stochrsi|cci|willr|mom|roc|obv|mfi|aroon|sar)\b",
        re.IGNORECASE,
    ),
    # Explicit rule/entry/exit terms
    re.compile(
        r"\b(crossover|crosses[_ ]above|crosses[_ ]below|entry_rules|exit_rules|stop[_ ]loss|take[_ ]profit|trailing[_ ]stop|buy when|sell when)\b",
        re.IGNORECASE,
    ),
    # Hub handoff format explicitly states the objective
    re.compile(r"User objective:", re.IGNORECASE),
)
# A stored job id is as concrete a strategy target as a resolved universe: the
# specialist reloads the run from the id. Matching the literal token is a hard
# syntactic fact, which is all a hub pre-flight gate may test. The prose forms
# this replaced ("check ... status", "is it done") classified fuzzy follow-up
# intent in the hub, which belongs to the skill and the specialist.
_STRATEGY_JOB_REFERENCE_PATTERN = re.compile(r"\bbt_[a-z0-9]{6,}\b", re.IGNORECASE)


def _has_strategy_objective(input_text: str) -> bool:
    """Return True when strategy intent/rules are concrete enough to backtest."""
    return any(pattern.search(input_text) for pattern in _STRATEGY_OBJECTIVE_PATTERNS)


def _references_strategy_job(input_text: str) -> bool:
    """Report whether the request names a stored backtest job.

    Args:
        input_text: The user request carried by the hand-off.

    Returns:
        True when a ``bt_<hash>`` job token is present.

    """
    return bool(_STRATEGY_JOB_REFERENCE_PATTERN.search(input_text))


_STRATEGY_QUERY_KEYWORDS = re.compile(
    r"\b(backtest|backtesting|trading\s+system|trading\s+strategy|strategy\s+design"
    r"|build\s+(?:a\s+)?strategy|design\s+(?:a\s+)?strategy|optimize\s+(?:a\s+)?strategy"
    r"|run\s+(?:a\s+)?backtest)\b",
    re.IGNORECASE,
)


_PREDICTION_MARKET_KEYWORDS = re.compile(
    r"\b(polymarket|prediction\s+market|prediction[-\s]market|event\s+odds"
    r"|YES/NO|yes.no\s+market)\b",
    re.IGNORECASE,
)

# Executable-handoff intent. "paper" is intentionally excluded: it is a context
# word ("before a paper trade", "paper ledger") that does not by itself signal an
# export/handoff. Genuine handoffs always carry export/artifact/handoff/validate.
_CRYPTO_BACKTEST_INTENT = re.compile(
    r"\b(backtest|backtesting|strategy|artifact|handoff|trade log|export|validate)\b",
    re.IGNORECASE,
)
_CRYPTO_RESEARCH_ONLY_INTENT = re.compile(
    r"\b(what moved|why did|compare|explain|snapshot|quote|order book|orderbook"
    r"|latest trade|bid|ask|price|ohlcv|candles?)\b",
    re.IGNORECASE,
)
_CRYPTO_PRODUCT_PATTERN = re.compile(r"\b[A-Z0-9]{2,15}-(?:USD|USDC|EUR|BTC|ETH)\b")
# A stored job id is as concrete a target as a product id: the specialist
# resolves the product from the job. Matching the literal token is a hard
# syntactic fact, not the fuzzy follow-up classification the hub must avoid.
_CRYPTO_JOB_REFERENCE_PATTERN = re.compile(r"\bcrypto_bt_[0-9a-f]{6,}\b", re.IGNORECASE)
# A stored artifact id is as concrete a target as a job id for the same reason:
# the crypto engine derives it from the product id, so the specialist resolves
# the product from the id alone. The literal `_coinbase_` segment and the
# trailing `_v<digits>` make this a hard syntactic match on a generated
# identifier, not fuzzy intent classification.
_CRYPTO_ARTIFACT_REFERENCE_PATTERN = re.compile(
    r"\b[a-z0-9]+_[a-z0-9]+_coinbase_[a-z0-9]+(?:_[a-z0-9]+)*_v\d+\b",
    re.IGNORECASE,
)
_CRYPTO_ASSET_PATTERN = re.compile(
    r"\b(BTC|ETH|SOL|XRP|DOGE|ADA|AVAX|LINK|LTC|BCH|UNI|AAVE|MATIC|DOT"
    r"|bitcoin|ethereum|solana|ripple|dogecoin|cardano|avalanche|chainlink"
    r"|litecoin|uniswap|polygon|polkadot)\b",
    re.IGNORECASE,
)
_CRYPTO_UNSUPPORTED_VENUE_PATTERN = re.compile(
    r"\b(alpaca|binance|kraken|coingecko|coinalyze|kaiko|tardis|glassnode"
    r"|amberdata|coinbase exchange)\b",
    re.IGNORECASE,
)
# Bare "future" is excluded for the same reason as "paper" above: it is a date
# word ("the future requested end", "future candle") far more often than a
# contract type. The plural names the instrument; "perp"/"perpetual" already
# cover the singular derivative.
_CRYPTO_UNSUPPORTED_INSTRUMENT_PATTERN = re.compile(
    r"\b(perp|perpetual|futures|option|options|funding|open interest"
    r"|liquidation|liquidations|basis|defi|yield|stablecoin|on[- ]?chain)\b",
    re.IGNORECASE,
)


def _is_strategy_query(query: str) -> bool:
    """Detect if the user query is an equity strategy/backtest request.

    Used to inject a routing hint so the hub doesn't answer
    strategy questions from training data. Does NOT match
    prediction-market backtest requests.
    """
    if not _STRATEGY_QUERY_KEYWORDS.search(query):
        return False
    # Prediction-market backtests should route to prediction_market_analysis
    return not _PREDICTION_MARKET_KEYWORDS.search(query)


def _build_strategy_routing_hint() -> str:
    """Build the strategy routing reminder prepended to strategy-like queries."""
    return (
        "[ROUTING REMINDER: This query appears to involve equity strategy design, "
        "backtesting, or trading systems. Follow Strategy Routing exactly: if the "
        "user did not provide concrete tradable tickers, resolve the universe first "
        "with screener_lookup; otherwise call strategy_analysis. When calling "
        "strategy_analysis, pass `user_request` as the user's original wording "
        "verbatim, `universe` as the resolved tickers, and `context` as resolved "
        "facts only. Do not rewrite signal conditions, risk rules, thresholds, or "
        "order semantics. Do not answer from training data.]\n\n"
    )


def _distinct_matches(pattern: re.Pattern[str], text: str) -> list[str]:
    """Return each distinct match of ``pattern`` in first-seen order.

    Args:
        pattern: Compiled scope pattern.
        text: Handoff text to scan.

    Returns:
        Distinct matched substrings, case-folded for de-duplication but
        returned in their original casing.
    """
    seen: dict[str, str] = {}
    for match in pattern.finditer(text):
        seen.setdefault(match.group(0).casefold(), match.group(0))
    return list(seen.values())


def _get_crypto_preflight_error(input_text: str) -> str | None:
    """Fail closed on hard scope violations in executable crypto handoffs.

    Blocks only deterministic facts: unsupported venue, unsupported
    instrument, wrong data-source policy, or a backtest with no product
    symbol at all. Fuzzy intent (export eligibility, job follow-ups) is
    the crypto specialist's contract, not the hub's.
    """
    is_executable_intent = bool(_CRYPTO_BACKTEST_INTENT.search(input_text))
    if not is_executable_intent:
        return None

    if _CRYPTO_RESEARCH_ONLY_INTENT.search(input_text) and not re.search(
        r"\b(backtest|strategy|artifact|handoff|export|validate)\b",
        input_text,
        re.IGNORECASE,
    ):
        return None

    # Report every violation, not the first. Stopping at one left the Hub
    # unable to name the rest of an out-of-scope request in its refusal.
    venues = _distinct_matches(_CRYPTO_UNSUPPORTED_VENUE_PATTERN, input_text)
    instruments = _distinct_matches(_CRYPTO_UNSUPPORTED_INSTRUMENT_PATTERN, input_text)
    if venues or instruments:
        detail = ""
        if venues:
            detail += f" Unsupported venue/provider in handoff: {', '.join(venues)}."
        if instruments:
            detail += f" Unsupported instrument/data type in handoff: {', '.join(instruments)}."
        return (
            "MISSING_CRYPTO_INPUTS: crypto_analysis v1 supports Coinbase Advanced "
            f"Trade spot only.{detail} Retry with Coinbase spot scope or route to "
            "the correct specialist."
        )

    if "data_source_policy" in input_text and "execution_venue_required" not in input_text:
        return (
            "MISSING_CRYPTO_INPUTS: executable crypto backtests require "
            "`data_source_policy=execution_venue_required` using Coinbase market data."
        )

    # Export/validation/trade-log eligibility is the specialist's contract: it
    # resolves job state through crypto tools and cannot fabricate a job_id.
    # Pre-classifying that intent here false-positives on requests that merely
    # mention artifacts (e.g. "would an artifact be eligible" on a new backtest).

    has_resolvable_target = (
        _CRYPTO_PRODUCT_PATTERN.search(input_text)
        or _CRYPTO_ASSET_PATTERN.search(input_text)
        or _CRYPTO_JOB_REFERENCE_PATTERN.search(input_text)
        or _CRYPTO_ARTIFACT_REFERENCE_PATTERN.search(input_text)
    )
    if not has_resolvable_target:
        return (
            "MISSING_CRYPTO_INPUTS: Coinbase spot backtests require a concrete product "
            "or asset symbol. Retry with a Coinbase product_id such as BTC-USD or an "
            "asset ticker/name the Crypto Agent can resolve."
        )

    return None


def _get_missing_strategy_inputs(
    user_request: str,
    universe: list[str],
    context: str,
) -> list[str]:
    """Return missing critical strategy inputs for the hub-to-strategy call.

    Args:
        user_request: The user's wording, preserved verbatim by the hub.
        universe: Resolved tradable tickers, empty when none was resolved.
        context: Hub-resolved facts accompanying the request.

    Returns:
        Human-readable names of the missing inputs, empty when the call may
        proceed.

    """
    if _references_strategy_job(user_request):
        return []

    missing: list[str] = []
    if not [ticker for ticker in universe if ticker.strip()]:
        missing.append("concrete universe tickers")
    if not _has_strategy_objective(f"{user_request}\n{context}"):
        missing.append("strategy objective or rule set")
    return missing


def _format_strategy_input_error(missing_inputs: list[str]) -> str:
    """Create a deterministic missing-input response for the hub."""
    missing_text = ", ".join(missing_inputs)
    return (
        "MISSING_STRATEGY_INPUTS: strategy_analysis requires concrete tradable tickers "
        "and a clear strategy objective or rule set before backtesting. "
        f"Missing: {missing_text}. "
        "Pass the resolved tickers in the `universe` argument, resolving them with "
        "screener_lookup or one concise clarification first when the user named none."
    )


def _render_strategy_handoff(user_request: str, universe: list[str], context: str) -> str:
    """Render the canonical two-block hand-off the Strategy Agent reads.

    The Hub supplies these blocks as typed arguments, so the structure is
    produced here rather than asked for in prose. That removes the whole class
    of malformed hand-off the Strategy Agent used to reject.

    Args:
        user_request: The user's wording, preserved verbatim.
        universe: Resolved tradable tickers.
        context: Hub-resolved facts, already formatted as bullet lines.

    Returns:
        The rendered hand-off text.

    """
    tickers = ", ".join(ticker.strip() for ticker in universe if ticker.strip())
    blocks = [f"User request:\n{user_request.strip()}", "Strategy context:"]
    if tickers:
        blocks.append(f"- Universe: [{tickers}]")
    if context.strip():
        blocks.append(context.strip())
    return "\n".join(blocks)


# Metadata appended around a request — correlation tags, tooling notes — is not
# part of what the user asked for, so requiring the Hub to echo it back proves
# nothing about signal fidelity and fails an otherwise faithful hand-off.
_TRAILING_ANNOTATION_RE = re.compile(r"\s*\[[^\[\]]*\]\s*\Z")


def _normalize_strategy_handoff_text(text: str) -> str:
    """Normalize text for faithful-handoff substring checks."""
    return " ".join(_TRAILING_ANNOTATION_RE.sub("", text).casefold().split())


def _get_strategy_handoff_fidelity_error(input_text: str, original_query: str | None) -> str | None:
    """Return an error when strategy handoff does not preserve the user request.

    The Hub may add context alongside, but the original user request must
    remain present verbatim enough that signal semantics are not rewritten
    before the Strategy Agent sees them.

    Args:
        input_text: The ``user_request`` argument the Hub supplied.
        original_query: The query the user actually submitted.

    Returns:
        The error text to return to the Hub, or None when the request was
        preserved faithfully.

    """
    if not original_query:
        return None

    normalized_query = _normalize_strategy_handoff_text(original_query)
    normalized_input = _normalize_strategy_handoff_text(input_text)
    threshold_below = (
        "drops below" in normalized_query
        or "falls below" in normalized_query
        or "is below" in normalized_query
        or "below" in normalized_query
    )
    original_cross_below = (
        "crosses below" in normalized_query
        or "cross below" in normalized_query
        or "crosses_below" in normalized_query
    )
    input_cross_below = (
        "crosses below" in normalized_input
        or "cross below" in normalized_input
        or "crosses_below" in normalized_input
    )
    if threshold_below and input_cross_below and not original_cross_below:
        return (
            "STRATEGY_HANDOFF_FIDELITY_ERROR: strategy_analysis received a "
            "handoff that appears to rewrite a threshold condition into a "
            "crossover condition. Retry with the user's original wording in "
            "`user_request` and keep any derived implementation details in "
            "`context`."
        )

    if not normalized_query or normalized_query in normalized_input:
        return None

    return (
        "STRATEGY_HANDOFF_FIDELITY_ERROR: strategy_analysis requires the "
        "original user request to be preserved. Retry with `user_request` set "
        "to the user's original wording, then put any resolved context in "
        "`context`. Do not rewrite threshold conditions into crossover "
        "conditions or add operator semantics the user did not explicitly "
        "specify."
    )


# A citation URL is a promise the reader can open and verify the claim. The
# research specialist has been observed re-slugifying an article title into a
# link that appeared in no retrieved result, so the answer's URLs are checked
# against the ones its own tools actually returned before the Hub sees them.
_CITATION_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"'`]+")
_UNVERIFIED_URL_MARKER = "SOURCE-UNVERIFIED"


def _normalize_citation_url(url: str) -> str:
    """Normalize a URL so trailing prose punctuation cannot mask a match.

    Args:
        url: A URL as it appeared in text.

    Returns:
        The comparable form of the URL.

    """
    return url.rstrip(".,;:!?").rstrip("/").casefold()


def _collect_retrieved_urls(outputs: list[dict[str, Any]]) -> set[str]:
    """Collect every URL the captured tool outputs actually returned.

    Args:
        outputs: Captured inner tool outputs for one specialist call.

    Returns:
        The normalized URLs available to cite.

    """
    urls: set[str] = set()
    for entry in outputs:
        payload = entry.get("output")
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        urls.update(_normalize_citation_url(url) for url in _CITATION_URL_RE.findall(text))
    return urls


def _redact_unretrieved_urls(answer: str, retrieved: set[str]) -> tuple[str, list[str]]:
    """Replace citation URLs that no retrieved result contained.

    Args:
        answer: The specialist's answer text.
        retrieved: Normalized URLs the specialist's tools returned.

    Returns:
        The answer with unverifiable URLs replaced, and those URLs in
        first-seen order.

    """
    dropped: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        url = match.group(0)
        if _normalize_citation_url(url) in retrieved:
            return url
        if url not in dropped:
            dropped.append(url)
        return _UNVERIFIED_URL_MARKER

    redacted = _CITATION_URL_RE.sub(_replace, answer)
    if not dropped:
        return answer, []
    disclosure = (
        f"\n\n_{len(dropped)} cited link(s) did not appear in any retrieved result and were "
        f"replaced with `{_UNVERIFIED_URL_MARKER}`. Treat those claims as unverified._"
    )
    return redacted + disclosure, dropped


# Track active specialists and their timing
_active_agents: set[str] = set()
_specialist_start_times: dict[str, float] = {}
_tool_start_times: dict[str, float] = {}  # tool_call_id -> start_time
_tool_names: dict[str, str] = {}  # tool_call_id -> tool_name
_inner_tool_outputs: list[dict[str, Any]] = []  # raw MCP outputs for scoring

# Callback for MCP tool events (set by CLI/client)
_mcp_tool_callback: Any = None


def set_mcp_tool_callback(callback: Any) -> None:
    """Set a callback to receive MCP tool call events.

    The callback will be called with:
        - event_type: "start" or "complete"
        - specialist_name: e.g., "Market Data Agent"
        - tool_name: e.g., "get_quote"
        - args: argument string
        - duration_ms: (only for "complete") execution time

    Args:
        callback: Async or sync function to handle events.
    """
    global _mcp_tool_callback
    _mcp_tool_callback = callback


def _create_stream_handler(tool_name: str, display_name: str) -> Any:
    """Create a stream handler for a specialist agent tool with timing.

    This handler logs when the specialist starts working and when it
    makes tool calls (MCP requests). Now includes execution timing for
    better performance visibility.

    Args:
        tool_name: The tool name (e.g., "market_data_analysis")
        display_name: Human-friendly name (e.g., "Market Data Agent")

    Returns:
        Async callback function for on_stream parameter.
    """

    async def handle_stream(event: AgentToolStreamEvent) -> None:
        """Handle streaming events from specialist agent with timing."""
        agent_name = event["agent"].name
        stream_event = event["event"]

        # Track specialist start time and log when first seen
        if agent_name not in _active_agents:
            _active_agents.add(agent_name)
            _specialist_start_times[agent_name] = time.perf_counter()
            logger.info("🔍 %s working...", display_name)

        # Track MCP tool calls with timing
        if isinstance(stream_event, RunItemStreamEvent):
            item = stream_event.item
            item_type = getattr(item, "type", None)

            # Tool call start
            if item_type == "tool_call_item":
                raw_item = getattr(item, "raw_item", None)
                if raw_item:
                    mcp_tool = (
                        raw_item.get("name", "unknown")
                        if isinstance(raw_item, dict)
                        else getattr(raw_item, "name", "unknown")
                    )
                    call_id = (
                        raw_item.get("call_id")
                        if isinstance(raw_item, dict)
                        else getattr(raw_item, "call_id", None)
                    )
                    if call_id:
                        _tool_start_times[call_id] = time.perf_counter()
                        _tool_names[call_id] = mcp_tool
                    # Parse args for display (if JSON)
                    raw_args = (
                        raw_item.get("arguments", "{}")
                        if isinstance(raw_item, dict)
                        else getattr(raw_item, "arguments", "{}")
                    )
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                        args_str = ", ".join(f"{k}={v}" for k, v in list(args.items())[:3])
                        if len(args) > 3:
                            args_str += ", ..."
                    except (json.JSONDecodeError, TypeError):
                        args_str = ""
                    display_args = f"({args_str})" if args_str else ""
                    logger.debug(f"  └─ {display_name} calling: {mcp_tool}{display_args}")

                    # Notify callback if set
                    if _mcp_tool_callback:
                        _mcp_tool_callback("start", display_name, mcp_tool, args_str, call_id)

            # Tool call output (completion)
            elif item_type == "tool_call_output_item":
                raw_item = getattr(item, "raw_item", None)
                if raw_item:
                    # raw_item is a TypedDict (dict), not Pydantic,
                    # so use dict .get() instead of getattr.
                    call_id = (
                        raw_item.get("call_id")
                        if isinstance(raw_item, dict)
                        else getattr(raw_item, "call_id", None)
                    )
                    if call_id and call_id in _tool_start_times:
                        duration_ms = int((time.perf_counter() - _tool_start_times[call_id]) * 1000)
                        mcp_tool = _tool_names.get(call_id, "unknown")
                        logger.debug(f"    ✅ {mcp_tool} completed ({duration_ms}ms)")

                        # Notify callback if set
                        if _mcp_tool_callback:
                            _mcp_tool_callback(
                                "complete", display_name, mcp_tool, "", call_id, duration_ms
                            )

                        # Capture raw MCP output for faithfulness scoring
                        raw_output = getattr(item, "output", None)
                        if raw_output is None and isinstance(raw_item, dict):
                            raw_output = raw_item.get("output")
                        if raw_output is not None:
                            _inner_tool_outputs.append(
                                {
                                    "specialist": display_name,
                                    "tool_name": mcp_tool,
                                    "output": raw_output,
                                }
                            )
                            logger.info(
                                "Captured inner output: %s/%s (total=%d)",
                                display_name,
                                mcp_tool,
                                len(_inner_tool_outputs),
                            )

                        # Clean up
                        del _tool_start_times[call_id]
                        if call_id in _tool_names:
                            del _tool_names[call_id]

    return handle_stream


def _clear_active_agents() -> None:
    """Clear active agents tracking (call between queries)."""
    _active_agents.clear()
    _specialist_start_times.clear()
    _tool_start_times.clear()
    _tool_names.clear()
    _inner_tool_outputs.clear()


def get_inner_tool_outputs() -> list[dict[str, Any]]:
    """Get raw MCP tool outputs captured during the last query.

    Returns:
        List of dicts with specialist, tool_name, and output keys.
    """
    return list(_inner_tool_outputs)


def clear_agent_activity_tracking() -> None:
    """Clear specialist agent activity tracking.

    Call this between user queries to reset the "agent working" logs.
    Without this, agents won't log "working..." on subsequent queries.
    """
    _clear_active_agents()


def _hub_context_management(
    *,
    model: str,
    compact_ratio: float | None,
) -> list[ContextManagement] | None:
    """Derive the hub's server-side compaction entry from its model window.

    Args:
        model: Hub model name.
        compact_ratio: Fraction of the model's context window at which to
            compact, or None to disable compaction.

    Returns:
        A single-entry ``context_management`` list, or None to leave the
        field unset.
    """
    if compact_ratio is None:
        return None

    model_info = CompactionModelInfo.maybe_for_model(model)
    if model_info is None:
        # No window means no defensible threshold. Skipping compaction only
        # costs us the optimisation; guessing a token count could compact a
        # 1M-token window at 20% and shred context every turn.
        logger.warning(
            "No known context window for hub model %s - server-side compaction disabled",
            model,
        )
        return None

    threshold = int(model_info.context_window * compact_ratio)
    logger.info(
        "Hub compaction at %d tokens (%.0f%% of %s's %d-token window)",
        threshold,
        compact_ratio * 100,
        model,
        model_info.context_window,
    )
    return [ContextManagement(type="compaction", compact_threshold=threshold)]


def _build_hub_agent(
    *,
    instructions: str,
    model: str,
    specialist_tools: list[Tool],
    guardrails: list[InputGuardrail[Any]],
    reasoning_effort: ReasoningEffort,
    verbosity: Verbosity,
    compact_ratio: float | None,
) -> Agent[None]:
    """Build the SandboxAgent Central Hub with lazy hub_skills.

    The Sandbox Hub keeps the same agents-as-tools wiring but exposes the
    files in ``HUB_SKILLS_DIR`` as lazy skills. Only skill metadata (name +
    description) reaches the model: ``load_skill`` stages the directory and
    returns a status envelope, and with ``Skills`` as the sole capability the
    hub has no file-read tool, so **SKILL.md bodies never enter the model's
    context**. Treat them as documentation and as the routing signal the
    regression gate asserts on -- never as an instruction channel. Any rule
    the hub must actually obey belongs in ``central_hub_base.md``, which is
    rendered into the instructions on every turn.

    Capabilities: ``Skills`` only. We deliberately do **not** include
    ``Capabilities.default()`` (Filesystem + Shell + Compaction):

    * Filesystem and Shell would expose model-side file/exec tools the
      hub does not need — it routes via specialist MCP tools.
    * The legacy ``Compaction`` capability injects ``context_management``
      via ``extra_args``, which collides with the first-class
      ``ModelSettings.context_management`` field that openai-agents
      0.16+ adds to every Responses API call (the duplicate-key guard in
      ``openai_responses.py`` raises ``TypeError``). Server-side
      compaction is configured through ``compact_ratio`` below, which
      sets ``ModelSettings.context_management`` directly.

    Args:
        instructions: Rendered hub prompt.
        model: Hub model name.
        specialist_tools: Specialist agents exposed as tools.
        guardrails: Input guardrails run before the hub sees a query.
        reasoning_effort: Hub reasoning effort tier.
        verbosity: Hub output verbosity tier.
        compact_ratio: Fraction of the model's context window at which to
            compact, or None to leave ``context_management`` unset.

    Returns:
        The configured hub ``SandboxAgent``.
    """
    skills_capability = Skills(
        lazy_from=LocalDirLazySkillSource(source=LocalDir(src=HUB_SKILLS_DIR)),
    )
    context_management = _hub_context_management(model=model, compact_ratio=compact_ratio)
    return SandboxAgent(
        name="central_hub",
        instructions=instructions,
        model=model,
        tools=specialist_tools,
        input_guardrails=guardrails,
        default_manifest=Manifest(
            # openai-agents 0.17+ restricts LocalDir.src to the SDK process
            # cwd unless explicitly granted. HUB_SKILLS_DIR is an absolute
            # path under the source tree, so grant it (read-only) to keep
            # skills loading independent of the launch directory.
            extra_path_grants=(SandboxPathGrant(path=str(HUB_SKILLS_DIR), read_only=True),),
        ),
        capabilities=[skills_capability],
        model_settings=ModelSettings(
            parallel_tool_calls=True,
            tool_choice="auto",
            # context="all_turns" keeps reasoning from earlier turns in the
            # rendered context. GPT-5.6 already defaults to this; pinning it
            # keeps the behaviour from moving under us on a model change.
            reasoning=Reasoning(effort=reasoning_effort, context="all_turns"),
            verbosity=verbosity,
            context_management=context_management,
        ),
    )


def _apply_hub_agent_settings(
    agent: Agent[None],
    *,
    model: str,
    reasoning_effort: ReasoningEffort,
    compact_ratio: float | None,
) -> None:
    """Retune a built hub agent's model and reasoning effort in place.

    The SDK resolves ``agent.model`` and ``agent.model_settings`` once per
    turn (``agents.run_internal.turn_preparation``), so mutating them takes
    effect on the next query. That is the whole point: a model change from
    the settings UI applies without tearing down the MCP connections, the
    loaded skills, or the open WebSockets.

    Only the two user-owned fields move. Instructions, tools, guardrails and
    verbosity are model-independent and are left exactly as built.

    Args:
        agent: The hub agent returned by :func:`_build_hub_agent`.
        model: Hub model name to switch to.
        reasoning_effort: Hub reasoning effort tier to switch to.
        compact_ratio: Fraction of the *new* model's context window at which
            to compact, or None to leave ``context_management`` unset.
    """
    agent.model = model
    settings = agent.model_settings
    # Rebuild rather than mutate: Reasoning is a pydantic model, and
    # context="all_turns" must survive the swap or the hub silently stops
    # carrying reasoning across turns.
    settings.reasoning = Reasoning(effort=reasoning_effort, context="all_turns")
    # The compaction threshold is a fraction of the model's window, so a
    # model change invalidates it.
    settings.context_management = _hub_context_management(model=model, compact_ratio=compact_ratio)


class CentralHubAgent:
    """Central hub agent that coordinates specialist agents.

    This agent calls specialists as tools (agents-as-tools pattern),
    receives their outputs, and synthesizes comprehensive responses.
    Runs on ``orchestrator_model``, the strongest tier we configure.

    The agents-as-tools pattern keeps the hub in control:
    - Hub calls market_data_tool() -> gets price data back
    - Hub calls fundamentals_tool() -> gets financials back
    - Hub synthesizes ALL outputs into final response

    Example:
        ```python
        async with CentralHubAgent() as hub:
            # Use with Agent SDK runner
            from agents import Runner
            result = await Runner.run(hub.agent, "Analyze AAPL")
        ```
    """

    def __init__(self) -> None:
        """Initialize Central Hub Agent.

        Loads configuration but does not initialize specialist agents yet.
        Call initialize() to set up all agents and tools.
        """
        self.config = get_config()
        self.agent: Agent[None] | None = None
        # Populated with a sandbox-aware RunConfig once the hub is initialized.
        self._run_config: RunConfig | None = None
        self._current_user_query: str | None = None

        # Specialist agents (initialized in initialize())
        self.fundamentals_agent: FundamentalsAgent | None = None
        self.market_data_agent: MarketDataAgent | None = None
        self.events_news_agent: EventsNewsAgent | None = None
        self.options_agent: OptionsAgent | None = None
        self.screener_agent: ScreenerAgent | None = None
        self.portfolio_agent: PortfolioAgent | None = None
        self.strategy_agent: StrategyAgent | None = None
        self.research_agent: ResearchAgent | None = None
        self.prediction_markets_agent: PredictionMarketsAgent | None = None
        self.crypto_agent: CryptoAgent | None = None

        # Track which agents were successfully initialized (for cleanup)
        self._initialized_agents: list[BaseAgent] = []
        # Optional specialists that failed startup. Exposed so clients can
        # tell the user "research / prediction-markets is unavailable"
        # instead of silently routing around it.
        self.degraded_capabilities: list[str] = []
        self._initialized = False

        # Semantic cache for session context (lazy init)
        self._cache: QueryCache | None = None
        cache_config = get_cache_config()
        if cache_config.is_configured():
            self._cache = QueryCache(cache_config)
            logger.info("Semantic cache enabled (LangCache)")

        logger.info("Central Hub Agent created (not initialized)")

    async def initialize(self) -> None:
        """Initialize central hub and all specialist agents.

        This async method must be called before using the hub.
        It initializes specialist agents and converts them to tools
        using the agents-as-tools pattern. Properly cleans up on partial failure.

        Raises:
            MCPClientError: If any specialist agent fails to initialize.
        """
        if self._initialized:
            logger.warning("Central Hub Agent already initialized")
            return

        logger.info("Initializing Central Hub Agent and all specialists")

        try:
            # Initialize all specialist agents in parallel for fast startup.
            # Each agent independently connects to its MCP server, loads tools,
            # and reads its prompt — no shared state between them.
            await self._init_specialists_parallel()

            # Central hub uses dedicated model (needs strong reasoning)
            model = self.config.orchestrator_model
            logger.info(f"Central Hub Agent using model: {model}")

            # Load instructions from prompt file with user preferences injected.
            # The compact base prompt carries the hub's invariant rules; lazy
            # skills carry the long conditional instructions.
            user_prefs = _prefs_store.load()
            instructions = load_prompt(
                "central_hub_base",
                USER_PREFERENCES=user_prefs.model_dump_json(indent=2),
            )

            # Create input guardrail to filter non-financial queries (if enabled)
            guardrails = []
            if self.config.enable_guardrails:
                financial_guardrail = create_input_guardrail()
                guardrails.append(financial_guardrail)
                logger.info("Input guardrail enabled for financial query validation")
            else:
                logger.warning("Input guardrails DISABLED - all queries will be processed")

            # Build tools list from initialized specialist agents
            # Using agents-as-tools pattern: orchestrator stays in control,
            # calls specialists as tools, receives their output, synthesizes

            specialist_tools: list[Tool] = []

            if self.market_data_agent and self.market_data_agent.agent:
                specialist_tools.append(
                    self.market_data_agent.agent.as_tool(
                        tool_name="market_data_analysis",
                        tool_description=(
                            "Get real-time stock prices, quotes, historical candles, "
                            "technical indicators (RSI, moving averages, ADX), volume analysis, "
                            "market movers (gainers/losers), and market status. "
                            "Use for any price or technical analysis questions."
                        ),
                        on_stream=_create_stream_handler(
                            "market_data_analysis", "Market Data Agent"
                        ),
                    )
                )

            if self.fundamentals_agent and self.fundamentals_agent.agent:
                specialist_tools.append(
                    self.fundamentals_agent.agent.as_tool(
                        tool_name="fundamentals_analysis",
                        tool_description=(
                            "Get company financial statements (income, balance sheet, cash flow), "
                            "valuation ratios (P/E, P/B, EV/EBITDA), analyst estimates, "
                            "price targets, company profiles, SEC filings (10-K, 10-Q, 8-K), "
                            "insider trading activity, and revenue segment breakdowns. "
                            "Use for fundamental analysis, valuation, regulatory filings, "
                            "insider sentiment, or business diversification questions."
                        ),
                        on_stream=_create_stream_handler(
                            "fundamentals_analysis", "Fundamentals Agent"
                        ),
                    )
                )

            if self.events_news_agent and self.events_news_agent.agent:
                specialist_tools.append(
                    self.events_news_agent.agent.as_tool(
                        tool_name="events_news_analysis",
                        tool_description=(
                            "Get AI-scored company news with impact ratings, "
                            "earnings history (dates, EPS estimates vs actual, revenue), "
                            "dividend schedules (ex-dates, payment dates, yield), "
                            "and market-moving catalysts. "
                            "Use for news, earnings, dividends, or event-related questions."
                        ),
                        on_stream=_create_stream_handler(
                            "events_news_analysis", "Events & News Agent"
                        ),
                    )
                )

            if self.options_agent and self.options_agent.agent:
                specialist_tools.append(
                    self.options_agent.agent.as_tool(
                        tool_name="options_analysis",
                        tool_description=(
                            "Get options chains, Greeks (delta, gamma, theta, vega), "
                            "implied volatility, strike prices, and expiration data. "
                            "Use for any options or derivatives questions."
                        ),
                        on_stream=_create_stream_handler("options_analysis", "Options Agent"),
                    )
                )

            if self.screener_agent and self.screener_agent.agent:
                specialist_tools.append(
                    self.screener_agent.agent.as_tool(
                        tool_name="screener_lookup",
                        tool_description=(
                            "Resolve company NAMES to ticker symbols and screen stocks "
                            "by criteria (market cap, sector, price). "
                            "Use when: (1) user mentions a company name instead of a ticker "
                            "(e.g., 'Palantir', 'Snowflake'), (2) stock screening requests, "
                            "or (3) a data specialist returned no results for a ticker that "
                            "may be misspelled. "
                            "Skip for well-known ticker symbols (AAPL, TSLA, MSFT, etc.) — "
                            "go directly to the relevant data specialist first."
                        ),
                        on_stream=_create_stream_handler("screener_lookup", "Screener Agent"),
                    )
                )

            if self.portfolio_agent and self.portfolio_agent.agent:
                specialist_tools.append(
                    self.portfolio_agent.agent.as_tool(
                        tool_name="portfolio_analysis",
                        tool_description=(
                            "Parse portfolio positions from text, "
                            "expand ETF holdings for look-through analysis, get Treasury rates. "
                            "Use for portfolio composition, ETF constituents, "
                            "or risk-free rate questions."
                        ),
                        on_stream=_create_stream_handler("portfolio_analysis", "Portfolio Agent"),
                    )
                )

            if self.strategy_agent and self.strategy_agent.agent:
                specialist_tools.append(self._build_strategy_tool())

            if self.research_agent and self.research_agent.agent:
                specialist_tools.append(self._build_research_tool())

            if self.prediction_markets_agent and self.prediction_markets_agent.agent:
                specialist_tools.append(self._build_prediction_tool())

            if self.crypto_agent and self.crypto_agent.agent:
                specialist_tools.append(self._build_crypto_tool())

            # Preference tools are local (no MCP routing needed)
            specialist_tools.append(get_preferences)
            specialist_tools.append(set_preferences)

            logger.info(f"Configured {len(specialist_tools)} specialist tools for central hub")

            # Create agent with tools (Agent SDK uses OPENAI_API_KEY env var).
            # Using agents-as-tools pattern: orchestrator stays in control.
            # The hub is a SandboxAgent with lazy hub skills.
            self.agent = _build_hub_agent(
                instructions=instructions,
                model=model,
                specialist_tools=specialist_tools,
                guardrails=guardrails,
                reasoning_effort=self.config.orchestrator_reasoning_effort,
                verbosity=self.config.orchestrator_verbosity,
                compact_ratio=self.config.orchestrator_compact_ratio,
            )
            self._run_config = RunConfig(
                sandbox=SandboxRunConfig(client=UnixLocalSandboxClient()),
                workflow_name="OBaI Central Hub",
            )
            logger.info(
                "Central Hub running as SandboxAgent (lazy skills from %s)",
                HUB_SKILLS_DIR,
            )

            self._initialized = True
            logger.info("Central Hub Agent initialized successfully")

        except Exception:
            # Clean up any agents that were initialized before the failure
            logger.exception("Central Hub initialization failed, cleaning up")
            await self._cleanup_agents()
            raise

    def apply_hub_settings(self, *, model: str, reasoning_effort: ReasoningEffort) -> None:
        """Retune the running hub to a new model and reasoning effort.

        Applies to the next query. Specialists are code-owned and are not
        touched. ``self.config`` is updated alongside the agent so every
        surface that reports the running values (``/api/status``,
        ``/api/settings``) stays honest instead of describing the agent the
        process started with.

        Callers must serialize this against in-flight queries — the web
        client does so through ``HubBridge.apply_hub_settings``.

        Args:
            model: Hub model name to switch to.
            reasoning_effort: Hub reasoning effort tier to switch to.

        Raises:
            RuntimeError: The hub has not been initialized yet.
        """
        if self.agent is None:
            msg = "Cannot apply hub settings before initialize()"
            raise RuntimeError(msg)

        _apply_hub_agent_settings(
            self.agent,
            model=model,
            reasoning_effort=reasoning_effort,
            compact_ratio=self.config.orchestrator_compact_ratio,
        )
        self.config.orchestrator_model = model
        self.config.orchestrator_reasoning_effort = reasoning_effort
        logger.info("Hub retuned live to model=%s effort=%s", model, reasoning_effort)

    async def _init_specialists_parallel(self) -> None:
        """Initialize all specialist agents in parallel.

        Each agent connects to its own MCP server on a separate port,
        loads its tools via list_tools(), and reads its prompt file.
        No shared state — safe to run concurrently.

        All required agents must succeed. Research, prediction markets, and
        crypto are optional and degrade gracefully if unavailable.

        Raises:
            MCPClientError: If any required agent fails to initialize.
        """
        # Construct instances (instant, no I/O)
        self.fundamentals_agent = FundamentalsAgent()
        self.market_data_agent = MarketDataAgent()
        self.events_news_agent = EventsNewsAgent()
        self.options_agent = OptionsAgent()
        self.screener_agent = ScreenerAgent()
        self.portfolio_agent = PortfolioAgent()
        self.strategy_agent = StrategyAgent()
        self.research_agent = ResearchAgent()
        self.prediction_markets_agent = PredictionMarketsAgent()
        self.crypto_agent = CryptoAgent()

        required = [
            self.fundamentals_agent,
            self.market_data_agent,
            self.events_news_agent,
            self.options_agent,
            self.screener_agent,
            self.portfolio_agent,
            self.strategy_agent,
        ]

        optional = [self.research_agent, self.prediction_markets_agent, self.crypto_agent]
        all_agents = [*required, *optional]
        results = await asyncio.gather(
            *[a.initialize() for a in all_agents],
            return_exceptions=True,
        )

        # Re-raise BaseException subtypes (KeyboardInterrupt, CancelledError)
        # that asyncio.gather captures but should not be silently swallowed.
        first_error: Exception | None = None
        for agent, result in zip(all_agents, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result
            if isinstance(result, Exception):
                if agent is self.research_agent:
                    logger.warning(
                        "Research Agent unavailable — research_analysis tool disabled. "
                        "Other agents unaffected.",
                    )
                    self.research_agent = None
                    self.degraded_capabilities.append("research")
                elif agent is self.prediction_markets_agent:
                    logger.warning(
                        "Prediction Markets Agent unavailable — "
                        "prediction_market_analysis tool disabled. "
                        "Other agents unaffected.",
                    )
                    self.prediction_markets_agent = None
                    self.degraded_capabilities.append("prediction_markets")
                elif agent is self.crypto_agent:
                    logger.warning(
                        "Crypto Agent unavailable — crypto_analysis tool disabled. "
                        "Other agents unaffected.",
                    )
                    self.crypto_agent = None
                    self.degraded_capabilities.append("crypto")
                else:
                    logger.error("Failed to initialize %s: %s", agent.agent_name, result)
                    first_error = first_error or result
            else:
                self._initialized_agents.append(agent)
                logger.info("✓ %s initialized", agent.agent_name)

        if first_error is not None:
            raise first_error

    async def _cleanup_agents(self) -> None:
        """Clean up all initialized specialist agents."""
        for agent in reversed(self._initialized_agents):
            try:
                await agent.close()
            except Exception:
                logger.exception(f"Error closing {agent.agent_name}")

        self._initialized_agents.clear()
        self.fundamentals_agent = None
        self.market_data_agent = None
        self.events_news_agent = None
        self.options_agent = None
        self.screener_agent = None
        self.portfolio_agent = None
        self.strategy_agent = None
        self.research_agent = None
        self.prediction_markets_agent = None
        self.crypto_agent = None
        self.agent = None
        self._run_config = None
        self._initialized = False

    async def close(self) -> None:
        """Close all specialist agents and release resources."""
        logger.info("Closing Central Hub Agent and all specialists")
        await self._cleanup_agents()
        logger.info("Central Hub Agent closed")

    async def clear_cache(self) -> bool:
        """Clear semantic cache and tool call cache.

        Call this when resetting conversation context (e.g., "clear" command).

        Returns:
            True if caches were cleared successfully.
        """
        # Clear tool call deduplication cache
        clear_tool_cache()

        # Clear semantic cache (LangCache)
        if self._cache:
            return await self._cache.clear()
        return True

    def _build_prediction_tool(self) -> Tool:
        """Build prediction-market tool wrapper with identifier guardrails."""
        if self.prediction_markets_agent is None or self.prediction_markets_agent.agent is None:
            msg = "Prediction Markets Agent not initialized"
            raise ValueError(msg)

        pred_agent = self.prediction_markets_agent.agent
        stream_handler = _create_stream_handler(
            "prediction_market_analysis", "Prediction Markets Agent"
        )

        @function_tool(
            name_override="prediction_market_analysis",
            description_override=_PREDICTION_TOOL_DESCRIPTION,
            strict_mode=True,
        )
        async def prediction_market_analysis(ctx: RunContextWrapper[Any], input: str) -> str:
            result = Runner.run_streamed(
                starting_agent=pred_agent,
                input=input,
                context=ctx.context,
                max_turns=25,
            )

            async for event in result.stream_events():
                await stream_handler({"agent": pred_agent, "event": event})

            final_output = getattr(result, "final_output", None)
            output = ""
            if isinstance(final_output, str):
                output = final_output
            elif final_output is not None:
                output = str(final_output)

            if not output:
                return output
            _set_prediction_passthrough(output)
            return _wrap_terminal_prediction_output(output)

        return prediction_market_analysis

    def _build_crypto_tool(self) -> Tool:
        """Build crypto tool wrapper with terminal relay."""
        if self.crypto_agent is None or self.crypto_agent.agent is None:
            msg = "Crypto Agent not initialized"
            raise ValueError(msg)

        crypto_agent = self.crypto_agent.agent
        stream_handler = _create_stream_handler("crypto_analysis", "Crypto Agent")

        @function_tool(
            name_override="crypto_analysis",
            description_override=_CRYPTO_TOOL_DESCRIPTION,
            strict_mode=True,
        )
        async def crypto_analysis(ctx: RunContextWrapper[Any], input: str) -> str:
            preflight_error = _get_crypto_preflight_error(input)
            if preflight_error:
                # Return to the Hub rather than relaying, matching the strategy
                # handoff errors above: this is a control signal, and shipping it
                # verbatim made the raw token the user's entire answer.
                logger.info("Blocked crypto_analysis due to preflight violation")
                return preflight_error

            result = Runner.run_streamed(
                starting_agent=crypto_agent,
                input=input,
                context=ctx.context,
                max_turns=self.config.crypto_max_turns,
            )

            async for event in result.stream_events():
                await stream_handler({"agent": crypto_agent, "event": event})

            final_output = getattr(result, "final_output", None)
            output = ""
            if isinstance(final_output, str):
                output = final_output
            elif final_output is not None:
                output = str(final_output)

            if not output:
                return output
            _set_crypto_passthrough(output)
            return _wrap_terminal_crypto_output(output)

        return crypto_analysis

    def _build_research_tool(self) -> Tool:
        """Build the research tool wrapper that verifies cited links.

        The specialist is instructed never to fabricate sources, and does so
        anyway: a citation URL it never retrieved reads exactly like one it
        did. Checking the answer's links against the URLs its own tools
        returned is the only enforcement that holds.
        """
        if self.research_agent is None or self.research_agent.agent is None:
            msg = "Research Agent not initialized"
            raise ValueError(msg)

        research_agent = self.research_agent.agent
        stream_handler = _create_stream_handler("research_analysis", "Research Agent")

        @function_tool(
            name_override="research_analysis",
            description_override=_RESEARCH_TOOL_DESCRIPTION,
            strict_mode=True,
        )
        async def research_analysis(ctx: RunContextWrapper[Any], input: str) -> str:
            captured_before = len(get_inner_tool_outputs())

            result = Runner.run_streamed(
                starting_agent=research_agent,
                input=input,
                context=ctx.context,
            )
            async for event in result.stream_events():
                await stream_handler({"agent": research_agent, "event": event})

            final_output = getattr(result, "final_output", None)
            output = final_output if isinstance(final_output, str) else str(final_output or "")
            if not output:
                return output

            own_outputs = [
                entry
                for entry in get_inner_tool_outputs()[captured_before:]
                if entry.get("specialist") == "Research Agent"
            ]
            verified, dropped = _redact_unretrieved_urls(
                output, _collect_retrieved_urls(own_outputs)
            )
            if dropped:
                logger.warning(
                    "research_analysis cited %d URL(s) absent from its retrieved results: %s",
                    len(dropped),
                    ", ".join(dropped),
                )
            return verified

        return research_analysis

    def _build_strategy_tool(self) -> Tool:
        """Build guarded strategy tool wrapper for hub routing.

        The hub prompt should already avoid calling strategy analysis with
        unresolved critical inputs. This wrapper enforces that contract
        deterministically in code as a fail-closed safety net.
        """
        if self.strategy_agent is None or self.strategy_agent.agent is None:
            msg = "Strategy Agent not initialized"
            raise ValueError(msg)

        strategy_agent = self.strategy_agent.agent
        stream_handler = _create_stream_handler("strategy_analysis", "Strategy Agent")

        @function_tool(
            name_override="strategy_analysis",
            description_override=_STRATEGY_TOOL_DESCRIPTION,
            strict_mode=True,
        )
        async def strategy_analysis(
            ctx: RunContextWrapper[Any],
            user_request: str,
            universe: list[str],
            context: str,
        ) -> str:
            handoff_error = _get_strategy_handoff_fidelity_error(
                user_request,
                self._current_user_query,
            )
            if handoff_error:
                logger.info("Blocked strategy_analysis due to unfaithful handoff")
                return handoff_error

            missing_inputs = _get_missing_strategy_inputs(user_request, universe, context)
            if missing_inputs:
                logger.info(
                    "Blocked strategy_analysis due to missing critical inputs: %s",
                    ", ".join(missing_inputs),
                )
                return _format_strategy_input_error(missing_inputs)

            handoff = _render_strategy_handoff(user_request, universe, context)
            result = Runner.run_streamed(
                starting_agent=strategy_agent,
                input=handoff,
                context=ctx.context,
                max_turns=self.config.strategy_max_turns,
            )

            async for event in result.stream_events():
                await stream_handler({"agent": strategy_agent, "event": event})

            final_output = getattr(result, "final_output", None)
            output = ""
            if isinstance(final_output, str):
                output = final_output
            elif final_output is not None:
                output = str(final_output)

            # Relay every non-empty response, matching crypto_analysis and
            # prediction_market_analysis. The hub is told to emit nothing but
            # the relayed output, so gating relay on the specialist's section
            # headings silently discarded whole classes of answer (completed
            # job-status polls, diagnostics, missing-inputs, errors, refusals).
            # The kind only labels the marker; it never decides the relay.
            if not output:
                return output
            kind = _strategy_relay_kind(output)
            _set_strategy_passthrough(output, kind)
            return _wrap_terminal_strategy_output(output, kind)

        return strategy_analysis

    async def __aenter__(self) -> "CentralHubAgent":
        """Async context manager entry."""
        await self.initialize()
        return self

    async def __aexit__(self, *_args: Any) -> None:
        """Async context manager exit."""
        await self.close()

    def get_specialist(self, specialist_name: str) -> Agent[None]:
        """Get a specialist agent by name.

        Args:
            specialist_name: Name of specialist (fundamentals, market_data,
                events_news, options, screener).

        Returns:
            Specialist's OpenAI Agent SDK agent instance.

        Raises:
            ValueError: If specialist name is invalid or agent not initialized.
        """
        if not self._initialized:
            msg = "Central Hub not initialized. Call initialize() first."
            raise ValueError(msg)

        specialists: dict[str, BaseAgent | None] = {
            "fundamentals": self.fundamentals_agent,
            "market_data": self.market_data_agent,
            "events_news": self.events_news_agent,
            "options": self.options_agent,
            "screener": self.screener_agent,
            "portfolio": self.portfolio_agent,
            "strategy": self.strategy_agent,
            "research": self.research_agent,
            "prediction_markets": self.prediction_markets_agent,
            "crypto": self.crypto_agent,
        }

        if specialist_name not in specialists:
            msg = (
                f"Invalid specialist: {specialist_name}. Must be one of {list(specialists.keys())}"
            )
            raise ValueError(msg)

        wrapper = specialists[specialist_name]
        if wrapper is None or wrapper.agent is None:
            msg = f"Specialist {specialist_name} not initialized"
            raise ValueError(msg)

        return wrapper.agent

    async def run(
        self,
        query: str,
        session: Session | None = None,
    ) -> AsyncIterator[Any]:
        """Run a query through the central hub.

        This is the recommended way to use the hub. It handles:
        - Semantic cache search (RAG-style context injection)
        - Streaming execution via Runner.run_streamed
        - Token usage tracking
        - Response caching for follow-up questions

        Args:
            query: User query to process.
            session: Optional session for conversation memory.

        Yields:
            Agent SDK streaming events (AgentUpdatedStreamEvent,
            RunItemStreamEvent, RawResponsesStreamEvent, etc.)

        Raises:
            ValueError: If hub not initialized.
            InputGuardrailTripwireTriggered: If query fails guardrail check.

        Example:
            ```python
            async with CentralHubAgent() as hub:
                async for event in hub.run("What is AAPL trading at?", session):
                    # Handle event (print streaming text, show tool calls, etc.)
                    if isinstance(event, RawResponsesStreamEvent):
                        print(event.data.delta, end="")
            ```
        """
        if not self._initialized or self.agent is None:
            msg = "Central Hub not initialized. Call initialize() first."
            raise ValueError(msg)

        # Reset agent activity tracking and passthrough state for this query
        _clear_active_agents()
        _clear_strategy_passthrough()
        _clear_prediction_passthrough()
        _clear_crypto_passthrough()
        self._current_user_query = query

        # RAG-style cache: search for similar cached response
        query_to_run = query
        if self._cache:
            cached_response = await self._cache.search(query=query)
            if cached_response:
                # Inject cached context for hub to decide if sufficient
                query_to_run = self._cache.build_rag_context(
                    query=query,
                    cached_response=cached_response,
                )
                logger.info("Cache HIT - injecting RAG context")
                # Record cached data as ground truth for scoring
                _inner_tool_outputs.append(
                    {
                        "specialist": "cache",
                        "tool_name": "semantic_cache",
                        "output": cached_response,
                    }
                )

        # Routing hint: detect strategy/backtest intent and remind the hub
        # to route through the strategy specialist. Disabled while testing
        # whether the new architecture (base-prompt pre-flight rule +
        # mandatory load_skill + tightened tool description + skill body)
        # is sufficient on its own. Re-enable if strategy turns regress.
        # if _is_strategy_query(query):
        #     query_to_run = _build_strategy_routing_hint() + query_to_run
        #     logger.info("Strategy intent detected — injected routing hint")

        prediction_context_block = ""

        # Run streamed
        # Opik tracing handled by OpikTracingProcessor (set up in init_opik).
        # run_config carries a SandboxRunConfig with a UnixLocalSandboxClient.
        result = Runner.run_streamed(
            starting_agent=self.agent,
            input=query_to_run,
            session=session,
            run_config=self._run_config,
        )

        # Buffer response text for caching, minus commentary-phase narration:
        # caching a status line as the answer poisons every later cache hit.
        answer = AnswerAccumulator()
        passthrough: str | None = None

        # Buffered hub-mediated relay: after a terminal specialist fires,
        # buffer hub synthesis and emit the specialist output directly.
        terminal_fired: str | None = None
        buffered_events: list[Any] = []

        async for event in result.stream_events():
            # Buffer streaming text deltas for caching
            if isinstance(event, RawResponsesStreamEvent):
                data = event.data
                if isinstance(data, ResponseTextDeltaEvent) and data.delta:
                    answer.add_delta(data.item_id, data.delta)

            # Also capture final message output
            elif isinstance(event, RunItemStreamEvent):
                item = event.item
                if isinstance(item, MessageOutputItem):
                    answer.note_message(
                        item.raw_item.id,
                        ItemHelpers.text_message_output(item),
                        item.raw_item.phase,
                    )

            # Detect terminal specialist firing: passthrough set by tool wrapper.
            if terminal_fired is None and _prediction_passthrough is not None:
                terminal_fired = "prediction"
            if terminal_fired is None and _get_crypto_passthrough() is not None:
                terminal_fired = "crypto"
            if terminal_fired is None and _get_strategy_passthrough() is not None:
                terminal_fired = "strategy"

            # After a terminal specialist fires, buffer hub text synthesis.
            if terminal_fired is not None:
                if isinstance(event, RawResponsesStreamEvent):
                    buffered_events.append(event)
                    continue
                if isinstance(event, RunItemStreamEvent) and isinstance(
                    getattr(event, "item", None), MessageOutputItem
                ):
                    buffered_events.append(event)
                    continue

            yield event

        # Context persistence disabled — the SDK session already carries
        # conversation history for follow-ups, and tool-output-based context
        # was injecting stale market identifiers that biased followup queries.

        # Always-passthrough relay for prediction output.
        # Prediction-market output is non-deterministic in shape, so the hub LLM
        # consistently rewrites or compresses it regardless of how strongly the
        # skill or control line frames the verbatim-relay contract. Bypass hub
        # authoring entirely: emit the specialist output directly to the client
        # and discard the buffered hub-authored text. validate_prediction_relay
        # is still invoked for trace diagnostics; the boolean does not gate the
        # choice. (Crypto and strategy use the same deterministic passthrough.)
        if terminal_fired == "prediction" and _prediction_passthrough:
            relay_ok = validate_prediction_relay(
                answer.text(),
                _prediction_passthrough,
                allowed_context=prediction_context_block,
            )
            if not relay_ok:
                logger.info("Hub authored invented identifiers — passthrough used")
            yield PredictionPassthroughEvent(content=_prediction_passthrough)
            passthrough = _prediction_passthrough
        elif terminal_fired == "crypto" and _get_crypto_passthrough():
            crypto_output = _get_crypto_passthrough() or ""
            yield CryptoPassthroughEvent(content=crypto_output)
            passthrough = crypto_output
        elif terminal_fired == "strategy" and _get_strategy_passthrough():
            # Strategy is a terminal author whose output is a deterministic
            # nine-section (or pending-stub) deliverable. Emit it verbatim and
            # drop any hub preamble (e.g. handoff-retry narration) — the hub is
            # not trusted to relay it clean, as prediction/crypto already are.
            strategy_state = _get_strategy_passthrough()
            strategy_output = strategy_state.content if strategy_state else ""
            yield StrategyPassthroughEvent(content=strategy_output)
            passthrough = strategy_output

        # Cache the response for future follow-up questions
        final_response = passthrough if passthrough is not None else answer.text()
        if self._cache and final_response:
            await self._cache.store(
                query=query,  # Original query, not augmented
                response=final_response,
            )


async def create_central_hub() -> CentralHubAgent:
    """Create and initialize a Central Hub Agent.

    This will also initialize all specialist agents:
    - Fundamentals Agent (FMP)
    - Market Data Agent (FMP)
    - Events/News Agent (FMP)
    - Options Agent (Massive)
    - Screener Agent (FMP)
    - Portfolio Agent (FMP)
    - Strategy Agent (backtest-server)
    - Research Agent (Exa)
    - Prediction Markets Agent (Polymarket)
    - Crypto Agent (Coinbase spot)

    Opik tracing is automatically initialized before agent creation
    if OPIK_ENABLED=true (default). Traces are sent to the Opik UI.

    Returns:
        Initialized CentralHubAgent instance.

    Raises:
        MCPClientError: If any specialist fails to initialize.

    Example:
        ```python
        hub = await create_central_hub()
        # Use hub.agent with Agent SDK Runner
        await hub.close()  # Don't forget to close!
        ```
    """
    # Enable file logging before anything else

    configure_file_logging()

    # Initialize Opik tracing before creating agents (patches Agent SDK)
    init_opik()

    hub = CentralHubAgent()
    await hub.initialize()
    return hub
