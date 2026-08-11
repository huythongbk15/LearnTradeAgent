# Live trading implementation TODO

Last updated: 2026-08-11 (Asia/Saigon)

Mainnet status: **NO-GO**. Completion of an engineering checkbox is not
authorization to submit real-money orders. Mainnet still requires the release
gates in this document and an explicit operator approval.

## Baseline

- Local implementation branch: `agent/protective-stop-p0-1`.
- Local starting commit: `0c62522` (`harden real-money Binance execution`).
  Its tree contains the PR #2 hardening changes; the sandbox could not fetch the
  newer merge ref because outbound Git access is blocked.
- Remote repair PR #4 and protective-stop PR #5 are merged; their CI and Phase
  6 workflow runs completed successfully.
- Python: 3.12.13.
- Full test suite after P0.2 order-lifecycle hardening: **304 passed, 3 skipped**.
- Critical live-path suite after P0.2 order-lifecycle hardening: **66 passed**.
- `trading_agent.execution.live_safety` coverage: **81.11%** (gate: 75%).
- Ruff: passed.
- GitHub workflow YAML parse: passed.
- `pip check`: no broken requirements.
- `pip-audit`: not rerun locally because the sandbox blocks access to PyPI; the
  latest remote CI/audit evidence was clean.
- Docker build: not rerun locally because no Docker daemon is available; the
  latest remote CI image build and Trivy scan passed.

## Readiness matrix

| Capability | Backtest | Testnet | Mainnet | Required action |
| --- | --- | --- | --- | --- |
| Next-bar signal timing | Implemented | Implemented by hourly runner | Not accepted | Preserve timing parity tests |
| Fee/spread/slippage model | Implemented | Pre-trade market-quality gates | Not calibrated | Compare predicted and actual fills |
| Intrabar protective stop | Simulated | Implemented; acceptance pending | Not soaked | P0.1 testnet acceptance |
| Order idempotency | Implemented in live ledger | Covered by tests | Not soaked | P0.2 lifecycle soak |
| Partial/unknown fill handling | Fail-closed | Covered by tests | Not soaked | P0.2 active reconciliation |
| Precision/minimum notional | Implemented | Covered by tests | Not soaked | Testnet exchange-filter matrix |
| Market-data freshness | Candle checks implemented | Order-book timestamp is incomplete | **Blocked** | P0.3 timestamp/sequence hardening |
| Risk-reducing sell | Not applicable | Free/locked quantity tests pass | Not soaked | Testnet exit matrix |
| State integrity | Signed local state | Implemented | Single-host only | P1.1 durable leader and snapshots |
| Monitoring/alerting | Modules exist | Not wired to the Binance runner | **Blocked** | P1.3 independent paging |
| Deployment/restore | CI artifacts exist | Not acceptance-tested | **Blocked** | P1.4/P1.5 drills and topology |
| Strategy evidence | Six contiguous OOS folds | Evidence gate implemented | Holdout incomplete | P2 statistical hardening |

## P0 - capital protection and execution correctness

### P0.1 Continuous protective stop

- [x] Verify current Binance Spot protective-order semantics against official
  documentation and isolate exchange-specific behavior behind the broker API.
- [x] Represent protective orders and their lifecycle in signed risk state.
- [x] Ensure every eligible open position is covered by an exchange-native stop.
- [x] Reconcile position and stop coverage before planning any entry/rebalance.
- [x] Recreate a missing stop without creating duplicates.
- [x] Replace a tightened stop safely and never widen a trailing stop.
- [x] Keep risk-reducing exits available while the entry kill switch is active.
- [x] Handle partial fills, dust, exchange precision and minimum notional.
  - [x] Reprotect the refreshed remainder before stopping on a partial fill.
  - [x] Fail closed and audit when exchange filters reject protective quantity.
  - [x] Define and enforce the controlled dust policy in P0.4.
- [x] Audit create, acknowledge, replace, cancel, fill and recovery events.
- [x] Add unit, integration, restart and timeout-after-accept regression tests.
- [ ] Add an opt-in Binance Spot Testnet acceptance test; never run it in unit CI.
- [x] Document exchange-held protection and operator reconciliation procedure.

Acceptance: every protectable position has exactly one valid protective order;
restart and timeout recovery are idempotent; loss of the hourly strategy runner
does not remove protection already held by the exchange.

### P0.2 Order lifecycle and fill accounting

- [x] Add explicit submitted/acknowledged/reconciling/manual-intervention states.
- [x] Poll non-terminal orders with a bounded deadline and jitter.
- [x] Fall back to order history and trade history by client order ID.
- [x] Persist cumulative fills, quote cost, trade IDs and all fee currencies.
- [x] Reconcile ledger, trades and balances before any new order batch.
- [x] Preserve and audit raw exchange statuses instead of silently normalizing
  unknown values to `open`.

### P0.3 Trusted time and market data

- [x] Separate exchange timestamp, request start and local receive timestamp.
      (`TimeStampedFetch` + `Ticker`/`OrderBook.request_started_at/received_at`.)
