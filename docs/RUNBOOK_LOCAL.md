# Trading Agent System — LOCAL-FIRST RUNBOOK

> Hướng dẫn vận hành **local-first** (không VPS, không SSH, không staging GitHub Actions).
> Tất cả chạy trên máy local (WSL/Linux/macOS) qua Docker Compose + cron.

---

## 🚨 Quick Reference (Tra cứu nhanh)

| Issue | Severity | Action |
|-------|----------|--------|
| **Trading agent crashed** | SEV-1 | `docker compose logs trading-agent` → `docker compose restart trading-agent` |
| **Circuit breaker triggered** | SEV-1 | Check positions, `trading-agent execution reset` hoặc restart |
| **DB connection failed** | SEV-1 | `docker compose restart timescaledb` |
| **Redis down** | SEV-2 | `docker compose restart redis` |
| **Prometheus/Grafana down** | SEV-3 | `docker compose restart prometheus grafana` |
| **Disk space > 85%** | SEV-2 | Cleanup logs/backups: `make clean` |
| **Exchange API rate limit** | SEV-2 | Reduce frequency, check `USE_LLM=false` |

---

## 🔧 Common Procedures (Thủ tục thường dùng)

### 1. Start/Stop Stack
```bash
# Start infrastructure only (DB, Redis, Monitoring)
docker compose --profile infra up -d

# Start app (CLI mode - runs once then exits)
docker compose --profile app run --rm trading-agent trading-agent execution run BTC/USDT

# Start metrics server (blocking, for Prometheus scraping)
docker compose --profile app run --rm trading-agent trading-agent system serve

# Stop everything
docker compose down

# Stop + remove volumes (NUCLEAR - loses all data)
docker compose down -v
```

### 2. Health Checks
```bash
# Quick health check script
./scripts/health_check.sh

# Manual checks
curl -sf http://localhost:8000/healthz    # Trading agent metrics endpoint
curl -sf http://localhost:9090/-/healthy   # Prometheus
curl -sf http://localhost:3000/api/health  # Grafana
docker compose ps                           # Container status
```

### 3. View Logs
```bash
# All logs (follow)
docker compose logs -f

# Specific service
docker compose logs -f trading-agent --tail=100
docker compose logs -f timescaledb
docker compose logs -f prometheus

# Via CLI (structured)
trading-agent system logs -n 100 -c execution
```

### 4. Trading Operations
```bash
# Run single analysis
trading-agent agents analyze BTC/USDT

# Run execution cycle (paper trade)
trading-agent execution run BTC/USDT --stop-loss 0.05

# Check status
trading-agent execution status
trading-agent execution trades -n 20
trading-agent execution risk

# Emergency close all
trading-agent execution close --all

# Reset paper state
trading-agent execution reset
```

### 5. Data Operations
```bash
# Fetch data
trading-agent data fetch BTC/USDT --timeframe 1h --save

# Download all configured symbols
trading-agent data download-all

# Validate data quality
trading-agent data validate

# List datasets
trading-agent data list-datasets
```

### 6. Backtest
```bash
# List strategies
trading-agent backtest list

# Run backtest
trading-agent backtest run ma_crossover -s BTC/USDT -t 1h -p fast_period=10 -p slow_period=30
```

### 7. Monitoring & Metrics
```bash
# Start metrics server (port 8000)
trading-agent system serve

# View Prometheus metrics
trading-agent system metrics

# Open Grafana
# http://localhost:3000 (admin/admin)

# Open Prometheus
# http://localhost:9090
```

### 8. Daily Summary (Manual)
```bash
trading-agent system daily --send-telegram
```

---

## 📊 Dashboard Access (Local)

| Service | URL | Credentials |
|---------|-----|-------------|
| **Grafana** | http://localhost:3000 | admin / admin (or `GRAFANA_PASSWORD` from .env.local) |
| **Prometheus** | http://localhost:9090 | — |
| **Streamlit Dashboard** | http://localhost:8501 | — (run `poetry run streamlit run dashboard/app.py`) |

### Grafana Dashboards Included
- **Trading Overview**: Equity, P&L, positions, trades
- **System Metrics**: CPU, memory, disk, network
- **Agent Decisions**: Signal history, confidence, agreement

---

## 🛑 Emergency Procedures

### Circuit Breaker Triggered
```bash
# Check status
trading-agent execution risk

# If drawdown > 15% or daily loss > 8%:
# 1. Review open positions
trading-agent execution status

# 2. Close all if needed
trading-agent execution close --all

# 3. Reset circuit breaker
trading-agent execution reset
```

### Agent Not Responding
```bash
# Check container
docker compose ps trading-agent

# Check logs
docker compose logs trading-agent --tail=50

# Restart
docker compose restart trading-agent
```

### Database Issues
```bash
# Check TimescaleDB
docker compose logs timescaledb --tail=20

# Restart
docker compose restart timescaledb

# Verify connection
docker compose exec timescaledb pg_isready -U trading -d trading_market_data
```

### Full Reset (Nuclear Option)
```bash
# ⚠️ DELETES ALL DATA: trades, positions, equity history
docker compose down -v
docker compose --profile infra up -d
# Re-initialize by running first trade
trading-agent execution run BTC/USDT
```

---

