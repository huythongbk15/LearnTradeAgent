# Trading Agent System — RUNBOOK (Sổ tay vận hành)

> Hướng dẫn vận hành: incident response, deployment, và maintenance procedures.

---

## 🚨 Incident Response — Quick Reference (Tra cứu nhanh)

| Severity | Response Time | Escalation |
|----------|---------------|------------|
| **SEV-1** (Trading stopped, data loss) | 15 min | Page on-call → CTO |
| **SEV-2** (Degraded performance, partial outage) | 30 min | Page on-call |
| **SEV-3** (Minor issue, non-critical) | 2 hours | Ticket → next business day |

### SEV-1 Checklist
- [ ] Acknowledge alert in PagerDuty/Opsgenie
- [ ] Check `#incidents` Slack channel
- [ ] Run `./scripts/health_check.sh` to assess scope
- [ ] If DB down → execute DB failover procedure
- [ ] If agent crashed → check logs, restart via `docker compose restart trading-agent`
- [ ] If exchange API down → verify circuit breaker, check exchange status page
- [ ] Document timeline in incident doc
- [ ] Post-incident review within 48h

---

## 🔧 Common Procedures (Thủ tục thường dùng)

### 1. Health Check
```bash
# Quick health check
./scripts/health_check.sh

# Or manually:
curl -sf https://trading-agent.example.com/healthz
curl -sf https://trading-agent.example.com/api/metrics
docker compose -f docker-compose.prod.yml ps
```

### 2. View Logs (Xem logs)
```bash
# Application logs (Loki/Grafana preferred)
# Via CLI:
docker compose -f docker-compose.prod.yml logs -f trading-agent --tail=100

# System logs
journalctl -u docker -f

# Nginx
docker compose -f docker-compose.prod.yml logs nginx
```

### 3. Restart Trading Agent (Zero-downtime)
```bash
# Rolling restart (3 replicas)
docker compose -f docker-compose.prod.yml up -d --no-deps --scale trading-agent=3 trading-agent
# Wait for health checks...
docker compose -f docker-compose.prod.yml up -d --no-deps --scale trading-agent=3 trading-agent
```

### 4. Emergency Stop All Trading (Dừng khẩn cấp tất cả trading)
```bash
# Via CLI (if agent responsive)
python -m trading_agent.cli execution close --all --confirm

# Via API
curl -X POST https://trading-agent.example.com/api/execution/close-all \
  -H "Authorization: Bearer $API_TOKEN"

# Nuclear option: scale to 0
docker compose -f docker-compose.prod.yml scale trading-agent=0
```

### 5. Database Failover (TimescaleDB + Patroni)
```bash
# Check cluster status
patronictl -c /etc/patroni.yml list

# Manual failover (if auto-failover failed)
patronictl -c /etc/patroni.yml failover --master trading-cluster --candidate <replica_name>

# Verify
psql -h timescaledb -U trading -c "SELECT pg_is_in_recovery();"
```

### 6. Redis Failover (Sentinel)
```bash
# Check sentinel status
redis-cli -h redis-sentinel -p 26379 SENTINEL get-master-addr-by-name trading-master

# Manual failover
redis-cli -h redis-sentinel -p 26379 SENTINEL failover trading-master
```

### 7. Rollback Deployment
```bash
# Blue-green: switch nginx upstream back
# Edit /etc/nginx/conf.d/upstream.conf to point to previous stack
nginx -s reload

# Or via GitHub Actions: re-run previous successful deployment workflow
```

---

## 📊 Monitoring & Alerts (Giám sát & Cảnh báo)

### Key Dashboards
- **Trading Overview**: https://grafana.trading-agent.example.com/d/trading-overview
- **System Metrics**: https://grafana.trading-agent.example.com/d/system-metrics
- **Logs**: https://grafana.trading-agent.example.com/explore (Loki)

### Critical Alerts
| Alert | Condition | Action |
|-------|-----------|--------|
| `TradingMaxDrawdownBreach` | Drawdown > 10% | Check circuit breaker, review positions |
| `TradingDailyLossBreach` | Daily loss > 5% | Check circuit breaker, stop trading |
| `TradingAgentDown` | No health check for 2 min | Restart agent, check logs |
| `TimescaleDBReplicationLag` | Lag > 30s | Check replica, consider failover |
| `RedisSentinelDown` | Sentinel quorum lost | Check Redis nodes, restart sentinel |
| `DiskSpaceCritical` | Disk > 85% | Cleanup logs, expand volume |
| `ExchangeAPIErrorRate` | Error rate > 5% | Check exchange status, reduce rate |

