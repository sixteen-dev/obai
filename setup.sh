#!/usr/bin/env bash
# =============================================================================
# OBaI Setup Script
#
# Sets up the complete OBaI multi-agent financial research system:
#   1. Checks prerequisites (Docker-compatible runtime, Python 3.12+, uv, git)
#   2. Validates required API keys (from shell environment)
#   3. Creates ~/.obai directory with preferences
#   4. Spins up Opik tracing stack (docker compose)
#   5. Builds and starts MCP data servers (docker compose)
#   6. Installs OBaI CLI globally (uv tool install)
#   7. Launches Web UI (FastAPI on port 8090)
#   8. Configures Opik SDK for local tracing
#
# Usage:
#   ./setup.sh                # Full setup
#   ./setup.sh --local        # Build MCP images from local source (skip GHCR pull)
#   ./setup.sh --skip-opik    # Skip Opik tracing stack
#   ./setup.sh --skip-mcp     # Skip MCP servers (start later)
#   ./setup.sh --prompt-keys  # Interactively prompt for missing API keys
#
# Prerequisites:
#   - Docker + Docker Compose v2 (or Rancher Desktop exposing `docker` + `docker compose`)
#   - Python 3.12+
#   - uv (https://docs.astral.sh/uv/)
#   - API keys exported in shell: OPENAI_API_KEY, FMP_API_KEY (minimum)
# =============================================================================

set -euo pipefail

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# --- Globals ---
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
OBAI_HOME="${OBAI_HOME:-$HOME/.obai}"
OBAI_DIR="$OBAI_HOME"
OPIK_DIR="$REPO_ROOT/infra/opik"
OBAI_SRC="$REPO_ROOT/src/obai"

SKIP_OPIK=false
SKIP_MCP=false
PROMPT_KEYS=false
LOCAL_BUILD=false
export OBAI_VERSION="$(cat "$REPO_ROOT/VERSION" 2>/dev/null || echo "unknown")"

# --- Parse args ---
for arg in "$@"; do
    case "$arg" in
        --skip-opik)   SKIP_OPIK=true ;;
        --skip-mcp)    SKIP_MCP=true ;;
        --local)       LOCAL_BUILD=true ;;
        --prompt-keys) PROMPT_KEYS=true ;;
        --help|-h)
            head -22 "$0" | tail -16
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $arg${NC}"
            exit 1
            ;;
    esac
done

load_env_file() {
    local env_file="$1"
    local line

    [ -f "$env_file" ] || return 0

    while IFS= read -r line || [ -n "$line" ]; do
        # Tolerate UTF-8 BOMs and CRLF files from browsers/editors.
        line="${line#$'\xEF\xBB\xBF'}"
        line="${line%$'\r'}"
        line="${line%%#*}"
        line="${line// /}"

        if [[ "$line" =~ ^[A-Za-z_][A-Za-z0-9_]*=.+$ ]]; then
            export "$line"
        fi
    done < "$env_file"
}

# --- Load saved API keys from ~/.obai/.env (does not override shell exports) ---
OBAI_ENV_FILE="$OBAI_DIR/.env"
load_env_file "$OBAI_ENV_FILE"

# --- Helpers ---
info()  { echo -e "${BLUE}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $1"; }
step()  { echo -e "\n${BOLD}=== $1 ===${NC}\n"; }

check_cmd() {
    if command -v "$1" &>/dev/null; then
        ok "$1 found: $(command -v "$1")"
        return 0
    else
        fail "$1 not found"
        return 1
    fi
}

prompt_key() {
    local name="$1"
    local desc="$2"
    echo ""
    echo -e "  ${BOLD}$name${NC} — $desc"
    printf "  Enter value (or press Enter to skip): "
    read -r value
    if [ -n "$value" ]; then
        export "$name=$value"
        # Append or update in env file
        mkdir -p "$OBAI_DIR"
        if grep -q "^${name}=" "$OBAI_ENV_FILE" 2>/dev/null; then
            local tmp="${OBAI_ENV_FILE}.tmp"
            while IFS= read -r line; do
                if [[ "$line" == "${name}="* ]]; then
                    echo "${name}=${value}"
                else
                    echo "$line"
                fi
            done < "$OBAI_ENV_FILE" > "$tmp"
            mv "$tmp" "$OBAI_ENV_FILE"
        else
            echo "${name}=${value}" >> "$OBAI_ENV_FILE"
        fi
        chmod 600 "$OBAI_ENV_FILE"
        ok "$name saved"
    fi
}

