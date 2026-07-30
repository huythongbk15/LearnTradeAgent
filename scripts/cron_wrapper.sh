#!/usr/bin/env bash
# =============================================================================
# Trading Agent — Cron Wrapper
# Chạy các lệnh trading qua Docker, log stdout/stderr ra file riêng.
# =============================================================================
set -euo pipefail

PROJECT_DIR="/home/huythong/.qwenpaw/workspaces/trading"
LOG_DIR="$PROJECT_DIR/logs"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')

mkdir -p "$LOG_DIR"

run_trade() {
    # Chạy 1 cycle trading trên BTC/USDT
    local logfile="$LOG_DIR/cron_trade_$TIMESTAMP.log"
    echo "[$TIMESTAMP] Running trading cycle..." >> "$logfile"
    cd "$PROJECT_DIR" && \
        docker compose --profile app run --rm trading-agent \
        execution run BTC/USDT --timeframe 1h --auto \
        >> "$logfile" 2>&1
    echo "[$TIMESTAMP] Done (exit: $?)" >> "$logfile"
}

run_backup() {
    # Backup DB + config (giữ 7 bản gần nhất)
    local logfile="$LOG_DIR/cron_backup_$TIMESTAMP.log"
    echo "[$TIMESTAMP] Running backup..." >> "$logfile"
    cd "$PROJECT_DIR" && python3 scripts/backup_local.py \
        >> "$logfile" 2>&1
    echo "[$TIMESTAMP] Done (exit: $?)" >> "$logfile"
}

run_retention() {
    # Dọn log cũ quá 14 ngày
    find "$LOG_DIR" -name '*.log' -mtime +14 -delete 2>/dev/null
    # Dọn backup cũ quá 30 ngày (ngoài retention của backup_local.py)
    find "$PROJECT_DIR/backups" -mindepth 1 -maxdepth 1 -type d -mtime +30 \
        -exec rm -rf {} + 2>/dev/null || true
}

case "${1:-help}" in
    trade)   run_trade ;;
    backup)  run_backup ;;
    retention) run_retention ;;
    *)
        echo "Usage: $0 {trade|backup|retention}"
        exit 1
        ;;
esac
