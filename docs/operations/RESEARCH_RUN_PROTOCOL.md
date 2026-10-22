# Quy trình chạy một nghiên cứu có thể kết luận

Status: TARGET — quy trình vận hành đề xuất; chưa có lượt chạy mới theo mẫu này
Owner: research operator + reviewer
Verified: 2026-10-18 (đường dẫn, mẫu và số liệu ví dụ)
Evidence: [BNB evidence card](BNB_ADX25_EVIDENCE_CARD.md), [tracker](../CAMPAIGN_STATUS.md)

## Mục đích và ranh giới

Mỗi lượt nghiên cứu trả lời **một câu hỏi kinh tế**, không phải tìm cách vượt gate.
Ưu tiên hoàn tất một vòng nhỏ trước khi mở rộng pair, timeframe hoặc tham số.
Đây là checklist cho agent/operator, **không phải schema được runner hỗ trợ**.
Không thay thế [methodology](../RESEARCH_METHODOLOGY.md), protocol calibration,
native gate contract hoặc quyền thực thi hiện có. Không thêm framework/selector.

```text
Câu hỏi → đóng băng manifest → preflight/parity → chạy có kiểm soát
        → đối soát ledger → evidence card/review → đúng một bước tiếp
```

## Các bước và điều kiện chuyển bước

| Bước | Người chịu trách nhiệm | Đầu ra bắt buộc / điều kiện chuyển |
| --- | --- | --- |
| 1. Đặt câu hỏi | Researcher | Một giả thuyết có thể bác bỏ; phân biệt signal, filter, sizing, allocation. Công bố dữ liệu đã xem và các thử nghiệm trước. |
| 2. Đóng băng | Operator | Manifest đủ source/data/config/hash, tiêu chí và budget; approval đúng scope. Không có trường bắt buộc `null`. |
| 3. Preflight | Engineer + reviewer | Adapter thực sự thực thi đúng ý nghĩa; cùng calendar/mask/warmup, signal cutoff và fill timing; parity theo từng bar, không chỉ cùng tổng return. |
| 4. Thực thi | Operator | Packet mới, command/exit/timeout/RSS/heartbeat, raw signals/orders/fills/marks; giữ packet cũ nguyên vẹn. |
| 5. Đối soát | Reviewer | Từ fills dựng lại cash/inventory/equity, fees, open PnL, return/DD; đối chiếu trước/sau source/input hashes. |
| 6. Kết luận | Researcher + reviewer | Card tách engineering/economics/qualification; mọi nhánh có terminal status và lý do. Người review độc lập không đồng nghĩa chạy lại cùng verifier. |
| 7. Chọn bước tiếp | Operator | Đúng một hành động, có đầu ra và điều kiện dừng. Không tự mở holdout hoặc chuyển paper/live. |

Trước bước 4, ghi command thực tế được kiểm bằng `--help`, input/output và expected
exit. Không đoán flag hoặc lấy runner legacy chỉ vì tên gần giống. Dùng wrapper:

```bash
.venv/bin/python scripts/qwenpaw_control/controlled_exec.py \
  --timeout <approved_seconds> --heartbeat 30 --result-file <new_packet>/execution.json \
  -- .venv/bin/python <reviewed_entrypoint> <reviewed_args>
```

Đây là cú pháp mẫu, **không chạy nguyên văn**. Chỉ đọc local data, ghi packet mới;
không network/broker. Khi lỗi, giữ packet và receipt để điều tra, không xóa hay
ghi đè để chạy lại. Retry chỉ trong budget đã duyệt và có attempt identity mới.

## Manifest mẫu: sao chép rồi điền, không dùng làm runtime config

[BNB draft manifest](BNB_ADX25_RESEARCH_MANIFEST.json) là mẫu điền cho case hiện tại.
Khi tái dùng, thay toàn bộ identity/câu hỏi; không giữ hash BNB cho dữ liệu khác.

Các nhóm bắt buộc:

- `question`, `primary_comparison`, `decision_rule`: mục tiêu trước khi xem kết quả,
  metric/đơn vị, trade-off return/DD/exposure; không đặt threshold hậu nghiệm.
