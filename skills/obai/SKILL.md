---
name: obai
description: "Use the OBaI CLI to answer financial and stock market questions by running `obai query` commands. Trigger this skill whenever the user asks about stock prices, earnings, fundamentals, options, market movers, portfolio analysis, screening, dividends, SEC filings, insider trades, backtesting strategies, or any financial market question. The agent should autonomously run `obai query \"<question>\" --json`, parse the JSON output, and present the answer — without asking the user to run commands themselves. Also trigger when the user asks to check OBaI server health, run evaluations, or compare stocks."
---

# OBaI CLI — Agent Execution Guide

OBaI is a multi-agent AI system for stock market research. When a user asks a financial question, **run the query yourself** using `obai query` and return the answer. Do not tell the user to run commands — execute them directly.

## How to Answer Financial Questions

1. Run `obai query "<user's question>" --json`
2. Parse the JSON response
3. Present the answer to the user in a clear format

```bash
# Always use --json for structured output you can parse
obai query "What is AAPL trading at?" --json
```

The system routes queries to the right specialist agents automatically — you don't need to pick which agent to use. Just pass the natural language question.

## Core Command: `obai query`

```bash
obai query "<question>" [OPTIONS]
```

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--json` | `-j` | `false` | Structured JSON output (always use this) |
| `--session` | `-s` | ephemeral | Named session for multi-turn conversation |
| `--model` | `-m` | `gpt-5.1` | Override orchestrator model |

### JSON Output Structure

```json
{
  "query": "What is AAPL trading at?",
  "response": "Apple Inc (AAPL) is currently trading at $255.78...",
  "agents_called": ["market_data"],
  "tool_calls": [{"tool": "get_quote", "agent": "market_data"}],
  "elapsed_ms": 2340,
  "session_id": "cli_a1b2c3d4",
  "timestamp": "2026-03-16T10:30:00Z",
  "model": "gpt-5.1",
  "guardrail_rejected": false,
  "faithfulness": {"numeric_accuracy": 0.95, "faithfulness_pass": true},
  "completeness": {"coverage_score": 0.88, "completeness_pass": true}
}
```

Key fields to use:
- `response` — the answer text to present to the user
- `agents_called` — which specialists were used
- `tool_calls` — specific MCP tools invoked
- `guardrail_rejected` — `true` if query was blocked as non-financial
- `error` — present only on failure, contains `type` and `message`

### Exit Codes

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success | Parse JSON, present `response` |
| `1` | Guardrail rejection | Query was non-financial, tell the user |
| `3` | Infrastructure error | Check `obai status --json`, report which servers are down |

### Reading from stdin

```bash
echo "What are the top gainers today?" | obai query - --json
```

## Multi-Turn Research

Use `--session` to maintain conversation context across queries. Same session ID = same memory.

```bash
obai query "Analyze AAPL earnings trend" --json --session research1
obai query "Now compare with MSFT" --json --session research1
obai query "Which has better growth prospects?" --json --session research1
```

The second and third queries understand context from the first because they share the session.

## Server Health Check

Before querying, verify servers are running:

```bash
obai status --json
```

Returns:
```json
{
  "servers": [
    {"name": "Fundamentals", "url": "http://localhost:8001/mcp", "status": "ok", "latency_ms": 125},
    {"name": "Market Data", "url": "http://localhost:8002/mcp", "status": "ok", "latency_ms": 98}
  ],
  "all_healthy": true
}
```

If `all_healthy` is `false`, report which servers are down. Exit code `3` means at least one server is unhealthy.

## What Each Agent Handles

Route awareness helps you understand the response, but you don't need to pick agents — the Hub does that automatically.

| Agent | Handles | Example Questions |
|-------|---------|-------------------|
| **Fundamentals** | Financials, ratios, SEC filings, insider trades, revenue segments | "What is AAPL's P/E ratio?", "Show MSFT income statement" |
| **Market Data** | Prices, quotes, candles, movers, market status | "What is AAPL trading at?", "Top gainers today" |
| **Events/News** | Earnings calendar, dividends, news search | "When does NVDA report earnings?", "Latest news on Tesla" |
| **Options** | Options chains, Greeks, IV, open interest | "Show AAPL options chain", "What's the IV on SPY puts?" |
| **Screening** | Stock screening with filters | "Find tech stocks with P/E under 20" |
| **Portfolio** | Portfolio analysis, ETF holdings, risk, treasury rates | "Analyze a portfolio of AAPL, MSFT, GOOGL", "Current treasury rates" |
| **Strategy** | Backtest design, iteration, performance metrics | "Design a momentum strategy for AAPL" |

Multi-domain queries (e.g., "Compare AAPL earnings with options flow") automatically dispatch to multiple agents in parallel.

## Evaluation

Run evaluations to assess agent quality:

```bash
# From src/obai/

# Single query evaluation with scoring
uv run python -m evaluation evaluate "What is AAPL trading at?" --json

# Full test suite
uv run python -m evaluation evaluate --suite --report results.md

# Fast mode (skip LLM judge)
uv run python -m evaluation evaluate --suite --no-builtin

# Filter by category
uv run python -m evaluation evaluate --suite --category A
```

| Flag | Short | Description |
|------|-------|-------------|
| `--suite` | `-s` | Run full test suite |
| `--model` | `-m` | Model for queries (default: `gpt-4o`) |
| `--judge` | `-j` | Model for LLM scorers (default: `anthropic/claude-sonnet-4-5-20250929`) |
| `--no-builtin` | | Skip Opik built-in scorers (faster) |
| `--category` | `-c` | Filter: A, B, C, or D |
| `--file` | `-f` | Custom YAML test file |
| `--report` | `-r` | Export markdown report |
| `--export` | `-e` | Export raw JSON results |

## Error Handling

**Guardrail rejection** (`exit 1`): The query was blocked as non-financial. The JSON will have `"guardrail_rejected": true` with an error message explaining why.

**Auth error** (`exit 3`): Usually means `OPENAI_API_KEY` is not set. Check the environment.

**MCP connection error** (`exit 3`): One or more servers are down. Run `obai status --json` to diagnose, then suggest `./setup.sh` or `docker compose up -d`.

## Configuration (Environment Variables)

| Variable | Default | What it does |
|----------|---------|-------------|
| `OPENAI_API_KEY` | required | OpenAI API key for all agents |
| `ORCHESTRATOR_MODEL` | `gpt-5.1` | Central Hub model |
| `SPECIALIST_MODEL` | `gpt-5-mini` | Default specialist model |
| `STRATEGY_MODEL` | `gpt-5.1` | Strategy agent model |
| `ENABLE_GUARDRAILS` | `true` | Block non-financial queries |
| `ENABLE_INLINE_SCORING` | `true` | Score every query for faithfulness |
| `MCP_TIMEOUT` | `30` | Request timeout (seconds) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
