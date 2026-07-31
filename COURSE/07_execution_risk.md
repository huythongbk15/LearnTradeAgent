# Bài 7: Execution & Risk — paper trade full cycle

> **Trạng thái:** 📝 DRAFT (chưa học — điền sau khi học xong)
> **File gốc:** `scripts/trade_local.py`

## 🎯 Mục tiêu

*(điền sau khi học)*

## 📂 File cần đọc

- `scripts/trade_local.py` — entry point chạy full cycle
- Paper exchange (mô phỏng khớp lệnh)
- Risk controller + circuit breaker + kill switch

## 🔑 Khái niệm chính (preview)

- Full cycle: phân tích → quyết định → đặt lệnh → theo dõi → chốt lời/cắt lỗ
- Paper exchange: không tiền thật, mô phỏng slippage/fee
- Stop-loss, position sizing, đa tài sản (BTC, SOL)
- Demo thực tế: BUY → SELL +3.35%

## 🧪 Demo plan

```bash
python3 scripts/trade_local.py --once
python3 -m trading.cli.main execution status
```

## ❓ Câu hỏi tự kiểm tra

*(điền sau khi học)*
