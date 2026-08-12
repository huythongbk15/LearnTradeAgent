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
| Binance Spot — protective stop (P0.1) | yes | yes | acceptance pending | **NO** |
| Binance Spot — order lifecycle (P0.2) | yes | yes | testnet partial | **NO** |
| Trusted time & market data (P0.3) | yes | yes | testnet partial | **NO** |
| Alpaca Paper (LiveBroker) | yes | yes | paper validated (live paper từ 2026-08-08) | N/A (paper) |
| OANDA (forex) | yes | partial | not validated | **NO** |
| DEX (Uniswap V3 / Jupiter / PancakeSwap) | yes | integration/dry-run | not validated | **NO** |
| Futures / Options adapters | yes | integration | not validated | **NO** |
| Order router / smart routing | yes | integration | not validated | **NO** |
| Portfolio optimizer (BL / HRP / risk parity) | yes | yes | research only | **NO** |
| Strategy plugin marketplace + sandbox | yes | yes | dry-run | **NO** |
| Adaptive ML (regime / online / meta) | yes | yes | research only | **NO** |
| Multi-region (K8s) | yes | dry-run | no real cluster validation | **NO** |
| Chaos engineering | yes | dry-run | no production validation | **NO** |
| Event sourcing + messaging | yes | yes | dry-run | **NO** |
| Monitoring (metrics server, alerts) | yes | partial | not wired to live runner (P1.3) | **NO** |
| CI/CD + supply chain (Trivy/SBOM/sign) | yes | CI runs | artifacts on master | **NO** (mainnet) |
| Docker image (runtime) | yes | CI build + smoke | not soak-tested | **NO** |

## Ghi chú

- **Alpaca Paper** được đánh dấu "paper validated" vì có vận hành paper liên tục nhiều ngày
  (equity ~$95-100k, position sync, fill real qua Alpaca paper endpoint). Đây KHÔNG phải production
  validated — không có vốn thật.
- **Binance Testnet** đã có lệnh SELL filled thành công (P0.3 testnet execute) nhưng chưa đạt
  acceptance gates (P3 trong LIVE_TRADING_TODO.md: 30 ngày soak, 100 lifecycles, 0 unexplained
  failures...).
- **Backtest metrics không nằm trong matrix này** — chúng là research evidence, xem
  [`RESEARCH_EVIDENCE.md`](RESEARCH_EVIDENCE.md). Backtest pass KHÔNG = production validated.

## Cách đọc

`Production = NO` nghĩa là: dù code có tồn tại và test pass, việc dùng nó với vốn thật
chưa được chứng minh. Trạng thái này chỉ đổi sau khi các release gates trong
`LIVE_TRADING_TODO.md` hoàn thành và có operator approval rõ ràng.