## 💾 Backup & Retention (Local)

### Automated (via cron)
```bash
# Crontab entries (run `crontab -e`):
# 0 23 * * * /home/user/trading/scripts/cron_wrapper.sh backup   # Daily backup 23:00
# 0 8 * * * /home/user/trading/scripts/cron_wrapper.sh daily    # Daily summary 08:00
# 0 2 * * 0 /home/user/trading/scripts/cron_wrapper.sh retention # Weekly cleanup Sunday 02:00
```

### Manual Backup
```bash
# Create timestamped backup
python scripts/backup_local.py

# List backups
ls -la backups/

# Backup location: backups/YYYYMMDD_HHMMSS/
# Contains: trading.db, equity_snapshots, trades, agent_decisions
```

### Restore
```bash
# List available backups
ls -la backups/

# Restore specific backup
python scripts/restore.sh --backup backups/20260729_152621

# Or manually:
cp backups/20260729_152621/trading.db data/trading.db
```

### Retention Policy (Auto)
- **Logs**: > 14 days → deleted
- **Backups**: > 30 days → deleted
- **Equity snapshots**: > 90 days → deleted (keep daily aggregates)

---

## 🔐 Secrets Management (Local)

### .env.local (gitignored)
```bash
# Create from template
cp .env.example .env.local

# Edit with real values
nano .env.local
```

### Required Secrets
| Variable | Source | Notes |
|----------|--------|-------|
| `OPENROUTER_API_KEY` | https://openrouter.ai | Primary LLM |
| `DEEPSEEK_API_KEY` | https://platform.deepseek.com | Fallback LLM |
| `NVIDIA_NIM_API_KEY` | https://build.nvidia.com | Fallback LLM (free) |
| `TELEGRAM_BOT_TOKEN` | @BotFather | For alerts |
| `TELEGRAM_CHAT_ID` | @userinfobot | Your chat ID |
| `TSDB_PASSWORD` | Auto-generated | TimescaleDB password |

### API Key Rotation (Quarterly)
```bash
# 1. Generate new key on provider dashboard
# 2. Update .env.local
nano .env.local

# 3. Restart containers to pick up new env
docker compose restart trading-agent

# 4. Verify
trading-agent agents analyze BTC/USDT  # Should work
```

---

## 🔒 Security (Local)

### Trivy Image Scan
```bash
# Install Trivy
# https://aquasecurity.github.io/trivy/latest/getting-started/installation/

# Scan local image
trivy image --severity HIGH,CRITICAL trading-agent:local
```

### Dependency Audit
```bash
# Python deps
pip-audit

# Or via GitHub Actions (runs on every push)
# See .github/workflows/ci.yml → security-scan job
```

---

## 📈 Capacity & Scaling (Local)

### Resource Limits (docker-compose.yml)
```yaml
# In docker-compose.yml, under services.trading-agent:
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 2G
    reservations:
      cpus: '0.5'
      memory: 512M
```

### Local Scaling Triggers
- **CPU > 80%** → Reduce `trade_interval` in cron (from 2h to 4h)
- **Memory > 80%** → Reduce `initial_capital` or symbols
- **Disk > 80%** → Run `make clean` or `cron_wrapper.sh retention`

---

## 🧪 Testing & Validation

### Pre-Deployment Checklist (Local)
```bash
# 1. Lint & type check
make lint          # ruff check
make typecheck     # mypy

# 2. Unit tests
make test          # pytest

# 3. Build image
docker compose build

# 4. Health check
docker compose --profile infra up -d
./scripts/health_check.sh

# 5. Smoke test trade
trading-agent execution run BTC/USDT --stop-loss 0.05
trading-agent execution status
```

### CI/CD (GitHub Actions)
- **CI** (`.github/workflows/ci.yml`): Runs on every push/PR
  - Lint (ruff), Type check (mypy), Tests (pytest)
  - Docker build test
  - **Trivy security scan** (HIGH/CRITICAL vulns)
- **CD Production** (`.github/workflows/cd-production.yml`): Manual trigger
  - Signature verification (cosign)
  - SBOM verification (syft)
  - Blue-green deploy (requires VPS - skipped for local-first)

---

## 📞 Local Support

| Issue | Where to Look |
|-------|---------------|
| Agent logic bugs | `src/trading_agent/agents/` |
| Execution bugs | `src/trading_agent/execution/` |
| Data issues | `src/trading_agent/data/` |
| Config issues | `config/config.yaml`, `.env.local` |
| Logs | `docker compose logs` or `logs/trading_agent.log` |
| Metrics | `http://localhost:8000/metrics` |

---

## 📝 Post-Incident Template (Local)

```markdown
# Incident: [Title] - YYYY-MM-DD

**Time**: HH:MM - HH:MM (local)
**Severity**: SEV-1/2/3
**Status**: Resolved / Monitoring

## What Happened
Brief description.

## Root Cause
Technical explanation.

## Impact
- Trades missed: X
- P&L impact: $X
- Data lost: Yes/No

## Resolution
Steps taken to fix.

## Prevention
- [ ] Fix: Description
- [ ] Monitoring: Add alert for X
- [ ] Process: Update runbook section Y
```

---

*Last updated: 2026-07-30*
*For production deployment, see `docs/RUNBOOK.md` (original)*