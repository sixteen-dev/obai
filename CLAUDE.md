# OBaI

## Project
- **Summary:** AI-powered financial analysis monorepo — MCP servers for market data, screening, options, fundamentals, events/news, prediction markets, backtesting, research, and portfolio management, plus a CLI/TUI client.
- **Stack:** Python 3.12+, FastMCP, OpenAI Agents SDK, Pydantic, FastAPI, DuckDB, structlog
- **Package manager:** `uv` — always use `uv run python` instead of `python` or `python3`
- **Manifests:** `pyproject.toml` (root monorepo), plus per-service `src/*/pyproject.toml`

## Behavioral Guidance
- The approved design is the plan. Implement against it, do not quietly re-design the task mid-flight.
- Don't assume. State assumptions explicitly before coding. If multiple interpretations exist, surface them instead of picking silently.
- If the request or design is unclear, stop and ask. If repo reality conflicts with the design, surface the mismatch before coding.
- Prefer the simplest correct solution. No speculative abstractions, no extra flexibility, no "while I'm here" cleverness.
- Make surgical changes. Touch only what the request requires. Mention unrelated issues, don't fix them unless asked.
- For multi-step work, define success in `step -> verify` form and keep going until the checks pass.
- If 200 lines could be 50, rewrite it.

## Execution Contract
- If changing behavior, write or update a failing test first.
- Implement the smallest change that satisfies the design.
- Run the relevant repo commands below before calling the work done. Default expectation: typecheck, tests, lint, and format.
- For docs, config, or scaffolding changes, run the relevant checks and say what is not applicable.
- Never mark work done without proof.

## Code Rules (Non-Negotiable)

1. **Linear flow.** Max 2 nesting levels. Top to bottom.
2. **Bound loops.** Explicit max on retries, polls, recursion. Define cap behavior.
3. **Small functions.** 40-60 lines max. One job per function.
4. **Own resources.** Open → close on every path, including errors.
5. **Narrow state.** No module globals. Pass deps explicitly.
6. **Assert assumptions.** Guards and validation on every public function. Fail loud.
7. **Never swallow errors.** No bare `except:`. No `except Exception: pass`. Log, raise, or return. Always specify exception types. Use `raise ... from e` to preserve chains.
8. **Visible side effects.** I/O obvious at call site. Separate pure from effectful.
9. **Minimal indirection.** Readable > elegant. One layer of abstraction max.
10. **Surgical changes only.** Touch only what the request requires. Do not refactor adjacent code, comments, or formatting unless the task needs it. Remove only the dead code your change creates.
11. **Warnings = errors.** Linters, typecheckers, analyzers are hard gates. Zero warnings.

## Conventions
- **Type hints:** Required on all functions, methods, class attributes. Use `T | None`, never `Optional`. Return types must be explicit. Type-checking imports go under `if TYPE_CHECKING:`.
- **Imports:** Group: stdlib → third-party → local. Absolute imports from `src/`. No wildcard imports, no defensive import guards (no try/except, no conditional imports). All imports at top of file.
- **Logging:** `structlog` for production. `logger.exception()` in except blocks. Never `traceback.print_exc()` or `print()`.
- **Data:** Dataclasses for data containers. `pathlib` for file ops, never `os.path`.
- **Docstrings:** Google-style for all public functions/classes with param/return types.
- **Testing:** pytest with fixtures. Test files: `test_*.py`. Descriptive test names. Mock external deps. Minimum 80% coverage.

## Commands
```sh
uv run ruff check . --fix        # Lint (fix auto-fixable)
uv run ruff format .             # Format
./scripts/run-all-typechecks.sh  # Type check (must pass with no errors)
./scripts/run-all-tests.sh       # All tests must pass
```

**Never typecheck or test from the repo root.** Each service has its own venv
with its own dependency floors, and the root venv's are older. `mypy src/`
from the root resolves imports against the *root* venv and invents errors that
do not exist in the environment the service actually runs in — `openai` is
2.29.0 at the root vs 2.42.0+ in `src/obai`, so `Reasoning(context=...)` reads
as an unexpected keyword there. Root `pytest` is worse: `testpaths = ["tests"]`
means it collects a handful of cases and exits 0 while every real suite goes
unrun. Both scripts `cd` into each service first, which is the only way the
per-service `[tool.mypy]` block and venv apply.

## Docs
- `docs/spec.md` — Product spec: features, business rules (TODO: create)
- `docs/tech.md` — Architecture: stack, schema, decisions (TODO: create)
- `docs/lessons.md` — Rules from past mistakes; update immediately on correction (TODO: create)
- `docs/prediction-markets-multi-venue-architecture.md` — Prediction markets multi-venue design
- `docs/prediction-markets-implementation-checklist.md` — Implementation checklist
- `docs/prediction-market-terminal-context-plan.md` — Terminal context plan

## Known Pitfalls
- Update this section every time the repo teaches you the same lesson twice.

---
_Every mistake is a rule waiting to be written._
