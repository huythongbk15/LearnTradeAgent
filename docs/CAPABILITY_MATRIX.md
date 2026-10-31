# Capability Matrix

> Nguồn chân lý: **executable code → tests → CI evidence → testnet/paper validation → documentation**.
> File này thay thế các dấu ✅ đơn lẻ trong README/docs bằng một mức độ trưởng thành có thể kiểm chứng.
> Live-readiness chi tiết: [`LIVE_TRADING_TODO.md`](LIVE_TRADING_TODO.md) — Mainnet status: **NO-GO**.

## Maturity levels

| Level | Định nghĩa |
| --- | --- |
| Implemented | Code tồn tại trong repo (có thể import/chạy). |
| Unit Tested | Có unit tests chạy trong CI. |
| Integration Tested | Có integration tests chạy trong CI (không cần cluster/exchange thật). |
| Dry-run Validated | Chạy được ở chế độ dry-run (không tác động bên ngoài). |
| Testnet/Paper Validated | Đã chạy với môi trường testnet/paper và có evidence ghi nhận. |
| Production Validated | Đã chạy với vốn thật sau khi vượt toàn bộ release gates — **hiện không capability nào đạt mức này**. |

## Matrix

| Capability | Implemented | Tests | External Validation | Production |
| --- | --- | --- | --- | --- |
| Binance Spot (CCXT) | yes | yes | testnet partial (P0.3 execute filled) | **NO** |
| Execution Simulator V2 (event-driven, P&L attribution, Reality Gap) | yes | yes | research only | **NO** |
| Event-sourced execution lifecycle (14 events, SQLite append-only, replay, snapshot/restore checksum) | yes | yes | dry-run | **NO** |
| Trading invariant chaos tests (9 invariants × 16 fault injections) | yes | yes | dry-run | **NO** |
| Shadow Mainnet Mode (real data/config/rules, NO order submission, hard guards) | yes | yes | dry-run | **NO** |
| Research governance (StrategyArtifact, promotion lifecycle, uncertainty gate, abstention, drift, trials) | yes | yes | research only | **NO** |
| Nested purged/embargoed outer/inner walk-forward + net cost accounting | yes | yes | deterministic/synthetic diagnostics only | **NO** |
| Append-only experiment registry + content-addressed feature artifacts | yes | yes | local research validation | **NO** |
| Canonical frozen Forecast → RiskDecision → TargetExposure contract | yes | yes | same-logic environment contract tested | **NO** |
| Eight-stage content-addressed research promotion ladder | yes | yes | no artifact has completed empirical ladder | **NO** |
| Đường research → runtime (EvidenceArtifact → ladder → PromotionStateStore → resolver) | một phần: gate, hook và store có; không có producer | chỉ test với fixture | chưa từng chạy cho strategy thật | **NO** |
| Runtime resolver theo tên strategy | 7 trong 20 strategy canonical | yes | chưa có artifact testnet nào | **NO** |
| Train-only probability calibration + split conformal intervals | yes | yes | synthetic independent-test benchmark only | **NO** |
| Distributional drift + execution-quality drift | yes | yes | synthetic/unit fixtures only | **NO** |
| Simulator calibration provenance (synthetic/testnet/shadow/live) | yes | yes | synthetic remains HEURISTIC; empirical dataset absent | **NO** |
| Distributional reality gap (quantiles/CVaR/Wasserstein) | yes | yes | unit fixtures; held-out exchange evidence absent | **NO** |
| Binance Spot — protective stop (P0.1) | yes | yes | acceptance pending | **NO** |
| Binance Spot — order lifecycle (P0.2) | yes | yes | testnet partial | **NO** |
| Trusted time & market data (P0.3) | yes | yes | testnet partial | **NO** |
| Alpaca Paper (LiveBroker) | yes | yes | paper validated lịch sử (08/2026); hiện không chạy, runner chặn BUY từ 2026-08-22 | N/A (paper) |
| OANDA (forex) — *Tầng 2, frozen* | yes | partial | not validated | **NO** |
| DEX (Uniswap V3 / Jupiter / PancakeSwap) — *Tầng 2, frozen* | yes | integration/dry-run | not validated | **NO** |
| Futures / Options adapters — *Tầng 2, frozen* | có file nhưng không khởi tạo được: Binance, Bybit và Deribit gọi `CCXTAdapter.__init__` sai tham số (đối chiếu 2026-10-31) | chỉ có test ranh giới import, không có test chức năng | not validated | **NO** |
| Order router / smart routing — *Tầng 2, frozen* | yes | integration | not validated | **NO** |
| Portfolio optimizer (BL / HRP / risk parity) | yes | yes | research only | **NO** |
| Strategy plugin marketplace + sandbox — *Tầng 2, frozen* | yes | yes | dry-run | **NO** |
| Adaptive ML (soft regime posterior / constrained online experts) | yes | yes | research only; adaptive lost fixed baseline on current synthetic benchmark | **NO** |
| Multi-region (K8s) — *Tầng 2, frozen* | yes | dry-run | no real cluster validation | **NO** |
| Chaos engineering — *Tầng 2, frozen* | yes | dry-run | no production validation | **NO** |
| Event sourcing + messaging | yes | yes | dry-run | **NO** |
| Risk decision semantics (RiskDecision: target/max_new/reduce_only) | yes | yes | dry-run | **NO** |
| Unified order permission gate (ALLOW/REDUCE_ONLY/BLOCK) | yes | yes | dry-run | **NO** |
| Effective config secret merge (ENV Telegram for paper/testnet) | yes | yes | dry-run | **NO** |
| Monitoring (metrics server, alerts) | yes | partial | not wired to live runner (P1.3) | **NO** |
| CI/CD + supply chain (Trivy/SBOM/sign) | yes | CI runs | artifacts on master | **NO** (mainnet) |
| Docker image (runtime) | yes | CI build + smoke | not soak-tested | **NO** |

