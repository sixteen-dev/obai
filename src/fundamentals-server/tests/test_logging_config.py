"""Error-log redaction: the FMP API key must never reach the logs.

The FMP client passes the key as an `apikey` query param, so httpx error
messages (and their tracebacks) embed the key-bearing URL. log_error must
redact the URL and suppress the traceback for those errors.
"""

from unittest.mock import MagicMock

from src.logging_config import _redact_urls, log_error

_KEY = "fake-fmp-token-value"  # gitleaks:allow — placeholder, not a real credential
_URL_ERR = (
    "Client error '403 Forbidden' for url "
    f"'https://financialmodelingprep.com/stable/profile?symbol=AAPL&apikey={_KEY}'"
)


class TestRedactUrls:
    def test_strips_url_carrying_apikey(self) -> None:
        redacted = _redact_urls(_URL_ERR)
        assert _KEY not in redacted
        assert "[URL REDACTED]" in redacted

    def test_leaves_plain_text_untouched(self) -> None:
        assert _redact_urls("boom, no url here") == "boom, no url here"


class TestLogErrorRedaction:
    def test_url_bearing_error_is_redacted_and_traceback_suppressed(self) -> None:
        logger = MagicMock()
        log_error(logger, RuntimeError(_URL_ERR))

        logger.error.assert_called_once()
        _, kwargs = logger.error.call_args
        assert _KEY not in kwargs["error_message"]
        assert "[URL REDACTED]" in kwargs["error_message"]
        # Traceback would re-render the raw URL, so it must be suppressed here.
        assert kwargs["exc_info"] is False

    def test_plain_error_keeps_traceback(self) -> None:
        logger = MagicMock()
        log_error(logger, ValueError("bad value"))

        _, kwargs = logger.error.call_args
        assert kwargs["error_message"] == "bad value"
        assert kwargs["exc_info"] is True