# =============================================================================
# Step 1: Prerequisites
# =============================================================================
step "1/8 Checking prerequisites"

errors=0

# Docker
if check_cmd docker; then
    if ! docker info &>/dev/null; then
        fail "Docker daemon is not running"
        echo "  Start Docker or Rancher Desktop, then re-run this script."
        errors=$((errors + 1))
    fi
else
    echo "  Rancher Desktop is the recommended local container runtime for OBaI."
    echo "  Download: https://rancherdesktop.io/"
    echo "  Install guide: https://docs.rancherdesktop.io/getting-started/installation/"
    echo "  After install, start Rancher Desktop and verify both 'docker' and"
    echo "  'docker compose' work in your shell, then re-run this script."
    errors=$((errors + 1))
fi

# Docker Compose v2
if docker compose version &>/dev/null; then
    ok "docker compose v2: $(docker compose version --short)"
else
    fail "docker compose v2 not found (need 'docker compose', not 'docker-compose')"
    echo "  If you are using Rancher Desktop, make sure the Docker/Moby runtime is enabled."
    echo "  Rancher Desktop docs: https://docs.rancherdesktop.io/getting-started/installation/"
    errors=$((errors + 1))
fi

# Python 3.12+ (check system python3, then uv-managed)
_py_found=false
if check_cmd python3; then
    py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    py_major=$(echo "$py_version" | cut -d. -f1)
    py_minor=$(echo "$py_version" | cut -d. -f2)
    if [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 12 ]; then
        ok "Python $py_version (>= 3.12)"
        _py_found=true
    fi
fi
if [ "$_py_found" = false ] && check_cmd uv; then
    # System python3 is too old or missing — check if uv manages 3.12+
    uv_py=$(uv python list --only-installed 2>/dev/null | grep -oP 'cpython-3\.\K(1[2-9]|[2-9][0-9])' | head -1 || true)
    if [ -n "$uv_py" ]; then
        ok "Python 3.$uv_py (uv-managed, >= 3.12)"
        _py_found=true
    fi
fi
if [ "$_py_found" = false ]; then
    fail "Python 3.12+ required. Install via: uv python install 3.12"
    errors=$((errors + 1))
fi

# uv
if check_cmd uv; then
    :
else
    echo "  Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    errors=$((errors + 1))
fi

# git
check_cmd git || errors=$((errors + 1))

if [ "$errors" -gt 0 ]; then
    echo ""
    fail "$errors prerequisite(s) missing. Fix them and re-run."
    exit 1
fi

# =============================================================================
# Step 2: API Keys (from shell environment)
# =============================================================================
step "2/8 Checking API keys"

missing_required=0
missing_optional=0

check_key() {
    local name="$1"
    local required="$2"
    local desc="$3"
    local val="${!name:-}"

    if [ -n "$val" ]; then
        ok "$name is set"
    elif [ "$required" = "required" ]; then
        fail "$name is NOT set — $desc"
        missing_required=$((missing_required + 1))
    else
        warn "$name is not set — $desc (optional)"
        missing_optional=$((missing_optional + 1))
    fi
}

check_key "OPENAI_API_KEY"    "required" "needed for Agent SDK (all agents)"
check_key "FMP_API_KEY"       "required" "needed for 6 of 8 MCP servers (fundamentals, market data, news, screening, portfolio, backtest)"
check_key "MASSIVE_API_KEY"   "optional" "needed for options-server only"
check_key "TAVILY_API_KEY"    "optional" "needed for events-news-server AI search"
check_key "EXA_API_KEY"       "optional" "needed for research-server (Exa semantic search)"
check_key "ANTHROPIC_API_KEY" "optional" "needed for LLM-judge evaluation scorers"