### Silencing Alerts (Tạm tắt cảnh báo)
```bash
# Via Alertmanager (if configured)
amtool silence add --duration=2h --author="oncall" alertname=TradingAgentDown

# Or in Grafana: Alerting → Silences
```

---

## 💾 Backup & Restore

### Backup Schedule
- **TimescaleDB**: Daily 04:00 UTC (pg_dump custom format, compressed)
- **Redis**: Daily 04:00 UTC (RDB snapshot)
- **Config**: Daily 04:00 UTC (tar.gz)
- **Retention**: 30 days daily, 12 months monthly

### Manual Backup
```bash
./scripts/backup.sh --s3-bucket trading-agent-backups
```

### Restore Procedure
```bash
# List available backups
aws s3 ls s3://trading-agent-backups/trading-agent/ --recursive

# Restore specific date
./scripts/restore.sh --date 20241215

# Restore latest
./scripts/restore.sh

# Point-in-time recovery (TimescaleDB)
# Requires WAL-G or pg_basebackup + WAL files
# See: https://github.com/wal-g/wal-g
```

### Verify Restore
```bash
# Check trade count
psql -h timescaledb -U trading -d trading -c "SELECT COUNT(*) FROM trades;"

# Check equity snapshots
psql -h timescaledb -U trading -d trading -c "SELECT COUNT(*) FROM equity_snapshots;"

# Run health check
./scripts/health_check.sh
```

---

## 🔐 Security Procedures

### API Key Rotation (Quarterly / Hàng quý)
```bash
# 1. Generate new keys on exchange
# 2. Update in 1Password/Vault
# 3. Update Docker secrets
echo "new_key" | docker secret create binance_api_key -
# 4. Rolling restart
docker compose -f docker-compose.prod.yml up -d --no-deps trading-agent
# 5. Verify health
./scripts/health_check.sh
# 6. Revoke old keys on exchange
```

### Certificate Renewal (Let's Encrypt)
```bash
# Auto-renewed by certbot timer
# Manual check:
certbot certificates

# Force renewal
certbot renew --force-renewal
nginx -s reload
```

### Incident: Suspected Compromise (Nghi ngờ bị xâm nhập)
1. **Isolate**: Scale trading-agent to 0, block outbound except monitoring
2. **Rotate**: All API keys, DB passwords, SSH keys
3. **Audit**: Check logs for unusual activity (Loki query: `{job="trading-agent"} |~ "unauthorized|failed|error"`)
4. **Report**: Security team, exchange security@ if API keys compromised

---

## 📈 Capacity Planning

### Current Specs (Production)
| Component | Spec | Headroom |
|-----------|------|----------|
| Trading Agent | 3 × 1 vCPU, 1GB RAM | 50% CPU at peak |
| TimescaleDB | 2 vCPU, 4GB RAM, 100GB SSD | 60% storage |
| Redis | 1 vCPU, 2GB RAM | 40% memory |
| Nginx | 1 vCPU, 512MB RAM | 30% CPU |

### Scaling Triggers
- **CPU > 70% for 10min** → Add agent replica
- **DB connections > 80%** → Add PgBouncer, consider read replica
- **Redis memory > 75%** → Increase memory or add shard
- **Disk > 80%** → Expand volume, cleanup old backups

### Load Test
```bash
# Locust load test (100 users, 10 min)
locust -f tests/load_test.py --host=https://trading-agent.example.com \
  --users 100 --spawn-rate 10 --run-time 10m --headless
```

---

## 📞 Contacts

| Role | Name | Phone | Slack | Email |
|------|------|-------|-------|-------|
| Primary On-call | | | | |
| Secondary On-call | | | | |
| Trading Lead | | | | |
| Infra Lead | | | | |
| Security | | | | |
| Exchange Support (Binance) | | | | support@binance.com |

---

## 📝 Post-Incident Template

```markdown
# Incident #INC-XXXX: [Title]

**Date**: YYYY-MM-DD
**Duration**: HH:MM
**Severity**: SEV-1/2/3
**Status**: Resolved / Monitoring

## Summary
Brief description of what happened.

## Timeline
- HH:MM - Alert fired
- HH:MM - On-call acknowledged
- HH:MM - Root cause identified
- HH:MM - Fix deployed
- HH:MM - Service restored

## Root Cause
Technical explanation.

## Impact
- Trades affected: X
- P&L impact: $X
- Users affected: X

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