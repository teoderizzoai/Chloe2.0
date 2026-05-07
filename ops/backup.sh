#!/usr/bin/env bash
# ops/backup.sh — nightly backup of Chloe state
set -euo pipefail

CHLOE_HOME="/opt/chloe"
BACKUP_DIR="${CHLOE_HOME}/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
MAX_BACKUPS=14  # Keep 2 weeks

# SQLite backup (hot copy using .backup command)
sqlite3 "${CHLOE_HOME}/data/chloe.db" ".backup ${BACKUP_DIR}/chloe_${TIMESTAMP}.db"

# Chroma backup (tar the directory)
tar -czf "${BACKUP_DIR}/chroma_${TIMESTAMP}.tar.gz" -C "${CHLOE_HOME}/data" chroma/

# Remove old backups
ls -t "${BACKUP_DIR}"/chloe_*.db 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs rm -f
ls -t "${BACKUP_DIR}"/chroma_*.tar.gz 2>/dev/null | tail -n +$((MAX_BACKUPS + 1)) | xargs rm -f

echo "[backup] Done: chloe_${TIMESTAMP}.db + chroma_${TIMESTAMP}.tar.gz"
