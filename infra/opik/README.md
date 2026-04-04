# Opik Self-Hosted (Local Development)

Self-hosted [Opik](https://github.com/comet-ml/opik) instance for LLM tracing and evaluation.

## Quick Start

```bash
# First-time setup (creates persistent external volumes and syncs ClickHouse config)
# Safe to re-run if the local Opik config changes.
./infra/opik/setup-volumes.sh

# Start Opik (all services)
docker compose -f infra/opik/docker-compose.yml up -d

# Wait for healthy (takes ~60-90s on first run for DB migrations)
docker compose -f infra/opik/docker-compose.yml ps

# Configure the Python SDK to use local instance
opik configure --use_local
```

UI available at **http://localhost:5173**

## Start with MCP Servers

To run Opik alongside the MCP servers for full dev environment:

```bash
# Start everything (MCP servers + Opik)
docker compose \
  -f dev/docker-compose.yml \
  -f infra/opik/docker-compose.yml \
  up -d

# Or start separately
docker compose -f dev/docker-compose.yml up -d          # MCP servers
docker compose -f infra/opik/docker-compose.yml up -d   # Opik
```

## Services

| Service | Port | Purpose |
|---------|------|---------|
| Frontend (UI) | 5173 | Trace visualization, experiment comparison |
| Backend (API) | via nginx | REST API, DB migrations |
| Python Backend | internal | Evaluators, optimization |
| MySQL | internal | Metadata storage |
| ClickHouse | internal | Analytics (traces, spans) |
| Redis | internal | Streams, cache |
| MinIO | internal | Object storage |
| ZooKeeper | internal | ClickHouse coordination |

## Data Persistence

All data is stored in **external** Docker volumes (`opik-mysql-data`, `opik-clickhouse-data`, etc.) created by `setup-volumes.sh`.

Data persists across:
- `docker compose down` / `up` cycles
- `docker compose down -v` (external volumes are **not** removed)
- System restarts

To wipe all data:

```bash
docker volume rm $(docker volume ls -q -f name=opik-)
```

## Verify SDK Connection

```python
import opik
opik.configure(use_local=True)

# Should create a trace visible at http://localhost:5173
with opik.track(name="test"):
    pass
```

## MCP Inspector

Use the MCP Inspector to interactively browse tools, test calls, and debug MCP servers:

```bash
npx @modelcontextprotocol/inspector
```

Then connect to any server's MCP endpoint (e.g. `http://localhost:8001/mcp`).

## Stop

```bash
docker compose -f infra/opik/docker-compose.yml down
```
