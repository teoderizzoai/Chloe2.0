#!/usr/bin/env bash
# Runs on the server on every push to main.
set -euo pipefail

APP_DIR=/opt/chloe

cd "$APP_DIR"

echo "[deploy] Pulling latest code..."
git pull origin main

echo "[deploy] Installing Python dependencies..."
.venv/bin/pip install --quiet -e .

echo "[deploy] Building frontend..."
cd frontend
npm ci --silent
npm run build
cd ..

echo "[deploy] Restarting service..."
systemctl restart chloe

echo "[deploy] Done."
