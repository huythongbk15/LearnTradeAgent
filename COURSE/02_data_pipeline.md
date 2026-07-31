# Bài 2: Data Pipeline — 696K candles đi vào hệ thống

> **Trạng thái:** 📝 DRAFT (chưa học — điền sau khi học xong)
> **File gốc:** `trading/data/pipeline.py` (654 dòng)

## 🎯 Mục tiêu

*(điền sau khi học)*

## 📂 File cần đọc

- `trading/data/pipeline.py` — fetch → validate → Parquet

## 🔑 Khái niệm chính (preview)

- CCXT fetch_ohlcv → Polars DataFrame → Parquet
- Incremental update (chỉ tải dữ liệu mới — tiết kiệm 95-99% bandwidth)
- Data validation: gap detection, duplicate check
- Lazy loading — vì sao CLI khởi động 0.22s

## 🧪 Demo plan

```bash
python3 -c "from trading.data.pipeline import ...; ..."
```

## ❓ Câu hỏi tự kiểm tra

*(điền sau khi học)*
