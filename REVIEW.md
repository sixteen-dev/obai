# Code Review Guidelines

This is a financial trading platform with multi-agent AI (OpenAI Agent SDK),
MCP servers (FastMCP), and Alpaca brokerage integration. Bugs here can cause
real money loss or data corruption. Be direct and assertive.

Only report issues you can tie to a concrete file and line in the PR diff.
For each finding, include severity (P0-P3), the concrete impact, and the smallest safe fix.
If evidence is incomplete, call that out and do not speculate.

## Always check

- Security: secrets in code/logs, injection, auth bypass, SSRF, unsafe deserialization
- Correctness: wrong logic, off-by-one, float precision for money, silent data loss
- Edge cases: empty responses, None where not expected, API timeouts, partial failures
- Concurrency: race conditions, shared mutable state, async pitfalls
- Error handling: bare except, swallowed exceptions, missing error propagation
- Breaking changes: public API signatures, config schema, protocol changes
- MCP servers: unvalidated external API responses, API keys in logs, missing rate limits
- Agent layer: session/state leaking between users, prompt injection via user input,
  tool call results trusted without validation, circular agent dependencies

## Skip

- Code style, formatting, naming, import ordering (handled by ruff)
- Type hints, docstrings (handled by mypy --strict)
- "Could be cleaner" or "consider refactoring" suggestions
- Adding comments or logging
- Test coverage unless a critical money/security path is untested
- Performance unless it's an actual bottleneck (not theoretical)
