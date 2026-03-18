#!/usr/bin/env bash
# =============================================================================
# OBaI Teardown Script
#
# Stops all OBaI services:
#   1. MCP data servers (docker compose)
#   2. Opik tracing stack (docker compose)
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

# --- Stop MCP servers ---
echo -e "\n${BOLD}=== Stopping MCP data servers ===${NC}\n"
if docker compose -f "$REPO_ROOT/docker-compose.yml" down 2>/dev/null; then
    info "MCP servers stopped"
else
    fail "Could not stop MCP servers (may not be running)"
fi

# --- Stop Opik ---
echo -e "\n${BOLD}=== Stopping Opik tracing stack ===${NC}\n"
if docker compose -f "$OPIK_DIR/docker-compose.yml" down 2>/dev/null; then
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
