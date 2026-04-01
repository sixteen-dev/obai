#!/usr/bin/env bash
# =============================================================================
# OBaI Release Script
#
# Bumps the version, updates all version files, commits, and creates a git tag.
# Does NOT push — that's your call.
#
# Usage:
#   ./scripts/release.sh patch   # 0.9.0 → 0.2.1
#   ./scripts/release.sh minor   # 0.9.0 → 0.3.0
#   ./scripts/release.sh major   # 0.9.0 → 1.0.0
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="$REPO_ROOT/VERSION"
ROOT_PYPROJECT="$REPO_ROOT/pyproject.toml"
CLI_PYPROJECT="$REPO_ROOT/src/obai/pyproject.toml"
CHANGELOG="$REPO_ROOT/CHANGELOG.md"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

# --- Read current version ---
if [ ! -f "$VERSION_FILE" ]; then
    echo -e "${RED}ERROR: VERSION file not found at $VERSION_FILE${NC}"
    exit 1
fi

CURRENT="$(tr -d '[:space:]' < "$VERSION_FILE")"
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

# --- Determine bump type ---
BUMP="${1:-patch}"
case "$BUMP" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    patch) PATCH=$((PATCH + 1)) ;;
    *)
        echo "Usage: $0 [major|minor|patch]"
        exit 1
        ;;
esac

NEW_VERSION="${MAJOR}.${MINOR}.${PATCH}"
TAG="v${NEW_VERSION}"

echo -e "${BOLD}Releasing OBaI${NC}"
echo -e "  Current: ${YELLOW}${CURRENT}${NC}"
echo -e "  New:     ${GREEN}${NEW_VERSION}${NC} (${BUMP})"
echo ""

# --- Check for clean working tree (except CHANGELOG) ---
if [ -n "$(git -C "$REPO_ROOT" diff --name-only -- ':!CHANGELOG.md')" ] || \
   [ -n "$(git -C "$REPO_ROOT" diff --cached --name-only)" ]; then
    echo -e "${RED}ERROR: Working tree has uncommitted changes (besides CHANGELOG.md).${NC}"
    echo "Commit or stash them first."
    exit 1
fi

# --- Update VERSION file ---
echo "$NEW_VERSION" > "$VERSION_FILE"

# --- Update pyproject.toml files ---
sed -i "s/^version = \"${CURRENT}\"/version = \"${NEW_VERSION}\"/" "$ROOT_PYPROJECT"
sed -i "s/^version = \"${CURRENT}\"/version = \"${NEW_VERSION}\"/" "$CLI_PYPROJECT"

# --- Update CHANGELOG.md ---
TODAY="$(date +%Y-%m-%d)"

# Insert new version header after [Unreleased]
sed -i "/^## \[Unreleased\]$/a\\\\n## [${NEW_VERSION}] - ${TODAY}" "$CHANGELOG"

# Update comparison links at bottom
sed -i "s|\(^\[Unreleased\]:.*compare/\)v.*\.\.\.HEAD|\1v${NEW_VERSION}...HEAD|" "$CHANGELOG"

# Add new version link before the last version link
PREV_TAG="v${CURRENT}"
sed -i "/^\[${CURRENT}\]/i [${NEW_VERSION}]: https://github.com/sixteen-dev/obai/releases/tag/${TAG}" "$CHANGELOG"

echo -e "${YELLOW}Opening CHANGELOG.md for review...${NC}"
echo "Add your release notes under [${NEW_VERSION}], then save and close."
echo ""

if [ -n "${EDITOR:-}" ]; then
    "$EDITOR" "$CHANGELOG"
elif command -v nano &>/dev/null; then
    nano "$CHANGELOG"
elif command -v vi &>/dev/null; then
    vi "$CHANGELOG"
else
    echo -e "${YELLOW}No editor found. Edit CHANGELOG.md manually, then press Enter to continue.${NC}"
    read -r
fi

# --- Commit and tag ---
git -C "$REPO_ROOT" add "$VERSION_FILE" "$ROOT_PYPROJECT" "$CLI_PYPROJECT" "$CHANGELOG"
git -C "$REPO_ROOT" commit -m "release: ${TAG}"
git -C "$REPO_ROOT" tag -a "$TAG" -m "Release ${TAG}"

echo ""
echo -e "${GREEN}${BOLD}Done!${NC} Created commit and tag ${BOLD}${TAG}${NC}"
echo ""
echo "Next steps:"
echo -e "  ${BOLD}git push origin main --tags${NC}    # push commit + tag"
echo ""
echo "GitHub Actions will create the release automatically."
