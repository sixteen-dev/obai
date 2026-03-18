"""Telemetry decorator for automatic trace capture.

Provides a decorator that wraps agent execution to automatically
capture and publish traces without modifying client code.

Usage:
    ```python
    from core_agents.telemetry.decorator import traced, TelemetryContext

    # Option 1: Decorator on async generator
    @traced
    async def run_query(hub, query, session):
        result = Runner.run_streamed(hub.agent, query, session=session)
        async for event in result.stream_events():
            yield event

    # Option 2: Context manager for more control
    async with TelemetryContext(query, model, session_id) as ctx:
        result = Runner.run_streamed(agent, query, session=session)
        async for event in result.stream_events():
            ctx.process_event(event)
            # handle event...
        ctx.set_token_usage(result.context_wrapper.usage)
    ```
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, ParamSpec, TypeVar

from core_agents.telemetry.config import get_telemetry_config
from core_agents.telemetry.models import TokenUsage
from core_agents.telemetry.publisher import TelemetryPublisher

if TYPE_CHECKING:
    from evaluation.trace.capture import TraceCapture

logger = logging.getLogger(__name__)

P = ParamSpec("P")
T = TypeVar("T")


# Module-level publisher (lazily initialized)
_publisher: TelemetryPublisher | None = None
_publisher_lock = asyncio.Lock()


async def _get_publisher() -> TelemetryPublisher | None:
    """Get or create the module-level telemetry publisher."""
    global _publisher
    if _publisher is not None:
        return _publisher

    async with _publisher_lock:
        if _publisher is not None:
            return _publisher

        config = get_telemetry_config()
        if not config.enabled:
            return None

        _publisher = TelemetryPublisher(config)
        try:
            await _publisher.start()
            logger.info(f"Telemetry publisher started (table: {config.table_name})")
        except Exception as e:
            logger.warning(f"Failed to start telemetry publisher: {e}")
            _publisher = None

    return _publisher


async def shutdown_telemetry() -> None:
    """Shutdown the module-level telemetry publisher.

    Call this on application shutdown for graceful cleanup.
    """
    global _publisher
    if _publisher is not None:
        await _publisher.stop()
        _publisher = None


class TelemetryContext:
    """Context manager for manual telemetry capture.

    Use this when you need more control over the capture process.

    Example:
        ```python
        async with TelemetryContext(query, model, session_id) as ctx:
            result = Runner.run_streamed(agent, query, session=session)
            async for event in result.stream_events():
                ctx.process_event(event)
                # handle event in your code...

            # After streaming completes, set token usage
            if hasattr(result, 'context_wrapper'):
                ctx.set_token_usage_from_result(result)
        # Trace is automatically published on exit
        ```
    """

    def __init__(
        self,
        query: str,
        model: str,
        session_id: str | None = None,
    ) -> None:
        """Initialize telemetry context.

        Args:
            query: The user query being processed.
            model: Model name for the agent.
            session_id: Optional session ID.
        """
        self.query = query
        self.model = model
        self.session_id = session_id
        self._capture: TraceCapture | None = None
        self._token_usage: TokenUsage | None = None
        self._publisher: TelemetryPublisher | None = None

    async def __aenter__(self) -> TelemetryContext:
        """Enter the context and start trace capture."""
        # Import here to avoid circular imports
        from evaluation.trace.capture import TraceCapture

        self._publisher = await _get_publisher()

        if self._publisher:
            self._capture = TraceCapture(
                query=self.query,
                model=self.model,
                session_id=self.session_id,
            )
            self._capture.start()

        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit the context and publish the trace."""
        if self._capture and self._publisher:
            # Record error if exception occurred
            if exc_val is not None:
                error_name = type(exc_val).__name__
                # Check if it's a guardrail rejection
                if "InputGuardrailTripwireTriggered" in error_name:
                    self._capture.record_guardrail(
                        passed=False,
                        classification="off_topic",
                        rejection_reason=str(exc_val),
                    )
                else:
                    self._capture.record_error(
                        error_type=error_name,
                        error_message=str(exc_val),
                    )

            # Attach raw MCP outputs from specialist inner calls
            try:
                from core_agents.central_hub_agent import get_inner_tool_outputs

                self._capture.set_inner_tool_outputs(get_inner_tool_outputs())
            except ImportError:
                pass

            # Finalize and publish
            trace = self._capture.finalize()
            self._publisher.publish(trace, self._token_usage)
            logger.debug(f"Published trace {trace.trace_id}")

    def process_event(self, event: Any) -> None:
        """Process an SDK streaming event for trace capture.

        Args:
            event: Agent SDK streaming event.
        """
        if self._capture:
            self._capture.process_sdk_event(event)

    def set_token_usage(self, usage: TokenUsage) -> None:
        """Set token usage manually.

        Args:
            usage: Token usage data.
        """
        self._token_usage = usage

    def set_token_usage_from_result(self, result: Any) -> None:
        """Extract and set token usage from Runner result.

        Args:
            result: The result from Runner.run_streamed() after iteration.
        """
        context_wrapper = getattr(result, "context_wrapper", None)
        if context_wrapper:
            usage = getattr(context_wrapper, "usage", None)
            if usage:
                self._token_usage = TokenUsage(
                    requests=getattr(usage, "requests", 0),
                    input_tokens=getattr(usage, "input_tokens", 0),
                    output_tokens=getattr(usage, "output_tokens", 0),
                    total_tokens=getattr(usage, "total_tokens", 0),
                )

    def record_guardrail(
        self,
        passed: bool,
        classification: str | None = None,
        rejection_reason: str | None = None,
    ) -> None:
        """Record guardrail check result.

        Args:
            passed: Whether guardrail passed.
            classification: Query classification.
            rejection_reason: Reason for rejection if not passed.
        """
        if self._capture:
            self._capture.record_guardrail(
                passed=passed,
                classification=classification,
                rejection_reason=rejection_reason,
            )