- `identity`: commit **và** tree/dirty diff/runner hash, input SHA, UTC calendar,
  mask/warmup, parameters và trial-registry snapshot. Commit không đủ khi dirty.
- `execution`: timing, sizing, exit/cooldown/risk, fee/spread/impact, seed, final-mark
  và carry convention. Strategy name không chứng minh execution semantics.
- `arms`: signal/filter khác nhau ở đâu, mọi yếu tố còn lại giữ chung; cash và
  buy-and-hold là benchmark, không mặc nhiên risk-matched.
- `budget`, `approval`, `artifacts`, `reuse`: timeout/retries/concurrency/stop,
  scope quyền, đầu ra và bằng chứng tương thích.

`null`, placeholder hoặc approval còn thiếu => **BLOCKED_PREFLIGHT**, không freeze.
Hash manifest sau khi điền; ghi approval gắn đúng hash. Source/config thay đổi thì
tạo revision/packet mới và review lại, không sửa manifest đã chạy.

## So sánh và tái sử dụng đúng phạm vi

- Cùng timestamp, evaluation mask, initial cash, sizing, cost/risk/mark conventions.
  Khác warmup thì đóng băng common evaluation calendar trước khi đo.
- Không trừ chi phí lần hai khỏi net PnL. Audit lineage/đơn vị/mục đích của
  `median_test_return_pct - 0.32 * median_fold_trades` trước khi dùng score để xếp
  hạng; chưa kết luận đó là bug hay một hurdle có chủ ý.
- Không so return đơn lẻ khi exposure khác nhau. Exposure-matched hậu nghiệm chỉ
  là diagnostic, không phải chiến lược đầu tư ex-ante.
- Tái dùng raw evidence chỉ khi identity liên quan khớp hoặc có compatibility
  receipt được review đúng phạm vi; source mới không tự kế thừa qualification cũ.
  Không bỏ source binding để ép `--reuse-from` chạy được.
- Timeout/OOM cục bộ: ghi INCOMPLETE, không coi là strategy thua; tiếp tục chỉ khi
  manifest cho phép. Source/data/contract drift: dừng toàn batch, bảo toàn evidence.
- Nếu đo tương quan, align timestamp/mask trước; không cắt hai mảng cùng độ dài
  rồi coi là cùng thời điểm.

## Evidence card mẫu (một card cho một câu hỏi)

```text
Question / scope / manifest hash:
Source + dirty identity / dataset hash / UTC window / trial-history:
Arms: DONE | NOT_RUN | INCOMPLETE | INVALID (lý do):
Parity + execution assumptions + khác biệt với runtime:
Metrics: net return %, DD magnitude %, trades, fees quote currency,
         mean/peak exposure %, time-in-market %, concentration:
Raw ledger / independent arithmetic / protected hashes:
Uncertainty: selected window? open positions? overlapping folds? effective trials?
Engineering verdict:
Economic verdict:
Qualification/authority verdict:
Independent reviewer + evidence checked / pending:
Exactly one next action + output + stop condition:
```

Chưa chạy thì metrics=`null`, không ghi 0. Lặp cùng seed không tăng số thử nghiệm
độc lập. DSR/PBO chỉ báo cáo nếu đủ trial history và đúng giả định; không tạo con
số confidence từ vài trade. Kết quả “chưa đủ bằng chứng” là kết luận hợp lệ.

## Nhịp làm việc cho agent

Mỗi vòng: đọc tracker → kiểm bằng chứng → chọn một thiếu sót → sửa/đo trong scope
→ kiểm lại → cập nhật card/tracker. Không tối ưu tham số sau khi xem window rồi
đổi nhãn thành OOS. Thay đổi promotion/gate phải chạy AC01–AC15 theo AGENTS.md.
Checklist này không cấp quyền chạy campaign; quyền thực thi phải được duyệt riêng.

Case đầu tiên và bước tiếp hiện tại: [BNB card](BNB_ADX25_EVIDENCE_CARD.md).
