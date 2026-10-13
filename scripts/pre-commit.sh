#!/usr/bin/env bash
set -e

# Detect ruff
if [ -f ".venv/bin/ruff" ]; then
    RUFF=".venv/bin/ruff"
elif command -v ruff >/dev/null 2>&1; then
    RUFF="ruff"
elif command -v uv >/dev/null 2>&1; then
    RUFF="uv run ruff"
else
    echo "Warning: ruff binary not found, skipping pre-commit check."
    exit 0
fi

# Detect staged Python files
STAGED_PY_FILES=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)

if [ -n "$STAGED_PY_FILES" ]; then
    echo "🔍 Running ruff check --fix (including import sorting)..."
    $RUFF check --fix $STAGED_PY_FILES

    echo "✨ Running ruff format..."
    $RUFF format $STAGED_PY_FILES

    # Re-stage automatically fixed / formatted files
    echo "$STAGED_PY_FILES" | xargs git add
    echo "✅ Pre-commit lint and format passed."
fi

exit 0
