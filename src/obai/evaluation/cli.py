#!/usr/bin/env python3
"""CLI for OBaI agent evaluation and debugging.

A rich, verbose CLI for evaluating OBaI agents with full trace capture.

Usage:
    # Basic query trace
    python -m evaluation query "What is AAPL trading at?"

    # Verbose trace output
    python -m evaluation query "What is AAPL trading at?" --verbose

    # Run evaluation with scorers
    python -m evaluation evaluate "What is AAPL trading at?"

    # Run full test suite
    python -m evaluation evaluate --suite
"""

import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

# Add OBaI root to path
obai_root = Path(__file__).parent.parent
sys.path.insert(0, str(obai_root))

from evaluation.eval_runner import (  # noqa: E402
    STANDARD_TEST_CASES,
    OBaIEvaluator,
    TestCase,
    load_test_cases,
    scorer_skip_is_allowed,
)
from evaluation.experiment import (  # noqa: E402
    _filter_test_cases,
    _validate_builtin_scorer_requirements,
    _validate_semantic_scorer_credentials,
    run_evaluate_as_experiment,
    run_experiment,
)
from evaluation.trace.capture import TraceCapture  # noqa: E402
from evaluation.trace.types import (  # noqa: E402
    AgentEvent,
    ErrorEvent,
    EventType,
    GuardrailEvent,
    ToolCallEvent,
    Trace,
)

app = typer.Typer(
    name="obai-eval",
    help="OBaI Agent Evaluation CLI - Debug, trace, and evaluate agent execution",
    no_args_is_help=True,
)

console = Console()


def print_banner() -> None:
    """Print CLI banner."""
    banner = Text()
    banner.append("OBaI Evaluation CLI", style="bold cyan")
    banner.append(" - Agent Debugging & Scoring", style="dim")
    console.print(Panel(banner, border_style="cyan"))


def format_elapsed(ms: float) -> str:
    """Format elapsed time."""
    if ms < 1000:
        return f"{ms:.0f}ms"
    return f"{ms / 1000:.2f}s"


def print_trace_event(event_type: str, message: str, elapsed_ms: float) -> None:
    """Print a trace event with timing."""
    time_str = f"[dim][T+{format_elapsed(elapsed_ms)}][/dim]"
    console.print(f"{time_str} [bold]{event_type}[/bold]")
    console.print(f"  {message}")


def print_tool_call(
    tool_name: str,
    args: dict[str, Any],
    response: dict[str, Any] | None = None,
    error: str | None = None,
    latency_ms: float | None = None,
) -> None:
    """Print tool call details."""
    args_str = json.dumps(args, indent=2) if args else "{}"

    if error:
        console.print(f"  [red]✗ {tool_name}[/red]")
        console.print(f"    Args: {args_str}")
        console.print(f"    [red]Error: {error}[/red]")
    else:
        latency_str = f" ({format_elapsed(latency_ms)})" if latency_ms else ""
        console.print(f"  [green]✓ {tool_name}[/green]{latency_str}")
        console.print(f"    Args: [dim]{json.dumps(args)}[/dim]")

        if response:
            # Truncate large responses
            resp_str = json.dumps(response, indent=2)
            if len(resp_str) > 500:
                resp_str = resp_str[:500] + "..."
            console.print(f"    Response: [dim]{resp_str}[/dim]")


def print_verbose_trace(trace: Trace) -> None:
    """Print full verbose trace output."""
    console.print()
    console.rule("[bold cyan]EXECUTION TRACE[/bold cyan]")
    console.print()

    for event in trace.events:
        if isinstance(event, GuardrailEvent):
            status = "[green]PASSED[/green]" if event.passed else "[red]REJECTED[/red]"
            msg = f"Classification: {event.classification or 'N/A'}, Status: {status}"
            if event.confidence:
                msg += f", Confidence: {event.confidence:.2f}"
            if event.rejection_reason:
                msg += f"\n  Reason: {event.rejection_reason}"
            print_trace_event("GUARDRAIL", msg, event.elapsed_ms)

        elif isinstance(event, AgentEvent):
            if event.is_handoff:
                msg = f"[yellow]→ {event.agent_name}[/yellow] (from {event.from_agent})"
                print_trace_event("HANDOFF", msg, event.elapsed_ms)
            else:
                msg = f"[cyan]{event.agent_name}[/cyan] starting"
                print_trace_event("AGENT", msg, event.elapsed_ms)

        elif isinstance(event, ToolCallEvent):
            if event.event_type == EventType.TOOL_CALL_START:
                print_trace_event("TOOL CALL", f"[bold]{event.tool_name}[/bold]", event.elapsed_ms)
                print_tool_call(
                    event.tool_name,
                    event.tool_args,
                    event.response,
                    event.error,
                    event.latency_ms,
                )

        elif isinstance(event, ErrorEvent):
            print_trace_event(
                "[red]ERROR[/red]",
                f"{event.error_type}: {event.error_message}",
                event.elapsed_ms,
            )

    console.print()


def print_metrics_summary(trace: Trace) -> None:
    """Print metrics summary table."""
    console.rule("[bold cyan]METRICS SUMMARY[/bold cyan]")
    console.print()

    metrics = trace.metrics
    timing = metrics.timing

    # Create metrics table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="dim")
    table.add_column("Value")

    # Routing
    specialists = ", ".join(metrics.specialists_called) or "none"
    table.add_row("Specialists Called", f"[cyan]{specialists}[/cyan]")

    # Sequence
    if metrics.call_sequence:
        seq = " → ".join(metrics.call_sequence)
        table.add_row("Call Sequence", seq)

    # Tools
    table.add_row("Total Tool Calls", str(metrics.total_tool_calls))
    if metrics.redundant_calls > 0:
        table.add_row("Redundant Calls", f"[yellow]{metrics.redundant_calls}[/yellow]")
    table.add_row("Unique Tools", ", ".join(metrics.unique_tools) or "none")

    # Timing
    if timing:
        table.add_row("Total Time", format_elapsed(timing.total_ms))
        if timing.guardrail_ms:
            table.add_row("Guardrail Time", format_elapsed(timing.guardrail_ms))

    console.print(table)
    console.print()


def print_response(trace: Trace) -> None:
    """Print final response."""
    console.rule("[bold green]RESPONSE[/bold green]")
    console.print()

    if trace.final_response:
        console.print(Panel(trace.final_response, border_style="green"))
    else:
        console.print("[yellow]No response generated[/yellow]")

    console.print()


