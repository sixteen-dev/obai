"""Debug utilities for OBaI agent execution.

This module provides tools for debugging agent execution without
affecting production performance. All debug features are disabled
by default and must be explicitly enabled.

Usage:
    ```python
    from core_agents.debug import create_scratchpad

    # Create a debug scratchpad (only active if OBAI_DEBUG_ENABLED=true)
    scratchpad = create_scratchpad("session_123", "What is AAPL trading at?")

    # Log execution events
    scratchpad.log_specialist_call("Market Data Agent", "Get AAPL price")
    scratchpad.log_mcp_tool("Market Data Agent", "get_quote", {"symbol": "AAPL"}, 234)
    ```

Environment Variables:
    OBAI_DEBUG_ENABLED: Set to "true" to enable debug logging (default: false)
    OBAI_DEBUG_DIR: Directory for debug logs (default: .obai/debug)
"""

from core_agents.debug.scratchpad import (
    DebugScratchpad,
    ScratchpadEntry,
    create_scratchpad,
)

__all__ = [
    "DebugScratchpad",
    "ScratchpadEntry",
    "create_scratchpad",
]
