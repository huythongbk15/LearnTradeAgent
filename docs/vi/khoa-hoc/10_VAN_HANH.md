# Bài 10 — Vận hành, release gates và incident response

> Mức độ: nâng cao · Thời lượng: 4–5 giờ · Trạng thái: **HIỆN HÀNH, mainnet NO-GO**

## Mục tiêu

- Dùng validation ladder L0–L5 đúng phạm vi.
- Phân biệt CI green, artifact provenance và operational readiness.
- Lập staged rollout, rollback và incident drill.
- Hiểu mainnet approval là quyết định evidence-based.

## Tài liệu cần đọc

- [Runbook kiểm tra luồng chính](../KIEM_TRA_LUONG_CHINH.md)
- [Live Trading Runbook](../../LIVE_TRADING_RUNBOOK.md)
- [Operational Drills](../../OPERATIONAL_DRILLS.md)
- [Deployment](../../DEPLOYMENT.md)
- [Security](../../SECURITY.md)
- [Live Readiness](../../LIVE_TRADING_TODO.md)
- [Capability Matrix](../../CAPABILITY_MATRIX.md)
- `.github/workflows/`

## 1. Validation ladder

```text
L0 CLI/import/contracts
L1 single-cell smoke
L2 small matrix
L3 locked full research matrix
L4 replay + stress/fault
L5 shadow/paper/testnet/canary soak
```

Mỗi level chỉ chứng minh phạm vi của nó. Full test suite xanh không thay thế broker
reconciliation hoặc 30-day soak.

## 2. Release evidence bundle

Một release candidate nên liên kết:

- commit/source identity;
- tests và coverage phạm vi quan trọng;
- SBOM;
- SLSA/provenance attestation;
- image/artifact signature;
- vulnerability scan;
- config/policy identity;
- golden replay/tournament evidence;
- deployment manifest;
- rollback target;
- operator approval.

Chữ ký chứng minh artifact/source chain, không chứng minh strategy có lợi nhuận.

## 3. Lab L0/L1

```bash
.venv/bin/python scripts/run_strategy_tournament.py --help
.venv/bin/python -m pytest -q \
  tests/test_backtest_report_v2.py \
  tests/strategies/test_s1_exit_gate.py \
  tests/backtest/test_tournament.py
```

Sau đó chạy dry-run một cell và ghi validation record theo mẫu.

Không gọi live command trong bài học.

## 4. Staged rollout

| Stage | Orders | Mục tiêu | Gate đi tiếp |
| --- | --- | --- | --- |
| Shadow | Không | Decision parity, latency, data health | Zero unexplained divergence |
| Internal paper | Simulated | Lifecycle/cost/fault behavior | Reconcile state và stable metrics |
| Broker paper/testnet | Broker giả lập | Protocol, ACK, rate limit, reconnect | Soak + zero unexplained mismatch |
| Canary | Vốn/phạm vi rất nhỏ | Reality gap và ops readiness | Explicit review/approval |
| Production | Theo limits | Vận hành kiểm soát | Continuous monitoring/revocation |

## 5. Incident drill — Stale data nhưng process vẫn sống

Tình huống:

- process heartbeat bình thường;
- tournament/runtime artifact không tiến triển;
- market data timestamp stale;
- không có crash.

Viết incident record:

1. Detection signal nào đáng tin?
2. Có được mở exposure mới không?
3. Giữ/giảm position hiện tại ra sao?
4. Evidence nào phải preserve?
5. Khi nào được resume?

Safe default: block new exposure, xác minh data/reconciliation, không restart mù để
xóa triệu chứng.

## 6. Incident drill — Broker timeout sau submit

Bạn không biết order đã được broker nhận hay chưa.

Không làm:

- retry không idempotency;
- coi order failed;
- release reservation;
- gửi order đối ứng để “sửa”.

Phải:

- giữ correlation/request evidence;
- query/reconcile broker state;
- xử lý late ACK/fill;
- alert nếu uncertainty kéo dài;
- chỉ resume sau terminal/external evidence.

## 7. Rollback plan

Một rollback plan tốt trả lời:

- trigger định lượng;
- ai có authority;
- policy/artifact trước đó;
- action với exposure/order đang mở;
- schema/state compatibility;
- reconciliation sau rollback;
- proof trước khi resume.

## 8. Bài tập review CI/CD

Chọn workflow provenance/staging trong `.github/workflows/` và lập bảng:

| Gate | Input | Output evidence | Fail behavior | Có chặn deploy? |
| --- | --- | --- | --- | --- |

Kiểm tra riêng signature, SBOM và SLSA attestation. Không kết luận xanh nếu chỉ một
job pass hoặc evidence bị skip.

## 9. Lỗi thường gặp

- Health check chỉ kiểm PID.
- Alert nhưng vẫn mở exposure mới.
- Rollback code nhưng không rollback policy/config.
- Restart trước khi preserve evidence.
- Dùng stale CI badge làm trạng thái hiện tại.
- Mainnet approval nằm trong env var nhưng không có operator evidence.

## Exit gate

- [ ] Hoàn thành L0/L1 validation record.
- [ ] Viết hai incident records.
- [ ] Lập staged rollout với gate định lượng.
- [ ] Review một CI workflow theo evidence table.
- [ ] Viết rollback plan có exposure/reconciliation handling.

Tiếp theo: [Bài 11 — Capstone](11_CAPSTONE.md).

