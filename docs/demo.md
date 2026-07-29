# 🎮 Demo Hướng Dẫn Chạy

> Hướng dẫn từ A→Z: từ cài đặt, chạy data collector, backtest, AI agents, đến paper trading.
> Thời gian hoàn thành: ~15 phút.

---

## 🏁 Yêu cầu

| Tool | Kiểm tra | Ghi chú |
|------|---------|---------|
| Python 3.12+ | `python3 --version` | Đã có sẵn |
| Poetry | `poetry --version` | Cài: `pip install poetry` |
| Git | `git --version` | Optional, để version control |
| Docker | `docker --version` | Optional, cho infra services |

---

## Bước 1: Clone & Cài đặt

```bash
# Di chuyển vào project
cd trading-agent

# Cài dependencies
poetry install

# Kiểm tra hệ thống
poetry run trading-agent info
```

**Kết quả mong đợi:**
```
Trading Agent System v0.3.0

┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Key               ┃ Value                        ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Default Exchange  │ binance                      │
│ Default Timeframe │ 1h                           │
│ Data Storage      │ parquet                      │
│ Enabled Exchanges │ binance, binance_futures     │
│ LLM Provider      │ deepseek/deepseek-chat-v4    │
│ Initial Capital   │ $10,000.00                   │
│ Commission        │ 0.100%                       │
│ Slippage          │ 0.050%                       │
└───────────────────┴──────────────────────────────┘
```

---

## Bước 2: Fetch Dữ Liệu

> ⚡ **2 cách:** Fetch 1 symbol hoặc download tất cả.

### Cách A: Fetch 1 symbol

```bash
# Fetch BTC/USDT 1h, 500 candles gần nhất
poetry run trading-agent data fetch BTC/USDT

# Fetch với khoảng thời gian cụ thể
poetry run trading-agent data fetch BTC/USDT --since 2026-01-01

# Fetch timeframe khác
poetry run trading-agent data fetch ETH/USDT --timeframe 4h --limit 200
```

**Output mẫu:**
```
Fetching BTC/USDT 1h from binance…
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:02
Got 5020 candles
Saved to data/raw/binance/BTC_USDT/1h.parquet
```

### Cách B: Download tất cả (theo config)

```bash
poetry run trading-agent data download-all
```

Sẽ fetch tất cả **symbols × timeframes** trong `config/config.yaml`.

---

## Bước 3: Kiểm Tra & Validate Dữ Liệu

### Liệt kê datasets

```bash
poetry run trading-agent data list-datasets
```

### Inspect chi tiết

```bash
poetry run trading-agent data inspect BTC/USDT

# Output:
# BTC/USDT (binance, 1h)
#   Rows: 5,020
#   Range: 2026-01-01 00:00:00 → 2026-07-29 03:00:00
```

### Kiểm tra chất lượng dữ liệu

```bash
# Kiểm tra gaps, outliers
poetry run trading-agent data validate --symbol BTC/USDT --timeframe 1h

# Validate tất cả datasets
poetry run trading-agent data validate
```

---

## Bước 4: Chạy Backtest

```bash
# Liệt kê strategies có sẵn
poetry run trading-agent backtest list

# Output:
# ┏━━━━━━━━━━━━━━━━┓
# ┃ Strategy       ┃
# ┡━━━━━━━━━━━━━━━━┩
# │ ma_crossover   │
# │ rsi            │
# │ bbands         │
# └────────────────┘

# Chạy backtest với tham số mặc định
poetry run trading-agent backtest run ma_crossover BTC/USDT --timeframe 1h

# Chạy với tham số tùy chỉnh
poetry run trading-agent backtest run ma_crossover BTC/USDT \
    --timeframe 1h \
    -p fast_period=10 -p slow_period=30

# Xem chi tiết metrics
# Output includes: Sharpe Ratio, Return %, Max Drawdown, Win Rate, ...
```

---

## Bước 5: Chạy Multi-Agent Analysis

> ⚠️ Yêu cầu: đã cấu hình LLM API key trong `config/config.yaml`.

```bash
# Phân tích đầy đủ (4 agents)
poetry run trading-agent agents analyze BTC/USDT -t 1h

# Chỉ xem kết quả cuối (không reasoning)
poetry run trading-agent agents analyze BTC/USDT -t 1h --quiet

# Output: BUY conf=70% price=$63,796 risk=LOW
```

**Kết quả mẫu:**
```
Multi-Agent Analysis — BTC/USDT (1h)
Price: $63,796.01
Final Signal: 🟡 HOLD  (confidence: 28%, risk: LOW)

├── technical_analyst — HOLD (conf: 50%)
├── sentiment_analyst — HOLD (conf: 65%)
└── risk_manager — BUY (conf: 70%)

Key Indicators:
  bb_lower: 63219.76  bb_mid: 63722.67  bb_upper: 64225.58
  ma_20: 63722.67  ma_50: 64185.34  rsi: 57.90
```

