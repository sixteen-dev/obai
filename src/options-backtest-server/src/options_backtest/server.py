"""MCP server: two read-only tools, health routes and the entry point (ADR 0003 §1).

The server validates strategies and advertises what it cannot do; it runs no backtest. It
imports no data, synthetic or simulation module (``tests/unit/test_import_graph.py``), so no
synthetic-data run is reachable over MCP. Importing it only registers the tools and routes:
settings and logging are configured in ``main()``.

The other eleven design §15.1 tools are not registered, not stubbed: a stub would be offered in
``tools/list`` and could only fail. The capabilities payload lists them as unavailable with the
capability and work package that supply them (ADR 0003 §1.8).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import date
from importlib.resources import files
from types import MappingProxyType
from typing import Any, Final, get_args

from fastmcp import FastMCP
from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from options_backtest.config import DEPLOYMENT_MODE, Settings
from options_backtest.errors import ErrorCode, Issue, SpecRejected, sorted_issues
from options_backtest.ingest import SUPPORTED_SCHEMA_VERSION, load_strategy
from options_backtest.logging_config import configure_logging, get_logger
from options_backtest.models.run import (
    ENGINE_VERSION,
    FEE_SCHEDULES,
    FUNDING_POLICIES,
    Policies,
    resolve_policies,
    root_issues,
)
from options_backtest.models.strategy import (
    Account,
    Execution,
    Product,
    StrategySpec,
    Structure,
    TargetDteExpiry,
)
from options_backtest.models.strategy_checks import (
    R1_OPTION_ROOTS,
    ValidatedStrategy,
    root_membership_issues,
)
from options_backtest.reference.products import PRODUCT_RULES_VERSION, R1_FAMILY, product_rules
from options_backtest.strict_json import parse

SERVER_NAME: Final = "options-backtest-server"
CAPABILITIES_TOOL: Final = "options_backtest_capabilities_tool"
VALIDATE_TOOL: Final = "options_backtest_validate_strategy_tool"
CAPABILITIES_SCHEMA_VERSION: Final = 1
STRATEGY_SCHEMA_ID: Final = "urn:obai:options:strategy:1"
STRATEGY_SCHEMA_SHA256: Final = "385ee006868bf6fb9f5c06b5099412f381bc123f48edd71fece1eb58685a7b5e"
"""SHA-256 of ``strategy.schema.json``, the schema ``StrategySpec`` mirrors (``SHA256SUMS``)."""
STRATEGY_SCHEMA_RESOURCE: Final = ("contracts", "strategy.schema.json")
"""The packaged copy of the schema, under the ``options_backtest`` package (ADR 0003 §8)."""
SESSION_MEMBERSHIP_UNCHECKED: Final = "unchecked_no_manifest"
HISTORICAL_DATA_UNAVAILABLE: Final = Issue(
    ErrorCode.DATA_ENTITLEMENT_MISSING,
    "no qualified historical options data: the historical data work package (WP2) is not built",
    "",
    retriable=False,
    missing_capability="historical_options_data",
    remediation="strategy validation is available now; a historical backtest needs the WP2 data "
    "provider and the WP5 job service",
)
"""The typed reason no historical backtest can run (ADR 0003 §1.7)."""

_ISO_DATE: Final = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_READ_ONLY_HINTS: Final = MappingProxyType(
    {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }
)
_MISSING_CAPABILITIES: Final = MappingProxyType(
    {
        "historical_options_data": (
            ("WP2", "WP5"),
            "needs qualified historical options data (WP2) and the job service (WP5)",
        ),
        "control_plane_jobs": (
            ("WP5",),
            "needs the durable job service (WP5); this deployment has no jobs or runs",
        ),
        "research_registry": (
            ("WP4", "WP5"),
            "needs the research registry (WP4) and the job service (WP5)",
        ),
    }
)
"""Capability -> (work packages that supply it, why a tool needing it is unavailable)."""
_UNAVAILABLE_TOOLS: Final = MappingProxyType(
    {
        "options_backtest_estimate_tool": "historical_options_data",
        "options_backtest_prepare_data_tool": "historical_options_data",
        "options_backtest_create_experiment_tool": "research_registry",
        "options_backtest_submit_tool": "historical_options_data",
        "options_backtest_job_tool": "control_plane_jobs",
        "options_backtest_cancel_tool": "control_plane_jobs",
        "options_backtest_result_tool": "control_plane_jobs",
        "options_backtest_events_tool": "control_plane_jobs",
        "options_backtest_compare_tool": "control_plane_jobs",
        "options_backtest_freeze_candidate_tool": "research_registry",
        "options_backtest_research_validation_tool": "research_registry",
    }
)
"""The eleven unregistered design §15.1 tools, in its order, and the capability each lacks."""
_REJECTED_IN_R1: Final = (
    ("covered_call", ErrorCode.UNSUPPORTED_STRUCTURE, "WP7"),
    ("cash_secured_put", ErrorCode.UNSUPPORTED_STRUCTURE, "WP7"),
    ("wheel", ErrorCode.UNSUPPORTED_STRUCTURE, "WP7"),
    ("naked_short", ErrorCode.UNSUPPORTED_STRUCTURE, "WP8"),
    ("calendar_or_diagonal", ErrorCode.UNSUPPORTED_STRUCTURE, "WP8"),
    ("american_equity_or_etf_options", ErrorCode.UNSUPPORTED_PRODUCT, "WP7"),
    ("intraday_or_0dte", ErrorCode.SCHEMA_VIOLATION, "WP8"),
)
"""Requests design §4 rejects in R1: (request, code, the work package that may add it).

