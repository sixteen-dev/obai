# options-backtest-server

Deterministic options strategy backtesting engine for OBaI, specified by
`docs/design/options-backtesting-system-v3.md` (at the repository root; `docs/design/` is
gitignored). It computes results under disclosed assumptions; it does not reconstruct a
broker's fills or promise profitability.

## Status: WP1 and ADR 0002 implemented

- **WP1** ([ADR 0001](docs/adr/0001-wp1-architecture.md)): typed errors, exact `Usd`/`Price`
  (any rounding raises `decimal.Inexact`), strict strategy ingestion (`load_strategy`), and the
  exact double-entry ledger in `engine/{journal,positions,trades,settlement,exercise,adjustments,fees,valuation,funding}.py`.
- **ADR 0002** ([ADR 0002](docs/adr/0002-synthetic-data-and-r1-simulation.md)):
  - synthetic data: `data/` (records, digest-verified `FrozenDataset`, on-disk store, as-of
    view), `reference/` (calendars, products, rates) and `synthetic/`;
  - European pricing, IV and features in `pricing/`;
  - the complete R1 simulation: `engine/{clock,orders,fills,selector,campaign,lifecycle,validity,simulator}.py`
    and `models/{run,artifacts,result}.py`.
- **Test suites:**
  - `tests/unit/`;
  - `tests/reference/`, with QuantLib as an independent pricing oracle;
  - `tests/e2e/`, the golden scenarios G01–G24, derived by hand against the T0 interface
    before the simulator had a body ([README](tests/e2e/README.md));
  - `tests/conformance/`, which includes the R1Campaign trace checker (`r1_trace.py`);
  - `tests/lean/`, the opt-in LEAN differential. Results are in
    [docs/lean-differential.md](docs/lean-differential.md), which also lists what LEAN does
    not validate.

The engine runs on synthetic data only, and every result carries the
`SYNTHETIC_FIXTURE_NOT_HISTORICAL` warning. Not built yet: a real data provider, a server, jobs
and hub integration (ADR 0002 §14).

## Commands

Run from this directory, never from the repository root:

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict src
uv run pytest            # every suite except `lean`; enforces branch coverage >= 90%
uv run pytest --no-cov tests/unit/test_money.py   # partial run
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
