# 📘 Trading Agent System — Tài Liệu Kỹ Thuật

> Phiên bản: `v0.1.0` · Cập nhật: 2026-07-29

---

## 🧭 Mục lục

| Tài liệu | Mô tả |
|----------|-------|
| [🏛 Kiến trúc hệ thống](architecture.md) | Sơ đồ tổng quan, luồng dữ liệu, các layer |
| [🧠 Quy trình suy luận & Ra quyết định](reasoning.md) | Cách agent suy luận, phối hợp và ra lệnh |
| [🎮 Demo hướng dẫn chạy](demo.md) | Tutorial từ A→Z: cài đặt → data → chạy thử |
| [📁 Cấu trúc mã nguồn](project-structure.md) | Từng module làm gì, nằm ở đâu |
| [⚡ Quick Start](getting-started.md) | Lệnh nhanh để bắt đầu |

---

## 🏗 Tổng quan hệ thống (1 phút)

```
┌──────────────────────────────────────────────────────────────┐
│                    📡 DATA LAYER                             │
│  CCXT → Parquet/TimescaleDB → Polars DataFrames              │
│  Binance · Bybit · OKX · (mở rộng sau)                      │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────┴───────────────────────────────────┐
│                    🧪 BACKTEST LAYER                         │
│  NautilusTrader (production) + VectorBT (research)           │
│  Strategy · Metrics · Optimization                           │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────┴───────────────────────────────────┐
│                    🤖 AI AGENT LAYER                         │
│  ┌────────────┐ ┌───────────┐ ┌──────────┐ ┌───────────┐   │
│  │ Technical  │ │ Sentiment │ │Fundamental│ │  Macro    │   │
│  │ Analyst    │ │ Analyst   │ │ Analyst   │ │  Analyst  │   │
│  └─────┬──────┘ └─────┬─────┘ └─────┬────┘ └─────┬─────┘   │
│        └──────────────┼─────────────┼────────────┘          │
│                   ┌───┴────┐   ┌────┴───┐                   │
│                   │ Trader │   │  Risk  │                   │
│                   │ Agent  │   │Manager │                   │
│                   └───┬────┘   └────┬───┘                   │
│                       └──────┬──────┘                       │
│                         ┌────┴────┐                         │
│                         │Portfolio│                         │
│                         │Manager  │                         │
│                         └────┬────┘                         │
└──────────────────────────────┼──────────────────────────────┘
                               │
┌──────────────────────────────┴──────────────────────────────┐
│                    ⚡ EXECUTION LAYER                        │
│  CCXT → Binance/Bybit/OKX                                   │
│  Position Manager · Risk Checks · Circuit Breaker           │
└─────────────────────────────────────────────────────────────┘

         ┌──────────────┐    ┌──────────────┐
         │  Grafana     │    │  Telegram    │
         │  Dashboard   │    │  Alerts      │
         └──────────────┘    └──────────────┘
              MONITORING & ALERTING
```

---

## 🎯 Mục tiêu thiết kế

| Nguyên tắc | Giải thích |
|-----------|-----------|
| ** Modular** | Mỗi layer độc lập, có thể swap implementation |
| ** Research → Production** | Cùng code base, cùng interface. Backtest → Paper → Live |
| ** AI-first** | LLM agents là trung tâm ra quyết định, không phải add-on |
| ** Free-model ưu tiên** | Ollama, DeepSeek, Qwen — giảm chi phí vận hành |
| ** Safety** | Risk manager luôn là lớp kiểm tra cuối trước khi execute |

---

## 🗺 Lộ trình dự kiến

```
Phase 0 ──── Data Pipeline + Skeleton          ← BẠN ĐANG Ở ĐÂY
Phase 1 ──── Strategy Library + Backtest Engine
Phase 2 ──── AI Multi-Agent Layer (LLM)
Phase 3 ──── Execution + Risk Management
Phase 4 ──── Monitoring + Optimization
Phase 5 ──── Production 24/7
```

---

## 🛠 Stack công nghệ

```
Language:    Python 3.12+
Package:     Poetry
CLI:         Click + Rich
Data:        CCXT → Polars → PyArrow/Parquet
Backtest:    NautilusTrader + VectorBT (Phase 1)
Agents:      LangGraph + Ollama/DeepSeek (Phase 2+)
Database:    TimescaleDB (sau Phase 4)
Infra:       Docker Compose
Monitoring:  Grafana (sau Phase 4)
```

---

> 📖 **Bắt đầu từ đâu?** Đọc [Quick Start](getting-started.md) nếu bạn muốn chạy ngay.
> Đọc [Kiến trúc](architecture.md) nếu bạn muốn hiểu sâu.
> Làm theo [Demo](demo.md) nếu bạn muốn guide từng bước.
