# LIVE TRADING — Small Capital Test Guide

## 🎯 Mục Tiêu
Test live trading với vốn nhỏ ($100-500) trước khi scale. Validate: execution, slippage, fees, risk controls, monitoring trong môi trường thực.

---

## 1. CHUẨN BỊ (Prerequisites)

### Broker Accounts (chọn 1)
| Broker | Asset Class | Min Deposit | Paper Trading | Notes |
|--------|-------------|-------------|---------------|-------|
| **Alpaca** | US Stocks/ETF | $0 | ✅ Full | API free, paper mode sẵn |
| **OANDA** | Forex/CFD | $1 | ✅ Full | Spread thấp, API v20 |
| **Binance Spot** | Crypto | $10 | ❌ (testnet only) | Testnet: testnet.binance.vision |
| **Bybit** | Crypto/Derivatives | $1 | ✅ Testnet | Unified testnet |

### Khuyến Nghị Cho Test Đầu Tiên
> **Alpaca Paper Trading** → An toàn nhất, $0 risk, full API parity với live

### Setup API Keys
```bash
# Alpaca Paper (miễn phí, đăng ký tại alpaca.markets)
export ALPACA_API_KEY="PKxxxxxxxxxxxx"
export ALPACA_API_SECRET="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"  # Paper

# OANDA (nếu dùng forex)
export OANDA_API_KEY="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
export OANDA_ACCOUNT_ID="101-xxxxxxxxxx-xxxxxxx"
export OANDA_BASE_URL="https://api-fxpractice.oanda.com"  # Practice

# Binance Testnet (crypto)
export BINANCE_API_KEY="xxxxxxxxxxxx"
export BINANCE_API_SECRET="xxxxxxxxxxxx"
export BINANCE_TESTNET=true
```

---

## 2. CẤU HÌNH RISK LIMITS CHO VỐN NHỎ

### Config Override (không sửa config.yaml gốc)
Tạo file `config/live_test.yaml`:
```yaml
# Live test config - override from CLI
execution:
  initial_capital: 500.0           # $500 test capital
  max_position_pct: 0.10           # 10% max per position = $50
  max_portfolio_heat: 0.50         # 50% max deployed = $250
  default_stop_loss_pct: 0.02      # 2% stop-loss (tight)
  default_take_profit_pct: 0.04    # 4% take-profit (2:1 R:R)
  cooldown_hours: 4                # 4h cooldown sau SL (ngắn hơn default 24h)

risk:
  max_drawdown_pct: 0.10           # 10% max DD → kill ($50 loss)
  daily_loss_limit_pct: 0.05       # 5% daily loss limit ($25/day)

# Symbols cho test (chỉ 1-2 symbols liquid)
symbols:
  alpaca:
    - "SPY"      # ETF S&P 500 - spread thấp, volume cao
    - "QQQ"      # ETF Nasdaq 100

# Chỉ dùng 1 timeframe
data:
  timeframes:
    - "5m"       # 5m cho test nhanh
```

---

## 3. QUY TRÌNH TEST (Step-by-Step)

### Phase 1: Connection Test (5 phút)
```bash
# Test Alpaca paper connection
python -m trading_agent.cli live connect --broker alpaca --paper \
  --api-key $ALPACA_API_KEY --api-secret $ALPACA_API_SECRET \
  --base-url https://paper-api.alpaca.markets

# Expected: "✅ Connected to Alpaca (paper) — Account: $100,000 paper equity"
```

### Phase 2: Dry Run (30 phút - 2h)
```bash
# Chạy regime-switching strategy DRY RUN (không execute thật)
python -m trading_agent.cli live run SPY \
  --broker alpaca \
  --strategy regime_switching \
  --timeframe 5m \
  --capital 500 \
  --interval 60 \
  --iterations 120 \
  --dry-run

# Monitor output:
# - Signal generation: BUY/HOLD/SELL
# - Position sizing calculation
# - Risk checks (should all pass)
# - No actual orders placed
```

### Phase 3: Micro Live Test ($10-50 position)
```bash
# Thực sự execute với position nhỏ
python -m trading_agent.cli live run SPY \
  --broker alpaca \
  --strategy regime_switching \
  --timeframe 5m \
  --capital 500 \
  --interval 60 \
  --iterations 50 \
  --execute \
  --stop-loss 0.015

# Trong lúc chạy, monitor ở terminal khác:
python -m trading_agent.cli live monitor --broker alpaca --interval 30
```

### Phase 4: Full Session (1-2 ngày)
```bash
# Chạy liên tục trong giờ giao dịch (9:30-16:00 ET)
python -m trading_agent.cli live run SPY \
  --broker alpaca \
  --strategy regime_switching \
  --timeframe 5m \
  --capital 500 \
  --interval 300 \
  --execute
```

