"""Trace event types for OBaI testing framework.

These Pydantic models define the structure for capturing agent execution traces.
Used by TraceCapture to record and by CLI to display.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Types of trace events."""

    QUERY_START = "query_start"
    GUARDRAIL_CHECK = "guardrail_check"
    AGENT_START = "agent_start"
    AGENT_HANDOFF = "agent_handoff"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    RESPONSE_CHUNK = "response_chunk"
    RESPONSE_COMPLETE = "response_complete"
    ERROR = "error"


class TraceEvent(BaseModel):
    """Base class for all trace events."""

    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    elapsed_ms: float = 0.0  # ms since trace start


class QueryStartEvent(TraceEvent):
    """Query received from user."""

    event_type: EventType = EventType.QUERY_START
    query: str
    session_id: str | None = None


class GuardrailEvent(TraceEvent):
    """Guardrail check result."""

    event_type: EventType = EventType.GUARDRAIL_CHECK
    passed: bool
    classification: str | None = None  # "financial", "off_topic", etc.
    confidence: float | None = None
    rejection_reason: str | None = None


class AgentEvent(TraceEvent):
    """Agent started or received handoff."""

    event_type: EventType = EventType.AGENT_START
    agent_name: str
    is_handoff: bool = False
    from_agent: str | None = None


class ToolCallEvent(TraceEvent):
    """Tool call start or end."""

    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    # Only populated for TOOL_CALL_END
    response: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float | None = None


class ResponseChunkEvent(TraceEvent):
    """Streaming response chunk."""

    event_type: EventType = EventType.RESPONSE_CHUNK
    agent_name: str
    chunk: str


class ResponseCompleteEvent(TraceEvent):
    """Final response complete."""

    event_type: EventType = EventType.RESPONSE_COMPLETE
    agent_name: str
    full_response: str


class ErrorEvent(TraceEvent):
    """Error occurred during execution."""

    event_type: EventType = EventType.ERROR
    error_type: str
    error_message: str
    agent_name: str | None = None


class ToolCallSummary(BaseModel):
    """Summary of a tool call for analysis."""

    tool_name: str
    args: dict[str, Any]
    response: dict[str, Any] | None = None
    error: str | None = None
    latency_ms: float
    timestamp: datetime
    agent_name: str


class AgentSummary(BaseModel):
    """Summary of an agent's work."""

    agent_name: str
    start_time: datetime
    end_time: datetime | None = None
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    response: str | None = None
    is_handoff_target: bool = False


class TraceTiming(BaseModel):
    """Timing breakdown for the trace."""

    total_ms: float
    guardrail_ms: float | None = None
    orchestrator_ms: float | None = None
    specialist_breakdown: dict[str, float] = Field(default_factory=dict)
    tool_breakdown: dict[str, float] = Field(default_factory=dict)


class TraceMetrics(BaseModel):
    """Computed metrics from trace (custom, not OpenAI Evals)."""

    # Routing
    specialists_called: list[str] = Field(default_factory=list)

    # Sequencing
    call_sequence: list[str] = Field(default_factory=list)  # ["screener", "market_data"]
    sequencing_correct: bool | None = None  # Set by test case validation

    # Efficiency
    total_tool_calls: int = 0
    redundant_calls: int = 0
    unique_tools: list[str] = Field(default_factory=list)

    # Timing
    timing: TraceTiming | None = None


class Trace(BaseModel):
    """Complete execution trace for a query."""

    trace_id: str
    query: str
    model: str
    session_id: str | None = None

    # Timestamps
    start_time: datetime
    end_time: datetime | None = None

    # Events (chronological)
    events: list[TraceEvent] = Field(default_factory=list)

    # Summaries (computed)
    agents: list[AgentSummary] = Field(default_factory=list)
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)

    # Final output
    final_response: str | None = None
    guardrail_passed: bool | None = None

    # Metrics
    metrics: TraceMetrics = Field(default_factory=TraceMetrics)

    # Raw MCP tool outputs from specialist agents (inner calls)
    inner_tool_outputs: list[dict[str, Any]] = Field(default_factory=list)

    # For OpenAI Evals integration
    output_text: str | None = None  # {{ sample.output_text }}
    output_tools: list[dict[str, Any]] = Field(default_factory=list)  # {{ sample.output_tools }}

    def to_evals_sample(self) -> dict[str, Any]:
        """Convert trace to OpenAI Evals sample format.

        Returns:
            Dict with output_text and output_tools for Evals graders.
        """
        return {
            "output_text": self.final_response or "",
            "output_tools": [
                {
                    "function": {
                        "name": tc.tool_name,
                        "arguments": tc.args,
                    }
                }
                for tc in self.tool_calls
            ],
        }
