# Version Management

The fundamentals-server uses automatic version bumping via pre-commit hooks.

## How It Works

1. **VERSION file**: Single source of truth for version number
   - Located at: `src/fundamentals-server/VERSION`
   - Format: Semantic versioning (e.g., `0.1.0`)

2. **Auto-bump on commit**: Pre-commit hook automatically:
   - Detects changes in `src/fundamentals-server/` directory
   - Bumps patch version (e.g., `0.1.0` → `0.1.1`)
   - Updates `VERSION` file
   - Updates `pyproject.toml` version
   - Stages updated files for commit

3. **Version is read at runtime**:
   - `src/__init__.py`: Reads from VERSION file
   - `src/config.py`: Settings.server_version reads from VERSION file

## Files Automatically Updated

When you commit changes to `src/fundamentals-server/`:

1. `src/fundamentals-server/VERSION` - Bumped to next patch version
2. `src/fundamentals-server/pyproject.toml` - Version field updated
3. Root `pyproject.toml` - Version updated to match highest service version

## Manual Version Bump

If you need to manually bump the version:

```bash
# Edit VERSION file
echo "0.2.0" > VERSION

# The changes will be picked up on next commit
```

## Version Scheme

We use semantic versioning:
- **Major** (1.0.0): Breaking changes
- **Minor** (0.1.0): New features, backwards compatible
- **Patch** (0.0.1): Bug fixes, auto-bumped on every commit

## Pre-commit Hook

The version bump is handled by `.pre-commit-config.yaml`:

```yaml
- repo: local
  hooks:
    - id: version-bump
      name: Auto version bump
      entry: python3 scripts/version-bump.py
      language: system
      stages: [pre-commit]
      pass_filenames: false
      always_run: true
```

Script location: `scripts/version-bump.py`