def traced(
    model: str | None = None,
    session_id_param: str = "session",
    query_param: str = "query",
) -> Callable[[Callable[P, AsyncIterator[T]]], Callable[P, AsyncIterator[T]]]:
    """Decorator for automatic telemetry capture on async generators.

    Wraps an async generator function that yields Agent SDK events.
    Automatically captures all events and publishes trace on completion.

    Args:
        model: Model name override. If None, tries to get from config.
        session_id_param: Name of the session parameter to extract session_id from.
        query_param: Name of the query parameter.

    Returns:
        Decorated async generator function.

    Example:
        ```python
        @traced(model="gpt-4o")
        async def run_agent(query: str, session: Session):
            result = Runner.run_streamed(hub.agent, query, session=session)
            async for event in result.stream_events():
                yield event

        # The trace is automatically captured and published
        async for event in run_agent("What is AAPL?", session):
            # handle event
            pass
        ```
    """

    def decorator(
        func: Callable[P, AsyncIterator[T]],
    ) -> Callable[P, AsyncIterator[T]]:
        @functools.wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> AsyncIterator[T]:
            # Extract query from kwargs or positional args
            # Handle both functions and methods (where args[0] is self)
            query = kwargs.get(query_param)
            if not query and args:
                # For methods, args[0] is self, args[1] is query
                # For functions, args[0] is query
                # Check if first arg is a string (query) or object (self)
                if isinstance(args[0], str):
                    query = args[0]
                elif len(args) > 1 and isinstance(args[1], str):
                    query = args[1]
                else:
                    query = "unknown"

            # Extract session_id from session parameter
            # Session can be in kwargs or as positional arg (for methods: args[2])
            session_id = None
            session = kwargs.get(session_id_param)
            if not session and len(args) > 2:
                # For methods: args = (self, query, session)
                session = args[2]
            if session:
                session_id = session.session_id

            # Get model from config if not provided
            nonlocal model
            actual_model = model
            if actual_model is None:
                try:
                    from core_agents.config import get_config

                    actual_model = get_config().orchestrator_model
                except Exception:
                    actual_model = "unknown"

            # Import here to avoid circular imports
            from evaluation.trace.capture import TraceCapture

            publisher = await _get_publisher()
            capture: TraceCapture | None = None
            token_usage: TokenUsage | None = None

            if publisher:
                capture = TraceCapture(
                    query=str(query),
                    model=actual_model,
                    session_id=str(session_id) if session_id else None,
                )
                capture.start()

            try:
                async for event in func(*args, **kwargs):
                    # Process event for telemetry
                    if capture:
                        capture.process_sdk_event(event)

                    # Yield to caller
                    yield event

            except Exception as e:
                # Record error in trace
                if capture:
                    error_name = type(e).__name__
                    if "InputGuardrailTripwireTriggered" in error_name:
                        capture.record_guardrail(
                            passed=False,
                            classification="off_topic",
                            rejection_reason=str(e),
                        )
                    else:
                        capture.record_error(
                            error_type=error_name,
                            error_message=str(e),
                        )
                raise

            finally:
                # Finalize and publish trace
                if capture and publisher:
                    # Attach raw MCP outputs from specialist inner calls
                    try:
                        from core_agents.central_hub_agent import (
                            get_inner_tool_outputs,
                        )

                        capture.set_inner_tool_outputs(get_inner_tool_outputs())
                    except ImportError:
                        pass

                    trace = capture.finalize()
                    publisher.publish(trace, token_usage)
                    logger.debug(f"Published trace {trace.trace_id}")

        return wrapper

    return decorator


@asynccontextmanager
async def telemetry_scope(
    query: str,
    model: str,
    session_id: str | None = None,
) -> AsyncIterator[TelemetryContext]:
    """Async context manager for telemetry capture.

    Convenience wrapper around TelemetryContext.

    Args:
        query: User query.
        model: Model name.
        session_id: Optional session ID.

    Yields:
        TelemetryContext for processing events.

    Example:
        ```python
        async with telemetry_scope(query, model, session_id) as ctx:
            result = Runner.run_streamed(agent, query, session=session)
            async for event in result.stream_events():
                ctx.process_event(event)
                # handle event...
            ctx.set_token_usage_from_result(result)
        ```
    """
    ctx = TelemetryContext(query, model, session_id)
    async with ctx:
        yield ctx
