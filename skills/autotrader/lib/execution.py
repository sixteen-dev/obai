"""Serialize paper orders and persist intents before any broker mutation.

All processes for an account must share AUTOTRADER_STATE_DIR on one host.
Broker state is authoritative; a local intent proves only an attempted POST.
"""

import fcntl
import hashlib
import json
import math
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .alpaca_client import AlpacaClient, AlpacaClientError, OrderRejectedError
from .logging_config import get_logger
from .models import OrderInfo
from .risk import RiskChecker

_logger = get_logger("execution")

_CLIENT_ORDER_ID = re.compile(r"[A-Za-z0-9_-]{1,48}")
_ORDER_KEYS = frozenset(
    {"symbol", "side", "qty", "order_type", "limit_price", "stop_price", "time_in_force"}
)
_CLOSE_KEYS = frozenset({"symbol", "close_position"})
_POSITIVE_KEYS = ("qty", "limit_price", "stop_price")
# Compared against the broker's order when a repeated intent is reconciled.
_MATCH_KEYS = ("symbol", "side", "qty", "order_type", "time_in_force", "stop_price")
_REDUCING = frozenset({("long", "sell"), ("short", "buy")})


class UncertainSubmissionError(AlpacaClientError):
    """The broker may have accepted an order; reconciliation is required."""


def submission_failure(exc: Exception, client_order_id: str) -> dict[str, object]:
    """Shape a failed submission for a machine-parsed CLI result.

    Both order scripts emit this on stdout so a caller reading one stream never
    misses `submission_state`, the only signal that an order may still exist.

    Args:
        exc: The failure raised by ``execute_order`` or the client.
        client_order_id: The intent ID this attempt was bound to.

    Returns:
        The error payload, with `submission_state` "unknown" only when an order
        may have reached the broker.
    """
    return {
        "error": str(exc),
        "client_order_id": client_order_id,
        "submission_state": (
            "unknown" if isinstance(exc, UncertainSubmissionError) else "not_submitted"
        ),
    }


def state_directory() -> Path:
    """Resolve durable state independently of the scheduler's working directory.

    Returns:
        The absolute directory holding execution intents and the lock file.

    Raises:
        ValueError: If AUTOTRADER_STATE_DIR is set but not a usable absolute
            path. A relative path would silently give each working directory its
            own lock, which voids the cross-process mutual exclusion.
    """
    configured = os.environ.get("AUTOTRADER_STATE_DIR")
    if configured is None:
        return (Path(__file__).resolve().parents[1] / "memory" / "execution").resolve()
    if not configured or not Path(configured).is_absolute():
        msg = "AUTOTRADER_STATE_DIR must be a non-empty absolute path"
        raise ValueError(msg)
    return Path(configured).resolve()


