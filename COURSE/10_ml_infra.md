# Bài 10: ML + Infra — tự thích nghi & chạy ổn định

> **Trạng thái:** 📝 DRAFT (chưa học — điền sau khi học xong)
> **File gốc:** `trading/ml/`, `trading/events/`, `trading/messaging/`, `trading/infrastructure/`

## 🎯 Mục tiêu

*(điền sau khi học)*

## 📂 File cần đọc

- `ml/regime_detection.py` — HMM/GMM phân loại bull/bear/sideways
- `ml/online/adaptive.py` — online learning (River)
- `ml/meta.py` — MAML/Reptile/Meta-SGD/ANIL
- `events/store.py` + `projections.py` — event sourcing
- `messaging/nats_bus.py`, `redis_streams.py`
- `infrastructure/chaos/` — chaos experiments

## 🔑 Khái niệm chính (preview)

- Regime detection: bật/tắt chiến lược theo trạng thái thị trường
- Online learning: tham số tự thích nghi mỗi bar
- Meta-learning: "học cách học" — thích nghi nhanh với regime mới
- Event sourcing: mọi hành động ghi vào sổ cái, replay được
- Chaos: chủ động phá hệ thống để kiểm tra khả năng hồi phục

## 🧪 Demo plan

```bash
python3 scripts/benchmark_phase6.py
python3 scripts/load_test_phase6.py --quick
python3 scripts/chaos_dryrun.py
python3 -m trading.cli.meta_learning --help
python3 -m pytest tests/test_phase6_integration.py -k "maml or event or chaos"
```

## ❓ Câu hỏi tự kiểm tra

*(điền sau khi học)*
