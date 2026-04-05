#!/usr/bin/env bash
# =============================================================================
# OBaI Teardown Script
#
# Stops all OBaI services:
#   1. Web UI server (background uvicorn process)
#   2. MCP data servers (docker compose)
#   3. Opik tracing stack (docker compose)
#
# Usage:
#   ./teardown.sh
# =============================================================================

set -euo pipefail

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
BOLD='\033[1m'
NC='\033[0m'

# --- Globals ---
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
OPIK_DIR="$REPO_ROOT/infra/opik"

info()  { echo -e "${GREEN}[OK]${NC}    $1"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $1"; }

# --- Stop Web UI ---
echo -e "\n${BOLD}=== Stopping Web UI ===${NC}\n"
WEB_PIDS=$(pgrep -f "obai web|uvicorn.*clients\.web" 2>/dev/null || true)
if [ -n "$WEB_PIDS" ]; then
    kill $WEB_PIDS 2>/dev/null || true
    sleep 1
    for pid in $WEB_PIDS; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
    done
    info "Web UI stopped (pid $WEB_PIDS)"
else
    info "Web UI not running"
fi

# --- Stop MCP servers ---
echo -e "\n${BOLD}=== Stopping MCP data servers ===${NC}\n"
if docker compose -p obai -f "$REPO_ROOT/docker-compose.yml" down 2>/dev/null; then
    info "MCP servers stopped"
else
    fail "Could not stop MCP servers (may not be running)"
fi

# --- Stop Opik ---
echo -e "\n${BOLD}=== Stopping Opik tracing stack ===${NC}\n"
if (cd "$OPIK_DIR" && docker compose -p obai-opik down) 2>/dev/null; then
    info "Opik services stopped"
else
    fail "Could not stop Opik services (may not be running)"
fi

# --- Summary ---
echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD}  OBaI Teardown Complete${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""
echo "  All services have been stopped."
echo "  Data volumes are preserved — run 'docker volume prune' to reclaim space."
echo ""
