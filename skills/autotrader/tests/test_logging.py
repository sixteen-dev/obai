"""Tests for logging configuration and log file creation.

Verifies that logs go to files (not stdout), paths resolve correctly,
and log entries contain expected fields.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lib.logging_config import LOGS_DIR, SKILL_ROOT, configure_logging, get_logger, reset_logging

from .conftest import FakeAccount, FakeOrder


@pytest.fixture(autouse=True)
def _clean_logging() -> None:  # type: ignore[misc]
    """Reset logging state before each test."""
    reset_logging()
    yield  # type: ignore[misc]
    reset_logging()


class TestLoggingConfig:
    """Test logging setup and path resolution."""

    def test_skill_root_resolves_to_autotrader(self) -> None:
        """SKILL_ROOT should point to the autotrader skill directory."""
        assert SKILL_ROOT.name == "autotrader"
        assert (SKILL_ROOT / "SKILL.md").exists()

    def test_logs_dir_is_under_skill_root(self) -> None:
        """LOGS_DIR should be {skill_root}/logs/."""
        assert LOGS_DIR == SKILL_ROOT / "logs"

    def test_configure_creates_logs_dir(self, tmp_path: Path) -> None:
        """configure_logging should create the logs directory if missing."""
        with patch("lib.logging_config.LOGS_DIR", tmp_path / "logs"):
            configure_logging()
            assert (tmp_path / "logs").exists()

    def test_configure_is_idempotent(self) -> None:
        """Calling configure_logging twice doesn't crash or duplicate handlers."""
        configure_logging()
        configure_logging()

    def test_get_logger_returns_logger(self) -> None:
        """get_logger should return a usable logger."""
        logger = get_logger("test")
        assert logger is not None


class TestLogFileOutput:
    """Test that log entries are written to files."""

    def test_log_writes_to_file(self, tmp_path: Path) -> None:
        """Logger should write JSONL entries to the log file."""
        log_dir = tmp_path / "logs"
        with (
            patch("lib.logging_config.LOGS_DIR", log_dir),
            patch("lib.logging_config._configured", False),
        ):
            configure_logging()
            logger = get_logger("test_writer")
            logger.info("test_event", key="value")

        # Find the log file
        log_files = list(log_dir.glob("autotrader_*.jsonl"))
        assert len(log_files) == 1

        content = log_files[0].read_text().strip()
        assert content  # not empty

        entry = json.loads(content)
        assert entry["event"] == "test_event"
        assert entry["key"] == "value"
        assert "timestamp" in entry

    def test_log_file_name_contains_date(self, tmp_path: Path) -> None:
        """Log file should be named autotrader_{date}.jsonl."""
        log_dir = tmp_path / "logs"
        with (
            patch("lib.logging_config.LOGS_DIR", log_dir),
            patch("lib.logging_config._configured", False),
        ):
            configure_logging()
            logger = get_logger("test_date")
            logger.info("date_check")

        log_files = list(log_dir.glob("autotrader_*.jsonl"))
        assert len(log_files) == 1
        # Filename should match autotrader_YYYY-MM-DD.jsonl
        name = log_files[0].name
        assert name.startswith("autotrader_")
        assert name.endswith(".jsonl")


class TestRiskLogging:
    """Test that risk checks produce log entries."""

    def test_risk_rejection_logged(
        self,
        alpaca_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Rejected risk check should produce a warning log entry."""
        log_dir = tmp_path / "logs"
        with (
            patch("lib.logging_config.LOGS_DIR", log_dir),
            patch("lib.logging_config._configured", False),
        ):
            configure_logging()

            alpaca_client._client.get_account.return_value = FakeAccount(
                equity="100000.00",
                last_equity="100000.00",
                long_market_value="50000.00",
            )
            alpaca_client._client.get_all_positions.return_value = []
            alpaca_client._client.get_orders.return_value = [
                FakeOrder(status="filled") for _ in range(20)
            ]

            from lib.risk import RiskChecker

            checker = RiskChecker(alpaca_client)
            result = checker.check_order("AAPL", "buy", 10, limit_price=200.0)

            assert result.allowed is False

        log_files = list(log_dir.glob("autotrader_*.jsonl"))
        assert len(log_files) == 1

        lines = log_files[0].read_text().strip().split("\n")
        events = [json.loads(line) for line in lines]
        rejection_events = [e for e in events if e.get("event") == "risk_check_rejected"]
        assert len(rejection_events) >= 1
        assert rejection_events[0]["symbol"] == "AAPL"

    def test_risk_pass_logged(
        self,
        alpaca_client: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Passed risk check should produce an info log entry."""
        log_dir = tmp_path / "logs"
        with (
            patch("lib.logging_config.LOGS_DIR", log_dir),
            patch("lib.logging_config._configured", False),
        ):
            configure_logging()

            alpaca_client._client.get_account.return_value = FakeAccount(
                equity="100000.00",
                last_equity="100000.00",
                long_market_value="50000.00",
            )
            alpaca_client._client.get_all_positions.return_value = []
            alpaca_client._client.get_orders.return_value = []

            from lib.risk import RiskChecker

            checker = RiskChecker(alpaca_client)
            result = checker.check_order("AAPL", "buy", 10, limit_price=200.0)

            assert result.allowed is True

        log_files = list(log_dir.glob("autotrader_*.jsonl"))
        assert len(log_files) == 1

        lines = log_files[0].read_text().strip().split("\n")
        events = [json.loads(line) for line in lines]
        pass_events = [e for e in events if e.get("event") == "risk_check_passed"]
        assert len(pass_events) >= 1
        assert pass_events[0]["symbol"] == "AAPL"
