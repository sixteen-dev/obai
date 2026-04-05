#!/usr/bin/env bash
# =============================================================================
# OBaI One-Liner Installer
#
# Installs OBaI multi-agent financial research system with a single command:
#   curl -fsSL https://raw.githubusercontent.com/sixteen-dev/obai/main/install.sh | bash
#
# What this script does:
#   1. Checks prerequisites (Docker, Python 3.12+, uv, git)
#   2. Clones OBaI to ~/.obai/src (or updates if already installed)
#   3. Prompts for API keys and saves to ~/.obai/.env
#   4. Runs the full setup (pulls pre-built images, installs CLI, starts services)
#
# Environment:
#   OBAI_HOME   — base directory (default: ~/.obai)
#   OBAI_BRANCH — git branch to install (default: main)
# =============================================================================

set -euo pipefail

# --- Colors ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $1"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $1"; }

timestamp() {
    date +"%Y%m%d-%H%M%S"
}

clone_obai_repo() {
    info "Cloning OBaI..."
    git clone --branch "$OBAI_BRANCH" "$OBAI_REPO" "$OBAI_SRC"
    ok "Cloned to $OBAI_SRC"
}

backup_obai_src() {
    local reason="$1"
    local backup_path="${OBAI_SRC}.backup.$(timestamp)"
    warn "$reason"
    mv "$OBAI_SRC" "$backup_path"
    ok "Backed up existing source to $backup_path"
}

# --- Constants ---
OBAI_HOME="${OBAI_HOME:-$HOME/.obai}"
OBAI_SRC="$OBAI_HOME/src"
OBAI_REPO="${OBAI_REPO:-https://github.com/sixteen-dev/obai.git}"
OBAI_BRANCH="${OBAI_BRANCH:-main}"
OBAI_ENV_FILE="$OBAI_HOME/.env"

# --- Banner ---
echo ""
echo -e "${BOLD}============================================${NC}"
echo -e "${BOLD}  OBaI Installer${NC}"
echo -e "${BOLD}  Multi-agent financial research assistant${NC}"
echo -e "${BOLD}============================================${NC}"
echo ""

# =============================================================================
# Step 1: Detect platform
# =============================================================================
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Linux)  info "Platform: Linux ($ARCH)" ;;
    Darwin) info "Platform: macOS ($ARCH)" ;;
    *)
        fail "Unsupported OS: $OS"
        echo "  OBaI supports Linux and macOS."
        exit 1
        ;;
esac

# =============================================================================
# Step 2: Check prerequisites
# =============================================================================
info "Checking prerequisites..."
echo ""
errors=0

# Docker
if command -v docker &>/dev/null; then
    if docker info &>/dev/null; then
        ok "Docker is running"
    else
        fail "Docker is installed but not running"
        echo "  Start Docker Desktop or Rancher Desktop, then re-run."
        errors=$((errors + 1))
    fi
else
    fail "Docker not found"
    echo "  Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
    echo "  Or Rancher Desktop:     https://rancherdesktop.io/"
    errors=$((errors + 1))
fi

# Docker Compose v2
if docker compose version &>/dev/null; then
    ok "Docker Compose v2: $(docker compose version --short)"
else
    fail "Docker Compose v2 not found"
    errors=$((errors + 1))
fi

# Python 3.12+
py_ok=false
if command -v python3 &>/dev/null; then
    py_version=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
    py_major=$(echo "$py_version" | cut -d. -f1)
    py_minor=$(echo "$py_version" | cut -d. -f2)
    if [ "$py_major" -ge 3 ] && [ "$py_minor" -ge 12 ]; then
        ok "Python $py_version"
        py_ok=true
    fi
fi
if [ "$py_ok" = false ] && command -v uv &>/dev/null; then
    uv_py=$(uv python list --only-installed 2>/dev/null | grep -oP 'cpython-3\.\K(1[2-9]|[2-9][0-9])' | head -1 || true)
    if [ -n "${uv_py:-}" ]; then
        ok "Python 3.$uv_py (uv-managed)"
        py_ok=true
    fi
fi
if [ "$py_ok" = false ]; then
    fail "Python 3.12+ not found"
    echo "  Install via: curl -LsSf https://astral.sh/uv/install.sh | sh && uv python install 3.12"
    errors=$((errors + 1))
fi

# uv
if command -v uv &>/dev/null; then
    ok "uv: $(uv --version 2>/dev/null || echo 'installed')"
else
    warn "uv not found — installing..."
    if curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null; then
        # Add to PATH for this session
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
        if command -v uv &>/dev/null; then
            ok "uv installed: $(uv --version 2>/dev/null)"
        else
            fail "uv installed but not on PATH"
            echo "  Add ~/.local/bin to your PATH, then re-run."
            errors=$((errors + 1))
        fi
    else
        fail "Could not install uv"
        echo "  Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        errors=$((errors + 1))
    fi
fi

# git
if command -v git &>/dev/null; then
    ok "git: $(git --version)"
else
    fail "git not found"
    errors=$((errors + 1))
fi

if [ "$errors" -gt 0 ]; then
    echo ""
    fail "$errors prerequisite(s) missing. Fix them and re-run."
    exit 1
fi

