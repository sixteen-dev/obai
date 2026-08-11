#!/usr/bin/env python3
"""Headless CLI for OBaI — multi-agent financial research assistant.

Commands:
    obai query "What is AAPL trading at?"       Single query, streams to stdout
    obai query "What is AAPL trading at?" --json JSON output for agents
    obai query "AAPL price" --session s1         Named session (multi-turn)
    obai chat                                     Interactive REPL
    obai status                                   MCP server connectivity

Exit codes:
    0  Success
    1  Guardrail rejection (non-financial query)
    2  CLI usage error (bad args, typer default)
    3  Infrastructure error (MCP down, API timeout)
"""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import json
import logging
import os
import sys
import time
import uuid
import warnings
from datetime import datetime, timezone
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents import SQLiteSession

    from core_agents.central_hub_agent import CentralHubAgent
    from core_agents.hub_settings import HubSettings, HubSettingsStore

# Keep console quiet by default — only stdlib, no heavy imports.
logging.basicConfig(level=logging.WARNING)
for _h in logging.root.handlers:
    if isinstance(_h, logging.StreamHandler) and not isinstance(_h, logging.FileHandler):
        _h.setLevel(logging.WARNING)

with contextlib.suppress(ImportError):
    import readline as _readline  # noqa: F401 — enables arrow-key history in input()

import typer

# --- Constants ---

_VERSION = _pkg_version("obai")
_SESSION_DB = Path.home() / ".obai" / "sessions.db"
_EXIT_GUARDRAIL = 1
_EXIT_INFRA = 3

_AGENT_SYSTEM_LOADED = False


def _bootstrap_agent_system() -> None:
    """Load heavy agent dependencies with import noise suppression.

    Imports the OpenAI Agent SDK, core agent modules, and evaluation
    scorers into sys.modules so subsequent local imports are instant.
    No-op after the first call.
    """
    global _AGENT_SYSTEM_LOADED
    if _AGENT_SYSTEM_LOADED:
        return

    # Suppress third-party deprecation warnings before they fire on import.
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"opik\..*")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"sentry_sdk\..*")
    warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"aiohttp\..*")
    warnings.filterwarnings("ignore", category=DeprecationWarning, message=r"enable_cleanup_closed")
    os.environ.setdefault("LITELLM_LOG", "ERROR")

    # Redirect stderr during import to suppress sentry_sdk.Hub deprecation
    # warning that opik emits at import time (bypasses Python warnings).
    devnull = open(os.devnull, "w")  # noqa: SIM115, PTH123
    old_stderr = sys.stderr
    sys.stderr = devnull
    try:
        import agents  # noqa: F401
        import openai.types.responses  # noqa: F401

        from core_agents import central_hub_agent, config, guardrails  # noqa: F401
        from evaluation.scorers import faithfulness  # noqa: F401
    finally:
        sys.stderr = old_stderr
        devnull.close()

    # Fix log handlers that captured devnull as their stream.
    _fix_closed_log_streams()

    _AGENT_SYSTEM_LOADED = True


def _fix_closed_log_streams() -> None:
    """Repair StreamHandlers that captured the devnull redirect.

    Only touches handlers whose stream is closed (pointing at devnull).
    Preserves the current level of open handlers so --verbose is not
    clobbered when _bootstrap_agent_system runs after _verbose_callback.
    """
    real_stderr = sys.stderr
    all_handlers: list[logging.Handler] = list(logging.root.handlers)
    for logger_ref in logging.root.manager.loggerDict.values():
        if isinstance(logger_ref, logging.Logger):
            all_handlers.extend(logger_ref.handlers)
    for handler in all_handlers:
        if (
            isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
            and handler.stream.closed
        ):
            handler.stream = real_stderr
            handler.setLevel(logging.WARNING)


# --- CLI setup ---


def _version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        typer.echo(f"obai {_VERSION}")
        raise typer.Exit()


