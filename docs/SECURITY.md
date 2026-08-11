# Security

## Credentials

- **Không commit secret.** Template: [`.env.example`](../.env.example) — không chứa secret thật.
- Phân loại: LLM credentials · Binance testnet · Binance mainnet (optional, **disabled by
  default**) · Alpaca · Telegram.
- Local: `.env` / `.env.local` (gitignored). CI: GitHub secrets.
- Mainnet credentials phải optional; mặc định tắt.

## Supply chain

- Dockerfile base images pin theo digest (`python:3.12-slim@sha256:...`,
  `node:24-alpine@sha256:...`).
- CI: dependency audit (pip-audit), Trivy image scan (CRITICAL, fail on exit-code 1), SBOM
  (SPDX) + cosign sign/attest, secret scan trong repo, YAML/compose validate.
- GitHub Actions pinned theo commit SHA (không dùng mutable tag).
- Không ghi "security passed" nếu job bị skipped — xem CI run thực tế.

## Live safety (tóm tắt)

- Trusted time & market data: quote age, request latency, exchange clock skew, WS sequence gap,
  stale candle/orderbook rejection — fail-closed (P0.3).
- Execution: client order ID idempotency, unknown status → fail closed, timeout → reconciliation,
  partial fill accounting, protective order coverage, kill switch (chặn entry, cho phép
  risk-reducing exit).
- Chi tiết: [`LIVE_TRADING_TODO.md`](LIVE_TRADING_TODO.md).

## Hardening còn thiếu (đã ghi nhận)

- Pin digest cho mọi infra service image trong compose (P0.6).
- Vault/secret manager cho production (hiện dùng Docker secrets / CI secrets).
- Independent alerting supervision (P1.3).
