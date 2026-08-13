# Final Holdout Policy (P2)

> Trạng thái: **FROZEN** — `data/research_manifest.json` (freeze 2026-08-11)

## Holdout window

| | |
| --- | --- |
| **Window** | 2026-02-06 00:00 UTC → 2026-08-05 (180 ngày, 6 tháng cuối) |
| **Timeframe** | 1h |
| **Datasets** | 71 file parquet đã fingerprint SHA-256 |
| **Manifest** | `data/research_manifest.json` (integrity SHA-256 tự tham chiếu) |
| **Generator** | `scripts/generate_holdout_manifest.py` |

Window được căn theo **end chung sớm nhất** trên toàn bộ symbol của timeframe,
đảm bảo không symbol nào rò rỉ dữ liệu tương lai vào holdout.

## Quy tắc bất biến

1. **Không dùng holdout cho bất kỳ quyết định nghiên cứu nào**: chọn tham số,
   feature engineering, training, validation, xếp hạng strategy, ensemble weight,
   hoặc thay đổi config đưa lên live.
2. **Chỉ score holdout đúng một lần** sau freeze date, để tạo bằng chứng release-gate.
3. **Không sửa** `data/research_manifest.json`. Muốn mở rộng holdout → tạo manifest
   mới với freeze date mới; manifest cũ vẫn là bản ghi cho window của nó.
4. Tool nào vi phạm sẽ fail-closed qua `guard_training_window()`:
   ```python
   from trading_agent.alpha_research.holdout import guard_training_window

   guard_training_window(start=..., end=...)  # raises HoldoutError nếu overlap
   ```

## Cách tạo lại / mở rộng

```bash
# xem window (không ghi)
python scripts/generate_holdout_manifest.py --months 6

# freeze manifest mới (ghi đè; chỉ làm khi thực sự muốn thay thế)
python scripts/generate_holdout_manifest.py --months 6 --write
```

## Ghi chú triển khai P2 còn lại (chưa hoàn thành)

- Per-fold trade minimums, regime breakdowns, block-bootstrap confidence
  intervals, Deflated/Probabilistic Sharpe.
- Stress test fees/spread/slippage 1x/2x/3x + gaps, latency, missing data,
  partial fills, outages, correlated drawdowns.
- Execution simulator: precision, fee assets, depth, cancellation, dust.
- Paper/testnet tracking error trước khi đánh giá maker/TWAP execution.
