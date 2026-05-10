"""Opik initialization for agent tracing.

Uses the OpikTracingProcessor for OpenAI Agents SDK integration,
which provides automatic tracing of all agent spans to the Opik UI.

Environment Variables (via AgentConfig):
    OPIK_ENABLED: Enable/disable Opik tracing (default: true)
    OPIK_OBAI_PROJECT_NAME: Opik project name (default: obai-eval)
    OPIK_URL: Opik server URL (default: http://localhost:5173)
"""

from __future__ import annotations

import logging
import os
import sys
import urllib.error
import urllib.request

from agents import set_trace_processors

from core_agents.config import get_config
from core_agents.prompt_manager import sync_prompts_to_opik

logger = logging.getLogger(__name__)

# Track initialization state
_opik_initialized = False


def init_opik() -> bool:
    """Initialize Opik tracing with explicit processor.

    Uses OpikTracingProcessor to hook into the OpenAI Agents SDK,
    automatically capturing all agent spans to the Opik UI.

    Call this once at application startup, before creating any agents.

    Returns:
        True if Opik was initialized, False if disabled via config.

    Example:
        ```python
        from core_agents.tracing import init_opik

        # At app startup
        if init_opik():
            print("Opik tracing enabled")

        # Now create and run agents - they're automatically traced
        hub = await create_central_hub()
        ```
    """
    global _opik_initialized  # noqa: PLW0603

    if _opik_initialized:
        logger.debug("Opik already initialized")
        return True

    config = get_config()

    if not config.opik_enabled:
        logger.info("Opik tracing disabled via OPIK_ENABLED=false")
        return False

    # Lazy-import opik so the module loads even when opik is not installed.
    # opik is an optional dep ([project.optional-dependencies] tracing).
    try:
        import opik
        from opik.integrations.openai.agents import OpikTracingProcessor
    except ImportError:
        logger.debug("opik not installed — tracing disabled")
        return False

    # Configure Opik for self-hosted instance with explicit URL.
    # use_local=True is required — without it, Opik demands a Comet API key.
    # We also set OPIK_URL_OVERRIDE env var as a belt-and-suspenders fallback,
    # since opik.configure(use_local=True) reads ~/.opik.config which may not exist.
    opik_url = config.opik_url.rstrip("/")
    api_url = f"{opik_url}/api"
    os.environ.setdefault("OPIK_URL_OVERRIDE", api_url)

    # Fast health check (2s timeout) before calling opik.configure(),
    # which has a ~16s internal timeout when the server is unreachable.
    try:
        req = urllib.request.Request(f"{opik_url}/health", method="GET")  # noqa: S310
        urllib.request.urlopen(req, timeout=2)  # noqa: S310
    except (urllib.error.URLError, TimeoutError, OSError):
        logger.warning(
            "Opik server unreachable at %s — tracing disabled. "
            "Start with: docker compose -f infra/opik/docker-compose.yml up -d",
            opik_url,
        )
        return False

    # Suppress Opik's direct prints ("OPIK: Configuration completed...",
    # "OPIK: Started logging traces...") and its own logger noise.
    # These bypass Python logging and write directly to stderr/stdout.
    opik_logger = logging.getLogger("opik")
    _prev_opik_level = opik_logger.level
    opik_logger.setLevel(logging.WARNING)

    _saved_stderr = sys.stderr
    sys.stderr = open(os.devnull, "w")  # noqa: SIM115, PTH123
    try:
        opik.configure(use_local=True, automatic_approvals=True)
    except Exception:
        sys.stderr.close()
        sys.stderr = _saved_stderr
        opik_logger.setLevel(_prev_opik_level)
        logger.warning(
            "Opik configure failed — is Opik running at %s? "
            "Tracing will be disabled. Start with: "
            "docker compose -f infra/opik/docker-compose.yml up -d",
            opik_url,
        )
        return False
    finally:
        if sys.stderr is not _saved_stderr:
            sys.stderr.close()
            sys.stderr = _saved_stderr

    logger.info("Opik configured with URL: %s", api_url)

    # Use explicit processor for OpenAI Agents SDK integration
    set_trace_processors([OpikTracingProcessor()])

    _opik_initialized = True
    logger.info("Opik tracing initialized for project: %s", config.opik_project)

    # Background: prompt sync is not needed for startup.
    import threading

    threading.Thread(target=sync_prompts_to_opik, daemon=True).start()

    return True


def is_opik_enabled() -> bool:
    """Check if Opik tracing is currently active.

    Returns:
        True if Opik was initialized and is active.
    """
    return _opik_initialized