def print_tool_breakdown(trace: Trace) -> None:
    """Print tool call breakdown tree."""
    if not trace.tool_calls:
        return

    console.rule("[bold cyan]TOOL CALLS[/bold cyan]")
    console.print()

    tree = Tree("[bold]Tool Calls[/bold]")

    for tc in trace.tool_calls:
        label = f"[bold]{tc.tool_name}[/bold] ({format_elapsed(tc.latency_ms)})"
        branch = tree.add(label)
        branch.add(f"[dim]Agent: {tc.agent_name}[/dim]")
        branch.add(f"[dim]Args: {json.dumps(tc.args)}[/dim]")

        if tc.error:
            branch.add(f"[red]Error: {tc.error}[/red]")
        elif tc.response:
            resp_str = json.dumps(tc.response)
            if len(resp_str) > 100:
                resp_str = resp_str[:100] + "..."
            branch.add(f"[dim]Response: {resp_str}[/dim]")

    console.print(tree)
    console.print()


async def run_query_with_trace(
    query: str,
    model: str | None,
    verbose: bool = False,
) -> Trace:
    """Run a query and capture full trace.

    The hub always runs on its configured model; ``model`` only labels the
    captured trace. Pass None to label it with the model that actually ran.

    Args:
        query: User query.
        model: Model label for the trace, or None to use the hub's own model.
        verbose: Whether to print verbose output.

    Returns:
        Captured trace.
    """
    # Import agent components
    try:
        from core_agents.central_hub_agent import (
            clear_agent_activity_tracking,
            create_central_hub,
            get_inner_tool_outputs,
        )
        from core_agents.config import get_config
    except ImportError as e:
        console.print(f"[red]Import error: {e}[/red]")
        console.print("[yellow]Make sure you're in the OBaI directory[/yellow]")
        raise typer.Exit(1) from e

    # Get config
    config = get_config()
    if verbose:
        console.print(f"[dim]Model: {model or config.orchestrator_model}[/dim]")

    # Initialize central hub
    console.print("[cyan]Initializing agents...[/cyan]")
    try:
        hub = await create_central_hub()
    except Exception as e:
        console.print(f"[red]Failed to initialize: {e}[/red]")
        raise typer.Exit(1) from e

    try:
        # Create trace capture
        capture = TraceCapture(
            query=query,
            model=model or config.orchestrator_model,
        )
        capture.start()

        # Record initial agent
        capture.record_agent_start("central_hub")

        # Clear activity tracking
        clear_agent_activity_tracking()

        console.print("[cyan]Running query...[/cyan]\n")

        # Run with streaming
        if hub.agent is None:
            console.print("[red]Agent not initialized[/red]")
            raise typer.Exit(1)

        # Drive the hub through the same entry point the web, CLI and TUI use.
        # Running hub.agent directly skipped the sandbox run config the hub
        # builds, and a SandboxAgent without one raises UserError, so no case
        # scored at all. It also skipped the phase-aware answer assembly,
        # terminal passthrough and cache behaviour that decide what a user
        # actually receives -- scoring a different execution path than the one
        # that ships is the single thing this harness must not do.
        async for event in hub.run(query):
            capture.process_sdk_event(event)

        # Attach raw MCP outputs from specialist inner calls
        capture.set_inner_tool_outputs(get_inner_tool_outputs())

        # Finalize trace
        trace = capture.finalize()

        return trace

    except Exception as e:
        error_name = type(e).__name__
        if "InputGuardrailTripwireTriggered" in error_name:
            # Guardrail rejection
            capture.record_guardrail(
                passed=False,
                classification="off_topic",
                rejection_reason=str(e),
            )
            trace = capture.finalize()
            console.print("[yellow]Query rejected by guardrail[/yellow]")
            return trace

        console.print(f"[red]Error: {e}[/red]")
        raise

    finally:
        await hub.close()


@app.command()
def query(
    query_text: Annotated[str, typer.Argument(help="Query to run")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show verbose trace")] = False,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model to use")] = None,
    export: Annotated[
        Path | None, typer.Option("--export", "-e", help="Export trace to JSON file")
    ] = None,
) -> None:
    """Run a query and display trace."""
    print_banner()
    console.print()

    # Run query
    trace = asyncio.run(run_query_with_trace(query_text, model, verbose))

    # Print output based on verbosity
    if verbose:
        print_verbose_trace(trace)
        print_tool_breakdown(trace)

    print_metrics_summary(trace)
    print_response(trace)

    # Export if requested
    if export:
        export_data = trace.model_dump(mode="json", exclude_none=True)
        export.write_text(json.dumps(export_data, indent=2, default=str))
        console.print(f"[green]Trace exported to {export}[/green]")


@app.command()
def test_connection() -> None:
    """Test MCP server connectivity."""
    print_banner()
    console.print()

    console.print("[cyan]Testing MCP server connections...[/cyan]\n")

    async def _test() -> None:
        try:
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
            ]

            table = Table(title="MCP Server Status")
            table.add_column("Server", style="cyan")
            table.add_column("URL", style="dim")
            table.add_column("Status")

            for name, url in servers:
                # Try to connect
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=5.0) as client:
                        # Just check if server responds
                        resp = await client.get(url.replace("/mcp", "/health"))
                        if resp.status_code < 500:
                            table.add_row(name, url, "[green]✓ OK[/green]")
                        else:
                            table.add_row(name, url, f"[yellow]⚠ {resp.status_code}[/yellow]")
                except Exception as e:
                    table.add_row(name, url, f"[red]✗ {type(e).__name__}[/red]")

            console.print(table)

        except ImportError as e:
            console.print(f"[red]Import error: {e}[/red]")

    asyncio.run(_test())


def print_rubric_breakdown(score_data: dict[str, Any]) -> None:
    """Print a detailed rubric breakdown table for LLM judge results.

    Args:
        score_data: LLMJudgeScorer result dict with per-dimension scores.
    """
    rubric_dims = [
        "factual_accuracy",
        "completeness",
        "clarity",
        "tool_use_quality",
        "reasoning_soundness",
    ]

    table = Table(title="LLM Judge Rubric Breakdown", border_style="cyan")
    table.add_column("Dimension", style="cyan")
    table.add_column("Score", justify="center")
    table.add_column("Threshold", justify="center", style="dim")
    table.add_column("Status", justify="center")
    table.add_column("Reasoning", max_width=50)

    for dim in rubric_dims:
        dim_data = score_data.get(dim, {})
        score = dim_data.get("score")
        threshold = dim_data.get("threshold", 3)
        passed = dim_data.get("passed", False)
        reasoning = dim_data.get("reasoning", "")

        score_str = str(score) if score is not None else "N/A"
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        display_name = dim.replace("_", " ").title()

        # Truncate reasoning for table display
        if len(reasoning) > 80:
            reasoning = reasoning[:77] + "..."

        table.add_row(display_name, score_str, str(threshold), status, reasoning)

    # Add summary row
    avg = score_data.get("average_score")
    rubric_pass = score_data.get("rubric_pass", False)
    avg_str = f"{avg:.2f}" if avg is not None else "N/A"
    overall_status = "[green]PASS[/green]" if rubric_pass else "[red]FAIL[/red]"
    table.add_row("", "", "", "", "", end_section=True)
    table.add_row("[bold]Overall[/bold]", f"[bold]{avg_str}[/bold]", "", overall_status, "")

    console.print(table)
    console.print()


