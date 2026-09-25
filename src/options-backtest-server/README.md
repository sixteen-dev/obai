# options-backtest-server

Deterministic options strategy backtesting engine for OBaI, specified by
`docs/design/options-backtesting-system-v3.md`. It computes results under disclosed
assumptions; it does not reconstruct a broker's fills or promise profitability.

## Status: WP1 in progress

WP1 builds the pure domain core, with no I/O, server or data provider:

- `errors.py`: typed errors and the §15.2 issue shape.
- `money.py`: exact `Usd` and `Price` values. Any arithmetic that would round raises `decimal.Inexact`.
- `models/market.py`: contract terms, deliverables and quotes. Payoff uses the deliverable
  and the aggregate exercise amount, never the premium multiplier.
- `strict_json.py`, `ingest.py`, `models/strategy.py`, `models/strategy_checks.py`: strict
  strategy ingestion. `load_strategy(raw)` returns a `ValidatedStrategy` or raises
  `SpecRejected` carrying its issues in the §15.2 error shape.
- `models/ledger.py`: ledger records (accounts, postings, lots, quantity events, fee lines,
  entries, state) and the posting helpers the engine's booking functions share.
- `engine/*`: the exact double-entry ledger. `journal` holds the one transition `apply_entry`,
  the `Journal` and `replay`; `positions` does FIFO lots; `trades`, `settlement`, `exercise`
  and `adjustments` book entries; `fees` is the assumed flat schedule; `valuation` gives NLV,
  net P&L and reconciliation; `funding` gives expiry bounds, encumbrances and funding headroom.

The architecture, scope and exclusions are recorded in
[ADR 0001](docs/adr/0001-wp1-architecture.md). The design's schema, example strategy and
ledger fixtures are vendored byte-exact under `tests/contracts/`, with `SHA256SUMS`.

## Commands

Run from this directory, never from the repository root:

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict --exclude '(^|/)tests/' src
uv run pytest            # enforces branch coverage >= 90%
uv run pytest --no-cov tests/unit/test_money.py   # partial run
```

These in-service commands are the portable gate. At the repository root,
`scripts/run-all-tests.sh` also runs this service's tests. `scripts/run-all-typechecks.sh` is
local and gitignored (`scripts/*`), so its wiring for this service is not shared.