def _verbose_callback(value: bool) -> None:
    """Enable debug logging when --verbose is passed."""
    if value:
        logging.getLogger().setLevel(logging.DEBUG)
        # Lower ALL console handlers (root + library loggers) so verbose
        # output includes everything.
        all_handlers: list[logging.Handler] = list(logging.root.handlers)
        for logger_ref in logging.root.manager.loggerDict.values():
            if isinstance(logger_ref, logging.Logger):
                all_handlers.extend(logger_ref.handlers)
        for handler in all_handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(
                handler, logging.FileHandler
            ):
                handler.setLevel(logging.DEBUG)


cli = typer.Typer(
    name="obai",
    help=(
        "OBaI — Multi-agent financial research assistant.\n\n"
        "Query stock prices, fundamentals, options, news, and more.\n\n"
        "Run without a subcommand to launch the interactive TUI.\n\n"
        "Requires OPENAI_API_KEY and MCP servers running (see `obai status`)."
    ),
    invoke_without_command=True,
    add_completion=True,
)


@cli.callback(invoke_without_command=True)
def _global_options(
    ctx: typer.Context,
    _version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
    _verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        callback=_verbose_callback,
        is_eager=True,
        help="Enable debug logging.",
    ),
) -> None:
    """Global options processed before subcommands."""
    if ctx.invoked_subcommand is None:
        _launch_tui()


def _launch_tui() -> None:
    """Launch the OBaI Textual TUI (lazy import to avoid textual overhead for headless)."""
    from clients.cli.tui import main as tui_main

    tui_main()


# --- Shared helpers ---


async def _init_hub() -> CentralHubAgent:
    """Create and initialize the central hub agent."""
    from core_agents.central_hub_agent import create_central_hub

    return await create_central_hub()


def _make_session(session_id: str | None) -> tuple[str, SQLiteSession]:
    """Create a named or ephemeral file-backed session.

    Returns:
        Tuple of (session_id, SQLiteSession).
    """
    from agents import SQLiteSession

    sid = session_id or f"cli_{uuid.uuid4().hex[:8]}"
    _SESSION_DB.parent.mkdir(parents=True, exist_ok=True)
    return sid, SQLiteSession(sid, db_path=str(_SESSION_DB))