def print_faithfulness_breakdown(score_data: dict[str, Any]) -> None:
    """Print detailed faithfulness breakdown.

    Args:
        score_data: FaithfulnessScorer result dict.
    """
    if "error" in score_data:
        console.print(f"[red]Faithfulness scorer error: {score_data['error']}[/red]")
        console.print()
        return

    # Numeric phase summary
    table = Table(title="Faithfulness: Numeric Accuracy", border_style="cyan")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    num_acc = score_data.get("numeric_accuracy")
    table.add_row("Accuracy", f"{num_acc:.0%}" if num_acc is not None else "N/A")
    table.add_row("Numbers Found", str(score_data.get("numeric_total", 0)))
    table.add_row("Matched", str(score_data.get("numeric_matched", 0)))
    table.add_row("Unmatched", str(score_data.get("numeric_unmatched", 0)))
    num_pass = score_data.get("numeric_pass", False)
    table.add_row("Phase 1 Pass", "[green]YES[/green]" if num_pass else "[red]NO[/red]")
    console.print(table)

    # Unfaithful claims
    claims = score_data.get("unfaithful_claims", [])
    if claims:
        claims_table = Table(title="Unfaithful Claims", border_style="red")
        claims_table.add_column("Claim", max_width=40)
        claims_table.add_column("Severity", justify="center")
        claims_table.add_column("Reasoning", max_width=40)

        for c in claims:
            sev = c.get("severity", "?")
            sev_style = {"high": "red", "medium": "yellow", "low": "dim"}.get(sev, "")
            sev_str = f"[{sev_style}]{sev}[/{sev_style}]" if sev_style else sev
            reason = c.get("reasoning", "")
            if len(reason) > 60:
                reason = reason[:57] + "..."
            claim_text = c.get("claim", "")
            if len(claim_text) > 60:
                claim_text = claim_text[:57] + "..."
            claims_table.add_row(claim_text, sev_str, reason)

        console.print(claims_table)

    # Semantic reasoning
    reasoning = score_data.get("semantic_reasoning", "")
    if reasoning:
        console.print(f"[dim]Semantic reasoning: {reasoning}[/dim]")

    console.print()


def print_completeness_breakdown(score_data: dict[str, Any]) -> None:
    """Print detailed completeness breakdown.

    Args:
        score_data: CompletenessScorer result dict.
    """
    if "error" in score_data:
        console.print(f"[red]Completeness scorer error: {score_data['error']}[/red]")
        console.print()
        return

    # Summary
    table = Table(title="Completeness: Coverage Analysis", border_style="cyan")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    cov = score_data.get("coverage_score")
    table.add_row("Coverage Score", f"{cov:.2f}" if cov is not None else "N/A")
    table.add_row("Omitted Count", str(score_data.get("omitted_count", 0)))
    table.add_row("High Severity", str(score_data.get("omitted_high_severity", 0)))
    c_pass = score_data.get("completeness_pass", False)
    table.add_row("Pass", "[green]YES[/green]" if c_pass else "[red]NO[/red]")
    console.print(table)

    # Omitted data points
    omitted = score_data.get("omitted_data", [])
    if omitted:
        om_table = Table(title="Omitted Data Points", border_style="yellow")
        om_table.add_column("Data Point", max_width=35)
        om_table.add_column("Source Tool", style="dim")
        om_table.add_column("Severity", justify="center")
        om_table.add_column("Relevance", max_width=30)

        for o in omitted:
            sev = o.get("severity", "?")
            sev_style = {"high": "red", "medium": "yellow", "low": "dim"}.get(sev, "")
            sev_str = f"[{sev_style}]{sev}[/{sev_style}]" if sev_style else sev
            dp = o.get("data_point", "")
            if len(dp) > 50:
                dp = dp[:47] + "..."
            rel = o.get("relevance", "")
            if len(rel) > 45:
                rel = rel[:42] + "..."
            om_table.add_row(dp, o.get("source_tool", "?"), sev_str, rel)

        console.print(om_table)

    # Reasoning
    reasoning = score_data.get("reasoning", "")
    if reasoning:
        console.print(f"[dim]Reasoning: {reasoning}[/dim]")

    console.print()


