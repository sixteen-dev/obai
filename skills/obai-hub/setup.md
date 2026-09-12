# Standalone MCP setup

Read this only for installation, connection troubleshooting or job setup.
The host agent supplies reasoning, persistent state and scheduling; the OBaI
MCP services supply tools. Installing skills alone does not supply either runtime.

## Install the bundle

Preserve the complete `obai-hub` and ten specialist directories, including
`obai-strategy/reference.md`. For paper trading, also preserve the complete
`autotrader` directory (`lib/`, `scripts/`, `context.md`, `pyproject.toml`,
`uv.lock` and references). Exclude local `.venv`, caches, secrets and private
memory when distributing; create a fresh environment and durable state at
the destination. Do not activate the separate `obai` CLI-wrapper skill when
the binary is absent. Install into the host's supported skill directory;
OpenClaw supports workspace `skills/` and its shared skills directory.

From the installed AutoTrader directory, `uv sync --frozen` installs only
its standalone runtime dependencies; the acceptance checks below also need its
dev group, so use `uv sync --frozen --all-groups` on a runner that must run
AutoTrader's offline tests. Resolve this directory to an absolute path
for scheduled commands. No OBaI CLI installation or model-default change
is required.

AutoTrader requires Python 3.12+ and POSIX file locking (Linux/macOS; use a
Linux/WSL runner on Windows). Execution jobs must share its persistent state.

## Connect the services

Reuse healthy servers, or start required services from the supplied Docker
Compose project. Stock domains need `FMP_API_KEY`; news search needs
`TAVILY_API_KEY`, options `MASSIVE_API_KEY`, qualitative research
`EXA_API_KEY`. Configure these on the relevant servers, not in job prompts.
Optional Qdrant education search remains disabled unless separately set up;
that feature also requires OpenAI embeddings. Alpaca keys do not substitute
for market-data provider keys.

The hub's routing table and `mcp-config.json` list the ten default addresses.
Replace `localhost` when the host runs elsewhere or inside a container.
The bundled JSON uses Claude-compatible `mcpServers`; merge its entries
without replacing unrelated configuration. OpenClaw instead uses:

```json
{
  "mcp": {
    "servers": {
      "obai-market-data": {
        "transport": "streamable-http",
        "url": "http://localhost:8002/mcp"
      }
    }
  }
}
```

Inspect the installed host's help before applying version-specific commands.
For OpenClaw, `openclaw mcp add obai-market-data --url
http://localhost:8002/mcp --transport streamable-http` registers this example;
`openclaw mcp doctor obai-market-data --probe` checks the connection.
Probe required servers and list their actual tools from the same runtime
that will execute jobs. A `/health/ready` response alone does not test MCP
authentication, discovery or scheduled credentials. Optional unavailable
domains need not block unrelated work.

## Paper jobs and acceptance

Use AutoTrader's [setup prompt](../autotrader/setup-prompt.md) for the concrete
job workflow. Alpaca credentials are separate paper-account keys supplied
through the host's secret store/environment. Confirm account, clock,
positions and orders read-only; do not place a test order for connectivity.

The host must implement durable per-task state, scheduling, one shared
execution directory/lock per paper account, and the deployed strategy's full
signal/sizing/protection adapter. The bundled signal helper has a deliberately
narrow contract, not full backtest-engine coverage. Run offline checks and
an isolated scheduled dry run before enabling an authorized eligible plan.
Record job IDs, timezones, enabled states, next runs and pause/resume commands.
Markdown coverage is not proof of equal output quality across host models;
compare representative tasks with the same tool evidence before claiming parity.

Sources: [OpenClaw skills](https://docs.openclaw.ai/tools/skills),
[MCP configuration](https://docs.openclaw.ai/tools/mcp),
[persistent scheduling](https://docs.openclaw.ai/automation/cron-jobs),
[Alpaca paper trading](https://docs.alpaca.markets/us/docs/paper-trading).

Instruction design follows OpenAI's [skills guidance](https://learn.chatgpt.com/docs/build-skills),
[GPT-6 Astra guidance](https://developers.openai.com/api/docs/guides/latest-model)
and [GPT-5.6 guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6):
load relevant capabilities, state authorization clearly, avoid conflicting
defaults, retain necessary evidence and verify proportionately. These are
host-neutral instructions; they do not require a particular model.
