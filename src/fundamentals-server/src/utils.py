"""Utility helpers for fundamentals-server.

Includes a lightweight async retry decorator with exponential backoff.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

import httpx

P = ParamSpec("P")
R = TypeVar("R")


def is_retryable_httpx_exc(exc: Exception) -> bool:
    """Return True if an httpx exception is retryable.

    Retries on:
    - 5xx HTTP responses
    - Network/timeout errors
    """
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            status = exc.response.status_code
            return 500 <= status < 600
        except Exception:
            return False
    return isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))


def retry_async(
    *,
    max_attempts: int = 3,
    initial_delay: float = 0.5,
    backoff: float = 2.0,
    jitter: float = 0.25,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    retry_if: Callable[[Exception], bool] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Async retry decorator with exponential backoff and jitter.

    Args:
        max_attempts: Maximum number of attempts
        initial_delay: Initial backoff delay (seconds)
        backoff: Exponential multiplier
        jitter: Random jitter upper bound to add to delay
        retry_on: Exception types to consider for retry
        retry_if: Optional predicate for more granular retry checks
    """

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            attempt = 1
            last_exc: Exception | None = None
            while attempt <= max_attempts:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:  # noqa: BLE001 - intentional broad catch
                    # Not a retryable type
                    if not isinstance(e, retry_on):
                        raise
                    # Predicate denies retry
                    if retry_if is not None and not retry_if(e):
                        raise
                    # Attempts exhausted
                    if attempt >= max_attempts:
                        raise
                    # Sleep with backoff + jitter, then retry
                    delay = initial_delay * (backoff ** (attempt - 1)) + random.uniform(0, jitter)
                    await asyncio.sleep(delay)
                    last_exc = e
                    attempt += 1
            # Should not reach here; re-raise last exception
            if last_exc is not None:
                raise last_exc
            # Fallback (typing completeness)
            return await func(*args, **kwargs)

        return wrapper

    return decorator
