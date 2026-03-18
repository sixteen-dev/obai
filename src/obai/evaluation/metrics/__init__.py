"""Custom metrics module for OBaI testing.

These metrics are computed from trace data and are NOT part of OpenAI Evals.
OpenAI Evals handles grading; this module handles agent-specific metrics.
"""

from evaluation.metrics.sequencing import validate_sequence

__all__ = ["validate_sequence"]
