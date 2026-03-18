"""Typed events for agent execution visibility.

These events provide structured information about agent execution
that can be rendered by CLI, Discord, or other clients.

Unlike trace events (testing/trace/types.py) which are for post-hoc analysis,
these events are designed for real-time streaming to clients.

Usage:
    ```python
    from core_agents.events import SpecialistStartEvent, AgentEvent

    # Type checking for event handlers
    def handle_event(event: AgentEvent) -> None:
        if isinstance(event, SpecialistStartEvent):
            print(f"Starting {event.specialist_name}...")
    ```
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class SpecialistStartEvent(BaseModel):
    """A specialist agent started working.

    Emitted when the central hub hands off to a specialist agent.

    Attributes:
        type: Event type discriminator.
        specialist_name: Name of the specialist (e.g., "market_data", "fundamentals").
        query: The query being processed by the specialist.
        timestamp: When the specialist started.
    """

    type: Literal["specialist_start"] = "specialist_start"
    specialist_name: str
    query: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class SpecialistEndEvent(BaseModel):
    """A specialist agent completed its work.

    Emitted when a specialist returns control to the hub.

    Attributes:
        type: Event type discriminator.
        specialist_name: Name of the specialist.
        duration_ms: Time taken in milliseconds.
        success: Whether the specialist completed successfully.
        timestamp: When the specialist finished.
    """

    type: Literal["specialist_end"] = "specialist_end"
    specialist_name: str
    duration_ms: int = 0
    success: bool = True
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class MCPToolCallEvent(BaseModel):
    """An MCP tool was called by a specialist.

    Emitted when a specialist starts calling an MCP server tool.

    Attributes:
        type: Event type discriminator.
        specialist_name: Which specialist made the call.
        tool_name: Name of the MCP tool being called.
        args: Arguments passed to the tool.
        timestamp: When the call started.
    """

    type: Literal["mcp_tool_call"] = "mcp_tool_call"
    specialist_name: str
    tool_name: str
    args: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class MCPToolResultEvent(BaseModel):
    """An MCP tool returned a result.

    Emitted when an MCP tool call completes.

    Attributes:
        type: Event type discriminator.
        specialist_name: Which specialist made the call.
        tool_name: Name of the MCP tool that completed.
        duration_ms: How long the call took in milliseconds.
        summary: Brief summary of the result (e.g., "Received 5 items").
        success: Whether the call succeeded.
        error: Error message if the call failed.
        timestamp: When the call completed.
    """

    type: Literal["mcp_tool_result"] = "mcp_tool_result"
    specialist_name: str
    tool_name: str
    duration_ms: int = 0
    summary: str = ""
    success: bool = True
    error: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class AnswerStreamEvent(BaseModel):
    """A chunk of the final answer is available.

    Emitted during streaming response generation.

    Attributes:
        type: Event type discriminator.
        text: The text chunk.
        timestamp: When this chunk was received.
    """

    type: Literal["answer_chunk"] = "answer_chunk"
    text: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class QueryCompleteEvent(BaseModel):
    """Query processing completed.

    Emitted when the entire query has been processed.

    Attributes:
        type: Event type discriminator.
        total_duration_ms: Total time from query start to completion.
        specialists_used: List of specialists that were invoked.
        success: Whether the query completed successfully.
        error: Error message if the query failed.
        timestamp: When processing completed.
    """

    type: Literal["query_complete"] = "query_complete"
    total_duration_ms: int = 0
    specialists_used: list[str] = Field(default_factory=list)
    success: bool = True
    error: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class ThinkingEvent(BaseModel):
    """The agent is thinking/planning.

    Emitted when the hub is analyzing the query before calling specialists.

    Attributes:
        type: Event type discriminator.
        message: Description of what the agent is thinking about.
        timestamp: When this thinking step occurred.
    """

    type: Literal["thinking"] = "thinking"
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


# Union type for all agent events (for type checking)
AgentEvent = (
    SpecialistStartEvent
    | SpecialistEndEvent
    | MCPToolCallEvent
    | MCPToolResultEvent
    | AnswerStreamEvent
    | QueryCompleteEvent
    | ThinkingEvent
)

__all__ = [
    "AgentEvent",
    "AnswerStreamEvent",
    "MCPToolCallEvent",
    "MCPToolResultEvent",
    "QueryCompleteEvent",
    "SpecialistEndEvent",
    "SpecialistStartEvent",
    "ThinkingEvent",
]