# =============================================================================
# Step 3: Clone or update repository
# =============================================================================
echo ""
info "Setting up OBaI source..."

mkdir -p "$OBAI_HOME"

if [ -d "$OBAI_SRC/.git" ]; then
    info "Existing installation found — updating..."
    if git -C "$OBAI_SRC" diff --quiet 2>/dev/null && git -C "$OBAI_SRC" diff --cached --quiet 2>/dev/null; then
        git -C "$OBAI_SRC" fetch origin

        if ! git -C "$OBAI_SRC" show-ref --verify --quiet "refs/remotes/origin/$OBAI_BRANCH"; then
            fail "Remote branch origin/$OBAI_BRANCH not found"
            exit 1
        fi

        current_head=$(git -C "$OBAI_SRC" rev-parse HEAD)
        remote_head=$(git -C "$OBAI_SRC" rev-parse "origin/$OBAI_BRANCH")

        if [ "$current_head" = "$remote_head" ]; then
            git -C "$OBAI_SRC" checkout -B "$OBAI_BRANCH" "origin/$OBAI_BRANCH" >/dev/null 2>&1 || true
            ok "Already up to date"
        elif git -C "$OBAI_SRC" merge-base --is-ancestor "$current_head" "$remote_head"; then
            git -C "$OBAI_SRC" checkout -B "$OBAI_BRANCH" "origin/$OBAI_BRANCH"
            ok "Updated to latest"
        elif git -C "$OBAI_SRC" merge-base --is-ancestor "$remote_head" "$current_head"; then
            warn "Local checkout is ahead of origin/$OBAI_BRANCH — leaving existing checkout untouched"
            info "If you want a clean reinstall, move or remove $OBAI_SRC and re-run."
        else
            backup_obai_src "Existing installation diverged from origin/$OBAI_BRANCH — likely due to a force-push"
            clone_obai_repo
        fi
    else
        warn "Local changes detected in $OBAI_SRC — skipping update"
        info "Run 'git -C $OBAI_SRC stash' to update, then re-run."
    fi
elif [ -d "$OBAI_SRC" ]; then
    backup_obai_src "$OBAI_SRC exists but is not a git repo"
    clone_obai_repo
else
    clone_obai_repo
fi

# =============================================================================
# Step 4: API key setup
# =============================================================================
echo ""
info "Configuring API keys..."
echo ""

# Load existing env file
if [ -f "$OBAI_ENV_FILE" ]; then
    set -a
    source "$OBAI_ENV_FILE"
    set +a
fi

save_key() {
    local name="$1"
    local value="$2"
    if grep -q "^${name}=" "$OBAI_ENV_FILE" 2>/dev/null; then
        sed -i "s|^${name}=.*|${name}=${value}|" "$OBAI_ENV_FILE"
    else
        echo "${name}=${value}" >> "$OBAI_ENV_FILE"
    fi
}

prompt_api_key() {
    local name="$1"
    local desc="$2"
    local required="$3"
    local current="${!name:-}"

    if [ -n "$current" ]; then
        local masked="${current:0:8}..."
        echo -e "  ${GREEN}\u2713${NC} $name ($masked)"
        return
    fi

    local label="optional"
    [ "$required" = "required" ] && label="REQUIRED"

    printf "  %s (%s — %s): " "$name" "$label" "$desc"
    read -r value

    if [ -n "$value" ]; then
        export "$name=$value"
        save_key "$name" "$value"
        echo -e "    ${GREEN}\u2713 saved${NC}"
    elif [ "$required" = "required" ]; then
        echo -e "    ${YELLOW}skipped${NC}"
    fi
}

# Ensure env file exists
touch "$OBAI_ENV_FILE"
chmod 600 "$OBAI_ENV_FILE"

echo -e "${BOLD}  Required:${NC}"
prompt_api_key "OPENAI_API_KEY" "Powers all agents" "required"
prompt_api_key "FMP_API_KEY" "Financial Modeling Prep, 6 of 8 servers" "required"
echo ""
echo -e "${BOLD}  Optional (press Enter to skip):${NC}"
prompt_api_key "MASSIVE_API_KEY" "Options chain data, free tier" "optional"
prompt_api_key "TAVILY_API_KEY" "AI news search, free tier" "optional"
prompt_api_key "EXA_API_KEY" "Semantic search for research" "optional"
prompt_api_key "ANTHROPIC_API_KEY" "LLM-judge evaluation" "optional"
echo ""

# Verify required keys
if [ -z "${OPENAI_API_KEY:-}" ] || [ -z "${FMP_API_KEY:-}" ]; then
    fail "Required API keys (OPENAI_API_KEY, FMP_API_KEY) are not set."
    echo ""
    info "Set them and re-run:"
    echo "  export OPENAI_API_KEY=sk-proj-..."
    echo "  export FMP_API_KEY=..."
    echo "  curl -fsSL https://raw.githubusercontent.com/sixteen-dev/obai/main/install.sh | bash"
    exit 1
fi

# =============================================================================
# Step 5: Run setup
# =============================================================================
echo ""
info "Running OBaI setup..."
echo ""

cd "$OBAI_SRC"

# Source env file so setup.sh sees the keys
set -a
source "$OBAI_ENV_FILE"
set +a

./setup.sh
