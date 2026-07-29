#!/usr/bin/env bash
# scripts/restore.sh — Restore from S3 backup
# Usage: ./restore.sh [--date YYYYMMDD] [--backup-path PATH] [--target-db DB_NAME]

set -euo pipefail

# Configuration
S3_BUCKET="${S3_BUCKET:-trading-agent-backups}"
S3_ENDPOINT="${S3_ENDPOINT:-}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-}"
S3_SECRET_KEY="${S3_SECRET_KEY:-}"
DB_HOST="${DB_HOST:-timescaledb}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-trading}"
DB_NAME="${DB_NAME:-trading}"
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
CONFIG_DIR="/opt/trading-agent/config"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $*"; }
error() { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $*"; }

RESTORE_DATE=""
BACKUP_PATH=""
TARGET_DB=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --date) RESTORE_DATE="$2"; shift 2 ;;
        --backup-path) BACKUP_PATH="$2"; shift 2 ;;
        --target-db) TARGET_DB="$2"; shift 2 ;;
        *) error "Unknown option: $1"; exit 1 ;;
    esac
done

# Determine backup path
if [[ -n "$BACKUP_PATH" ]]; then
    S3_SOURCE="$BACKUP_PATH"
elif [[ -n "$RESTORE_DATE" ]]; then
    # Format: YYYY/MM/DD
    FORMATTED_DATE=$(echo "$RESTORE_DATE" | sed 's/\(....\)\(..\)\(..\)/\1\/\2\/\3/')
    S3_SOURCE="s3://$S3_BUCKET/trading-agent/$FORMATTED_DATE/"
else
    # Latest backup
    log "Finding latest backup..."
    S3_SOURCE=$(aws s3 ls "s3://$S3_BUCKET/trading-agent/" --recursive | sort | tail -1 | awk '{print "s3://"$3}' | sed 's|/[^/]*$|/|')
fi

if [[ -z "$S3_SOURCE" ]]; then
    error "No backup found"
    exit 1
fi

log "Restoring from: $S3_SOURCE"

# Download backup
RESTORE_DIR="/tmp/restore_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RESTORE_DIR"

AWS_CLI_OPTS=()
[[ -n "$S3_ENDPOINT" ]] && AWS_CLI_OPTS+=(--endpoint-url "$S3_ENDPOINT")
[[ -n "$S3_ACCESS_KEY" ]] && export AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY"
[[ -n "$S3_SECRET_KEY" ]] && export AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY"

log "Downloading backup..."
aws "${AWS_CLI_OPTS[@]}" s3 sync "$S3_SOURCE" "$RESTORE_DIR"

# Find dump file
DUMP_FILE=$(find "$RESTORE_DIR" -name "timescaledb_*.dump" | head -1)
if [[ -z "$DUMP_FILE" ]]; then
    error "No TimescaleDB dump found in backup"
    exit 1
fi
log "Found DB dump: $DUMP_FILE"

# Find Redis RDB
RDB_FILE=$(find "$RESTORE_DIR" -name "redis_*.rdb" | head -1)

# Find config tar
CONFIG_TAR=$(find "$RESTORE_DIR" -name "config_*.tar.gz" | head -1)

# Confirm before restore
echo ""
warn "⚠️  THIS WILL OVERWRITE THE CURRENT DATABASE"
warn "Target DB: ${TARGET_DB:-$DB_NAME} on $DB_HOST:$DB_PORT"
echo ""
read -p "Type 'RESTORE' to confirm: " CONFIRM
if [[ "$CONFIRM" != "RESTORE" ]]; then
    log "Aborted"
    exit 1
fi

# 1. Restore TimescaleDB
log "Restoring TimescaleDB..."
# Terminate existing connections
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -c "
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname = '${TARGET_DB:-$DB_NAME}' AND pid <> pg_backend_pid();
" || true

# Drop and recreate database
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d postgres -c "
    DROP DATABASE IF EXISTS ${TARGET_DB:-$DB_NAME};
    CREATE DATABASE ${TARGET_DB:-$DB_NAME};
" || { error "Failed to recreate database"; exit 1; }

# Restore dump
pg_restore \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "${TARGET_DB:-$DB_NAME}" \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists \
    "$DUMP_FILE"

log "TimescaleDB restore complete"

# 2. Restore Redis
if [[ -n "$RDB_FILE" ]]; then
    log "Restoring Redis..."
    # Stop Redis, replace RDB, restart
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" SHUTDOWN NOSAVE
    sleep 2
    cp "$RDB_FILE" /data/dump.rdb  # Assumes Redis data dir mounted
    # Redis will auto-load on startup
    log "Redis restore initiated (will load on next startup)"
fi

# 3. Restore Config
if [[ -n "$CONFIG_TAR" ]]; then
    log "Restoring config files..."
    tar -xzf "$CONFIG_TAR" -C "$(dirname "$CONFIG_DIR")"
    log "Config restore complete"
fi

# Cleanup
rm -rf "$RESTORE_DIR"

log "✅ Restore completed successfully"
log "Verify: Run health checks and check trading-agent logs"