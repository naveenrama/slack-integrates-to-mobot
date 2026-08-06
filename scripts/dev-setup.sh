#!/usr/bin/env bash
# Usage: source scripts/dev-setup.sh
# This script MUST be sourced (not executed) so the venv stays active in your shell.
# Idempotent — safe to re-source. Skips steps that are already done.

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "ERROR: This script must be sourced, not executed."
    echo "  Run: source scripts/dev-setup.sh"
    return 1 2>/dev/null || exit 1
fi

set -euo pipefail

ENV_DIR="mobot-in-slack-env"
PYTHON_VERSION="3.13"

echo "=== Mobot Slack Agent — Dev Setup ==="
echo ""

# Check uv is installed
if ! command -v uv &> /dev/null; then
    echo "ERROR: uv not found. Install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    return 1
fi

# Create venv if it doesn't exist
if [ ! -d "$ENV_DIR" ]; then
    echo "→ Creating virtual environment ($ENV_DIR)..."
    uv venv --python "$PYTHON_VERSION" "$ENV_DIR"
else
    echo "→ Virtual environment already exists ($ENV_DIR)"
fi

# Activate
echo "→ Activating environment..."
source "$ENV_DIR/bin/activate"

# Install deps if needed (check for mobot-in-slack package)
if ! python -c "import slack_bolt" 2>/dev/null; then
    echo "→ Installing dependencies..."
    uv pip install -e ".[dev]"
else
    echo "→ Dependencies already installed"
fi

# Generate .env if missing
if [ ! -f .env ]; then
    echo "→ Creating .env from template..."
    cp .env.example .env
    FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    sed -i '' "s|your-fernet-key-here|$FERNET_KEY|" .env
    echo "  Generated TOKEN_ENCRYPTION_KEY"
fi

# Load .env into current shell
echo "→ Loading .env..."
set -a
source .env
set +a

echo ""
echo "=== Ready ==="
echo ""
echo "  Python:  $(python --version)"
echo "  Env:     $ENV_DIR (active)"
echo "  .env:    loaded into shell"
echo ""
echo "  Run: python app.py"
echo "  (Bolt + OAuth server both start in one process)"
echo ""
