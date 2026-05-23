"""Input guardrails for OBaI financial research agent.

Guardrails validate user queries before they reach the orchestrator,
preventing off-topic queries from consuming tokens and API credits.
"""

import asyncio
import logging
from typing import Any

from agents import Agent, Runner
from agents.guardrail import GuardrailFunctionOutput, input_guardrail
from pydantic import BaseModel, Field

from .config import get_config
from .prompt_loader import load_prompt

logger = logging.getLogger(__name__)


class FinancialQueryValidation(BaseModel):
    """Output model for financial query validation guardrail.

    Attributes:
        reasoning: Brief explanation of the validation decision.
        is_financial_query: True if query is financial/market-related, False otherwise.
    """

    reasoning: str = Field(description="Brief explanation of why this query is or isn't financial")
    is_financial_query: bool = Field(
        description="Whether the query is related to financial markets, stocks, or investing"
    )


async def create_guardrail_agent() -> Agent[None]:
    """Create a guardrail agent for validating financial queries.

    Model is sourced from ``AgentConfig.guardrail_model`` so deployments that
    retire a model family can switch via environment variables instead of a
    code change.

    Returns:
        OpenAI Agent SDK agent instance configured for guardrail validation.
    """
    model = get_config().guardrail_model

    # Load guardrail instructions
    instructions = load_prompt("guardrail")

    # Create agent with structured output (uses OPENAI_API_KEY env var)
    agent = Agent(
        name="obai_financial_query_guardrail",
        instructions=instructions,
        model=model,
        output_type=FinancialQueryValidation,
    )

    logger.info(f"Guardrail agent created with model: {model}")
    return agent


def _extract_text_from_item(item: Any) -> str:
    """Extract text content from a single message item.

    Args:
        item: A message item (dict, object with content/text attr, etc.)

    Returns:
        Extracted text, or empty string if no text found.
    """
    content = None
    if hasattr(item, "content"):
        content = item.content
    elif hasattr(item, "text"):
        content = item.text
    elif isinstance(item, dict):
        content = item.get("content") or item.get("text")

    if content is None:
        return ""

    if isinstance(content, str):
        return content

    # Content can be a list of content parts
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if hasattr(part, "text"):
                parts.append(part.text)
            elif isinstance(part, dict) and "text" in part:
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts)

    return ""


def _extract_latest_user_query(input_data: str | list[Any]) -> str:
    """Extract the latest user query with minimal context for guardrail.

    When sessions are active, input_data is [session_history..., new_user_msg].
    We send the last 2 user messages so the guardrail can resolve pronoun
    references ("it", "that stock") without being confused by assistant
    responses containing financial data.

    Args:
        input_data: Raw input string or list of message items from Agent SDK.

    Returns:
        The latest user query, optionally prefixed with prior context.
    """
    if isinstance(input_data, str):
        return input_data

    if not input_data:
        return ""

    # Collect last 2 user messages (newest first) for pronoun resolution.
    user_msgs: list[str] = []
    for item in reversed(input_data):
        role = None
        if isinstance(item, dict):
            role = item.get("role")
        elif hasattr(item, "role"):
            role = item.role

        if role == "user":
            text = _extract_text_from_item(item)
            if text:
                user_msgs.append(text)
                if len(user_msgs) == 2:  # noqa: PLR2004
                    break

    if not user_msgs:
        return _extract_text_from_item(input_data[-1])

    if len(user_msgs) == 1:
        return user_msgs[0]

    # Format with context so guardrail can resolve "it", "that stock", etc.
    return f"[Previous: {user_msgs[1]}]\nCurrent query: {user_msgs[0]}"


def create_input_guardrail() -> Any:
    """Create input guardrail function for OBaI orchestrator.

    This guardrail validates that user queries are financial/market-related
    before they reach the orchestrator agent.

    Thread-safe initialization using asyncio.Lock to prevent race conditions
    when multiple requests arrive simultaneously.

    Returns:
        Decorated guardrail function that can be attached to orchestrator.

    Example:
        ```python
        from core_agents.guardrails import create_input_guardrail

        financial_guardrail = create_input_guardrail()

        orchestrator = Agent(
            name="orchestrator",
            input_guardrails=[financial_guardrail],
            ...
        )
        ```
    """
    # Thread-safe initialization state
    guardrail_agent: Agent[None] | None = None
    init_lock = asyncio.Lock()

    @input_guardrail(run_in_parallel=False)
    async def financial_query_guardrail(
        context: Any,  # RunContextWrapper
        agent: Any,  # Agent
        input_data: str | list[Any],  # str or list[TResponseInputItem]
    ) -> Any:  # GuardrailFunctionOutput
        """Validate that input is a financial/market-related query.

        Only validates the LATEST user message, not the full session history.
        Runs sequentially (before agent starts) to avoid wasting tokens on
        rejected queries.

        Args:
            context: Run context wrapper from Agent SDK.
            agent: The agent being guarded (orchestrator).
            input_data: User input string or message list.

        Returns:
            GuardrailFunctionOutput with validation result.
                - If is_financial_query=True: Allow query to proceed
                - If is_financial_query=False: Trigger tripwire (reject query)
        """
        nonlocal guardrail_agent

        # Thread-safe initialization using double-checked locking pattern
        if guardrail_agent is None:
            async with init_lock:
                # Check again inside lock (another coroutine may have initialized it)
                if guardrail_agent is None:
                    guardrail_agent = await create_guardrail_agent()
                    logger.info("Guardrail agent initialized (thread-safe)")

        # Extract ONLY the latest user message, not full session history.
        # When sessions are active, input_data is [history..., new_user_msg].
        # Validating the full history would mix old financial responses with
        # the new query, causing incorrect guardrail decisions.
        query_text = _extract_latest_user_query(input_data)

        query_preview = query_text[:100]
        logger.debug(f"Guardrail input: {type(input_data)}, query: {query_preview}...")

        # Run validation
        result = await Runner.run(
            guardrail_agent,
            query_text,
            context=context.context if hasattr(context, "context") else None,
        )

        # Get structured output
        validation = result.final_output_as(FinancialQueryValidation)

        logger.info(
            f"Guardrail result: is_financial={validation.is_financial_query}, "
            f"reasoning={validation.reasoning}"
        )

        # Return guardrail result
        # tripwire_triggered=True will reject the query
        return GuardrailFunctionOutput(
            output_info=validation,
            tripwire_triggered=not validation.is_financial_query,
        )

    return financial_query_guardrail


def get_rejection_message(validation: FinancialQueryValidation) -> str:
    """Generate user-friendly rejection message for non-financial queries.

    Args:
        validation: The validation result from guardrail.

    Returns:
        Friendly rejection message explaining why the query was rejected.
    """
    return (
        f"Sorry, I can only help with stock market research and financial analysis.\n\n"
        f"Your query appears to be: {validation.reasoning}\n\n"
        f"I can help you with:\n"
        f"• Stock prices, quotes, and trading data\n"
        f"• Company fundamentals and financial statements\n"
        f"• Earnings reports, news, and dividends\n"
        f"• Options chains and Greeks\n"
        f"• Technical analysis and market trends\n"
        f"• Trading strategy design and backtesting\n"
        f"• Market movers and sector performance\n\n"
        f"Please ask a finance-related question!"
    )
