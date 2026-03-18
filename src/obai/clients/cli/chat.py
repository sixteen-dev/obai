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
import json
import logging
import os
import sys
import time
import uuid
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Suppress third-party deprecation warnings before they fire on import.
# SentryHubDeprecationWarning from opik->sentry_sdk.Hub bypasses warnings.filterwarnings
# due to Python 3.13 C-level warning internals, so we temporarily redirect stderr.
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"opik\..*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"sentry_sdk\..*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"aiohttp\..*")
warnings.filterwarnings("ignore", category=DeprecationWarning, message=r"enable_cleanup_closed")

# Tell LiteLLM to shut up before it gets imported (it creates its own handler on import).
os.environ.setdefault("LITELLM_LOG", "ERROR")

# Set CLI logging to WARNING so library init noise is suppressed.
# Python's basicConfig is a no-op if handlers already exist, so this
# "wins" over later basicConfig calls in config.py.
logging.basicConfig(level=logging.WARNING)

# Pin the console handler's own level to WARNING.  basicConfig creates a
# StreamHandler(stderr) with level=NOTSET, which defers to the root logger
# level.  When configure_file_logging() later lowers root to INFO (so the
# file handler receives everything), the console handler would start passing
# INFO too.  Setting the handler level explicitly prevents that — console
# stays quiet unless --verbose is passed.
for _h in logging.root.handlers:
    if isinstance(_h, logging.StreamHandler) and not isinstance(_h, logging.FileHandler):
        _h.setLevel(logging.WARNING)

with contextlib.suppress(ImportError):
    import readline as _readline  # noqa: F401 — enables arrow-key history in input()

import typer

# Redirect stderr during import to suppress sentry_sdk.Hub deprecation warning
# that opik emits at import time (bypasses Python warnings infrastructure).
_devnull = open(os.devnull, "w")  # noqa: SIM115, PTH123
_old_stderr = sys.stderr
sys.stderr = _devnull

from agents import SQLiteSession  # noqa: E402
from agents.items import ItemHelpers, MessageOutputItem  # noqa: E402
from agents.stream_events import (  # noqa: E402
    AgentUpdatedStreamEvent,
    RawResponsesStreamEvent,
    RunItemStreamEvent,
)
from openai.types.responses import ResponseTextDeltaEvent  # noqa: E402

from core_agents.central_hub_agent import (  # noqa: E402
    CentralHubAgent,
    create_central_hub,
    get_inner_tool_outputs,
)
from core_agents.config import get_config  # noqa: E402
from core_agents.guardrails import get_rejection_message  # noqa: E402
from evaluation.scorers.faithfulness import (  # noqa: E402
    CompletenessScorer,
    FaithfulnessScorer,
    build_scorer_input,
)

sys.stderr = _old_stderr
_devnull.close()


# Fix logging handlers that captured _devnull as their stream during the
# stderr redirect above.  Any StreamHandler created while sys.stderr pointed
# to /dev/null now holds a closed file — patch them to use the real stderr.
# Also pin ALL console handlers to WARNING so library loggers (LiteLLM, Opik,
# httpx, etc.) that created their own handlers during import stay quiet.
def _fix_closed_log_streams() -> None:
    real_stderr = sys.stderr
    all_handlers: list[logging.Handler] = list(logging.root.handlers)
    for logger_ref in logging.root.manager.loggerDict.values():
        if isinstance(logger_ref, logging.Logger):
            all_handlers.extend(logger_ref.handlers)
    for handler in all_handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            if handler.stream.closed:
                handler.stream = real_stderr
            handler.setLevel(logging.WARNING)


_fix_closed_log_streams()

# --- Constants ---

_VERSION = "0.1.0"
_SESSION_DB = Path.home() / ".obai" / "sessions.db"
_EXIT_GUARDRAIL = 1
_EXIT_INFRA = 3


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
    return await create_central_hub()


def _make_session(session_id: str | None) -> tuple[str, SQLiteSession]:
    """Create a named or ephemeral file-backed session.

    Returns:
        Tuple of (session_id, SQLiteSession).
    """
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
    start = time.perf_counter()
    response_text = ""
    agents_called: list[str] = []
    tool_calls: list[dict[str, str]] = []
    current_agent = "central_hub"
    got_streaming_delta = False

    try:
        async for event in hub.run(query, session):
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
                elif (
                    item_type == "message_output_item"
                    and isinstance(item, MessageOutputItem)
                    and not got_streaming_delta
                ):
                    # Only use MessageOutputItem as fallback when no streaming deltas arrived.
                    msg = ItemHelpers.text_message_output(item)
                    if msg:
                        response_text = msg

            elif isinstance(event, RawResponsesStreamEvent):
                data = event.data
                if isinstance(data, ResponseTextDeltaEvent) and data.delta:
                    if not got_streaming_delta:
                        got_streaming_delta = True
                        response_text = ""
                    response_text += data.delta
                    if not json_mode:
                        sys.stdout.write(data.delta)
                        sys.stdout.flush()

        if not json_mode and response_text:
            sys.stdout.write("\n")

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
        return _build_result(
            query=query,
            response=response_text,
            agents_called=agents_called,
            tool_calls=tool_calls,
            elapsed_ms=elapsed_ms,
            session_id=session_id,
        )
    inner_outputs = get_inner_tool_outputs()
    if inner_outputs and response_text:
        scorer_input = build_scorer_input(response_text, inner_outputs)
        if scorer_input:
            try:
                faithfulness = await FaithfulnessScorer().score(
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
) -> None:
    """Send a single query and stream the response."""

    async def _main() -> None:
        if model:
            get_config().orchestrator_model = model
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
) -> None:
    """Interactive REPL with conversation memory."""

    async def _main() -> None:
        if model:
            get_config().orchestrator_model = model
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
    """Check connectivity to all 6 MCP data servers."""

    async def _main() -> int:
        import httpx

        config = get_config()
        servers = [
            ("Fundamentals", config.mcp_fundamentals_url),
            ("Market Data", config.mcp_market_data_url),
            ("Events/News", config.mcp_events_news_url),
            ("Options", config.mcp_options_url),
            ("Screening", config.mcp_screener_url),
            ("Portfolio", config.mcp_portfolio_url),
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


def main() -> None:
    """Entry point for the obai CLI."""
    cli()


if __name__ == "__main__":
    main()
