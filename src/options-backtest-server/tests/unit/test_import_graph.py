"""The import graph keeps ADR 0002 §1's one-way layering as §17 items 1-3 and 54 state it.

Among ADR 0002's modules imports only go down the layers data -> reference -> pricing -> engine
(with ``models.run``, ``models.artifacts`` and ``models.result``) -> simulator, ``data.asof``'s
import of ``reference.rates`` the one exception (item 1); ``reference`` imports only WP1 and
``data.records`` (item 1); the float64 pricing kernels stay free of the domain model (item 2);
``models.artifacts`` imports only ``engine.clock`` and ``engine.orders`` of WP3's engine
(item 3); ``synthetic`` generates fixtures and no ``src`` module may depend on it; only the
simulator joins selection and execution; the graph is acyclic. Checked on the source (AST), so
an import guarded by ``TYPE_CHECKING`` counts too.
"""

import ast
import importlib
from collections.abc import Iterator
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
PACKAGE = "options_backtest"
SYNTHETIC = f"{PACKAGE}.synthetic"
SELECTOR = f"{PACKAGE}.engine.selector"
FILLS = f"{PACKAGE}.engine.fills"
SIMULATOR = f"{PACKAGE}.engine.simulator"
DATA_RECORDS = f"{PACKAGE}.data.records"
ARTIFACTS = f"{PACKAGE}.models.artifacts"
LAYERS = ((f"{PACKAGE}.data", 1), (f"{PACKAGE}.reference", 2), (f"{PACKAGE}.pricing", 3))
"""ADR 0002 packages by layer, lowest first; WP3's engine and models are 4, the simulator 5."""
UPWARD_ALLOWED = {(f"{PACKAGE}.data.asof", f"{PACKAGE}.reference.rates")}
"""``AsOfView.curve`` returns a ``DiscountCurve`` (§17 item 1)."""
KERNEL_ALLOWED = {
    f"{PACKAGE}.pricing.european": {f"{PACKAGE}.money", f"{PACKAGE}.errors"},
    f"{PACKAGE}.pricing.iv": {
        f"{PACKAGE}.money",
        f"{PACKAGE}.errors",
        f"{PACKAGE}.pricing.european",
    },
}
SERVER_ALLOWED = {
    f"{PACKAGE}.server": {
        f"{PACKAGE}.errors",
        f"{PACKAGE}.ingest",
        f"{PACKAGE}.models.run",
        f"{PACKAGE}.models.strategy",
        f"{PACKAGE}.models.strategy_checks",
        f"{PACKAGE}.reference.products",
        f"{PACKAGE}.strict_json",
        f"{PACKAGE}.config",
        f"{PACKAGE}.logging_config",
    },
    f"{PACKAGE}.config": set(),
    f"{PACKAGE}.logging_config": set(),
}
"""ADR 0003 §1.1 and §8: the MCP server reaches no data, synthetic or simulation module."""
ADR_0002_MODULES = {
    f"{PACKAGE}.data",
    f"{PACKAGE}.data.records",
    f"{PACKAGE}.data.manifest",
    f"{PACKAGE}.data.store",
    f"{PACKAGE}.data.asof",
    f"{PACKAGE}.reference",
    f"{PACKAGE}.reference.calendars",
    f"{PACKAGE}.reference.products",
    f"{PACKAGE}.reference.rates",
    f"{PACKAGE}.pricing",
    f"{PACKAGE}.pricing.european",
    f"{PACKAGE}.pricing.iv",
    f"{PACKAGE}.pricing.features",
    f"{PACKAGE}.synthetic",
    f"{PACKAGE}.synthetic.market",
    f"{PACKAGE}.models.run",
    f"{PACKAGE}.models.artifacts",
    f"{PACKAGE}.models.result",
    f"{PACKAGE}.engine.clock",
    f"{PACKAGE}.engine.orders",
    f"{PACKAGE}.engine.fills",
    f"{PACKAGE}.engine.selector",
    f"{PACKAGE}.engine.campaign",
    f"{PACKAGE}.engine.lifecycle",
    f"{PACKAGE}.engine.validity",
    f"{PACKAGE}.engine.simulator",
}


