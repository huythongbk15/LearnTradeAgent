# 📘 Trading Agent System — Tài Liệu Kỹ Thuật

> Phiên bản: `v0.3.0` · Cập nhật: 2026-07-29

---

## 🧭 Mục lục

| Tài liệu | Mô tả |
|----------|-------|
| [🏛 Kiến trúc hệ thống](architecture.md) | Sơ đồ tổng quan, luồng dữ liệu, các layer |
| [🧠 Quy trình suy luận & Ra quyết định](reasoning.md) | Cách agent suy luận, phối hợp và ra lệnh |
| [🎮 Demo hướng dẫn chạy](demo.md) | Tutorial từ A→Z: cài đặt → data → backtest → agents → execution |
| [📁 Cấu trúc mã nguồn](project-structure.md) | Từng module làm gì, nằm ở đâu |
| [⚡ Quick Start](getting-started.md) | Lệnh nhanh để bắt đầu |
| [⚡ Tối ưu hóa hệ thống](optimization.md) | CLI startup 4s→0.22s, parameter sweep +71%, LLM cost 22x rẻ hơn |

---

## 🏗 Tổng quan hệ thống (1 phút)

```
┌──────────────────────────────────────────────────────────────┐
│                    📡 DATA LAYER                             │
│  CCXT → Parquet → Polars DataFrames                           │
│  5 symbols × 4 timeframes · 696K candles · 0 gaps            │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────┴───────────────────────────────────┐
│                    🧪 BACKTEST LAYER                         │
│  4 strategies · Parameter sweep · Walk-forward               │
│  Metrics: Sharpe, Return, Win Rate, Max DD                   │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────┴───────────────────────────────────┐
│                    🤖 AI AGENT LAYER (Phase 2)               │
│  ┌────────────┐ ┌──────────────┐ ┌──────────────┐           │
│  │ Technical  │ │ Sentiment    │ │ Risk Manager │           │
│  │ Analyst    │ │ Analyst      │ │              │           │
│  └─────┬──────┘ └───────┬──────┘ └──────┬───────┘           │
│        └────────────────┼────────────────┘                   │
│                    ┌────┴────┐                                │
│                    │  Trader │  ← Weighted voting + debate   │
│                    │  Agent  │                                │
│                    └────┬────┘                                │
│                         │  DeepSeek V4 Flash / OpenRouter     │
└─────────────────────────┼────────────────────────────────────┘
                          │
┌─────────────────────────┴────────────────────────────────────┐
│                    ⚡ EXECUTION LAYER (Phase 3)               │
│  ┌────────────┐   ┌───────────┐   ┌───────────────────┐     │
│  │ Paper      │   │ Portfolio │   │ Risk Controller   │     │
│  │ Exchange   │ → │ Manager   │ → │ Drawdown · Daily   │     │
│  │ (simulated)│   │ (P&L)     │   │ Loss · Circuit Brk │     │
│  └────────────┘   └───────────┘   └───────────────────┘     │
│  State: data/execution/paper_binance.json                    │
└─────────────────────────────────────────────────────────────┘
```

---

## 🎯 Status hiện tại

| Phase | Module | Status | Chi tiết |
|-------|--------|--------|----------|
| **0** | Data Pipeline | ✅ **Hoàn thành** | CCXT → Parquet, 5 symbols × 4 TFs, 696K candles |
| **1** | Backtest Engine | ✅ **Hoàn thành** | 4 strategies, parameter sweep, walk-forward |
| **2** | AI Multi-Agent | ✅ **Hoàn thành** | 4 agents (Technical, Sentiment, Risk, Trader), DeepSeek V4 Flash |
| **3** | Execution & Risk | ✅ **Hoàn thành** | Paper exchange, risk controller, circuit breaker, CLI |
| **4** | Monitoring | ⬜ Chưa bắt đầu | Grafana, Telegram alerts |
| **5** | Production | ⬜ Chưa bắt đầu | Docker 24/7, failover |

---

## 🗺 Lộ trình hoàn chỉnh

```
Phase 0 ──── Data Pipeline + Skeleton            ✅
Phase 1 ──── Strategy Library + Backtest Engine   ✅
Phase 2 ──── AI Multi-Agent Layer (LLM)           ✅
Phase 3 ──── Execution + Risk Management          ✅ ← BẠN ĐANG Ở ĐÂY
Phase 4 ──── Monitoring + Optimization            ⬜
Phase 5 ──── Production 24/7                      ⬜
```

---

## 🛠 Stack công nghệ

```
Language:    Python 3.12+
CLI:         Click + Rich
Data:        CCXT → Polars → PyArrow/Parquet
Backtest:    Custom engine với Polars vectorized ops
Agents:      DeepSeek V4 Flash (primary) → GPT-4o-mini (fallback) → Ollama (local)
Execution:   Paper exchange (simulated) / CCXT (live — future)
LLM Cost:    ~$0.00009 / analysis cycle (4 agents)
Infra:       Docker Compose
```

---

## ⚡ Tối ưu nổi bật

| Tối ưu | Chỉ số |
|--------|--------|
| CLI startup time | 4s → **0.22s** (18x) |
| Paper reset | 3.8s → **0.06s** (63x) |
| Parameter sweep | default +10.73% → **optimized +71.96%** |
| LLM cost | $0.002/analysis → **$0.00009/analysis** (22x rẻ hơn) |

> 📖 Chi tiết: [optimization.md](optimization.md)

---

> 📖 **Bắt đầu từ đâu?** Đọc [Quick Start](getting-started.md) nếu bạn muốn chạy ngay.
> Đọc [Kiến trúc](architecture.md) nếu bạn muốn hiểu sâu.
> Làm theo [Demo](demo.md) nếu bạn muốn guide từng bước.
