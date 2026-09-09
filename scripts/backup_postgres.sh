#!/usr/bin/env bash
# =============================================================================
# Automated PostgreSQL Backup Script for AgentFxTrading
# Keeps compressed daily dumps and cleans up backups older than 14 days.
# =============================================================================

set -euo pipefail

BACKUP_DIR="/root/AgentFxTrading/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/agentfx_${TIMESTAMP}.sql.gz"
LOG_FILE="${BACKUP_DIR}/backup.log"

mkdir -p "${BACKUP_DIR}"

export PGPASSWORD="kaz@112358."
export PGHOST="127.0.0.1"
export PGPORT="5432"
export PGUSER="agentfx"
export PGDATABASE="agentfx"

echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] Starting backup..." >> "${LOG_FILE}"

if pg_dump --clean --if-exists --no-owner --no-privileges | gzip > "${BACKUP_FILE}"; then
    BACKUP_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
    echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] Backup SUCCESS: ${BACKUP_FILE} (${BACKUP_SIZE})" >> "${LOG_FILE}"
else
    echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] Backup FAILED!" >> "${LOG_FILE}"
    exit 1
fi

# Retention policy: remove backups older than 14 days
find "${BACKUP_DIR}" -type f -name "agentfx_*.sql.gz" -mtime +14 -delete
echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] Cleaned up backups older than 14 days." >> "${LOG_FILE}"
