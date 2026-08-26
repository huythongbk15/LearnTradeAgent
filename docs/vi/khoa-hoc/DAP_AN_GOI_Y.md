# Đáp án và gợi ý tự kiểm tra

> Chỉ mở sau khi đã ghi câu trả lời của bạn. Đây là gợi ý kiểm tra reasoning,
> không phải đáp án code duy nhất.

## Bài 00

- `pyproject.toml` chứa package/test/tool configuration chính.
- Canonical registry ở `src/trading_agent/strategies/canonical/registry.py`.
- Report contract ở `src/trading_agent/backtest/report_v2.py` và schemas directory.
- Tournament CLI ở `scripts/run_strategy_tournament.py`.
- Mainnet status phải đọc live readiness/capability matrix hiện hành.
- Một targeted test pass chỉ chứng minh phạm vi test đó ở commit/environment đó.

## Bài 01

- Research đề xuất evidence; promotion/control plane cấp eligibility; risk/execution
  mới có authority đi tới order.
- `OrderIntent` chưa phải broker order.
- Timeout là uncertainty, không phải terminal state.
- Thiếu promotion phải reject/abstain; dùng default strategy là fail-open.
- Audit flow cần store/event/correlation, không chỉ function calls.

## Bài 02

Future-leak example tại open `04` chỉ dùng closes `01,02,03` cho MA(3):

```text
(101 + 102 + 99) / 3 = 100.666...
```

Dùng close `04=110` làm MA thành `(102+99+110)/3 = 103.667`, tức đã dùng thông
tin chưa biết tại thời điểm quyết định. Row count bằng nhau không chứng minh content
giống nhau; cần content-level identity phù hợp.

## Bài 03

- Descriptor mô tả contract/identity; implementation tạo behavior.
- Registry là allowlist và integrity boundary.
- Params-binding evidence mạnh là fixture mà chỉ thay param, giữ input cố định và
  quan sát decision/indicator thay đổi đúng dự kiến.
- `NO_TRADE` nên có reason; signal `0` không reason có thể trộn thiếu data với chủ
  động đứng ngoài.
- Parity theo từng bar phát hiện timing/leakage mà tổng return có thể che mất.

## Bài 04

- Return luôn đọc cùng benchmark/drawdown/cost/sample.
- PF > 1 nhưng total return âm có thể do mark-to-market, open trade, sizing, fee,
  distribution hoặc report semantics; cần ledger để kết luận.
- Golden replay chứng minh determinism với cùng input, không chứng minh generalization.
- Partial fill phải dùng actual quantity và fee, không planned quantity.

## Bài 05

- Expected cells là tích các chiều matrix.
- `FAILED` có identity/reason nên phân tích được; missing là evidence bị mất.
- Artifact progress đáng tin hơn PID sống.
- Params hash đổi chưa chứng minh computation đổi.
- Tournament chỉ là raw evaluation; selection cần WFO, uncertainty, trials, hard gates.

## Bài 06

Worksheet raw evaluations:

```text
5 × 10 × 8 × 3 × 6 = 7,200
```

Candidate B ổn định nhất trong bảng ví dụ; A/C có tail/fold instability. Nếu
incumbent đạt 1.8% ổn định, B có thể chỉ đáng chọn khi improvement sau costs và
uncertainty đủ rõ. `KEEP_INCUMBENT` hoặc `NO_SELECTION` đều hợp lệ.

## Bài 07

- Selection trả lời “evidence hỗ trợ gì”; promotion trả lời “environment được phép
  chạy gì”.
- Environment mismatch, expiry, revocation hoặc content mismatch đều phải fail closed.
- Rollback chọn lại policy/promotion đã biết, không sửa historical artifacts.
- Cache cần cơ chế kiểm tra version/revocation/expiry.

## Bài 08

- `CANCEL_REQUESTED` không terminal; late fill vẫn hợp lệ.
- Submit timeout cần reconcile/idempotency, không retry mù.
- Reservation chỉ release theo terminal/actual evidence.
- `PROTECTED` cần external durable ACK và quantity hợp lệ.
- Restart cần broker reconciliation trước resume.

## Bài 09

Với hysteresis example, trend không được enter tại `0.72` nếu chỉ có một kỳ >=0.70;
đến chuỗi `0.75, 0.77` mới đủ hai kỳ liên tiếp, subject to dwell/entropy rules.
Posterior `0.61` chưa đủ exit vì policy yêu cầu dưới `0.60` hai kỳ.

Portfolio có authority cao hơn router: router đề xuất candidate/target, allocator
và risk constraints giới hạn hoặc reject exposure.

## Bài 10

- Process sống nhưng artifact/data stale → block new exposure và điều tra.
- Broker timeout → preserve correlation, reconcile, chờ terminal/external evidence.
- Signature/SBOM/SLSA chứng minh supply-chain identity, không chứng minh alpha.
- CI green không đóng shadow/testnet/canary soak.
- Rollback phải bao phủ code, config/policy, state compatibility và exposure.

## Capstone

Một capstone tốt thường kết luận:

```text
CONTINUE_RESEARCH hoặc INSUFFICIENT_EVIDENCE
```

Mini tournament không đủ production promotion. Điểm cao đến từ lineage rõ,
matrix accounting đầy đủ, uncertainty trung thực, failure handling và next
experiment có khả năng falsify hypothesis.