def _build_result(
    *,
    query: str,
    response: str,
    agents_called: list[str],
    tool_calls: list[dict[str, str]],
    elapsed_ms: int,
    session_id: str,
    guardrail_rejected: bool = False,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build the structured result dict for JSON output."""
    from core_agents.config import get_config

    config = get_config()
    result: dict[str, Any] = {
        "query": query,
        "response": response or None,
        "agents_called": agents_called,
        "tool_calls": tool_calls,
        "elapsed_ms": elapsed_ms,
        "guardrail_rejected": guardrail_rejected,
        "session_id": session_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": config.orchestrator_model,
    }
    if error:
        result["error"] = error
    return result


def _emit_json(result: dict[str, Any]) -> None:
    """Write JSON result to stdout."""
    sys.stdout.write(json.dumps(result, indent=2) + "\n")


def _print_faithfulness(result: dict[str, Any]) -> None:
    """Print a compact faithfulness summary to stderr."""
    num_acc = result.get("numeric_accuracy")
    passed = result.get("faithfulness_pass")
    semantic = result.get("semantic_score")

    parts: list[str] = []
    if passed is not None:
        icon = "\u2713" if passed else "\u2717"
        parts.append(f"Faithful: {icon}")
    if num_acc is not None:
        parts.append(f"numeric={num_acc:.0%}")
    if semantic is not None:
        parts.append(f"semantic={semantic:.2f}")

    if parts:
        sys.stderr.write(f"  [{', '.join(parts)}]\n")


def _print_completeness(result: dict[str, Any]) -> None:
    """Print a compact completeness summary to stderr."""
    coverage = result.get("coverage_score")
    passed = result.get("completeness_pass")

    parts: list[str] = []
    if passed is not None:
        icon = "\u2713" if passed else "\u2717"
        parts.append(f"Complete: {icon}")
    if coverage is not None:
        parts.append(f"coverage={coverage:.2f}")

    if parts:
        sys.stderr.write(f"  [{', '.join(parts)}]\n")


def _read_query(query_text: str) -> str:
    """Read query from argument or stdin when '-' is passed."""
    if query_text == "-":
        return sys.stdin.read().strip()
    return query_text


async def _run_query(  # noqa: PLR0912
    query: str,
    hub: CentralHubAgent,
    session: SQLiteSession,
    session_id: str,
    *,
    json_mode: bool = False,
) -> dict[str, Any]:
    """Run a query through the hub, streaming output to stdout.

    Args:
        query: User query text.
        hub: Initialized CentralHubAgent.
        session: SQLiteSession for conversation memory.
        session_id: Session identifier string.
        json_mode: If True, collect and print structured JSON.

    Returns:
        Result dict with query metadata.

    Raises:
        SystemExit: On guardrail rejection (1) or infra error (3).
    """
    from agents.items import ItemHelpers, MessageOutputItem
    from agents.stream_events import (
        AgentUpdatedStreamEvent,
        RawResponsesStreamEvent,
        RunItemStreamEvent,
    )
    from openai.types.responses import ResponseTextDeltaEvent

    from core_agents.central_hub_agent import (
        CryptoPassthroughEvent,
        PredictionPassthroughEvent,
        StrategyPassthroughEvent,
        get_inner_tool_outputs,
    )
    from core_agents.config import get_config
    from core_agents.guardrails import get_rejection_message
    from core_agents.response_assembly import AnswerAccumulator
    from evaluation.scorers.faithfulness import (
        CompletenessScorer,
        FaithfulnessScorer,
        build_scorer_input,
    )

    start = time.perf_counter()
    # Holds the text streamed since the last hub tool call (or query start).
    # Resets on every tool_call_item so intermediate "thinking" narration
    # gets discarded, and drops any message the model labelled commentary —
    # only the answer survives to stdout.
    answer = AnswerAccumulator()
    # A terminal specialist's output replaces the answer wholesale.
    passthrough: str | None = None
    agents_called: list[str] = []
    tool_calls: list[dict[str, str]] = []
    current_agent = "central_hub"

    try:
        async for event in hub.run(query, session):
            # Terminal passthrough: use specialist output directly
            if isinstance(
                event,
                PredictionPassthroughEvent | CryptoPassthroughEvent | StrategyPassthroughEvent,
            ):
                passthrough = event.content
                continue

            if isinstance(event, AgentUpdatedStreamEvent):
                agent_name = event.new_agent.name
                if agent_name not in agents_called:
                    agents_called.append(agent_name)
                current_agent = agent_name

            elif isinstance(event, RunItemStreamEvent):
                item = event.item
                item_type = getattr(item, "type", None)
                if item_type == "tool_call_item":
                    raw = getattr(item, "raw_item", None)
                    if raw:
                        name: str = getattr(raw, "name", "unknown")
                        tool_calls.append({"tool": name, "agent": current_agent})
                    # Anything streamed before this hub tool call was thinking.
                    answer.reset()
                elif item_type == "message_output_item" and isinstance(item, MessageOutputItem):
                    # Carries the phase label, and the whole text when no
                    # deltas arrived.
                    answer.note_message(
                        item.raw_item.id,
                        ItemHelpers.text_message_output(item),
                        item.raw_item.phase,
                    )

            elif isinstance(event, RawResponsesStreamEvent):
                data = event.data
                if isinstance(data, ResponseTextDeltaEvent) and data.delta:
                    answer.add_delta(data.item_id, data.delta)

        response_text = passthrough if passthrough is not None else answer.text()

        if not json_mode and response_text:
            sys.stdout.write(response_text)
            sys.stdout.write("\n")
            sys.stdout.flush()

    except Exception as e:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        ename = type(e).__name__

        if "InputGuardrailTripwireTriggered" in ename:
            gr = getattr(e, "guardrail_result", None)
            out = getattr(gr, "output", None)
            info = getattr(out, "output_info", None)
            msg = get_rejection_message(info) if info else "Query not related to financial topics."
            if json_mode:
                _emit_json(
                    _build_result(
                        query=query,
                        response="",
                        agents_called=agents_called,
                        tool_calls=tool_calls,
                        elapsed_ms=elapsed_ms,
                        session_id=session_id,
                        guardrail_rejected=True,
                        error={"type": "guardrail_rejection", "message": msg},
                    )
                )
            else:
                sys.stderr.write(f"Error: {msg}\n")
            raise SystemExit(_EXIT_GUARDRAIL) from e

        # Provide actionable hints for common errors
        err_str = str(e)
        hint = ""
        if "401" in err_str or "authentication" in err_str.lower() or "bearer" in err_str.lower():
            hint = "\nHint: Set OPENAI_API_KEY environment variable with a valid API key."

        # Infrastructure / unexpected errors
        if json_mode:
            _emit_json(
                _build_result(
                    query=query,
                    response="",
                    agents_called=agents_called,
                    tool_calls=tool_calls,
                    elapsed_ms=elapsed_ms,
                    session_id=session_id,
                    error={"type": ename, "message": err_str + hint},
                )
            )
        else:
            sys.stderr.write(f"Error: {e}\n{hint}\n" if hint else f"Error: {e}\n")
        raise SystemExit(_EXIT_INFRA) from e

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # Run faithfulness + completeness scoring against raw MCP tool outputs
    faithfulness: dict[str, Any] | None = None
    completeness: dict[str, Any] | None = None
    if not get_config().enable_inline_scoring:
        result = _build_result(
            query=query,
            response=response_text,
            agents_called=agents_called,
            tool_calls=tool_calls,
            elapsed_ms=elapsed_ms,
            session_id=session_id,
        )
        if json_mode:
            _emit_json(result)
        return result
    inner_outputs = get_inner_tool_outputs()
    if inner_outputs and response_text:
        scorer_input = build_scorer_input(response_text, inner_outputs)
        if scorer_input:
            try:
                faithfulness = await FaithfulnessScorer(skip_llm=True).score(
                    output=scorer_input,
                    query=query,
                )
            except Exception as exc:
                logging.getLogger(__name__).debug("Faithfulness scoring failed: %s", exc)
            try:
                completeness = await CompletenessScorer().score(
                    output=scorer_input,
                    query=query,
                )
            except Exception as exc:
                logging.getLogger(__name__).debug("Completeness scoring failed: %s", exc)

    result = _build_result(
        query=query,
        response=response_text,
        agents_called=agents_called,
        tool_calls=tool_calls,
        elapsed_ms=elapsed_ms,
        session_id=session_id,
    )
    if faithfulness:
        result["faithfulness"] = faithfulness
    if completeness:
        result["completeness"] = completeness
    if json_mode:
        _emit_json(result)
    else:
        if faithfulness and not faithfulness.get("skipped"):
            _print_faithfulness(faithfulness)
        if completeness and not completeness.get("skipped"):
            _print_completeness(completeness)
    return result


# --- Commands ---


@cli.command()
def tui() -> None:
    """Launch the interactive terminal UI (rich panels, markdown, debug view)."""
    _launch_tui()


@cli.command()
def query(
    query_text: str = typer.Argument(
        ...,
        help="The query to send to OBaI (use '-' to read from stdin)",
    ),
    json_mode: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output structured JSON",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session",
        "-s",
        help="Named session ID for multi-turn (persisted to ~/.obai/sessions.db)",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Override orchestrator model for this query",
    ),
    scoring: bool = typer.Option(
        False,
        "--scoring",
        help="Run faithfulness + completeness scoring on the response",
    ),
) -> None:
    """Send a single query and stream the response."""

    async def _main() -> None:
        _bootstrap_agent_system()
        from core_agents.config import get_config

        if model:
            get_config().orchestrator_model = model
        if scoring:
            get_config().enable_inline_scoring = True
        hub = await _init_hub()
        try:
            sid, session = _make_session(session_id)
            resolved_query = _read_query(query_text)
            await _run_query(resolved_query, hub, session, sid, json_mode=json_mode)
        finally:
            await hub.close()

    asyncio.run(_main())


@cli.command()
def chat(
    session_id: str | None = typer.Option(
        None,
        "--session",
        "-s",
        help="Named session ID (persisted to ~/.obai/sessions.db)",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Override orchestrator model",
    ),
    scoring: bool = typer.Option(
        False,
        "--scoring",
        help="Run faithfulness + completeness scoring on every response",
    ),
) -> None:
    """Interactive REPL with conversation memory."""

    async def _main() -> None:
        _bootstrap_agent_system()
        from core_agents.config import get_config

        if model:
            get_config().orchestrator_model = model
        if scoring:
            get_config().enable_inline_scoring = True
        typer.echo("OBaI Chat (type 'quit' to exit, 'clear' to reset, 'help' for commands)")
        hub = await _init_hub()
        typer.echo("Ready.\n")
        try:
            sid, session = _make_session(session_id)

            while True:
                try:
                    user_input = input("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    typer.echo("\n")
                    break

                if not user_input:
                    continue
                if user_input.lower() in ("quit", "exit"):
                    break
                if user_input.lower() == "help":
                    _chat_help()
                    continue
                if user_input.lower() == "clear":
                    sid, session = _make_session(None)
                    await hub.clear_cache()
                    typer.echo("Session cleared.\n")
                    continue

                t0 = time.perf_counter()
                with contextlib.suppress(SystemExit):
                    await _run_query(user_input, hub, session, sid, json_mode=False)
                elapsed = time.perf_counter() - t0
                typer.echo(f"  ({elapsed:.1f}s)\n")
        finally:
            await hub.close()

    asyncio.run(_main())


def _chat_help() -> None:
    """Print available chat REPL commands."""
    typer.echo(
        "\nCommands:\n"
        "  help   — Show this help\n"
        "  clear  — Reset session and agent cache\n"
        "  quit   — Exit (also: exit, Ctrl+C, Ctrl+D)\n"
        "\nExamples:\n"
        "  What is AAPL trading at?\n"
        "  Compare AAPL and MSFT P/E ratios\n"
        "  What are the top tech stocks by market cap?\n"
    )


@cli.command()
def status(
    json_mode: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Output structured JSON",
    ),
) -> None:
    """Check connectivity to all MCP data servers."""

    async def _main() -> int:
        import httpx

        from core_agents.config import get_config

        config = get_config()
        servers = [
            ("Fundamentals", config.mcp_fundamentals_url),
            ("Market Data", config.mcp_market_data_url),
            ("Events/News", config.mcp_events_news_url),
            ("Options", config.mcp_options_url),
            ("Screening", config.mcp_screener_url),
            ("Portfolio", config.mcp_portfolio_url),
            ("Backtest", config.mcp_backtest_url),
            ("Research", config.mcp_research_url),
            ("Prediction Markets", config.mcp_prediction_markets_url),
            ("Crypto", config.mcp_crypto_url),
        ]

        results: list[dict[str, Any]] = []
        any_down = False

        async with httpx.AsyncClient(timeout=5.0) as client:
            for name, url in servers:
                entry = await _check_server(client, name, url)
                results.append(entry)
                if entry["status"] != "ok":
                    any_down = True

        if json_mode:
            payload = {"servers": results, "all_healthy": not any_down}
            sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        else:
            _print_status_table(results)

        return _EXIT_INFRA if any_down else 0

    exit_code = asyncio.run(_main())
    if exit_code:
        raise SystemExit(exit_code)


def _print_status_table(results: list[dict[str, Any]]) -> None:
    """Print a formatted status table with colors."""
    max_name = max(len(r["name"]) for r in results)
    max_url = max(len(r["url"]) for r in results)

    # Header
    header = f"{'Server':<{max_name}}  {'URL':<{max_url}}  Status"
    typer.echo(header)
    typer.echo("-" * len(header))

    for r in results:
        latency = r["latency_ms"]
        if r["status"] == "ok":
            mark = typer.style(f"\u2713 OK ({latency}ms)", fg=typer.colors.GREEN)
        else:
            mark = typer.style(f"\u2717 {r['status']}", fg=typer.colors.RED)
        typer.echo(f"{r['name']:<{max_name}}  {r['url']:<{max_url}}  {mark}")


async def _check_server(
    client: Any,
    name: str,
    url: str,
) -> dict[str, Any]:
    """Check a single MCP server health endpoint.

    Args:
        client: httpx.AsyncClient instance.
        name: Display name of the server.
        url: MCP server URL.

    Returns:
        Dict with name, url, status, and latency_ms.
    """
    import httpx

    t0 = time.perf_counter()
    try:
        resp = await client.get(url.replace("/mcp", "/health"))
        latency = int((time.perf_counter() - t0) * 1000)
        ok = resp.status_code < 500  # noqa: PLR2004
        return {
            "name": name,
            "url": url,
            "status": "ok" if ok else f"http_{resp.status_code}",
            "latency_ms": latency,
        }
    except httpx.ConnectError:
        return {"name": name, "url": url, "status": "connection_refused", "latency_ms": None}
    except httpx.TimeoutException:
        return {"name": name, "url": url, "status": "timeout", "latency_ms": None}
    except Exception as e:
        return {"name": name, "url": url, "status": type(e).__name__, "latency_ms": None}


@cli.command()
def web(
    port: int = typer.Option(8090, "--port", "-p", help="Server port"),
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="Bind address"),
    reload: bool = typer.Option(
        False,
        "--reload",
        help="Dev: restart on Python source changes (each restart re-inits the hub).",
    ),
) -> None:
    """Launch the web UI in a browser."""
    from clients.web.server import run_server

    typer.echo(f"Starting OBaI Web UI at http://{host}:{port}")
    run_server(host=host, port=port, reload=reload)


# --- Lifecycle subcommands ---


@cli.command()
def start() -> None:
    """Start OBaI: Docker services (MCP + Opik) and the web UI. Preserves data."""
    from clients.cli.lifecycle import run_start

    run_start()


@cli.command()
def stop() -> None:
    """Stop all OBaI services. Docker images and data volumes are preserved."""
    from clients.cli.lifecycle import run_stop

    run_stop()


@cli.command("teardown", hidden=True)
def teardown() -> None:
    """Alias for `stop`."""
    from clients.cli.lifecycle import run_stop

    run_stop()


@cli.command()
def restart() -> None:
    """Restart OBaI: stop everything, then bring it back up."""
    from clients.cli.lifecycle import run_restart

    run_restart()


@cli.command()
def upgrade(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt (for scripts/CI).",
    ),
) -> None:
    """Pull the latest version and restart on it. Prompts before changing anything."""
    from clients.cli.lifecycle import run_upgrade

    run_upgrade(assume_yes=yes)


@cli.command("update", hidden=True)
def update(
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt (for scripts/CI).",
    ),
) -> None:
    """Alias for `upgrade`."""
    from clients.cli.lifecycle import run_upgrade

    run_upgrade(assume_yes=yes)


# --- Config subcommands ---

_KNOWN_KEYS = [
    ("OPENAI_API_KEY", "Powers all agents (OpenAI Agent SDK)"),
    ("FMP_API_KEY", "Financial Modeling Prep (6 of 8 MCP servers)"),
    ("MASSIVE_API_KEY", "Options chain data (options-server)"),
    ("TAVILY_API_KEY", "AI news search (events-news-server)"),
    ("EXA_API_KEY", "Semantic search (research-server)"),
    ("ANTHROPIC_API_KEY", "LLM-judge evaluation scoring"),
]

_ENV_FILE = Path.home() / ".obai" / ".env"

# The two env vars that outrank ~/.obai/settings.json (see
# core_agents.config._HubSettingsSource). A stale export here is the reason a
# saved hub setting appears to do nothing, so both setters warn about it and
# `show` names it as the source.
_HUB_MODEL_ENV = "ORCHESTRATOR_MODEL"
_HUB_EFFORT_ENV = "ORCHESTRATOR_REASONING_EFFORT"

config_app = typer.Typer(help="Manage OBaI configuration.")
cli.add_typer(config_app, name="config")


@config_app.command("set-key")
def config_set_key(
    key_name: str = typer.Argument(
        ...,
        help="API key name (e.g., OPENAI_API_KEY, FMP_API_KEY)",
    ),
    value: str | None = typer.Option(
        None,
        "--value",
        "-v",
        help="Key value (prompted securely if not provided)",
    ),
) -> None:
    """Set an API key in ~/.obai/.env."""
    valid_names = [k for k, _ in _KNOWN_KEYS]
    if key_name not in valid_names:
        typer.echo(f"Unknown key: {key_name}")
        typer.echo(f"Valid keys: {', '.join(valid_names)}")
        raise typer.Exit(1)

    if value is None:
        value = getpass.getpass(f"Enter {key_name}: ")
    if not value:
        typer.echo("Empty value — key not saved.")
        raise typer.Exit(1)

    _ENV_FILE.parent.mkdir(parents=True, exist_ok=True)

    # Read existing lines, update or append
    lines: list[str] = []
    found = False
    if _ENV_FILE.is_file():
        for line in _ENV_FILE.read_text().splitlines():
            if line.startswith(f"{key_name}="):
                lines.append(f"{key_name}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key_name}={value}")

    _ENV_FILE.write_text("\n".join(lines) + "\n")
    _ENV_FILE.chmod(0o600)
    typer.echo(f"{key_name} saved to {_ENV_FILE}")


def _env_override(var_name: str) -> str | None:
    """Return the environment value that outranks the hub settings file.

    pydantic-settings matches env vars case-insensitively, so a lowercase
    export shadows the file exactly as an upper-cased one does.

    Args:
        var_name: Upper-cased environment variable name.

    Returns:
        The value found in the environment, or None when it is unset.
    """
    for key, value in os.environ.items():
        if key.upper() == var_name:
            return value
    return None


def _load_hub_settings(store: HubSettingsStore) -> HubSettings:
    """Load hub settings, turning a corrupt file into a clean CLI error.

    Args:
        store: Settings store to read.

    Returns:
        The stored settings, or the shipped defaults when no file exists.

    Raises:
        typer.Exit: The settings file exists but is not valid hub settings.
    """
    try:
        return store.load()
    except ValueError as e:
        typer.echo(typer.style(str(e), fg=typer.colors.RED))
        raise typer.Exit(1) from e


def _echo_hub_saved(label: str, value: str, path: Path, env_var: str) -> None:
    """Confirm a saved hub setting and flag an env var that outranks it.

    Args:
        label: Human-readable setting name.
        value: Value just written.
        path: Settings file that was written.
        env_var: Environment variable that overrides the file.
    """
    typer.echo(f"{label} set to {typer.style(value, fg=typer.colors.GREEN)} in {path}")
    override = _env_override(env_var)
    if override is not None:
        typer.echo(
            typer.style(
                f"Warning: {env_var}={override} is set in your environment and outranks "
                f"this file. Unset it or the saved value will not take effect.",
                fg=typer.colors.YELLOW,
            )
        )
    # Deliberately not "obai restart": that tears down and rebuilds the whole
    # Docker stack for a two-field change. Only the hub reads these settings,
    # and every client builds its own on startup.
    typer.echo("Applies to clients started from now on; relaunch a running obai chat/tui.")


@config_app.command("set-model")
def config_set_model(
    model: str = typer.Argument(
        ...,
        help="Hub model name (e.g., gpt-5.6-sol, gpt-5.6-terra)",
    ),
) -> None:
    """Set the hub model in ~/.obai/settings.json."""
    from core_agents.hub_settings import HUB_MODELS, HubSettingsStore

    if model not in HUB_MODELS:
        typer.echo(f"Unknown model: {model}")
        typer.echo(f"Valid models: {', '.join(HUB_MODELS)}")
        raise typer.Exit(1)

    store = HubSettingsStore()
    settings = _load_hub_settings(store)
    store.save(settings.model_copy(update={"hub_model": model}))
    _echo_hub_saved("Hub model", model, store.path, _HUB_MODEL_ENV)


@config_app.command("set-effort")
def config_set_effort(
    effort: str = typer.Argument(
        ...,
        help="Hub reasoning effort (e.g., medium, high, xhigh, max)",
    ),
) -> None:
    """Set the hub reasoning effort in ~/.obai/settings.json."""
    from core_agents.hub_settings import HUB_REASONING_EFFORTS, HubSettingsStore

    if effort not in HUB_REASONING_EFFORTS:
        typer.echo(f"Unknown reasoning effort: {effort}")
        typer.echo(f"Valid efforts: {', '.join(HUB_REASONING_EFFORTS)}")
        raise typer.Exit(1)

    store = HubSettingsStore()
    settings = _load_hub_settings(store)
    store.save(settings.model_copy(update={"hub_reasoning_effort": effort}))
    _echo_hub_saved("Hub reasoning effort", effort, store.path, _HUB_EFFORT_ENV)


def _hub_file_keys(path: Path) -> set[str]:
    """Return the field names actually present in the settings file.

    Separates "the user chose this" from "this is the shipped default" for a
    value that happens to equal the default. Only called once the file has
    been validated, so it is known to parse.

    Args:
        path: Settings file path.

    Returns:
        Field names in the file; empty when the file is absent or empty.
    """
    if not path.is_file():
        return set()
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return set()
    return {str(key) for key in json.loads(raw)}


def _hub_provenance(stored: str, field: str, env_var: str, file_keys: set[str]) -> tuple[str, str]:
    """Resolve one hub setting's effective value and where it came from.

    An environment value is reported verbatim and is deliberately not
    validated here — it is what the hub will actually receive, valid or not.
    :func:`_echo_hub_settings` flags it against the accepted values.

    Args:
        stored: Value the settings file resolved to.
        field: Field name as written in the settings file.
        env_var: Environment variable that outranks the file.
        file_keys: Field names present in the settings file.

    Returns:
        The effective value and a human-readable source for it.
    """
    override = _env_override(env_var)
    if override is not None:
        return override, f"env {env_var}"
    if field in file_keys:
        return stored, "settings file"
    return stored, "shipped default"


def _echo_hub_settings() -> None:
    """Print the resolved hub model and effort with the source of each.

    Raises:
        typer.Exit: The settings file exists but is not valid hub settings.
    """
    from core_agents.hub_settings import HUB_MODELS, HUB_REASONING_EFFORTS, HubSettingsStore

    store = HubSettingsStore()
    settings = _load_hub_settings(store)
    file_keys = _hub_file_keys(store.path)
    rows = (
        ("hub model", settings.hub_model, "hub_model", _HUB_MODEL_ENV, HUB_MODELS),
        (
            "reasoning effort",
            settings.hub_reasoning_effort,
            "hub_reasoning_effort",
            _HUB_EFFORT_ENV,
            HUB_REASONING_EFFORTS,
        ),
    )

    typer.echo(f"OBaI Hub Settings ({store.path}):\n")
    rejected: list[str] = []
    for label, stored, field, env_var, choices in rows:
        value, source = _hub_provenance(stored, field, env_var, file_keys)
        accepted = value in choices
        colour = typer.colors.GREEN if accepted else typer.colors.RED
        typer.echo(f"  {label:<22} {typer.style(f'{value:<16}', fg=colour)}  (from {source})")
        if not accepted:
            rejected.append(f"    {env_var}={value!r} — accepted values: {', '.join(choices)}")

    # An env var is applied verbatim and outranks everything, so an unlisted
    # value here is what the hub will get. Saying so is the whole point of
    # this command: without it `show` reports a healthy green setting for a
    # value that can fail every query.
    if rejected:
        typer.echo(
            typer.style(
                "\n  Not an accepted value — the hub may fail to start:", fg=typer.colors.RED
            ),
        )
        typer.echo("\n".join(rejected))
    typer.echo("\nChanges apply to clients started from now on.\n")


@config_app.command("show")
def config_show() -> None:
    """Display API key status (masked) and the resolved hub settings.

    Raises:
        typer.Exit: The hub settings file exists but is not valid.
    """
    typer.echo(f"\nOBaI API Keys ({_ENV_FILE}):\n")
    for key_name, desc in _KNOWN_KEYS:
        val = os.environ.get(key_name, "")
        if val:
            masked = val[:8] + "..." if len(val) > 8 else val  # noqa: PLR2004
            status = typer.style(f"\u2713 {masked}", fg=typer.colors.GREEN)
        else:
            status = typer.style("\u2717 not set", fg=typer.colors.RED)
        typer.echo(f"  {key_name:<22} {status}  ({desc})")
    typer.echo("")
    _echo_hub_settings()


def _load_env_file() -> None:
    """Load ~/.obai/.env into os.environ (does not override existing vars)."""
    env_path = Path.home() / ".obai" / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


def main() -> None:
    """Entry point for the obai CLI."""
    _load_env_file()
    cli()


if __name__ == "__main__":
    main()
