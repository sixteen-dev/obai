"""Custom Textual widgets for OBaI TUI client.

This module provides specialized widgets for displaying:
- Tool call hierarchies with status indicators
- Streaming markdown responses
- Debug log panels
- Collapsible conversation history
- Splash screen with branding
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from rich.markdown import Markdown as RichMarkdown
from rich.markup import escape as escape_markup
from rich.text import Text
from textual.containers import Container, Horizontal, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, DirectoryTree, Input, Static, TextArea

if TYPE_CHECKING:
    from rich.console import RenderableType
    from textual.app import ComposeResult
    from textual.events import Key
    from textual.strip import Strip


# ASCII art logo for OBaI
OBAI_LOGO = r"""
   ____  ____        _____
  / __ \|  _ \      |_   _|
 | |  | | |_) | __ _  | |
 | |  | |  _ < / _` | | |
 | |__| | |_) | (_| |_| |_
  \____/|____/ \__,_|_____|
"""

OBAI_TAGLINE = "Financial Research Agent"
OBAI_DESCRIPTION = """
Multi-agent system for comprehensive stock market analysis.
Ask about quotes, fundamentals, earnings, news, options, and more.
"""


class WelcomeBanner(Static):
    """Welcome banner with logo shown above input until first query.

    Similar to how Claude Code displays its logo on startup.
    """

    DEFAULT_CSS = """
    WelcomeBanner {
        width: 100%;
        height: auto;
        content-align: center middle;
        text-align: center;
        padding: 1 0;
        margin: 0 0 1 0;
    }
    WelcomeBanner.hidden {
        display: none;
    }
    """

    def render(self) -> "RenderableType":
        """Render the welcome banner."""
        logo = Text(OBAI_LOGO.strip(), style="bold cyan")
        tagline = Text(f"\n{OBAI_TAGLINE}", style="italic yellow")
        hint = Text("\n\nType your question below to get started", style="dim")
        return Text.assemble(logo, tagline, hint)

    def hide(self) -> None:
        """Hide the welcome banner."""
        self.add_class("hidden")


class SplashScreen(Container):
    """Splash screen with OBaI branding.

    Shows a large logo and description on startup,
    positioned in the main content area above the input.
    """

    DEFAULT_CSS = """
    SplashScreen {
        width: 100%;
        height: 1fr;
        align: center middle;
    }
    SplashScreen.hidden {
        display: none;
    }
    #splash-content {
        width: auto;
        height: auto;
        padding: 2 4;
    }
    #splash-logo {
        text-align: center;
        color: $primary;
        text-style: bold;
    }
    #splash-tagline {
        text-align: center;
        color: $secondary;
        text-style: italic;
        margin-top: 1;
    }
    #splash-description {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    #splash-status {
        text-align: center;
        color: $warning;
        margin-top: 2;
    }
    """

    status_text = reactive("Initializing agents...")

    def compose(self) -> "ComposeResult":
        """Compose the splash screen."""
        with Container(id="splash-content"):
            yield Static(OBAI_LOGO, id="splash-logo")
            yield Static(OBAI_TAGLINE, id="splash-tagline")
            yield Static(OBAI_DESCRIPTION.strip(), id="splash-description")
            yield Static(self.status_text, id="splash-status")

    def watch_status_text(self, new_status: str) -> None:
        """Update the status text display."""
        try:
            status_widget = self.query_one("#splash-status", Static)
            status_widget.update(new_status)
        except Exception:  # noqa: S110
            pass  # Widget may not exist during initial compose

    def set_status(self, status: str) -> None:
        """Update the loading status message.

        Args:
            status: Status message to display.
        """
        self.status_text = status

    def hide(self) -> None:
        """Hide the splash screen."""
        self.add_class("hidden")

    def show(self) -> None:
        """Show the splash screen."""
        self.remove_class("hidden")


class ProcessingIndicator(Static):
    """Animated processing indicator shown during query execution."""

    DEFAULT_CSS = """
    ProcessingIndicator {
        height: 1;
        padding: 0 1;
        color: $warning;
        text-style: italic;
        background: $surface-darken-1;
    }
    ProcessingIndicator.hidden {
        display: none;
    }
    """

    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    frame_index = reactive(0)
    message = reactive("Processing...")
    is_active = reactive(False)

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        """Initialize the processing indicator.

        Args:
            name: Optional name for the widget.
            id: Optional ID for the widget.
            classes: Optional CSS classes.
        """
        super().__init__(name=name, id=id, classes=classes)
        self.add_class("hidden")
        self._timer_handle: object | None = None

    def render(self) -> RenderableType:
        """Render the spinner with message."""
        if not self.is_active:
            return ""
        spinner = self.SPINNER_FRAMES[self.frame_index % len(self.SPINNER_FRAMES)]
        return Text.from_markup(f"[yellow]{spinner}[/] {self.message}")

    def start(self, message: str = "Processing...") -> None:
        """Start the processing animation.

        Args:
            message: Message to show next to spinner.
        """
        self.message = message
        self.is_active = True
        self.remove_class("hidden")
        self._timer_handle = self.set_interval(0.1, self._advance_frame)

    def _advance_frame(self) -> None:
        """Advance the spinner animation frame."""
        self.frame_index = (self.frame_index + 1) % len(self.SPINNER_FRAMES)

    def update_message(self, message: str) -> None:
        """Update the processing message.

        Args:
            message: New message to display.
        """
        self.message = message

    def stop(self) -> None:
        """Stop the processing animation."""
        self.is_active = False
        self.add_class("hidden")
        if self._timer_handle:
            self._timer_handle.stop()
            self._timer_handle = None


@dataclass
class ToolCallData:
    """Data for a single tool call."""

    call_id: str
    agent_name: str
    tool_name: str
    args: str
    start_time: float
    duration_ms: int | None = None
    completed: bool = False
    children: list[ToolCallData] = field(default_factory=list)


class ToolCallItem(Static):
    """Single tool call display with status indicator.

    Shows tool calls with status icons:
    - Animated spinner for pending/running
    - Checkmark (✓) for completed with timing
    """

    DEFAULT_CSS = """
    ToolCallItem {
        height: auto;
        padding: 0 1;
    }
    ToolCallItem.child {
        margin-left: 4;
    }
    """

    SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    completed = reactive(False)
    duration_ms = reactive(0)
    spinner_frame = reactive(0)

    def __init__(
        self,
        call_id: str,
        agent_name: str,
        tool_name: str,
        args: str,
        is_child: bool = False,
    ) -> None:
        """Initialize tool call item.

        Args:
            call_id: Unique identifier for this tool call.
            agent_name: Name of the agent making the call.
            tool_name: Name of the tool being called.
            args: Formatted argument string.
            is_child: Whether this is a nested MCP tool call.
        """
        super().__init__(id=f"tool-{call_id}")
        self.call_id = call_id
        self.agent_name = agent_name
        self.tool_name = tool_name
        self.args = args
        self.is_child = is_child
        self._timer_handle: object | None = None
        if is_child:
            self.add_class("child")
        # Start spinner animation
        self._start_spinner()

    def _start_spinner(self) -> None:
        """Start the spinner animation."""
        self._timer_handle = self.set_interval(0.1, self._advance_spinner)

    def _advance_spinner(self) -> None:
        """Advance spinner to next frame."""
        if not self.completed:
            self.spinner_frame = (self.spinner_frame + 1) % len(self.SPINNER_FRAMES)

    def render(self) -> RenderableType:
        """Render the tool call with current status."""
        prefix = "└─ " if self.is_child else ""

        if self.completed:
            icon = "[green]✓[/]"
            timing = f" [dim]{self.duration_ms:,}ms[/]"
        else:
            spinner = self.SPINNER_FRAMES[self.spinner_frame]
            icon = f"[yellow]{spinner}[/]"
            timing = ""

        # Escape special chars to prevent markup parsing errors
        safe_args = escape_markup(self.args) if self.args else ""
        safe_tool = escape_markup(self.tool_name)
        safe_agent = escape_markup(self.agent_name)
        args_display = f"({safe_args})" if safe_args else "()"

        if self.is_child:
            return Text.from_markup(f"{prefix}{icon} [bold]{safe_tool}[/]{args_display}{timing}")
        else:
            agent_part = f"[cyan]{safe_agent}:[/]"
            tool_part = f"[bold]{safe_tool}[/]{args_display}{timing}"
            return Text.from_markup(f"{icon} {agent_part} {tool_part}")

    def mark_complete(self, duration_ms: int) -> None:
        """Mark this tool call as completed.

        Args:
            duration_ms: How long the call took in milliseconds.
        """
        self.duration_ms = duration_ms
        self.completed = True
        # Stop spinner animation
        if self._timer_handle:
            self._timer_handle.stop()
            self._timer_handle = None


class ToolCallsWidget(Static):
    """Container for hierarchical tool call display.

    Manages a tree of tool calls with parent (specialist) and
    child (MCP) relationships.
    """

    DEFAULT_CSS = """
    ToolCallsWidget {
        height: auto;
        padding: 0;
        background: $surface;
        margin: 0 0 1 0;
    }
    """

    def __init__(self) -> None:
        """Initialize tool calls container."""
        super().__init__()
        self._tools: dict[str, ToolCallItem] = {}
        self._parent_map: dict[str, str] = {}  # child_id -> parent_id

    def add_tool(
        self,
        call_id: str,
        agent_name: str,
        tool_name: str,
        args: str,
    ) -> None:
        """Add a specialist tool call.

        Args:
            call_id: Unique identifier for this call.
            agent_name: Display name of the agent.
            tool_name: Name of the tool.
            args: Formatted argument string.
        """
        item = ToolCallItem(
            call_id=call_id,
            agent_name=agent_name,
            tool_name=tool_name,
            args=args,
            is_child=False,
        )
        self._tools[call_id] = item
        self.mount(item)

    def add_mcp_tool(
        self,
        call_id: str,
        parent_call_id: str,
        tool_name: str,
        args: str,
    ) -> None:
        """Add a nested MCP tool call.

        Args:
            call_id: Unique identifier for this call.
            parent_call_id: ID of the parent specialist call.
            tool_name: Name of the MCP tool.
            args: Formatted argument string.
        """
        # Get parent agent name for context
        parent = self._tools.get(parent_call_id)
        agent_name = parent.agent_name if parent else "Unknown"

        item = ToolCallItem(
            call_id=call_id,
            agent_name=agent_name,
            tool_name=tool_name,
            args=args,
            is_child=True,
        )
        self._tools[call_id] = item
        self._parent_map[call_id] = parent_call_id

        # Mount after parent
        if parent:
            self.mount(item, after=parent)
        else:
            self.mount(item)

    def complete_tool(self, call_id: str, duration_ms: int) -> None:
        """Mark a tool call as completed.

        Args:
            call_id: Unique identifier for the call.
            duration_ms: How long the call took.
        """
        item = self._tools.get(call_id)
        if item:
            item.mark_complete(duration_ms)

    def complete_all(self) -> None:
        """Force complete all running tool calls (cleanup on query end)."""
        for item in self._tools.values():
            if not item.completed:
                item.mark_complete(0)

    @property
    def tool_count(self) -> int:
        """Get total number of tool calls."""
        return len(self._tools)

    @property
    def specialist_count(self) -> int:
        """Get number of specialist (non-MCP) tool calls."""
        return len([t for t in self._tools.values() if not t.is_child])

    @property
    def mcp_count(self) -> int:
        """Get number of MCP tool calls."""
        return len([t for t in self._tools.values() if t.is_child])


class ResponseWidget(Static):
    """Streaming response with markdown finalization.

    Optimized for streaming: uses list buffer and throttled updates.
    After finalization: caches rendered markdown.
    """

    DEFAULT_CSS = """
    ResponseWidget {
        height: auto;
        padding: 1;
        background: $surface;
        margin: 0 0 1 0;
    }
    ResponseWidget.empty {
        display: none;
    }
    """

    def __init__(self) -> None:
        """Initialize response widget."""
        super().__init__()
        self._chunks: list[str] = []  # O(1) append buffer
        self._content: str = ""  # Joined content (cached)
        self._is_streaming = True
        self._cached_markdown: RichMarkdown | None = None
        self.add_class("empty")

    @property
    def content(self) -> str:
        """Get the full content string."""
        if not self._content and self._chunks:
            self._content = "".join(self._chunks)
        return self._content

    @content.setter
    def content(self, value: str) -> None:
        """Set content directly (used for error messages)."""
        self._chunks.clear()
        self._content = value
        self._cached_markdown = None
        if value:
            self.remove_class("empty")
        else:
            self.add_class("empty")

    def render(self) -> RenderableType:
        """Render response as text or markdown."""
        if not self._chunks and not self._content:
            return ""

        if self._is_streaming:
            # Plain text with cursor during streaming (fast)
            # Join chunks only when needed for display
            text = "".join(self._chunks) if self._chunks else self._content
            return Text(text + "\u2588")  # Block cursor
        else:
            # Use cached markdown after finalization
            if self._cached_markdown is not None:
                return self._cached_markdown
            return RichMarkdown(self.content)

    def append(self, delta: str) -> None:
        """Append streaming token and refresh.

        Args:
            delta: Text chunk to append.
        """
        if not delta:
            return

        self._chunks.append(delta)

        # Show widget on first content
        if len(self._chunks) == 1:
            self.remove_class("empty")

        # Always refresh - plain text rendering is fast
        self.refresh()

    def finalize(self) -> None:
        """Switch from streaming to markdown rendering."""
        self._is_streaming = False
        # Join chunks once and cache (only if chunks exist)
        if self._chunks:
            self._content = "".join(self._chunks)
            self._chunks.clear()  # Free memory
        # Pre-render markdown once
        self._cached_markdown = RichMarkdown(self._content)
        self.refresh()

    def set_error(self, message: str) -> None:
        """Replace content with error/rejection message and finalize.

        Unlike setting content + finalize() separately, this avoids the
        race where finalize() overwrites the content from empty chunks.

        Args:
            message: Error or rejection message (markdown).
        """
        self._chunks.clear()
        self._content = message
        self._is_streaming = False
        self._cached_markdown = RichMarkdown(message)
        if message:
            self.remove_class("empty")
        self.refresh()

    def clear(self) -> None:
        """Reset the widget."""
        self._chunks.clear()
        self._content = ""
        self._is_streaming = True
        self._cached_markdown = None
        self.add_class("empty")


class SummaryWidget(Static):
    """Query execution summary display."""

    DEFAULT_CSS = """
    SummaryWidget {
        height: auto;
        padding: 0 1;
        color: $text-muted;
        border: solid $surface-lighten-1;
    }
    SummaryWidget.empty {
        display: none;
    }
    """

    def __init__(self) -> None:
        """Initialize summary widget."""
        super().__init__()
        self._parts: list[str] = []
        self.add_class("empty")

    def _render_parts(self) -> None:
        """Render stored summary parts."""
        if self._parts:
            self.remove_class("empty")
            self.update(" \u2502 ".join(self._parts))

    def set_summary(
        self,
        duration_ms: int,
        specialists: list[str],
        tool_count: int,
        mcp_count: int,
    ) -> None:
        """Set the summary content.

        Args:
            duration_ms: Total query duration.
            specialists: List of specialist names used.
            tool_count: Number of specialist tool calls.
            mcp_count: Number of MCP tool calls.
        """
        self._parts = [f"\u23f1 {duration_ms:,}ms"]

        if specialists:
            self._parts.append(f"Specialists: {', '.join(specialists)}")

        if tool_count > 0:
            if mcp_count > 0:
                self._parts.append(f"Agents: {tool_count} ({mcp_count} MCP tools)")
            else:
                self._parts.append(f"Agents: {tool_count}")

        self._render_parts()

    def set_faithfulness(self, result: dict[str, Any]) -> None:
        """Append faithfulness evaluation result to the summary.

        Args:
            result: Dict from FaithfulnessScorer.score().
        """
        if result.get("skipped"):
            return

        numeric_acc = result.get("numeric_accuracy")
        faithful = result.get("faithfulness_pass")
        semantic_score = result.get("semantic_score")

        if faithful is not None:
            icon = "\u2713" if faithful else "\u2717"
            label = f"Faithful: {icon}"
            if semantic_score is not None:
                label += f" ({int(semantic_score * 100)}%)"
            elif numeric_acc is not None:
                label += f" (num: {int(numeric_acc * 100)}%)"
            self._parts.append(label)
        elif numeric_acc is not None:
            self._parts.append(f"Numeric acc: {int(numeric_acc * 100)}%")

        self._render_parts()

    def set_completeness(self, result: dict[str, Any]) -> None:
        """Append completeness evaluation result to the summary.

        Args:
            result: Dict from CompletenessScorer.score().
        """
        if result.get("skipped"):
            return

        coverage = result.get("coverage_score")
        passed = result.get("completeness_pass")

        if passed is not None:
            icon = "\u2713" if passed else "\u2717"
            label = f"Complete: {icon}"
            if coverage is not None:
                label += f" ({int(coverage * 100)}%)"
            self._parts.append(label)

        self._render_parts()


class QueryBlock(Collapsible):
    """Collapsible query/response block.

    Contains the full context of a single query:
    - User's question
    - Tool calls made
    - Response received
    - Execution summary
    """

    DEFAULT_CSS = """
    QueryBlock {
        height: auto;
        margin: 0 0 1 0;
    }
    QueryBlock > Contents {
        height: auto;
    }
    .user-query {
        color: $text;
        padding: 1;
        margin: 0 0 0 0;
        background: $primary-darken-3;
        border: solid $primary-darken-1;
    }
    .query-separator {
        height: 1;
        margin: 1 0;
        background: $surface-lighten-2;
    }
    .response-label {
        color: $success;
        padding: 0 1;
        text-style: bold;
        margin: 0 0 0 0;
    }
    """

    def __init__(self, query_text: str) -> None:
        """Initialize query block.

        Args:
            query_text: The user's query text.
        """
        # Truncate for collapsed title
        short_query = query_text[:50] + "..." if len(query_text) > 50 else query_text
        super().__init__(title=f"You: {short_query}", collapsed=False)
        self.query_text = query_text
        self._char_count = 0

    def compose(self) -> "ComposeResult":
        """Compose the query block contents."""
        safe_query = escape_markup(self.query_text)
        # User query with distinct background
        yield Static(
            Text.from_markup(f"[bold cyan]You:[/] {safe_query}"),
            classes="user-query",
        )
        # Separator
        yield Static("", classes="query-separator")
        # Tool calls
        yield ToolCallsWidget()
        # Response section
        yield Static("Response:", classes="response-label")
        yield ResponseWidget()
        yield SummaryWidget()

    def get_tools_widget(self) -> ToolCallsWidget:
        """Get the tool calls widget."""
        return self.query_one(ToolCallsWidget)

    def get_response_widget(self) -> ResponseWidget:
        """Get the response widget."""
        return self.query_one(ResponseWidget)

    def get_summary_widget(self) -> SummaryWidget:
        """Get the summary widget."""
        return self.query_one(SummaryWidget)

    def update_collapsed_title(self) -> None:
        """Update title with character count for collapsed view."""
        response = self.get_response_widget()
        self._char_count = len(response.content)
        short = self.query_text[:40] + "..." if len(self.query_text) > 40 else self.query_text
        self.title = f"You: {short} ({self._char_count:,} chars)"


class DebugLogEntry(Static):
    """Single debug log entry."""

    DEFAULT_CSS = """
    DebugLogEntry {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(self, timestamp: str, event_type: str, details: dict[str, str]) -> None:
        """Initialize debug log entry.

        Args:
            timestamp: Formatted timestamp string.
            event_type: Type of event.
            details: Key-value details about the event.
        """
        super().__init__()
        self.timestamp = timestamp
        self.event_type = event_type
        self.details = details

    def render(self) -> RenderableType:
        """Render the log entry."""
        text = Text()
        text.append(f"{self.timestamp} ", style="dim")
        text.append(self.event_type, style="cyan")

        for key, value in self.details.items():
            str_val = str(value)
            if key == "traceback":
                # Show full traceback, don't truncate
                text.append(f"\n  {key}:\n", style="dim red")
                text.append(str_val, style="red")
            elif len(str_val) > 120:
                text.append(f"\n  {key}=", style="dim")
                text.append(str_val[:120] + "...")
            else:
                text.append(f"\n  {key}=", style="dim")
                text.append(str_val)

        return text


class DebugPanel(VerticalScroll):
    """Scrollable debug log panel.

    Displays streaming debug events from agent execution.
    """

    DEFAULT_CSS = """
    DebugPanel {
        height: 100%;
        border-left: solid $primary;
        background: $surface;
    }
    DebugPanel.hidden {
        display: none;
    }
    """

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        """Initialize debug panel.

        Args:
            name: Optional name for the widget.
            id: Optional ID for the widget.
            classes: Optional CSS classes.
        """
        super().__init__(name=name, id=id, classes=classes)
        self.add_class("hidden")
        self._entry_count = 0
        self._max_entries = 500  # Prevent memory issues

    def add_log(self, event_type: str, **details: str) -> None:
        """Add a debug log entry.

        Args:
            event_type: Type of event being logged.
            **details: Key-value pairs of event details.
        """
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]

        entry = DebugLogEntry(ts, event_type, dict(details))
        self.mount(entry)
        self._entry_count += 1

        # Prune old entries if needed
        if self._entry_count > self._max_entries:
            self._prune_old_entries()

        self.scroll_end(animate=False)

    def _prune_old_entries(self) -> None:
        """Remove oldest entries to stay under max."""
        entries = list(self.query(DebugLogEntry))
        to_remove = len(entries) - self._max_entries // 2
        for entry in entries[:to_remove]:
            entry.remove()
        self._entry_count = len(entries) - to_remove

    def clear_logs(self) -> None:
        """Clear all debug logs."""
        for entry in self.query(DebugLogEntry):
            entry.remove()
        self._entry_count = 0

    def show(self) -> None:
        """Show the debug panel."""
        self.remove_class("hidden")

    def hide(self) -> None:
        """Hide the debug panel."""
        self.add_class("hidden")

    def toggle(self) -> bool:
        """Toggle visibility.

        Returns:
            True if now visible, False if hidden.
        """
        if self.has_class("hidden"):
            self.show()
            return True
        else:
            self.hide()
            return False


class ConversationView(VerticalScroll):
    """Scrollable conversation history with auto-scroll."""

    DEFAULT_CSS = """
    ConversationView {
        height: 100%;
        padding: 1;
    }
    """

    auto_scroll = reactive(True)

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,  # noqa: A002
        classes: str | None = None,
    ) -> None:
        """Initialize conversation view.

        Args:
            name: Optional name for the widget.
            id: Optional ID for the widget.
            classes: Optional CSS classes.
        """
        super().__init__(name=name, id=id, classes=classes)
        self._current_block: QueryBlock | None = None

    def new_query(self, text: str) -> QueryBlock:
        """Start a new query block.

        Collapses previous blocks and creates a new expanded one.

        Args:
            text: The user's query text.

        Returns:
            The new QueryBlock widget.
        """
        # Collapse previous blocks
        for block in self.query(QueryBlock):
            block.collapsed = True
            block.update_collapsed_title()

        # Create and mount new block
        block = QueryBlock(text)
        self.mount(block)
        self._current_block = block

        # Auto-scroll to new block
        if self.auto_scroll:
            self.scroll_end()

        return block

    @property
    def current_block(self) -> QueryBlock | None:
        """Get the current (most recent) query block."""
        return self._current_block

    def clear_conversation(self) -> None:
        """Remove all query blocks."""
        for block in self.query(QueryBlock):
            block.remove()
        self._current_block = None


class FilteredDirectoryTree(DirectoryTree):
    """DirectoryTree that only shows supported file types."""

    SUPPORTED_EXTENSIONS = {".csv", ".json", ".txt", ".md", ".yaml", ".yml", ".xml"}

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        """Filter to only show directories and supported file types."""
        for path in paths:
            # Always show directories
            if path.is_dir():
                # Skip hidden directories
                if not path.name.startswith("."):
                    yield path
            # Only show supported file types
            elif path.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                yield path


class FilePickerScreen(ModalScreen[Path | None]):
    """Modal file picker dialog.

    Returns the selected file path or None if cancelled.
    """

    DEFAULT_CSS = """
    FilePickerScreen {
        align: center middle;
    }
    #file-picker-container {
        width: 80%;
        height: 80%;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #file-picker-title {
        text-align: center;
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    #file-picker-hint {
        text-align: center;
        color: $text-muted;
        margin-bottom: 1;
    }
    #file-tree {
        height: 1fr;
        border: solid $surface-lighten-2;
        margin-bottom: 1;
    }
    #file-picker-path {
        height: 1;
        margin-bottom: 1;
    }
    #file-picker-buttons {
        height: 3;
        align: center middle;
    }
    #file-picker-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(self, start_path: Path | None = None) -> None:
        """Initialize file picker.

        Args:
            start_path: Starting directory. Defaults to current working directory.
        """
        super().__init__()
        self.start_path = start_path or Path.cwd()
        self._selected_path: Path | None = None

    def compose(self) -> "ComposeResult":
        """Compose the file picker dialog."""
        with Container(id="file-picker-container"):
            yield Static("📁 Select a File", id="file-picker-title")
            yield Static(
                "Supported: CSV, JSON, TXT, MD, YAML, XML",
                id="file-picker-hint",
            )
            yield FilteredDirectoryTree(self.start_path, id="file-tree")
            yield Input(
                placeholder="Selected file path...",
                id="file-picker-path",
                disabled=True,
            )
            with Horizontal(id="file-picker-buttons"):
                yield Button("Select", id="select-btn", variant="primary", disabled=True)
                yield Button("Cancel", id="cancel-btn")

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """Handle file selection in the tree."""
        self._selected_path = event.path
        # Update path display
        path_input = self.query_one("#file-picker-path", Input)
        path_input.value = str(event.path)
        # Enable select button
        self.query_one("#select-btn", Button).disabled = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "select-btn":
            self.dismiss(self._selected_path)
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_cancel(self) -> None:
        """Cancel and close the dialog."""
        self.dismiss(None)


class QueryInput(TextArea):
    """Multi-line text input for queries.

    Submits on Ctrl+Enter, regular Enter adds newlines.
    """

    class Submitted(Message):
        """Posted when the user submits the query with Ctrl+Enter."""

        def __init__(self, value: str) -> None:
            """Initialize the Submitted message.

            Args:
                value: The text content when submitted.
            """
            super().__init__()
            self.value = value

    DEFAULT_CSS = """
    QueryInput {
        height: 5;
        border: solid $primary;
        padding: 0 1;
    }
    QueryInput:focus {
        border: solid $accent;
    }
    """

    def __init__(
        self,
        placeholder: str = "",
        id: str | None = None,  # noqa: A002
    ) -> None:
        """Initialize the query input.

        Args:
            placeholder: Placeholder text shown when empty.
            id: Widget ID.
        """
        super().__init__(id=id, soft_wrap=True)
        self._placeholder = placeholder

    @property
    def placeholder(self) -> str:
        """Get the placeholder text."""
        return self._placeholder

    @placeholder.setter
    def placeholder(self, value: str) -> None:
        """Set the placeholder text."""
        self._placeholder = value
        self.refresh()

    def _on_key(self, event: "Key") -> None:
        """Handle key events - Enter submits, Shift+Enter for new line."""
        # Import here to avoid circular imports
        from textual.events import Key

        if not isinstance(event, Key):
            return

        # Enter submits (without shift)
        if event.key == "enter":
            text = self.text.strip()
            if text:
                self.post_message(self.Submitted(text))
                self.clear()
            event.prevent_default()
            event.stop()
        # Shift+Enter adds new line
        elif event.key == "shift+enter":
            self.insert("\n")
            event.prevent_default()
            event.stop()
        else:
            super()._on_key(event)

    def render_line(self, y: int) -> "Strip":
        """Render a line, showing placeholder if empty."""
        # Show placeholder when empty
        if not self.text and y == 0:
            from rich.segment import Segment
            from rich.style import Style
            from textual.strip import Strip

            placeholder_style = Style(color="grey50", italic=True)
            return Strip([Segment(self._placeholder, placeholder_style)])
        return super().render_line(y)


class HelpFooter(Static):
    """Help footer showing keyboard shortcuts below the input."""

    DEFAULT_CSS = """
    HelpFooter {
        height: 1;
        dock: bottom;
        background: $surface-darken-1;
        color: $text-muted;
        text-align: center;
        padding: 0 1;
    }
    """

    def render(self) -> "RenderableType":
        """Render the help footer with shortcuts."""
        return Text.from_markup(
            "[dim]F1[/] Debug  │  "
            "[dim]Ctrl+O[/] File  │  "
            "[dim]Ctrl+Y[/] Copy  │  "
            "[dim]Ctrl+C[/] Quit  │  "
            "[dim]Enter[/] Send"
        )


class StatusBar(Static):
    """Status bar showing configuration and connection info."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def set_config(
        self,
        orchestrator_model: str,
        specialist_model: str,
        opik_enabled: bool,
    ) -> None:
        """Set the configuration display.

        Args:
            orchestrator_model: Model used for central hub.
            specialist_model: Model used for specialists.
            opik_enabled: Whether Opik tracing is enabled.
        """
        parts = [
            f"Hub: {orchestrator_model}",
            f"Specialists: {specialist_model}",
        ]

        if opik_enabled:
            parts.append("\u2713 Opik")
        else:
            parts.append("\u2717 Opik")

        self.update(" \u2502 ".join(parts))
