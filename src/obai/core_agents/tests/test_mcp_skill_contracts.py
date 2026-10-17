"""Offline drift checks between exported skills and registered MCP tools."""

import ast
from pathlib import Path
from typing import TypeGuard

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]

# (server directory stem, specialist skill name). Every domain names its skill
# after its server except backtest, whose skill is obai-strategy.
_DOMAINS: tuple[tuple[str, str], ...] = (
    ("fundamentals", "fundamentals"),
    ("market-data", "market-data"),
    ("events-news", "events-news"),
    ("options", "options"),
    ("screening", "screening"),
    ("portfolio", "portfolio"),
    ("research", "research"),
    ("prediction-markets", "prediction-markets"),
    ("crypto", "crypto"),
    ("backtest", "strategy"),
)

# Explicitly gated administration capability, deliberately not routed from a
# specialist skill.
_ADMIN_TOOLS = frozenset(
    {"ensure_prediction_market_history", "ensure_prediction_market_history_tool"}
)


def _server_tree(server: str) -> ast.Module:
    """Parse one MCP server's module without importing it."""
    path = _REPO_ROOT / "src" / f"{server}-server" / "src" / "server.py"
    return ast.parse(path.read_text())


def _is_tool_call(node: ast.expr) -> TypeGuard[ast.Call]:
    """Report whether an expression is an ``mcp.tool(...)`` call."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "tool"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "mcp"
    )


def _tool_name(call: ast.Call, fallback: str) -> str:
    """Return the registered name: the ``name=`` keyword, else the function's."""
    for keyword in call.keywords:
        if keyword.arg == "name":
            return str(ast.literal_eval(keyword.value))
    return fallback


def _call_target(node: ast.Call) -> str:
    """Name the function a call-form registration wraps."""
    target = node.args[0] if node.args else None
    if not isinstance(target, ast.Name):
        raise AssertionError(f"Unrecognized mcp.tool(...) target at line {node.lineno}")
    return target.id


def _registered_tools(tree: ast.Module) -> set[str]:
    """Collect every tool name the module registers.

    Two forms are in use: the ``@mcp.tool(...)`` decorator, and the
    ``mcp.tool(...)(function)`` call that conditional registrations need.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if _is_tool_call(decorator):
                    names.add(_tool_name(decorator, node.name))
            continue
        if isinstance(node, ast.Call):
            registration = node.func
            if _is_tool_call(registration):
                names.add(_tool_name(registration, _call_target(node)))
    return names


def _tool_references(tree: ast.Module) -> int:
    """Count every ``mcp.tool`` reference, in whatever form it appears."""
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "tool"
        and isinstance(node.value, ast.Name)
        and node.value.id == "mcp"
    )


@pytest.mark.parametrize(("server", "skill"), _DOMAINS)
def test_every_registration_form_is_recognized(server: str, skill: str) -> None:
    """A registration this walker cannot read would make the drift check vacuous.

    Every registration form contains exactly one ``mcp.tool`` reference and
    yields exactly one tool name, so the two counts must agree. A mismatch means
    either a new form (a bare ``@mcp.tool``, ``add_tool``, a registration in
    another module) or two registrations resolving to the same name.
    """
    tree = _server_tree(server)
    assert _tool_references(tree) == len(_registered_tools(tree)), server


@pytest.mark.parametrize(("server", "skill"), _DOMAINS)
def test_all_user_facing_tools_are_documented(server: str, skill: str) -> None:
    """A tool the specialist skill never names cannot be routed to."""
    registered = _registered_tools(_server_tree(server)) - _ADMIN_TOOLS
    assert registered, server
    text = (_REPO_ROOT / "skills" / f"obai-{skill}" / "SKILL.md").read_text()
    undocumented = {
        name for name in registered if f"`{name}`" not in text and f"**{name}**" not in text
    }
    assert not undocumented, undocumented