Each code is the one validation returns for the request written as stated: the schema refuses
0DTE and intraday (``min_dte`` at least seven, one clock profile), so that entry is a
SCHEMA_VIOLATION (ADR 0003 §8).
"""

mcp = FastMCP(SERVER_NAME, version=ENGINE_VERSION)


# Tools ------------------------------------------------------------------------------------


@mcp.tool(annotations={"title": "Options Backtest Capabilities", **_READ_ONLY_HINTS})
def options_backtest_capabilities_tool(product_family: str | None = None) -> dict[str, Any]:
    """Report what options-strategy backtesting this service supports now and what it cannot do.

    Static facts read from the validator's own code: the strategy schema's identity and body,
    product families and roots, structures, clock, account, execution, fee and funding policies,
    the requests R1 rejects, all thirteen service tools with their availability, and the typed
    reason historical backtesting is unavailable. Read-only; touches no market data.

    Args:
        product_family: Optional product family to check; the answer says if it is supported.

    Returns:
        The capabilities payload.

    """
    return _logged(CAPABILITIES_TOOL, lambda: capabilities_payload(product_family))


@mcp.tool(annotations={"title": "Validate Options Strategy", **_READ_ONLY_HINTS})
def options_backtest_validate_strategy_tool(
    strategy_json: str, start_date: str | None = None, end_date: str | None = None
) -> dict[str, Any]:
    """Validate an options strategy document; report why it is rejected, or its summary.

    The document is JSON text for schema ``urn:obai:options:strategy:1``, whose body the
    capabilities tool returns. Ingestion runs in stages and stops at the first failing one:
    strict parsing (size, encoding, duplicate keys, non-finite numbers), the schema version, the
    schema, then the semantic checks; a document that passes them all gets its policy and
    product-root checks. A rejection carries the first failing ingestion stage's issues plus the
    product root checks whenever the product block is itself well-formed;
    the later stages did not run, so a rejection need not name all of the document's problems.
    The optional window is always checked, for date syntax and order only, and its issues join
    the rejection; session membership needs a data manifest. A valid strategy cannot be
    backtested here: the payload reports historical options data as unavailable with a typed
    issue. Read-only; runs no simulation.

    Args:
        strategy_json: The strategy document as JSON text.
        start_date: Optional first window date, YYYY-MM-DD.
        end_date: Optional last window date, YYYY-MM-DD.

    Returns:
        On rejection ``isError``, ``valid: false`` and the first failing ingestion stage's issues,
        the product root checks when they ran, and any window issue, each with its code, JSON
        pointer, message and remediation; otherwise the validated summary, policies, assumptions,
        data requirements and versions.

    """
    return _logged(VALIDATE_TOOL, lambda: validation_payload(strategy_json, start_date, end_date))


def _logged(tool: str, build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Build a tool's payload and log one event: tool, outcome and issue count, never input."""
    logger = get_logger(__name__)
    try:
        payload = build()
    except Exception:
        logger.exception("tool_call", tool=tool, outcome="error")
        raise
    outcome = "rejected" if payload.get("valid") is False else "valid"
    issue_count = len(payload.get("issues", ()))
    logger.info("tool_call", tool=tool, outcome=outcome, issue_count=issue_count)
    return payload