- [x] Reject high-latency responses and excessive exchange clock skew.
      (`reject_high_latency`, `ServerClock.sync/check` wired before every run.)
- [x] Validate order-book sequence/update IDs and WebSocket snapshot+diff sync.
      (`OrderBookSequenceTracker`, `DiffStreamState`, `BinanceDepthProvider`.)
- [x] Export quote age, request latency, sequence gap and clock-skew metrics.
      (`DataTrustMonitor.metrics()` printed each run cycle; 27 new tests.)

### P0.4 Quantity-based risk-reducing sells

- [x] Validate sell quantity against free base-asset balance after precision.
- [x] Account for locked quantity and protective-order reservations.
- [x] Permit valid risk-reducing sells while entry limits are locked.
- [x] Define a controlled dust/minimum-notional policy: only deterministic
  minimum-filter remainders up to `LIVE_MAX_DUST_USD` (default USD 5, hard cap
  USD 10) are signed and audited; all other failures remain fail-closed.

### P0.5 Canary profiles

- [x] Add explicit `testnet`, `mainnet-canary` and `mainnet-normal` profiles.
- [x] Canary maximum order: `min(USD 25, 0.25% equity)`.
- [x] Canary maximum symbol exposure: 5%; gross exposure: 10%.
- [x] Canary minimum cash reserve: 80%.
- [x] Canary daily-loss lock: 0.5%; account-drawdown lock: 2%.
- [x] Prevent automatic risk-limit escalation and audit every limit change.

### P0.6 Repository and supply-chain gates

- [ ] Require PRs and successful CI checks before merging to `master`.
- [x] Add CODEOWNERS for live runner, risk state, exchange adapter and workflows.
- [ ] Require production-environment approval and disable practical bypasses.
- [ ] Pin base/service container images by digest and validate Compose in CI.
  - [x] Require the application image by digest, verify its embedded commit and
    use the resolved digest in staging/production CD.
  - [x] Validate production and Oracle Compose merges in CI; remove incompatible
    `container_name`/replica and host-network/network combinations.
  - [ ] Pin Dockerfile base images and every infrastructure service by digest.
- [x] Add weekly dependency update PRs for Python, npm, Docker and Actions.
- [ ] Apply and verify the server-side branch ruleset and production reviewers
  documented in `.github/BRANCH_PROTECTION.md`.

## P1 - production operations

- [ ] P1.1: distributed leader lease with fencing, schema migrations, encrypted
  versioned snapshots and automated restore tests.
- [ ] P1.2: correlation IDs and off-host append-only audit retention.
- [ ] P1.3: Prometheus metrics plus independently supervised paging and synthetic
  alert-delivery tests.
- [ ] P1.4: out-of-band kill switch and documented incident/credential drills.
- [ ] P1.5: one execution leader, separate scheduler/monitoring, tested health and
  rollback behavior, and no deployment-triggered mainnet enablement.
  - [x] Reconcile the production workflow's expected blue/green services with
    the actual Compose topology. Resolved by simplifying to a **single execution
    leader** (commit e7f51ba): removed the fake blue/green requirement, the
    `trading-scheduler` service (module did not exist) and the wrong
    `:8080/healthz` checks; CD now verifies digest + signature + SBOM and health
    via `cli system health`, with a documented rollback path.
- [ ] P1.6: dedicated spot subaccount, withdrawal disabled, IP allowlist and
  separate read-only monitoring credentials.

## P2 - statistical and execution quality

- [ ] Freeze a 6-12 month final holdout and publish an immutable research manifest.
- [ ] Add per-fold trade minimums, regime breakdowns, block-bootstrap confidence
  intervals and Deflated/Probabilistic Sharpe.
- [ ] Stress fees, spread and slippage at 1x/2x/3x plus gaps, latency, missing data,
  partial fills, outages and correlated drawdowns.
- [ ] Model exchange precision, fee assets, depth, cancellation and dust in the
  execution simulator.
- [ ] Measure paper/testnet tracking error before evaluating maker/TWAP execution.

## P3 - release gates

- [ ] At least 30 continuous calendar days on Binance Spot Testnet.
- [ ] At least 100 complete order lifecycles.
- [ ] Zero unexplained duplicate or deadline-expired unresolved orders.
- [ ] 100% protective-stop coverage for eligible open positions.
- [ ] No unexplained ledger/balance drift outside the documented tolerance.
- [ ] Restart, network-loss, API-timeout and stale-data drills pass.
- [ ] Alerts reach the independent operator device and synthetic tests pass.
- [ ] Backup/restore and emergency credential-revocation drills pass.
- [ ] All required CI/security checks pass on the exact release commit.
- [ ] Paper/testnet tracking error stays inside approved limits.
- [ ] Canary limits and maximum acceptable loss receive explicit approval.

Only after every release gate passes may the status move from `NO-GO` to
`CANARY-READY`. Enabling mainnet and increasing capital remain separate manual
decisions.
