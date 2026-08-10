"""Unit tests for configuration management.

Tests config loading, validation, and the reset function.
"""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from core_agents import hub_settings as hub_settings_module
from core_agents.config import AgentConfig, get_config, reset_config
from core_agents.hub_settings import HubSettings, HubSettingsStore


@pytest.fixture(autouse=True)
def setup_env(  # type: ignore[misc]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Set required environment variables and reset config for all tests.

    Also points the hub settings file at an empty tmp_path so these tests
    read shipped defaults rather than whatever the developer running them
    happens to have selected in ~/.obai/settings.json.
    """
    monkeypatch.setattr(
        hub_settings_module,
        "default_hub_settings_path",
        lambda: tmp_path / "settings.json",
    )

    # Save and clear any model environment variables
    saved_env: dict[str, str] = {}
    model_vars = [
        "ORCHESTRATOR_MODEL",
        "SPECIALIST_MODEL",
        "MARKET_DATA_MODEL",
        "FUNDAMENTALS_MODEL",
        "EVENTS_NEWS_MODEL",
        "OPTIONS_MODEL",
        "CRYPTO_MODEL",
        "LOG_LEVEL",
        "ENABLE_GUARDRAILS",
        "ORCHESTRATOR_REASONING_EFFORT",
        "STRATEGY_REASONING_EFFORT",
    ]
    for var in model_vars:
        if var in os.environ:
            saved_env[var] = os.environ.pop(var)

    os.environ["OPENAI_API_KEY"] = "test-key"
    reset_config()  # Reset before test
    yield
    reset_config()  # Clean up after test

    # Restore saved environment variables
    for var, value in saved_env.items():
        os.environ[var] = value


class TestAgentConfig:
    """Test AgentConfig class."""

    def test_default_models(self) -> None:
        """Test default model values."""
        config = AgentConfig()
        assert config.orchestrator_model == "gpt-5.6-sol"
        assert config.specialist_model == "gpt-5.6-luna"

    def test_every_default_model_is_gpt_5_6(self) -> None:
        """No OpenAI-facing default may drift off the gpt-5.6 price tier.

        Every model default we ship bills the user per query. Pinning the
        whole set here means a stale model name shows up as a test failure
        rather than as a surprise line on someone's OpenAI invoice.
        """
        config = AgentConfig()
        defaults = {
            "orchestrator": config.orchestrator_model,
            "specialist": config.specialist_model,
            "strategy": config.get_strategy_model(),
            "crypto": config.get_agent_model("crypto"),
            "prediction_markets": config.get_agent_model("prediction_markets"),
            "guardrail": config.guardrail_model,
        }
        off_tier = {
            name: model for name, model in defaults.items() if not model.startswith("gpt-5.6-")
        }
        assert not off_tier, f"default models off the gpt-5.6 tier: {off_tier}"

    def test_default_reasoning_effort(self) -> None:
        """Every tier sits at the balanced medium starting point."""
        config = AgentConfig()
        assert config.orchestrator_reasoning_effort == "medium"
        assert config.specialist_reasoning_effort == "medium"
        assert config.strategy_reasoning_effort == "medium"
        assert config.crypto_reasoning_effort == "medium"
        assert config.prediction_markets_reasoning_effort == "medium"

    def test_default_compact_ratio(self) -> None:
        """Hub compaction is on by default at 90% of the model window."""
        config = AgentConfig()
        assert config.orchestrator_compact_ratio == 0.9

    def test_compact_ratio_disabled_when_none(self) -> None:
        """Setting the ratio to None is the documented off switch."""
        config = AgentConfig(orchestrator_compact_ratio=None)
        assert config.orchestrator_compact_ratio is None

    def test_compact_ratio_rejects_out_of_range(self) -> None:
        """A ratio above 1.0 would put the threshold past the context window."""
        with pytest.raises(ValidationError):
            AgentConfig(orchestrator_compact_ratio=1.5)
        with pytest.raises(ValidationError):
            AgentConfig(orchestrator_compact_ratio=0.0)

    def test_get_agent_reasoning_effort_default(self) -> None:
        """Specialists without an override fall back to the specialist tier."""
        config = AgentConfig()
        effort = config.get_agent_reasoning_effort("market_data")
        assert effort == config.specialist_reasoning_effort

    def test_get_agent_reasoning_effort_override(self) -> None:
        """The three heavy specialists keep an explicit per-agent pin."""
        config = AgentConfig()
        assert config.get_agent_reasoning_effort("strategy") == "medium"
        assert config.get_agent_reasoning_effort("crypto") == "medium"
        assert config.get_agent_reasoning_effort("prediction_markets") == "medium"

    def test_get_agent_reasoning_effort_env_override(self) -> None:
        """A per-agent env var still wins over the pinned default."""
        os.environ["STRATEGY_REASONING_EFFORT"] = "xhigh"
        reset_config()
        try:
            assert get_config().get_agent_reasoning_effort("strategy") == "xhigh"
        finally:
            del os.environ["STRATEGY_REASONING_EFFORT"]
            reset_config()

    def test_default_mcp_urls(self) -> None:
        """Test default MCP server URLs."""
        config = AgentConfig()
        assert "localhost:8001" in config.mcp_fundamentals_url
        assert "localhost:8002" in config.mcp_market_data_url
        assert "localhost:8003" in config.mcp_events_news_url
        assert "localhost:8004" in config.mcp_options_url
        assert "localhost:8010" in config.mcp_crypto_url

    def test_get_agent_model_default(self) -> None:
        """Test get_agent_model falls back to specialist_model."""
        config = AgentConfig()
        # market_data_model is None by default, should return specialist_model
        model = config.get_agent_model("market_data")
        assert model == config.specialist_model

    def test_get_agent_model_override(self) -> None:
        """Test get_agent_model uses agent-specific override."""
        os.environ["MARKET_DATA_MODEL"] = "gpt-4-turbo"
        reset_config()

        config = get_config()
        model = config.get_agent_model("market_data")
        assert model == "gpt-4-turbo"

    def test_log_level_validation(self) -> None:
        """Test log level validation."""
        # Valid levels should work
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            os.environ["LOG_LEVEL"] = level
            reset_config()
            config = get_config()
            assert config.log_level == level

    def test_log_level_case_insensitive(self) -> None:
        """Test log level is case insensitive."""
        os.environ["LOG_LEVEL"] = "debug"
        reset_config()
        config = get_config()
        assert config.log_level == "DEBUG"


class TestConfigSingleton:
    """Test config singleton pattern."""

    def test_get_config_returns_same_instance(self) -> None:
        """Test that get_config returns the same instance."""
        config1 = get_config()
        config2 = get_config()
        assert config1 is config2

    def test_reset_config_clears_instance(self) -> None:
        """Test that reset_config clears the singleton."""
        config1 = get_config()
        reset_config()
        config2 = get_config()
        # Should be different instances after reset
        assert config1 is not config2

    def test_reset_allows_env_var_changes(self) -> None:
        """Test that reset allows picking up new env vars."""
        # Get initial config
        config1 = get_config()
        initial_model = config1.specialist_model

        # Change env var
        os.environ["SPECIALIST_MODEL"] = "gpt-4-turbo"
        reset_config()

        # Get new config
        config2 = get_config()
        assert config2.specialist_model == "gpt-4-turbo"
        assert config2.specialist_model != initial_model


class TestHubSettingsFilePrecedence:
    """Hub model/effort resolution: env > ~/.obai/settings.json > default."""

    def test_settings_file_applies_when_env_unset(self, tmp_path: Path) -> None:
        """The file is what the web UI and `obai config` write."""
        HubSettingsStore(path=tmp_path / "settings.json").save(
            HubSettings(hub_model="gpt-5.6-terra", hub_reasoning_effort="xhigh"),
        )
        reset_config()

        config = get_config()
        assert config.orchestrator_model == "gpt-5.6-terra"
        assert config.orchestrator_reasoning_effort == "xhigh"

    def test_env_var_beats_settings_file(self, tmp_path: Path) -> None:
        """An explicit env var stays authoritative, as the eval A/B relies on."""
        HubSettingsStore(path=tmp_path / "settings.json").save(
            HubSettings(hub_model="gpt-5.6-terra", hub_reasoning_effort="max"),
        )
        os.environ["ORCHESTRATOR_MODEL"] = "gpt-5.6-sol"
        os.environ["ORCHESTRATOR_REASONING_EFFORT"] = "high"
        reset_config()

        config = get_config()
        assert config.orchestrator_model == "gpt-5.6-sol"
        assert config.orchestrator_reasoning_effort == "high"

    def test_env_and_file_resolve_per_field(self, tmp_path: Path) -> None:
        """Env pinning the model must not drag the effort off the file."""
        HubSettingsStore(path=tmp_path / "settings.json").save(
            HubSettings(hub_model="gpt-5.6-terra", hub_reasoning_effort="max"),
        )
        os.environ["ORCHESTRATOR_MODEL"] = "gpt-5.6-sol"
        reset_config()

        config = get_config()
        assert config.orchestrator_model == "gpt-5.6-sol"
        assert config.orchestrator_reasoning_effort == "max"

    def test_missing_file_falls_back_to_shipped_defaults(self) -> None:
        """Fresh install and upgraded install both land here — no migration."""
        config = get_config()
        assert config.orchestrator_model == "gpt-5.6-sol"
        assert config.orchestrator_reasoning_effort == "medium"

    def test_settings_file_does_not_touch_specialists(self, tmp_path: Path) -> None:
        """The toggle is hub-only; specialist tiers stay code-owned."""
        HubSettingsStore(path=tmp_path / "settings.json").save(
            HubSettings(hub_model="gpt-5.6-terra", hub_reasoning_effort="max"),
        )
        reset_config()

        config = get_config()
        assert config.specialist_model == "gpt-5.6-luna"
        assert config.specialist_reasoning_effort == "medium"
        assert config.get_agent_reasoning_effort("strategy") == "medium"


class TestReasoningEffortTiers:
    """The effort literal must match what the gpt-5.6 API actually accepts."""

    def test_max_is_accepted(self) -> None:
        """`max` is a real tier on every gpt-5.6 model."""
        config = AgentConfig(orchestrator_reasoning_effort="max")
        assert config.orchestrator_reasoning_effort == "max"

    def test_minimal_is_rejected(self) -> None:
        """gpt-5.6 rejects `minimal` at request time — fail at config time.

        mypy flags the argument for the same reason this test exists, so the
        ignore is the static half of the assertion: if `minimal` were ever
        added back to `ReasoningEffort`, the ignore goes unused and the
        strict run fails.
        """
        with pytest.raises(ValidationError):
            AgentConfig(orchestrator_reasoning_effort="minimal")  # type: ignore[arg-type]


class TestGuardrailConfig:
    """Test guardrail configuration."""

    def test_guardrails_enabled_by_default(self) -> None:
        """Test guardrails are enabled by default."""
        config = AgentConfig()
        assert config.enable_guardrails is True

    def test_guardrails_can_be_disabled(self) -> None:
        """Test guardrails can be disabled via env var."""
        os.environ["ENABLE_GUARDRAILS"] = "false"
        reset_config()

        config = get_config()
        assert config.enable_guardrails is False
