# Trading Agent System — RUNBOOK (Local-First)

> Hướng dẫn vận hành cho môi trường LOCAL (WSL/Linux), không VPS, không SSH, không GitHub Actions staging.

---

## 🚨 Incident Response — Quick Reference

| Severity | Mô tả | Response Time | Escalation |
|----------|-------|---------------|------------|
| **SEV-1** | Trading stopped, data loss, money at risk | 15 min | Page → Owner |
| **SEV-2** | Degraded performance, partial outage | 30 min | Page → Owner |
| **SEV-3** | Minor issue, non-critical | 2 hours | Ticket → next day |

### SEV-1 Checklist
- [ ] Acknowledge alert (Telegram/Phone)
- [ ] Run `./scripts/health_check.sh` để assess scope
- [ ] If DB down → `docker compose -f docker-compose.yml restart timescaledb`
- [ ] If agent crashed → check logs, restart via `docker compose restart trading-agent`
- [ ] If exchange API down → verify circuit breaker, check exchange status page
- [ ] Document timeline in incident doc
- [ ] Post-incident review within 48h

---

## 🔧 Common Procedures (Local-First)

### 1. Health Check
```bash
# Quick health check
./scripts/health_check.sh

# Manual checks
curl -sf http://localhost:8000/healthz
curl -sf http://localhost:8000/metrics | grep trading_equity
docker compose ps
```

### 2. View Logs
```bash
# App logs (structured JSON)
docker compose logs -f trading-agent --tail=100

# Filter by component
docker compose logs -f trading-agent | grep "trading_agent.execution"

# System logs
journalctl -u docker -f
```

### 3. Restart Trading Agent
```bash
# Graceful restart (SIGTERM → SIGKILL after 30s)
docker compose restart trading-agent

# Force restart
docker compose kill trading-agent && docker compose up -d trading-agent

# With profile
docker compose --profile app up -d trading-agent
```

### 4. Emergency Stop All Trading
```bash
# Via CLI (if agent responsive)
python -m trading_agent.cli execution close --all --confirm

# Nuclear option: scale to 0
docker compose scale trading-agent=0

# Or stop entire stack
docker compose down
```

### 5. Database Operations
```bash
# Connect to TimescaleDB
docker compose exec timescaledb psql -U trading -d trading_market_data

# Check trade count
psql -h localhost -U trading -d trading_market_data -c "SELECT COUNT(*) FROM trades;"

# Backup DB
./scripts/backup_local.sh

# Restore from backup
./scripts/restore_local.sh --date 20241215
```

### 6. Redis Operations
```bash
# Connect
docker compose exec redis redis-cli

# Check keys
redis-cli KEYS "trading:*"

# Flush (careful!)
redis-cli FLUSHDB
```

---

## 📊 Monitoring & Alerts (Local Stack)

### Key Dashboards
| Dashboard | URL | Description |
|-----------|-----|-------------|
| Grafana | http://localhost:3000 | Main dashboards (admin/admin) |
| Prometheus | http://localhost:9090 | Metrics query |
| Streamlit | http://localhost:8501 | Trading dashboard |
| Loki | http://localhost:3100 | Logs (via Grafana) |

### Critical Local Alerts (Telegram)
| Alert | Condition | Action |
|-------|-----------|--------|
| `TradingMaxDrawdownBreach` | Drawdown > 10% | Check circuit breaker, review positions |
| `TradingDailyLossBreach` | Daily loss > 5% | Check circuit breaker, stop trading |
| `TradingAgentDown` | No health check 2 min | Restart agent, check logs |
| `TimescaleDBDown` | pg_isready fails | Restart timescaledb container |
| `DiskSpaceCritical` | Disk > 85% | Cleanup logs, expand volume |

### Silencing Alerts (Local)
```bash
# No Alertmanager in local stack — just disable cron temporarily
crontab -l | grep -v "trade_local" | crontab -
# Restore after fix
crontab scripts/crontab_local.txt
```

---

## 💾 Backup & Restore (Local-First)

### Backup Schedule (via cron)
| What | When | Retention |
|------|------|-----------|
| TimescaleDB (pg_dump) | Daily 23:00 | 30 days |
| Redis (RDB) | Daily 23:00 | 30 days |
| Config + .env.local | Daily 23:00 | 30 days |
| Logs (tar.gz) | Weekly Sunday | 14 days |

