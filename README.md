# 🤖 Trading Agent System

**Multi-Agent AI Crypto Trading System** — kết hợp LLM agents với chiến lược giao dịch truyền thống để phân tích và tự động giao dịch crypto.

> ✅ Phase 0 (Data) · ✅ Phase 1 (Backtest) · ✅ Phase 2 (AI Agents) · ✅ Phase 3 (Execution)

---

## 🏗 Kiến trúc tổng quan

```
📡 DATA LAYER — CCXT → Parquet (696K candles, 20 datasets)
       ↓
🧪 BACKTEST LAYER — 4 strategies + parameter sweep + walk-forward
       ↓
🤖 AI AGENT LAYER — Technical · Sentiment · Risk · Trader (DeepSeek V4)
       ↓
⚡ EXECUTION LAYER — Paper exchange · Risk controller · Circuit breaker
```

---

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

📘 **Tài liệu đầy đủ:** [`docs/`](docs/) — kiến trúc, suy luận, demo, tối ưu hóa.

---

## 🔬 Các Phase hoàn thành

| Phase | Module | Chi tiết |
|-------|--------|----------|
| ✅ **0** | **Data Pipeline** | 5 symbols × 4 timeframes, 696K candles, 0 gaps, data validation |
| ✅ **1** | **Backtest Engine** | 4 strategies (MA, RSI, BBands, MACD), parameter sweep, walk-forward analysis |
| ✅ **2** | **AI Agents** | 4 agents (Technical, Sentiment, Risk, Trader), DeepSeek V4 Flash, weighted voting |
| ✅ **3** | **Execution & Risk** | Paper exchange, position tracking, risk controller, circuit breaker, kill switch |

---

## ⚡ Tối ưu nổi bật

| Tối ưu | Chỉ số |
|--------|--------|
| CLI startup time | 4s → **0.22s** (lazy imports) |
| Parameter sweep | default +10.73% → **optimized +71.96%** |
| LLM cost | $0.002/analysis → **$0.00009** (DeepSeek V4 Flash) |
| Incremental data | 95-99% less data transfer |

---

## 🛠 Stack

| Layer | Công nghệ |
|-------|-----------|
| **CLI** | Click + Rich |
| **Data** | CCXT → Polars → Parquet |
| **Backtest** | Custom engine (vectorized Polars) |
| **AI Agents** | DeepSeek V4 Flash / GPT-4o-mini / Ollama |
| **Execution** | Paper exchange (simulated) |
| **LLM Cost** | ~$0.00009 / cycle (4 agents) |

---

## 📖 Tài liệu

| File | Mô tả |
|------|-------|
| [`docs/README.md`](docs/README.md) | 🧭 Index tài liệu |
| [`docs/architecture.md`](docs/architecture.md) | 🏛 Kiến trúc hệ thống |
| [`docs/reasoning.md`](docs/reasoning.md) | 🧠 Quy trình suy luận |
| [`docs/demo.md`](docs/demo.md) | 🎮 Demo từng bước |
| [`docs/project-structure.md`](docs/project-structure.md) | 📁 Cấu trúc mã nguồn |
| [`docs/getting-started.md`](docs/getting-started.md) | ⚡ Quick start |
| [`docs/optimization.md`](docs/optimization.md) | ⚡ Tối ưu hóa |

---

## 📚 Khóa học bóc tách hệ thống

> Học lại từng phần nhỏ của dự án — đọc code thật, hiểu vì sao thiết kế như vậy, chạy demo.

→ **[`COURSE/`](COURSE/) — 10 bài deep-dive** (Data Model → Data Pipeline → Backtest → Strategies → LLM → Agents → Execution → Portfolio → Multi-Exchange → ML + Infra)

## ⚠️ Disclaimer

**Chỉ dành cho mục đích nghiên cứu và giáo dục.** Giao dịch tiền mã hóa tiềm ẩn rủi ro lớn. Không sử dụng số tiền bạn không thể mất. Luôn bắt đầu với paper trading trước khi giao dịch thật.

---

<div align="center">
<sub>Built with ❤️ · v0.3.0 · 2026-07-29</sub>
</div>