if [ "$missing_required" -gt 0 ]; then
    if [ "$PROMPT_KEYS" = true ]; then
        info "Prompting for missing required keys..."
        [ -z "${OPENAI_API_KEY:-}" ] && prompt_key "OPENAI_API_KEY" "Powers all agents (OpenAI Agent SDK)"
        [ -z "${FMP_API_KEY:-}" ]    && prompt_key "FMP_API_KEY" "Financial Modeling Prep (6 of 8 MCP servers)"
        # Re-check after prompting
        if [ -z "${OPENAI_API_KEY:-}" ] || [ -z "${FMP_API_KEY:-}" ]; then
            fail "Required API keys still missing. Cannot continue."
            exit 1
        fi
    else
        echo ""
        fail "$missing_required required API key(s) missing."
        echo ""
        info "Export them in your shell and re-run:"
        echo "  export OPENAI_API_KEY=sk-proj-..."
        echo "  export FMP_API_KEY=..."
        echo ""
        info "Or add to your shell profile (~/.bashrc, ~/.zshrc) for persistence."
        exit 1
    fi
fi

if [ "$missing_optional" -gt 0 ]; then
    if [ "$PROMPT_KEYS" = true ]; then
        info "Optional keys enhance functionality. Press Enter to skip any."
        [ -z "${MASSIVE_API_KEY:-}" ]   && prompt_key "MASSIVE_API_KEY" "Options chain data (free tier)"
        [ -z "${TAVILY_API_KEY:-}" ]    && prompt_key "TAVILY_API_KEY" "AI news search (free tier)"
        [ -z "${EXA_API_KEY:-}" ]       && prompt_key "EXA_API_KEY" "Semantic search for research"
        [ -z "${ANTHROPIC_API_KEY:-}" ] && prompt_key "ANTHROPIC_API_KEY" "LLM-judge evaluation scoring"
    else
        warn "$missing_optional optional key(s) missing. Some features will be degraded."
    fi
fi

# =============================================================================
# Step 3: Create ~/.obai directory
# =============================================================================
step "3/8 Setting up ~/.obai"

mkdir -p "$OBAI_DIR/logs"
ok "$OBAI_DIR directory ready"

# Create default preferences if missing
PREFS_FILE="$OBAI_DIR/preferences.json"
if [ ! -f "$PREFS_FILE" ]; then
    cat > "$PREFS_FILE" <<'PREFSEOF'
{
  "risk_tolerance": "moderate",
  "investment_horizon": "medium",
  "default_benchmark": "SPY",
  "initial_capital": 100000,
  "currency": "USD",
  "market": "US"
}
PREFSEOF
    ok "Created default preferences at $PREFS_FILE"
else
    ok "Preferences file exists"
fi

# =============================================================================
# Step 4: Opik tracing stack
# =============================================================================
if [ "$SKIP_OPIK" = false ]; then
    step "4/8 Starting Opik tracing stack"

    # Create external volumes
    info "Creating Opik docker volumes..."
    bash "$OPIK_DIR/setup-volumes.sh"

    # Start Opik
    info "Starting Opik services (mysql, redis, clickhouse, minio, backend, frontend)..."
    (cd "$OPIK_DIR" && docker compose -p obai-opik up -d)

    # Wait for frontend
    info "Waiting for Opik UI..."
    retries=0
    until curl -sf http://localhost:5173 >/dev/null 2>&1 || [ "$retries" -ge 30 ]; do
        retries=$((retries + 1))
        sleep 2
    done

    if curl -sf http://localhost:5173 >/dev/null 2>&1; then
        ok "Opik UI available at http://localhost:5173"
    else
        warn "Opik UI not responding yet — it may still be starting up"
    fi
else
    step "4/8 Skipping Opik (--skip-opik)"
fi

