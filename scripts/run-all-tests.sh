#!/usr/bin/env bash
# Run pytest for every service and the autotrader skill, from each
# service's own directory. The monorepo's root pytest cannot resolve
# service-internal `from src.config import ...` style imports because
# each service ships its own pyproject with `pythonpath = ["."]`, so
# the runner must be invoked from inside each service tree.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

services=(
    "src/backtest-server"
    "src/events-news-server"
    "src/fundamentals-server"
    "src/market-data-server"
    "src/options-server"
    "src/portfolio-server"
    "src/prediction-markets-server"
    "src/research-server"
    "src/screening-server"
    "src/obai"
    "skills/autotrader"
)

fail_count=0
for svc in "${services[@]}"; do
    if [ ! -d "$svc" ] || [ ! -f "$svc/pyproject.toml" ]; then
        echo "skip: $svc (no pyproject.toml)"
        continue
    fi
    echo
    echo "=== $svc ==="
    if ! (cd "$svc" && uv run pytest -q "$@"); then
        fail_count=$((fail_count + 1))
        echo "FAIL: $svc"
    fi
done

if [ "$fail_count" -gt 0 ]; then
    echo
    echo "FAIL: $fail_count service(s) had failing tests"
    exit 1
fi
echo
echo "OK: all services passed"
