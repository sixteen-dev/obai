"""The MCP server in process: two read-only tools, honest unavailability, health (ADR 0003 §1).

Every tool call goes through ``fastmcp.Client(mcp)``, so the payloads are what an MCP client
receives, not what the handler returned before serialization.
"""

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Final, get_args

import httpx
import pytest
import structlog
from fastmcp import Client
from structlog.testing import capture_logs

from options_backtest import server
from options_backtest.errors import ErrorCode
from options_backtest.ingest import load_strategy
from options_backtest.models.run import FLAT_FEE_SCHEDULE_ID, ZERO_INTEREST_FUNDING_ID
from options_backtest.models.strategy import Structure
from options_backtest.reference.products import PRODUCT_RULES_VERSION
from options_backtest.server import mcp

SERVICE_DIR: Final = Path(__file__).resolve().parents[2]
CONTRACTS_DIR: Final = SERVICE_DIR / "tests" / "contracts"
EXAMPLE: Final = CONTRACTS_DIR / "example-strategy.json"
E2E_STRATEGIES: Final = sorted((SERVICE_DIR / "tests" / "e2e" / "strategies").glob("*.json"))
CAPABILITIES: Final = "options_backtest_capabilities_tool"
VALIDATE: Final = "options_backtest_validate_strategy_tool"
ALL_TOOLS: Final = (
    CAPABILITIES,
    VALIDATE,
    "options_backtest_estimate_tool",
    "options_backtest_prepare_data_tool",
    "options_backtest_create_experiment_tool",
    "options_backtest_submit_tool",
    "options_backtest_job_tool",
    "options_backtest_cancel_tool",
    "options_backtest_result_tool",
    "options_backtest_events_tool",
    "options_backtest_compare_tool",
    "options_backtest_freeze_candidate_tool",
    "options_backtest_research_validation_tool",
)
"""Design §15.1's thirteen tools, in its order."""
UNAVAILABLE_TOOLS: Final = {
    "options_backtest_estimate_tool": ("historical_options_data", ["WP2", "WP5"]),
    "options_backtest_prepare_data_tool": ("historical_options_data", ["WP2", "WP5"]),
    "options_backtest_submit_tool": ("historical_options_data", ["WP2", "WP5"]),
    "options_backtest_job_tool": ("control_plane_jobs", ["WP5"]),
    "options_backtest_cancel_tool": ("control_plane_jobs", ["WP5"]),
    "options_backtest_result_tool": ("control_plane_jobs", ["WP5"]),
    "options_backtest_events_tool": ("control_plane_jobs", ["WP5"]),
    "options_backtest_compare_tool": ("control_plane_jobs", ["WP5"]),
    "options_backtest_create_experiment_tool": ("research_registry", ["WP4", "WP5"]),
    "options_backtest_freeze_candidate_tool": ("research_registry", ["WP4", "WP5"]),
    "options_backtest_research_validation_tool": ("research_registry", ["WP4", "WP5"]),
}
ISSUE_FIELDS: Final = {
    "code",
    "message",
    "json_pointer",
    "retriable",
    "missing_capability",
    "affected_interval",
    "remediation",
}
"""Design §15.2's error shape."""
LOCAL_ONLY: Final = {"available": False, "reason": "individual_license"}


def _example_text() -> str:
    return EXAMPLE.read_text(encoding="utf-8")


def _example(**patch: Any) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(_example_text())
    document.update(patch)
    return document


def _with(path: tuple[str | int, ...], value: object) -> str:
    """Return the example with the member at ``path`` replaced, as JSON text."""
    document = _example()
    node: Any = document
    for part in path[:-1]:
        node = node[part]
    node[path[-1]] = value
    return json.dumps(document)


def _nested_arrays(depth: int) -> list[Any]:
    value: list[Any] = []
    for _ in range(depth - 1):
        value = [value]
    return value


