# 🎮 Demo Hướng Dẫn Chạy

> Hướng dẫn từ A→Z: từ cài đặt, chạy data collector, đến inspect kết quả.
> Thời gian hoàn thành: ~10 phút.

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
Trading Agent System v0.1.0

┏━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Key               ┃ Value                        ┃
┡━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Default Exchange  │ binance                      │
│ Default Timeframe │ 1h                           │
│ Data Storage      │ parquet                      │
│ Enabled Exchanges │ binance, binance_futures     │
│ LLM Provider      │ openai / gpt-4o-mini         │
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

# Fetch từ exchange khác (nếu đã cấu hình)
poetry run trading-agent data fetch BTC/USDT:USDT \
    --exchange binance_futures \
    --timeframe 1h
```

**Output mẫu:**
```
Fetching BTC/USDT 1h from binance…
  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 100% 0:00:02
Got 5020 candles

          timestamp     open     high      low    close     volume
2026-01-01 00:00:00 87648.21 87849.26 87632.74 87809.23 233.66036
2026-01-01 01:00:00 87809.24 88050.00 87809.23 87960.00 241.44162
... (5000+ rows)

Saved to data/raw/binance/BTC_USDT/1h.parquet
```

### Cách B: Download tất cả (theo config)

```bash
poetry run trading-agent data download-all
```

Sẽ fetch tất cả **symbols × timeframes** trong `config/config.yaml`.
Kiểm tra cấu hình trong:
```yaml
# config/config.yaml
symbols:
  binance:
    - "BTC/USDT"
    - "ETH/USDT"
    - "SOL/USDT"
    # ... thêm/xoá symbol tại đây

data:
  timeframes:
    - "1m"
    - "5m"
    - "15m"
    - "1h"
    - "4h"
    - "1d"
```

---

## Bước 3: Kiểm Tra Dữ Liệu

### Liệt kê datasets đã có

```bash
poetry run trading-agent data list-datasets
```

**Output:**
```
┏━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┓
┃ Exchange ┃ Symbol   ┃ Timeframe ┃
┡━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━┩
│ binance  │ BTC/USDT │ 1h        │
│ binance  │ ETH/USDT │ 1h        │
│ binance  │ BTC/USDT │ 4h        │
└──────────┴──────────┴───────────┘
```

### Inspect chi tiết

```bash
poetry run trading-agent data inspect BTC/USDT
```

**Output:**
```
BTC/USDT (binance, 1h)
  Rows: 5,020
  Range: 2026-01-01 00:00:00 → 2026-07-29 03:00:00
  Columns: timestamp, open, high, low, close, volume, exchange, symbol, timeframe
```

### Dùng Makefile (shortcut)

```bash
make info         # trading-agent info
make fetch S=BTC/USDT T=1h   # fetch data
make inspect S=BTC/USDT T=1h # inspect
make datasets      # list datasets
```

---

## Bước 4: Khám Phá Dữ Liệu Với Python

```bash
# Mở Python shell với project context
poetry run python
```

```python
# Trong Python shell:
from trading_agent.data.storage import load_ohlcv, list_datasets

# Liệt kê datasets
print(list_datasets())

# Load dữ liệu
df = load_ohlcv("binance", "BTC/USDT", "1h")
print(df.describe())

# Tính toán cơ bản
df = df.with_columns(
    (df["close"] - df["open"]).alias("body"),
    ((df["close"] - df["open"]) / df["open"] * 100).alias("return_pct")
)
print(df.head())
```

---

## Bước 5: Docker Infra (Khi cần)

> Dùng Docker nếu bạn muốn TimescaleDB, Redis, Grafana chạy local.

**Bước 5a:** Kích hoạt WSL integration trong Docker Desktop:
- Mở Docker Desktop → Settings → Resources → WSL Integration
- Bật cho WSL distro của bạn

**Bước 5b:** Tạo file `.env`:

```bash
cat > .env << EOF
TSDB_PASSWORD=trading_secret
GRAFANA_PASSWORD=admin
EOF
```

**Bước 5c:** Khởi động infra:

```bash
# Start tất cả services
docker compose --profile infra up -d