@contextmanager
def execution_lock() -> Iterator[Path]:
    """Fail promptly if another process is reconciling/submitting an order."""
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "execution.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AlpacaClientError(
                "Another execution is active; reconcile on the next run"
            ) from exc
        try:
            yield directory
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _normalized_request(request: dict[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize an order request before anything is persisted.

    Normalizing here means the dict that is compared on a retry is the dict that
    was sent, and validating here means a parameter error cannot surface after
    the durable marker exists.

    Args:
        request: Either a close request (`symbol`, `close_position`) or a full
            order request carrying every key ``AlpacaClient.submit_order`` takes.

    Returns:
        The canonical request.

    Raises:
        ValueError: If the key set is wrong or a numeric field is unusable.
    """
    symbol = str(request.get("symbol", "")).upper()
    if not symbol:
        msg = "Order request must name a symbol"
        raise ValueError(msg)
    keys = set(request)
    if keys == _CLOSE_KEYS and request["close_position"] is True:
        return {"symbol": symbol, "close_position": True}
    if keys != _ORDER_KEYS:
        msg = f"Order request keys must be {sorted(_ORDER_KEYS)} or {sorted(_CLOSE_KEYS)}"
        raise ValueError(msg)
    for key in _POSITIVE_KEYS:
        value = request[key]
        if value is None:
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            msg = f"{key} must be positive and finite"
            raise ValueError(msg)
    return {
        **request,
        "symbol": symbol,
        "side": str(request["side"]).lower(),
        "order_type": str(request["order_type"]).lower(),
        "time_in_force": str(request["time_in_force"]).lower(),
    }


def _intent_path(directory: Path, account_id: str, client_order_id: str) -> Path:
    """Bind the on-disk marker to one broker account and one client order ID."""
    key = hashlib.sha256(f"{account_id}:{client_order_id}".encode()).hexdigest()
    return directory / f"{key}.json"


def _load_record(path: Path, client_order_id: str) -> dict[str, Any]:
    """Read a persisted submission record, treating damage as an unresolved send."""
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise UncertainSubmissionError(
            f"Unreadable submission record for {client_order_id}: {exc}. "
            "An order may exist; reconcile this ID before any retry."
        ) from exc
    if not isinstance(record, dict) or "intent" not in record:
        raise UncertainSubmissionError(
            f"Incomplete submission record for {client_order_id}. "
            "An order may exist; reconcile this ID before any retry."
        )
    return record


def _write_record(directory: Path, path: Path, record: dict[str, Any]) -> None:
    """Persist the marker durably, atomically, before any broker mutation."""
    if path.exists():
        msg = f"Refusing to overwrite an existing submission record: {path.name}"
        raise UncertainSubmissionError(msg)
    temp = path.with_suffix(".tmp")
    with temp.open("w") as file:
        json.dump(record, file, sort_keys=True, allow_nan=False)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temp, path)
    directory_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _matches_sent_order(existing: OrderInfo, reference: dict[str, Any]) -> bool:
    """Report whether the broker's order under this client ID is the one sent."""
    fields = [key for key in _MATCH_KEYS if key in reference]
    if reference.get("order_type") in {"limit", "stop_limit"}:
        fields.append("limit_price")
    return all(getattr(existing, field) == reference.get(field) for field in fields)


def _lookup_order(
    client: AlpacaClient,
    client_order_id: str,
    *,
    unresolved: bool,
) -> OrderInfo | None:
    """Look up the broker's order, escalating a failed lookup for a sent intent."""
    try:
        return client.get_order_by_client_id(client_order_id)
    except AlpacaClientError as exc:
        if not unresolved:
            raise
        raise UncertainSubmissionError(
            f"Cannot reconcile unresolved submission {client_order_id}: {exc}. "
            "An order may exist; do not resubmit or create a replacement intent."
        ) from exc


def _reconcile(client: AlpacaClient, path: Path, intent: dict[str, Any]) -> OrderInfo | None:
    """Resolve a repeated intent against broker state before any new submission.

    Args:
        client: The paper trading client.
        path: On-disk marker for this account and client order ID.
        intent: The immutable intent this call is submitting.

    Returns:
        The broker's order when this intent already has one, or None when no
        marker exists and a submission is still allowed.

    Raises:
        UncertainSubmissionError: If a marker exists but the broker shows no
            order, or the lookup itself failed.
        AlpacaClientError: If the ID belongs to a different intent or order.
    """
    client_order_id = intent["client_order_id"]
    record = _load_record(path, client_order_id) if path.exists() else None
    if record is not None and record["intent"] != intent:
        msg = "Client order ID already belongs to a different intent"
        raise AlpacaClientError(msg)
    existing = _lookup_order(client, client_order_id, unresolved=record is not None)
    if existing is None and record is None:
        return None
    if existing is None:
        raise UncertainSubmissionError(
            f"Unresolved submission {client_order_id}; broker lookup found no order. "
            "Reconcile this ID; do not resubmit or create a replacement intent."
        )
    # Also protect against collisions when local state was restored/lost.
    reference = (record or {}).get("submitted") or intent["request"]
    if not _matches_sent_order(existing, reference):
        msg = "Broker client order ID belongs to a different order"
        raise AlpacaClientError(msg)
    return existing


def _resolved_close(client: AlpacaClient, symbol: str) -> dict[str, Any]:
    """Turn a close request into the concrete reducing order for the held position."""
    position = client.get_position(symbol)
    if position is None:
        msg = "No position to close"
        raise AlpacaClientError(msg)
    return {
        "symbol": symbol,
        "side": "sell" if position.side == "long" else "buy",
        "qty": position.qty,
        "order_type": "market",
        "limit_price": None,
        "stop_price": None,
        "time_in_force": "day",
    }


def _check_reduce_only(client: AlpacaClient, order: dict[str, Any]) -> None:
    """Reject a reduce-only order that would grow or reverse the position."""
    position = client.get_position(order["symbol"])
    if (
        position is None
        or not math.isfinite(position.qty)
        or position.qty < order["qty"]
        or (position.side, order["side"]) not in _REDUCING
    ):
        msg = "Reduce-only order would increase or reverse the position"
        raise AlpacaClientError(msg)


def _persist_then_submit(
    client: AlpacaClient,
    directory: Path,
    path: Path,
    intent: dict[str, Any],
    order: dict[str, Any],
) -> OrderInfo:
    """Record the intent durably, then submit it exactly once.

    Any failure after the marker exists leaves the submission unresolved —
    except a broker verdict, which proves no order was created.
    """
    client_order_id = intent["client_order_id"]
    _write_record(directory, path, {"intent": intent, "submitted": order})
    _logger.info(
        "order_intent_persisted",
        client_order_id=client_order_id,
        symbol=order["symbol"],
        side=order["side"],
        qty=order["qty"],
        order_type=order["order_type"],
    )
    try:
        return client.submit_order(**order, client_order_id=client_order_id)
    except OrderRejectedError:
        # A 4xx verdict proves no order exists, so the marker must not outlive
        # it — otherwise this ID stays blocked after its cause is fixed.
        path.unlink(missing_ok=True)
        _logger.warning("order_rejected_by_broker", client_order_id=client_order_id)
        raise
    except Exception as exc:
        # Deliberately broad: after the marker is written, anything raised here
        # may follow an in-flight POST. Narrowing would misclassify a response
        # that fails JSON decoding or model validation, both ValueError.
        raise UncertainSubmissionError(
            f"Submission {client_order_id} did not complete: {exc}. "
            "Reconcile this client order ID before any retry."
        ) from exc


def execute_order(
    client: AlpacaClient,
    request: dict[str, Any],
    client_order_id: str,
    *,
    allow_after_hours: bool = False,
    reduce_only: bool = False,
) -> OrderInfo:
    """Submit once per immutable intent, or return its current broker order.

    A crash/timeout followed by a 404 is ambiguous and remains blocked. Never
    delete that intent or change its ID to force another submission.

    Args:
        client: The paper trading client.
        request: A close request (`symbol`, `close_position`) or a full order
            request carrying every key ``AlpacaClient.submit_order`` takes.
        client_order_id: Stable intent ID, 1-48 of [A-Za-z0-9_-].
        allow_after_hours: Queue the order while the market is closed.
        reduce_only: Reject anything that would grow or reverse the position.

    Returns:
        The broker's order for this intent.

    Raises:
        ValueError: If the client order ID or request is malformed.
        UncertainSubmissionError: If a prior submission is unresolved.
        AlpacaClientError: If the market is closed, risk rejects the order, the
            ID belongs to different work, or the broker refuses the order.
    """
    if not _CLIENT_ORDER_ID.fullmatch(client_order_id):
        msg = "client_order_id must be 1-48 letters, digits, underscores or hyphens"
        raise ValueError(msg)
    request = _normalized_request(request)
    with execution_lock() as directory:
        account_id = client.get_account().account_id
        if not account_id:
            msg = "Missing broker account ID; cannot bind order intent"
            raise AlpacaClientError(msg)
        path = _intent_path(directory, account_id, client_order_id)
        intent = {
            "account_id": account_id,
            "client_order_id": client_order_id,
            "request": request,
            "reduce_only": reduce_only,
        }
        existing = _reconcile(client, path, intent)
        if existing is not None:
            return existing
        if not allow_after_hours and not client.get_clock().get("is_open"):
            msg = "Market is closed; wait for the next open"
            raise AlpacaClientError(msg)
        closing = bool(request.get("close_position"))
        order = _resolved_close(client, request["symbol"]) if closing else request
        if closing or reduce_only:
            _check_reduce_only(client, order)
        risk = RiskChecker(client).check_order(
            order["symbol"], order["side"], order["qty"], order["limit_price"]
        )
        if not risk.allowed:
            msg = f"Risk rejected: {risk.rejection_reason}"
            raise AlpacaClientError(msg)
        # Validate against the SDK before the marker exists, so a parameter
        # error can never masquerade as an uncertain submission.
        client.prepare_order(**order, client_order_id=client_order_id)
        return _persist_then_submit(client, directory, path, intent, order)
