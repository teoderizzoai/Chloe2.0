#!/usr/bin/env bash
# One-time server bootstrap. Run as root on a fresh Hetzner Ubuntu server.
# Usage: bash setup.sh [git-repo-url]
set -euo pipefail

REPO_URL="${1:-https://github.com/teoderizzoai/Chloe2.0}"
APP_DIR=/opt/chloe

echo "[setup] Installing system packages..."
apt-get update -q
apt-get install -y -q software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -q
apt-get install -y -q python3.12 python3.12-venv python3-pip nginx nodejs npm git

echo "[setup] Cloning repository..."
git clone "$REPO_URL" "$APP_DIR"

echo "[setup] Creating Python virtual environment..."
cd "$APP_DIR"
python3.12 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .

echo "[setup] Building frontend..."
cd frontend
npm ci --silent
npm run build
cd ..

echo "[setup] Writing .env file..."
cat > "$APP_DIR/.env" <<'EOF'
GEMINI_API_KEY=your_key_here
EOF
echo "  --> Edit $APP_DIR/.env and set GEMINI_API_KEY before starting the service."

echo "[setup] Installing systemd service..."
cp ops/chloe.service /etc/systemd/system/chloe.service
systemctl daemon-reload
systemctl enable chloe

echo "[setup] Configuring nginx..."
cp ops/nginx.conf /etc/nginx/sites-available/chloe
ln -sf /etc/nginx/sites-available/chloe /etc/nginx/sites-enabled/chloe
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

echo ""
echo "[setup] Complete!"
echo "  1. Edit /opt/chloe/.env and set GEMINI_API_KEY"
echo "  2. Run: systemctl start chloe"
echo "  3. Check: systemctl status chloe"
echo "  4. Logs:  journalctl -u chloe -f"
