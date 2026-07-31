# 🤖 Trading Agent System

**Multi-Agent AI Trading Platform** — kết hợp LLM agents với chiến lược giao dịch systematic, multi-exchange, multi-asset (Crypto + Stocks + Forex + Futures/Options).

[![CI](https://github.com/huythongbk15/LearnTradeAgent/actions/workflows/ci.yml/badge.svg)](https://github.com/huythongbk15/LearnTradeAgent/actions/workflows/ci.yml)

---

## 🏗 Kiến trúc 3 tầng

```
┌─ PHASE 0-3 · CORE LOOP ────────────────────────────────────────┐
│  data/pipeline ──→ backtest/engine ──→ 4 LLM agents ──→ execution│
│  (fetch·validate)   (vectorized)      (weighted voting)  (paper)│
└────────────────────────────────────────────────────────────────┘
┌─ PHASE 4-5 · OPS ──────────────────────────────────────────────┐
│  logging · SQLite · metrics · Streamlit dashboard · Telegram   │
│  Docker · CI/CD · Trivy scan · backup/restore · runbook        │
└────────────────────────────────────────────────────────────────┘
┌─ PHASE 6 · SCALE ──────────────────────────────────────────────┐
│  8 CEX + DEX + Alpaca + OANDA · order router · portfolio       │
│  strategy marketplace · adaptive ML · event sourcing · chaos   │
└────────────────────────────────────────────────────────────────┘
```

## ✅ Trạng thái 7 phase — HOÀN THÀNH 100%

| Phase | Module | Điểm nổi bật |
|-------|--------|--------------|
| **0** | Data Pipeline | 5 symbols × 4 TFs, 696K candles, 0 gaps, incremental update |
| **1** | Backtest Engine | 4 strategies, parameter sweep +71.96%, walk-forward, OOS |
| **2** | AI Agents | Technical · Sentiment · Risk · Trader, DeepSeek V4 Flash ($0), fallback chain |
| **3** | Execution & Risk | Paper exchange, risk controller, circuit breaker, kill switch |
| **4** | Monitoring & Ops | Logging, SQLite, metrics engine, Streamlit dashboard, Telegram alerts |
| **5** | Production | Docker 24/7, CI/CD xanh, Trivy scan, backup/restore, runbook |
| **6** | Scale & Multi-Asset | 8 sàn + DEX + stocks + forex, portfolio optimizer, plugin marketplace, meta-learning |

> 📊 Kiến trúc đầy đủ: [`TRADING_SYSTEM_OVERVIEW.md`](TRADING_SYSTEM_OVERVIEW.md) · Index tài liệu: [`docs/README.md`](docs/README.md)

## 🔬 Chi tiết từng phase

### Phase 0 — Data Pipeline ✅
**Mục tiêu:** Xây nền tảng dữ liệu sạch cho toàn bộ hệ thống.
- Fetch **5 symbols × 4 timeframes** (1h/4h/1d...) qua CCXT → Polars → Parquet
- **696K candles, 0 gaps** — data validation, gap detection, duplicate check
- **Incremental update**: chỉ tải dữ liệu mới → tiết kiệm **95-99% bandwidth**
- Data sufficiency analysis: xác định đủ dữ liệu cho backtest/ML

### Phase 1 — Backtest Engine ✅
**Mục tiêu:** Đo hiệu quả chiến lược trước khi đưa tiền thật.
- 4 chiến lược: **MA Crossover, RSI, Bollinger Bands, MACD** (vectorized Polars)
- **Parameter sweep**: default +10.73% → optimized **+71.96%**
- **Walk-forward + Out-of-Sample + stability analysis** — phát hiện overfitting, chọn bộ tham số bền
- Metrics: Sharpe, Return, Win Rate, Max Drawdown

### Phase 2 — AI Agents ✅
**Mục tiêu:** Kết hợp LLM với phân tích kỹ thuật truyền thống.
- 4 agent chuyên biệt: **Technical · Sentiment · Risk · Trader** + weighted voting
- **Multi-timeframe** (1h/4h/1d) — 28 tests pass
- **LLM fallback chain**: OpenCode (deepseek-v4-flash-free, **$0**) → OpenAI → DeepSeek → Ollama; `USE_LLM=false` khi offline
- Chi phí: $0.002 → **$0/cycle** · PortfolioManager + AgentStrategy backtest integration

### Phase 3 — Execution & Risk ✅
**Mục tiêu:** Đặt lệnh an toàn, có kiểm soát rủi ro.
- **Paper exchange** (mô phỏng khớp lệnh, fee, slippage) + execution engine
- **Risk controller**: stop-loss, position sizing · **circuit breaker** · **kill switch**
- CLI 6 lệnh (`execution run/status/trades/risk...`), lazy import → startup **0.22s**
- Demo full cycle: **BUY → SELL +3.35%** trên BTC

### Phase 4 — Monitoring & Ops ✅
**Mục tiêu:** Nhìn thấy hệ thống đang làm gì.
- Structured logging + **SQLite DB** (trades, PnL, risk events)
- **Metrics engine** + **Streamlit dashboard** (equity curve, drawdown, phân bổ)
- **Telegram alerts** (lệnh, stop-loss, lỗi) — 7 tài liệu docs cập nhật

### Phase 5 — Production Hardening ✅
**Mục tiêu:** Chạy 24/7 như production thật.
- **Docker** multi-stage, docker-compose prod · **CI/CD GitHub Actions** (lint, test, build, **Trivy security scan**)
- Monitoring: Prometheus + Grafana + Loki · **backup/restore** (WAL-G pattern) + runbook
- Graceful shutdown, LLM caching (TTL), multi-symbol execution CLI
- **CI/CD xanh hoàn toàn** — kể cả Telegram notify (secrets chuẩn)

### Phase 6 — Scale & Multi-Asset ✅
**Mục tiêu:** Mở rộng đa sàn, đa tài sản, đa chiến lược. *(chi tiết: [docs/phase6-scale.md](docs/phase6-scale.md))*
- **Multi-exchange**: CCXT 8 CEX + **DEX** (Uniswap V3/Jupiter/PancakeSwap) + **Alpaca** (stocks) + **OANDA** (forex) + Futures/Options
- **Order router** (Best Price/TWAP/VWAP/Split) · WebSocket Manager · Health Monitor failover
- **Portfolio**: risk budgeting, correlation monitor, auto-rebalancer, Black-Litterman optimizer, Kelly allocation, attribution
- **Strategy marketplace**: plugin (pluggy), registry, sandbox, versioning, backtest validation
- **Adaptive ML**: regime detection (HMM/GMM), online learning (River), meta-learning (MAML/Reptile)
- **Infra**: event sourcing, NATS/Redis messaging, OpenTelemetry tracing, K8s multi-region, chaos engineering
- 52 integration tests · 81 tests tổng · benchmarks + load tests

## 🚀 Bắt đầu nhanh (10 lệnh)

```bash
# 1. Cài đặt
poetry install

# 2. Fetch dữ liệu Bitcoin
poetry run trading-agent data fetch BTC/USDT --since 2026-07-01

# 3. Kiểm tra dữ liệu
poetry run trading-agent data inspect BTC/USDT

# 4. Chạy backtest
poetry run trading-agent backtest run ma_crossover BTC/USDT

# 5. Phân tích multi-agent
poetry run trading-agent agents analyze BTC/USDT

# 6. Xem portfolio
poetry run trading-agent execution status

# 7. Full cycle: phân tích → trade
poetry run trading-agent execution run BTC/USDT

# 8. Xem trade history
poetry run trading-agent execution trades

# 9. Xem risk status
poetry run trading-agent execution risk

# 10. System info
poetry run trading-agent info
```

## 📚 Tài liệu & Khóa học

| Đâu | Mô tả |
|-----|-------|
| [`docs/README.md`](docs/README.md) | 🧭 Index toàn bộ tài liệu kỹ thuật |
| [`TRADING_SYSTEM_OVERVIEW.md`](TRADING_SYSTEM_OVERVIEW.md) | 🏛 Tổng quan kiến trúc & lộ trình 6 phase |
| [`COURSE/`](COURSE/) | 🎓 Khóa học 10 bài bóc tách hệ thống (deep-dive + demo) |
| [`docs/architecture.md`](docs/architecture.md) | 🏗 Kiến trúc chi tiết |
| [`docs/demo.md`](docs/demo.md) | 🎮 Demo từng bước A→Z |
| [`docs/getting-started.md`](docs/getting-started.md) | ⚡ Quick start chi tiết |
| [`docs/optimization.md`](docs/optimization.md) | ⚡ Hồ sơ tối ưu hóa |
| [`docs/phase6-scale.md`](docs/phase6-scale.md) | 🌐 Phase 6: Scale & Multi-Asset |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) · [`RUNBOOK_LOCAL.md`](docs/RUNBOOK_LOCAL.md) | 🚑 Vận hành production & local |

## ⚡ Con số nổi bật

| Tối ưu | Chỉ số |
|--------|--------|
| CLI startup time | 4s → **0.22s** (lazy imports) |
| Parameter sweep | default +10.73% → **optimized +71.96%** |
| LLM cost | $0.002/analysis → **$0** (DeepSeek V4 Flash qua OpenCode) |
| Incremental data | 95-99% ít dữ liệu phải tải lại |
| Test suite | **81 tests pass** · CI/CD xanh |

## 🛠 Stack

| Layer | Công nghệ |
|-------|-----------|
| **CLI** | Click + Rich |
| **Data** | CCXT → Polars → Parquet, SQLite/TimescaleDB |
| **Backtest** | Custom engine vectorized (Polars) |
| **AI Agents** | DeepSeek V4 Flash (primary) → OpenAI → DeepSeek → Ollama (fallback) |
| **Exchanges** | CCXT (8 CEX) · Web3.py (DEX) · Alpaca (stocks) · OANDA (forex) |
| **Portfolio** | Risk parity · HRP · Black-Litterman · Kelly allocation |
| **Execution** | Paper exchange (simulated) |
| **Infra** | Docker · GitHub Actions · K8s (multi-region) · OpenTelemetry |

---

## ⚠️ Disclaimer

**Chỉ dành cho mục đích nghiên cứu và giáo dục.** Giao dịch tiền mã hóa tiềm ẩn rủi ro lớn. Không sử dụng số tiền bạn không thể mất. Luôn bắt đầu với paper trading trước khi giao dịch thật.

---

<div align="center">
<sub>Built with ❤️ · v1.0.0 · 2026-07-31</sub>
</div>
