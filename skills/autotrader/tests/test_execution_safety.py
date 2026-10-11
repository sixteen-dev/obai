"""Regression cases for repeated, concurrent paper-trading jobs."""

import json
from pathlib import Path
from typing import Any

import pytest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderStatus, PositionSide

from lib.alpaca_client import AlpacaClientError, OrderRejectedError
from lib.execution import (
    UncertainSubmissionError,
    execute_order,
    execution_lock,
    state_directory,
)
from lib.risk import RiskChecker
from scripts import execute_trade

from .conftest import FakeAccount, FakeOrder, FakePosition, api_error

# ─────────────────────────────────────────────────────────────────────────────
# Risk gates on broker state
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("equity", ["0", "-1", "nan", "inf"])
def test_invalid_equity_blocks_new_exposure(alpaca_client: Any, equity: str) -> None:
    alpaca_client._client.get_account.return_value = FakeAccount(equity=equity)
    assert not RiskChecker(alpaca_client).check_order("XYZ", "buy", 1, 100).allowed


@pytest.mark.parametrize("price", [float("nan"), float("inf"), -1, 0])
def test_invalid_price_is_not_replaced_with_existing_price(
    alpaca_client: Any, price: float
) -> None:
    assert not RiskChecker(alpaca_client).check_order("AAPL", "buy", 1, price).allowed


def test_daily_entry_limits_do_not_block_a_protective_exit(alpaca_client: Any) -> None:
    alpaca_client._client.get_account.return_value = FakeAccount(equity="90000")
    alpaca_client._client.get_orders.return_value = [FakeOrder(status=OrderStatus.FILLED)] * 20
    assert RiskChecker(alpaca_client).check_order("AAPL", "sell", 25).allowed


def test_daily_loss_limit_rejects_at_the_configured_boundary(alpaca_client: Any) -> None:
    """Exactly -3% is a breach, so the limit itself is not a tradable state."""
    alpaca_client._client.get_account.return_value = FakeAccount(
        equity="100000.00", last_equity="103000.00"
    )
    result = RiskChecker(alpaca_client).check_order("XYZ", "buy", 1, 100)
    assert not result.allowed
    assert "Daily loss limit" in result.rejection_reason