---

## 4. MONITORING TRONG TEST

### Dashboard (Streamlit)
```bash
# Terminal 1: Streamlit dashboard
python -m trading_agent.cli data download-all  # nếu cần data
poetry run streamlit run dashboard/app.py

# Mở http://localhost:8501
# Tab Overview: Equity, P&L real-time
# Tab Risk: Circuit breaker, drawdown gauges
```

### CLI Monitoring
```bash
# Terminal 2: Watch positions/P&L
watch -n 10 "python -m trading_agent.cli live positions --broker alpaca"

# Terminal 3: Check risk status
watch -n 30 "python -m trading_agent.cli execution risk"

# Terminal 4: Logs
tail -f logs/trading_agent.log | grep -E "BUY|SELL|STOP|RISK|CIRCUIT"
```

### Telegram Alerts (Optional)
```bash
export TELEGRAM_BOT_TOKEN="xxxx:xxxx"
export TELEGRAM_CHAT_ID="123456789"
# Config: alerts.telegram.enabled: true
```

---

## 5. GO/NO-GO CRITERIA ĐỂ SCALE

| Metric | Threshold (Pass) | Threshold (Fail) |
|--------|------------------|------------------|
| **Order Fill Rate** | ≥ 95% | < 90% |
| **Avg Slippage** | ≤ 2 bps (0.02%) | > 5 bps |
| **Avg Commission** | As expected (Alpaca: $0) | > 2x expected |
| **Circuit Breaker** | Never triggered in test | Triggered > 1x |
| **Max Drawdown** | < 5% of test capital | > 10% |
| **Daily P&L Volatility** | Stable, no fat tails | Erratic |
| **Strategy Signals** | Reasonable frequency (5-20/day) | Too many (>50) or too few (<2) |
| **Dashboard/Alerts** | All working | Any broken |

### Decision Matrix
```
✅ ALL PASS → Scale to $2,000-5,000 (same config, larger capital)
⚠️  1-2 MINOR ISSUES → Fix, re-test 1 day
❌ ANY CRITICAL FAIL → Debug, don't scale
```

---

## 6. COMMON ISSUES & FIXES

| Issue | Symptom | Fix |
|-------|---------|-----|
| **API Rate Limit** | 429 errors, slow fills | Reduce interval, add backoff |
| **Timezone Mismatch** | Signals at wrong hours | Ensure UTC throughout, market hours filter |
| **Insufficient Buying Power** | Order rejected | Check margin, reduce position size |
| **Stale Data** | Signals on old prices | Verify data pipeline latency < 1s |
| **Circuit Breaker False Positive** | Kills during normal vol | Widen DD limit to 15-20% for test |

---

## 7. CHECKLIST TRƯỚC KHI CHẠY LIVE

- [ ] API keys set in env (không hardcode)
- [ ] Paper trading mode verified first
- [ ] Config override: capital=$500, max_pos=10%, DD=10%
- [ ] Single symbol (SPY) or max 2 symbols
- [ ] Stop-loss 1.5-2%, Take-profit 3-4%
- [ ] Streamlit dashboard running
- [ ] Telegram alerts tested (optional)
- [ ] Logs directory writable
- [ ] Emergency close command ready: `trading-agent live close --all --broker alpaca`
- [ ] Market hours check: only run 9:30-16:00 ET

---

## 8. EMERGENCY PROCEDURES

```bash
# 1. Close ALL positions immediately
python -m trading_agent.cli live close --all --broker alpaca --execute

# 2. Cancel all open orders
python -m trading_agent.cli live orders --broker alpaca --status open --cancel-all

# 3. Check account status
python -m trading_agent.cli live balance --broker alpaca

# 4. Disable circuit breaker if false trigger
python -m trading_agent.cli execution risk --reset-circuit-breaker
```

---

## 9. SCALE UP PLAN (Sau khi PASS)

| Stage | Capital | Max Pos | Symbols | Timeframe |
|-------|---------|---------|---------|-----------|
| **Test** | $500 | 10% ($50) | 1 (SPY) | 5m |
| **Stage 1** | $2,000 | 10% ($200) | 2 (SPY, QQQ) | 5m |
| **Stage 2** | $10,000 | 15% ($1,500) | 4-5 ETFs | 5m/15m |
| **Stage 3** | $50,000 | 20% ($10,000) | 8-10 multi-asset | Multi-TF |

Mỗi stage: run 1-2 tuần, verify metrics, then scale 5x.

---

**TL;DR**: Bắt đầu `Alpaca paper` → `dry-run` → `$500 live` SPY 5m regime-switching. Monitor dashboard + CLI. Pass criteria: fill rate >95%, slippage <2bps, no circuit breaker, DD <5%. Then scale 5x per stage.