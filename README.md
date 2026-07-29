# 🤖 Trading Agent System

**Multi-Agent AI Crypto Trading System** — kết hợp LLM agents với chiến lược giao dịch truyền thống để phân tích và tự động giao dịch crypto.

> ⚡ Research → Backtest → Paper Trade → Live

---

## 🏗 Kiến trúc tổng quan

```
┌─────────────────────────────────────────────┐
│               AI AGENT LAYER                │
│  Technical · Sentiment · Fundamental · Macro │
│        Trader · Risk Manager · Portfolio     │
└──────────────────────┬──────────────────────┘
                       │
┌──────────────────────┴──────────────────────┐
│            EXECUTION & DATA LAYER            │
│  Backtest (NautilusTrader + VectorBT)        │
│  Live (CCXT) · Data (Parquet → TimescaleDB)  │
└─────────────────────────────────────────────┘
```

📘 **Tài liệu đầy đủ:** [`docs/`](docs/) — kiến trúc, suy luận, demo, cấu trúc code.
Xem tổng quan chiến lược: [`TRADING_SYSTEM_OVERVIEW.md`](TRADING_SYSTEM_OVERVIEW.md)

---

## 📋 Yêu cầu

- **Python 3.12+**
- **Poetry** (cài: `pip install poetry`)
- **Docker** (khuyến nghị, cho TimescaleDB + Grafana)

---

## 🚀 Bắt đầu nhanh

```bash
# Clone & vào project
cd trading-agent

# Cài dependencies
poetry install

# Xem thông tin hệ thống
poetry run trading-agent info

# Fetch BTC data từ Binance (1h, từ đầu 2026)
poetry run trading-agent data fetch BTC/USDT --since 2026-01-01

# Hoặc download tất cả symbols đã cấu hình
poetry run trading-agent data download-all

# Liệt kê datasets đã có
poetry run trading-agent data list-datasets

# Inspect dữ liệu
poetry run trading-agent data inspect BTC/USDT
```

---

## 🔬 Phase hiện tại: **Phase 0 — Data Pipeline**

- [x] Project skeleton (Poetry, Python 3.12)
- [x] Config module (YAML)
- [x] Data collector (CCXT → Parquet)
- [x] CLI (`trading-agent` command)
- [x] BTC/USDT 1h data (5,020 candles ✓)
- [ ] Data cho các symbols khác
- [ ] TimescaleDB setup
- [ ] Backtest engine integration

---

## 📁 Cấu trúc project

```
├── config/
│   └── config.yaml          # Global configuration
├── src/
│   └── trading_agent/
│       ├── config/
│       │   └── loader.py    # Config loader
│       ├── data/
│       │   ├── collector.py # CCXT data collector
│       │   ├── storage.py   # Parquet storage
│       │   └── types.py     # Data types
│       ├── strategies/      # Strategy library (Phase 1)
│       ├── backtest/        # Backtest engine (Phase 1)
│       ├── agents/          # AI agents (Phase 2)
│       └── cli.py           # Command-line interface
├── data/
│   ├── raw/                 # Raw parquet files
│   └── processed/           # Processed / feature-engineered
├── tests/
├── notebooks/
├── docker-compose.yml       # TimescaleDB + Redis + Grafana
├── Dockerfile
├── pyproject.toml
└── README.md
```

---

## 🎯 Lộ trình

| Phase | Nội dung | Status |
|-------|----------|--------|
| **0** | Data Pipeline & Môi trường | ✅ **Đang làm** |
| **1** | Strategy Library & Backtest | ⏳ |
| **2** | AI Multi-Agent Layer | ⏳ |
| **3** | Execution & Risk Management | ⏳ |
| **4** | Monitoring, Logging, Optimization | ⏳ |
| **5** | Production Hóa & Scale | ⏳ |

---

## 🛠 Công nghệ chính

| Layer | Công nghệ |
|-------|-----------|
| **Data Pipeline** | CCXT · Polars · PyArrow · Parquet |
| **Backtest** | NautilusTrader · VectorBT |
| **AI Agents** | LangGraph · Ollama · DeepSeek · Qwen |
| **Database** | TimescaleDB · Redis |
| **Dashboard** | Grafana · Streamlit |
| **DevOps** | Docker · Poetry · Makefile |

---

## ⚠️ Disclaimer

**Chỉ dành cho mục đích nghiên cứu và giáo dục.** Giao dịch tiền mã hóa tiềm ẩn rủi ro lớn. Không sử dụng số tiền bạn không thể mất. Luôn backtest kỹ và bắt đầu với paper trading trước khi giao dịch thật.

---

<div align="center">
<sub>Built with ❤️ by Trading Agent</sub>
</div>