# Capabilities -----------------------------------------------------------------------------


def capabilities_payload(product_family: str | None) -> dict[str, Any]:
    """Return the capabilities payload of ADR 0003 §1.6; every list is read from the code.

    Args:
        product_family: The family the caller asked about, or None.

    Returns:
        The payload, with ``requested_product_family_supported`` None when no family was asked.

    Raises:
        TypeError: If ``product_family`` is neither a str nor None.

    """
    if product_family is not None and not isinstance(product_family, str):
        raise TypeError(
            f"product_family must be a str or None, got {type(product_family).__name__}"
        )
    family = _product_family()
    supported = None if product_family is None else product_family == family["family"]
    return {
        "schema_version": CAPABILITIES_SCHEMA_VERSION,
        "deployment_mode": DEPLOYMENT_MODE,
        "versions": _capabilities_versions(),
        "supported": _supported(family),
        "rejected_in_r1": _rejected_in_r1(),
        "historical_backtest": {
            "available": False,
            "issue": _issue_dict(HISTORICAL_DATA_UNAVAILABLE),
        },
        "data": {"provider_manifests": [], "fidelity": "unavailable"},
        "tools": _tools(),
        "deployment": _deployment(),
        "requested_product_family": product_family,
        "requested_product_family_supported": supported,
    }


def _product_family() -> dict[str, Any]:
    """Return the R1 family with its roots, read from the product rules of ``R1_OPTION_ROOTS``."""
    rules = [product_rules(root) for root in sorted(R1_OPTION_ROOTS)]
    statuses = {rule.status for rule in rules}
    if {rule.family for rule in rules} != {R1_FAMILY} or len(statuses) != 1:
        raise RuntimeError(f"the R1 roots must share family {R1_FAMILY!r} and one rules status")
    return {
        "family": R1_FAMILY,
        "roots": [{"root": rule.root, "underlying": rule.underlying_id} for rule in rules],
        "status": statuses.pop(),
    }


def _supported(family: dict[str, Any]) -> dict[str, Any]:
    return {
        "product_families": [family],
        "structures": list(get_args(Structure)),
        "clock_profiles": _literal_values(StrategySpec, "clock_profile"),
        "account_policies": _literal_values(Account, "policy"),
        "execution_models": _literal_values(Execution, "model"),
        "fee_schedules": sorted(FEE_SCHEDULES),
        "funding_policies": sorted(FUNDING_POLICIES),
        "entry_dte_min": _entry_dte_min(),
    }


def _literal_values(model: type[BaseModel], field: str) -> list[str]:
    """Return the values of a ``Literal`` field, the set the strict model accepts."""
    values = get_args(model.model_fields[field].annotation)
    if not values or not all(isinstance(value, str) for value in values):
        raise RuntimeError(f"{model.__name__}.{field} is not a string Literal")
    return list(values)


def _entry_dte_min() -> int:
    """Return the schema's lower bound on an entry expiry's calendar days to expiration."""
    minimum: object = TargetDteExpiry.model_json_schema()["properties"]["min_dte"]["minimum"]
    if not isinstance(minimum, int):
        raise RuntimeError(f"TargetDteExpiry.min_dte has no integer minimum: {minimum!r}")
    return minimum


def _rejected_in_r1() -> list[dict[str, str]]:
    rejected = [
        {"request": request, "code": code.value, "work_package": work_package}
        for request, code, work_package in _REJECTED_IN_R1
    ]
    rejected.append(
        {
            "request": "positive_min_open_interest",
            "code": ErrorCode.DATA_ENTITLEMENT_MISSING.value,
            "missing_capability": "historical_open_interest",
        }
    )
    return rejected


