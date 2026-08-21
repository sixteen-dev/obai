"""Trace capture for OBaI agent execution.

Hooks into Agent SDK streaming events to capture:
- Tool call sequences with timestamps
- Agent handoffs
- Response chunks
- Timing breakdown

Usage:
    async with TraceCapture(query="What is AAPL?", model="gpt-5.6-sol") as capture:
        result = Runner.run_streamed(agent, query)
        async for event in result.stream_events():
            capture.process_event(event)
        trace = capture.get_trace()
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from evaluation.trace.types import (
    AgentEvent,
    AgentSummary,
    ErrorEvent,
    EventType,
    GuardrailEvent,
    QueryStartEvent,
    ResponseChunkEvent,
    ResponseCompleteEvent,
    ToolCallEvent,
    ToolCallSummary,
    Trace,
    TraceMetrics,
    TraceTiming,
)


class TraceCapture:
    """Captures execution trace from Agent SDK streaming events.

    This class processes streaming events from Runner.run_streamed() and
    builds a complete Trace object for analysis and debugging.

    Example:
        ```python
        capture = TraceCapture(query="AAPL price?", model="gpt-5.6-sol")
        capture.start()

        result = Runner.run_streamed(agent, query)
        async for event in result.stream_events():
            capture.process_event(event)

        trace = capture.finalize()
        ```
    """

    def __init__(
        self,
        query: str,
        model: str,
        session_id: str | None = None,
    ) -> None:
        """Initialize trace capture.

        Args:
            query: User query being executed.
            model: Model name (e.g., "gpt-5.6-sol").
            session_id: Optional session ID for context tracking.
        """
        self.query = query
        self.model = model
        self.session_id = session_id

        self.trace_id = str(uuid.uuid4())[:8]
        self.start_time: datetime | None = None
        self.events: list[Any] = []  # TraceEvent subclasses

        # State tracking
        self._current_agent: str = "central_hub"
        self._agent_start_times: dict[str, datetime] = {}
        self._tool_start_times: dict[str, datetime] = {}  # call_id -> start
        self._pending_tool_calls: dict[str, ToolCallEvent] = {}  # call_id -> event
        self._response_chunks: list[str] = []
        self._agent_summaries: dict[str, AgentSummary] = {}
        self._tool_calls: list[ToolCallSummary] = []
        self._inner_tool_outputs: list[dict[str, Any]] = []
        self._finalized = False

    def start(self) -> None:
        """Start trace capture. Call before processing events."""
        self.start_time = datetime.now(tz=UTC)
        event = QueryStartEvent(
            query=self.query,
            session_id=self.session_id,
            elapsed_ms=0.0,
        )
        self.events.append(event)

    def _elapsed_ms(self) -> float:
        """Get elapsed time since trace start in milliseconds."""
        if self.start_time is None:
            return 0.0
        delta = datetime.now(tz=UTC) - self.start_time
        return delta.total_seconds() * 1000

    def record_guardrail(
        self,
        passed: bool,
        classification: str | None = None,
        confidence: float | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        """Record guardrail check result.

        Args:
            passed: Whether the guardrail passed.
            classification: Query classification (e.g., "financial").
            confidence: Confidence score if available.
            rejection_reason: Reason for rejection if not passed.
        """
        event = GuardrailEvent(
            passed=passed,
            classification=classification,
            confidence=confidence,
            rejection_reason=rejection_reason,
            elapsed_ms=self._elapsed_ms(),
        )
        self.events.append(event)

    def record_agent_start(
        self,
        agent_name: str,
        is_handoff: bool = False,
        from_agent: str | None = None,
    ) -> None:
        """Record agent starting or receiving handoff.

        Args:
            agent_name: Name of the agent.
            is_handoff: Whether this is a handoff from another agent.
            from_agent: Source agent if handoff.
        """
        event_type = EventType.AGENT_HANDOFF if is_handoff else EventType.AGENT_START
        event = AgentEvent(
            event_type=event_type,
            agent_name=agent_name,
            is_handoff=is_handoff,
            from_agent=from_agent,
            elapsed_ms=self._elapsed_ms(),
        )
        self.events.append(event)

        # Track for summary
        self._current_agent = agent_name
        self._agent_start_times[agent_name] = datetime.now(tz=UTC)

        if agent_name not in self._agent_summaries:
            self._agent_summaries[agent_name] = AgentSummary(
                agent_name=agent_name,
                start_time=datetime.now(tz=UTC),
                is_handoff_target=is_handoff,
            )

    def record_tool_call_start(
        self,
        tool_name: str,
        args: dict[str, Any],
        call_id: str | None = None,
    ) -> None:
        """Record tool call starting.

        Args:
            tool_name: Name of the tool (e.g., "get_quote").
            args: Tool arguments.
            call_id: Unique call ID for matching with output events.
        """
        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_START,
            tool_name=tool_name,
            tool_args=args,
            elapsed_ms=self._elapsed_ms(),
        )
        self.events.append(event)
        key = call_id or tool_name
        self._tool_start_times[key] = datetime.now(tz=UTC)
        self._pending_tool_calls[key] = event

    def record_tool_call_end(
        self,
        tool_name: str,
        response: dict[str, Any] | None = None,
        error: str | None = None,
        call_id: str | None = None,
    ) -> None:
        """Record tool call completing.

        Args:
            tool_name: Name of the tool.
            response: Tool response if successful.
            error: Error message if failed.
            call_id: Unique call ID for matching with start event.
        """
        key = call_id or tool_name
        start_time = self._tool_start_times.pop(key, datetime.now(tz=UTC))
        latency_ms = (datetime.now(tz=UTC) - start_time).total_seconds() * 1000

        # Get args from matching pending call
        args: dict[str, Any] = {}
        pending = self._pending_tool_calls.pop(key, None)
        if pending is not None:
            args = pending.tool_args

        event = ToolCallEvent(
            event_type=EventType.TOOL_CALL_END,
            tool_name=tool_name,
            tool_args=args,
            response=response,
            error=error,
            latency_ms=latency_ms,
            elapsed_ms=self._elapsed_ms(),
        )
        self.events.append(event)

        # Add to summary
        summary = ToolCallSummary(
            tool_name=tool_name,
            args=args,
            response=response,
            error=error,
            latency_ms=latency_ms,
            timestamp=start_time,
            agent_name=self._current_agent,
        )
        self._tool_calls.append(summary)

        # Add to agent's tool calls
        if self._current_agent in self._agent_summaries:
            self._agent_summaries[self._current_agent].tool_calls.append(summary)

    def record_response_chunk(self, agent_name: str, chunk: str) -> None:
        """Record streaming response chunk.

        Args:
            agent_name: Agent producing the response.
            chunk: Text chunk.
        """
        event = ResponseChunkEvent(
            agent_name=agent_name,
            chunk=chunk,
            elapsed_ms=self._elapsed_ms(),
        )
        self.events.append(event)
        self._response_chunks.append(chunk)

    def record_response_complete(self, agent_name: str, full_response: str) -> None:
        """Record response complete.

        Args:
            agent_name: Agent that produced the response.
            full_response: Complete response text.
        """
        event = ResponseCompleteEvent(
            agent_name=agent_name,
            full_response=full_response,
            elapsed_ms=self._elapsed_ms(),
        )
        self.events.append(event)

        if agent_name in self._agent_summaries:
            self._agent_summaries[agent_name].response = full_response
            self._agent_summaries[agent_name].end_time = datetime.now(tz=UTC)

    def record_error(
        self,
        error_type: str,
        error_message: str,
        agent_name: str | None = None,
    ) -> None:
        """Record error during execution.

        Args:
            error_type: Type of error (e.g., "MCPConnectionError").
            error_message: Error message.
            agent_name: Agent where error occurred.
        """
        event = ErrorEvent(
            error_type=error_type,
            error_message=error_message,
            agent_name=agent_name,
            elapsed_ms=self._elapsed_ms(),
        )
        self.events.append(event)

    def process_sdk_event(self, event: Any) -> None:
        """Process an Agent SDK streaming event.

        This is the main integration point with Agent SDK. Pass events
        from Runner.run_streamed().stream_events() here.

        Args:
            event: Agent SDK streaming event.
        """
        # Import here to avoid circular imports
        from agents.stream_events import (
            AgentUpdatedStreamEvent,
            RawResponsesStreamEvent,
            RunItemStreamEvent,
        )
        from openai.types.responses import ResponseTextDeltaEvent

        # Handle agent changes (handoffs)
        if isinstance(event, AgentUpdatedStreamEvent):
            agent_name = event.new_agent.name
            self.record_agent_start(
                agent_name=agent_name,
                is_handoff=True,
                from_agent=self._current_agent,
            )

        # Handle completed items (tool calls, messages)
        elif isinstance(event, RunItemStreamEvent):
            item = event.item
            item_type = getattr(item, "type", None)

            # Tool call
            if item_type == "tool_call_item":
                raw_item = getattr(item, "raw_item", None)
                if raw_item:
                    tool_name = getattr(raw_item, "name", "unknown")
                    call_id = getattr(raw_item, "call_id", None)
                    raw_args = getattr(raw_item, "arguments", "{}")
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except (json.JSONDecodeError, TypeError):
                        args = {"raw": str(raw_args)[:100]}

                    self.record_tool_call_start(tool_name, args, call_id=call_id)

            # Tool output
            elif item_type == "tool_call_output_item":
                # ToolCallOutputItem.output is the direct Python return value;
                # raw_item is a FunctionCallOutput TypedDict (plain dict at
                # runtime) so getattr() won't find its keys — use .get().
                tool_output = getattr(item, "output", None)
                raw_item = getattr(item, "raw_item", None)

                # Extract call_id for matching with the start event.
                output_call_id: str | None = None
                if raw_item is not None:
                    if isinstance(raw_item, dict):
                        output_call_id = raw_item.get("call_id")
                    else:
                        output_call_id = getattr(raw_item, "call_id", None)

                # Extract the output string: prefer item.output, fall back
                # to raw_item["output"] (dict key, NOT attribute).
                output: str | None = None
                if isinstance(tool_output, str):
                    output = tool_output
                elif raw_item is not None:
                    if isinstance(raw_item, dict):
                        output = raw_item.get("output")
                    else:
                        output = getattr(raw_item, "output", None)

                # Parse to dict for downstream scorers.
                _MAX_RAW = 4000
                response: dict[str, Any] | None = None
                if isinstance(output, str):
                    try:
                        parsed = json.loads(output)
                        response = (
                            parsed if isinstance(parsed, dict) else {"raw": output[:_MAX_RAW]}
                        )
                    except (json.JSONDecodeError, TypeError):
                        response = {"raw": output[:_MAX_RAW]}
                elif isinstance(output, dict):
                    response = output
                elif output is not None:
                    response = {"raw": str(output)[:_MAX_RAW]}

                # Match output to its corresponding tool call by call_id.
                key = output_call_id or ""
                pending = self._pending_tool_calls.get(key)
                if pending is not None:
                    self.record_tool_call_end(
                        tool_name=pending.tool_name,
                        response=response,
                        call_id=key,
                    )
                elif self._pending_tool_calls:
                    # Fallback: match to oldest pending call (preserves
                    # behavior for SDK versions without call_id).
                    oldest_key = next(iter(self._pending_tool_calls))
                    oldest = self._pending_tool_calls[oldest_key]
                    self.record_tool_call_end(
                        tool_name=oldest.tool_name,
                        response=response,
                        call_id=oldest_key,
                    )

        # Handle streaming text
        elif isinstance(event, RawResponsesStreamEvent):
            data = event.data
            if isinstance(data, ResponseTextDeltaEvent):
                delta = data.delta
                if delta:
                    self.record_response_chunk(self._current_agent, delta)

    def set_inner_tool_outputs(self, outputs: list[dict[str, Any]]) -> None:
        """Set raw MCP tool outputs from specialist inner calls.

        Args:
            outputs: List of dicts with specialist, tool_name, output.
        """
        self._inner_tool_outputs = outputs

    def finalize(self) -> Trace:
        """Finalize trace and compute metrics.

        Call this after all events have been processed.

        Returns:
            Complete Trace object with metrics.
        """
        if self._finalized:
            msg = "Trace already finalized"
            raise RuntimeError(msg)

        self._finalized = True
        end_time = datetime.now(tz=UTC)

        # Build final response from chunks
        final_response = "".join(self._response_chunks) if self._response_chunks else None

        # Compute metrics
        metrics = self._compute_metrics()

        # Build agent summaries list
        agents = list(self._agent_summaries.values())

        # Check guardrail status
        guardrail_passed: bool | None = None
        for event in self.events:
            if isinstance(event, GuardrailEvent):
                guardrail_passed = event.passed
                break

        trace = Trace(
            trace_id=self.trace_id,
            query=self.query,
            model=self.model,
            session_id=self.session_id,
            start_time=self.start_time or datetime.now(tz=UTC),
            end_time=end_time,
            events=self.events,
            agents=agents,
            tool_calls=self._tool_calls,
            final_response=final_response,
            guardrail_passed=guardrail_passed,
            metrics=metrics,
            inner_tool_outputs=self._inner_tool_outputs,
            output_text=final_response,
            output_tools=[
                {"function": {"name": tc.tool_name, "arguments": tc.args}}
                for tc in self._tool_calls
            ],
        )

        return trace

    def _compute_metrics(self) -> TraceMetrics:
        """Compute trace metrics for analysis."""
        # Specialists called
        specialists_called = list(self._agent_summaries.keys())

        # Call sequence (order of agents that made tool calls)
        call_sequence: list[str] = []
        for tc in self._tool_calls:
            if tc.agent_name not in call_sequence:
                call_sequence.append(tc.agent_name)

        # Efficiency
        tool_names = [tc.tool_name for tc in self._tool_calls]
        unique_tools = list(set(tool_names))

        # Count redundant calls (same tool + same args)
        call_signatures = [
            (tc.tool_name, json.dumps(tc.args, sort_keys=True)) for tc in self._tool_calls
        ]
        redundant_calls = len(call_signatures) - len(set(call_signatures))

        # Timing
        total_ms = self._elapsed_ms()

        # Guardrail timing
        guardrail_ms: float | None = None
        for event in self.events:
            if isinstance(event, GuardrailEvent):
                guardrail_ms = event.elapsed_ms
                break

        # Tool breakdown
        tool_breakdown: dict[str, float] = {}
        for tc in self._tool_calls:
            tool_breakdown[tc.tool_name] = tool_breakdown.get(tc.tool_name, 0) + tc.latency_ms

        # Specialist breakdown
        specialist_breakdown: dict[str, float] = {}
        for agent_name, summary in self._agent_summaries.items():
            if summary.start_time and summary.end_time:
                delta = (summary.end_time - summary.start_time).total_seconds() * 1000
                specialist_breakdown[agent_name] = delta

        timing = TraceTiming(
            total_ms=total_ms,
            guardrail_ms=guardrail_ms,
            specialist_breakdown=specialist_breakdown,
            tool_breakdown=tool_breakdown,
        )

        return TraceMetrics(
            specialists_called=specialists_called,
            call_sequence=call_sequence,
            total_tool_calls=len(self._tool_calls),
            redundant_calls=redundant_calls,
            unique_tools=unique_tools,
            timing=timing,
        )

    async def __aenter__(self) -> "TraceCapture":
        """Async context manager entry."""
        self.start()
        return self

    async def __aexit__(self, *_args: Any) -> None:
        """Async context manager exit."""
        # Finalize is called explicitly, not here
        pass
