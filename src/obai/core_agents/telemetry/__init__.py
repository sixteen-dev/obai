"""Telemetry module for async trace storage to DynamoDB.

This module provides non-blocking telemetry capture for conversation traces,
tool calls, timing, and token usage. All writes are fire-and-forget to avoid
impacting the main request flow.

Usage:
    ```python
    # Option 1: Decorator (recommended for agent-level integration)
    from core_agents.telemetry import traced, shutdown_telemetry

    @traced(model="gpt-4o")
    async def run_query(query: str, session: Session):
        result = Runner.run_streamed(hub.agent, query, session=session)
        async for event in result.stream_events():
            yield event

    # Use it - telemetry is automatic
    async for event in run_query("What is AAPL?", session):
        handle_event(event)

    # On shutdown
    await shutdown_telemetry()

    # Option 2: Context manager (for more control)
    from core_agents.telemetry import telemetry_scope

    async with telemetry_scope(query, model, session_id) as ctx:
        result = Runner.run_streamed(agent, query, session=session)
        async for event in result.stream_events():
            ctx.process_event(event)
            handle_event(event)
        ctx.set_token_usage_from_result(result)

    # Option 3: Manual publisher management
    from core_agents.telemetry import TelemetryPublisher

    publisher = TelemetryPublisher()
    await publisher.start()
    publisher.publish(trace, token_usage)
    await publisher.stop()
    ```

Environment Variables:
    TELEMETRY_ENABLED: Enable/disable telemetry (default: true)
    TELEMETRY_TABLE_NAME: DynamoDB table name (default: obai_traces_dev)
    TELEMETRY_REGION: AWS region (default: us-east-2)
    TELEMETRY_ENDPOINT_URL: Custom endpoint for LocalStack
    TELEMETRY_TTL_DAYS: Trace retention in days (default: 90)
    TELEMETRY_ENVIRONMENT: Environment name (default: dev)
"""

from core_agents.telemetry.config import (
    TelemetryConfig,
    get_telemetry_config,
    reset_telemetry_config,
)
from core_agents.telemetry.decorator import (
    TelemetryContext,
    shutdown_telemetry,
    telemetry_scope,
    traced,
)
from core_agents.telemetry.models import TokenUsage, TraceItem
from core_agents.telemetry.publisher import (
    TelemetryPublisher,
    get_publisher,
    publish_trace,
    shutdown_publisher,
)

__all__ = [
    # Config
    "TelemetryConfig",
    "get_telemetry_config",
    "reset_telemetry_config",
    # Decorator & Context Manager (recommended)
    "traced",
    "telemetry_scope",
    "TelemetryContext",
    "shutdown_telemetry",
    # Models
    "TokenUsage",
    "TraceItem",
    # Publisher (low-level)
    "TelemetryPublisher",
    "get_publisher",
    "publish_trace",
    "shutdown_publisher",
]
