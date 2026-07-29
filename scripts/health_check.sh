#!/usr/bin/env bash
# scripts/health_check.sh — Comprehensive health check for trading agent system

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

OK=0
WARN=0
FAIL=0

check() {
    local name="$1"
    local cmd="$2"
    local timeout="${3:-10}"

    echo -n "Checking $name... "
    if timeout "$timeout" bash -c "$cmd" >/dev/null 2>&1; then
        echo -e "${GREEN}✓ OK${NC}"
        ((OK++))
        return 0
    else
        echo -e "${RED}✗ FAIL${NC}"
        ((FAIL++))
        return 1
    fi
}

warn_check() {
    local name="$1"
    local cmd="$2"
    local timeout="${3:-10}"

    echo -n "Checking $name... "
    if timeout "$timeout" bash -c "$cmd" >/dev/null 2>&1; then
        echo -e "${GREEN}✓ OK${NC}"
        ((OK++))
        return 0
    else
        echo -e "${YELLOW}⚠ WARN${NC}"
        ((WARN++))
        return 1
    fi
}

echo "=========================================="
echo " Trading Agent System — Health Check"
echo "=========================================="
echo ""

# 1. Application Health
check "Trading Agent HTTP" "curl -sf http://localhost:8000/healthz"
check "Trading Agent Metrics" "curl -sf http://localhost:8000/metrics | grep -q trading_equity"

# 2. Database
check "TimescaleDB Connection" "pg_isready -h timescaledb -p 5432 -U trading -d trading"
check "TimescaleDB Writable" "psql -h timescaledb -U trading -d trading -c 'INSERT INTO health_check (id, ts) VALUES (1, NOW()) ON CONFLICT (id) DO UPDATE SET ts=NOW()' >/dev/null"

# 3. Redis
check "Redis Connection" "redis-cli -h redis -p 6379 ping | grep -q PONG"
check "Redis Writable" "redis-cli -h redis -p 6379 SET health_check_ok 1 EX 10 >/dev/null"

# 4. Exchange Connectivity
check "Binance REST API" "curl -sf 'https://api.binance.com/api/v3/ping' | grep -q '{}'"
check "Binance WebSocket" "timeout 5 bash -c 'exec 3<>/dev/tcp/stream.binance.com/9443 && echo -e \"GET /ws/btcusdt@trade HTTP/1.1\r\nHost: stream.binance.com\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n\" >&3 && cat <&3 | head -1' | grep -q 'HTTP/1.1 101'"

# 5. Monitoring Stack
check "Prometheus" "curl -sf http://prometheus:9090/-/healthy"
check "Grafana" "curl -sf http://grafana:3000/api/health | grep -q 'ok'"
check "Loki" "curl -sf http://loki:3100/ready"
check "Promtail" "curl -sf http://promtail:9080/ready"

# 6. Nginx
check "Nginx" "curl -sf http://nginx/healthz"
check "SSL Cert Valid (30+ days)" "openssl x509 -enddate -noout -in /etc/letsencrypt/live/trading-agent.example.com/fullchain.pem | awk -F= '{print \$2}' | xargs -I {} date -d {} +%s | awk -v now=$(date +%s) '{if ((\$1 - now) > 2592000) exit 0; else exit 1}'"

# 7. Disk Space
warn_check "Disk Space (/)" "df -h / | awk 'NR==2 {gsub(/%/,\"\",\$5); if (\$5 < 85) exit 0; else exit 1}'"
warn_check "Disk Space (/var/lib/docker)" "df -h /var/lib/docker | awk 'NR==2 {gsub(/%/,\"\",\$5); if (\$5 < 85) exit 0; else exit 1}'"

# 8. Memory
warn_check "Memory Usage" "free | awk 'NR==2 {if (\$3/\$2 < 0.85) exit 0; else exit 1}'"

# 9. Container Health
check "Docker Daemon" "docker info >/dev/null"
check "Trading Agent Container Running" "docker ps --filter 'name=trading-agent' --filter 'status=running' | grep -q trading-agent"

# 10. Data Freshness
warn_check "Recent Equity Snapshot (< 5 min)" "psql -h timescaledb -U trading -d trading -t -c \"SELECT COUNT(*) FROM equity_snapshots WHERE timestamp > NOW() - INTERVAL '5 minutes'\" | grep -q '^ *[1-9]'"

echo ""
echo "=========================================="
echo " Summary: ${GREEN}$OK passed${NC}, ${YELLOW}$WARN warnings${NC}, ${RED}$FAIL failed${NC}"
echo "=========================================="

if [[ $FAIL -gt 0 ]]; then
    exit 1
elif [[ $WARN -gt 0 ]]; then
    exit 2
else
    exit 0
fi