def print_eval_results(results: dict[str, Any], verbose: bool = False) -> None:
    """Print evaluation results in a nice table."""
    console.rule("[bold cyan]EVALUATION RESULTS[/bold cyan]")
    console.print()

    # Summary table
    scores = results.get("scores", {})

    table = Table(title=f"Query: {results.get('query', 'N/A')[:50]}...")
    table.add_column("Scorer", style="cyan")
    table.add_column("Result")
    table.add_column("Details", style="dim")

    for scorer_name, score_data in scores.items():
        if isinstance(score_data, dict):
            # Check for pass/fail indicators
            if "correct_tools" in score_data:
                status = "[green]✓[/green]" if score_data["correct_tools"] else "[red]✗[/red]"
                details = f"missing: {score_data.get('missing_tools', [])}"
            elif "correct_sequence" in score_data:
                status = "[green]✓[/green]" if score_data["correct_sequence"] else "[red]✗[/red]"
                details = score_data.get("reason", "")
            elif "quality_pass" in score_data:
                status = "[green]✓[/green]" if score_data["quality_pass"] else "[red]✗[/red]"
                details = f"len={score_data.get('response_length', 0)}"
            elif "efficiency" in score_data:
                eff = score_data.get("efficiency", 0)
                color = "green" if eff >= 0.8 else "yellow"
                status = f"[{color}]{eff:.2f}[/{color}]"
                details = f"calls={score_data.get('total_calls', 0)}"
            elif "relevant" in score_data:
                score_val = score_data.get("score", 0)
                passed = score_data.get("relevant", False)
                status = "[green]✓[/green]" if passed else "[red]✗[/red]"
                details = f"score={score_val:.2f}"
            elif "task_completed" in score_data:
                passed = score_data.get("task_completed", False)
                status = "[green]✓[/green]" if passed else "[red]✗[/red]"
                details = score_data.get("reason", "")[:50]
            elif "tools_correct" in score_data:
                passed = score_data.get("tools_correct", False)
                status = "[green]✓[/green]" if passed else "[red]✗[/red]"
                details = score_data.get("reason", "")[:50]
            elif "rubric_pass" in score_data:
                passed = score_data.get("rubric_pass", False)
                avg = score_data.get("average_score")
                status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
                if avg is not None:
                    details = f"avg={avg:.1f}/5.0"
                else:
                    details = str(score_data.get("error", ""))[:50]
            elif "faithfulness_pass" in score_data:
                passed = score_data.get("faithfulness_pass", False)
                status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
                num_acc = score_data.get("numeric_accuracy")
                sem_score = score_data.get("semantic_score")
                parts = []
                if num_acc is not None:
                    parts.append(f"numeric={num_acc:.0%}")
                if sem_score is not None:
                    parts.append(f"semantic={sem_score:.2f}")
                details = ", ".join(parts) if parts else ""
            elif "completeness_pass" in score_data:
                passed = score_data.get("completeness_pass", False)
                status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
                cov = score_data.get("coverage_score")
                high = score_data.get("omitted_high_severity", 0)
                parts = []
                if cov is not None:
                    parts.append(f"coverage={cov:.2f}")
                if high:
                    parts.append(f"high_omissions={high}")
                details = ", ".join(parts) if parts else ""
            elif "error" in score_data:
                status = "[red]ERROR[/red]"
                details = str(score_data["error"])[:50]
            else:
                # Generic score display
                status = str(list(score_data.values())[0]) if score_data else "N/A"
                details = ""

            table.add_row(scorer_name, status, details)

    console.print(table)
    console.print()

    if verbose:
        for _scorer_name, score_data in scores.items():
            if not isinstance(score_data, dict):
                continue
            if "rubric_pass" in score_data:
                print_rubric_breakdown(score_data)
            elif "faithfulness_pass" in score_data:
                print_faithfulness_breakdown(score_data)
            elif "completeness_pass" in score_data:
                print_completeness_breakdown(score_data)

        console.print("[dim]Full results:[/dim]")
        console.print(json.dumps(scores, indent=2, default=str))


# Map scorer keys to their boolean pass field
_SCORER_PASS_KEYS: dict[str, str] = {
    "OutcomeContractScorer": "outcome_pass",
    "PartialRefusalSemanticScorer": "partial_refusal_semantic_pass",
    "DatePolicyScorer": "date_policy_pass",
    "ToolOrchestrationScorer": "correct_tools",
    "SequenceScorer": "correct_sequence",
    "ResponseQualityScorer": "quality_pass",
    "EfficiencyScorer": "within_budget",
    "StrategyContractScorer": "contract_pass",
    "StrategyGroundingScorer": "grounding_pass",
    "StrategyDecisionScorer": "strategy_decision_pass",
    "AnswerRelevanceScorer": "relevant",
    "TaskCompletionScorer": "task_completed",
    "ToolCorrectnessScorer": "tools_correct",
    "LLMJudgeScorer": "rubric_pass",
    "FaithfulnessScorer": "faithfulness_pass",
    "CompletenessScorer": "completeness_pass",
}

# Category labels for summary table
_CATEGORY_LABELS: dict[str, str] = {
    "A": "Single",
    "B": "Multi",
    "C": "Guard",
    "D": "Error",
    "E": "Session",
}


def _test_case_passed(result: dict[str, Any]) -> bool | None:
    """Determine if a test case result is pass, fail, or error.

    Args:
        result: Single test case result dict.

    Returns:
        True if passed, False if failed, None if error.
    """
    if "error" in result and "scores" not in result:
        return None

    scores = result.get("scores", {})
    if not isinstance(scores, dict) or not scores:
        return None
    required_scorers = result.get("_required_scorers")
    expected_scorers = result.get("expected_scorers")
    if (
        not isinstance(required_scorers, list)
        or not required_scorers
        or not all(isinstance(name, str) and name for name in required_scorers)
        or len(set(required_scorers)) != len(required_scorers)
        or not isinstance(expected_scorers, list)
        or not expected_scorers
        or not all(isinstance(name, str) and name for name in expected_scorers)
        or len(set(expected_scorers)) != len(expected_scorers)
        or expected_scorers != required_scorers
        or set(scores) != set(required_scorers)
        or any(name not in _SCORER_PASS_KEYS for name in required_scorers)
    ):
        return None

    outcome_score = scores.get("OutcomeContractScorer")
    if not isinstance(outcome_score, dict):
        return None
    if (
        result.get("aborted")
        or "error" in outcome_score
        or outcome_score.get("skipped") is True
        or not isinstance(outcome_score.get("outcome_pass"), bool)
    ):
        # Infrastructure and explicitly unclassifiable partial executions
        # confound every downstream product assertion.
        return None

    scorer_error = False
    for scorer_name in required_scorers:
        score_data = scores[scorer_name]
        if not isinstance(score_data, dict):
            scorer_error = True
            continue
        if "error" in score_data:
            scorer_error = True
            continue
        if score_data.get("skipped") is True:
            if not scorer_skip_is_allowed(scorer_name, score_data):
                scorer_error = True
            continue
        pass_key = _SCORER_PASS_KEYS.get(scorer_name)
        if pass_key and (pass_key not in score_data or not isinstance(score_data[pass_key], bool)):
            scorer_error = True

    product_failure = False
    for scorer_name in required_scorers:
        score_data = scores[scorer_name]
        if not isinstance(score_data, dict) or score_data.get("skipped") is True:
            continue
        pass_key = _SCORER_PASS_KEYS.get(scorer_name)
        if pass_key and score_data.get(pass_key) is False:
            product_failure = True

    # Preserve captured product evidence when an unrelated semantic scorer also
    # crashes.  If no product failure was established, an evaluator error keeps
    # the row incomplete rather than green.
    if product_failure:
        return False
    return None if scorer_error else True


def _preflight_output_path(path: Path, *, label: str) -> None:
    """Reject an unusable suite output destination before paid execution."""
    if path.exists():
        if not path.is_file():
            raise ValueError(f"{label} path is not a regular file: {path}")
        if not os.access(path, os.W_OK):
            raise ValueError(f"{label} path is not writable: {path}")
        return
    parent = path.parent
    if not parent.exists() or not parent.is_dir():
        raise ValueError(f"{label} parent directory does not exist: {parent}")
    if not os.access(parent, os.W_OK):
        raise ValueError(f"{label} parent directory is not writable: {parent}")


