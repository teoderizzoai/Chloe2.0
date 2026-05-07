#!/usr/bin/env bash
# ops/bootstrap.sh — Chloe 2.0 VPS provisioning
# Idempotent: safe to re-run on an already-configured server.
# Requires: Debian 12, root or sudo, internet access.

set -euo pipefail

CHLOE_USER="chloe"
CHLOE_HOME="/opt/chloe"
CHLOE_REPO="https://github.com/teo/chloe.git"   # Update to real URL
PYTHON_VERSION="3.12"
CADDY_VERSION="2.8.4"

log() { echo "[bootstrap] $*"; }

# ── System packages ────────────────────────────────────────────────────────────

log "Updating apt and installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-venv \
    python${PYTHON_VERSION}-dev \
    git \
    curl \
    sqlite3 \
    firejail \
    htop \
    ufw \
    logrotate \
    ca-certificates

# ── Caddy ─────────────────────────────────────────────────────────────────────

if ! command -v caddy &>/dev/null; then
    log "Installing Caddy ${CADDY_VERSION}..."
    curl -fsSL "https://github.com/caddyserver/caddy/releases/download/v${CADDY_VERSION}/caddy_${CADDY_VERSION}_linux_amd64.tar.gz" \
        | tar -xz -C /usr/local/bin caddy
    chmod +x /usr/local/bin/caddy
    groupadd --system caddy 2>/dev/null || true
    useradd --system --gid caddy --no-create-home caddy 2>/dev/null || true
fi

# ── Chloe user ────────────────────────────────────────────────────────────────

if ! id "${CHLOE_USER}" &>/dev/null; then
    log "Creating user ${CHLOE_USER}..."
    useradd --system --home-dir "${CHLOE_HOME}" --shell /bin/bash "${CHLOE_USER}"
fi

# ── Directory structure ────────────────────────────────────────────────────────

log "Creating directory structure..."
install -d -o "${CHLOE_USER}" -g "${CHLOE_USER}" -m 750 \
    "${CHLOE_HOME}" \
    "${CHLOE_HOME}/data" \
    "${CHLOE_HOME}/data/chroma" \
    "${CHLOE_HOME}/logs" \
    "${CHLOE_HOME}/backups" \
    "${CHLOE_HOME}/prompts"

# ── Python venv ───────────────────────────────────────────────────────────────

VENV="${CHLOE_HOME}/.venv"
if [ ! -d "${VENV}" ]; then
    log "Creating Python venv at ${VENV}..."
    sudo -u "${CHLOE_USER}" python${PYTHON_VERSION} -m venv "${VENV}"
fi

# ── Clone or update repo ──────────────────────────────────────────────────────

REPO_DIR="${CHLOE_HOME}/app"
if [ ! -d "${REPO_DIR}/.git" ]; then
    log "Cloning repository..."
    sudo -u "${CHLOE_USER}" git clone "${CHLOE_REPO}" "${REPO_DIR}"
else
    log "Updating repository..."
    sudo -u "${CHLOE_USER}" git -C "${REPO_DIR}" pull --ff-only
fi

# ── Install Python dependencies ───────────────────────────────────────────────

log "Installing Python dependencies..."
sudo -u "${CHLOE_USER}" "${VENV}/bin/pip" install --quiet --upgrade pip
sudo -u "${CHLOE_USER}" "${VENV}/bin/pip" install --quiet -e "${REPO_DIR}[all]"

# ── Environment file ──────────────────────────────────────────────────────────

ENV_FILE="${CHLOE_HOME}/.env"
if [ ! -f "${ENV_FILE}" ]; then
    log "Creating .env from example (fill in secrets!)..."
    cp "${REPO_DIR}/.env.example" "${ENV_FILE}"
    chown "${CHLOE_USER}:${CHLOE_USER}" "${ENV_FILE}"
    chmod 600 "${ENV_FILE}"
    log "WARNING: Fill in ${ENV_FILE} before starting the service."
fi

# ── Database migrations ───────────────────────────────────────────────────────

log "Running database migrations..."
sudo -u "${CHLOE_USER}" bash -c "
    cd ${REPO_DIR}
    ${VENV}/bin/python -c 'from chloe.state.db import migrate; migrate()'
"

# ── Systemd unit ──────────────────────────────────────────────────────────────

log "Writing systemd unit..."
cat > /etc/systemd/system/chloe.service <<EOF
[Unit]
Description=Chloe 2.0 AI Companion
After=network.target

[Service]
Type=simple
User=${CHLOE_USER}
WorkingDirectory=${REPO_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV}/bin/python -m chloe.app
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=chloe

# Resource limits
LimitNOFILE=65536
MemoryMax=2G

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable chloe
log "Systemd unit enabled. Start with: systemctl start chloe"

# ── Caddy configuration ───────────────────────────────────────────────────────

CADDYFILE="/etc/caddy/Caddyfile"
mkdir -p /etc/caddy
if [ ! -f "${CADDYFILE}" ]; then
    log "Writing Caddyfile (replace YOUR_DOMAIN)..."
    cat > "${CADDYFILE}" <<'EOF'
YOUR_DOMAIN {
    reverse_proxy localhost:8000
    tls {
        email teo.derizzo@gmail.com
    }
}
EOF
fi

# Write caddy systemd unit
cat > /etc/systemd/system/caddy.service <<EOF
[Unit]
Description=Caddy reverse proxy
After=network.target

[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile
Restart=on-failure
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable caddy

# ── Firewall ──────────────────────────────────────────────────────────────────

log "Configuring UFW firewall..."
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 41641/udp  # Tailscale
ufw --force enable

# ── Nightly backup cron ───────────────────────────────────────────────────────

log "Setting up nightly backup cron..."
cat > /etc/cron.d/chloe-backup <<EOF
# Nightly backup at 03:30 UTC
30 3 * * * ${CHLOE_USER} ${REPO_DIR}/ops/backup.sh >> ${CHLOE_HOME}/logs/backup.log 2>&1
EOF

# ── Chroma rebuild cron ───────────────────────────────────────────────────────

log "Setting up weekly Chroma rebuild cron (Sundays 04:00)..."
cat > /etc/cron.d/chloe-chroma-rebuild <<EOF
# Weekly Chroma rebuild on Sundays at 04:00 UTC
0 4 * * 0 ${CHLOE_USER} cd ${REPO_DIR} && ${VENV}/bin/chloe rebuild-chroma >> ${CHLOE_HOME}/logs/chroma_rebuild.log 2>&1
EOF

# ── Logrotate ─────────────────────────────────────────────────────────────────

cat > /etc/logrotate.d/chloe <<EOF
${CHLOE_HOME}/logs/*.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
}
EOF

# ── Final checks ──────────────────────────────────────────────────────────────

log "Bootstrap complete!"
log ""
log "Next steps:"
log "  1. Fill in ${ENV_FILE} with all required secrets"
log "  2. Update /etc/caddy/Caddyfile with your domain"
log "  3. Set up Tailscale: tailscale up"
log "  4. Start services: systemctl start caddy && systemctl start chloe"
log "  5. Verify: curl http://localhost:8000/health"
