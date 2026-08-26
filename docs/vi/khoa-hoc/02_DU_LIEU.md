# Bài 02 — Dữ liệu, chất lượng và point-in-time correctness

> Mức độ: cơ bản–trung cấp · Thời lượng: 4–5 giờ · Trạng thái: **HIỆN HÀNH**

## Mục tiêu

- Giải thích data manifest và data quality gate.
- Phân biệt event time, observed time và decision time.
- Phát hiện future leakage/incomplete bar.
- Hiểu vì sao missing data phải abstain hoặc reject.

## File cần đọc

- `src/trading_agent/data/pipeline.py`
- `src/trading_agent/execution/data_trust.py`
- `src/trading_agent/strategies/canonical/features.py`
- `src/trading_agent/exchanges/models.py`
- `tests/test_data_trust.py`
- `tests/test_holdout_manifest.py`
- `tests/strategies/test_canonical_contract.py`

## 1. Ba trục thời gian

```text
event_time     thời gian thị trường của bar/tick
observed_at    thời điểm hệ thống được phép biết dữ liệu
decision_time  thời điểm strategy tạo decision
```

Invariant chính:

```text
feature_event_time < observed_at <= decision_time
```

Nếu strategy quyết định ở bar `j`, window thường chỉ được dùng bar đã đóng đến
`j-1`. Dùng close của chính bar `j` để đặt lệnh ở open `j` là future leakage.

## 2. Data quality không chỉ là “có file”

Checklist:

- schema OHLCV đúng;
- timestamp có timezone và tăng đơn điệu;
- không duplicate;
- timeframe/gap policy rõ;
- `high >= max(open, close)` và `low <= min(open, close)`;
- volume/price không âm hoặc vô lý;
- đủ warmup;
- data manifest bind đúng window/content;
- holdout chưa bị mở sớm.

## 3. Lab có hướng dẫn — Đọc data trust tests

```bash
.venv/bin/python -m pytest -q tests/test_data_trust.py tests/test_holdout_manifest.py -vv
```

Với mỗi test, ghi:

- dữ liệu xấu được tạo thế nào;
- exception/reason code mong đợi;
- fail xảy ra trước hay sau strategy;
- evidence nào chứng minh rejection.

## 4. Lab thực hành — Future-leak thought experiment

Cho chuỗi 6 close:

```text
time:   00  01  02  03  04  05
close: 100 101 102  99 110 111
```

Strategy quyết định tại open `04`, dùng MA(3).

1. Window hợp lệ gồm bar nào?
2. MA hợp lệ là bao nhiêu?
3. Nếu dùng close `04=110`, kết quả thay đổi ra sao?
4. Vì sao backtest có thể đẹp giả?

Viết bằng tay trước, sau đó viết một function Python nhỏ trong scratch branch để
tính hai trường hợp và test chúng.

## 5. Bài tập code tự làm

Tạo một fixture DataFrame tối thiểu có:

- một timestamp trùng;
- một gap 2 giờ trong timeframe 1h;
- một bar chưa đóng tại `observed_at`;
- một bar OHLC bất hợp lệ.

Viết bốn test mô tả safe behavior mong muốn. Không sửa production code nếu test
hiện tại đã pass; mục tiêu là hiểu contract.

## 6. Bài tập manifest

Mở [golden replay manifest](../../../artifacts/golden/golden_replay_s0.json) và trả lời:

- manifest bind những identity nào;
- field nào volatile không nên dùng để so sánh merit;
- nếu đổi một candle nhưng giữ row count, hệ thống có phát hiện chắc chắn không;
- cần bổ sung hash nào nếu chưa đủ.

## 7. Lỗi thường gặp

- Sort dữ liệu sau khi đã tính indicator.
- Forward-fill qua gap mà không ghi quality evidence.
- Dùng wall clock để khẳng định exchange candle đã đóng.
- Coi empty window là signal flat hợp lệ mà không reason.
- Hash metadata nhưng không hash nội dung OHLCV.

## Self-check

1. `observed_at` khác `event_time` thế nào?
2. Vì sao warmup thiếu không nên tính indicator một phần?
3. Holdout manifest bảo vệ điều gì?
4. Data gap khi nào có thể record, khi nào phải reject?
5. Row count giống nhau có chứng minh dataset giống nhau không?

## Exit gate

- [ ] Hai data-trust test suite đạt và có nhật ký.
- [ ] Tính đúng future-leak example bằng tay và code.
- [ ] Viết bốn test fixture dữ liệu xấu.
- [ ] Audit được ưu/nhược của manifest hiện có.

Tiếp theo: [Bài 03 — Canonical strategy](03_CANONICAL_STRATEGY.md).

