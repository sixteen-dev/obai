"""Pydantic models for DynamoDB telemetry storage.

These models define the structure of trace items stored in DynamoDB.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from evaluation.trace.types import Trace


class TokenUsage(BaseModel):
    """Token usage from Agent SDK."""

    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class TraceItem(BaseModel):
    """DynamoDB item for a conversation trace.

    Designed for efficient querying via GSIs for evaluation.
    """

    # DynamoDB Keys
    pk: str  # SESSION#{session_id} or TRACE#{trace_id}
    sk: str  # TRACE#{timestamp}#{trace_id}
    trace_id: str
    session_id: str | None = None
    timestamp: str  # ISO8601
    date: str  # YYYY-MM-DD for daily GSI

    # Input
    query: str
    model: str

    # Output
    final_response: str | None = None
    guardrail_passed: bool | None = None

    # Tool calls (denormalized)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    total_tool_calls: int = 0
    unique_tools: list[str] = Field(default_factory=list)
    redundant_calls: int = 0

    # Agents
    specialists_called: list[str] = Field(default_factory=list)
    call_sequence: list[str] = Field(default_factory=list)

    # Timing
    total_latency_ms: float = 0.0
    guardrail_latency_ms: float | None = None
    timing_breakdown: dict[str, float] = Field(default_factory=dict)

    # Status
    status: str = "success"  # success | error | guardrail_rejected
    error_type: str | None = None
    error_message: str | None = None

    # Token usage
    token_usage: TokenUsage | None = None
    estimated_cost_usd: float | None = None

    # Evaluation (for future human feedback)
    evaluation_score: float | None = None
    evaluation_notes: str | None = None

    # Metadata
    environment: str = "dev"

    # TTL for auto-cleanup (Unix timestamp)
    ttl: int | None = None

    @classmethod
    def from_trace(
        cls,
        trace: Trace,
        token_usage: TokenUsage | None = None,
        environment: str = "dev",
        ttl_days: int = 90,
    ) -> TraceItem:
        """Convert a Trace object to a DynamoDB TraceItem.

        Args:
            trace: The trace object from TraceCapture.
            token_usage: Optional token usage from Agent SDK.
            environment: Environment name (dev/prod).
            ttl_days: Days until auto-deletion.

        Returns:
            TraceItem ready for DynamoDB storage.
        """
        # Generate keys
        timestamp_str = trace.start_time.isoformat()
        date_str = trace.start_time.strftime("%Y-%m-%d")

        pk = f"SESSION#{trace.session_id}" if trace.session_id else f"TRACE#{trace.trace_id}"

        sk = f"TRACE#{timestamp_str}#{trace.trace_id}"

        # Determine status
        status = "success"
        error_type = None
        error_message = None

        if trace.guardrail_passed is False:
            status = "guardrail_rejected"
        else:
            # Check for errors in trace
            from evaluation.trace.types import ErrorEvent

            for event in trace.events:
                if isinstance(event, ErrorEvent):
                    status = "error"
                    error_type = event.error_type
                    error_message = event.error_message
                    break

        # Convert tool calls to dicts
        tool_calls_data = [
            {
                "tool_name": tc.tool_name,
                "args": tc.args,
                "response": tc.response,
                "error": tc.error,
                "latency_ms": tc.latency_ms,
                "agent_name": tc.agent_name,
                "timestamp": tc.timestamp.isoformat(),
            }
            for tc in trace.tool_calls
        ]

        # Build timing breakdown
        timing_breakdown: dict[str, float] = {}
        if trace.metrics.timing:
            timing_breakdown.update(trace.metrics.timing.specialist_breakdown)
            timing_breakdown.update(trace.metrics.timing.tool_breakdown)

        # Calculate TTL
        ttl_timestamp = None
        if ttl_days > 0:
            ttl_timestamp = int(time.time()) + (ttl_days * 24 * 60 * 60)

        # Estimate cost if we have token usage
        estimated_cost = None
        if token_usage:
            estimated_cost = cls._estimate_cost(token_usage, trace.model)

        return cls(
            pk=pk,
            sk=sk,
            trace_id=trace.trace_id,
            session_id=trace.session_id,
            timestamp=timestamp_str,
            date=date_str,
            query=trace.query,
            model=trace.model,
            final_response=trace.final_response,
            guardrail_passed=trace.guardrail_passed,
            tool_calls=tool_calls_data,
            total_tool_calls=trace.metrics.total_tool_calls,
            unique_tools=trace.metrics.unique_tools,
            redundant_calls=trace.metrics.redundant_calls,
            specialists_called=trace.metrics.specialists_called,
            call_sequence=trace.metrics.call_sequence,
            total_latency_ms=(trace.metrics.timing.total_ms if trace.metrics.timing else 0.0),
            guardrail_latency_ms=(
                trace.metrics.timing.guardrail_ms if trace.metrics.timing else None
            ),
            timing_breakdown=timing_breakdown,
            status=status,
            error_type=error_type,
            error_message=error_message,
            token_usage=token_usage,
            estimated_cost_usd=estimated_cost,
            environment=environment,
            ttl=ttl_timestamp,
        )

    @staticmethod
    def _estimate_cost(usage: TokenUsage, model: str) -> float:
        """Estimate cost based on token usage and model.

        Pricing as of Jan 2025 (approximate, check OpenAI for current):
        - gpt-4o: $2.50/1M input, $10/1M output
        - gpt-4o-mini: $0.15/1M input, $0.60/1M output
        - gpt-4-turbo: $10/1M input, $30/1M output

        Args:
            usage: Token usage data.
            model: Model name.

        Returns:
            Estimated cost in USD.
        """
        # Pricing per 1M tokens (input, output)
        pricing: dict[str, tuple[float, float]] = {
            "gpt-4o": (2.50, 10.00),
            "gpt-4o-mini": (0.15, 0.60),
            "gpt-4-turbo": (10.00, 30.00),
            "gpt-4": (30.00, 60.00),
            "gpt-3.5-turbo": (0.50, 1.50),
        }

        # Find matching pricing (partial match)
        input_price, output_price = 2.50, 10.00  # Default to gpt-4o
        for model_prefix, prices in pricing.items():
            if model_prefix in model.lower():
                input_price, output_price = prices
                break

        input_cost = (usage.input_tokens / 1_000_000) * input_price
        output_cost = (usage.output_tokens / 1_000_000) * output_price

        return round(input_cost + output_cost, 6)

    def to_dynamo_item(self) -> dict[str, Any]:
        """Convert to DynamoDB item format.

        DynamoDB requires Decimal instead of float, so we recursively
        convert all float values.

        Returns:
            Dict suitable for boto3 put_item.
        """
        data = self.model_dump(exclude_none=True)

        # Convert nested models to dicts
        if "token_usage" in data and data["token_usage"]:
            data["token_usage"] = dict(data["token_usage"])

        # DynamoDB doesn't support float - convert to Decimal
        return cast(dict[str, Any], self._convert_floats_to_decimal(data))

    @staticmethod
    def _convert_floats_to_decimal(obj: Any) -> Any:
        """Recursively convert float values to Decimal for DynamoDB.

        Args:
            obj: Any Python object (dict, list, or primitive).

        Returns:
            Same structure with floats converted to Decimal.
        """
        if isinstance(obj, float):
            return Decimal(str(obj))
        if isinstance(obj, dict):
            return {k: TraceItem._convert_floats_to_decimal(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [TraceItem._convert_floats_to_decimal(item) for item in obj]
        return obj
