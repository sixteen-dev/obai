"""Configuration for OBaI agents.

Uses Pydantic settings for environment variable management.
All config classes consolidated here for single import.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


# Reasoning effort and output verbosity tiers used by ModelSettings on
# every agent. Two tiers (orchestrator + specialist) live as fields on
# AgentConfig below — same pattern as the model name fields.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
Verbosity = Literal["low", "medium", "high"]


# =============================================================================
# LangCache Semantic Caching Config
# =============================================================================


class CacheConfig(BaseSettings):
    """LangCache configuration for semantic query caching.

    Environment variables (prefix: LANGCACHE_):
        LANGCACHE_ENABLED: Enable/disable caching (default: false)
        LANGCACHE_SERVER_URL: Redis LangCache server URL
        LANGCACHE_API_KEY: LangCache API key
        LANGCACHE_CACHE_ID: Cache namespace (default: obai_query_cache)
        LANGCACHE_SIMILARITY_THRESHOLD: Min similarity for hit (default: 0.85)
    """

    model_config = SettingsConfigDict(
        env_prefix="LANGCACHE_",
        case_sensitive=False,
    )

    enabled: bool = True
    server_url: str = ""
    api_key: str = ""
    cache_id: str = ""
    similarity_threshold: float = 0.85

    @field_validator("similarity_threshold")
    @classmethod
    def validate_threshold(cls, v: float) -> float:
        """Validate similarity threshold is between 0 and 1."""
        if not 0.0 <= v <= 1.0:
            msg = f"similarity_threshold must be between 0.0 and 1.0, got {v}"
            raise ValueError(msg)
        return v

    def is_configured(self) -> bool:
        """Check if cache is enabled and properly configured."""
        return self.enabled and bool(self.server_url) and bool(self.api_key)


@lru_cache
def get_cache_config() -> CacheConfig:
    """Get cache configuration singleton."""
    return CacheConfig()


def reset_cache_config() -> None:
    """Reset cache config. Useful for testing."""
    get_cache_config.cache_clear()


# =============================================================================
# Agent Config
# =============================================================================


class AgentConfig(BaseSettings):
    """Configuration for OBaI agents.

    Attributes:
        openai_api_key: OpenAI API key for agent SDK.
        mcp_fundamentals_url: URL for fundamentals MCP server.
        mcp_market_data_url: URL for market data MCP server.
        mcp_events_news_url: URL for events/news MCP server.
        mcp_options_url: URL for options MCP server.
        mcp_screener_url: URL for stock screener MCP server.
        mcp_portfolio_url: URL for portfolio analysis MCP server.
        mcp_backtest_url: URL for backtest strategy MCP server.
        mcp_timeout: Timeout for MCP requests in seconds.
        mcp_max_retries: Maximum retries for MCP requests.
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str = Field(
        ...,
        description="OpenAI API key",
    )

    # Agent Models
    orchestrator_model: str = Field(
        default="gpt-5.5",
        description="Model for orchestrator agent (needs strong reasoning)",
    )
    specialist_model: str = Field(
        default="gpt-5-mini",
        description="Model for specialist agents (can reason about tool selection)",
    )
    market_data_model: str | None = Field(
        default=None,
        description="Override model for market data agent (uses specialist_model if None)",
    )
    fundamentals_model: str | None = Field(
        default=None,
        description="Override model for fundamentals agent (uses specialist_model if None)",
    )
    events_news_model: str | None = Field(
        default=None,
        description="Override model for events/news agent (uses specialist_model if None)",
    )
    options_model: str | None = Field(
        default=None,
        description="Override model for options agent (uses specialist_model if None)",
    )
    screener_model: str | None = Field(
        default=None,
        description="Override model for screener agent (uses specialist_model if None)",
    )
    portfolio_model: str | None = Field(
        default=None,
        description="Override model for portfolio agent (uses specialist_model if None)",
    )
    strategy_model: str | None = Field(
        default="gpt-5.1",
        description="Override model for strategy agent (uses orchestrator_model if None)",
    )
    research_model: str | None = Field(
        default=None,
        description="Override model for research agent (uses specialist_model if None)",
    )
    prediction_markets_model: str | None = Field(
        default=None,
        description="Override model for prediction markets agent (uses specialist_model if None)",
    )

    # Reasoning effort and output verbosity. Two tiers, same shape as the
    # model fields above. Defaults live in code; not exposed via .env.
    orchestrator_reasoning_effort: ReasoningEffort = Field(
        default="high",
        description="Hub reasoning effort: none|minimal|low|medium|high|xhigh",
    )
    orchestrator_verbosity: Verbosity = Field(
        default="low",
        description="Hub output verbosity: low|medium|high",
    )
    specialist_reasoning_effort: ReasoningEffort = Field(
        default="medium",
        description="Specialist reasoning effort: none|minimal|low|medium|high|xhigh",
    )
    specialist_verbosity: Verbosity = Field(
        default="low",
        description="Specialist output verbosity: low|medium|high",
    )

    # Guardrails
    enable_guardrails: bool = Field(
        default=True,
        description="Enable input guardrails to filter non-financial queries (cost optimization)",
    )

    # Strategy agent run-loop budget. Strategy turns commonly need
    # screener + fundamentals + multiple backtest iterations + an optional
    # walk-forward kickoff before composing the final memo, so the SDK
    # default of 10 is undersized.
    strategy_max_turns: int = Field(
        default=25,
        ge=5,
        le=100,
        description="Max turns for the strategy_analysis tool's inner Runner.run loop",
    )

    # MCP Server URLs
    mcp_fundamentals_url: str = Field(
        default="http://localhost:8001/mcp",
        description="Fundamentals MCP server URL",
    )
    mcp_market_data_url: str = Field(
        default="http://localhost:8002/mcp",
        description="Market data MCP server URL",
    )
    mcp_events_news_url: str = Field(
        default="http://localhost:8003/mcp",
        description="Events/news MCP server URL",
    )
    mcp_options_url: str = Field(
        default="http://localhost:8004/mcp",
        description="Options MCP server URL",
    )
    mcp_screener_url: str = Field(
        default="http://localhost:8005/mcp",
        description="Stock screener MCP server URL",
    )
    mcp_portfolio_url: str = Field(
        default="http://localhost:8006/mcp",
        description="Portfolio analysis MCP server URL",
    )
    mcp_backtest_url: str = Field(
        default="http://localhost:8007/mcp",
        description="Backtest strategy MCP server URL",
    )
    mcp_research_url: str = Field(
        default="http://localhost:8008/mcp",
        description="Research MCP server URL",
    )
    mcp_prediction_markets_url: str = Field(
        default="http://localhost:8009/mcp",
        description="Prediction markets MCP server URL",
    )

    # MCP Client Settings
    mcp_timeout: float = Field(
        default=30.0,
        ge=1.0,
        le=300.0,
        description="MCP request timeout in seconds",
    )
    mcp_max_retries: int = Field(
        default=2,
        ge=0,
        le=5,
        description="Maximum retries for MCP requests",
    )

    # Tool Call Cache
    tool_cache_ttl: int = Field(
        default=300,
        ge=10,
        le=600,
        description="TTL in seconds for tool call deduplication cache",
    )
    tool_cache_maxsize: int = Field(
        default=500,
        ge=100,
        le=5000,
        description="Max entries in tool call cache (LRU eviction)",
    )

    # Inline Scoring (faithfulness + completeness on every query)
    enable_inline_scoring: bool = Field(
        default=False,
        description="Run faithfulness and completeness scoring on every query in TUI/CLI",
    )

    # Evaluation Judge Models
    eval_judge_model: str = Field(
        default="claude-sonnet-4-5",
        description="Anthropic model for strong scorers (Faithfulness, Completeness, LLMJudge)",
    )
    eval_builtin_model: str = Field(
        default="anthropic/claude-haiku-4-5-20251001",
        description="LiteLLM model ID for Opik built-in scorers (AnswerRelevance, TaskCompletion)",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )

    # Opik Tracing (Self-Hosted)
    opik_enabled: bool = Field(
        default=True,
        description="Enable Opik tracing for eval data collection",
    )
    opik_project: str = Field(
        default="obai-eval",
        description="Opik project name (env: OPIK_OBAI_PROJECT_NAME)",
        validation_alias=AliasChoices("OPIK_OBAI_PROJECT_NAME", "opik_project"),
    )
    opik_url: str = Field(
        default="http://localhost:5173",
        description="Opik server URL (self-hosted)",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level is valid."""
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid_levels:
            msg = f"Invalid log level: {v}. Must be one of {valid_levels}"
            raise ValueError(msg)
        return v_upper

    def get_agent_model(self, agent_type: str) -> str:
        """Get model for specific agent type.

        Args:
            agent_type: Agent type (market_data, fundamentals, events_news, options, screener).

        Returns:
            Model name to use for this agent type.
        """
        # Check for agent-specific override
        override_field = f"{agent_type}_model"
        override_value: str | None = getattr(self, override_field, None)

        if override_value is not None:
            return override_value

        # Fall back to specialist model
        return self.specialist_model

    def get_strategy_model(self) -> str:
        """Get model for strategy agent.

        Strategy design requires strong reasoning (iterative backtesting,
        metric interpretation, overfitting detection), so it defaults to
        the orchestrator model rather than the specialist model.

        Returns:
            Model name to use for the strategy agent.
        """
        if self.strategy_model is not None:
            return self.strategy_model
        return self.orchestrator_model

    def configure_logging(self) -> None:
        """Configure logging based on settings."""
        logging.basicConfig(
            level=getattr(logging, self.log_level),
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        logger.debug("Logging configured at %s level", self.log_level)


# Global config instance
_config: AgentConfig | None = None


def get_config() -> AgentConfig:
    """Get global config instance (singleton pattern).

    Returns:
        Global AgentConfig instance.
    """
    global _config
    if _config is None:
        _config = AgentConfig()  # type: ignore[call-arg]
        _config.configure_logging()
        logger.debug("Agent configuration loaded")
    return _config


def reset_config() -> None:
    """Reset the global config instance.

    This is primarily for testing purposes to allow tests to override
    configuration without polluting global state between tests.

    Example:
        ```python
        def test_custom_config():
            # Reset before test
            reset_config()

            # Set custom env vars
            os.environ["SPECIALIST_MODEL"] = "gpt-4-turbo"

            # Get fresh config
            config = get_config()
            assert config.specialist_model == "gpt-4-turbo"

            # Reset after test
            reset_config()
        ```
    """
    global _config
    _config = None
    logger.debug("Config instance reset")
