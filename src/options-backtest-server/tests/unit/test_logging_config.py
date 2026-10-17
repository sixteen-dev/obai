"""Structured JSON logging (ADR 0003 §1.3)."""

import json
import logging
from collections.abc import Iterator

import pytest
import structlog

from options_backtest.logging_config import configure_logging, get_logger


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Give every test the process's logging state back: root level, handlers and structlog."""
    root = logging.getLogger()
    level, handlers = root.level, list(root.handlers)
    yield
    structlog.reset_defaults()
    for handler in [handler for handler in root.handlers if handler not in handlers]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)


def test_events_render_as_json_with_level_logger_and_timestamp(
    caplog: pytest.LogCaptureFixture,
) -> None:
    configure_logging("INFO")

    get_logger("options_backtest.test").info("tool_call", tool="probe", issue_count=2)

    event = json.loads(caplog.records[-1].getMessage())
    assert event["event"] == "tool_call"
    assert (event["tool"], event["issue_count"]) == ("probe", 2)
    assert (event["level"], event["logger"]) == ("info", "options_backtest.test")
    assert "timestamp" in event
    assert isinstance(structlog.get_config()["processors"][-1], structlog.processors.JSONRenderer)


def test_the_level_is_applied(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging("WARNING")
    logger = get_logger("options_backtest.test")

    logger.info("dropped")
    logger.warning("kept")

    assert logging.getLogger().level == logging.WARNING
    assert [json.loads(record.getMessage())["event"] for record in caplog.records] == ["kept"]


def test_configuring_twice_changes_nothing() -> None:
    configure_logging("DEBUG")
    handlers, config = list(logging.getLogger().handlers), structlog.get_config()

    configure_logging("DEBUG")

    assert logging.getLogger().handlers == handlers
    assert [type(processor) for processor in structlog.get_config()["processors"]] == [
        type(processor) for processor in config["processors"]
    ]
    assert structlog.get_config()["cache_logger_on_first_use"] is True


def test_an_unknown_level_raises() -> None:
    with pytest.raises(ValueError, match="LOUD"):
        configure_logging("LOUD")


def test_get_logger_rejects_an_empty_name() -> None:
    with pytest.raises(ValueError, match="name"):
        get_logger("")
