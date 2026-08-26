# Bài 01 — Kiến trúc hệ thống và chuỗi authority

> Mức độ: cơ bản · Thời lượng: 3–4 giờ · Trạng thái: **HIỆN HÀNH**

## Mục tiêu

- Giải thích năm plane và trách nhiệm của từng plane.
- Phân biệt decision, authorization và execution.
- Truy một luồng từ observation đến fill/audit.
- Xác định nơi hệ thống phải fail closed.

## Tài liệu và code cần đọc

1. [Architecture](../../ARCHITECTURE.md)
2. [Vòng đời chiến lược](../VONG_DOI_CHIEN_LUOC.md)
3. `src/trading_agent/authority/decision.py`
4. `src/trading_agent/authority/resolver.py`
5. `src/trading_agent/execution/application.py`
6. `tests/test_e2e_authority_chain.py`

Không cần đọc hết file trong lần đầu. Tìm class/function được test gọi.

## 1. Năm plane

| Plane | Câu hỏi nó trả lời | Không được tự làm |
| --- | --- | --- |
| Research | Strategy có evidence gì? | Tự cấp quyền runtime |
| Decision | Muốn target exposure nào? | Gửi order trực tiếp |
| Execution | Biến intent hợp lệ thành order/fill | Sửa research evidence |
| Control | Environment/policy nào được phép? | Tạo alpha signal |
| Observability | Điều gì đã xảy ra? | Thay đổi financial state âm thầm |

Một agent hoặc strategy có thể đề xuất, nhưng authority chain mới quyết định có
được mở exposure hay không.

## 2. Mental model

```text
MarketObservation
  → StrategyRuntime / forecast
  → StrategyOutput
  → target exposure / OrderIntent
  → risk + permission authorization
  → canonical order plan
  → broker gateway
  → lifecycle events
  → fill ledger + reconciliation
```

Mỗi bước phải có input/output contract và causation identity. Không được gọi
broker từ decision code để “đi đường tắt”.

## 3. Lab có hướng dẫn — Trace bằng test

Chạy:

```bash
.venv/bin/python -m pytest -q tests/test_e2e_authority_chain.py -vv
```

Sau đó đọc test theo thứ tự:

1. Fixture dựng environment/state gì?
2. Input observation/forecast là gì?
3. Artifact/promotion được tạo ở đâu?
4. Resolver trả `StrategyRuntime` như thế nào?
5. Decision biến thành intent ở đâu?
6. Event hoặc store nào làm bằng chứng?

Dùng mẫu `Code-reading trace` để ghi lại.

## 4. Bài tập tự làm

### Bài 01-A — Vẽ authority map

Vẽ sơ đồ có tối thiểu 12 node, gồm:

- data observation;
- strategy descriptor/registry;
- evaluation/promotion;
- runtime resolver;
- risk/permission;
- order planner/gateway;
- lifecycle/store;
- fill/reconciliation;
- audit/monitoring.

Đánh dấu mỗi cạnh là `data`, `decision`, `authorization` hoặc `evidence`.

### Bài 01-B — Tìm ba bypass nguy hiểm

Với mỗi tình huống, chỉ ra invariant bị phá và safe behavior:

1. Strategy gọi `place_order()` trực tiếp.
2. Runtime không thấy promotion nên dùng strategy mặc định.
3. Broker báo timeout và code coi order đã cancel.

### Bài 01-C — Đọc test như specification

Chọn một test trong `tests/execution/test_canonical_pipeline.py`. Viết:

- guarantee test đang bảo vệ;
- setup tối thiểu;
- failure nào test chưa bao phủ;
- test bổ sung bạn sẽ viết.

Không cần sửa test thật ở bài này.

## 5. Lỗi thường gặp

- Đồng nhất agent “Risk” với authority risk enforcement.
- Nghĩ rằng `OrderIntent` là order đã gửi.
- Coi timeout là trạng thái terminal.
- Không phân biệt internal event và external broker ACK.
- Vẽ flow nhưng bỏ qua store/audit evidence.

## Self-check

1. Research plane có được promote trực tiếp không?
2. Ai có authority cao hơn: strategy hay portfolio/risk?
3. Vì sao broker timeout không chứng minh cancel?
4. Một decision cần identity nào để audit?
5. Fail-closed khác fallback strategy thế nào?

## Exit gate

- [ ] Vẽ đủ authority map và giải thích được từng boundary.
- [ ] Trace test e2e từ input đến evidence.
- [ ] Phân tích đúng ba bypass nguy hiểm.
- [ ] Đạt ít nhất 4/5 self-check không xem gợi ý.

Tiếp theo: [Bài 02 — Dữ liệu và point-in-time](02_DU_LIEU.md).