def _tools() -> dict[str, dict[str, Any]]:
    tools: dict[str, dict[str, Any]] = {
        name: {"available": True, "missing_capability": None, "work_package": [], "reason": None}
        for name in (CAPABILITIES_TOOL, VALIDATE_TOOL)
    }
    for name, capability in _UNAVAILABLE_TOOLS.items():
        work_packages, reason = _MISSING_CAPABILITIES[capability]
        tools[name] = {
            "available": False,
            "missing_capability": capability,
            "work_package": list(work_packages),
            "reason": reason,
        }
    return tools


def _deployment() -> dict[str, dict[str, Any]]:
    local_only = {"available": False, "reason": "individual_license"}
    return {"hosted": dict(local_only), "multi_tenant": dict(local_only)}


def _versions() -> dict[str, Any]:
    return {
        "engine": ENGINE_VERSION,
        "product_rules": PRODUCT_RULES_VERSION,
        "strategy_schema": {
            "id": STRATEGY_SCHEMA_ID,
            "version": SUPPORTED_SCHEMA_VERSION,
            "sha256": STRATEGY_SCHEMA_SHA256,
        },
    }


def _capabilities_versions() -> dict[str, Any]:
    """Return the versions with the schema body the specialist writes documents against."""
    versions = _versions()
    versions["strategy_schema"]["body"] = _strategy_schema_body()
    return versions


def _strategy_schema_body() -> dict[str, Any]:
    """Read the packaged strategy schema, refusing any copy that is not the pinned contract.

    Raises:
        RuntimeError: If the packaged file's sha256 is not ``STRATEGY_SCHEMA_SHA256``, or the
            file is not a JSON object.

    """
    raw = files("options_backtest").joinpath(*STRATEGY_SCHEMA_RESOURCE).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != STRATEGY_SCHEMA_SHA256:
        raise RuntimeError(
            f"the packaged strategy schema has sha256 {digest}, not {STRATEGY_SCHEMA_SHA256}"
        )
    body: object = json.loads(raw)
    if not isinstance(body, dict):
        raise RuntimeError("the packaged strategy schema is not a JSON object")
    return body


def _issue_dict(issue: Issue) -> dict[str, Any]:
    """Return an issue in the design §15.2 error shape, its code as the code's string."""
    return {
        "code": issue.code.value,
        "message": issue.message,
        "json_pointer": issue.json_pointer,
        "retriable": issue.retriable,
        "missing_capability": issue.missing_capability,
        "affected_interval": issue.affected_interval,
        "remediation": issue.remediation,
    }


# Validation -------------------------------------------------------------------------------


def validation_payload(
    strategy_json: str, start_date: str | None, end_date: str | None
) -> dict[str, Any]:
    """Validate a strategy document and an optional window (ADR 0003 §1.5).

    Args:
        strategy_json: The strategy document as JSON text.
        start_date: Optional first window date, YYYY-MM-DD.
        end_date: Optional last window date, YYYY-MM-DD.

    Returns:
        The first failing ingestion stage's issues, the product root checks when the product
        block is well-formed, and every window issue, sorted by (pointer, code), as a rejection;
        else the validated summary. Issues are returned, never raised.

    Raises:
        TypeError: If an argument has the wrong type.

    """
    if not isinstance(strategy_json, str):
        raise TypeError(f"strategy_json must be a str, got {type(strategy_json).__name__}")
    bound, strategy_issues = _check_strategy(strategy_json)
    window, window_issues = _check_window(start_date, end_date)
    issues = sorted_issues([*strategy_issues, *window_issues])
    if issues:
        return _rejection(issues)
    if bound is None:  # a raise, not an assert, so ``python -O`` keeps it
        raise AssertionError("a strategy without issues must have bound policies")
    return _accepted(*bound, window)


def _check_strategy(
    strategy_json: str,
) -> tuple[tuple[ValidatedStrategy, Policies] | None, list[Issue]]:
    """Ingest the text, bind its policies and check its roots; collect the issues.

    ``surrogatepass`` lets a lone surrogate reach ``strict_json``'s strict UTF-8 decode, which
    reports it as MALFORMED_JSON at "". A rejected document keeps its failing stage's issues and
    gains the product root checks when its product block is well-formed (ADR 0003 §8).
    """
    raw = strategy_json.encode("utf-8", "surrogatepass")
    try:
        strategy = load_strategy(raw)
    except SpecRejected as e:
        return None, _merged(e.issues, _product_issues(raw))
    foreign_roots = root_issues(strategy.spec.product)
    try:
        policies = resolve_policies(strategy.spec)
    except SpecRejected as e:
        return None, [*e.issues, *foreign_roots]
    if foreign_roots:
        return None, foreign_roots
    return (strategy, policies), []


