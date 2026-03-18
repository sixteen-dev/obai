# Project Instructions for Claude

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately — don't keep pushing
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update `tasks/lessons.md` with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes — don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests — then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. **Plan First**: Write plan to `tasks/todo.md` with checkable items
2. **Verify Plan**: Check in before starting implementation
3. **Track Progress**: Mark items complete as you go
4. **Explain Changes**: High-level summary at each step
5. **Document Results**: Add review section to `tasks/todo.md`
6. **Capture Lessons**: Update `tasks/lessons.md` after corrections

## Core Principles

- **Simplicity First**: Make every change as simple as possible. Impact minimal code.
- **No Laziness**: Find root causes. No temporary fixes. Senior developer standards.
- **Minimal Impact**: Changes should only touch what's necessary. Avoid introducing bugs.

## Development Environment
- Python 3.12+
- Package manager: `uv`
- Linter/Formatter: `ruff`
- Type checker: `mypy` (strict mode)
- Test framework: `pytest`

## Code Standards

### Python Rules
- ALWAYS use `uv run python` instead of `python` or `python3` to execute scripts

### Type Hints
- **ALWAYS** use type hints for all functions, methods, and class attributes
- Import types from `typing` module when needed
- Use  `T | None` for clarity
- Return types must be explicit, never omit them

### Code Structure
- Keep functions under 20 lines
- Single responsibility principle for all functions/classes
- Use descriptive variable names (no single letters except in loops)
- Prefer composition over inheritance
- Use dataclasses for data containers
- Use pathlib for file operations, never os.path

### Imports
- Group imports: stdlib, third-party, local
- Use absolute imports from `src/` directory
- Type-checking imports go under `if TYPE_CHECKING:` block
- ALWAYS place all imports at the top of files
- NEVER use defensive coding around imports (no try/except, no if guards, no conditional imports)

### Error Handling
- No bare `except:` clauses
- Always specify exception types
- Use custom exceptions for domain errors
- Document raised exceptions in docstrings
- Use `structlog` for structured logging in production services
- Always use `logger.exception()` inside except blocks (includes traceback automatically)
- Use `raise ... from e` to preserve exception chains
- Use `traceback.format_exc()` only when sending errors to external services (Sentry, Slack, etc.)
- Never use `traceback.print_exc()` in production — use logging instead

### Documentation
- Google-style docstrings for all public functions/classes
- Include parameter types and return types in docstrings
- Add usage examples for complex functions


## Before Committing Code
All code must pass these checks:
```bash
uv run ruff check . --fix     # Fix linting issues
uv run ruff format .          # Format code
uv run mypy src/ --strict     # Type check (must pass with no errors)
uv run pytest                 # All tests must pass
```

## Testing Requirements
- Minimum 80% code coverage
- Use pytest fixtures for setup
- Test file names: `test_*.py`
- Use descriptive test names that explain what is being tested
- Group related tests in classes
- Mock external dependencies

## DO NOT
- Use `print()` for debugging (use logging)
- Ignore type errors with `# type: ignore` without justification
- Use mutable default arguments
- Import with wildcard `from module import *`
- Leave commented-out code
- Use `assert` for validation (use explicit exceptions)

## ALWAYS
- Handle edge cases explicitly
- Keep dependencies minimal
- Document breaking changes