def _get_failure_reason(result: dict[str, Any]) -> str:
    """Extract a short failure reason from scorer results.

    Args:
        result: Single test case result dict.

    Returns:
        Human-readable failure reason string.
    """
    # Guardrail rejection failure
    if "guardrail_rejected" in result and not result["guardrail_rejected"]:
        return "guardrail should have rejected"

    scores = result.get("scores", {})
    reasons: list[str] = []

    for scorer_name, score_data in scores.items():
        if not isinstance(score_data, dict):
            continue
        pass_key = _SCORER_PASS_KEYS.get(scorer_name)
        if pass_key and pass_key in score_data and not score_data[pass_key]:
            short_name = scorer_name.replace("Scorer", "")
            detail = ""
            if "missing_tools" in score_data and score_data["missing_tools"]:
                detail = f"missing {score_data['missing_tools']}"
            elif "reason" in score_data:
                detail = str(score_data["reason"])[:40]
            reasons.append(f"{short_name}: {detail}" if detail else short_name)

    return "; ".join(reasons) if reasons else "unknown"


def _scorer_status_md(scorer_name: str, score_data: dict[str, Any]) -> tuple[str, str]:
    """Return (status_emoji, detail_string) for a scorer result in markdown.

    Args:
        scorer_name: Name of the scorer class.
        score_data: Result dict from the scorer.

    Returns:
        Tuple of (pass/fail emoji, short detail string).
    """
    if "error" in score_data:
        return "ERROR", str(score_data["error"])[:60]

    pass_key = _SCORER_PASS_KEYS.get(scorer_name)
    if pass_key and pass_key in score_data:
        passed = bool(score_data[pass_key])
        status = "PASS" if passed else "FAIL"
    else:
        status = "—"

    # Build detail string per scorer type
    if "numeric_accuracy" in score_data:
        num = score_data.get("numeric_accuracy")
        sem = score_data.get("semantic_score")
        parts = []
        if num is not None:
            parts.append(f"numeric={num:.0%}")
        if sem is not None:
            parts.append(f"semantic={sem:.2f}")
        return status, ", ".join(parts)
    if "coverage_score" in score_data:
        cov = score_data.get("coverage_score")
        high = score_data.get("omitted_high_severity", 0)
        detail = f"coverage={cov:.2f}" if cov is not None else ""
        if high:
            detail += f", high_omissions={high}"
        return status, detail
    if "average_score" in score_data:
        avg = score_data.get("average_score")
        return status, f"avg={avg:.1f}/5.0" if avg is not None else ""
    if "efficiency" in score_data:
        eff = score_data["efficiency"]
        calls = score_data.get("total_calls", 0)
        return status, f"eff={eff:.2f}, calls={calls}"
    if "score" in score_data:
        return status, f"score={score_data['score']:.2f}"
    if "response_length" in score_data:
        return status, f"len={score_data['response_length']}"
    if "contract_pass" in score_data:
        mode = score_data.get("mode", "unknown")
        reason = score_data.get("reason")
        if reason:
            return status, f"mode={mode}, {reason[:80]}"
        return status, f"mode={mode}"
    if "grounding_pass" in score_data:
        if score_data.get("reason"):
            return status, str(score_data["reason"])[:80]
        if score_data.get("artifact_embedded_verbatim"):
            return status, "verbatim"
        return status, "critical_fields_preserved"

    return status, ""


def generate_markdown_report(
    results: list[dict[str, Any]],
    test_cases: list[TestCase],
) -> str:
    """Generate a markdown evaluation report.

    Args:
        results: List of per-test-case result dicts.
        test_cases: Corresponding TestCase objects.

    Returns:
        Markdown string for the full report.
    """
    lines: list[str] = []
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # --- Header ---
    lines.append("# OBaI Evaluation Report")
    lines.append("")
    lines.append(f"**Date**: {now}  ")

    # Compute pass rate
    total = len(results)
    passed_count = sum(1 for r in results if _test_case_passed(r) is True)
    failed_count = sum(1 for r in results if _test_case_passed(r) is False)
    error_count = sum(1 for r in results if _test_case_passed(r) is None)
    rate = (passed_count / total * 100) if total else 0
    lines.append(f"**Pass Rate**: {rate:.1f}% ({passed_count}/{total})  ")
    lines.append("")

    # --- Category summary table ---
    cat_stats: dict[str, dict[str, int]] = {}
    for tc, result in zip(test_cases, results, strict=True):
        cat = tc.category or "?"
        if cat not in cat_stats:
            cat_stats[cat] = {"total": 0, "passed": 0, "failed": 0, "errors": 0}
        cat_stats[cat]["total"] += 1
        status = _test_case_passed(result)
        if status is True:
            cat_stats[cat]["passed"] += 1
        elif status is False:
            cat_stats[cat]["failed"] += 1
        else:
            cat_stats[cat]["errors"] += 1

    if len(cat_stats) > 1:
        lines.append("## Suite Summary")
        lines.append("")
        lines.append("| Category | Tests | Passed | Failed | Errors |")
        lines.append("|----------|------:|-------:|-------:|-------:|")
        for cat in sorted(cat_stats):
            s = cat_stats[cat]
            label = _CATEGORY_LABELS.get(cat, cat)
            lines.append(
                f"| {cat}: {label} | {s['total']} | {s['passed']} | {s['failed']} | {s['errors']} |"
            )
        lines.append(
            f"| **Total** | **{total}** | **{passed_count}** "
            f"| **{failed_count}** | **{error_count}** |"
        )
        lines.append("")

    # --- Per-test results ---
    lines.append("## Test Results")
    lines.append("")

    for tc, result in zip(test_cases, results, strict=True):
        tid = tc.id or "—"
        query = result.get("query", tc.query)
        status = _test_case_passed(result)

        if status is True:
            badge = "PASS"
        elif status is False:
            badge = "FAIL"
        else:
            badge = "ERROR"

        lines.append(f"### {tid}: {query}")
        lines.append("")
        lines.append(f"**Status**: {badge}  ")
        lines.append(f"**Query Type**: {tc.query_type}  ")

        # Guardrail rejection test
        if "guardrail_rejected" in result:
            rejected = result["guardrail_rejected"]
            lines.append(
                f"**Guardrail**: {'Correctly rejected' if rejected else 'Should have rejected'}  "
            )
            lines.append("")
            continue

        # Error case
        if "error" in result and "scores" not in result:
            lines.append(f"**Error**: {result['error']}  ")
            lines.append("")
            continue

        # Scorer table
        scores = result.get("scores", {})
        if scores:
            lines.append("")
            lines.append("| Scorer | Result | Details |")
            lines.append("|--------|--------|---------|")
            for scorer_name, score_data in scores.items():
                if not isinstance(score_data, dict):
                    continue
                s_status, s_detail = _scorer_status_md(scorer_name, score_data)
                short_name = scorer_name.replace("Scorer", "")
                lines.append(f"| {short_name} | {s_status} | {s_detail} |")
            lines.append("")

        # Failure reason
        if status is False:
            reason = _get_failure_reason(result)
            lines.append(f"**Failure**: {reason}  ")
            lines.append("")

    # --- Failed tests summary ---
    failed_tests = [
        (tc.id, tc.query[:50], _get_failure_reason(r))
        for tc, r in zip(test_cases, results, strict=True)
        if _test_case_passed(r) is False
    ]
    if failed_tests:
        lines.append("## Failed Tests")
        lines.append("")
        for tid, q, reason in failed_tests:
            lines.append(f'- **{tid}**: "{q}" — {reason}')
        lines.append("")

    # --- Error tests summary ---
    error_tests = [
        (tc.id, tc.query[:50], str(r.get("error", "unknown"))[:60])
        for tc, r in zip(test_cases, results, strict=True)
        if _test_case_passed(r) is None
    ]
    if error_tests:
        lines.append("## Errors")
        lines.append("")
        for tid, q, err in error_tests:
            lines.append(f'- **{tid}**: "{q}" — {err}')
        lines.append("")

    return "\n".join(lines)


