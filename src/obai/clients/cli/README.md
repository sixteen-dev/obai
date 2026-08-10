# OBaI CLI Client

Modern terminal UI client for OBaI - the Financial Research Agent.

## Features

- **Textual-based TUI**: Beautiful terminal interface with keyboard navigation
- **Welcome Banner**: OBaI logo displayed on startup
- **Collapsible History**: Previous queries collapse for focused viewing
- **Tool Call Hierarchy**: See agents and their MCP tools with real-time spinners
- **Streaming Responses**: Watch responses generate in real-time with markdown rendering
- **Debug Panel**: Toggle (F1) to see detailed event logs
- **Conversation Memory**: DynamoDB-backed session persistence

## Quick Start

```bash
cd src/OBaI
uv run python -m clients.cli.tui
```

## Prerequisites

### 1. MCP Servers Running

Start all 4 MCP servers (in separate terminals):

```bash
cd src/servers/fundamentals-server && uv run fastmcp run server.py  # port 8001
cd src/servers/market-data-server && uv run fastmcp run server.py   # port 8002
cd src/servers/events-news-server && uv run fastmcp run server.py   # port 8003
cd src/servers/options-server && uv run fastmcp run server.py       # port 8004
```

### 2. Environment Variables

```bash
export OPENAI_API_KEY=sk-proj-...

# MCP Server URLs (defaults shown)
export MCP_FUNDAMENTALS_URL=http://localhost:8001/mcp
export MCP_MARKET_DATA_URL=http://localhost:8002/mcp
export MCP_EVENTS_NEWS_URL=http://localhost:8003/mcp
export MCP_OPTIONS_URL=http://localhost:8004/mcp

# Optional: Model configuration
export SPECIALIST_MODEL=gpt-5.6-luna          # Specialist agents model

# The two hub settings are better set with `obai config` (see below) — these
# exports pin them and make the web UI and CLI appear to do nothing.
# export ORCHESTRATOR_MODEL=gpt-5.6-sol         # Central hub model
# export ORCHESTRATOR_REASONING_EFFORT=medium   # none|low|medium|high|xhigh|max

# Optional: Weave tracing
export WANDB_API_KEY=your-key           # Enable W&B Weave tracing
```

The CLI also loads `~/.obai/.env` into the environment at startup (without overriding anything already exported), so a line there behaves exactly like an `export`.

### Hub Model & Reasoning Effort

You do not have to export the two hub settings — `obai config` saves them to `~/.obai/settings.json`, the same file the web UI settings modal writes:

```bash
obai config set-model gpt-5.6-terra   # gpt-5.6-sol | gpt-5.6-terra
obai config set-effort high           # medium | high | xhigh | max
```

Two caveats:

- **`ORCHESTRATOR_MODEL` and `ORCHESTRATOR_REASONING_EFFORT` win over the file.** They remain supported — that is how the eval and regression harnesses pin a hub model — but if either is exported in your shell or sitting in `~/.obai/.env`, `obai config` writes the file and nothing changes. The command warns when it detects this.
- **Changes apply on the next hub start.** Run `obai restart` afterwards; a running session keeps the model and effort it was built with.

With no file present, OBaI uses the shipped defaults (`gpt-5.6-sol`, `medium`), so there is nothing to create on a fresh install or an upgrade.

### 3. Install Dependencies

```bash
cd src/OBaI
uv sync --all-extras
```

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `Enter` | Submit query |
| `Shift+Enter` | New line (multi-line input supported) |
| `F1` | Toggle debug panel |
| `Ctrl+O` | Attach file (CSV, JSON, TXT, MD, YAML, XML) |
| `Ctrl+Y` | Copy last response to clipboard |
| `Ctrl+L` | Clear conversation |
| `Ctrl+C` | Quit |

**Multi-line Input**: The input box supports multiple lines. Press `Shift+Enter` to add new lines, `Enter` to submit your query.

**File Attachment**: Press `Ctrl+O` to open a file picker. Supported formats: CSV, JSON, TXT, MD, YAML, XML. Files over 50KB are truncated.

**Copy/Paste**: Use standard paste shortcuts (`Ctrl+V` or `Ctrl+Shift+V`) in the input. Hold `Shift` while clicking and dragging to select text from responses.

## Commands

Type these in the input field:

| Command | Description |
|---------|-------------|
| `quit` / `exit` | Exit the TUI |
| `clear` | Clear conversation history |

## UI Layout

```
┌─ OBaI - Financial Research Agent ──────────────────┐
│ Hub: gpt-5.6-sol │ Specialist: gpt-5.6-luna │ ✓    │
├────────────────────────────────────────────────────┤
│                                                    │
│    ____  ____        _____                         │
│   / __ \|  _ \      |_   _|                        │
│  | |  | | |_) | __ _  | |                          │
│  | |  | |  _ < / _` | | |                          │
│  | |__| | |_) | (_| |_| |_                         │
│   \____/|____/ \__,_|_____|                        │
│                                                    │
│         Financial Research Agent                   │
│                                                    │
│      Type your question below to get started       │
│                                                    │
├────────────────────────────────────────────────────┤
│ Ask about stocks... (Enter to send)                │
├────────────────────────────────────────────────────┤
│  F1 Debug │ Ctrl+O File │ Ctrl+C Quit │ Shift+Drag  │
└────────────────────────────────────────────────────┘
```

After querying:

```
┌─ You: analyze AAPL ────────────────────────────────┐
│ You: analyze AAPL                                  │
├────────────────────────────────────────────────────┤
│ ✓ Central Hub → analyze                            │
│ ✓ Market Data Agent → market_data_analysis         │
│   └─ ✓ get_stock_quote [234ms]                     │
│   └─ ✓ get_technicals [456ms]                      │
│ ✓ Fundamentals Agent → fundamentals_analysis       │
│   └─ ✓ get_financial_ratios [321ms]                │
│ ✓ Central Hub → synthesize                         │
│                                                    │
│ Response:                                          │
│ ## Apple Inc (AAPL) Analysis                       │
│ ...                                                │
│                                                    │
│ ⏱ 5,234ms │ Specialists: 2 │ Tools: 4              │
└────────────────────────────────────────────────────┘
```

## Debug Mode

Enable debug panel on startup:

```bash
OBAI_DEBUG=1 uv run python -m clients.cli.tui
```

Or press `F1` to toggle during use.

## Architecture

```
tui.py      → Main Textual app, event handling
widgets.py  → Custom widgets (tools, response, debug panel)
```

The TUI processes streaming events from `hub.run()`:
- `AgentUpdatedStreamEvent` → Agent handoffs
- `RunItemStreamEvent` → Tool calls and completions
- `RawResponsesStreamEvent` → Response text tokens

## Troubleshooting

### "Error importing core_agents"

```bash
cd src/OBaI
uv sync --all-extras
```

### "Failed to initialize"

1. Check `OPENAI_API_KEY` is set
2. Verify MCP servers are running
3. Test MCP connectivity:
   ```bash
   uv run python clients/cli/test_connection.py
   ```

### Weave disabled

Set `WANDB_API_KEY` environment variable for W&B Weave tracing.
