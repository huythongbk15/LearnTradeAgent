# Core System — Luồng cốt lõi của Trading Agent

> **Status:** CURRENT
> **Owner:** Trading systems
> **Verified:** 2026-08-31
> **Evidence:** [Capability Matrix](CAPABILITY_MATRIX.md), [Main-flow Validation](operations/MAIN_FLOW_VALIDATION.md), tests trong `tests/`

Đây là tài liệu định hướng duy nhất cho câu hỏi: **hệ thống này thực sự làm gì, quyết định được tạo ra ở đâu và khi nào một lệnh được phép đi ra ngoài**. Các tài liệu chi tiết, roadmap và khóa học đều phải được đọc sau trang này.

## 1. Sản phẩm cốt lõi

Trading Agent là một hệ thống giao dịch có bằng chứng và fail-closed. Nó biến dữ liệu thị trường thành **forecast → quyết định rủi ro → target exposure → order intent**, nhưng chỉ cho phép gửi lệnh khi dữ liệu, strategy, policy, quyền hạn và môi trường đều hợp lệ.

Mục tiêu không phải là “tìm strategy thắng tuyệt đối”. Mục tiêu là một vòng lặp có thể tái lập, giải thích, giới hạn rủi ro và rollback:

```text
market data
  → quality gate + point-in-time features
  → canonical strategy / forecast
  → deterministic risk decision + target exposure
  → authority + shared-capital allocation
  → order permission + execution plan
  → lifecycle / broker / fills / reconciliation / protection
  → attribution + monitoring + evidence + promotion
```

Nếu một mắt xích thiếu bằng chứng hoặc không xác định được identity, kết quả hợp lệ là `NO_TRADE`, `BLOCK` hoặc `REDUCE_ONLY`; không được tự động đoán thay thế.

## 2. Phạm vi và ranh giới

### Bắt buộc trong core

- Một data manifest và quy tắc point-in-time, không look-ahead.
- Registry strategy canonical với identity (strategy, params, code SHA).
- Backtest/tournament có cell accounting, cost model và execution health.
- Evidence → selection policy bất biến → promotion có actor/ticket/audit.
- Authority chain fail-closed trước khi tạo order intent.
- Shared capital, risk budget, protective-order và kill-switch semantics.
- Event-sourced order lifecycle, reconciliation và attribution.
- Evidence cho phép replay, rollback và quyết định release.

### Không thuộc core execution

LLM/agents, options, DEX/futures adapters, portfolio optimizers, web dashboard, messaging bus, multi-region/Kubernetes và các thử nghiệm online-learning là **bề mặt mở rộng**. Chúng chỉ được nối vào core qua contract đã kiểm thử; không được trở thành một đường đặt lệnh thứ hai.

## 3. Bản đồ thành phần chuẩn

| Trách nhiệm | Điểm vào chuẩn | Kết quả phải có |
| --- | --- | --- |
| Nạp và kiểm tra dữ liệu | `src/trading_agent/data/`, `src/trading_agent/features/` | dataset/feature manifest, quality decision |
| Strategy và forecast | `src/trading_agent/strategies/canonical/`, `src/trading_agent/research/forecast.py` | forecast đóng băng, target exposure |
| Đánh giá ứng viên | `src/trading_agent/backtest/tournament.py`, `src/trading_agent/backtest/portfolio_backtest.py` | evaluation artifact, ledger, attribution |
| Chọn và promote | `src/trading_agent/research/selection_policy.py`, `src/trading_agent/research/promotion.py` | policy/artifact bất biến, audit trail |
| Quyền hạn runtime | `src/trading_agent/authority/resolver.py`, `src/trading_agent/authority/adaptive_router.py` | `ALLOW` / `REDUCE_ONLY` / `BLOCK`, routing decision |
| Phân bổ danh mục | `src/trading_agent/authority/portfolio.py`, `src/trading_agent/risk/` | exposure/risk budget dùng chung |
| Lập và thực thi lệnh | `src/trading_agent/execution/engine.py`, `src/trading_agent/execution/lifecycle/` | order intent, lifecycle events, fills |
| Theo dõi và bằng chứng | `src/trading_agent/monitoring/`, `docs/reference/EVIDENCE_ARTIFACTS.md` | metrics, reconciliation, release record |

Các CLI/script được xem là adapter, không phải nơi định nghĩa business truth:

- `python -m trading_agent.cli` — giao diện thao tác và health check.
- `scripts/full_system_backtest.py` — replay toàn vòng với strategy artifact; adaptive routing chỉ bật khi truyền đủ dependency.
- `scripts/run_strategy_tournament.py` — ma trận cell có `--dry-run` và `--tail-bars`.
- `scripts/run_canonical_strategy.py` — chạy một strategy canonical có identity rõ ràng.
- `scripts/run_wfo_evidence.py` — tạo research evidence; không tự promote.

## 4. Các invariant không được phá