### Manual Backup
```bash
# Full backup
./scripts/backup_local.sh

# Backup to custom location
./scripts/backup_local.sh --dest /mnt/backup/trading-agent

# List backups
ls -la backups/
```

### Restore
```bash
# Latest backup
./scripts/restore_local.sh

# Specific date
./scripts/restore_local.sh --date 20241215

# Dry run (show what would be restored)
./scripts/restore_local.sh --dry-run
```

### Verify Restore
```bash
# Check trade count
psql -h localhost -U trading -d trading -c "SELECT COUNT(*) FROM trades;"

# Check equity snapshots
psql -h localhost -U trading -d trading -c "SELECT COUNT(*) FROM equity_snapshots;"

# Run health check
./scripts/health_check.sh
```

---

## 🔐 Security Procedures (Local)

### API Key Rotation (Quarterly)
```bash
# 1. Generate new keys on exchange (Binance, Bybit, etc.)
# 2. Update .env.local
nano .env.local
# BINANCE_API_KEY=new_key
# BINANCE_API_SECRET=new_secret

# 3. Restart agent to pick up new keys
docker compose restart trading-agent

# 4. Verify health
./scripts/health_check.sh

# 5. Revoke old keys on exchange
```

### Secrets Management (Local)
- **DO NOT** commit `.env.local` to git (in `.gitignore`)
- Store backup of `.env.local` in encrypted location (1Password, Bitwarden, age/gpg)
- Rotate keys quarterly or after any suspected exposure

### Incident: Suspected Compromise
1. **Isolate**: `docker compose scale trading-agent=0`
2. **Rotate**: All API keys, DB passwords, SSH keys
3. **Audit**: Check logs for unusual activity
   ```bash
   docker compose logs trading-agent | grep -i "unauthorized\|failed\|error"
   ```
4. **Report**: Exchange security@ if API keys compromised

---

## 📈 Capacity Planning (Local)

### Current Specs (WSL2 / Linux)
| Component | Resources | Headroom |
|-----------|-----------|----------|
| Trading Agent | 1 vCPU, 1GB RAM | 50% CPU at peak |
| TimescaleDB | 2 vCPU, 4GB RAM, 50GB SSD | 60% storage |
| Redis | 1 vCPU, 1GB RAM | 40% memory |
| Grafana | 1 vCPU, 512MB RAM | 30% CPU |
| Prometheus | 1 vCPU, 2GB RAM, 10GB | 30d retention |

### Scaling Triggers (Local)
- **CPU > 70% for 10min** → Reduce trade frequency (cron: 2h → 4h)
- **DB connections > 80%** → Add PgBouncer (future)
- **Redis memory > 75%** → Reduce cache TTL
- **Disk > 80%** → Cleanup old backups, reduce Prometheus retention

### Load Test (Local)
```bash
# Quick load test
for i in {1..10}; do
  python -m trading_agent.cli execution run BTC/USDT --timeframe 1h &
done
wait
```

---

## 📞 Contacts (Local)

| Role | Contact |
|------|---------|
| Owner/On-call | You (Telegram bot) |
| Exchange Support | Binance: support@binance.com |

---

## 📝 Post-Incident Template

```markdown
# Incident #INC-YYYYMMDD-XXX: [Title]

**Date**: YYYY-MM-DD
**Duration**: HH:MM
**Severity**: SEV-1/2/3
**Status**: Resolved / Monitoring

## Summary
Brief description of what happened.

## Timeline
- HH:MM - Alert fired
- HH:MM - Acknowledged
- HH:MM - Root cause identified
- HH:MM - Fix deployed
- HH:MM - Service restored

## Root Cause
Technical explanation.

## Impact
- Trades affected: X
- P&L impact: $X
- Downtime: X min

## Action Items
- [ ] Fix: Description (Owner, Due date)
- [ ] Prevention: Description (Owner, Due date)
- [ ] Monitoring: Add alert for X (Owner, Due date)

## Lessons Learned
What worked well, what didn't.
```

---

*Last updated: $(date +%Y-%m-%d)*
*Review quarterly or after each SEV-1 incident*