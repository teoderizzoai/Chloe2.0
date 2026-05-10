#!/usr/bin/env bash
# chloe.sh — entry point that ensures a venv exists before running the CLI
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${SCRIPT_DIR}/.venv"

# Load .env if present
if [ -f "${SCRIPT_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  source "${SCRIPT_DIR}/.env"
  set +a
fi

if [ ! -d "$VENV" ]; then
    echo "[chloe] Creating virtual environment at ${VENV}..."
    python3 -m venv "$VENV"
    echo "[chloe] Installing package..."
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -e "$SCRIPT_DIR"
    echo "[chloe] Ready."
fi

exec "$VENV/bin/chloe" "$@"
