"""Opik scorers for OBaI agent evaluation.

This module provides both Opik built-in metrics and custom OBaI-specific
scorers for evaluating multi-agent financial research queries.

Built-in scorers (from Opik):
    - AnswerRelevanceScorer: Rates response relevancy to query
    - TaskCompletionScorer: Assesses whether agent completed the task
    - ToolCorrectnessScorer: Assesses whether tools were used correctly

Custom scorers (OBaI-specific):
    - OutcomeContractScorer: Validates declared high-level result outcome
    - PartialRefusalSemanticScorer: Semantically validates scoped refusals
    - DatePolicyScorer: Validates explicit as-of disclosure for current cases
    - ToolOrchestrationScorer: Validates correct specialist agents called
    - SequenceScorer: Validates agent call order for dependency queries
    - StrategyContractScorer: Validates final strategy artifact structure
    - StrategyGroundingScorer: Validates final strategy artifact fidelity
    - StrategyDecisionScorer: LLM judge for strategy decision quality

LLM-judge scorers:
    - LLMJudgeScorer: Multi-dimensional rubric scoring (5 dimensions, 1-5 scale)

Ground-truth verification scorers:
    - FaithfulnessScorer: Two-phase (deterministic + LLM) against MCP responses
    - CompletenessScorer: LLM-based check for omitted relevant data
"""

from evaluation.scorers.builtin import (
    AnswerRelevanceScorer,
    TaskCompletionScorer,
    ToolCorrectnessScorer,
)
from evaluation.scorers.custom import (
    DatePolicyScorer,
    OutcomeContractScorer,
    PartialRefusalSemanticScorer,
    SequenceScorer,
    StrategyContractScorer,
    StrategyDecisionScorer,
    StrategyGroundingScorer,
    ToolOrchestrationScorer,
)
from evaluation.scorers.faithfulness import (
    CompletenessScorer,
    FaithfulnessScorer,
)
from evaluation.scorers.llm_judge import LLMJudgeScorer

__all__ = [
    # Built-in Opik scorers (class wrappers)
    "AnswerRelevanceScorer",
    "TaskCompletionScorer",
    "ToolCorrectnessScorer",
    # Custom OBaI scorers
    "OutcomeContractScorer",
    "DatePolicyScorer",
    "PartialRefusalSemanticScorer",
    "ToolOrchestrationScorer",
    "SequenceScorer",
    "StrategyContractScorer",
    "StrategyDecisionScorer",
    "StrategyGroundingScorer",
    # LLM-judge scorers
    "LLMJudgeScorer",
    # Ground-truth verification scorers
    "FaithfulnessScorer",
    "CompletenessScorer",
]
