# Final Holdout Policy (P2)

> Trạng thái: **FROZEN** — `data/research_manifest.json`.
> Metadata verified 2026-10-19; owner: research operator.
> Freeze metadata: `2026-09-06T11:33:21.967798+07:00`.

## Holdout window

| | |
| --- | --- |
| **Window** | 2026-02-06 16:00 UTC → 2026-08-05 16:00 UTC |
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

## Xác minh / mở rộng

Manifest SHA-256:
`c1802ba532f7a7a17e7794fb2fb001f36c08f404b3a530be4026b870a65b2247`.
Inventory metadata — local evidence `data/bnb_qualification_readiness_20261019_01/inventory.json`
đã kiểm integrity mà không đọc giá holdout. Timestamp naive trong manifest được
helper chuẩn hóa UTC. Timeframe1h trong metadata không cấp quyền dùng holdout4h.

Không chạy generator với `--write` để ghi đè manifest đã freeze. Đề xuất mở rộng
phải được duyệt riêng, dùng artifact mới và giữ bản cũ. Dữ liệu nằm trước boundary
không tự động là unseen/OOS; cần exposure ledger. Metadata inventory không chứng
minh holdout chưa từng bị truy cập trong toàn bộ lịch sử dự án.

## Ghi chú triển khai P2 còn lại (chưa hoàn thành)

- Per-fold trade minimums, regime breakdowns, block-bootstrap confidence
  intervals, Deflated/Probabilistic Sharpe.
- Stress test fees/spread/slippage 1x/2x/3x + gaps, latency, missing data,
  partial fills, outages, correlated drawdowns.
- Execution simulator: precision, fee assets, depth, cancellation, dust.
- Paper/testnet tracking error trước khi đánh giá maker/TWAP execution.
