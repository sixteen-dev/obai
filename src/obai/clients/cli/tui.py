#!/usr/bin/env python3
"""Textual-based TUI client for OBaI.

A modern terminal UI with:
- Collapsible conversation history
- Hierarchical tool call display
- Streaming markdown responses
- Toggle-able debug panel

Usage:
    cd src/OBaI
    uv run python -m clients.cli.tui
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
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.markup import escape as escape_markup
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Header

# Add OBaI root to path for imports
obai_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(obai_root))

import opik

from clients.cli.widgets import (
    ConversationView,
    DebugPanel,
    FilePickerScreen,
    HelpFooter,
    ProcessingIndicator,
    QueryBlock,
    QueryInput,
    SplashScreen,
    StatusBar,
    WelcomeBanner,
)
from core_agents.central_hub_agent import get_inner_tool_outputs
from core_agents.config import get_config
from evaluation.scorers.faithfulness import (
    CompletenessScorer,
    FaithfulnessScorer,
    build_scorer_input,
)

if TYPE_CHECKING:
    from agents import Session

    from core_agents.central_hub_agent import CentralHubAgent

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """Configure logging for clean TUI display.

    Suppresses noisy libraries so they don't interfere with the UI.
    Debug logging can be viewed in the debug panel instead.

    Set OBAI_DEBUG=1 to enable full DEBUG logging to stderr + a log file.
    """
    debug_mode = os.environ.get("OBAI_DEBUG", "").lower() in ("1", "true", "yes")

    if debug_mode:
        # Full logging to file only — never stderr (it corrupts the TUI).
        # Tail the log in another terminal: tail -f ~/.obai/logs/obai_debug.log
        log_dir = Path.home() / ".obai" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "obai_debug.log"
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s %(name)s %(levelname)s %(message)s",
            handlers=[logging.FileHandler(log_file, mode="w")],
        )
        logging.getLogger().info("Debug logging enabled → %s", log_file)
        # Still suppress the noisiest libraries even in debug
        for name in ("httpx", "httpcore", "urllib3"):
            logging.getLogger(name).setLevel(logging.WARNING)
        return

    # Normal mode: suppress all logging to console - use debug panel instead
    noisy_loggers = [
        "httpx",
        "httpcore",
        "mcp",
        "mcp.client",
        "mcp.client.streamable_http",
        "urllib3",
        "openai",
        "openai.agents",
        "core_agents",
        "core_agents.mcp",
        "core_agents.mcp.client",
        "core_agents.mcp.tool_converter",
        "core_agents.base_agent",
        "core_agents.central_hub_agent",
        "core_agents.config",
        "core_agents.prompt_loader",
        "core_agents.guardrails",
        "core_agents.tracing",
        "evaluation",
    ]
    for logger_name in noisy_loggers:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


class QueryTimer:
    """Track query execution timing and specialists used."""

    def __init__(self) -> None:
        """Initialize query timer."""
        self.start_time: float = 0
        self.specialists_used: list[str] = []

    def start(self) -> None:
        """Start timing a new query."""
        self.start_time = time.perf_counter()
        self.specialists_used = []

    def add_specialist(self, name: str) -> None:
        """Record that a specialist was used."""
        if name not in self.specialists_used and name != "Central Hub":
            self.specialists_used.append(name)

    def get_duration_ms(self) -> int:
        """Get elapsed time since start in milliseconds."""
        return int((time.perf_counter() - self.start_time) * 1000)


class ToolCallTracker:
    """Track tool calls for timing and parent relationships."""

    def __init__(self) -> None:
        """Initialize tracker."""
        self._start_times: dict[str, float] = {}
        self._specialist_ids: dict[str, str] = {}  # specialist_name -> call_id
        self._current_specialist_id: str | None = None

    def clear(self) -> None:
        """Clear tracked state for new query."""
        self._start_times.clear()
        self._specialist_ids.clear()
        self._current_specialist_id = None

    def start_specialist_call(self, call_id: str, specialist_name: str) -> None:
        """Record a specialist tool call starting."""
        self._start_times[call_id] = time.perf_counter()
        self._specialist_ids[specialist_name] = call_id
        self._current_specialist_id = call_id

    def start_mcp_call(self, call_id: str) -> None:
        """Record an MCP tool call starting."""
        self._start_times[call_id] = time.perf_counter()

    def complete_call(self, call_id: str) -> int | None:
        """Complete a call and return duration in ms."""
        if call_id not in self._start_times:
            return None
        duration_ms = int((time.perf_counter() - self._start_times[call_id]) * 1000)
        del self._start_times[call_id]
        return duration_ms

    def get_specialist_id(self, specialist_name: str) -> str | None:
        """Get the call ID for a specialist by name."""
        return self._specialist_ids.get(specialist_name)

    @property
    def current_specialist_id(self) -> str | None:
        """Get the current specialist call ID for MCP nesting."""
        return self._current_specialist_id


class OBaIApp(App[None]):
    """OBaI Terminal User Interface.

    A Textual-based chat interface for the OBaI financial research assistant.
    """

    TITLE = "OBaI - Financial Research Agent"

    CSS = """
    #main-container {
        height: 1fr;
    }
    #main-container.hidden {
        display: none;
    }
    #conversation {
        width: 2fr;
    }
    #debug-panel {
        width: 1fr;
        min-width: 40;
    }
    #status-bar {
        dock: top;
        height: 1;
    }
    #status-bar.hidden {
        display: none;
    }
    #processing-indicator {
        dock: bottom;
        height: 1;
    }
    #processing-indicator.hidden {
        display: none;
    }
    #query-input {
        dock: bottom;
        height: 5;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("f1", "toggle_debug", "Debug", show=True),
        Binding("ctrl+o", "open_file", "File", show=True),
        Binding("ctrl+y", "copy_response", "Copy", show=True),
        Binding("ctrl+l", "clear_conversation", "Clear", show=False),
        Binding("ctrl+c", "quit", "Quit", show=True),
        Binding("escape", "cancel_query", "Cancel", show=False),
    ]

    def __init__(self) -> None:
        """Initialize the OBaI TUI app."""
        super().__init__()
        self.hub: CentralHubAgent | None = None
        self.session: Session | None = None
        self.query_timer = QueryTimer()
        self.tool_tracker = ToolCallTracker()
        self._is_processing = False
        self._current_block: QueryBlock | None = None
        self._first_query = True  # Track if welcome banner should show
        self._attached_file: tuple[str, str] | None = None  # (filename, content)

        # Check if debug should be visible by default
        self._show_debug = os.environ.get("OBAI_DEBUG", "").lower() in ("1", "true", "yes")

    def compose(self) -> ComposeResult:
        """Compose the app layout - minimal for fast startup."""
        yield Header()
        # Status bar (hidden until loaded)
        yield StatusBar(id="status-bar", classes="hidden")
        # Splash takes up main area during loading
        yield SplashScreen(id="splash-screen")
        # Welcome banner (hidden until splash done, shows until first query)
        yield WelcomeBanner(id="welcome-banner", classes="hidden")
        # Processing indicator (hidden until query)
        yield ProcessingIndicator(id="processing-indicator", classes="hidden")
        # Multi-line input at bottom, help footer below it
        yield QueryInput(
            placeholder="⏳ Starting...",
            id="query-input",
        )
        yield HelpFooter(id="help-footer")

    async def on_mount(self) -> None:
        """Initialize app after mounting."""
        # Focus input so user can type while loading
        self.query_one("#query-input", QueryInput).focus()

        # Initialize the hub in the background (splash screen visible)
        self.initialize_hub()

    def _set_loading_status(self, status: str) -> None:
        """Update both splash and input placeholder with loading status."""
        self.query_one(SplashScreen).set_status(status)
        self.query_one("#query-input", QueryInput).placeholder = f"⏳ {status}"

    @work(exclusive=True)
    async def initialize_hub(self) -> None:
        """Initialize the central hub agent."""
        try:
            self._set_loading_status("Loading modules...")

            # Heavy imports - do them in a thread to not block UI
            def do_imports() -> tuple[Any, ...]:
                from agents import SQLiteSession

                from core_agents.central_hub_agent import (
                    create_central_hub,
                    set_mcp_tool_callback,
                )
                from core_agents.config import get_config
                from core_agents.tracing import init_opik, is_opik_enabled

                return (
                    create_central_hub,
                    set_mcp_tool_callback,
                    get_config,
                    SQLiteSession,
                    init_opik,
                    is_opik_enabled,
                )

            imports = await asyncio.to_thread(do_imports)
            (
                create_central_hub,
                set_mcp_tool_callback,
                get_config,
                SQLiteSession,
                init_opik,
                is_opik_enabled,
            ) = imports

            self._set_loading_status("Loading configuration...")
            config = get_config()

            # Initialize Opik tracing if enabled
            self._set_loading_status("Initializing tracing...")
            init_opik()

            # Update status bar with config
            status_bar = self.query_one(StatusBar)
            status_bar.set_config(
                orchestrator_model=config.orchestrator_model,
                specialist_model=config.specialist_model,
                opik_enabled=is_opik_enabled(),
            )

            self._set_loading_status("Connecting to MCP servers...")
            self.log_debug("Initializing", status="Starting central hub...")

            self._set_loading_status("Initializing specialist agents...")
            self.hub = await create_central_hub()

            # Unique session ID per TUI launch — starts empty, accumulates
            # conversation history within the session, dies with the process.
            # In-memory SQLite: no external deps, no cross-launch leakage.
            session_id = f"tui_{uuid.uuid4().hex[:8]}"
            self.session = SQLiteSession(session_id)
            self.log_debug("Session", id=session_id)

            # Set up MCP tool callback for nested tool display
            set_mcp_tool_callback(self._handle_mcp_tool_event)

            self._set_loading_status("Ready!")
            self.log_debug("Initialized", status="All agents ready")

            # Transition to main UI immediately
            await self._show_main_ui()

        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            error_msg = escape_markup(str(e))
            logger.error("Hub initialization failed:\n%s", tb)
            self.log_debug("InitError", error=str(e), traceback=tb)
            self._set_loading_status(f"Error: {error_msg[:80]}")
            self.notify(
                f"Failed to initialize: {error_msg}\n\nRun with OBAI_DEBUG=1 for full traceback.",
                severity="error",
                timeout=15,
            )

    async def _show_main_ui(self) -> None:
        """Transition from splash screen to main UI."""
        # Hide splash, show status bar and welcome banner
        self.query_one(SplashScreen).hide()
        self.query_one(StatusBar).remove_class("hidden")
        self.query_one(WelcomeBanner).remove_class("hidden")

        # Mount the main container (heavy widgets deferred for fast startup)
        splash = self.query_one(SplashScreen)
        main_container = Horizontal(id="main-container")
        await self.mount(main_container, after=splash)

        # Add children to main container
        conversation = ConversationView(id="conversation")
        debug_panel = DebugPanel(id="debug-panel")
        await main_container.mount(conversation)
        await main_container.mount(debug_panel)

        # Show debug if env var set
        if self._show_debug:
            debug_panel.show()

        # Update input placeholder and focus
        query_input = self.query_one("#query-input", QueryInput)
        query_input.placeholder = "Ask about stocks... (Enter to send)"
        query_input.focus()

    def _handle_mcp_tool_event(
        self,
        event_type: str,
        specialist_name: str,
        tool_name: str,
        args: str,
        call_id: str,
        duration_ms: int | None = None,
    ) -> None:
        """Handle MCP tool events from specialist agents."""
        if not self._current_block:
            return

        tools = self._current_block.get_tools_widget()
        # Look up parent by specialist name, not current_specialist_id
        parent_id = self.tool_tracker.get_specialist_id(specialist_name)

        if event_type == "start":
            self.tool_tracker.start_mcp_call(call_id)
            if parent_id:
                tools.add_mcp_tool(call_id, parent_id, tool_name, args)
            else:
                # No parent found - add as top-level with specialist name
                tools.add_tool(call_id, specialist_name, tool_name, args)
            self.log_debug("MCPToolStart", tool=tool_name, agent=specialist_name)

        elif event_type == "complete":
            actual_duration = self.tool_tracker.complete_call(call_id)
            tools.complete_tool(call_id, actual_duration or duration_ms or 0)
            self.log_debug(
                "MCPToolComplete",
                tool=tool_name,
                duration=f"{actual_duration or duration_ms}ms",
            )

    def log_debug(self, event_type: str, **details: str) -> None:
        """Log to the debug panel.

        Args:
            event_type: Type of event.
            **details: Key-value event details.
        """
        with contextlib.suppress(Exception):
            self.query_one(DebugPanel).add_log(event_type, **details)

    def action_toggle_debug(self) -> None:
        """Toggle the debug panel visibility."""
        try:
            panel = self.query_one(DebugPanel)
            self._show_debug = panel.toggle()
        except Exception:  # noqa: S110, BLE001
            pass  # Panel not mounted yet

    def action_clear_conversation(self) -> None:
        """Clear the conversation and debug logs."""
        with contextlib.suppress(Exception):
            self.query_one(ConversationView).clear_conversation()
            self.query_one(DebugPanel).clear_logs()

        # Also clear the session
        if self.session:
            asyncio.create_task(self._clear_session())

    async def _clear_session(self) -> None:
        """Clear the session and cache, create a fresh session."""
        if self.session:
            await self.session.clear_session()
        if self.hub:
            await self.hub.clear_cache()

        from agents import SQLiteSession

        session_id = f"tui_{uuid.uuid4().hex[:8]}"
        self.session = SQLiteSession(session_id)
        self.log_debug("SessionCleared", new_id=session_id)
        self.notify("Conversation cleared", severity="information")

    def action_cancel_query(self) -> None:
        """Cancel the current query (placeholder - full implementation needs work)."""
        if self._is_processing:
            self.notify("Query cancellation not yet implemented", severity="warning")

    def action_copy_response(self) -> None:
        """Copy the last response to clipboard."""
        import subprocess

        try:
            conv = self.query_one(ConversationView)
            if not conv.current_block:
                self.notify("No response to copy", severity="warning")
                return

            response_widget = conv.current_block.get_response_widget()
            content = response_widget.content

            if not content:
                self.notify("Response is empty", severity="warning")
                return

            # Try xclip first, then xsel (common Linux clipboard tools)
            copied = False
            for cmd in [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]:
                try:
                    process = subprocess.Popen(  # noqa: S603
                        cmd,
                        stdin=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                    )
                    process.communicate(input=content.encode("utf-8"))
                    if process.returncode == 0:
                        copied = True
                        break
                except FileNotFoundError:
                    continue

            if copied:
                char_count = len(content)
                self.notify(f"Copied {char_count:,} chars to clipboard", severity="information")
            else:
                self.notify("Install xclip or xsel for clipboard support", severity="error")

        except Exception as e:
            self.notify(f"Copy failed: {e}", severity="error")

    def action_open_file(self) -> None:
        """Open file picker to attach a file to the query."""
        if self._is_processing:
            self.notify("Cannot attach file while processing", severity="warning")
            return

        def handle_file_selected(path: "Path | None") -> None:
            """Handle the selected file path."""
            if path is None:
                return  # User cancelled

            try:
                # Read file content
                content = path.read_text(encoding="utf-8")
                file_size = len(content)

                # Truncate if too large (>50KB)
                max_size = 50_000
                if file_size > max_size:
                    content = content[:max_size]
                    content += f"\n... [Truncated to {max_size:,} of {file_size:,} chars]"
                    self.notify(f"File truncated to {max_size:,} chars", severity="warning")

                # Store file content separately (not in input - causes UI glitches)
                file_ext = path.suffix.lower()
                formatted = f"--- File: {path.name} ---\n```{file_ext[1:]}\n{content}\n```"
                self._attached_file = (path.name, formatted)

                # Update input placeholder to show attached file
                query_input = self.query_one("#query-input", QueryInput)
                query_input.placeholder = f"📎 {path.name} attached - type your question..."
                query_input.focus()

                self.notify(f"Attached: {path.name}", severity="information")
                self.log_debug("FileAttached", file=path.name, size=str(file_size))

            except UnicodeDecodeError:
                self.notify("Cannot read binary file", severity="error")
            except OSError as e:
                self.notify(f"Error reading file: {e}", severity="error")

        # Push the file picker modal - start from home directory
        self.push_screen(FilePickerScreen(Path.home()), handle_file_selected)

    def on_query_input_submitted(self, event: QueryInput.Submitted) -> None:
        """Handle query submission from multi-line input."""
        text = event.value.strip()
        if not text:
            return

        # Reset placeholder
        query_input = self.query_one("#query-input", QueryInput)
        query_input.placeholder = "Ask about stocks... (Enter to send)"

        # Handle commands
        if text.lower() in ("quit", "exit"):
            self.exit()
            return

        if text.lower() == "clear":
            self._attached_file = None
            self.action_clear_conversation()
            return

        # Check if hub is ready
        if not self.hub or not self.session:
            self.notify("Still initializing, please wait...", severity="warning")
            return

        # Combine query with attached file content
        if self._attached_file:
            _, file_content = self._attached_file
            text = f"{text}\n\n{file_content}"
            self._attached_file = None  # Clear after use

        # Run the query
        self.run_query(text)

    @work(exclusive=True)
    async def run_query(self, text: str) -> None:
        """Execute a query and stream results.

        Args:
            text: The user's query text.
        """
        # Import event types
        from agents.items import ItemHelpers, MessageOutputItem
        from agents.stream_events import (
            AgentUpdatedStreamEvent,
            RawResponsesStreamEvent,
            RunItemStreamEvent,
        )
        from openai.types.responses import ResponseTextDeltaEvent

        from core_agents.guardrails import get_rejection_message

        self._is_processing = True
        self.query_timer.start()
        self.tool_tracker.clear()

        # Hide welcome banner on first query
        if self._first_query:
            self.query_one(WelcomeBanner).hide()
            self._first_query = False

        # Show processing indicator
        processing = self.query_one(ProcessingIndicator)
        processing.start("Analyzing query...")

        # Create new query block
        conv = self.query_one(ConversationView)
        block = conv.new_query(text)
        self._current_block = block

        tools = block.get_tools_widget()
        response = block.get_response_widget()
        summary = block.get_summary_widget()

        self.log_debug("QueryStart", query=text[:50])

        # Specialist tool name mapping
        specialist_tools = {
            "market_data_analysis": "Market Data Agent",
            "fundamentals_analysis": "Fundamentals Agent",
            "events_news_analysis": "Events & News Agent",
            "options_analysis": "Options Agent",
            "screener_lookup": "Screener Agent",
            "portfolio_analysis": "Portfolio Agent",
            "strategy_analysis": "Strategy Agent",
            "research_analysis": "Research Agent",
        }

        # Track state for hub activity display
        current_agent = "Central Hub"
        hub_shown_analyzing = False
        hub_shown_synthesizing = False
        # Capture specialist-level outputs as fallback for scoring
        outer_tool_outputs: list[dict[str, Any]] = []

        try:
            response_text = ""

            # Show initial hub activity
            tools.add_tool("hub_analyze", "Central Hub", "analyze", f"Query: {text[:40]}...")
            hub_shown_analyzing = True

            async for event in self.hub.run(text, self.session):  # type: ignore[union-attr]
                # Log all events to debug panel
                self._log_stream_event(event)

                # Handle agent changes
                if isinstance(event, AgentUpdatedStreamEvent):
                    agent_name = event.new_agent.name
                    display_name = agent_name.replace("obai_", "").replace("_", " ").title()

                    # Mark hub analysis complete when delegating
                    if hub_shown_analyzing and display_name != "Central Hub":
                        tools.complete_tool("hub_analyze", 0)
                        hub_shown_analyzing = False

                    # Track when returning to hub for synthesis
                    is_returning_to_hub = (
                        "Central Hub" in display_name
                        and current_agent != "Central Hub"
                        and not hub_shown_synthesizing
                    )
                    if is_returning_to_hub:
                        tools.add_tool(
                            "hub_synth", "Central Hub", "synthesize", "Generating report..."
                        )
                        hub_shown_synthesizing = True

                    current_agent = display_name
                    self.query_timer.add_specialist(display_name)
                    processing.update_message(f"{display_name} working...")

                # Handle run items
                elif isinstance(event, RunItemStreamEvent):
                    item = event.item
                    item_type = getattr(item, "type", None)

                    # Tool call start
                    if item_type == "tool_call_item":
                        raw_item = getattr(item, "raw_item", None)
                        if raw_item:
                            tool_name = getattr(raw_item, "name", "unknown")
                            call_id = getattr(raw_item, "call_id", None)

                            # Get display name for specialists
                            if tool_name in specialist_tools:
                                display_name = specialist_tools[tool_name]
                                self.query_timer.add_specialist(display_name)
                                # Mark hub analysis complete when calling specialist
                                if hub_shown_analyzing:
                                    tools.complete_tool("hub_analyze", 0)
                                    hub_shown_analyzing = False
                            else:
                                display_name = current_agent

                            # Parse args
                            raw_args = getattr(raw_item, "arguments", "{}")
                            args_str = self._format_args(raw_args, tool_name, specialist_tools)

                            if call_id:
                                self.tool_tracker.start_specialist_call(call_id, display_name)
                                tools.add_tool(call_id, display_name, tool_name, args_str)

                    # Tool call output - mark tool as complete
                    elif item_type == "tool_call_output_item":
                        raw_item = getattr(item, "raw_item", None)
                        if raw_item:
                            # raw_item is a TypedDict (dict), not a Pydantic model,
                            # so use dict .get() instead of getattr.
                            call_id = (
                                raw_item.get("call_id")
                                if isinstance(raw_item, dict)
                                else getattr(raw_item, "call_id", None)
                            )
                            if call_id:
                                dur = self.tool_tracker.complete_call(call_id)
                                tools.complete_tool(call_id, dur if dur is not None else 0)

                                # Capture specialist output for scoring fallback
                                output = getattr(item, "output", None)
                                if output:
                                    outer_tool_outputs.append(
                                        {
                                            "specialist": current_agent,
                                            "tool_name": "specialist_response",
                                            "output": output,
                                        }
                                    )

                                # Yield to event loop for UI updates
                                await asyncio.sleep(0)

                    # Message output (non-streaming)
                    elif item_type == "message_output_item":
                        if isinstance(item, MessageOutputItem):
                            msg_text = ItemHelpers.text_message_output(item)
                            if msg_text and not response_text:
                                response_text = msg_text

                # Handle streaming text
                elif isinstance(event, RawResponsesStreamEvent):
                    data = event.data
                    if isinstance(data, ResponseTextDeltaEvent):
                        delta = data.delta
                        if delta:
                            # Update status on first token
                            if not response_text:
                                processing.update_message("Generating response...")
                            response.append(delta)
                            response_text += delta
                            # Yield to event loop for UI updates
                            await asyncio.sleep(0)
                            conv.scroll_end(animate=False)

            # Stop processing indicator and finalize
            processing.stop()
            response.finalize()
            conv.scroll_end(animate=False)

            # Now complete the hub synthesize step (output is done)
            if hub_shown_synthesizing:
                tools.complete_tool("hub_synth", self.query_timer.get_duration_ms())

            # Safety: complete any tools that didn't get completion events
            tools.complete_all()

            # Set summary
            summary.set_summary(
                duration_ms=self.query_timer.get_duration_ms(),
                specialists=self.query_timer.specialists_used,
                tool_count=tools.specialist_count,
                mcp_count=tools.mcp_count,
            )

            self.log_debug(
                "QueryComplete",
                duration=f"{self.query_timer.get_duration_ms()}ms",
                chars=str(len(response_text)),
            )

            # Kick off faithfulness scoring in background (if enabled)
            if get_config().enable_inline_scoring:
                # Prefer inner MCP outputs (raw JSON); fall back to outer
                # specialist outputs (formatted text) when inner is empty.
                inner_outputs = get_inner_tool_outputs()
                scoring_outputs = inner_outputs or outer_tool_outputs
                logger.info(
                    "Faithfulness check: inner=%d outer=%d has_response=%s",
                    len(inner_outputs),
                    len(outer_tool_outputs),
                    bool(response_text),
                )
                self.log_debug(
                    "FaithfulnessCheck",
                    inner_count=str(len(inner_outputs)),
                    outer_count=str(len(outer_tool_outputs)),
                    has_response=str(bool(response_text)),
                )
                if scoring_outputs and response_text:
                    self._score_faithfulness(text, response_text, scoring_outputs, block)
                else:
                    logger.info("Faithfulness skipped: no tool outputs or empty response")

        except Exception as e:
            error_name = type(e).__name__
            tools.complete_all()  # Stop all spinners on error

            # Check for guardrail rejection
            if "InputGuardrailTripwireTriggered" in error_name:
                # Extract validation from: e.guardrail_result.output.output_info
                guardrail_result = getattr(e, "guardrail_result", None)
                output = getattr(guardrail_result, "output", None)
                validation_info = getattr(output, "output_info", None)
                if validation_info:
                    rejection_msg = get_rejection_message(validation_info)
                else:
                    rejection_msg = (
                        "Sorry, I can only help with stock market research and financial analysis."
                    )
                response.set_error(f"**Query Rejected**\n\n{rejection_msg}")
                self.log_debug("GuardrailRejection", reason=rejection_msg[:50])
            else:
                # Generic error — show full error + traceback in debug
                import traceback

                tb = traceback.format_exc()
                response.set_error(f"**Error**\n\n`{error_name}`: {e}")
                logger.error("Query failed:\n%s", tb)
                self.log_debug("QueryError", error=str(e), traceback=tb)
                self.notify(f"Error: {escape_markup(str(e))}", severity="error")

        finally:
            self._is_processing = False
            self._current_block = None
            processing.stop()
            # Ensure all tool spinners are stopped
            with contextlib.suppress(Exception):
                tools.complete_all()

    def _format_args(
        self,
        raw_args: str,
        tool_name: str,
        specialist_tools: dict[str, str],
    ) -> str:
        """Format tool arguments for display."""
        try:
            args = json.loads(raw_args) if raw_args else {}

            # For specialist tools, show the query
            if tool_name in specialist_tools:
                input_text = str(args.get("input", ""))
                if len(input_text) > 50:
                    return input_text[:50] + "..."
                return input_text

            # For other tools, show key=value pairs
            pairs = [f"{k}={v}" for k, v in list(args.items())[:3]]
            result = ", ".join(pairs)
            if len(args) > 3:
                result += ", ..."
            return str(result)

        except (json.JSONDecodeError, TypeError):
            return ""

    def _log_stream_event(self, event: Any) -> None:
        """Log a stream event to debug panel."""
        from agents.stream_events import (
            AgentUpdatedStreamEvent,
            RawResponsesStreamEvent,
            RunItemStreamEvent,
        )

        if isinstance(event, AgentUpdatedStreamEvent):
            self.log_debug("AgentUpdated", agent=event.new_agent.name)

        elif isinstance(event, RunItemStreamEvent):
            item = event.item
            item_type = getattr(item, "type", "?")
            raw_item = getattr(item, "raw_item", None)
            name = getattr(raw_item, "name", "") if raw_item else ""
            self.log_debug("RunItem", type=item_type, name=name)

        elif isinstance(event, RawResponsesStreamEvent):
            data = event.data
            # Only log some deltas to avoid spam
            if hasattr(data, "delta") and data.delta and len(data.delta) > 5:
                self.log_debug("StreamDelta", chars=str(len(data.delta)))

    @work(exclusive=False)
    async def _score_faithfulness(
        self,
        query: str,
        response_text: str,
        inner_outputs: list[str | Any],
        block: QueryBlock,
    ) -> None:
        """Run faithfulness scoring in background after query completes.

        Mirrors the eval CLI: calls FaithfulnessScorer.score() directly
        and lets the @opik.track() decorators inside the scorer handle
        tracing to Opik (global opik config set by init_opik at startup).

        Args:
            query: Original user query.
            response_text: Final response text.
            inner_outputs: Snapshot of inner MCP tool outputs.
            block: QueryBlock to update with results.
        """
        logger.info("Faithfulness scoring started for: %s", query[:40])
        scorer_input = self._build_scorer_input(response_text, inner_outputs)
        if not scorer_input:
            logger.info("Faithfulness skipped: no scorer input built")
            return

        summary = block.get_summary_widget()

        try:
            logger.info("Running FaithfulnessScorer.score()...")
            scorer = FaithfulnessScorer(skip_llm=True)
            result = await scorer.score(output=scorer_input, query=query)
            logger.info("Faithfulness result: %s", result)
            summary.set_faithfulness(result)

            num_acc = result.get("numeric_accuracy")
            self.log_debug(
                "Faithfulness",
                numeric=f"{num_acc:.0%}" if num_acc is not None else "n/a",
                pass_=str(result.get("faithfulness_pass")),
                skipped=str(result.get("skipped", False)),
            )
        except Exception:
            logger.exception("Faithfulness scoring failed")

        try:
            logger.info("Running CompletenessScorer.score()...")
            completeness_scorer = CompletenessScorer()
            completeness_result = await completeness_scorer.score(
                output=scorer_input,
                query=query,
            )
            logger.info("Completeness result: %s", completeness_result)
            summary.set_completeness(completeness_result)

            coverage = completeness_result.get("coverage_score")
            self.log_debug(
                "Completeness",
                coverage=f"{coverage:.0%}" if coverage is not None else "n/a",
                pass_=str(completeness_result.get("completeness_pass")),
                skipped=str(completeness_result.get("skipped", False)),
            )
        except Exception:
            logger.exception("Completeness scoring failed")

        opik.flush_tracker()

    @staticmethod
    def _build_scorer_input(
        response_text: str,
        inner_outputs: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Build faithfulness scorer input from captured tool outputs."""
        return build_scorer_input(response_text, inner_outputs)

    async def action_quit(self) -> None:
        """Clean up and quit."""
        if self.hub:
            try:
                await self.hub.close()
            except Exception as e:
                logger.warning(f"Error closing hub: {e}")

        self.exit()


def main() -> None:
    """Entry point for the OBaI TUI."""
    # Suppress warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", message=".*sentry_sdk.Hub.*is deprecated.*")

    configure_logging()

    app = OBaIApp()
    app.run()


if __name__ == "__main__":
    main()
