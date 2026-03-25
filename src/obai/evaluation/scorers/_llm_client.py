"""Shared LLM client for evaluation scorers.

Provides structured completion via AsyncOpenAI pointed at Anthropic's
OpenAI-compatible endpoint. Replaces the previous litellm dependency.

Anthropic's OpenAI-compatible endpoint ignores ``response_format``, so
structured output is achieved by injecting the JSON schema into the
system prompt and parsing the response with Pydantic.
"""

from __future__ import annotations

import functools
import json
import os
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_JUDGE_MODEL = "claude-sonnet-4-5"
_MAX_TOKENS = 4096


@functools.lru_cache(maxsize=1)
def _get_client() -> AsyncOpenAI:
    """Return a singleton AsyncOpenAI client for Anthropic's API.

    The client is created once and reused across all scorer calls,
    enabling HTTP connection pooling and Anthropic's automatic
    server-side prompt caching for repeated system prompts.

    Returns:
        Configured AsyncOpenAI client (cached singleton).

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not set.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        msg = "ANTHROPIC_API_KEY environment variable required for evaluation"
        raise ValueError(msg)
    return AsyncOpenAI(api_key=api_key, base_url="https://api.anthropic.com/v1/")


def _strip_fences(text: str) -> str:
    """Remove markdown code fences from model response.

    Args:
        text: Raw model output that may contain fenced JSON.

    Returns:
        Cleaned text with fences removed.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return text


async def structured_completion(
    *,
    model: str,
    system: str,
    user: str,
    response_model: type[T],
    temperature: float | None = None,
) -> T:
    """Chat completion expecting structured JSON output via Anthropic.

    Appends the Pydantic model's JSON schema to the system prompt so the
    model knows the exact shape to produce.

    Args:
        model: Anthropic model ID (e.g. "claude-sonnet-4-5").
        system: System prompt content.
        user: User prompt content.
        response_model: Pydantic model class for the expected response.
        temperature: Sampling temperature (omitted if None).

    Returns:
        Parsed instance of response_model.

    Raises:
        pydantic.ValidationError: If model output doesn't match schema.
        openai.APIError: On API communication errors.
    """
    client = _get_client()
    schema_json = json.dumps(response_model.model_json_schema(), indent=2)
    system_with_schema = (
        f"{system}\n\n"
        "Respond ONLY with valid JSON (no markdown fences, no extra text) "
        f"matching this schema:\n{schema_json}"
    )

    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_with_schema},
            {"role": "user", "content": user},
        ],
        "max_tokens": _MAX_TOKENS,
    }
    if temperature is not None:
        create_kwargs["temperature"] = temperature

    response = await client.chat.completions.create(**create_kwargs)
    content = response.choices[0].message.content or ""
    return response_model.model_validate_json(_strip_fences(content))
