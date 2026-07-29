#!/usr/bin/env bash
# scripts/backup.sh — Automated backup for TimescaleDB, Redis, and Config
# Usage: ./backup.sh [--s3-bucket BUCKET] [--retention-days DAYS]

set -euo pipefail

# Configuration
S3_BUCKET="${S3_BUCKET:-trading-agent-backups}"
S3_ENDPOINT="${S3_ENDPOINT:-}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-}"
S3_SECRET_KEY="${S3_SECRET_KEY:-}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
BACKUP_DIR="/tmp/backup_$(date +%Y%m%d_%H%M%S)"
DB_HOST="${DB_HOST:-timescaledb}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-trading}"
DB_NAME="${DB_NAME:-trading}"
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
CONFIG_DIR="/opt/trading-agent/config"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $*"; }
error() { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $*"; }

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --s3-bucket) S3_BUCKET="$2"; shift 2 ;;
        --retention-days) RETENTION_DAYS="$2"; shift 2 ;;
        *) error "Unknown option: $1"; exit 1 ;;
    esac
done

# Check dependencies
command -v pg_dump >/dev/null || { error "pg_dump not found"; exit 1; }
command -v redis-cli >/dev/null || { error "redis-cli not found"; exit 1; }
command -v aws >/dev/null || { error "aws cli not found"; exit 1; }

log "Starting backup to s3://$S3_BUCKET"
mkdir -p "$BACKUP_DIR"

# 1. TimescaleDB Backup (custom format, compressed)
log "Backing up TimescaleDB..."
pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    -Fc \
    -Z 9 \
    --no-owner \
    --no-privileges \
    > "$BACKUP_DIR/timescaledb_$(date +%Y%m%d_%H%M%S).dump"

if [[ ! -s "$BACKUP_DIR"/timescaledb_*.dump ]]; then
    error "TimescaleDB backup failed or empty"
    exit 1
fi
log "TimescaleDB backup complete"

# 2. Redis Backup (RDB)
log "Backing up Redis..."
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" BGSAVE
# Wait for BGSAVE to complete
while [[ "$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" LASTSAVE)" -eq "$(redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" LASTSAVE)" ]]; do
    sleep 1
done
redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" --rdb "$BACKUP_DIR/redis_$(date +%Y%m%d_%H%M%S).rdb"
log "Redis backup complete"

# 3. Config Files Backup
log "Backing up config files..."
if [[ -d "$CONFIG_DIR" ]]; then
    tar -czf "$BACKUP_DIR/config_$(date +%Y%m%d_%H%M%S).tar.gz" -C "$(dirname "$CONFIG_DIR")" "$(basename "$CONFIG_DIR")"
    log "Config backup complete"
else
    warn "Config directory not found: $CONFIG_DIR"
fi

# 4. Upload to S3
log "Uploading to S3..."
DATE_PREFIX=$(date +%Y/%m/%d)
S3_PREFIX="s3://$S3_BUCKET/trading-agent/$DATE_PREFIX"

AWS_CLI_OPTS=()
[[ -n "$S3_ENDPOINT" ]] && AWS_CLI_OPTS+=(--endpoint-url "$S3_ENDPOINT")
[[ -n "$S3_ACCESS_KEY" ]] && export AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY"
[[ -n "$S3_SECRET_KEY" ]] && export AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY"

aws "${AWS_CLI_OPTS[@]}" s3 sync "$BACKUP_DIR" "$S3_PREFIX" --storage-class STANDARD_IA
log "Upload complete: $S3_PREFIX"

# 5. Cleanup old backups (local)
log "Cleaning up local temp files..."
rm -rf "$BACKUP_DIR"

# 6. Cleanup old S3 backups (retention)
log "Applying retention policy ($RETENTION_DAYS days)..."
aws "${AWS_CLI_OPTS[@]}" s3 ls "s3://$S3_BUCKET/trading-agent/" --recursive | \
    while read -r line; do
        FILE_DATE=$(echo "$line" | awk '{print $1" "$2}')
        FILE_PATH=$(echo "$line" | awk '{print $4}')
        if [[ $(date -d "$FILE_DATE" +%s) -lt $(date -d "-$RETENTION_DAYS days" +%s) ]]; then
            log "Deleting old backup: $FILE_PATH"
            aws "${AWS_CLI_OPTS[@]}" s3 rm "s3://$S3_BUCKET/$FILE_PATH"
        fi
    done

log "✅ Backup completed successfully"