def _sha256sums_line(name: str) -> str:
    for line in (CONTRACTS_DIR / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        digest, listed = line.split("  ", maxsplit=1)
        if listed == name:
            return digest
    raise AssertionError(f"{name} is not in SHA256SUMS")


async def _call(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    async with Client(mcp) as client:
        result = await client.call_tool(name, dict(arguments))
    assert result.structured_content is not None
    return result.structured_content


async def _capabilities(**arguments: Any) -> dict[str, Any]:
    return await _call(CAPABILITIES, arguments)


async def _validate(strategy_json: str, **window: str) -> dict[str, Any]:
    return await _call(VALIDATE, {"strategy_json": strategy_json, **window})


def _codes_at(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [(issue["code"], issue["json_pointer"]) for issue in payload["issues"]]


@pytest.fixture
def _no_structlog_cache() -> Iterator[None]:
    yield
    structlog.reset_defaults()


# Tool list --------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exactly_the_two_read_only_tools_are_registered() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert sorted(tool.name for tool in tools) == sorted([CAPABILITIES, VALIDATE])
    for tool in tools:
        annotations = tool.annotations
        assert annotations is not None
        assert annotations.title
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is True
        assert annotations.openWorldHint is False


@pytest.mark.asyncio
async def test_validate_takes_the_strategy_as_text_and_an_optional_window() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    schema = tools[VALIDATE].inputSchema
    assert schema["required"] == ["strategy_json"]
    assert schema["properties"]["strategy_json"]["type"] == "string"
    assert set(schema["properties"]) == {"strategy_json", "start_date", "end_date"}
    assert set(tools[CAPABILITIES].inputSchema["properties"]) == {"product_family"}


@pytest.mark.asyncio
async def test_validate_says_what_a_rejection_holds_and_what_did_not_run() -> None:
    """The specialist reads this text, so it must not promise every issue of the document."""
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    description = tools[VALIDATE].description
    assert description is not None
    assert "every issue" not in description
    assert "first failing ingestion stage" in description
    assert "product root checks whenever the product block is itself well-formed" in description
    assert "later stages did not run" in description


# Capabilities -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capabilities_advertise_all_thirteen_tools_and_only_two_available() -> None:
    tools = (await _capabilities())["tools"]

    assert list(tools) == list(ALL_TOOLS)
    assert {name for name, entry in tools.items() if entry["available"]} == {
        CAPABILITIES,
        VALIDATE,
    }
    for name in (CAPABILITIES, VALIDATE):
        assert tools[name] == {
            "available": True,
            "missing_capability": None,
            "work_package": [],
            "reason": None,
        }
    for name, (capability, work_packages) in UNAVAILABLE_TOOLS.items():
        assert tools[name]["missing_capability"] == capability
        assert tools[name]["work_package"] == work_packages
        assert tools[name]["reason"]


@pytest.mark.asyncio
async def test_capabilities_carry_versions_read_from_the_code_and_the_contracts() -> None:
    versions = (await _capabilities())["versions"]

    assert versions["engine"] == (SERVICE_DIR / "VERSION").read_text(encoding="utf-8").strip()
    assert versions["product_rules"] == PRODUCT_RULES_VERSION
    schema = json.loads((CONTRACTS_DIR / "strategy.schema.json").read_text(encoding="utf-8"))
    assert versions["strategy_schema"] == {
        "id": "urn:obai:options:strategy:1",
        "version": 1,
        "sha256": _sha256sums_line("strategy.schema.json"),
        "body": schema,
    }
    assert schema["$id"] == versions["strategy_schema"]["id"]


def test_capabilities_refuse_a_packaged_schema_that_is_not_the_pinned_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The body and the identity travel together, so a drifted copy fails loud."""
    monkeypatch.setattr(server, "STRATEGY_SCHEMA_SHA256", "0" * 64)

    with pytest.raises(RuntimeError, match="sha256"):
        server.capabilities_payload(None)


@pytest.mark.asyncio
async def test_capabilities_list_what_the_validator_enforces() -> None:
    payload = await _capabilities()

    assert payload["schema_version"] == 1
    assert payload["deployment_mode"] == "local_single_user"
    assert payload["supported"] == {
        "product_families": [
            {
                "family": "us_european_pm_cash_index",
                "roots": [
                    {"root": "SPXW", "underlying": "SPX"},
                    {"root": "XSP", "underlying": "XSP"},
                ],
                "status": "template_unverified",
            }
        ],
        "structures": list(get_args(Structure)),
        "clock_profiles": ["scheduled_daily_v1"],
        "account_policies": ["fully_funded_v1"],
        "execution_models": ["natural_package_limit_v1"],
        "fee_schedules": [FLAT_FEE_SCHEDULE_ID],
        "funding_policies": [ZERO_INTEREST_FUNDING_ID],
        "entry_dte_min": 7,
    }


@pytest.mark.asyncio
async def test_capabilities_list_the_r1_rejections_with_their_codes() -> None:
    rejected = (await _capabilities())["rejected_in_r1"]

    assert rejected == [
        {"request": "covered_call", "code": "UNSUPPORTED_STRUCTURE", "work_package": "WP7"},
        {"request": "cash_secured_put", "code": "UNSUPPORTED_STRUCTURE", "work_package": "WP7"},
        {"request": "wheel", "code": "UNSUPPORTED_STRUCTURE", "work_package": "WP7"},
        {"request": "naked_short", "code": "UNSUPPORTED_STRUCTURE", "work_package": "WP8"},
        {"request": "calendar_or_diagonal", "code": "UNSUPPORTED_STRUCTURE", "work_package": "WP8"},
        {
            "request": "american_equity_or_etf_options",
            "code": "UNSUPPORTED_PRODUCT",
            "work_package": "WP7",
        },
        {"request": "intraday_or_0dte", "code": "SCHEMA_VIOLATION", "work_package": "WP8"},
        {
            "request": "positive_min_open_interest",
            "code": "DATA_ENTITLEMENT_MISSING",
            "missing_capability": "historical_open_interest",
        },
    ]


def _zero_dte() -> str:
    """Return the example with its entry expiry at zero days to expiration."""
    document = _example()
    document["legs"][0]["expiry_selection"].update(target_dte=0, min_dte=0, max_dte=0)
    return json.dumps(document)


R1_REJECTION_DOCUMENTS: Final = {
    "covered_call": [_with(("structure",), "covered_call")],
    "cash_secured_put": [_with(("structure",), "cash_secured_put")],
    "wheel": [_with(("structure",), "wheel")],
    "naked_short": [_with(("structure",), "naked_short")],
    "calendar_or_diagonal": [_with(("structure",), "calendar"), _with(("structure",), "diagonal")],
    "american_equity_or_etf_options": [
        _with(
            ("product",),
            {
                "underlying_symbol": "SPY",
                "allowed_option_roots": ["SPY"],
                "family": "us_american_physical_equity",
            },
        )
    ],
    "intraday_or_0dte": [_zero_dte(), _with(("clock_profile",), "intraday_v1")],
    "positive_min_open_interest": [_with(("liquidity", "min_open_interest"), 1)],
}
"""One document per ``rejected_in_r1`` request, written as a user would state the request."""


@pytest.mark.asyncio
async def test_each_r1_rejection_advertises_the_code_validation_returns() -> None:
    """ADR 0003 §1.6 and §8 (L2/F5): the payload is read from the code that enforces it."""
    rejected = (await _capabilities())["rejected_in_r1"]

    assert [entry["request"] for entry in rejected] == list(R1_REJECTION_DOCUMENTS)
    for entry in rejected:
        for document in R1_REJECTION_DOCUMENTS[entry["request"]]:
            payload = await _validate(document)
            codes = {issue["code"] for issue in payload["issues"]}
            assert codes == {entry["code"]}, (entry["request"], _codes_at(payload))


@pytest.mark.asyncio
async def test_capabilities_report_historical_backtesting_unavailable_with_a_typed_issue() -> None:
    payload = await _capabilities()

    issue = payload["historical_backtest"]["issue"]
    assert payload["historical_backtest"]["available"] is False
    assert set(issue) == ISSUE_FIELDS
    assert issue["code"] == ErrorCode.DATA_ENTITLEMENT_MISSING.value
    assert issue["missing_capability"] == "historical_options_data"
    assert (issue["json_pointer"], issue["retriable"], issue["affected_interval"]) == (
        "",
        False,
        None,
    )
    assert "WP2" in issue["message"]
    assert issue["remediation"]
    assert payload["data"] == {"provider_manifests": [], "fidelity": "unavailable"}
    assert payload["deployment"] == {"hosted": LOCAL_ONLY, "multi_tenant": LOCAL_ONLY}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("family", "supported"),
    [
        (None, None),
        ("us_european_pm_cash_index", True),
        ("us_american_equity", False),
        ("US_EUROPEAN_PM_CASH_INDEX", False),
    ],
)
async def test_capabilities_answer_the_requested_family_truthfully(
    family: str | None, supported: bool | None
) -> None:
    arguments = {} if family is None else {"product_family": family}

    payload = await _capabilities(**arguments)

    assert payload["requested_product_family"] == family
    assert payload["requested_product_family_supported"] is supported
    assert payload["supported"]["product_families"][0]["family"] == "us_european_pm_cash_index"


# Validation: accepted documents -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [EXAMPLE, *E2E_STRATEGIES], ids=lambda path: path.name)
async def test_every_vendored_strategy_validates_with_the_loaders_facts(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    loaded = load_strategy(text.encode("utf-8"))

    payload = await _validate(text)

    assert payload["valid"] is True
    assert "isError" not in payload
    assert payload["backtest_available"] is False
    assert payload["strategy"]["leg_order"] == list(loaded.leg_order)
    assert payload["strategy"]["premium_direction"] == loaded.premium_direction.value
    assert payload["strategy"]["structure"] == loaded.spec.structure
    assert payload["window"] is None


@pytest.mark.asyncio
async def test_a_valid_strategy_reports_summary_policies_assumptions_and_requirements() -> None:
    payload = await _validate(_example_text(), start_date="2024-03-04", end_date="2024-06-28")
    capabilities = await _capabilities()

    assert payload["strategy"] == {
        "name": "Illustrative XSP 45-DTE put vertical — no historical performance implied",
        "structure": "vertical",
        "product": {
            "underlying_symbol": "XSP",
            "allowed_option_roots": ["XSP"],
            "family": "us_european_pm_cash_index",
        },
        "legs": [
            {"leg_id": "short_put", "side": "sell", "option_type": "put", "ratio": 1},
            {"leg_id": "long_put", "side": "buy", "option_type": "put", "ratio": 1},
        ],
        "leg_order": ["short_put", "long_put"],
        "premium_direction": "credit",
    }
    assert payload["policies"] == {
        "fee_schedule": {
            "schedule_id": FLAT_FEE_SCHEDULE_ID,
            "trade_per_contract": "1.00",
            "exercise_assignment_per_contract": "0.00",
            "cash_settlement_per_contract": "0.00",
        },
        "funding_policy_id": ZERO_INTEREST_FUNDING_ID,
    }
    assert payload["window"] == {
        "start_date": "2024-03-04",
        "end_date": "2024-06-28",
        "session_membership": "unchecked_no_manifest",
    }
    assert payload["data_requirements"] == {
        "historical_options_data": {
            "required": True,
            "available": False,
            "issue": capabilities["historical_backtest"]["issue"],
        }
    }
    schema_identity = {
        key: value
        for key, value in capabilities["versions"]["strategy_schema"].items()
        if key != "body"
    }
    assert payload["versions"] == {**capabilities["versions"], "strategy_schema": schema_identity}
    assert set(schema_identity) == {"id", "version", "sha256"}


@pytest.mark.asyncio
async def test_the_assumptions_name_every_bound_policy_and_the_product_rules() -> None:
    assumptions = " | ".join((await _validate(_example_text()))["assumptions"])

    for token in (
        FLAT_FEE_SCHEDULE_ID,
        "assumed_schedule",
        ZERO_INTEREST_FUNDING_ID,
        "fully_funded_v1",
        "natural_package_limit_v1",
        "cash-settled",
        PRODUCT_RULES_VERSION,
    ):
        assert token in assumptions


@pytest.mark.asyncio
async def test_a_window_with_one_date_keeps_the_other_open() -> None:
    payload = await _validate(_example_text(), start_date="2024-03-04")

    assert payload["window"] == {
        "start_date": "2024-03-04",
        "end_date": None,
        "session_membership": "unchecked_no_manifest",
    }


# Validation: rejections -------------------------------------------------------------------


def _replaced(old: str, new: str) -> str:
    text = _example_text()
    assert text.count(old) == 1, old
    return text.replace(old, new)


REJECTIONS: Final = {
    "malformed": ('{"schema_version": 1,', [("MALFORMED_JSON", "")]),
    "byte_order_mark": ("﻿" + EXAMPLE.read_text(encoding="utf-8"), [("MALFORMED_JSON", "")]),
    "over_64_kib": (EXAMPLE.read_text(encoding="utf-8") + " " * 65536, [("RESOURCE_LIMIT", "")]),
    "depth_over_16": (
        _with(("name",), _nested_arrays(16)),
        [("RESOURCE_LIMIT", "/name" + "/0" * 15)],
    ),
    "duplicate_key": (
        _replaced('"structure": "vertical",', '"structure": "vertical", "structure": "vertical",'),
        [("DUPLICATE_KEY", "/structure")],
    ),
    "nan": (
        _replaced('"max_relative_spread": 0.25', '"max_relative_spread": NaN'),
        [("NONFINITE_NUMBER", "/liquidity/max_relative_spread")],
    ),
    "unknown_field": (json.dumps(_example(colour="blue")), [("UNKNOWN_FIELD", "/colour")]),
    "schema_version_1_0": (
        _replaced('"schema_version": 1,', '"schema_version": 1.0,'),
        [("UNSUPPORTED_SCHEMA_VERSION", "/schema_version")],
    ),
    "spx_root": (
        _with(
            ("product",),
            {**_example()["product"], "underlying_symbol": "SPX", "allowed_option_roots": ["SPX"]},
        ),
        [("UNSUPPORTED_PRODUCT", "/product/allowed_option_roots/0")],
    ),
    "spxw_root_under_xsp": (
        _with(("product", "allowed_option_roots"), ["SPXW"]),
        [("UNSUPPORTED_PRODUCT", "/product/allowed_option_roots/0")],
    ),
    "min_open_interest_1": (
        _with(("liquidity", "min_open_interest"), 1),
        [("DATA_ENTITLEMENT_MISSING", "/liquidity/min_open_interest")],
    ),
    "put_delta_positive": (
        _replaced('"target_delta": -0.30', '"target_delta": 0.30'),
        [("INVALID_SELECTOR", "/legs/0/strike_selection/target_delta")],
    ),
    "unknown_fee_schedule": (
        _with(("fee_schedule_id",), "broker_fees_v9"),
        [("INVALID_STRATEGY_RULE", "/fee_schedule_id")],
    ),
    "unknown_policies_and_foreign_root": (
        json.dumps(
            _example(
                fee_schedule_id="broker_fees_v9",
                funding_policy_id="margin_v2",
                product={**_example()["product"], "allowed_option_roots": ["XSP", "SPXW"]},
            )
        ),
        [
            ("INVALID_STRATEGY_RULE", "/fee_schedule_id"),
            ("INVALID_STRATEGY_RULE", "/funding_policy_id"),
            ("UNSUPPORTED_PRODUCT", "/product/allowed_option_roots/1"),
        ],
    ),
    "lone_surrogate": (
        _replaced('"name": "Illustrative', '"name": "\ud800Illustrative'),
        [("MALFORMED_JSON", "")],
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(REJECTIONS))
async def test_a_rejected_strategy_returns_every_issue_with_code_and_pointer(case: str) -> None:
    strategy_json, expected = REJECTIONS[case]

    payload = await _validate(strategy_json)

    assert payload["isError"] is True
    assert payload["valid"] is False
    assert payload["error"] == f"strategy rejected: {len(expected)} issue(s)"
    assert _codes_at(payload) == expected
    assert all(set(issue) == ISSUE_FIELDS for issue in payload["issues"])
    assert set(payload) == {"isError", "valid", "error", "issues"}


def _without_liquidity(**product: object) -> str:
    """Return the example with ``product`` patched and its required liquidity block removed."""
    document = _example(product={**_example()["product"], **product})
    del document["liquidity"]
    return json.dumps(document)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("strategy_json", "product_issue"),
    [
        (
            _without_liquidity(underlying_symbol="SPX", allowed_option_roots=["SPX"]),
            ("UNSUPPORTED_PRODUCT", "/product/allowed_option_roots/0"),
        ),
        (
            _without_liquidity(allowed_option_roots=["SPXW"]),
            ("UNSUPPORTED_PRODUCT", "/product/allowed_option_roots/0"),
        ),
        (
            _without_liquidity(allowed_option_roots=["SPXW", "SPX"]),
            ("UNSUPPORTED_PRODUCT", "/product/allowed_option_roots/1"),
        ),
    ],
    ids=["spx_root", "spxw_under_xsp", "membership_before_underlying"],
)
async def test_a_schema_rejection_also_runs_the_product_root_checks(
    strategy_json: str, product_issue: tuple[str, str]
) -> None:
    """ADR 0003 §8 (F1/F2/M2/M1): a well-formed product block is checked with the schema stage.

    The root-to-underlying check runs only once every root is an R1 root, so an SPX root beside
    SPXW under XSP is reported once, for its membership.
    """
    payload = await _validate(strategy_json)

    assert _codes_at(payload) == [("SCHEMA_VIOLATION", "/liquidity"), product_issue]
    assert payload["error"] == "strategy rejected: 2 issue(s)"


@pytest.mark.asyncio
async def test_a_semantic_rejection_also_runs_the_root_to_underlying_check() -> None:
    document = _example(product={**_example()["product"], "allowed_option_roots": ["SPXW"]})
    document["liquidity"]["min_open_interest"] = 1

    payload = await _validate(json.dumps(document))

    assert _codes_at(payload) == [
        ("DATA_ENTITLEMENT_MISSING", "/liquidity/min_open_interest"),
        ("UNSUPPORTED_PRODUCT", "/product/allowed_option_roots/0"),
    ]


@pytest.mark.asyncio
async def test_a_malformed_product_block_yields_the_schema_issues_only() -> None:
    document = _example(product={"underlying_symbol": "SPX", "allowed_option_roots": ["SPX"]})
    del document["liquidity"]

    payload = await _validate(json.dumps(document))

    assert _codes_at(payload) == [
        ("SCHEMA_VIOLATION", "/liquidity"),
        ("SCHEMA_VIOLATION", "/product/family"),
    ]


def _spx_text(old: str, new: str) -> str:
    """Return the SPX-root rejection document with one text edit."""
    text = REJECTIONS["spx_root"][0]
    assert text.count(old) == 1, old
    return text.replace(old, new)


PRE_SCHEMA_REJECTIONS: Final = {
    "malformed_json": (REJECTIONS["spx_root"][0][:-1], ("MALFORMED_JSON", "")),
    "duplicate_key": (
        _spx_text('"structure": "vertical",', '"structure": "vertical", "structure": "vertical",'),
        ("DUPLICATE_KEY", "/structure"),
    ),
    "schema_version_2": (
        _spx_text('"schema_version": 1,', '"schema_version": 2,'),
        ("UNSUPPORTED_SCHEMA_VERSION", "/schema_version"),
    ),
    "schema_version_text": (
        _spx_text('"schema_version": 1,', '"schema_version": "1",'),
        ("UNSUPPORTED_SCHEMA_VERSION", "/schema_version"),
    ),
    "not_an_object": (f"[{REJECTIONS['spx_root'][0]}]", ("SCHEMA_VIOLATION", "")),
}
"""SPX-root texts with no product block to read: rejected at stages 1-4, or not an object."""


@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(PRE_SCHEMA_REJECTIONS))
async def test_a_rejection_before_the_schema_stage_yields_no_product_issue(case: str) -> None:
    """Without an object past stage 4 there is no product block, so the SPX root is unchecked."""
    strategy_json, expected = PRE_SCHEMA_REJECTIONS[case]

    payload = await _validate(strategy_json)

    assert _codes_at(payload) == [expected]


@pytest.mark.asyncio
async def test_a_positive_open_interest_names_the_missing_capability() -> None:
    payload = await _validate(REJECTIONS["min_open_interest_1"][0])

    [issue] = payload["issues"]
    assert issue["missing_capability"] == "historical_open_interest"
    assert issue["remediation"] == "set liquidity.min_open_interest to 0"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("window", "expected"),
    [
        ({"start_date": "2024/03/04"}, [("SCHEMA_VIOLATION", "/start_date")]),
        ({"start_date": "20240304"}, [("SCHEMA_VIOLATION", "/start_date")]),
        ({"end_date": "2024-02-30"}, [("SCHEMA_VIOLATION", "/end_date")]),
        ({"start_date": "٢٠٢٤-03-04"}, [("SCHEMA_VIOLATION", "/start_date")]),
        (
            {"start_date": "2024-03-05", "end_date": "2024-03-04"},
            [("SCHEMA_VIOLATION", "/end_date")],
        ),
        (
            {"start_date": "2024-13-01", "end_date": "2024-3-4"},
            [("SCHEMA_VIOLATION", "/end_date"), ("SCHEMA_VIOLATION", "/start_date")],
        ),
    ],
)
async def test_a_malformed_window_is_rejected_at_its_argument(
    window: dict[str, str], expected: list[tuple[str, str]]
) -> None:
    payload = await _validate(_example_text(), **window)

    assert payload["isError"] is True
    assert _codes_at(payload) == expected


@pytest.mark.asyncio
async def test_strategy_and_window_issues_are_reported_together_sorted() -> None:
    payload = await _validate(REJECTIONS["duplicate_key"][0], start_date="20240304")

    assert payload["error"] == "strategy rejected: 2 issue(s)"
    assert _codes_at(payload) == [
        ("SCHEMA_VIOLATION", "/start_date"),
        ("DUPLICATE_KEY", "/structure"),
    ]


@pytest.mark.asyncio
async def test_a_window_issue_never_echoes_the_argument() -> None:
    payload = await _validate(_example_text(), start_date="x" * 5000)

    [issue] = payload["issues"]
    assert "xxxx" not in issue["message"]


# Logging and failures ---------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_structlog_cache")
async def test_each_call_logs_tool_outcome_and_issue_count_never_the_strategy() -> None:
    marker = "Illustrative-marker-3f9a"
    rejected = _with(("name",), marker).replace('"min_open_interest": 0', '"min_open_interest": 1')

    with capture_logs() as logs:
        await _validate(rejected)
        await _validate(_example_text())
        await _capabilities(product_family="us_american_equity")

    calls = [(log["tool"], log["outcome"], log["issue_count"]) for log in logs]
    assert calls == [(VALIDATE, "rejected", 1), (VALIDATE, "valid", 0), (CAPABILITIES, "valid", 0)]
    assert all(log["event"] == "tool_call" for log in logs)
    assert marker not in repr(logs)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_no_structlog_cache")
async def test_an_unexpected_failure_is_logged_and_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_raw: bytes) -> None:
        raise RuntimeError("engine defect")

    monkeypatch.setattr(server, "load_strategy", broken)

    with capture_logs() as logs:
        async with Client(mcp) as client:
            result = await client.call_tool(
                VALIDATE, {"strategy_json": _example_text()}, raise_on_error=False
            )

    assert result.is_error is True
    assert "engine defect" in str(result.content)
    assert [(log["tool"], log["outcome"], log["log_level"]) for log in logs] == [
        (VALIDATE, "error", "error")
    ]


def test_the_payload_builders_refuse_arguments_of_the_wrong_type() -> None:
    with pytest.raises(TypeError, match="strategy_json"):
        server.validation_payload(b"{}", None, None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="start_date"):
        server.validation_payload("{}", 20240304, None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="product_family"):
        server.capabilities_payload(7)  # type: ignore[arg-type]


# Health -----------------------------------------------------------------------------------


async def _get(path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=mcp.http_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_health_reports_liveness_and_the_deployment_mode() -> None:
    response = await _get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "server": "options-backtest-server",
        "version": (SERVICE_DIR / "VERSION").read_text(encoding="utf-8").strip(),
        "deployment_mode": "local_single_user",
    }


@pytest.mark.asyncio
async def test_readiness_separates_api_control_plane_and_data() -> None:
    response = await _get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "server": "options-backtest-server",
        "version": (SERVICE_DIR / "VERSION").read_text(encoding="utf-8").strip(),
        "deployment_mode": "local_single_user",
        "readiness": {"api": "ready", "control_plane": "unavailable", "data": "unavailable"},
        "hosted": LOCAL_ONLY,
        "multi_tenant": LOCAL_ONLY,
        "historical_data": {
            "available": False,
            "reason": "no qualified historical data (WP2 not built)",
        },
    }


# Entry point ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_main_reads_settings_configures_logging_and_serves_stateless_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("TRANSPORT", "HOST", "PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOG_LEVEL", "warning")
    monkeypatch.chdir(tmp_path)
    levels: list[str] = []
    runs: list[dict[str, Any]] = []

    async def run_async(**kwargs: Any) -> None:
        runs.append(kwargs)

    monkeypatch.setattr(server, "configure_logging", levels.append)
    monkeypatch.setattr(mcp, "run_async", run_async)

    await server.main()

    assert levels == ["WARNING"]
    assert runs == [
        {
            "transport": "streamable-http",
            "host": "127.0.0.1",
            "port": 8012,
            "path": "/mcp",
            "stateless_http": True,
            "show_banner": False,
        }
    ]
