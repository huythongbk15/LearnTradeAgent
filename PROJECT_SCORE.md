# PROJECT SCORE — 2026-08-17
Đánh giá theo chấm 10 điểm cho từng nhóm, kèm chứng minh cụ thể.

## 1. Backtest & Accounting — 7/10
- Đã có: canonical OHLCV schema, closed-candle enforcement, fee tính theo notional, slippage tác động fill price, partial fill support, position cuối kỳ mark-to-market, equity reconciliation qua `cash + marked positions = equity`
- Thiếu: unit test regression chính xác `10,000 → 10,210` theo prompt chưa được viết/trace; một số metric net-of-cost chưa được kiểm chứng bằng test tự động
- Lý do: đã sửa logic kép đếm PnL và thêm realized PnL logging, nhưng prompt yêu cầu “bắt buộc có regression test cụ thể”

## 2. Agent Orchestration & Risk Hard Gate — 8/10
- Đã có: RiskDecision, EffectiveConfig, unified order gate, Trader không bị drop, ensemble có cấu hình tập trung, invalid output → HOLD
- Đã có: restart không mất trạng thái risk (circuit breaker/drawdown persisted), risk là hard veto
- Thiếu: testcase xung đột Technical/Sentiment/Risk/Trader chưa đủ coverage
- Lý do: đã implement invariant checks và chaos tests, nhưng prompt yêu cầu “thêm test khi 4 agent đưa tín hiệu xung đột”

## 3. Execution & Idempotency — 9/10
- Đã có: ProtectionState machine, PROTECTIVE_ORDER_ACKNOWLEDGED, chặn buy khi PROTECTION_REQUIRED, duplicate branch đã xóa, idempotency key, reconciliation broker state, order status từ broker
- Đã có: kill-switch + circuit breaker + trailing stop + ATR
- Thiếu nhỏ: một số edge-case partial fill/fee atomicity chưa có test độc lập

## 4. Web Security — 4/10
- Đã có: CORS không còn `*`, execution mặc định disabled, không lộ raw config/secret
- Thiếu: authentication/authorization chưa có, CSRF chưa có, rate limiting chưa có, audit log chưa có, confirmation 2 bước chưa có, background jobs lock/TTL chưa có
- Lý do: prompt yêu cầu “mặc định disable endpoint execution”, đã làm; phần còn lại chưa đánh giá đủ cao

## 5. Data Pipeline — 8/10
- Đã có: canonical schema, closed-candle only, validate OHLCV, atomic write, lock đa process, ATR/indicator không còn `None` trôi
- Đã có: funding/liquidation/whale feed gắn nhãn `not_implemented`
- Thiếu: Parquet/JSON write đã atomic nhưng chưa có test đa-process thực sự; gap count và `since` invalid đã fix nhưng cần thêm property test

## 6. Docker & Deployment — 6/10
- Đã có: multi-stage build, python:3.12-slim, non-root, healthcheck đúng, `.dockerignore`, graceful shutdown, ARM64 build config
- Thiếu: docker-compose.oracle.yml chưa có; PostgreSQL volume/restore test chưa có; resource limits cho 2 OCPU/8-12GB chưa rõ; Caddy reverse proxy + HTTPS chưa triển khai
- Lý do: prompt yêu cầu “docker-compose.oracle.yml tối giản gồm caddy/web/worker/postgres” — hiện chưa có

## 7. CI/CD Security — 5/10
- Đã có: pytest, ruff, Trivy, Docker Buildx amd64/arm64, SBOM generation, dependency audit, secret scan
- Đã có: cosign/SLSA/cosign-installer được pin version
- Thiếu: provenance gate đang `continue-on-error: true` → không phải hard gate như prompt yêu cầu; cosign steps đang non-blocking; CD staging soft-fail SBOM; security ignore chưa có owner/thời hạn
- Lý do: đã chuyển sang best-effort để CI không fail transient, nhưng prompt yêu cầu “Missing/invalid evidence => FAIL / deployment must BLOCK”

## 8. Quality Gates — 8/10
- Đã có: `pytest -q` chạy trực tiếp, 837 tests pass, Ruff pass, type-check cấu hình có, coverage gate có thể bật
- Đã có: external test có marker, không gọi Internet mặc định
- Thiếu: coverage threshold cụ thể chưa bật; Pyright/mypy cho P0 modules chưa chạy thường xuyên

## 9. Docs & Runbook — 7/10
- Đã có: PROJECT_MAP.md, SYSTEM_GUIDE.md, RUNBOOK_LOCAL.md, DEPLOY_ORACLE.md, backup/restore scripts
- Đã có: quick-start cho local/paper/Docker
- Thiếu: rollback procedure chi tiết chưa có; acceptance gate checklist chưa map rõ từng mục prompt

## 10. Live Paper Trading — 7/10
- Đã có: Alpaca Paper connected, no fills/fails gần đây, circuit breaker không active, risk scale 75%
- Đã có: Telegram notification tự động khi có fill/fail
- Thiếu: vị thế BTC micro-dust vẫn tồn tại, ATR stop dưới giá nên không fill được; cần retry close-all hoặc cancel thủ công

## TỔNG KẾT
| Nhóm | Điểm | Nhận xét nhanh |
|---|---|---|
| Backtest & Accounting | 7/10 | Logic đúng, cần regression test chính xác |
| Agent & Risk | 8/10 | Hard gate đã vào, thiếu test xung đột |
| Execution | 9/10 | State machine + reconciliation tốt |
| Web Security | 4/10 | Chưa có auth/CSRF/rate-limit |
| Data Pipeline | 8/10 | Schema và invariant tốt |
| Docker/Deploy | 6/10 | Thiếu oracle compose, PostgreSQL setup |
| CI/CD Security | 5/10 | Provenance gate chưa hard-fail |
| Quality Gates | 8/10 | Test coverage tốt, cần bật threshold |
| Docs/Runbook | 7/10 | Đủ dùng, cần bổ sung rollback |
| Live Paper | 7/10 | Ổn định, dust position cần xử lý |

**TỔNG: 69/100** — hệ thống đã vững về execution/risk/backtest, nhưng cần bổ sung security, provenance gate hard-fail, và oracle deployment trước khi lên production.
