#!/bin/bash
# Watchdog: monitor QwenPaw app process, auto-restart if dead
# Usage: nohup bash scripts/watchdog.sh &

# ============ QWENPAW LLM RETRY ENV (bắt buộc cho app con) ============
# QwenPaw đọc các env này tại import time (constant.py). Export ở đây để
# app được watchdog spawn ra luôn kế thừa — không phụ thuộc .bashrc.
export QWENPAW_LLM_MAX_RETRIES=6
export QWENPAW_LLM_BACKOFF_BASE=1.0
export QWENPAW_LLM_BACKOFF_CAP=15.0
export QWENPAW_LLM_MAX_CONCURRENT=10
export QWENPAW_LLM_RATE_LIMIT_PAUSE=5.0
export QWENPAW_LLM_RATE_LIMIT_JITTER=0.5

LOG=~/.qwenpaw/qwenpaw.log
APP_PATTERN="qwenpaw app"
CHECK_INTERVAL=30
MAX_ERRORS=10

error_count=0

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [watchdog] $1"
}

while true; do
    # Check if QwenPaw app is running
    if ! pgrep -f "$APP_PATTERN" > /dev/null 2>&1; then
        log "QwenPaw app NOT running — restarting..."
        cd /home/huythong/.qwenpaw/workspaces/trading
        qwenpaw app &
        sleep 10
        error_count=0
        log "QwenPaw app restarted (PID: $(pgrep -f "$APP_PATTERN"))"
    fi

    # Check recent errors in log
    recent_errors=$(tail -200 "$LOG" 2>/dev/null | grep -c "unhandled error" || echo 0)
    if [ "$recent_errors" -gt "$MAX_ERRORS" ]; then
        log "Too many recent errors ($recent_errors > $MAX_ERRORS) — restarting..."
        pkill -f "$APP_PATTERN" 2>/dev/null
        sleep 5
        cd /home/huythong/.qwenpaw/workspaces/trading
        qwenpaw app &
        sleep 10
        error_count=0
        log "QwenPaw app force-restarted"
    fi

    sleep $CHECK_INTERVAL
done
