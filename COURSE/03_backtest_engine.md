# Bài 3: Backtest Engine — test chiến lược trên 700k nến

> **Trạng thái:** 📝 DRAFT (chưa học — điền sau khi học xong)
> **File gốc:** `trading/backtest/engine.py` (370 dòng)

## 🎯 Mục tiêu

*(điền sau khi học)*

## 📂 File cần đọc

- `trading/backtest/engine.py`

## 🔑 Khái niệm chính (preview)

- Vectorized Polars — tính signal cả cột thay vì lặp từng dòng
- 4 chiến lược: MA Crossover (+71.96%), RSI, BBands, MACD
- Parameter sweep, walk-forward, OOS — và nguy cơ overfitting
- Commission/slippage model

## 🧪 Demo plan

```bash
python3 -m trading.cli.main backtest run ma_crossover BTC/USDT
python3 scripts/backtest_local.py
```

## ❓ Câu hỏi tự kiểm tra

*(điền sau khi học)*
