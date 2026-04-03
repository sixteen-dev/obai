# Project Instructions for Claude

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