1. **Không nhìn trước tương lai:** mọi feature, fold và holdout phải giữ thứ tự thời gian.
2. **Không có strategy trực tiếp tới broker:** strategy chỉ phát forecast/target; permission và lifecycle mới quyết định lệnh.
3. **Fail closed:** dữ liệu stale/gap, artifact không hợp lệ, policy hết hạn, unknown order hoặc thiếu protective coverage đều chặn exposure mới.
4. **Một đường order duy nhất:** mọi adapter (paper, testnet, broker) đi qua cùng permission, planner, lifecycle và reconciliation.
5. **Vốn dùng chung:** nhiều pair không được cộng cơ học từ các backtest độc lập; phải dùng allocator và risk budget chung.
6. **Abstention là kết quả hợp lệ:** không có winner đủ bằng chứng thì giữ incumbent hoặc `NO_TRADE`.
7. **Promotion không nhảy stage:** mỗi bước cần evidence, actor, ticket và audit; rollback tạo artifact/policy mới.
8. **Provenance đi cùng kết quả:** code, config, data, feature, strategy params, cost model và image/release identity phải truy được.

## 5. Môi trường và mức trưởng thành

```text
DESIGNED → IMPLEMENTED → TESTED → RESEARCH_VALIDATED
                                  → PAPER_VALIDATED → TESTNET_VALIDATED
                                                        → PRODUCTION_VALIDATED
```

Backtest hoặc unit test chỉ chứng minh phần tương ứng. `PAPER_VALIDATED` không phải vốn thật; `PRODUCTION_VALIDATED` hiện **chưa đạt cho capability nào**. Mainnet giữ `NO-GO` cho tới khi [Live Readiness](LIVE_TRADING_TODO.md) và [S7 Operational Evidence Runbook](S7_OPERATIONAL_EVIDENCE_RUNBOOK.md) có đủ evidence thực tế.

## 6. Các luồng người dùng chuẩn

| Mục tiêu | Điểm bắt đầu | Kỳ vọng |
| --- | --- | --- |
| Kiểm tra repo/CLI | [Getting Started](getting-started.md), `python -m trading_agent.cli --help` | không gửi lệnh, chỉ đọc cấu hình/help |
| Kiểm tra main flow | [Main-flow Validation](operations/MAIN_FLOW_VALIDATION.md) | smoke → matrix → replay → operational evidence |
| So sánh strategy | [Research-to-Production](guides/RESEARCH_TO_PRODUCTION.md) | tournament/WFO artifact, không tự promote |
| Chạy replay full-system | `scripts/full_system_backtest.py --help` | output cô lập, ledger và routing report |
| Vận hành paper/testnet | [Live Trading Runbook](LIVE_TRADING_RUNBOOK.md) | environment gate, reconciliation, rollback |
| Đọc trạng thái release | [Capability Matrix](CAPABILITY_MATRIX.md), [Adaptive Roadmap Status](ADAPTIVE_ROADMAP_STATUS.md) | phân biệt code-complete với soak evidence |

Mọi lệnh dài phải chạy qua controlled execution của workspace; xem [Documentation Standard](DOCUMENTATION_STANDARD.md). Không đặt lệnh mainnet trong tutorial hoặc ví dụ mặc định.

## 7. Sự thật hiện tại cần giữ trong mọi tài liệu

- P0 và capability code S0–S6 đã có contract/test tương ứng; điều đó không tự đóng S7.
- Adaptive routing là opt-in và phải có router, posterior provider và runtime provider; không được fallback im lặng về strategy khác.
- S2-0208 (timeout/retry độc lập cho từng tournament cell) và S4-0403 (dọn compatibility adapter `ArtifactLifecycle`) vẫn là nợ kỹ thuật đã ghi nhận.
- S7 cần testnet/shadow/canary thực tế, calibration, lifecycle evidence và release attestation (cosign/SBOM/SLSA); không thay bằng fixture hay số tổng hợp.
- Full-repo mypy còn nợ legacy; chỉ các module đã nêu trong status report được kiểm tra sạch.

## 8. Nguyên tắc mở rộng sau này

Trước khi thêm module hoặc phase mới, phải trả lời được bốn câu hỏi:

1. Nó thay đổi bước nào trong flow ở trên?
2. Contract và invariant nào bảo vệ thay đổi đó?
3. Evidence nào chứng minh không làm suy yếu đường order duy nhất?
4. Nếu evidence thiếu, hệ thống sẽ abstain/rollback ở đâu?

Nếu chưa trả lời được, nội dung nên nằm ở `TARGET` hoặc `archive`, không đưa vào core runtime.

## 9. Đọc tiếp

- [Documentation Map](DOCUMENTATION_MAP.md) — taxonomy và thứ tự đọc.
- [Architecture](ARCHITECTURE.md) — mô tả chi tiết các plane và boundary.
- [Strategy Lifecycle](architecture/STRATEGY_LIFECYCLE.md) — contract research → runtime.
- [Backtest Engine](BACKTEST_ENGINE.md) — execution simulation và attribution.
- [Promotion Binding](PROMOTION_BINDING.md) — provenance/promotion binding.
- [Runtime Resolver](RUNTIME_RESOLVER.md) — authority fail-closed.
- [Capability Matrix](CAPABILITY_MATRIX.md) — mức trưởng thành có thể kiểm chứng.