def test_maximum_positions_enforced(alpaca_client: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_POSITIONS", "2")
    result = RiskChecker(alpaca_client).check_order("XYZ", "buy", 1, 100)
    assert not result.allowed
    assert "positions" in result.rejection_reason.lower()


def test_pending_orders_reserve_gross_exposure(alpaca_client: Any) -> None:
    sdk = alpaca_client._client
    sdk.get_account.return_value = FakeAccount(long_market_value="85000")
    sdk.get_orders.return_value = [
        FakeOrder(
            symbol="XYZ",
            qty="50",
            filled_qty="0",
            type="limit",
            limit_price="100",
        )
    ]
    result = RiskChecker(alpaca_client).check_order("NEW", "buy", 1, 100)
    assert not result.allowed
    assert "Exposure" in result.rejection_reason


def test_partial_fill_reserves_only_remaining_quantity(alpaca_client: Any) -> None:
    sdk = alpaca_client._client
    sdk.get_account.return_value = FakeAccount(long_market_value="84000")
    sdk.get_orders.return_value = [
        FakeOrder(
            symbol="XYZ",
            qty="100",
            filled_qty="50",
            status="partially_filled",
            type="limit",
            limit_price="100",
        )
    ]
    assert RiskChecker(alpaca_client).check_order("NEW", "buy", 1, 100).allowed


def test_protective_exits_are_not_counted_as_extra_exposure(alpaca_client: Any) -> None:
    """A resting stop against a held long is already inside long_market_value."""
    sdk = alpaca_client._client
    symbols = ("AAPL", "MSFT", "GOOG", "AMZN")
    sdk.get_account.return_value = FakeAccount(long_market_value="80000.00", cash="20000.00")
    sdk.get_all_positions.return_value = [
        FakePosition(symbol=s, qty="100", market_value="20000.00", current_price="200.00")
        for s in symbols
    ]
    sdk.get_orders.return_value = [
        FakeOrder(
            id=f"stop-{s}",
            symbol=s,
            side="sell",
            qty="100",
            filled_qty="0",
            type="stop",
            stop_price="190",
            status="new",
        )
        for s in symbols
    ]
    assert RiskChecker(alpaca_client).check_order("NVDA", "buy", 25, 200.0).allowed


def test_pending_exit_without_a_price_does_not_halt_unrelated_entries(alpaca_client: Any) -> None:
    """A market exit carries no price, but it adds no exposure either."""
    sdk = alpaca_client._client
    sdk.get_account.return_value = FakeAccount(long_market_value="20000.00")
    sdk.get_all_positions.return_value = [
        FakePosition(symbol="AAPL", qty="100", market_value="20000.00", current_price="200.00")
    ]
    sdk.get_orders.return_value = [
        FakeOrder(symbol="AAPL", side="sell", qty="100", filled_qty="0", status="new")
    ]
    assert RiskChecker(alpaca_client).check_order("NVDA", "buy", 1, 200.0).allowed


def test_unpriceable_pending_entry_fails_closed_and_names_the_symbol(alpaca_client: Any) -> None:
    sdk = alpaca_client._client
    sdk.get_all_positions.return_value = []
    sdk.get_orders.return_value = [
        FakeOrder(symbol="TSLA", side="buy", qty="10", filled_qty="0", status="new")
    ]
    result = RiskChecker(alpaca_client).check_order("NVDA", "buy", 1, 200.0)
    assert not result.allowed
    assert "TSLA" in result.rejection_reason


def test_existing_exit_order_blocks_a_second_sell(alpaca_client: Any) -> None:
    alpaca_client._client.get_orders.return_value = [
        FakeOrder(
            side="sell",
            qty="25",
            filled_qty="0",
        )
    ]
    assert not RiskChecker(alpaca_client).check_order("AAPL", "sell", 25).allowed


@pytest.mark.parametrize("setting", ["MAX_POSITION_PCT", "MAX_DAILY_LOSS_PCT", "MAX_EXPOSURE_PCT"])
def test_nonfinite_risk_limits_rejected(
    alpaca_client: Any, monkeypatch: pytest.MonkeyPatch, setting: str
) -> None:
    monkeypatch.setenv(setting, "nan")
    with pytest.raises(ValueError, match=setting):
        RiskChecker(alpaca_client)


def test_short_position_quantity_is_normalized(alpaca_client: Any) -> None:
    alpaca_client._client.get_all_positions.return_value = [
        FakePosition(
            side=PositionSide.SHORT,
            qty="-25",
            market_value="-5000",
        )
    ]
    alpaca_client._client.get_account.return_value = FakeAccount(equity="90000")
    assert RiskChecker(alpaca_client).check_order("AAPL", "buy", 25).allowed


# ─────────────────────────────────────────────────────────────────────────────
# Submit-once execution
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def order_request() -> dict[str, Any]:
    return {
        "symbol": "AAPL",
        "side": "buy",
        "qty": 10.0,
        "order_type": "market",
        "limit_price": 200.0,
        "stop_price": None,
        "time_in_force": "day",
    }


def test_repeated_intent_returns_current_fill_without_resubmitting(
    alpaca_client: Any, order_request: dict[str, Any]
) -> None:
    first = execute_order(alpaca_client, order_request, "signal-123")
    assert first.status == "accepted"
    sdk = alpaca_client._client
    assert sdk.submit_order.call_args.args[0].client_order_id == "signal-123"
    sdk.get_order_by_client_id.side_effect = None
    sdk.get_order_by_client_id.return_value = FakeOrder(
        status="partially_filled", filled_qty="4", qty="10"
    )
    sdk.get_clock.side_effect = AssertionError("Reconciliation must work after market close")
    second = execute_order(alpaca_client, order_request, "signal-123")
    assert second.filled_qty == 4
    assert second.status == "partially_filled"
    sdk.submit_order.assert_called_once()


def test_timeout_persists_intent_and_a_404_does_not_authorize_retry(
    alpaca_client: Any, order_request: dict[str, Any]
) -> None:
    sdk = alpaca_client._client
    marker_present: list[bool] = []

    def timeout_after_post(*args: Any, **kwargs: Any) -> None:
        marker_present.append(bool(list(state_directory().glob("*.json"))))
        raise TimeoutError("response lost")

    sdk.submit_order.side_effect = timeout_after_post
    with pytest.raises(UncertainSubmissionError, match="did not complete"):
        execute_order(alpaca_client, order_request, "signal-timeout")
    # Asserted outside the raises-block: an AssertionError raised inside the
    # side_effect would be converted into the very error being matched.
    assert marker_present == [True], "Intent must be persisted before the POST"

    with pytest.raises(UncertainSubmissionError, match="Unresolved submission"):
        execute_order(alpaca_client, order_request, "signal-timeout")
    sdk.submit_order.assert_called_once()
    sdk.get_order_by_client_id.side_effect = None
    sdk.get_order_by_client_id.return_value = FakeOrder(status="filled", qty="10")
    assert execute_order(alpaca_client, order_request, "signal-timeout").status == "filled"
    sdk.submit_order.assert_called_once()


def test_unreconcilable_intent_is_never_reported_as_not_submitted(
    alpaca_client: Any, order_request: dict[str, Any]
) -> None:
    """A failed lookup over an unresolved intent must stay 'unknown'."""
    sdk = alpaca_client._client
    sdk.submit_order.side_effect = TimeoutError("response lost")
    with pytest.raises(UncertainSubmissionError):
        execute_order(alpaca_client, order_request, "lookup-down")

    sdk.get_order_by_client_id.side_effect = api_error(503, "service unavailable")
    with pytest.raises(UncertainSubmissionError, match="Cannot reconcile"):
        execute_order(alpaca_client, order_request, "lookup-down")


def test_broker_rejection_frees_the_intent_for_a_later_retry(
    alpaca_client: Any, order_request: dict[str, Any]
) -> None:
    """A 4xx verdict proves no order exists, so the ID must not stay blocked."""
    sdk = alpaca_client._client
    sdk.submit_order.side_effect = api_error(422, "potential wash trade detected")
    with pytest.raises(OrderRejectedError, match="wash trade"):
        execute_order(alpaca_client, order_request, "rejected-once")
    assert not list(state_directory().glob("*.json"))

    sdk.submit_order.side_effect = None
    sdk.submit_order.return_value = FakeOrder()
    assert execute_order(alpaca_client, order_request, "rejected-once").status == "accepted"


def test_parameter_error_cannot_masquerade_as_an_uncertain_submission(
    alpaca_client: Any, order_request: dict[str, Any]
) -> None:
    """A limit order with no price fails before anything durable is written."""
    broken = {**order_request, "order_type": "limit", "limit_price": None}
    with pytest.raises(ValueError, match="limit_price required"):
        execute_order(alpaca_client, broken, "bad-params")
    assert not list(state_directory().glob("*.json"))
    alpaca_client._client.submit_order.assert_not_called()


@pytest.mark.parametrize("request_keys", ["missing", "extra"])
def test_malformed_request_shape_is_rejected_before_the_lock(
    alpaca_client: Any, order_request: dict[str, Any], request_keys: str
) -> None:
    malformed = dict(order_request)
    if request_keys == "missing":
        del malformed["time_in_force"]
    else:
        malformed["extended_hours"] = True
    with pytest.raises(ValueError, match="Order request keys"):
        execute_order(alpaca_client, malformed, "bad-shape")
    alpaca_client._client.submit_order.assert_not_called()


def test_changed_request_cannot_reuse_client_id(
    alpaca_client: Any, order_request: dict[str, Any]
) -> None:
    execute_order(alpaca_client, order_request, "immutable")
    with pytest.raises(AlpacaClientError, match="different intent"):
        execute_order(alpaca_client, {**order_request, "qty": 20}, "immutable")
    alpaca_client._client.submit_order.assert_called_once()


def test_broker_order_under_a_reused_id_is_not_adopted(
    alpaca_client: Any, order_request: dict[str, Any]
) -> None:
    """With local state lost, the broker's order must still match what was sent."""
    sdk = alpaca_client._client
    sdk.get_order_by_client_id.side_effect = None
    sdk.get_order_by_client_id.return_value = FakeOrder(qty="99", filled_qty="0")
    with pytest.raises(AlpacaClientError, match="different order"):
        execute_order(alpaca_client, order_request, "collision")
    sdk.submit_order.assert_not_called()


def test_overlapping_execution_cannot_reach_broker(
    alpaca_client: Any, order_request: dict[str, Any]
) -> None:
    with execution_lock(), pytest.raises(AlpacaClientError, match="execution is active"):
        execute_order(alpaca_client, order_request, "overlap")
    alpaca_client._client.submit_order.assert_not_called()


def test_reduce_only_cannot_open_a_short(alpaca_client: Any, order_request: dict[str, Any]) -> None:
    with pytest.raises(AlpacaClientError, match="Reduce-only"):
        execute_order(
            alpaca_client, {**order_request, "side": "sell", "qty": 30}, "exit", reduce_only=True
        )
    alpaca_client._client.submit_order.assert_not_called()


def test_close_retry_reconciles_after_position_is_gone(alpaca_client: Any) -> None:
    sdk = alpaca_client._client
    sdk.submit_order.return_value = FakeOrder(side="sell", qty="25", status="accepted")
    request = {"symbol": "AAPL", "close_position": True}
    execute_order(alpaca_client, request, "close-123", reduce_only=True)
    sdk.get_order_by_client_id.side_effect = None
    sdk.get_order_by_client_id.return_value = FakeOrder(side="sell", qty="25", status="filled")
    sdk.get_open_position.side_effect = AssertionError("Do not re-close a filled intent")
    assert execute_order(alpaca_client, request, "close-123", reduce_only=True).status == "filled"
    sdk.submit_order.assert_called_once()


def test_close_retry_rejects_an_order_that_is_not_the_exit_that_was_sent(
    alpaca_client: Any,
) -> None:
    """The resolved exit is persisted, so a close ID cannot adopt another order."""
    sdk = alpaca_client._client
    sdk.submit_order.return_value = FakeOrder(side="sell", qty="25", status="accepted")
    request = {"symbol": "AAPL", "close_position": True}
    execute_order(alpaca_client, request, "close-456", reduce_only=True)
    sdk.get_order_by_client_id.side_effect = None
    sdk.get_order_by_client_id.return_value = FakeOrder(side="buy", qty="25", status="filled")
    with pytest.raises(AlpacaClientError, match="different order"):
        execute_order(alpaca_client, request, "close-456", reduce_only=True)


def test_auth_failure_is_not_a_missing_order(alpaca_client: Any) -> None:
    alpaca_client._client.get_order_by_client_id.side_effect = api_error(403, "forbidden")
    with pytest.raises(AlpacaClientError):
        alpaca_client.get_order_by_client_id("test")


def test_sdk_cannot_automatically_replay_posts(alpaca_client: Any) -> None:
    assert alpaca_client._client._retry == 0
    # The mock accepts any attribute, so bind the private name to the installed
    # SDK too: a rename upstream must fail here rather than silently restore
    # POST replay. AlpacaClient.__init__ raises if the attribute is gone.
    assert TradingClient(api_key="k", secret_key="s", paper=True)._retry > 0


def test_relative_state_directory_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-cwd lock would void mutual exclusion between scheduled jobs."""
    monkeypatch.setenv("AUTOTRADER_STATE_DIR", "memory/execution")
    with pytest.raises(ValueError, match="AUTOTRADER_STATE_DIR"):
        state_directory()


def test_state_directory_defaults_inside_the_skill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOTRADER_STATE_DIR", raising=False)
    assert state_directory() == Path(__file__).resolve().parents[1] / "memory" / "execution"


def test_persisted_record_keeps_the_intent_and_the_resolved_order(
    alpaca_client: Any, order_request: dict[str, Any]
) -> None:
    execute_order(alpaca_client, order_request, "record-shape")
    (path,) = state_directory().glob("*.json")
    record = json.loads(path.read_text())
    assert record["intent"]["client_order_id"] == "record-shape"
    assert record["intent"]["request"]["symbol"] == "AAPL"
    assert record["submitted"]["qty"] == 10.0


def test_script_does_not_report_a_timeout_as_an_order_rejection(
    alpaca_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(execute_trade, "AlpacaClient", lambda: alpaca_client)
    monkeypatch.setattr(
        "sys.argv",
        [
            "execute_trade",
            "--symbol",
            "AAPL",
            "--side",
            "buy",
            "--qty",
            "1",
            "--limit-price",
            "200",
            "--client-order-id",
            "timeout-script",
        ],
    )
    alpaca_client._client.submit_order.side_effect = TimeoutError("lost response")
    with pytest.raises(SystemExit):
        execute_trade.main()
    result = json.loads(capsys.readouterr().out)
    assert result["submission_state"] == "unknown"
    assert result["client_order_id"] == "timeout-script"
    assert "allowed" not in result