# =============================================================================
# Step 5: MCP data servers
# =============================================================================
if [ "$SKIP_MCP" = false ]; then
    step "5/8 Building and starting MCP servers"

    if [ "$LOCAL_BUILD" = true ]; then
        info "Building MCP server images from local source (--local)..."
        docker compose -p obai -f "$REPO_ROOT/docker-compose.yml" build
    else
        info "Pulling pre-built images from GHCR..."
        if docker compose -p obai -f "$REPO_ROOT/docker-compose.yml" pull 2>/dev/null; then
            ok "Pre-built images pulled successfully"
        else
            warn "Could not pull pre-built images — building locally"
            info "Building 9 MCP server images (this may take a few minutes on first run)..."
            docker compose -p obai -f "$REPO_ROOT/docker-compose.yml" build
        fi
    fi

    info "Starting MCP servers..."
    # --remove-orphans drops containers (e.g. obai-qdrant) for services that
    # were removed from docker-compose.yml since the last run.
    docker compose -p obai -f "$REPO_ROOT/docker-compose.yml" up -d --remove-orphans

    # Health check
    info "Waiting for servers to become healthy..."
    sleep 10

    # Qdrant disabled — re-enable here if QDRANT_ENABLED=true on fundamentals-server.
    # if curl -sf "http://localhost:6333/healthz" >/dev/null 2>&1; then
    #     ok "qdrant (port 6333)"
    # else
    #     warn "qdrant (port 6333) — not healthy yet, may still be starting"
    # fi

    servers=(
        "fundamentals:8001"
        "market-data:8002"
        "events-news:8003"
        "options:8004"
        "screening:8005"
        "portfolio:8006"
        "backtest:8007"
        "research:8008"
        "prediction-markets:8009"
    )

    # `/health` is liveness only — the server is up but may have no working
    # provider keys. `/health/ready` additionally verifies upstream
    # connectivity / required keys, which is what setup should gate on.
    for entry in "${servers[@]}"; do
        name="${entry%%:*}"
        port="${entry##*:}"
        if curl -sf "http://localhost:$port/health/ready" >/dev/null 2>&1; then
            ok "$name-server (port $port)"
        elif curl -sf "http://localhost:$port/health" >/dev/null 2>&1; then
            warn "$name-server (port $port) — alive but not ready (check API keys / upstream)"
        else
            warn "$name-server (port $port) — not healthy yet, may still be starting"
        fi
    done

    # Qdrant seeding check disabled — re-enable alongside the qdrant service.
    # QDRANT_COUNT=$(curl -sf "http://localhost:6333/collections/financial-fundamentals" 2>/dev/null \
    #     | python3 -c "import sys,json; print(json.load(sys.stdin).get('result',{}).get('points_count',0))" 2>/dev/null || echo "0")
    #
    # if [ "$QDRANT_COUNT" = "0" ]; then
    #     echo ""
    #     warn "Qdrant collection is empty — educational search won't work until seeded."
    #     info "Seed from local PDFs:"
    #     echo "    uv run python scripts/vector_bucket/seed_qdrant.py --pdf-dir ./data/pdfs"
    # else
    #     ok "Qdrant collection has $QDRANT_COUNT vectors"
    # fi
else
    step "5/8 Skipping MCP servers (--skip-mcp)"
fi

# =============================================================================
# Step 6: Install OBaI CLI
# =============================================================================
step "6/8 Installing OBaI CLI"

info "Installing OBaI as a global tool via uv (editable — source changes take effect immediately)..."

# Editable install so `obai` always runs from source — no reinstall needed after code changes.
if uv tool install --reinstall --editable "$OBAI_SRC" 2>/dev/null; then
    ok "OBaI CLI installed (editable)"
else
    # Fallback: non-editable snapshot (requires reinstall after source changes)
    info "Editable install failed, trying snapshot install..."
    if uv tool install --reinstall --from "$OBAI_SRC" obai 2>/dev/null; then
        ok "OBaI CLI installed (snapshot — re-run setup after code changes)"
    else
        fail "Could not install OBaI CLI with uv."
        echo "  Try manually:"
        echo "    uv tool install --editable \"$OBAI_SRC\""
        echo "  Or run from source:"
        echo "    cd \"$OBAI_SRC\" && uv run obai"
    fi
fi


# Verify obai is on PATH
if command -v obai &>/dev/null; then
    ok "'obai' command available at $(command -v obai)"
else
    # Check common uv tool bin locations
    UV_BIN="$HOME/.local/bin"
    if [ -f "$UV_BIN/obai" ]; then
        warn "'obai' installed at $UV_BIN/obai but not on PATH"
        echo ""
        info "Add this to your shell profile (~/.bashrc or ~/.zshrc):"
        echo "  export PATH=\"$UV_BIN:\$PATH\""
    else
        warn "'obai' not found on PATH after install"
    fi
fi

# =============================================================================
# Step 7: Launch Web UI
# =============================================================================
step "7/8 Launching Web UI"

WEB_PORT=8090
info "Starting OBaI Web UI on port $WEB_PORT..."

if command -v obai &>/dev/null; then
    obai web --port "$WEB_PORT" &
    WEB_PID=$!
