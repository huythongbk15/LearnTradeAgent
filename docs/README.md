# 📘 Trading Agent System — Tài Liệu Kỹ Thuật

> Phiên bản: `v1.0.0` · Cập nhật: 2026-08-10 · **7 phase hoàn thành 100%**

---

## 🧭 Mục lục

| Tài liệu | Mô tả |
|----------|-------|
| [📘 **Tài liệu tổng hợp hệ thống**](SYSTEM_GUIDE.md) | 🆕 Tính năng, cách dùng cụ thể, phương pháp trade (các option), UI/UX, Telegram notify — **đọc đầu tiên** |
| [🌐 Web UI (FastAPI + React)](../README.md#-web-ui-fastapi--react) | 🆕 Giao diện web realtime thay CLI: `make webui-start` → localhost:8000 |
| [📊 Tổng kết dự án cuối](PROJECT_SUMMARY.md) | Tính năng, ưu/nhược, cấu trúc & vận hành tối ưu |
| [🏛 Kiến trúc hệ thống](architecture.md) | Sơ đồ tổng quan, luồng dữ liệu, các layer |
| [🧠 Quy trình suy luận & Ra quyết định](reasoning.md) | Cách agent suy luận, phối hợp và ra lệnh |
| [🤖 Phase 2: AI Agents](phase2-agents.md) | Chi tiết 4 agent, weighted voting, LLM fallback |
| [🎮 Demo hướng dẫn chạy](demo.md) | Tutorial A→Z: cài đặt → data → backtest → agents → execution |
| [📁 Cấu trúc mã nguồn](project-structure.md) | Từng module làm gì, nằm ở đâu |
| [⚡ Quick Start](getting-started.md) | Lệnh nhanh để bắt đầu |
| [⚡ Tối ưu hóa hệ thống](optimization.md) | CLI startup 4s→0.22s, parameter sweep +71%, LLM cost 22x rẻ hơn |
| [🌐 Phase 6: Scale & Multi-Asset](phase6-scale.md) | Multi-exchange, multi-asset, portfolio, marketplace, ML |
| [📊 Phase 6 P3 Report](PHASE6_P3_REPORT.md) | Integration tests (52), hardening, benchmarks |
| [📋 Phase 6 P2 Completion](phase6-p2-completion.md) | K8s multi-region, event sourcing, chaos chi tiết |
| [🚑 Runbook Production](RUNBOOK.md) | Vận hành production: incident, backup, deploy |
| [🚑 Runbook Local](RUNBOOK_LOCAL.md) | Vận hành local-first (không cần cloud) |
| [🪟 WSL Guide](wsl-guide/README.md) | Hướng dẫn môi trường WSL |

> 🎓 **Khóa học deep-dive:** [`COURSE/`](../COURSE/) — 10 bài bóc tách hệ thống (Data Model → ML + Infra)

---

## 🏗 Tổng quan hệ thống (1 phút)

### Core loop (Phase 0-3)

```
📡 DATA LAYER ──→ 🧪 BACKTEST LAYER ──→ 🤖 AI AGENT LAYER ──→ ⚡ EXECUTION LAYER
CCXT→Parquet       4 strategies         Technical·Sentiment   Paper exchange
696K candles       sweep+walk-forward   ·Risk·Trader (LLM)    Risk controller
```

### Ops & Scale (Phase 4-6)

```
PHASE 4-5: logging · SQLite · metrics · Streamlit · Telegram · Docker · CI/CD · Trivy · backup
PHASE 6:   8 CEX + DEX + Alpaca + OANDA · order router · portfolio (BL/HRP) · plugin marketplace
           adaptive ML (regime/online/meta) · event sourcing · NATS/Redis · K8s multi-region · chaos
```

---

## ✅ Status hiện tại — 7 phase HOÀN THÀNH

| Phase | Module | Status | Chi tiết |
|-------|--------|--------|----------|
| **0** | Data Pipeline | ✅ **Hoàn thành** | CCXT → Parquet, 5 symbols × 4 TFs, 696K candles |
| **1** | Backtest Engine | ✅ **Hoàn thành** | 4 strategies, parameter sweep, walk-forward, OOS |
| **2** | AI Multi-Agent | ✅ **Hoàn thành** | 4 agents, DeepSeek V4 Flash ($0), fallback chain |
| **3** | Execution & Risk | ✅ **Hoàn thành** | Paper exchange, risk controller, circuit breaker, CLI |
| **4** | Monitoring & Ops | ✅ **Hoàn thành** | Logging, SQLite, metrics, Streamlit dashboard, Telegram alerts |
| **5** | Production | ✅ **Hoàn thành** | Docker, CI/CD xanh, Trivy scan, backup/restore, runbook |
| **6** | Scale & Multi-Asset | ✅ **Hoàn thành** | Đa sàn, đa tài sản, portfolio, marketplace, ML, infra |

> 81 tests pass · CI/CD green (Lint + Test → Build + Trivy → Telegram notify)

---

## 🛠 Stack công nghệ

```
Language:    Python 3.12+
CLI:         Click + Rich
Data:        CCXT → Polars → PyArrow/Parquet · SQLite/TimescaleDB
Backtest:    Custom engine vectorized (Polars)
Agents:      DeepSeek V4 Flash (primary, $0) → OpenAI → DeepSeek → Ollama (fallback)
Execution:   Paper exchange (simulated)
Exchanges:   CCXT (8 CEX) · Web3.py (DEX) · Alpaca (stocks) · OANDA (forex)
Portfolio:   Risk parity · HRP · Black-Litterman · Kelly
ML:          HMM/GMM regime · River online learning · MAML/Reptile meta-learning
Infra:       Docker · GitHub Actions · K8s kustomize · NATS/Redis · OpenTelemetry · Chaos
LLM Cost:    $0 / analysis cycle (DeepSeek V4 Flash qua OpenCode)
```

---

## ⚡ Tối ưu nổi bật

| Tối ưu | Chỉ số |
|--------|--------|
| CLI startup time | 4s → **0.22s** (18x) |
| Paper reset | 3.8s → **0.06s** (63x) |
| Parameter sweep | default +10.73% → **optimized +71.96%** |
| LLM cost | $0.002/analysis → **$0/analysis** |

> 📖 Chi tiết: [optimization.md](optimization.md)

---

> 📖 **Bắt đầu từ đâu?** Đọc [Quick Start](getting-started.md) để chạy ngay ·
> Đọc [Kiến trúc](architecture.md) để hiểu sâu ·
> Làm theo [Demo](demo.md) để guide từng bước ·
> Muốn học từ gốc: [Khóa học COURSE](../COURSE/)
