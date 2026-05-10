"""Opik tracing for OBaI agent evaluation.

This module provides optional Opik integration for agent tracing
and evaluation. When enabled, all Agent SDK calls are automatically
traced to the Opik UI for analysis and debugging.

Configuration is via AgentConfig in core_agents.config:
    OPIK_ENABLED: Enable/disable Opik tracing (default: true)
    OPIK_OBAI_PROJECT_NAME: Opik project name (default: obai-eval)
    OPIK_URL: Opik server URL (default: http://localhost:5173)

Usage:
    ```python
    from core_agents.tracing import init_opik, is_opik_enabled

    # Initialize at application startup (before creating agents)
    if init_opik():
        print("Opik tracing enabled")

    # Check if Opik is active
    if is_opik_enabled():
        print("Traces are being sent to Opik")
    ```
"""

from core_agents.tracing.opik_init import (
    init_opik,
    is_opik_enabled,
)

__all__ = [
    "init_opik",
    "is_opik_enabled",
]
