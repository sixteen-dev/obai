"""Typed errors shared by strategy ingestion and the ledger (design §15.2, ADR 0001 §4)."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum


class ErrorCode(StrEnum):
    """Machine-readable error codes: the §15.2 required list plus the ADR 0001 §4 additions."""

    UNSUPPORTED_STRUCTURE = "UNSUPPORTED_STRUCTURE"
    UNSUPPORTED_PRODUCT = "UNSUPPORTED_PRODUCT"
    UNSUPPORTED_ACCOUNT_STATE = "UNSUPPORTED_ACCOUNT_STATE"
    INVALID_SELECTOR = "INVALID_SELECTOR"
    DATA_ENTITLEMENT_MISSING = "DATA_ENTITLEMENT_MISSING"
    DATA_COVERAGE_GAP = "DATA_COVERAGE_GAP"
    QUOTE_SEMANTICS_UNKNOWN = "QUOTE_SEMANTICS_UNKNOWN"
    MISSING_SETTLEMENT = "MISSING_SETTLEMENT"
    UNSUPPORTED_CORPORATE_ACTION = "UNSUPPORTED_CORPORATE_ACTION"
    PRICING_INPUT_UNAVAILABLE = "PRICING_INPUT_UNAVAILABLE"
    INSUFFICIENT_CAPITAL = "INSUFFICIENT_CAPITAL"
    HOLDOUT_LOCKED = "HOLDOUT_LOCKED"
    SEARCH_BUDGET_EXCEEDED = "SEARCH_BUDGET_EXCEEDED"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    ARTIFACT_COMMIT_FAILED = "ARTIFACT_COMMIT_FAILED"
    MALFORMED_JSON = "MALFORMED_JSON"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    NONFINITE_NUMBER = "NONFINITE_NUMBER"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    SCHEMA_VIOLATION = "SCHEMA_VIOLATION"
    UNSUPPORTED_SCHEMA_VERSION = "UNSUPPORTED_SCHEMA_VERSION"
    INVALID_STRATEGY_RULE = "INVALID_STRATEGY_RULE"


@dataclass(frozen=True, slots=True)
class Issue:
    """One reportable problem in the §15.2 error shape.

    Attributes:
        code: Machine-readable error code.
        message: Human-readable explanation; never empty.
        json_pointer: RFC 6901 pointer into the rejected document; "" is the whole document.
        retriable: Whether repeating the same request can succeed.
        missing_capability: Capability whose absence caused the issue, if any.
        affected_interval: Time interval the issue applies to, if any.
        remediation: What the caller can change to resolve it, if known.

    """

    code: ErrorCode
    message: str
    json_pointer: str
    retriable: bool = False
    missing_capability: str | None = None
    affected_interval: str | None = None
    remediation: str | None = None

    def __post_init__(self) -> None:
        """Reject a non-enum code, an empty message or a malformed pointer."""
        if not isinstance(self.code, ErrorCode):
            raise TypeError(f"Issue.code must be an ErrorCode, got {self.code!r}")
        if not self.message:
            raise ValueError("Issue.message must be non-empty")
        if self.json_pointer and not self.json_pointer.startswith("/"):
            raise ValueError(
                f"Issue.json_pointer must be '' or start with '/': {self.json_pointer!r}"
            )


def sorted_issues(issues: Iterable[Issue]) -> tuple[Issue, ...]:
    """Return issues in the report order of ADR 0001 §4: by (pointer, code).

    Args:
        issues: Issues in any order.

    Returns:
        The issues sorted by JSON pointer, then code.

    Raises:
        TypeError: If an item is not an ``Issue``.

    """
    items = tuple(issues)
    if not all(isinstance(issue, Issue) for issue in items):
        raise TypeError("sorted_issues needs Issue items")
    return tuple(sorted(items, key=lambda issue: (issue.json_pointer, issue.code)))


class SpecRejected(Exception):
    """A strategy specification was rejected; carries every issue found.

    Attributes:
        issues: The issues, in the order the caller reported them.

    """

    def __init__(self, issues: Sequence[Issue]) -> None:
        """Build the rejection.

        Args:
            issues: Every issue found; at least one.

        Raises:
            ValueError: If ``issues`` is empty.

        """
        if not issues:
            raise ValueError("SpecRejected requires at least one issue")
        self.issues: tuple[Issue, ...] = tuple(issues)
        summary = "; ".join(f"{issue.code} at {issue.json_pointer}" for issue in self.issues)
        super().__init__(f"strategy rejected: {summary}")


class LedgerInvariantError(Exception):
    """A ledger entry or state transition would break a ledger invariant."""


class UnsupportedLifecycle(Exception):
    """A lifecycle transition the engine does not support; the affected run is invalid.

    Attributes:
        code: Error code classifying the unsupported state.

    """

    def __init__(self, code: ErrorCode, message: str) -> None:
        """Build the error.

        Args:
            code: Error code classifying the unsupported state.
            message: What was unsupported; never empty.

        Raises:
            TypeError: If ``code`` is not an ``ErrorCode``.
            ValueError: If ``message`` is empty.

        """
        if not isinstance(code, ErrorCode):
            raise TypeError(f"UnsupportedLifecycle.code must be an ErrorCode, got {code!r}")
        if not message:
            raise ValueError("UnsupportedLifecycle.message must be non-empty")
        self.code = code
        super().__init__(f"{code}: {message}")


class MissingMarkError(Exception):
    """A required mark is absent; it is never replaced by zero.

    Attributes:
        instrument_ids: Instruments lacking a mark, in the caller's order.

    """

    def __init__(self, instrument_ids: Sequence[str]) -> None:
        """Build the error.

        Args:
            instrument_ids: Instruments lacking a mark; at least one.

        Raises:
            TypeError: If ``instrument_ids`` is a bare string.
            ValueError: If ``instrument_ids`` is empty.

        """
        if isinstance(instrument_ids, str):
            raise TypeError("MissingMarkError needs a sequence of instrument ids, not a str")
        if not instrument_ids:
            raise ValueError("MissingMarkError requires at least one instrument id")
        self.instrument_ids: tuple[str, ...] = tuple(instrument_ids)
        super().__init__(f"missing mark for: {', '.join(self.instrument_ids)}")
