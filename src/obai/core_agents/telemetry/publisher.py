"""Async telemetry publisher for fire-and-forget trace storage.

Publishes traces to DynamoDB without blocking the main request flow.
Each trace is written immediately in a separate async task.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from core_agents.telemetry.client import DynamoClient, DynamoWriteError
from core_agents.telemetry.config import TelemetryConfig, get_telemetry_config
from core_agents.telemetry.models import TokenUsage, TraceItem

if TYPE_CHECKING:
    from evaluation.trace.types import Trace

logger = logging.getLogger(__name__)


class TelemetryPublisher:
    """Async, non-blocking telemetry publisher.

    Publishes traces to DynamoDB using fire-and-forget pattern. Each trace
    is written immediately in a background task without blocking the caller.

    Example:
        ```python
        publisher = TelemetryPublisher()
        await publisher.start()

        # Fire-and-forget - returns immediately
        publisher.publish(trace, token_usage)

        # On shutdown
        await publisher.stop()
        ```
    """

    def __init__(self, config: TelemetryConfig | None = None) -> None:
        """Initialize the publisher.

        Args:
            config: Optional config. Uses default if not provided.
        """
        self._config = config or get_telemetry_config()
        self._client: DynamoClient | None = None
        self._running = False
        self._pending_tasks: set[asyncio.Task[None]] = set()

    @property
    def is_enabled(self) -> bool:
        """Check if telemetry is enabled."""
        return self._config.enabled

    async def start(self) -> None:
        """Start the publisher and connect to DynamoDB.

        Call this once at application startup.
        """
        if not self._config.enabled:
            logger.info("Telemetry disabled, skipping publisher start")
            return

        self._client = DynamoClient(self._config)
        await self._client.connect()
        self._running = True
        logger.info(f"Telemetry publisher started (table: {self._config.table_name})")

    async def stop(self) -> None:
        """Stop the publisher and wait for pending writes.

        Call this at application shutdown for graceful cleanup.
        """
        self._running = False

        # Wait for pending tasks to complete (with timeout)
        if self._pending_tasks:
            logger.info(f"Waiting for {len(self._pending_tasks)} pending telemetry writes...")
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._pending_tasks, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout waiting for pending telemetry writes")

        # Close client
        if self._client:
            await self._client.close()
            self._client = None

        logger.info("Telemetry publisher stopped")

    def publish(
        self,
        trace: Trace,
        token_usage: TokenUsage | None = None,
    ) -> None:
        """Publish a trace to DynamoDB (fire-and-forget).

        This method returns immediately. The actual write happens
        in a background task.

        Args:
            trace: The trace to publish.
            token_usage: Optional token usage data from Agent SDK.
        """
        if not self._config.enabled or not self._running:
            return

        if self._client is None:
            logger.warning("Publisher not started, dropping trace")
            return

        # Create background task for the write
        task = asyncio.create_task(
            self._write_trace(trace, token_usage),
            name=f"telemetry-{trace.trace_id}",
        )

        # Track task and clean up when done
        self._pending_tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        """Callback when a write task completes."""
        self._pending_tasks.discard(task)

        # Log any exceptions
        if task.exception() is not None:
            logger.error(f"Telemetry write failed: {task.exception()}")

    async def _write_trace(
        self,
        trace: Trace,
        token_usage: TokenUsage | None,
    ) -> None:
        """Write a trace to DynamoDB with retry.

        Args:
            trace: The trace to write.
            token_usage: Optional token usage data.
        """
        if self._client is None:
            return

        # Convert trace to DynamoDB item
        item = TraceItem.from_trace(
            trace=trace,
            token_usage=token_usage,
            environment=self._config.environment,
            ttl_days=self._config.ttl_days,
        )

        # Retry loop
        last_error: Exception | None = None
        for attempt in range(self._config.max_retries):
            try:
                await self._client.put_trace(item)
                logger.debug(f"Published trace {trace.trace_id}")
                return
            except DynamoWriteError as e:
                last_error = e
                if attempt < self._config.max_retries - 1:
                    await asyncio.sleep(self._config.retry_delay_seconds)
                    logger.warning(f"Retrying trace write (attempt {attempt + 2})")

        # All retries failed
        logger.error(
            f"Failed to publish trace {trace.trace_id} "
            f"after {self._config.max_retries} attempts: {last_error}"
        )


# Global publisher instance for convenience
_publisher: TelemetryPublisher | None = None


async def get_publisher() -> TelemetryPublisher:
    """Get or create the global telemetry publisher.

    Returns:
        The global TelemetryPublisher instance.
    """
    global _publisher
    if _publisher is None:
        _publisher = TelemetryPublisher()
        await _publisher.start()
    return _publisher


async def shutdown_publisher() -> None:
    """Shutdown the global telemetry publisher."""
    global _publisher
    if _publisher is not None:
        await _publisher.stop()
        _publisher = None


def publish_trace(
    trace: Trace,
    token_usage: TokenUsage | None = None,
) -> None:
    """Convenience function to publish a trace using global publisher.

    Fire-and-forget. If publisher isn't started, trace is dropped with warning.

    Args:
        trace: The trace to publish.
        token_usage: Optional token usage data.
    """
    global _publisher
    if _publisher is not None:
        _publisher.publish(trace, token_usage)
    else:
        logger.warning(f"Telemetry publisher not started, dropping trace {trace.trace_id}")