def _product_issues(raw: bytes) -> list[Issue]:
    """Run the product root checks on a rejected document's well-formed product block.

    The R1 membership check runs first; the root-to-underlying check needs every root's product
    rules, so it runs only when every root is an R1 root.
    """
    product = _well_formed_product(raw)
    if product is None:
        return []
    membership = root_membership_issues(product)
    if membership:
        return membership
    return root_issues(product)


def _well_formed_product(raw: bytes) -> Product | None:
    """Return the product block when ingestion stages 1-4 passed and it validates, else None.

    ``load_strategy`` already rejected these same bytes and reported the failing stage's issues,
    so a None here drops no issue: a stage 1-4 failure leaves no document to read, and a malformed
    product block is named by the schema stage.
    """
    try:
        tree = parse(raw)
    except SpecRejected:
        return None  # stages 1-3; load_strategy reported these issues from the same bytes
    if not isinstance(tree, Mapping):
        return None  # not an object; the schema stage reported it at ""
    version = tree.get("schema_version")
    if type(version) is not int or version != SUPPORTED_SCHEMA_VERSION:
        return None  # stage 4; load_strategy reported it
    try:
        return Product.model_validate(tree.get("product"))
    except ValidationError:
        return None  # the schema stage reported the malformed or missing product block


def _merged(stage_issues: tuple[Issue, ...], product_issues: list[Issue]) -> list[Issue]:
    """Join the product checks to the failing stage's issues, one issue per (pointer, code)."""
    seen = {(issue.json_pointer, issue.code) for issue in stage_issues}
    extra = [issue for issue in product_issues if (issue.json_pointer, issue.code) not in seen]
    return [*stage_issues, *extra]


def _check_window(
    start_date: str | None, end_date: str | None
) -> tuple[dict[str, Any] | None, list[Issue]]:
    """Check the window's syntax and order; None when neither date is given."""
    start, start_issues = _parse_date(start_date, "start_date")
    end, end_issues = _parse_date(end_date, "end_date")
    issues = [*start_issues, *end_issues]
    if start is not None and end is not None and end < start:
        message = f"end_date {end} is before start_date {start}"
        issues.append(Issue(ErrorCode.SCHEMA_VIOLATION, message, "/end_date"))
    if issues or (start is None and end is None):
        return None, issues
    window = {
        "start_date": None if start is None else start.isoformat(),
        "end_date": None if end is None else end.isoformat(),
        "session_membership": SESSION_MEMBERSHIP_UNCHECKED,
    }
    return window, []


def _parse_date(value: str | None, name: str) -> tuple[date | None, list[Issue]]:
    """Parse ``YYYY-MM-DD``; the pattern first, since ``fromisoformat`` accepts other shapes.

    The issue never echoes the argument, which has no length bound.
    """
    if value is None:
        return None, []
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str or None, got {type(value).__name__}")
    pointer = f"/{name}"
    if _ISO_DATE.fullmatch(value) is None:
        message = f"{name} must be a date written YYYY-MM-DD"
        return None, [Issue(ErrorCode.SCHEMA_VIOLATION, message, pointer)]
    try:
        return date.fromisoformat(value), []
    except ValueError as e:
        message = f"{name} is not a calendar date: {e}"
        return None, [Issue(ErrorCode.SCHEMA_VIOLATION, message, pointer)]


def _rejection(issues: tuple[Issue, ...]) -> dict[str, Any]:
    return {
        "isError": True,
        "valid": False,
        "error": f"strategy rejected: {len(issues)} issue(s)",
        "issues": [_issue_dict(issue) for issue in issues],
    }


def _accepted(
    strategy: ValidatedStrategy, policies: Policies, window: dict[str, Any] | None
) -> dict[str, Any]:
    unavailable = _issue_dict(HISTORICAL_DATA_UNAVAILABLE)
    return {
        "valid": True,
        "strategy": _strategy_summary(strategy),
        "policies": _policies_summary(policies),
        "window": window,
        "assumptions": _assumptions(strategy.spec, policies),
        "data_requirements": {
            "historical_options_data": {"required": True, "available": False, "issue": unavailable}
        },
        "backtest_available": False,
        "versions": _versions(),
    }


