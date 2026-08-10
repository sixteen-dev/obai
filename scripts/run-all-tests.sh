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
    "src/crypto-server"
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

# These suites live outside their owning pytest testpaths, so invoke them
# explicitly while preserving each environment's import isolation.
echo
echo "=== canonical OBaI regression harness ==="
if ! uv run pytest -q ".claude/skills/obai-e2e-regression/tests" "$@"; then
    fail_count=$((fail_count + 1))
    echo "FAIL: canonical OBaI regression harness"
fi

# The root `tests/` tree is the root pyproject's own testpaths, but the loop
# above only visits services, so nothing here would ever reach it. It holds
# the guards for setup.sh/install.sh, which no service suite can cover.
echo
echo "=== bootstrap shell scripts ==="
if ! uv run pytest -q "tests" "$@"; then
    fail_count=$((fail_count + 1))
    echo "FAIL: bootstrap shell scripts"
fi

echo
echo "=== OBaI broader evaluation contracts ==="
if ! (cd "src/obai" && uv run pytest -q "evaluation/tests" "$@"); then
    fail_count=$((fail_count + 1))
    echo "FAIL: OBaI broader evaluation contracts"
fi

if [ "$fail_count" -gt 0 ]; then
    echo
    echo "FAIL: $fail_count service(s) had failing tests"
    exit 1
fi
echo
echo "OK: all services passed"
