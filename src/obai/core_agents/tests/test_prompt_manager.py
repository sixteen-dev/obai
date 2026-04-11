"""Tests for prompt sync/versioning helpers."""

from unittest.mock import patch

from core_agents.prompt_manager import PROMPT_NAMES, sync_prompts_to_opik


class TestPromptManager:
    """Prompt sync behavior and prompt registry coverage."""

    def test_prediction_markets_prompt_is_in_sync_registry(self) -> None:
        """Prediction-market prompt should be versioned alongside other prompts."""
        assert "prediction_markets" in PROMPT_NAMES

    def test_sync_results_include_prediction_markets_when_opik_unavailable(self) -> None:
        """Unavailable Opik still reports all tracked prompts."""
        with patch("core_agents.prompt_manager._get_opik_client", return_value=None):
            results = sync_prompts_to_opik()

        assert "prediction_markets" in results
        assert results["prediction_markets"] is False