def _strategy_summary(strategy: ValidatedStrategy) -> dict[str, Any]:
    """Return a quotable summary; not a document ``load_strategy`` accepts back (§1.5)."""
    spec = strategy.spec
    return {
        "name": spec.name,
        "structure": spec.structure,
        "product": {
            "underlying_symbol": spec.product.underlying_symbol,
            "allowed_option_roots": list(spec.product.allowed_option_roots),
            "family": spec.product.family,
        },
        "legs": [
            {
                "leg_id": leg.leg_id,
                "side": leg.side,
                "option_type": leg.option_type,
                "ratio": leg.ratio,
            }
            for leg in spec.legs
        ],
        "leg_order": list(strategy.leg_order),
        "premium_direction": strategy.premium_direction.value,
    }


def _policies_summary(policies: Policies) -> dict[str, Any]:
    schedule = policies.fee_schedule
    return {
        "fee_schedule": {
            "schedule_id": schedule.schedule_id,
            "trade_per_contract": str(schedule.trade_per_contract.amount),
            "exercise_assignment_per_contract": str(
                schedule.exercise_assignment_per_contract.amount
            ),
            "cash_settlement_per_contract": str(schedule.cash_settlement_per_contract.amount),
        },
        "funding_policy_id": policies.funding_policy_id,
    }


def _assumptions(spec: StrategySpec, policies: Policies) -> list[str]:
    schedule = policies.fee_schedule
    return [
        f"fees: {schedule.schedule_id} (cost basis {schedule.cost_basis}): an illustrative "
        "schedule, not a broker's fees",
        f"funding: {policies.funding_policy_id}: zero interest on cash, no borrowing",
        f"account: {spec.account.policy}: every position fully funded, no leverage",
        f"execution: {spec.execution.model}: package limit orders fill only at later quote "
        "observations",
        f"settlement: {spec.product.family}: European options, PM cash-settled on the official "
        "close",
        f"product rules: {PRODUCT_RULES_VERSION}: contract terms are templates not yet verified "
        "against Cboe",
    ]


# Health -----------------------------------------------------------------------------------


def _identity() -> dict[str, str]:
    return {"server": SERVER_NAME, "version": ENGINE_VERSION, "deployment_mode": DEPLOYMENT_MODE}


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Liveness: the process serves HTTP.

    Args:
        _request: The request; unused.

    Returns:
        200 with the server identity and deployment mode.

    """
    return JSONResponse({"status": "healthy", **_identity()})


@mcp.custom_route("/health/ready", methods=["GET"])
async def health_ready(_request: Request) -> JSONResponse:
    """Readiness: the API serves both tools; control plane and data are unavailable (§1.4).

    Args:
        _request: The request; unused.

    Returns:
        200 with API, control-plane and data readiness and the license-bound deployment facts.

    """
    return JSONResponse(
        {
            "status": "ready",
            **_identity(),
            "readiness": {"api": "ready", "control_plane": "unavailable", "data": "unavailable"},
            **_deployment(),
            "historical_data": {
                "available": False,
                "reason": "no qualified historical data (WP2 not built)",
            },
        }
    )


# Entry point ------------------------------------------------------------------------------


async def main() -> None:
    """Serve stateless streamable HTTP at ``/mcp`` with the environment's settings.

    The banner is off: printing it checks PyPI for a newer fastmcp, and this server makes no
    outbound call.
    """
    settings = Settings()
    configure_logging(settings.log_level)
    get_logger(__name__).info(
        "server_starting",
        server=SERVER_NAME,
        version=ENGINE_VERSION,
        deployment_mode=DEPLOYMENT_MODE,
        transport=settings.transport,
        host=settings.host,
        port=settings.port,
    )
    await mcp.run_async(
        transport=settings.transport,
        host=settings.host,
        port=settings.port,
        path="/mcp",
        stateless_http=True,
        show_banner=False,
    )


if __name__ == "__main__":
    asyncio.run(main())
