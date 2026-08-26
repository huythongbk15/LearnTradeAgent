# Bài 00 — Chuẩn bị môi trường và phương pháp học

> Mức độ: nhập môn · Thời lượng: 2–3 giờ · Trạng thái: **HIỆN HÀNH**

## Mục tiêu

Sau bài này bạn có thể:

- xác nhận đúng Python environment;
- chạy test nhỏ và CLI help an toàn;
- dùng controlled execution cho tác vụ quan trọng;
- phân biệt source, tests, scripts, docs, data và artifacts;
- tạo nhật ký tái hiện cho mỗi lab.

## 1. Bản đồ repository tối thiểu

```text
src/trading_agent/   code package chính
tests/               executable specifications
scripts/             entrypoints nghiên cứu/vận hành
config/              cấu hình và policy
data/                dữ liệu và run outputs local
artifacts/           bằng chứng có identity
docs/                kiến trúc, runbook, course
.github/workflows/   CI/CD và release gates
```

Không học thuộc toàn bộ cây thư mục. Dùng [Project Map](../../PROJECT_MAP.md) để tìm
module khi cần.

## 2. Preflight

Các lệnh chỉ đọc hoặc chạy test local:

```bash
pwd
.venv/bin/python --version
.venv/bin/python -c "import trading_agent; print(trading_agent.__file__)"
.venv/bin/python -m pytest --version
test -f data/raw/binance/BTC_USDT/1h.parquet
```

Kỳ vọng:

- đang ở repo root;
- Python thuộc `.venv` và đúng range trong `pyproject.toml`;
- `trading_agent` resolve về `src/trading_agent`;
- pytest gọi được.
- có local BTC/USDT 1h data cho các lab mặc định.

## 3. CLI help an toàn

```bash
.venv/bin/python scripts/full_system_backtest.py --help
.venv/bin/python scripts/run_strategy_tournament.py --help
.venv/bin/python scripts/verify_golden_replay.py --help
```

> Không gọi `scripts/multi_pair_1h_backtest.py --help`: script không có help-only
> mode và sẽ bắt đầu batch thực.

## 4. Controlled execution

Tác vụ có output quan trọng hoặc có thể chạy dài:

```bash
python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 3600 --heartbeat 30 \
  --result-file data/backtests/course/control_result.json \
  -- .venv/bin/python -m pytest -q tests/test_backtest_report_v2.py
```

Bạn cần hiểu ba bằng chứng:

- heartbeat: tiến trình còn sống;
- timeout: giới hạn tài nguyên/thời gian;
- result JSON: command, return code, stdout và stderr.

## 5. Lab có hướng dẫn — Chạy contract test đầu tiên

```bash
.venv/bin/python -m pytest -q tests/test_backtest_report_v2.py
```

Trong nhật ký, ghi:

```text
Commit:
Python:
Command:
Exit code:
Passed/failed:
Warning:
Kết luận:
```

Kết luận đúng không phải “toàn hệ thống tốt”. Nó chỉ là report contract tests đang
đạt ở commit/environment đó.

## 6. Bài tập tự làm

### Bài 00-A — Tìm nguồn sự thật

Tìm và ghi đường dẫn cho:

1. package configuration;
2. test configuration;
3. canonical strategy registry;
4. report schema;
5. tournament CLI;
6. production readiness status.

Không dùng search web; chỉ dùng repository.

### Bài 00-B — Phân loại claim

Phân loại mỗi câu thành `HIỆN HÀNH`, `MỤC TIÊU` hoặc `KHÔNG ĐỦ BẰNG CHỨNG`:

- “Có class regime detector trong source.”
- “Regime router đã production validated.”
- “Một targeted test vừa pass.”
- “Toàn bộ suite hiện xanh.”
- “Mainnet được phép chạy.”

Viết lý do và tài liệu/artifact cần kiểm tra thêm.

## 7. Lỗi thường gặp

- Dùng system Python rồi kết luận import hỏng.
- Xem một test pass như chứng nhận toàn hệ thống.
- Chạy script dài trực tiếp, không timeout/result file.
- Ghi secret hoặc account ID vào nhật ký.
- Sửa code khi chưa tạo được failing test hoặc reproduction.

## Exit gate

- [ ] Import package từ đúng `.venv`.
- [ ] Ba CLI help an toàn chạy được.
- [ ] Report contract test có kết quả được ghi lại.
- [ ] Xác nhận local BTC/USDT 1h data tồn tại.
- [ ] Hoàn thành 00-A và 00-B.
- [ ] Giải thích được vì sao test pass không đồng nghĩa production-ready.

Tiếp theo: [Bài 01 — Kiến trúc và authority](01_KIEN_TRUC.md).