elif [ -f "$OBAI_SRC/clients/web/server.py" ]; then
    uv run --directory "$OBAI_SRC" obai web --port "$WEB_PORT" &
    WEB_PID=$!
else
    WEB_PID=""
    warn "Could not start Web UI — obai command not found"
fi

if [ -n "${WEB_PID:-}" ]; then
    retries=0
    until curl -sf "http://127.0.0.1:$WEB_PORT" >/dev/null 2>&1 || [ "$retries" -ge 15 ]; do
        retries=$((retries + 1))
        sleep 1
    done

    if curl -sf "http://127.0.0.1:$WEB_PORT" >/dev/null 2>&1; then
        ok "Web UI running (pid $WEB_PID)"
    else
        warn "Web UI not responding yet — it may still be initializing agents"
    fi
fi

# =============================================================================
# Step 8: Configure Opik SDK
# =============================================================================
step "8/8 Final configuration"

# Set up Opik to point at local instance
if [ "$SKIP_OPIK" = false ]; then
    if command -v opik &>/dev/null; then
        info "Configuring Opik SDK for local tracing..."
        opik configure --use_local 2>/dev/null || true
        ok "Opik SDK configured"
    elif uv run --directory "$OBAI_SRC" opik configure --use_local 2>/dev/null; then
        ok "Opik SDK configured (via uv)"
    else
        warn "Could not configure Opik SDK automatically"
        info "Run manually: opik configure --use_local"
    fi
fi

# =============================================================================
# Summary
# =============================================================================
# --- Key status helper ---
key_status() {
    local name="$1"
    local label="$2"
    local val="${!name:-}"
    if [ -n "$val" ]; then
        echo -e "    ${GREEN}\u2713${NC} $label"
    else
        echo -e "    ${RED}\u2717${NC} $label ${YELLOW}(not set)${NC}"
    fi
}

echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD}  OBaI v${OBAI_VERSION} Setup Complete${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""
echo "  Config:      $OBAI_DIR/"
echo "  Logs:        $OBAI_DIR/logs/obai.log"
echo "  Preferences: $OBAI_DIR/preferences.json"
echo ""

echo -e "  ${BOLD}API Keys:${NC}"
key_status "OPENAI_API_KEY"    "OPENAI_API_KEY     — agents"
key_status "FMP_API_KEY"       "FMP_API_KEY        — market data"
key_status "MASSIVE_API_KEY"   "MASSIVE_API_KEY    — options"
key_status "TAVILY_API_KEY"    "TAVILY_API_KEY     — news search"
key_status "EXA_API_KEY"       "EXA_API_KEY        — research"
key_status "ANTHROPIC_API_KEY" "ANTHROPIC_API_KEY  — evaluation"
echo ""

echo -e "  ${BOLD}Web UI:     http://127.0.0.1:${WEB_PORT}${NC}"
echo ""

if [ "$SKIP_OPIK" = false ]; then
    echo "  Opik UI:     http://localhost:5173"
fi

if [ "$SKIP_MCP" = false ]; then
    echo ""
    # echo "  Qdrant:      http://localhost:6333/dashboard"  # disabled
    echo "  MCP Servers:"
    echo "    fundamentals    http://localhost:8001/mcp"
    echo "    market-data     http://localhost:8002/mcp"
    echo "    events-news     http://localhost:8003/mcp"
    echo "    options         http://localhost:8004/mcp"
    echo "    screening       http://localhost:8005/mcp"
    echo "    portfolio       http://localhost:8006/mcp"
    echo "    backtest        http://localhost:8007/mcp"
    echo "    research        http://localhost:8008/mcp"
    echo "    prediction-mkts http://localhost:8009/mcp"
fi

echo ""
echo "  Quick start:"
echo "    obai status                              # Check server connectivity"
echo "    obai query \"What is AAPL trading at?\"    # Single query"
echo "    obai chat                                # Interactive REPL"
echo "    obai config show                         # View API key status"
echo "    obai config set-key TAVILY_API_KEY       # Add/update a key"
echo ""
echo "  MCP Inspector (optional, for MCP server testing):"
echo "    Docs: https://modelcontextprotocol.io/docs/tools/inspector"
echo "    Run:  npx @modelcontextprotocol/inspector"
echo ""