## Phân tầng phạm vi (2026-10-29)

Theo [CORE_RUNTIME_BOUNDARY.md](operations/CORE_RUNTIME_BOUNDARY.md):

- **Tầng 0, lõi runtime:** những gì runner testnet/paper thật sự dùng (execution, exchanges CCXT/Alpaca, authority, code Enhanced MA mà runner dùng để tính state; chưa được promote, BUY bị khóa). Các mục khác chỉ được ưu tiên sau khi lõi này qua L0.
- **Tầng 1, nghiên cứu offline:** backtest, research, calibration, ml, portfolio optimizer. Giữ nguyên, nhưng runner không được import.
- **Tầng 2, frozen:** các dòng đánh dấu *Tầng 2, frozen* ở trên, cùng các package `features` (không được import ở đâu), `enterprise`, `messaging`, `infrastructure`, `online_learning`. Không phát triển thêm và không xóa ở bước này.

`tests/test_runtime_import_boundary.py` thi hành ranh giới: runner không được nạp Tầng 1/Tầng 2. Các module Tầng 2 còn bị nạp qua `__init__` của package được theo dõi bằng xfail strict.

## Ghi chú

- **Alpaca Paper** được đánh dấu "paper validated" vì có vận hành paper liên tục nhiều ngày
  (equity ~$95-100k, position sync, fill real qua Alpaca paper endpoint). Đây KHÔNG phải production
  validated — không có vốn thật. Đó là evidence lịch sử từ 2026-08-08. `scripts/live_enhanced_ma.py`
  chặn mọi BUY từ 2026-08-22 vì không có promoted artifact. File cuối cùng runner này ghi có ngày
  2026-08-29; cron không chạy và Task Scheduler không có task nào cho Alpaca.
- **Binance Testnet** đã có lệnh SELL filled thành công (P0.3 testnet execute) nhưng chưa đạt
  acceptance gates (P3 trong LIVE_TRADING_TODO.md: 30 ngày soak, 100 lifecycles, 0 unexplained
  failures...).
- **Backtest metrics không nằm trong matrix này** — chúng là research evidence, xem
  [`RESEARCH_EVIDENCE.md`](RESEARCH_EVIDENCE.md). Backtest pass KHÔNG = production validated.
- Methodology benchmark results are explicitly `SYNTHETIC_DIAGNOSTIC`. They cannot
  promote a model or execution algorithm. See [`RESEARCH_METHODOLOGY.md`](RESEARCH_METHODOLOGY.md).
- MPC is presently a deterministic feasibility layer. Without a calibrated impact
  model and held-out order-level exchange dataset, there is no valid MPC > TWAP/POV claim.

## Cách đọc

`Production = NO` nghĩa là: dù code có tồn tại và test pass, việc dùng nó với vốn thật
chưa được chứng minh. Trạng thái này chỉ đổi sau khi các release gates trong
`LIVE_TRADING_TODO.md` hoàn thành và có operator approval rõ ràng.
