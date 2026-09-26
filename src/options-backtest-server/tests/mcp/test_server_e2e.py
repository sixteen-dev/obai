"""The MCP server over the wire: a real process, real HTTP, real MCP (ADR 0003 §4.2).

``python -m options_backtest.server`` runs as a subprocess on loopback and a free port; the
tests speak streamable HTTP to it with ``fastmcp.Client`` and plain HTTP to its health routes.
Zero cost: no model, no market data, no network beyond 127.0.0.1.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from fastmcp import Client

SERVICE_DIR: Final = Path(__file__).resolve().parents[2]
EXAMPLE: Final = SERVICE_DIR / "tests" / "contracts" / "example-strategy.json"
VERSION: Final = (SERVICE_DIR / "VERSION").read_text(encoding="utf-8").strip()
CAPABILITIES: Final = "options_backtest_capabilities_tool"
VALIDATE: Final = "options_backtest_validate_strategy_tool"
READY_ATTEMPTS: Final = 50
READY_INTERVAL_S: Final = 0.1
STOP_TIMEOUT_S: Final = 10.0
LOCAL_ONLY: Final = {"available": False, "reason": "individual_license"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def _child_environment(port: int) -> dict[str, str]:
    environment = {**os.environ, "HOST": "127.0.0.1", "PORT": str(port), "LOG_LEVEL": "WARNING"}
    environment.pop("TRANSPORT", None)
    return environment


def _not_ready_reason(url: str) -> str | None:
    """Return None when ``url`` answers 200, else why it did not."""
    try:
        response = httpx.get(url, timeout=1.0)
    except httpx.TransportError as e:  # not listening yet
        return repr(e)
    return None if response.status_code == 200 else f"HTTP {response.status_code}"


def _wait_until_ready(process: subprocess.Popen[bytes], base_url: str, stderr: Path) -> None:
    """Poll ``/health/ready`` at most ``READY_ATTEMPTS`` times; fail with the child's stderr."""
    reason: str | None = "not polled"
    for _ in range(READY_ATTEMPTS):
        if process.poll() is not None:
            pytest.fail(f"server exited with {process.returncode}:\n{stderr.read_text()}")
        reason = _not_ready_reason(f"{base_url}/health/ready")
        if reason is None:
            return
        time.sleep(READY_INTERVAL_S)
    pytest.fail(
        f"server not ready after {READY_ATTEMPTS} attempts ({reason}):\n{stderr.read_text()}"
    )


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Terminate the child; kill it if it outlives ``STOP_TIMEOUT_S``."""
    process.terminate()
    try:
        process.wait(timeout=STOP_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=STOP_TIMEOUT_S)


@pytest.fixture(scope="module")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Run the server for this module; stop it on every path."""
    workdir = tmp_path_factory.mktemp("server")
    stderr = workdir / "stderr.log"
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    with stderr.open("wb") as sink:
        process = subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            [sys.executable, "-m", "options_backtest.server"],
            cwd=workdir,
            env=_child_environment(port),
            stdout=subprocess.DEVNULL,
            stderr=sink,
        )
        try:
            _wait_until_ready(process, url, stderr)
            yield url
        finally:
            _stop(process)


async def _call(base_url: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with Client(f"{base_url}/mcp") as client:
        result = await client.call_tool(name, arguments)
    assert result.structured_content is not None
    return result.structured_content


def _example_with(old: str, new: str) -> str:
    text = EXAMPLE.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    return text.replace(old, new)


@pytest.mark.asyncio
async def test_the_two_tools_and_their_annotations_over_the_wire(base_url: str) -> None:
    async with Client(f"{base_url}/mcp") as client:
        tools = await client.list_tools()

    assert sorted(tool.name for tool in tools) == [CAPABILITIES, VALIDATE]
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False


@pytest.mark.asyncio
async def test_capabilities_over_the_wire(base_url: str) -> None:
    payload = await _call(base_url, CAPABILITIES, {})

    assert payload["deployment_mode"] == "local_single_user"
    assert payload["versions"]["engine"] == VERSION
    assert [root["root"] for root in payload["supported"]["product_families"][0]["roots"]] == [
        "SPXW",
        "XSP",
    ]
    assert payload["historical_backtest"]["available"] is False
    assert payload["historical_backtest"]["issue"]["code"] == "DATA_ENTITLEMENT_MISSING"
    assert payload["historical_backtest"]["issue"]["missing_capability"] == (
        "historical_options_data"
    )
    assert len(payload["tools"]) == 13  # noqa: PLR2004 — design §15.1
    assert sorted(name for name, tool in payload["tools"].items() if tool["available"]) == [
        CAPABILITIES,
        VALIDATE,
    ]
    assert payload["deployment"] == {"hosted": LOCAL_ONLY, "multi_tenant": LOCAL_ONLY}


@pytest.mark.asyncio
async def test_the_example_strategy_validates_over_the_wire(base_url: str) -> None:
    payload = await _call(
        base_url,
        VALIDATE,
        {"strategy_json": EXAMPLE.read_text(encoding="utf-8"), "start_date": "2024-03-04"},
    )

    assert payload["valid"] is True
    assert payload["backtest_available"] is False
    assert payload["strategy"]["leg_order"] == ["short_put", "long_put"]
    assert payload["strategy"]["premium_direction"] == "credit"
    assert payload["window"]["session_membership"] == "unchecked_no_manifest"
    issue = payload["data_requirements"]["historical_options_data"]["issue"]
    assert issue["code"] == "DATA_ENTITLEMENT_MISSING"


REJECTIONS: Final = {
    "duplicate_key": (
        ('"structure": "vertical",', '"structure": "vertical", "structure": "vertical",'),
        ("DUPLICATE_KEY", "/structure"),
    ),
    "nan": (
        ('"max_relative_spread": 0.25', '"max_relative_spread": NaN'),
        ("NONFINITE_NUMBER", "/liquidity/max_relative_spread"),
    ),
    "spx_root": (
        ('"allowed_option_roots": ["XSP"]', '"allowed_option_roots": ["SPX"]'),
        ("UNSUPPORTED_PRODUCT", "/product/allowed_option_roots/0"),
    ),
    "min_open_interest_1": (
        ('"min_open_interest": 0', '"min_open_interest": 1'),
        ("DATA_ENTITLEMENT_MISSING", "/liquidity/min_open_interest"),
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(REJECTIONS))
async def test_rejections_over_the_wire(base_url: str, case: str) -> None:
    (old, new), expected = REJECTIONS[case]

    payload = await _call(base_url, VALIDATE, {"strategy_json": _example_with(old, new)})

    assert payload["isError"] is True
    assert payload["valid"] is False
    assert [(issue["code"], issue["json_pointer"]) for issue in payload["issues"]] == [expected]


def test_health_bodies_over_the_wire(base_url: str) -> None:
    health = httpx.get(f"{base_url}/health", timeout=5.0)
    ready = httpx.get(f"{base_url}/health/ready", timeout=5.0)

    assert (health.status_code, ready.status_code) == (200, 200)
    assert health.json() == {
        "status": "healthy",
        "server": "options-backtest-server",
        "version": VERSION,
        "deployment_mode": "local_single_user",
    }
    body = ready.json()
    assert body["status"] == "ready"
    assert body["deployment_mode"] == "local_single_user"
    assert body["readiness"] == {
        "api": "ready",
        "control_plane": "unavailable",
        "data": "unavailable",
    }
    assert (body["hosted"], body["multi_tenant"]) == (LOCAL_ONLY, LOCAL_ONLY)
    assert body["historical_data"] == {
        "available": False,
        "reason": "no qualified historical data (WP2 not built)",
    }