def print_suite_summary(
    results: list[dict[str, Any]],
    test_cases: list[TestCase],
) -> None:
    """Print aggregate pass/fail summary table after a suite run.

    Args:
        results: List of per-test-case result dicts.
        test_cases: Corresponding TestCase objects.
    """
    console.print()
    console.rule("[bold cyan]SUITE SUMMARY[/bold cyan]")
    console.print()

    # Aggregate by category
    cat_stats: dict[str, dict[str, int]] = {}
    failed_tests: list[tuple[str, str, str]] = []  # (id, query, reason)
    error_tests: list[tuple[str, str, str]] = []

    for tc, result in zip(test_cases, results, strict=True):
        cat = tc.category or "?"
        if cat not in cat_stats:
            cat_stats[cat] = {"total": 0, "passed": 0, "failed": 0, "errors": 0}
        cat_stats[cat]["total"] += 1

        status = _test_case_passed(result)
        if status is True:
            cat_stats[cat]["passed"] += 1
        elif status is False:
            cat_stats[cat]["failed"] += 1
            reason = _get_failure_reason(result)
            failed_tests.append((tc.id, tc.query[:40], reason))
        else:
            cat_stats[cat]["errors"] += 1
            error_msg = result.get("error", "unknown error")[:40]
            error_tests.append((tc.id, tc.query[:40], str(error_msg)))

    # Summary table
    table = Table(border_style="cyan", show_lines=False)
    table.add_column("Category", style="bold")
    table.add_column("Tests", justify="right")
    table.add_column("Passed", justify="right", style="green")
    table.add_column("Failed", justify="right", style="red")
    table.add_column("Errors", justify="right", style="yellow")

    total_t = total_p = total_f = total_e = 0
    for cat in sorted(cat_stats):
        s = cat_stats[cat]
        label = _CATEGORY_LABELS.get(cat, cat)
        table.add_row(
            f"{cat}: {label}",
            str(s["total"]),
            str(s["passed"]),
            str(s["failed"]),
            str(s["errors"]),
        )
        total_t += s["total"]
        total_p += s["passed"]
        total_f += s["failed"]
        total_e += s["errors"]

    table.add_section()
    table.add_row(
        "[bold]Total[/bold]",
        f"[bold]{total_t}[/bold]",
        f"[bold]{total_p}[/bold]",
        f"[bold]{total_f}[/bold]",
        f"[bold]{total_e}[/bold]",
    )
    console.print(table)

    # Pass rate
    rate = (total_p / total_t * 100) if total_t else 0
    console.print(f"\nPass Rate: [bold]{rate:.1f}%[/bold] ({total_p}/{total_t})")

    # Failed test details
    if failed_tests:
        console.print("\n[red bold]Failed Tests:[/red bold]")
        for tid, q, reason in failed_tests:
            console.print(f'  {tid}: "{q}" — {reason}')

    if error_tests:
        console.print("\n[yellow bold]Error Tests:[/yellow bold]")
        for tid, q, err in error_tests:
            console.print(f'  {tid}: "{q}" — {err}')

    console.print()


