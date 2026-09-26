# options-backtest-server

Deterministic options strategy backtesting engine for OBaI, specified by
`docs/design/options-backtesting-system-v3.md` (at the repository root; `docs/design/` is
gitignored). It computes results under disclosed assumptions; it does not reconstruct a
broker's fills or promise profitability.

## Status: WP1, ADR 0002 and the ADR 0003 server implemented

- **WP1** ([ADR 0001](docs/adr/0001-wp1-architecture.md)): typed errors, exact `Usd`/`Price`
  (any rounding raises `decimal.Inexact`), strict strategy ingestion (`load_strategy`), and the
  exact double-entry ledger in `engine/{journal,positions,trades,settlement,exercise,adjustments,fees,valuation,funding}.py`.
- **ADR 0002** ([ADR 0002](docs/adr/0002-synthetic-data-and-r1-simulation.md)):
  - synthetic data: `data/` (records, digest-verified `FrozenDataset`, on-disk store, as-of
    view), `reference/` (calendars, products, rates) and `synthetic/`;
  - European pricing, IV and features in `pricing/`;
  - the complete R1 simulation: `engine/{clock,orders,fills,selector,campaign,lifecycle,validity,simulator}.py`
    and `models/{run,artifacts,result}.py`.
- **ADR 0003** ([ADR 0003](docs/adr/0003-obai-integration-routing-slice.md)): a local
  single-user MCP server (`config.py`, `logging_config.py`, `server.py`). It has two read-only
  tools:
  - `options_backtest_capabilities_tool`: what is supported, what R1 rejects, and the other
    eleven design tools, listed as unavailable with the missing capability;
  - `options_backtest_validate_strategy_tool`: strict validation of a strategy document and
    an optional window.

  Historical backtesting is reported as unavailable, with the typed issue
  `DATA_ENTITLEMENT_MISSING` / `historical_options_data`. The server imports no data, synthetic
  or simulation module, so no run is reachable over MCP.
- **Test suites:**
  - `tests/unit/`;
  - `tests/reference/`, with QuantLib as an independent pricing oracle;
  - `tests/e2e/`, the golden scenarios G01–G24, derived by hand against the T0 interface
    before the simulator had a body ([README](tests/e2e/README.md));
  - `tests/conformance/`, which includes the R1Campaign trace checker (`r1_trace.py`);
  - `tests/mcp/`, the service-local MCP end-to-end suite. It starts `python -m
    options_backtest.server` on loopback and a free port and checks the tools, validation,
    rejections and health bodies over real HTTP. It costs nothing: no model and no market
    data. The directory has no `__init__.py`, because a package named `mcp` would shadow the
    MCP SDK;
  - `tests/lean/`, the opt-in LEAN differential. Results are in
    [docs/lean-differential.md](docs/lean-differential.md), which also lists what LEAN does
    not validate.

The engine runs on synthetic data only, and every result carries the
`SYNTHETIC_FIXTURE_NOT_HISTORICAL` warning. Not built yet: a real data provider (WP2), jobs and
runs over MCP (WP5), and the hub route (ADR 0003 Phase B).

## Commands

Run from this directory, never from the repository root:

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src
uv run pytest            # every suite except `lean`; enforces branch coverage >= 90%
uv run pytest --no-cov tests/unit/test_money.py   # partial run
uv run pytest --no-cov tests/mcp                  # MCP end-to-end suite only
```

### Running the server

```sh
uv run python -m options_backtest.server   # streamable HTTP at http://127.0.0.1:8012/mcp
curl -s http://127.0.0.1:8012/health/ready
```

The variables are `TRANSPORT` (`streamable-http`, the default, or its alias `http`), `HOST`
(default `127.0.0.1`), `PORT` (default `8012`) and `LOG_LEVEL` (default `INFO`). They are read
from the environment or a `.env` file in the working directory. The deployment mode is fixed at
`local_single_user` and cannot be configured. Logs are JSON lines on stdout.

The container binds `0.0.0.0` inside its own network namespace. Publish its port on loopback
only:

```sh
docker build -t obai/options-backtest-server:dev .
docker run --rm -p 127.0.0.1:8012:8012 -e HOST=0.0.0.0 obai/options-backtest-server:dev
```

These in-service commands are the portable gate. At the repository root,
`scripts/run-all-tests.sh` runs this service's tests together with every other service's.
`scripts/run-all-typechecks.sh` is local and gitignored (`scripts/*`), so the way it is set up
for this service is not shared.

### LEAN differential (opt-in)

The LEAN differential is marked `lean` and is excluded by default. It needs a LEAN checkout
built at the pinned commit and a .NET SDK, both outside the repository:

```sh
T=$HOME/.local/share/obai-lean
curl -sSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
bash /tmp/dotnet-install.sh --version 10.0.401 --install-dir "$T/dotnet"
git init "$T/Lean" && cd "$T/Lean"
git remote add origin https://github.com/QuantConnect/Lean.git
git fetch --depth 1 origin b1337938bacbdcdf7327ba4c6a10ffa89ffac403 && git checkout FETCH_HEAD
DOTNET_ROOT="$T/dotnet" "$T/dotnet/dotnet" build Launcher/QuantConnect.Lean.Launcher.csproj
```

The last command writes the Debug build to `Launcher/bin/Debug`, which is where the harness
runs the launcher. Then, from this directory:

```sh
LEAN_ROOT=$HOME/.local/share/obai-lean/Lean DOTNET_ROOT=$HOME/.local/share/obai-lean/dotnet \
  uv run pytest --no-cov -m lean tests/lean -v
```

- Without `LEAN_ROOT`, a built launcher, or `dotnet` (under `DOTNET_ROOT`, else on `PATH`), the
  tests skip and give the reason.
- Set `LEAN_REPORT_DIR=<dir>` to keep the per-scenario JSON that
  [docs/lean-differential.md](docs/lean-differential.md) is rendered from.
- Update that report whenever the engine, the export, the replay or LEAN changes.
