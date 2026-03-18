"""Prompt loading and templating utilities.

Loads agent instructions from Opik (versioned) with fallback to
markdown files on disk. Variable substitution is always applied locally.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from string import Template

from core_agents.prompt_manager import get_prompt_from_opik

logger = logging.getLogger(__name__)


def load_prompt(agent_name: str, *, commit: str | None = None, **variables: str) -> str:
    """Load agent prompt with variable substitution.

    Tries Opik first for versioned prompts, falls back to markdown file.

    Args:
        agent_name: Name of agent (e.g., "central_hub", "market_data").
        commit: Optional Opik version commit hash for rollback.
        **variables: Variables to substitute in template (e.g., model="gpt-4o").

    Returns:
        Prompt text with variables substituted.

    Raises:
        FileNotFoundError: If prompt unavailable from both Opik and file.
        ValueError: If prompt validation fails.
    """
    # Try Opik first (versioned prompts)
    template_text = get_prompt_from_opik(agent_name, commit=commit)

    if template_text is not None:
        source = "Opik"
    else:
        # Fall back to markdown file
        prompts_dir = Path(__file__).parent / "prompts"
        prompt_path = prompts_dir / f"{agent_name}.md"

        if not prompt_path.exists():
            msg = f"Prompt file not found: {prompt_path}"
            raise FileNotFoundError(msg)

        template_text = prompt_path.read_text()
        source = f"file ({prompt_path.name})"

    # Always inject current date/time so agents know "today"
    now = datetime.now(timezone.utc)
    default_vars = {
        "TODAY_DATE": now.strftime("%Y-%m-%d"),
        "TODAY_DATETIME": now.isoformat(),
        "CURRENT_YEAR": str(now.year),
    }
    # User-provided variables override defaults
    all_variables = {**default_vars, **variables}

    # Substitute variables
    template = Template(template_text)
    prompt = template.safe_substitute(**all_variables)

    # Validate prompt
    validate_prompt(prompt, agent_name)

    logger.info("Loaded prompt for %s from %s", agent_name, source)
    return prompt


def validate_prompt(prompt: str, agent_name: str) -> None:
    """Validate prompt meets minimum requirements.

    Args:
        prompt: Prompt text to validate.
        agent_name: Name of agent for error messages.

    Raises:
        ValueError: If prompt validation fails.
    """
    if len(prompt) < 100:
        msg = f"{agent_name} prompt too short: {len(prompt)} characters"
        raise ValueError(msg)

    # Different required sections for central hub vs specialist agents
    if agent_name == "central_hub":
        # Central hub uses agents-as-tools pattern
        required_sections = ["Routing Logic", "Constraints"]
    elif agent_name == "guardrail":
        # Guardrail is a simple classifier, minimal requirements
        required_sections = ["Valid Topics", "Invalid Topics"]
    else:
        # Specialist agents use THINK → PLAN → ACT → REFLECT workflow
        required_sections = ["Workflow:", "Your expertise", "Output Guidelines"]

    missing_sections = [s for s in required_sections if s not in prompt]

    if missing_sections:
        msg = f"{agent_name} prompt missing sections: {', '.join(missing_sections)}"
        raise ValueError(msg)

    logger.debug(f"Prompt validation passed for {agent_name}")