@app.command(name="evaluate")
def evaluate_cmd(
    query_text: Annotated[
        str | None, typer.Argument(help="Single query to evaluate (or use --suite)")
    ] = None,
    suite: Annotated[bool, typer.Option("--suite", "-s", help="Run standard test suite")] = False,
    include_extended: Annotated[
        bool,
        typer.Option(
            "--include-extended",
            help="Include extended-only suite cases (additional API cost)",
        ),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Show detailed output")] = False,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Model to use")] = None,
    judge_model: Annotated[
        str | None, typer.Option("--judge", "-j", help="Model for LLM-based scorers")
    ] = None,
    no_builtin: Annotated[
        bool, typer.Option("--no-builtin", help="Skip Opik built-in scorers")
    ] = False,
    category: Annotated[
        str | None, typer.Option("--category", "-c", help="Filter by category (A/B/C/D)")
    ] = None,
    ids: Annotated[
        str | None,
        typer.Option("--ids", "-i", help="Comma-separated test IDs (for a surgical paid run)"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-l", help="Maximum selected cases (additional cost cap)"),
    ] = None,
    file: Annotated[Path | None, typer.Option("--file", "-f", help="Custom YAML test file")] = None,
    export: Annotated[
        Path | None, typer.Option("--export", "-e", help="Export results to JSON")
    ] = None,
    report: Annotated[
        Path | None, typer.Option("--report", "-r", help="Export report to markdown file")
    ] = None,
) -> None:
    """Run evaluation with Opik scorers.

    Examples:
        # Evaluate single query
        python -m evaluation evaluate "What is AAPL trading at?"

        # Run full test suite from YAML
        python -m evaluation evaluate --suite

        # Run single category
        python -m evaluation evaluate --suite --category A

        # Custom YAML file
        python -m evaluation evaluate --suite --file custom.yaml

        # Export markdown report
        python -m evaluation evaluate --suite --report report.md

        # Evaluate without LLM-based scorers (faster)
        python -m evaluation evaluate "AAPL price" --no-builtin

    Suite exit codes are 0 for all-pass, 1 for a captured contract failure,
    2 for invalid configuration, and 3 for incomplete/errored scoring.
    """
    print_banner()
    console.print()

    if not query_text and not suite:
        console.print("[red]Provide a query or use --suite[/red]")
        raise typer.Exit(1)

    if suite:
        # Suite mode: run through Opik experiment pipeline so every
        # eval run is tracked as an experiment (not just traces).
        try:
            run_test_cases = load_test_cases(
                path=file,
                category=category,
                include_extended=include_extended,
            )
        except FileNotFoundError as exc:
            # Suite mode is paid. Substituting a different built-in corpus when
            # the requested/default manifest is missing silently changes both
            # coverage and spend, so fail before starting any query.
            missing_path = file if file is not None else str(exc)
            console.print(f"[red]Test suite file not found: {missing_path}[/red]")
            raise typer.Exit(2) from exc
        except ValueError as exc:
            console.print(f"[red]Invalid test suite: {exc}[/red]")
            raise typer.Exit(2) from exc

        id_list = (
            [test_id.strip() for test_id in ids.split(",") if test_id.strip()]
            if ids is not None
            else None
        )
        if ids is not None and not id_list:
            console.print("[red]--ids must contain at least one non-empty test ID[/red]")
            raise typer.Exit(2)
        try:
            if id_list:
                requested_ids = {test_id.upper() for test_id in id_list}
                available_ids = {test_case.id.upper() for test_case in run_test_cases}
                unavailable_ids = sorted(requested_ids - available_ids)
                if unavailable_ids:
                    raise ValueError(
                        "Requested test IDs are unavailable: "
                        + ", ".join(unavailable_ids)
                        + ". Extended-only cases require --include-extended."
                    )
            run_test_cases = _filter_test_cases(
                run_test_cases,
                ids=id_list,
                limit=limit,
            )
            _validate_builtin_scorer_requirements(run_test_cases, no_builtin=no_builtin)
            _validate_semantic_scorer_credentials(run_test_cases, no_builtin=no_builtin)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc

        if not run_test_cases:
            filter_msg = f" for category '{category}'" if category else ""
            console.print(
                f"[red]No test cases found{filter_msg}. "
                "Extended-only cases require --include-extended.[/red]"
            )
            raise typer.Exit(2)

        try:
            if export is not None:
                _preflight_output_path(export, label="Export")
            if report is not None:
                _preflight_output_path(report, label="Report")
            if export is not None and report is not None and export.resolve() == report.resolve():
                raise ValueError("--export and --report must use different files")
            trusted_evaluator = OBaIEvaluator(
                use_builtin_scorers=not no_builtin,
                judge_model=judge_model,
            )
            required_scorers_by_id = {
                case.id: trusted_evaluator.expected_scorer_names(case) for case in run_test_cases
            }
        except Exception as exc:
            console.print(f"[red]Invalid evaluation configuration: {exc}[/red]")
            raise typer.Exit(2) from exc

        cat_msg = f" (category {category.upper()})" if category else ""
        n = len(run_test_cases)
        console.print(f"[cyan]Running {n} test cases{cat_msg} as experiment...[/cyan]\n")

        try:
            exp_name, results = run_evaluate_as_experiment(
                query_runner=run_query_with_trace,
                test_cases=run_test_cases,
                judge_model=judge_model,
                no_builtin=no_builtin,
            )
        except Exception as exc:
            # Selection/schema validation has already completed above.  Any
            # exception from task execution or experiment collection is an
            # incomplete evaluation, not a product failure or config typo.
            console.print(f"[yellow]Evaluation did not complete: {exc}[/yellow]")
            raise typer.Exit(3) from exc
        console.print(f"\n[dim]Experiment:[/dim] {exp_name}")

        expected_ids = [case.id for case in run_test_cases]
        observed_ids = [
            str(result.get("test_id", "")) if isinstance(result, dict) else "" for result in results
        ]
        if len(results) != len(run_test_cases) or Counter(observed_ids) != Counter(expected_ids):
            missing = sorted((Counter(expected_ids) - Counter(observed_ids)).elements())
            unexpected = sorted((Counter(observed_ids) - Counter(expected_ids)).elements())
            details = []
            if missing:
                details.append(f"missing={missing}")
            if unexpected:
                details.append(f"unexpected={unexpected}")
            console.print(
                "[yellow]Evaluation result set is incomplete or mismatched"
                + (f" ({', '.join(details)})" if details else "")
                + ".[/yellow]"
            )
            raise typer.Exit(3)
        result_by_id = {str(result["test_id"]): result for result in results}
        results = [result_by_id[test_id] for test_id in expected_ids]
        for result, test_id in zip(results, expected_ids, strict=True):
            # Derived locally from the selected case, never trusted from a
            # task result or mutable remote dataset row.
            result["_required_scorers"] = required_scorers_by_id[test_id]

    else:
        # Single query mode: run directly (no experiment needed).
        test_cases = [
            TestCase(
                query=query_text or "",
                expected_tools=[],
                query_type="ad_hoc",
                description="Ad-hoc query evaluation",
            )
        ]
        try:
            _validate_semantic_scorer_credentials(test_cases, no_builtin=no_builtin)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(2) from exc

        async def _run_single() -> tuple[list[dict[str, Any]], list[TestCase]]:
            evaluator = OBaIEvaluator(
                use_builtin_scorers=not no_builtin,
                judge_model=judge_model,
            )
            trace = await run_query_with_trace(
                query=test_cases[0].query,
                model=model,
                verbose=verbose,
            )
            result = await evaluator.evaluate_trace(trace, test_cases[0])
            result["test_id"] = ""
            result["category"] = ""
            print_eval_results(result, verbose)
            return [result], test_cases

        results, run_test_cases = asyncio.run(_run_single())

    # Suite results are a regression gate even when filtering selects one row.
    if suite:
        print_suite_summary(results, run_test_cases)
    else:
        console.rule("[bold cyan]SUMMARY[/bold cyan]")
        total = len(results)
        errors = sum(1 for r in results if "error" in r and "scores" not in r)
        console.print(f"Total: {total}, Errors: {errors}")

    try:
        if export:
            export.write_text(json.dumps(results, indent=2, default=str))
            console.print(f"[green]Results exported to {export}[/green]")

        if report:
            md = generate_markdown_report(results, run_test_cases)
            report.write_text(md)
            console.print(f"[green]Markdown report exported to {report}[/green]")
    except Exception as exc:
        console.print(f"[yellow]Evaluation output could not be written: {exc}[/yellow]")
        raise typer.Exit(3 if suite else 1) from exc

    if suite:
        statuses = [_test_case_passed(result) for result in results]
        if any(status is False for status in statuses):
            raise typer.Exit(1)
        if not statuses or any(status is None for status in statuses):
            # Distinguish an incomplete/crashed evaluator from a captured
            # product-contract failure and from configuration errors (2).
            raise typer.Exit(3)


