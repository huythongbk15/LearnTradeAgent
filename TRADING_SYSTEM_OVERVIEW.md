# Trading Agent System — Tổng Quan Kiến Trúc & Lộ Trình

> Tài liệu này map toàn bộ hệ thống trading agent chuyên nghiệp, phân tích các dự án GitHub nổi bật,
> và đề xuất cách kết hợp chúng theo từng phase phát triển.

---

## 🧭 Tầm Nhìn

Xây dựng một **hệ thống multi-agent trading tự động** có khả năng:

- **Phân tích đa chiều** (kỹ thuật, cơ bản, sentiment, vĩ mô)
- **Ra quyết định thông minh** dựa trên LLM + các mô hình truyền thống
- **Quản lý rủi ro** tự động
- **Backtest & paper-trade** trước khi live
- **Kết nối đa sàn, đa tài sản** (chứng khoán + crypto)
- **Tự động hóa** execution, rebalance, logging

---

## 📦 Tổng Hợp Dự Án GitHub Quan Trọng

### Nhóm 1: AI / LLM Multi-Agent Trading (MỚI — Xu hướng 2025-2026)

| Dự án | Stars | Ngôn ngữ | Chức năng chính | Ưu điểm | Nhược điểm |
|-------|-------|----------|-----------------|---------|------------|
| **[TradingAgents](https://github.com/tauricresearch/tradingagents)** | ~9.3K | Python | Multi-agent LLM trading với 7 role: fundamental, sentiment, technical analysts, trader, risk manager | Kiến trúc trading firm thực tế; hỗ trợ nhiều LLM provider; có paper NeurIPS; debate giữa các agent | Mới, còn đang phát triển; chưa có backtesting tích hợp sẵn |
| **[ai-hedge-fund](https://github.com/virattt/ai-hedge-fund)** | ~49.6K | Python | 18 agent mang phong cách các trader huyền thoại, phân tích đa chiều | Cộng đồng lớn nhất; hỗ trợ Ollama; kiến trúc agent chuyên biệt hóa rõ | Chỉ là proof-of-concept; không production-ready; không có backtest engine |
| **[AgenticTrading](https://github.com/Open-Finance-Lab/AgenticTrading)** | Mới | Python | Playground cho LLM-powered trading agents, có giám sát decision logs | Có paper NeurIPS 2025; hỗ trợ backtest + paper-trading | Dạng experimental; cộng đồng nhỏ |

### Nhóm 2: Production Trading Bots (CHÍN MUỒI — Crypto)

| Dự án | Stars | Ngôn ngữ | Chức năng chính | Ưu điểm | Nhược điểm |
|-------|-------|----------|-----------------|---------|------------|
| **[Freqtrade](https://github.com/freqtrade/freqtrade)** | ~48K | Python | Trading bot hoàn chỉnh, backtest, hyperopt, FreqAI cho ML | Cộng đồng lớn nhất; FreqAI cho adaptive ML; tài liệu phong phú; 8+ sàn futures | Chỉ crypto; cần code Python; không có GUI mặc định |
| **[Hummingbot](https://github.com/hummingbot/hummingbot)** | ~8K | Python | Market making chuyên sâu, hỗ trợ CEX + DEX | Chiến lược market making tốt nhất open source; hỗ trợ DEX; cross-exchange arbitrage | Tập trung vào market making; setup phức tạp |
| **[OctoBot](https://github.com/Drakkar-Software/OctoBot)** | ~4K | Python | GUI-first, có marketplace strategy miễn phí | Có web UI; không cần code; hỗ trợ TradingView + AI; dễ deploy Docker | Cộng đồng nhỏ hơn Freqtrade; ít exchange connectors |

### Nhóm 3: Algo Trading Platform (ĐA NĂNG)

| Dự án | Stars | Ngôn ngữ | Chức năng chính | Ưu điểm | Nhược điểm |
|-------|-------|----------|-----------------|---------|------------|
| **[OpenAlgo](https://github.com/marketcalls/openalgo)** | ~13K | Python/Flask/React | Nền tảng self-hosted, 34+ broker plugins (chứng khoán Ấn Độ) | Production-ready; có MCP cho AI agents; audit trail; drag-drop strategy; sandbox execution | Chỉ tập trung thị trường Ấn Độ; không hỗ trợ quốc tế |
| **[NautilusTrader](https://github.com/nautechsystems/nautilus_trader)** | ~5K | Rust + Python | High-performance, event-driven, backtest + live unified code | Rust core (ms latency); backtest chính xác nhất open source; hỗ trợ đa tài sản | Setup phức tạp ban đầu; learning curve dốc |

### Nhóm 4: Backtesting Engines

| Dự án | Stars | Phương pháp | Ưu điểm | Nhược điểm |
|-------|-------|-------------|---------|------------|
| **VectorBT** | ~5K | Vectorized (NumPy+Numba) | Cực nhanh (167x Backtrader); parameter sweeps; portfolio analytics | Không live trading; cần rewrite code khi sang production |
| **Backtrader** | ~14K | Event-driven | Dễ dùng; cộng đồng lớn; nhiều broker integration | Chậm; không phù hợp production quy mô lớn |
| **NautilusTrader** | ~5K | Event-driven (Rust) | Backtest sát production nhất; fill modeling; slippage | Phức tạp hơn |

### Nhóm 5: Infrastructure / Data / Execution

| Dự án | Chức năng | Ghi chú |
|-------|-----------|---------|
| **[CCXT](https://github.com/ccxt/ccxt)** | Unified API cho 100+ crypto exchanges | PHẢI CÓ cho crypto; REST + WebSocket; đa ngôn ngữ |
| **Alpaca API** | Stock trading API (Mỹ) | Tốt cho chứng khoán Mỹ; có paper trading |
| **Polygon.io** | Market data aggregator | Real-time + historical data |
| **Alpha Vantage** | Free market data API | Giới hạn request; tốt cho nghiên cứu |

---

## 🏗️ Kiến Trúc Đề Xuất — Kết Hợp Các Dự Án

```
┌─────────────────────────────────────────────────────────────┐
│                    AI AGENT LAYER                           │
│  ┌─────────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐  │
│  │ Technical   │ │Sentiment │ │Fundamental│ │ Macro     │  │
│  │ Analyst     │ │Analyst   │ │Analyst   │ │ Analyst   │  │
│  └──────┬──────┘ └────┬─────┘ └────┬─────┘ └─────┬─────┘  │
│         └─────────────┼────────────┼──────────────┘         │
│                   ┌───┴────┐  ┌────┴───┐                    │
│                   │Trader  │  │Risk   │                    │
│                   │ Agent  │  │Manager│                    │
│                   └───┬────┘  └────┬───┘                    │
│              (Inspired by TradingAgents + ai-hedge-fund)    │
└──────────────────────────┼──────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────────┐
│                   DECISION LAYER                            │
│        ┌─────────────────┴────────────────┐                │
│        │   Portfolio Manager / Orchestrator│                │
│        │   (Weight signals, risk check,    │                │
│        │    decide: BUY/SELL/HOLD, sizing) │                │
│        └─────────────────┬────────────────┘                │
└──────────────────────────┼──────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────────┐
│               EXECUTION & DATA LAYER                        │
│  ┌─────────────────┐  ┌──┴──────────┐  ┌────────────────┐  │
│  │ Backtest Engine │  │ Live Trading│  │ Market Data    │  │
│  │ (NautilusTrader │  │ (CCXT/      │  │ Pipeline       │  │
│  │  + VectorBT)    │  │  Alpaca/    │  │ (Polygon/      │  │
│  │                 │  │  OpenAlgo)  │  │  AlphaVantage) │  │
│  └─────────────────┘  └─────────────┘  └────────────────┘  │
│                         ┌──────────────┐                     │
│                         │ Database     │                     │
│                         │ (TimescaleDB │                     │
│                         │  + SQLite    │                     │
│                         │  + Redis)    │                     │
│                         └──────────────┘                     │
└──────────────────────────────────────────────────────────────┘
```

---

## 🗺️ Lộ Trình Phát Triển Theo Phase

### Phase 0: Nền Tảng & Data Pipeline ✅ **HOÀN THÀNH**
**Mục tiêu:** Có data pipeline hoạt động, có môi trường research

| Module | Công nghệ | Status |
|--------|-----------|--------|
| Market Data Collector | CCXT + Polars | ✅ |
| Data Storage | Parquet files (append + dedup) | ✅ |
| Data Validation | Gap/outlier/null detection | ✅ |
| CLI | Click + Rich | ✅ |
| Incremental Update | Chỉ fetch candles mới | ✅ |

**Kết quả:** 5 symbols × 4 timeframes = 20 datasets, 696K candles, 0 gaps.

---

### Phase 1: Chiến Lược & Backtest Engine ✅ **HOÀN THÀNH**
**Mục tiêu:** Có thể backtest chiến lược, đánh giá hiệu suất

| Module | Công nghệ | Status |
|--------|-----------|--------|
| Strategy Library | MA Crossover, RSI, BBands, MACD | ✅ |
| Backtest Engine | Vectorized (Polars) | ✅ |
| Parameter Sweep | Grid search | ✅ |
| Walk-Forward | 2y train / 1y test | ✅ |
| Performance Metrics | Sharpe, Return, DD, Win Rate | ✅ |

**Kết quả:** MA Crossover optimized +71.96% return (từ +10.73% default).

---

### Phase 2: AI Agent Layer (Multi-Agent System) ✅ **HOÀN THÀNH**
**Mục tiêu:** Hệ thống agent thông minh với LLM phân tích và ra quyết định

| Agent | Chức năng | Status |
|-------|-----------|--------|
| **Technical Analyst** | Phân tích chart, indicators, patterns | ✅ |
| **Sentiment Analyst** | RSI extremes, volume insight | ✅ |
| **Risk Manager** | Volatility, position sizing | ✅ |
| **Trader Agent** | Weighted voting + decision | ✅ |

**Công nghệ:**
- LLM: DeepSeek V4 Flash (OpenRouter free) primary → GPT-4o-mini fallback → Ollama local
- Cost: ~$0.00009 / analysis cycle (4 agents)
- Orchestrator: Custom Python (không LangGraph dependency)

---

### Phase 3: Execution & Risk Management ✅ **HOÀN THÀNH**
**Mục tiêu:** Paper trading an toàn với kiểm soát rủi ro chặt chẽ

| Module | Công nghệ | Status |
|--------|-----------|--------|
| Paper Exchange | Simulated market/limit/stop orders | ✅ |
| Position Tracker | P&L, equity curve | ✅ |
| Risk Controller | Max DD 15%, daily loss 8% | ✅ |
| Circuit Breaker | Tự động close all + cooldown | ✅ |
| CLI Integration | 6 execution commands | ✅ |

**Kết quả:** Full trade cycle tested (BUY → price up → SELL: +3.35%), stop-loss trigger confirmed.

---

### Phase 4: Monitoring, Logging & Optimization ✅ **HOÀN THÀNH**
**Mục tiêu:** Dashboard real-time, logging đầy đủ, alerting, metrics

| Module | Công nghệ | Status |
|--------|-----------|--------|
| Structured Logging | loguru + rotating file + WAL | ✅ |
| Trade Database | SQLite (trades, equity, agent_decisions) | ✅ |
| Performance Tracker | Sharpe, Sortino, DD, Win Rate, rolling | ✅ |
| Streamlit Dashboard | Overview, Trades, Agents, Risk tabs | ✅ |
| Alerting (Telegram) | Trade/risk/daily alerts | ✅ |

**Kết quả:** Dashboard 4 tabs hiển thị P&L, equity curve, trade history, agent decisions, risk metrics.

---

### Phase 5: Production Hardening & Operations ✅ **HOÀN THÀNH**
**Mục tiêu:** Production-ready: CI/CD, IaC, HA, backup/restore, security, observability stack

| Module | Công nghệ |
|--------|-----------|
| Container | Multi-stage Dockerfile, docker-compose.prod.yml |
| CI/CD | GitHub Actions (lint, test, build, scan, deploy) |
| IaC | Terraform/Ansible cho VPS provisioning |
| HA Database | TimescaleDB + Patroni (3-node) |
| HA Redis | Redis Sentinel (3-node) |
| HA App | 3 replicas + leader election (Redis lock) |
| Backup/DR | WAL-G to S3, PITR, monthly restore drill |
| Security | Trivy scan, distroless, network policy, Vault |
| Observability | Prometheus + Grafana + Loki + Tempo |
| Runbook | Incident response, on-call, postmortem template |

---

### Phase 6: Scale & Multi-Asset ✅ **HOÀN THÀNH**
**Mục tiêu:** Multi-exchange, multi-asset class, portfolio manager, strategy marketplace

| Module | Công nghệ |
|--------|-----------|
| Multi-Exchange | CCXT unified adapter (Binance, Bybit, OKX, Coinbase) |
| Multi-Asset | Crypto + Stocks (Alpaca) + Forex (OANDA) + DEX + Futures/Options |
| Real-Time Data | WebSocket Manager (ticker/orderbook/trades, auto-reconnect, heartbeat) + Health Monitor (latency, error rate, auto-failover) |
| Unified Data Pipeline | Ingest multi-asset OHLCV → SQLite/TimescaleDB hypertable, backfill + incremental |
| Portfolio Manager | Risk budgeting, correlation monitoring, rebalancing, Black-Litterman optimizer |
| Strategy Marketplace | Plugin architecture, sandboxed execution |
| Advanced ML | Online learning, regime detection, adaptive sizing, meta-learning |
| Reliability | Multi-region sync, chaos engineering, event sourcing, distributed tracing |

---

## 🔄 Ma Trận Kết Hợp Dự Án Tối Ưu (Production-Ready)

| Layer | Dự án chính | Vai trò |
|-------|-------------|---------|
| **AI Multi-Agent** | TradingAgents + ai-hedge-fund | Xương sống của agent layer |
| **Backtest** | NautilusTrader + VectorBT | VectorBT cho research nhanh, Nautilus cho production |
| **Execution (Crypto)** | CCXT + Freqtrade (lấy strategy engine) | CCXT cho kết nối sàn, Freqtrade cho strategy management |
| **Execution (Stock)** | OpenAlgo hoặc Alpaca API | Tùy thị trường mục tiêu |
| **Data** | CCXT + Polygon.io + TimescaleDB | Pipeline data thống nhất |
| **LLM** | Claude/GPT/DeepSeek + Ollama (local) | Qua TradingAgents hoặc tự orchestrate |
| **Dashboard** | Grafana + Streamlit + Telegram Bot | Monitoring và alert |
| **Infra/Deploy** | Docker + GitHub Actions + Terraform | CI/CD, IaC, container orchestration |
| **Observability** | Prometheus + Grafana + Loki + Tempo | Metrics, logs, traces |
| **Security** | Trivy + Vault + Network Policy | Scan, secrets, zero-trust network |

---

## ✅ Kết Luận & Khuyến Nghị Phase 5-6

**Phase 0-6 ĐÃ HOÀN THÀNH** — Hệ thống có đầy đủ: data pipeline, backtest, AI agents, paper execution,
monitoring dashboard, production hardening (CI/CD, container, observability, backup), multi-asset
(CCXT crypto + Alpaca stocks + OANDA forex + DEX + futures/options), portfolio manager
(risk budgeting, rebalancing, Black-Litterman optimizer), real-time data (WebSocket Manager +
Health Monitor + auto-failover), strategy marketplace, và reliability (multi-region, chaos engineering,
event sourcing, meta-learning).

**Phase 5 — Production Hardening (ĐÃ HOÀN THÀNH):**

| Priority | Tasks | Timeline |
|----------|-------|----------|
| **P0** | Multi-stage Dockerfile, docker-compose.prod.yml, health checks | Tuần 1 |
| **P0** | GitHub Actions: lint→test→build→scan→deploy staging | Tuần 1 |
| **P0** | Terraform/Ansible cho VPS (DigitalOcean/Hetzner), DNS, TLS | Tuần 1-2 |
| **P1** | Prometheus + Grafana + Loki stack (metrics, logs, dashboards) | Tuần 2 |
| **P1** | TimescaleDB + Patroni (3-node), Redis Sentinel (3-node) | Tuần 2-3 |
| **P1** | Leader election cho scheduler, graceful shutdown | Tuần 2 |
| **P2** | WAL-G backup to S3, PITR test, restore drill | Tuần 3 |
| **P2** | Trivy scan, distroless base, network policies, Vault secrets | Tuần 3 |
| **P2** | Runbook, on-call rotation, incident template | Tuần 3 |

**Bước tiếp theo (go-live):** nối real-time data (WS Manager + Health Monitor) vào order router,
chạy cron `data-pipeline` backfill lên TimescaleDB, thực hiện dry-run trước rồi mới paper trade
trên nhiều asset class.

**Công cụ quản lý dự án (updated):**
- Python 3.12+, Poetry
- Git + GitHub (Actions, Environments, Secrets)
- Docker + Docker Compose (dev/staging/prod overrides)
- Terraform/Ansible (infra as code)
- Makefile + Taskfile (automation)
- Trivy (security scan), Hadolint (Dockerfile lint)

---

> ⚠️ **Disclaimer:** Tất cả các dự án trên đều là open-source. Tuy nhiên, khi sử dụng cho mục đích trading thật,
> cần có trách nhiệm với vốn của mình. Luôn backtest kỹ và bắt đầu với paper trading trước.