def _module_name(path: Path) -> str:
    parts = path.relative_to(SRC).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _source_modules() -> dict[str, Path]:
    return {_module_name(path): path for path in sorted((SRC / PACKAGE).rglob("*.py"))}


MODULES = _source_modules()


def _import_names(tree: ast.Module, path: Path) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"relative import in {path}"
            assert node.module is not None
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


def _package_imports(module: str) -> set[str]:
    """Return the package modules ``module`` imports; ``from m import name`` counts as ``m``."""
    path = MODULES[module]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {name for name in _import_names(tree, path) if name in MODULES}


GRAPH = {module: _package_imports(module) for module in MODULES}


def test_every_adr_0002_module_exists() -> None:
    assert MODULES.keys() >= ADR_0002_MODULES, sorted(ADR_0002_MODULES - MODULES.keys())


@pytest.mark.parametrize("module", sorted(MODULES))
def test_every_module_imports(module: str) -> None:
    assert importlib.import_module(module).__name__ == module


def test_no_src_module_imports_synthetic() -> None:
    offenders = {
        module: sorted(name for name in imports if name.startswith(SYNTHETIC))
        for module, imports in GRAPH.items()
        if not module.startswith(SYNTHETIC)
    }
    assert {module: names for module, names in offenders.items() if names} == {}


@pytest.mark.parametrize("module", sorted(SERVER_ALLOWED))
def test_server_modules_import_only_their_allowlist(module: str) -> None:
    assert module in GRAPH, f"{module} does not exist"
    assert GRAPH[module] <= SERVER_ALLOWED[module], sorted(GRAPH[module] - SERVER_ALLOWED[module])


@pytest.mark.parametrize("module", sorted(KERNEL_ALLOWED))
def test_pricing_kernels_import_only_money_and_errors(module: str) -> None:
    assert GRAPH[module] <= KERNEL_ALLOWED[module], sorted(GRAPH[module] - KERNEL_ALLOWED[module])


def _layer(module: str) -> int | None:
    """Return an ADR 0002 module's layer; None for WP1, the package root and ``synthetic``."""
    if module == SIMULATOR:
        return 5
    for package, layer in LAYERS:
        if module == package or module.startswith(f"{package}."):
            return layer
    wp3 = module.startswith((f"{PACKAGE}.engine.", f"{PACKAGE}.models."))
    return 4 if wp3 and module in ADR_0002_MODULES else None


def test_adr_0002_modules_import_only_down_their_layers() -> None:
    layered = {module: layer for module in GRAPH if (layer := _layer(module)) is not None}
    upward = {
        (module, imported)
        for module, layer in layered.items()
        for imported in GRAPH[module]
        if layer < layered.get(imported, 0)
    }
    assert upward <= UPWARD_ALLOWED, sorted(upward - UPWARD_ALLOWED)


def test_reference_imports_only_wp1_and_data_records() -> None:
    reference = [module for module in GRAPH if _layer(module) == 2]  # noqa: PLR2004 — reference
    offenders = {
        module: sorted(GRAPH[module] & ADR_0002_MODULES - {DATA_RECORDS}) for module in reference
    }
    assert {module: names for module, names in offenders.items() if names} == {}


def test_artifacts_import_only_the_clock_and_orders_of_wp3s_engine() -> None:
    wp3_engine = {module for module in ADR_0002_MODULES if module.startswith(f"{PACKAGE}.engine.")}
    allowed = {f"{PACKAGE}.engine.clock", f"{PACKAGE}.engine.orders"}
    assert GRAPH[ARTIFACTS] & wp3_engine <= allowed


def test_only_the_simulator_joins_selection_and_execution() -> None:
    joiners = {module for module, imports in GRAPH.items() if {SELECTOR, FILLS} <= imports}
    assert joiners <= {SIMULATOR}


def test_import_graph_is_acyclic() -> None:
    done: set[str] = set()
    stack: list[str] = []

    def visit(module: str) -> None:
        if module in done:
            return
        assert module not in stack, " -> ".join([*stack[stack.index(module) :], module])
        stack.append(module)
        for imported in sorted(GRAPH[module]):
            visit(imported)
        stack.pop()
        done.add(module)

    for module in sorted(GRAPH):
        visit(module)
