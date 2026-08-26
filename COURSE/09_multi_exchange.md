# Bài 9: Multi-Exchange — 8 sàn, 1 API

> ⚠️ **HISTORICAL DRAFT:** Không dùng các tuyên bố năng lực ở đây để quyết định triển khai. Xem [Capability Matrix](../docs/CAPABILITY_MATRIX.md).

> **Trạng thái:** 📝 DRAFT (chưa học — điền sau khi học xong)
> **File gốc:** `trading/exchanges/order_router.py` + adapters

## 🎯 Mục tiêu

*(điền sau khi học)*

## 📂 File cần đọc

- `exchanges/order_router.py` — Best Price, TWAP, VWAP, Split
- `exchanges/ccxt_adapter.py` — wrapper 8 sàn CEX
- `exchanges/alpaca_adapter.py` — US stocks
- `exchanges/oanda_adapter.py` — forex
- `exchanges/websocket_manager.py`, `health_monitor.py`

## 🔑 Khái niệm chính (preview)

- Unified adapter pattern — 1 interface, nhiều sàn
- Smart routing: chọn sàn giá tốt nhất, chia lệnh lớn tránh slippage
- Rate limit token bucket, failover khi sàn lag

## 🧪 Demo plan

```bash
python3 -m pytest tests/test_phase6_integration.py -k "failover or router"
```

## ❓ Câu hỏi tự kiểm tra

*(điền sau khi học)*
