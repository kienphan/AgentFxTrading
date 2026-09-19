#!/usr/bin/env bash
# =============================================================================
# Automated PostgreSQL Backup Script for AgentFxTrading
# Keeps compressed daily dumps and cleans up backups older than 14 days.
# Connection details come from DATABASE_URL in <project>/.env — nothing is
# hard-coded here. PROJECT_ROOT can be overridden via the environment.
# =============================================================================

set -euo pipefail
umask 077

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ENV_FILE="${PROJECT_ROOT}/.env"
BACKUP_DIR="${PROJECT_ROOT}/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/agentfx_${TIMESTAMP}.sql.gz"
LOG_FILE="${BACKUP_DIR}/backup.log"

mkdir -p "${BACKUP_DIR}"

log() { echo "[$(date -u +"%Y-%m-%d %H:%M:%S UTC")] $*" >> "${LOG_FILE}"; }

# Last DATABASE_URL= line wins; surrounding quotes are stripped.
DATABASE_URL="$(grep -E '^DATABASE_URL=' "${ENV_FILE}" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d "\"'" || true)"
if [[ -z "${DATABASE_URL}" ]]; then
    log "Backup FAILED: DATABASE_URL not found in ${ENV_FILE}"
    exit 1
fi

log "Starting backup..."

# libpq accepts the URL directly (percent-encoded password included).
if pg_dump --dbname="${DATABASE_URL}" --clean --if-exists --no-owner --no-privileges | gzip > "${BACKUP_FILE}"; then
    BACKUP_SIZE=$(du -h "${BACKUP_FILE}" | cut -f1)
    log "Backup SUCCESS: ${BACKUP_FILE} (${BACKUP_SIZE})"
else
    log "Backup FAILED!"
    rm -f "${BACKUP_FILE}"
    exit 1
fi

# Retention policy: remove backups older than 14 days
find "${BACKUP_DIR}" -type f -name "agentfx_*.sql.gz" -mtime +14 -delete
log "Cleaned up backups older than 14 days."
