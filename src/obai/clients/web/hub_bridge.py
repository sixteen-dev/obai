"""Bridge between the Central Hub and WebSocket connections.

Handles:
- MCP callback multiplexing (single global callback -> active connection)
- Query serialization (asyncio.Lock — hub.run() uses module-level state)
- Event translation (Agent SDK events -> JSON-serializable dicts)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from clients.shared import SPECIALIST_TOOLS, ToolCallTracker, format_tool_args

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agents import Session

    from core_agents.central_hub_agent import CentralHubAgent

logger = logging.getLogger(__name__)


class HubBridge:
    """Bridge between CentralHubAgent and WebSocket connections.

    Serializes queries (one at a time via asyncio.Lock), translates
    Agent SDK events + MCP callbacks into JSON-serializable dicts.
    """

    def __init__(self, hub: CentralHubAgent) -> None:
        """Initialize bridge with hub reference, lock, and MCP buffer."""
        self._hub = hub
        self._lock = asyncio.Lock()
        self._mcp_buffer: list[dict[str, Any]] = []
        self._tracker = ToolCallTracker()

    def install_mcp_callback(self) -> None:
        """Register the global MCP tool callback.

        Must be called once after hub initialization.
        """
        from core_agents.central_hub_agent import set_mcp_tool_callback

        set_mcp_tool_callback(self._on_mcp_event)

    def _on_mcp_event(
        self,
        event_type: str,
        specialist_name: str,
        tool_name: str,
        args: str,
        call_id: str,
        duration_ms: int | None = None,
    ) -> None:
        """Global MCP callback — appends events to buffer.

        Called synchronously from within hub.run() iteration, so no
        async queue needed. Buffer is drained after each hub event.
        """
        if event_type == "start":
            self._tracker.start_mcp(call_id)
            parent_id = self._tracker.get_specialist_id(specialist_name)
            self._mcp_buffer.append(
                {
                    "type": "tool_start",
                    "call_id": call_id,
                    "agent": specialist_name,
                    "tool": tool_name,
                    "args": args,
                    "parent_id": parent_id,
                    "is_mcp": True,
                }
            )
        elif event_type == "complete":
            actual_dur = self._tracker.complete(call_id)
            self._mcp_buffer.append(
                {
                    "type": "tool_complete",
                    "call_id": call_id,
                    "duration_ms": actual_dur or duration_ms or 0,
                }
            )

    def _drain_mcp_buffer(self) -> list[dict[str, Any]]:
        """Drain and return all buffered MCP events."""
        if not self._mcp_buffer:
            return []
        events = list(self._mcp_buffer)
        self._mcp_buffer.clear()
        return events

    async def run_query(
        self,
        text: str,
        session: Session,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run a query through the hub, yielding WS-protocol dicts.

        Acquires the query lock (hub.run uses module-level state).
        Mirrors the event handling in tui.py:671-793.

        Args:
            text: User query text.
            session: Agent SDK session for conversation memory.

        Yields:
            JSON-serializable dicts matching the WebSocket protocol.
        """
        # Lazy imports — heavy agent SDK deps
        from agents.items import ItemHelpers, MessageOutputItem
        from agents.stream_events import (
            AgentUpdatedStreamEvent,
            RawResponsesStreamEvent,
            RunItemStreamEvent,
        )
        from openai.types.responses import ResponseTextDeltaEvent

        from core_agents.central_hub_agent import PredictionPassthroughEvent
        from core_agents.guardrails import get_rejection_message

        async with self._lock:
            self._tracker.clear()
            self._mcp_buffer.clear()

            current_agent = "Central Hub"
            hub_analyzing = False
            hub_synthesizing = False
            response_text = ""
            query_start = time.perf_counter()
            specialists_used: list[str] = []
            tool_events: list[dict[str, Any]] = []

            # Initial hub analyze step
            hub_analyze_id = "hub_analyze"
            yield {
                "type": "tool_start",
                "call_id": hub_analyze_id,
                "agent": "Central Hub",
                "tool": "analyze",
                "args": f"Query: {text[:40]}...",
                "parent_id": None,
                "is_mcp": False,
            }
            hub_analyzing = True

            try:
                async for event in self._hub.run(text, session):
                    # --- Prediction passthrough ---
                    if isinstance(event, PredictionPassthroughEvent):
                        response_text = event.content
                        yield {"type": "text_delta", "delta": event.content}
                        continue

                    # --- Agent switch ---
                    if isinstance(event, AgentUpdatedStreamEvent):
                        agent_name = event.new_agent.name
                        display = agent_name.replace("obai_", "").replace("_", " ").title()

                        if hub_analyzing and display != "Central Hub":
                            analyze_ms = int((time.perf_counter() - query_start) * 1000)
                            yield {
                                "type": "tool_complete",
                                "call_id": hub_analyze_id,
                                "duration_ms": analyze_ms,
                            }
                            hub_analyzing = False

                        is_returning = (
                            "Central Hub" in display
                            and current_agent != "Central Hub"
                            and not hub_synthesizing
                        )
                        if is_returning:
                            yield {
                                "type": "tool_start",
                                "call_id": "hub_synth",
                                "agent": "Central Hub",
                                "tool": "synthesize",
                                "args": "Generating report...",
                                "parent_id": None,
                                "is_mcp": False,
                            }
                            hub_synthesizing = True

                        current_agent = display
                        if display not in specialists_used and display != "Central Hub":
                            specialists_used.append(display)
                        yield {"type": "agent_switch", "agent": display}

                    # --- Tool call items ---
                    elif isinstance(event, RunItemStreamEvent):
                        item = event.item
                        item_type = getattr(item, "type", None)

                        if item_type == "tool_call_item":
                            raw_item = getattr(item, "raw_item", None)
                            if raw_item:
                                tool_name = getattr(raw_item, "name", "unknown")
                                call_id = getattr(raw_item, "call_id", None)

                                if tool_name in SPECIALIST_TOOLS:
                                    display_name = SPECIALIST_TOOLS[tool_name]
                                    if hub_analyzing:
                                        analyze_ms = int((time.perf_counter() - query_start) * 1000)
                                        yield {
                                            "type": "tool_complete",
                                            "call_id": hub_analyze_id,
                                            "duration_ms": analyze_ms,
                                        }
                                        hub_analyzing = False
                                else:
                                    display_name = current_agent

                                raw_args = getattr(raw_item, "arguments", "{}")
                                args_str = format_tool_args(raw_args, tool_name)

                                if call_id:
                                    self._tracker.start_specialist(call_id, display_name)
                                    evt = {
                                        "type": "tool_start",
                                        "call_id": call_id,
                                        "agent": display_name,
                                        "tool": tool_name,
                                        "args": args_str,
                                        "parent_id": None,
                                        "is_mcp": False,
                                    }
                                    yield evt
                                    tool_events.append(evt)

                        elif item_type == "tool_call_output_item":
                            raw_item = getattr(item, "raw_item", None)
                            if raw_item:
                                call_id = (
                                    raw_item.get("call_id")
                                    if isinstance(raw_item, dict)
                                    else getattr(raw_item, "call_id", None)
                                )
                                if call_id:
                                    dur = self._tracker.complete(call_id)
                                    yield {
                                        "type": "tool_complete",
                                        "call_id": call_id,
                                        "duration_ms": dur if dur is not None else 0,
                                    }

                        elif item_type == "message_output_item":
                            if isinstance(item, MessageOutputItem) and not response_text:
                                msg_text = ItemHelpers.text_message_output(item)
                                if msg_text:
                                    response_text = msg_text

                    # --- Streaming text ---
                    elif isinstance(event, RawResponsesStreamEvent):
                        data = event.data
                        if isinstance(data, ResponseTextDeltaEvent):
                            delta = data.delta
                            if delta:
                                response_text += delta
                                yield {"type": "text_delta", "delta": delta}

                    # Drain MCP buffer after each hub event
                    for mcp_evt in self._drain_mcp_buffer():
                        yield mcp_evt
                        tool_events.append(mcp_evt)

                # Finalize
                if hub_synthesizing:
                    total_ms = int((time.perf_counter() - query_start) * 1000)
                    yield {"type": "tool_complete", "call_id": "hub_synth", "duration_ms": total_ms}

                total_ms = int((time.perf_counter() - query_start) * 1000)
                complete_evt: dict[str, Any] = {
                    "type": "complete",
                    "duration_ms": total_ms,
                    "specialists": specialists_used,
                    "response_text": response_text,
                    "tool_data": tool_events,
                }

                yield complete_evt

            except Exception as e:
                error_name = type(e).__name__
                if "InputGuardrailTripwireTriggered" in error_name:
                    guardrail_result = getattr(e, "guardrail_result", None)
                    output = getattr(guardrail_result, "output", None)
                    validation_info = getattr(output, "output_info", None)
                    if validation_info:
                        rejection_msg = get_rejection_message(validation_info)
                    else:
                        rejection_msg = (
                            "Sorry, I can only help with stock market research"
                            " and financial analysis."
                        )
                    yield {"type": "error", "message": rejection_msg, "guardrail": True}
                else:
                    logger.exception("Query failed: %s", e)
                    yield {"type": "error", "message": str(e), "guardrail": False}
