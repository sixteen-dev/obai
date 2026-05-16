"""Utility helpers for market-data-server.

Provides a lightweight async retry decorator that mirrors the helpers used by
the other FMP-backed services. Centralizing the same shape keeps retry
behavior consistent across services without pulling in a heavy dependency.
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
    """Return True for transient httpx errors that should be retried.

    Retries cover 5xx responses, 429 rate-limit responses, and the
    timeout/network families. 4xx other than 429 are not retried — those are
    client errors that won't improve by trying again.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            status = exc.response.status_code
        except Exception:
            return False
        return status == 429 or 500 <= status < 600
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
    """Async retry decorator with exponential backoff and jitter."""

    def decorator(func: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @wraps(func)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            attempt = 1
            while True:
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 - delegated to predicates
                    if not isinstance(exc, retry_on):
                        raise
                    if retry_if is not None and not retry_if(exc):
                        raise
                    if attempt >= max_attempts:
                        raise
                    delay = initial_delay * (backoff ** (attempt - 1)) + random.uniform(
                        0, jitter
                    )
                    await asyncio.sleep(delay)
                    attempt += 1

        return wrapper

    return decorator
