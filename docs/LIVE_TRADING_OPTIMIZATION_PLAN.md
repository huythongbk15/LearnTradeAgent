# Live trading optimization checklist

This checklist is ordered by failure severity. A completed engineering item is
not, by itself, permission to trade real money. Mainnet remains NO-GO until the
testnet soak and operational acceptance gates in `LIVE_TRADING_RUNBOOK.md` pass.

## P0 — execution correctness

- [x] Enforce one live runner per state/account with a cross-platform process lock.
- [x] Persist an order lifecycle ledger and reconcile unfinished client order IDs.
- [x] Stop a batch on unknown, open or partially-filled orders.
- [x] Separate entry filters from risk-reducing exits and add persistent ATR protection.
- [x] Generate evidence with the exact live allocations and risk parameters.
- [x] Validate portfolio-level OOS results, not symbols in isolation only.
- [x] Reject stale, gapped or malformed hourly evidence data.
- [x] Reject stale, gapped, unordered or malformed live Binance candles.
- [x] Apply exchange amount precision and Binance min/max amount/notional filters.
- [x] Reject excessive spread, insufficient order-book depth and excessive book slippage.
- [x] Apply the same market-quality limits on testnet and mainnet.

## P1 — operational hardening

- [x] Bind risk state and evidence to the account, strategy, symbols and build version.
- [x] Protect state/evidence files with restrictive permissions and integrity metadata.
- [x] Add durable structured execution/reconciliation/run-heartbeat audit events.
- [x] Add a supervisor-friendly watchdog for stale, failed and unresolved runs.
- [ ] Connect local audit events to an independent pager and test alert delivery.
- [x] Cover duplicate processes, timeout-after-accept, partial fills and restart recovery.
- [x] Raise critical live-path coverage to at least 75% and enforce it in CI.
- [x] Fix the Phase 6 manifest job and align GitHub workflows on the `master` branch.
- [x] Pin third-party GitHub Actions and tighten production image identity verification.
- [x] Publish SHA-tagged images with an OIDC signature and signed SPDX SBOM attestation.
- [x] Replace the tracked root credential manifest with a commit-safe example.
- [ ] Define and acceptance-test the actual staging service, health endpoint and
  database migration command; the current Compose image defaults to CLI help.
- [ ] Repair and acceptance-test the production blue/green topology; the current
  workflow references blue/green services that are not defined by the Compose files.

## Release gates

- [ ] All unit, integration, lint, lock and dependency checks pass in a clean runner.
- [ ] At least 30 calendar days of Binance Spot Testnet operation are reconciled.
- [ ] No unexplained duplicate, stale-data, timeout or fill-reconciliation incidents remain.
- [ ] Kill-switch, restart, backup/restore and alert-delivery drills pass.
- [ ] Mainnet canary limits and maximum acceptable loss are approved and documented.
