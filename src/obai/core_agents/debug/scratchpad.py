"""Debug scratchpad for agent execution logging.

This module provides an append-only JSONL log for debugging agent execution.
It is for DEBUGGING ONLY - not injected into prompts.

Useful for understanding why an agent made certain decisions, tracking
tool calls, and diagnosing issues in production.

Usage:
    ```python
    from core_agents.debug.scratchpad import DebugScratchpad

    # Create scratchpad for a query
    scratchpad = DebugScratchpad(session_id="cli", query="What is AAPL trading at?")

    # Log events during execution
    scratchpad.log_specialist_call("Market Data Agent", "Get AAPL price")
    scratchpad.log_mcp_tool("Market Data Agent", "get_quote", {"symbol": "AAPL"}, 234)
    scratchpad.log_response("AAPL is trading at $185.42...")

    # Scratchpad file is written to .obai/debug/
    ```

Environment Variables:
    OBAI_DEBUG_DIR: Directory for debug logs (default: .obai/debug)
    OBAI_DEBUG_ENABLED: Enable/disable debug logging (default: false)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class ScratchpadEntry(BaseModel):
    """Single entry in the debug scratchpad.

    Attributes:
        entry_type: Type of entry (query, specialist, tool, response, error).
        content: Main content of the entry.
        metadata: Additional context for the entry.
        timestamp: When the entry was created.
    """

    entry_type: Literal["query", "specialist", "tool", "response", "error"]
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class DebugScratchpad:
    """Append-only debug log for agent execution.

    Writes to .obai/debug/ for post-hoc analysis.
    NOT used for prompt context - purely for debugging.

    The scratchpad creates a unique JSONL file for each query,
    allowing easy correlation of tool calls and responses.

    Attributes:
        session_id: ID of the current session.
        query: The user's query being processed.
        filepath: Path to the scratchpad file.
        enabled: Whether logging is enabled.
    """

    def __init__(
        self,
        session_id: str,
        query: str,
        base_dir: Path | None = None,
    ) -> None:
        """Initialize debug scratchpad.

        Args:
            session_id: Current session identifier.
            query: The user's query being processed.
            base_dir: Directory for debug logs (default: .obai/debug).
        """
        self.session_id = session_id
        self.query = query
        self.enabled = os.environ.get("OBAI_DEBUG_ENABLED", "false").lower() == "true"

        if not self.enabled:
            self.filepath = Path("/dev/null")  # Dummy path when disabled
            return

        # Determine base directory
        env_dir = os.environ.get("OBAI_DEBUG_DIR")
        if env_dir:
            self.base_dir = Path(env_dir)
        elif base_dir:
            self.base_dir = base_dir
        else:
            self.base_dir = Path(".obai/debug")

        self.base_dir.mkdir(parents=True, exist_ok=True)

        # Create unique file for this query
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]  # noqa: S324
        self.filepath = self.base_dir / f"{session_id}_{timestamp}_{query_hash}.jsonl"

        # Write initial entry
        self._write(
            ScratchpadEntry(
                entry_type="query",
                content=query,
                metadata={"session_id": session_id},
            )
        )
        logger.debug(f"Debug scratchpad created: {self.filepath}")

    def _write(self, entry: ScratchpadEntry) -> None:
        """Append entry to scratchpad file.

        Args:
            entry: The entry to write.
        """
        if not self.enabled:
            return

        try:
            with open(self.filepath, "a") as f:
                f.write(
                    json.dumps(
                        {
                            "type": entry.entry_type,
                            "content": entry.content,
                            "metadata": entry.metadata,
                            "timestamp": entry.timestamp.isoformat(),
                        },
                        default=str,
                    )
                    + "\n"
                )
        except OSError:
            logger.exception(f"Failed to write to scratchpad: {self.filepath}")

    def log_specialist_call(
        self,
        specialist_name: str,
        query_to_specialist: str,
    ) -> None:
        """Log when a specialist agent is called.

        Args:
            specialist_name: Name of the specialist (e.g., "Market Data Agent").
            query_to_specialist: The query being sent to the specialist.
        """
        self._write(
            ScratchpadEntry(
                entry_type="specialist",
                content=f"Called {specialist_name}",
                metadata={"specialist": specialist_name, "query": query_to_specialist},
            )
        )

    def log_mcp_tool(
        self,
        specialist_name: str,
        tool_name: str,
        args: dict[str, Any],
        duration_ms: int | None = None,
        result_summary: str | None = None,
    ) -> None:
        """Log MCP tool execution.

        Args:
            specialist_name: Which specialist made the call.
            tool_name: Name of the MCP tool.
            args: Arguments passed to the tool.
            duration_ms: How long the call took (if known).
            result_summary: Brief summary of the result.
        """
        metadata: dict[str, Any] = {
            "specialist": specialist_name,
            "tool": tool_name,
            "args": args,
        }
        if duration_ms is not None:
            metadata["duration_ms"] = duration_ms
        if result_summary:
            metadata["result_summary"] = result_summary

        self._write(
            ScratchpadEntry(
                entry_type="tool",
                content=f"{specialist_name} -> {tool_name}",
                metadata=metadata,
            )
        )

    def log_response(self, response_preview: str, full_length: int | None = None) -> None:
        """Log final response (truncated for readability).

        Args:
            response_preview: First part of the response (up to 500 chars).
            full_length: Full length of the response (if truncated).
        """
        metadata: dict[str, Any] = {}
        if full_length is not None:
            metadata["full_length"] = full_length
            metadata["truncated"] = len(response_preview) < full_length

        self._write(
            ScratchpadEntry(
                entry_type="response",
                content=response_preview[:500],
                metadata=metadata,
            )
        )

    def log_error(
        self,
        error: str,
        error_type: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Log error encountered during execution.

        Args:
            error: Error message.
            error_type: Type/class of the error.
            context: Additional context about what was happening.
        """
        metadata: dict[str, Any] = context.copy() if context else {}
        if error_type:
            metadata["error_type"] = error_type

        self._write(
            ScratchpadEntry(
                entry_type="error",
                content=error,
                metadata=metadata,
            )
        )

    def log_planning(self, plan_text: str) -> None:
        """Log hub's planning output.

        Args:
            plan_text: The planning output from the hub.
        """
        self._write(
            ScratchpadEntry(
                entry_type="specialist",  # Reuse specialist type for hub
                content="Hub planning",
                metadata={"plan": plan_text},
            )
        )


def create_scratchpad(session_id: str, query: str) -> DebugScratchpad:
    """Create a debug scratchpad for a query.

    This is the recommended way to create a scratchpad.

    Args:
        session_id: Current session identifier.
        query: The user's query being processed.

    Returns:
        A DebugScratchpad instance (may be disabled based on environment).
    """
    return DebugScratchpad(session_id=session_id, query=query)


__all__ = [
    "DebugScratchpad",
    "ScratchpadEntry",
    "create_scratchpad",
]