@app.command()
def list_tests(
    category: Annotated[
        str | None, typer.Option("--category", "-c", help="Filter by category (A/B/C/D)")
    ] = None,
    include_extended: Annotated[
        bool,
        typer.Option(
            "--include-extended",
            help="Include extended-only cases",
        ),
    ] = False,
    file: Annotated[Path | None, typer.Option("--file", "-f", help="Custom YAML test file")] = None,
) -> None:
    """List available test cases.

    Loads from YAML suite file, falls back to built-in test cases.
    """
    print_banner()
    console.print()

    # Try loading from YAML, fall back to STANDARD_TEST_CASES
    try:
        test_cases = load_test_cases(
            path=file,
            category=category,
            include_extended=include_extended,
        )
        source = str(file) if file else "suite.yaml"
    except FileNotFoundError:
        test_cases = STANDARD_TEST_CASES
        source = "built-in"
    except ValueError as exc:
        console.print(f"[red]Invalid test suite: {exc}[/red]")
        raise typer.Exit(2) from exc

    cat_msg = f" (category {category.upper()})" if category else ""
    table = Table(title=f"Test Cases — {source}{cat_msg}")
    table.add_column("ID", style="dim")
    table.add_column("Cat", style="bold")
    table.add_column("Query", style="cyan")
    table.add_column("Type")
    table.add_column("Expected Tools", style="dim")
    table.add_column("Seq", style="dim")
    table.add_column("Reject", style="dim")

    for tc in test_cases:
        has_seq = "✓" if tc.expected_sequence else ""
        reject = "✓" if tc.expect_rejection else ""
        table.add_row(
            tc.id or "-",
            tc.category or "-",
            tc.query[:40] + "..." if len(tc.query) > 40 else tc.query,
            tc.query_type,
            ", ".join(tc.expected_tools)[:30] if tc.expected_tools else "-",
            has_seq,
            reject,
        )

    console.print(table)
    console.print(f"\n[dim]{len(test_cases)} test cases[/dim]")


@app.command(name="experiment")
def experiment_cmd(
    name: Annotated[
        str | None, typer.Option("--name", "-n", help="Experiment name (auto-generated if omitted)")
    ] = None,
    category: Annotated[
        str | None, typer.Option("--category", "-c", help="Filter by category (A/B/C/D)")
    ] = None,
    smoke: Annotated[
        bool, typer.Option("--smoke", help="Only run smoke-test cases (marked in suite.yaml)")
    ] = False,
    include_extended: Annotated[
        bool,
        typer.Option(
            "--include-extended",
            help="Include extended-only cases (additional API cost)",
        ),
    ] = False,
    ids: Annotated[
        str | None, typer.Option("--ids", "-i", help="Comma-separated test IDs (e.g. A1,A3,B4)")
    ] = None,
    limit: Annotated[
        int | None, typer.Option("--limit", "-l", help="Max number of test cases to run")
    ] = None,
    compare: Annotated[
        str | None,
        typer.Option(
            "--compare",
            help="Candidate models: MODEL or MODEL,SPECIALIST",
        ),
    ] = None,
    judge_model: Annotated[
        str | None, typer.Option("--judge", "-j", help="Model for LLM-based scorers")
    ] = None,
    no_builtin: Annotated[
        bool, typer.Option("--no-builtin", help="Skip LLM-based scorers (faster)")
    ] = False,
    dataset_name: Annotated[
        str, typer.Option("--dataset", help="Opik dataset name")
    ] = "obai-eval-suite",
) -> None:
    """Run Opik experiments for model comparison.

    Without --compare, runs a single experiment. With --compare, runs two
    experiments back-to-back (baseline + candidate) against the same dataset
    so you can compare them side-by-side in the Opik UI.

    Examples:
        # Compare current models vs a candidate hub model (3 test cases)
        python -m evaluation experiment --name "cand" --compare gpt-5.6-terra --limit 3

        # Compare with both orchestrator and specialist overrides
        python -m evaluation experiment --name "cand" --compare gpt-5.6-terra,gpt-5.6-luna --smoke

        # Single experiment (no comparison)
        python -m evaluation experiment --name "baseline" --limit 3

        # Smoke test comparison without LLM scorers (cheapest)
        python -m evaluation experiment --name "quick" --compare gpt-5.6-terra --smoke --no-builtin
    """
    print_banner()
    console.print()

    id_list = (
        [test_id.strip() for test_id in ids.split(",") if test_id.strip()]
        if ids is not None
        else None
    )
    if ids is not None and not id_list:
        console.print("[red]--ids must contain at least one non-empty test ID[/red]")
        raise typer.Exit(2)

    # Parse --compare: "gpt-5.6-terra" or "gpt-5.6-terra,gpt-5.6-luna"
    compare_orchestrator: str | None = None
    compare_specialist: str | None = None
    if compare:
        parts = [p.strip() for p in compare.split(",")]
        compare_orchestrator = parts[0]
        if len(parts) > 1:
            compare_specialist = parts[1]

    if compare_orchestrator:
        console.print(f"[cyan]Running comparison: baseline vs {compare}[/cyan]")
    else:
        console.print("[cyan]Starting Opik experiment...[/cyan]")

    try:
        exp_names = run_experiment(
            query_runner=run_query_with_trace,
            experiment_name=name,
            category=category,
            smoke=smoke,
            include_extended=include_extended,
            ids=id_list,
            limit=limit,
            judge_model=judge_model,
            no_builtin=no_builtin,
            dataset_name=dataset_name,
            compare_orchestrator=compare_orchestrator,
            compare_specialist=compare_specialist,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc

    console.print()
    for exp in exp_names:
        console.print(f"  [green bold]✓[/green bold] {exp}")
    console.print()
    console.print(
        f"[dim]Dataset base:[/dim] {dataset_name} [dim](selection fingerprint appended)[/dim]"
    )
    if len(exp_names) > 1:
        console.print(
            "[bold]Compare:[/bold] http://localhost:5173 → "
            "[bold]Experiments[/bold] → select both → Compare"
        )


def main() -> None:
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