---

## Bước 6: Paper Trading

### Xem trạng thái portfolio

```bash
poetry run trading-agent execution status

# Output:
# 💰 Portfolio Summary
# Equity: $10,000.00  (+0.00%)
# Cash: $10,000.00  |  Positions: $0.00
# Unrealized P&L: +0.00  |  Trades: 0
```

### Chạy full cycle: phân tích → tự động trade

```bash
# Phân tích BTC → nếu BUY signal thì mua → set stop-loss
poetry run trading-agent execution run BTC/USDT -t 1h --stop-loss 0.05
```

### Xem trade history

```bash
poetry run trading-agent execution trades
```

### Kiểm tra risk status

```bash
poetry run trading-agent execution risk
# 🛡️ Risk Controller Status
# Drawdown: 0.00% (limit: 15%) ✅
# Daily Loss: 0.00% (limit: 8%) ✅
# Circuit Breaker: OK
```

### Kill switch (đóng mọi vị thế)

```bash
# Đóng 1 symbol
poetry run trading-agent execution close BTC/USDT

# Đóng tất cả
poetry run trading-agent execution close --all
```

### Reset về trạng thái ban đầu

```bash
poetry run trading-agent execution reset
```

---

## 🏁 Demo Script Đầy Đủ

```bash
#!/bin/bash
# ============================================
# Trading Agent System — Full Demo Script
# Chạy: bash demo.sh
# ============================================

set -e

echo "🔧 Bước 1: Kiểm tra môi trường..."
poetry --version ; python3 --version

echo ""
echo "📥 Bước 2: Fetch BTC/USDT 1h..."
poetry run trading-agent data fetch BTC/USDT --since 2026-07-01

echo ""
echo "🔍 Bước 3: Kiểm tra dữ liệu..."
poetry run trading-agent data inspect BTC/USDT
poetry run trading-agent data validate --symbol BTC/USDT --timeframe 1h

echo ""
echo "📊 Bước 4: Chạy backtest MA Crossover..."
poetry run trading-agent backtest run ma_crossover BTC/USDT --timeframe 1h

echo ""
echo "🧠 Bước 5: Multi-agent analysis..."
poetry run trading-agent agents analyze BTC/USDT -t 1h --quiet

echo ""
echo "💰 Bước 6: Paper trading status..."
poetry run trading-agent execution status

echo ""
echo "✅ Demo hoàn tất!"
```

---

## 📊 Toàn bộ CLI commands

```bash
# ── System ──
trading-agent info                    # System info
trading-agent config validate         # Validate config

# ── Data (Phase 0) ──
trading-agent data fetch <symbol>     # Fetch OHLCV
trading-agent data update <symbol>    # Incremental update
trading-agent data inspect <symbol>   # Inspect data
trading-agent data validate [...]     # Validate data quality
trading-agent data list-datasets      # List datasets
trading-agent data download-all       # Download all config'd symbols
trading-agent data export <symbol>    # Export to CSV/JSON

# ── Backtest (Phase 1) ──
trading-agent backtest list           # List strategies
trading-agent backtest run <strategy> # Run backtest

# ── AI Agents (Phase 2) ──
trading-agent agents list             # List agents
trading-agent agents analyze <symbol> # Multi-agent analysis

# ── Execution (Phase 3) ──
trading-agent execution status        # Portfolio summary
trading-agent execution run <symbol>  # Agents → trade
trading-agent execution trades        # Trade history
trading-agent execution close [sym]   # Close position(s)
trading-agent execution risk          # Risk controller
trading-agent execution reset         # Reset state
```

---

## 🐛 Troubleshooting

| Vấn đề | Nguyên nhân | Fix |
|--------|------------|-----|
| `ModuleNotFoundError` | Chưa `poetry install` | Chạy `poetry install` |
| `Rate limit` | CCXT bị giới hạn | Tự động retry hoặc giảm symbol |
| `No data returned` | Symbol sai / hết hạn | Kiểm tra: `ccxt.binance().fetch_ohlcv('BTC/USDT', '1h', limit=1)` |
| LLM timeout | API key hết hạn / quota | Kiểm tra OpenRouter API key trong config |
| Paper state cũ | Reset chưa chạy | `trading-agent execution reset` |

---

> 📖 Quay lại [tài liệu chính](README.md)
> 🧠 Đọc [Quy trình suy luận](reasoning.md) để hiểu cách hệ thống ra quyết định
> ⚡ Xem [Tối ưu hóa](optimization.md) để biết các optimization đã thực hiện
