"""OBaI Evaluation Framework.

A hybrid evaluation framework combining:
- Custom trace capture for agent-specific data (tool sequences, timing, efficiency)
- Opik metrics for scoring (built-in + custom scorers)

Usage:
    # CLI - run single query evaluation
    python -m evaluation evaluate "What is AAPL trading at?"

    # CLI - run test suite
    python -m evaluation evaluate --suite

    # Programmatic
    from evaluation import TraceCapture, Trace

    capture = TraceCapture(query="What is AAPL?", model="gpt-5.6-sol")
    capture.start()
    # ... process events ...
    trace = capture.finalize()
"""

from evaluation.trace.capture import TraceCapture
from evaluation.trace.types import (
    AgentEvent,
    ToolCallEvent,
    Trace,
    TraceEvent,
)

__all__ = [
    "TraceCapture",
    "Trace",
    "TraceEvent",
    "ToolCallEvent",
    "AgentEvent",
]
