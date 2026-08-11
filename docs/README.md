# 📘 Trading Agent System — Tài Liệu Kỹ Thuật

> Cập nhật: 2026-08-11 · **Mainnet status: `NO-GO`** — xem [LIVE_TRADING_TODO.md](LIVE_TRADING_TODO.md)

---

## 🧭 Mục lục tài liệu

### Trạng thái & bằng chứng (đọc trước)

| Tài liệu | Mô tả |
|----------|-------|
| [📋 **Live Readiness**](LIVE_TRADING_TODO.md) | Gates P0-P3, readiness matrix, **mainnet NO-GO** |
| [🧩 **Capability Matrix**](CAPABILITY_MATRIX.md) | Mức độ trưởng thành từng capability (implemented → production validated) |
| [🔬 **Research Evidence**](RESEARCH_EVIDENCE.md) | Bằng chứng backtest: in-sample/OOS/WFO/holdout, không gọi research là production |
| [📊 Tổng kết dự án](PROJECT_SUMMARY.md) | Tính năng, ưu/nhược, vận hành |
| [📁 Project Map (generated)](PROJECT_MAP.md) | ⚠️ Auto-generated — cây thư mục thật |

### Kiến trúc & phát triển

| Tài liệu | Mô tả |
|----------|-------|
| [🏛 **Architecture**](ARCHITECTURE.md) | Kiến trúc 5 planes theo code thực tế |
| [📁 Cấu trúc mã nguồn](project-structure.md) | Từng module làm gì, nằm ở đâu |
| [⚡ Development](DEVELOPMENT.md) | Môi trường, test, lint, CI, quy tắc |
| [🚀 Deployment](DEPLOYMENT.md) | Topology single leader, fail-closed, rollback |
| [🔐 Security](SECURITY.md) | Credentials, supply chain, live safety |
| [🏗 Kiến trúc cũ (archive)](archive/architecture_2026-08.md) | Bản kiến trúc trước khi rewrite — chỉ để tham khảo |

### Hướng dẫn sử dụng

| Tài liệu | Mô tả |
|----------|-------|
| [📘 Tài liệu tổng hợp hệ thống](SYSTEM_GUIDE.md) | Tính năng, cách dùng, trade options, UI/UX, Telegram |
| [⚡ Quick Start](getting-started.md) | Lệnh nhanh để bắt đầu |
| [🎮 Demo hướng dẫn chạy](demo.md) | Tutorial A→Z |
| [🧠 Quy trình suy luận & Ra quyết định](reasoning.md) | Cách agent suy luận, phối hợp |
| [🤖 Phase 2: AI Agents](phase2-agents.md) | Chi tiết 4 agent, weighted voting, LLM fallback |

### Phase 6 & vận hành

| Tài liệu | Mô tả |
|----------|-------|
| [🌐 Phase 6: Scale & Multi-Asset](phase6-scale.md) | Multi-exchange, multi-asset, portfolio, ML |
| [📋 Phase 6 P3 Report](PHASE6_P3_REPORT.md) | Integration tests, hardening, benchmarks |
| [📋 Phase 6 P2 Completion](phase6-p2-completion.md) | K8s multi-region, event sourcing, chaos |
| [⚡ Tối ưu hóa hệ thống](optimization.md) | CLI startup, parameter sweep, LLM cost |
| [🚑 Runbook Production](RUNBOOK.md) · [🚑 Runbook Local](RUNBOOK_LOCAL.md) | Vận hành |
| [🚑 Live Trading Runbook](LIVE_TRADING_RUNBOOK.md) | Vận hành live path (fail-closed) |

> 🎓 **Khóa học deep-dive:** [`COURSE/`](../COURSE/) — 10 bài bóc tách hệ thống

---

## Trạng thái hiện tại

| Mặt | Trạng thái |
|-----|-----------|
| Research / backtest | Hoạt động — xem [Research Evidence](RESEARCH_EVIDENCE.md) |
| Paper trading | Hoạt động (Alpaca paper) |
| Testnet (Binance) | Partial (P0.3 execute) |
| Mainnet | **NO-GO** |
| CI status | Xem [GitHub Actions](https://github.com/huythongbk15/LearnTradeAgent/actions) — không claim cố định |
| Production validated | Chưa — xem [Capability Matrix](CAPABILITY_MATRIX.md) |

## 🛠 Stack công nghệ

```
Language:    Python 3.12 (>=3.12,<3.13)
CLI:         Click + Rich
Data:        CCXT → Polars → PyArrow/Parquet · SQLite/TimescaleDB
Backtest:    Custom engine vectorized (Polars)
Agents:      DeepSeek V4 Flash (primary, $0) → OpenAI → DeepSeek → Ollama (fallback)
Execution:   Paper exchange (simulated) · LiveBroker (Alpaca paper) · Binance testnet
Exchanges:   CCXT (8 CEX) · Web3.py (DEX) · Alpaca (stocks) · OANDA (forex)
Portfolio:   Risk parity · HRP · Black-Litterman · Kelly
ML:          HMM/GMM regime · online learning · meta-learning
Infra:       Docker · GitHub Actions · K8s kustomize · OpenTelemetry · Chaos
```