# Kiểm tra
docker compose ps

# View logs
docker compose logs -f

# Tắt
docker compose down
```

---

## Bước 6: Chạy Toàn Bộ Pipeline

```bash
# 1. Fetch BTC + ETH data
poetry run trading-agent data fetch BTC/USDT --since 2026-06-01
poetry run trading-agent data fetch ETH/USDT --since 2026-06-01

# 2. Inspect kết quả
poetry run trading-agent data inspect BTC/USDT

# 3. List tất cả datasets
poetry run trading-agent data list-datasets

# 4. Xem thông tin hệ thống
poetry run trading-agent info
```

---

## 📊 Demo Script Đầy Đủ (Copy-Paste)

```bash
#!/bin/bash
# ============================================
# Trading Agent System — Full Demo Script
# Chạy: bash demo.sh
# ============================================

set -e

echo "🔧 Bước 1: Kiểm tra môi trường..."
poetry --version
python3 --version

echo ""
echo "📥 Bước 2: Fetch BTC và ETH data..."
poetry run trading-agent data fetch BTC/USDT --since 2026-06-01
poetry run trading-agent data fetch ETH/USDT --since 2026-06-01

echo ""
echo "📋 Bước 3: Liệt kê datasets..."
poetry run trading-agent data list-datasets

echo ""
echo "🔍 Bước 4: Inspect BTC/USDT..."
poetry run trading-agent data inspect BTC/USDT

echo ""
echo "📊 Bước 5: Phân tích nhanh với Python..."
poetry run python -c "
from trading_agent.data.storage import load_ohlcv
import polars as pl

df = load_ohlcv('binance', 'BTC/USDT', '1h')
print(f'Total candles: {len(df):,}')
print(f'Date range: {df[\"timestamp\"].min()} → {df[\"timestamp\"].max()}')
print(f'Price range: \${df[\"low\"].min():,.2f} → \${df[\"high\"].max():,.2f}')
print(f'Avg volume: {df[\"volume\"].mean():,.2f} BTC')
"

echo ""
echo "✅ Demo hoàn tất!"
echo "   Xem thêm: docs/README.md"
```

Lưu thành `demo.sh` và chạy:

```bash
chmod +x demo.sh
./demo.sh
```

---

## 🐛 Troubleshooting

| Vấn đề | Nguyên nhân | Fix |
|--------|------------|-----|
| `ModuleNotFoundError` | Chưa `poetry install` | Chạy `poetry install` |
| `Rate limit` | CCXT bị giới hạn | Tự động retry, hoặc giảm số lượng symbol |
| `No data returned` | Symbol sai / hết hạn | Kiểm tra: `poetry run python -c "import ccxt; print(ccxt.binance().fetch_ohlcv('BTC/USDT', '1h', limit=1))"` |
| Docker không chạy | WSL chưa được enable | Enable WSL integration trong Docker Desktop |
| `ccxt.base.errors.BadSymbol` | Symbol không tồn tại | Kiểm tra `poetry run python -c "import ccxt; e=ccxt.binance(); e.load_markets(); print([s for s in e.markets if 'BTC' in s][:10])"` |

---

## 🎯 Kết Quả Demo

Sau khi hoàn thành demo, bạn sẽ có:

| Thành quả | Mô tả |
|-----------|-------|
| ✅ Môi trường dev | Poetry + Python 3.12 |
| ✅ Data pipeline | CCXT → Parquet với pagination + retry |
| ✅ Dữ liệu | BTC/USDT + ETH/USDT (hoặc hơn) |
| ✅ CLI thành thạo | `trading-agent data fetch/inspect/list-datasets` |
| ✅ Docker infra sẵn sàng | TimescaleDB + Redis + Grafana |

---

> 📖 Quay lại [tài liệu chính](README.md)
> 🧠 Đọc [Quy trình suy luận](reasoning.md) để hiểu cách hệ thống ra quyết định
