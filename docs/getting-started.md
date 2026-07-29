# ⚡ Quick Start

> 5 lệnh để bắt đầu với Trading Agent System.

```bash
# 1. Cài đặt
poetry install

# 2. Xem thông tin hệ thống
poetry run trading-agent info

# 3. Fetch dữ liệu Bitcoin
poetry run trading-agent data fetch BTC/USDT --since 2026-01-01

# 4. Kiểm tra dữ liệu
poetry run trading-agent data inspect BTC/USDT

# 5. Xem danh sách datasets
poetry run trading-agent data list-datasets
```

📍 **Toàn bộ hướng dẫn chi tiết:** [🎮 Demo](demo.md)

---

## Cấu hình nhanh

Mở `config/config.yaml` và chỉnh sửa:

```yaml
# Chỉnh symbols bạn muốn trade
symbols:
  binance:
    - "BTC/USDT"
    - "ETH/USDT"
    - "SOL/USDT"     # Thêm/xoá ở đây

# Chỉnh timeframes bạn cần
data:
  timeframes:
    - "1h"           # Mặc định
    - "4h"           # Thêm/xoá ở đây
```

---

## Shortcuts (Makefile)

```bash
make fetch S=BTC/USDT T=1h    # Fetch data
make inspect S=BTC/USDT T=1h  # Inspect
make datasets                  # List datasets
make shell                     # Python shell
```

---

## Tài liệu liên quan

| File | Nội dung |
|------|---------|
| [🏛 Kiến trúc](architecture.md) | Sơ đồ tổng quan, các layer |
| [🧠 Suy luận](reasoning.md) | Cách agent ra quyết định |
| [🎮 Demo](demo.md) | Hướng dẫn từng bước chi tiết |
| [📁 Cấu trúc](project-structure.md) | Mỗi module làm gì |
