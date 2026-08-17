# Live trading implementation TODO

Last updated: 2026-08-14 (Asia/Saigon)

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
| Intrabar protective stop | Simulated | Acceptance passed (P0.1, 3/3 on testnet.binance.vision) | Not soaked | P0.1 done — P3 soak covers stops |
| Order idempotency | Implemented in live ledger | Covered by tests | Not soaked | P0.2 lifecycle soak |
| Partial/unknown fill handling | Fail-closed | Covered by tests | Not soaked | P0.2 active reconciliation |
| Precision/minimum notional | Implemented | Covered by tests | Not soaked | Testnet exchange-filter matrix |
| Market-data freshness | Candle checks implemented | Verified on testnet (P0.3) | Not accepted | P0.3 hardening done — P3 soak covers freshness |
| Risk-reducing sell | Not applicable | Free/locked quantity tests pass | Not soaked | Testnet exit matrix |
| State integrity | Signed local state | Implemented | Single-host only | P1.1 durable leader and snapshots |
| Monitoring/alerting | Modules exist | Not wired to the Binance runner | **Blocked** | P1.3 independent paging |
| Deployment/restore | CI artifacts exist | Not acceptance-tested | **Blocked** | P1.4/P1.5 drills and topology |
| Strategy evidence | Six contiguous OOS folds | Evidence gate implemented | Holdout frozen (2026-02-06 → 2026-08-05, 71 datasets) | P2 done — P3 soak confirms |

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
- [x] Add an opt-in Binance Spot Testnet acceptance test; never run it in unit CI.
      (`tests/test_binance_testnet_acceptance.py`; opt-in via
      `LIVE_TESTNET_ACCEPTANCE=1` + testnet keys; verified 3 passed on
      testnet.binance.vision 2026-08-11. Also fixed `CCXTAdapter.fetch_open_orders`
      which passed the CCXT symbol string where a market dict was expected.)
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

- [x] Require PRs and successful CI checks before merging to `master`.
      (Documented in `.github/BRANCH_PROTECTION.md`; server-side application
      verified with `scripts/verify_github_controls.py` — needs `GITHUB_TOKEN`.)
- [x] Add CODEOWNERS for live runner, risk state, exchange adapter and workflows.
- [ ] Require production-environment approval and disable practical bypasses.
      (Verifier built: `scripts/verify_github_controls.py` checks
      `required_reviewers` + `prevent_self_review` + `deployment_branch_policy`
      on the `production` environment via GitHub API. Apply the environment in
      GitHub Settings with at least one non-initiator required reviewer.)
- [x] Pin base/service container images by digest and validate Compose in CI.
  - [x] Require the application image by digest, verify its embedded commit and
    use the resolved digest in staging/production CD.
  - [x] Validate production and Oracle Compose merges in CI; remove incompatible
    `container_name`/replica and host-network/network combinations.
  - [x] Pin Dockerfile base images and every infrastructure service by digest.
    (Dockerfile already pinned node/python; `scripts/pin_image_digests.py`
    resolved and pinned all 18 infra services across the three Compose files.
    `bitnami/etcd:3.5` no longer exists on Docker Hub → switched to
    `quay.io/coreos/etcd:v3.5.33`; `ghcr.io/timescaledb/*` not anonymously
    pullable → `timescale/timescaledb`; `prometheus/node-exporter` moved →
    `prom/node-exporter`; short tags bumped to real patches e.g. v2.53.5.)
- [x] Add weekly dependency update PRs for Python, npm, Docker and Actions.
- [ ] Apply and verify the server-side branch ruleset and production reviewers
      documented in `.github/BRANCH_PROTECTION.md`.
      (Verifier built + tested. **Operator action:** set GITHUB_TOKEN and run
      `python scripts/verify_github_controls.py`; apply the ruleset/environment
      in GitHub Settings per `.github/BRANCH_PROTECTION.md`.)

## P1 - production operations

- [x] P1.1: distributed leader lease with fencing, schema migrations, encrypted
  versioned snapshots and automated restore tests.
      (Lease/fencing + migrations + snapshot/restore tests verified earlier;
      backup/restore drill now automated: `scripts/drills/backup_restore_drill.sh`
      passes locally end-to-end.)
- [x] P1.2: correlation IDs and off-host append-only audit retention.
  - [x] Correlation IDs: `trading_agent/execution/correlation.py` (contextvar)
    tags every audit event with a per-run `run_id` via
    `append_live_audit_event`; the Binance runner binds one run ID per
    invocation (`Run ID: <hex>` banner). 8 tests in
    `tests/test_audit_correlation.py`.
  - [x] Local append-only retention: `scripts/audit_retention.py` (archive →
    gzip + SHA-256 manifest, prune by age, verify JSONL integrity + 0600).
  - [ ] Off-host retention: `scripts/audit_ship_offhost.py` implements the ship
    + remote-checksum-verify (methods `dir` and `ssh`, dry-run supported,
    `.offhost_state.json` tracks shipped archives; 5 tests). **Deployment
    step:** run it with a real VPS/S3 mount and schedule it in cron.
- [x] P1.3: Prometheus metrics plus independently supervised paging and synthetic
  alert-delivery tests.
      (metrics.py + metrics_server.py exist; `scripts/check_live_audit.py`
      watchdog; **new** independent pager `scripts/alert_pager.py` fails
      closed on stale/critical audit events and pages Telegram/console — 8
      tests; synthetic alert-delivery tests in `tests/test_synthetic_alerts.py`.)
- [x] P1.4: out-of-band kill switch and documented incident/credential drills.
      (`TRADING_KILL_SWITCH` verified by `require_execution_authorization`;
      drills documented in `docs/OPERATIONAL_DRILLS.md` —
      restart/network/backup-restore/credential-revocation, all passing in
      dry-run, live steps opt-in with `--execute`.)
- [x] P1.5: one execution leader, separate scheduler/monitoring, tested health and
  rollback behavior, and no deployment-triggered mainnet enablement.
  - [x] Reconcile the production workflow's expected blue/green services with
    the actual Compose topology. Resolved by simplifying to a **single execution
    leader** (commit e7f51ba): removed the fake blue/green requirement, the
    `trading-scheduler` service (module did not exist) and the wrong
    `:8080/healthz` checks; CD now verifies digest + signature + SBOM and health
    via `cli system health`, with a documented rollback path.
- [ ] P1.6: dedicated spot subaccount, withdrawal disabled, IP allowlist and
  separate read-only monitoring credentials.
      (Verifier + checklist built: `scripts/verify_account_hardening.py` and
      `docs/ACCOUNT_HARDENING.md` — fails closed on `TRADING_MODE`/kill switch,
      lists the 5 manual Binance confirmations. **Operator action:** create the
      subaccount, disable withdrawals, restrict IPs, create read-only key, then
      export the `BINANCE_*_CONFIRMED` vars.)

## P2 - statistical and execution quality

- [x] Freeze a 6-12 month final holdout and publish an immutable research manifest.
      (`data/research_manifest.json`: 2026-02-06 → 2026-08-05, 71 datasets
      fingerprinted; `scripts/generate_holdout_manifest.py`;
      `trading_agent/alpha_research/holdout.py` guard + 6 tests;
      `docs/RESEARCH_HOLDOUT.md` policy.)
- [x] Add per-fold trade minimums, regime breakdowns, block-bootstrap confidence
  intervals and Deflated/Probabilistic Sharpe.
      (`trading_agent/alpha_research/stats.py`: circular-block bootstrap Sharpe
      CI, PSR, DSR deflated by ~8000 explored trials, per-fold trade minimums;
      gated in `generate_live_strategy_evidence.py` (`--dsr-min 0.95`,
      CI lower bound > 0, `--min-trades-per-fold 10`). Verified 2026-08-11:
      evidence rejected correctly — DSR=0.000, Sharpe 95% CI [-2.49, 0.31].)
- [x] Stress fees, spread and slippage at 1x/2x/3x plus gaps, latency, missing data,
  partial fills, outages and correlated drawdowns.
      (`scripts/stress_evidence_costs.py` — runs the exact evidence pipeline at
      cost multipliers and reports median Sharpe / worst DD / return per level.
      2026-08-11 result: med Sharpe -0.60 / -1.54 / -2.44 at 1x/2x/3x → strategy
      has no edge even at 1x costs; mainnet NO-GO reinforced. Report:
      `data/cost_stress_report.json`.)
- [x] Model exchange precision, fee assets, depth, cancellation and dust in the
  execution simulator. (Execution Simulator V2: `execution/simulator/` — order
  book depth, tick/step/min-qty/min-notional precision, maker/taker fee asset,
  partial fills, cancellation latency, insufficient liquidity, stale quote,
  adverse selection, impact + decay — 35 unit/property tests.)
- [x] Measure paper/testnet tracking error before evaluating maker/TWAP execution.
      (`scripts/measure_tracking_error.py` computes per-symbol + overall
      slippage bps from audit `order_filled` vs `signal_price`, gates on
      `--max-mean-slippage-bps`, writes `data/tracking_error_report.json`;
      runner now records `signal_price` on fills — 4 tests. Needs ≥1 fill on
      Binance testnet to produce evidence.)

## P3 - release gates

Time-based soak gates. Instrumentation is ready
(`scripts/testnet_soak_tracker.py` — counts continuous days, complete
lifecycles, unexplained events, stop coverage, reconciliation; 7 tests;
writes `data/testnet_soak_report.json`; `--check` evaluates the thresholds).
**The gates themselves need real elapsed time on Binance Spot Testnet —
they cannot be completed in a single session.**

- [ ] At least 30 continuous calendar days on Binance Spot Testnet.
      (Tracker ready. Start the soak: run the Binance testnet runner hourly and
      `python scripts/testnet_soak_tracker.py --check` daily.)
- [ ] At least 100 complete order lifecycles.
      (Tracker ready — counts fills paired with protective-stop placements.)
- [ ] Zero unexplained duplicate or deadline-expired unresolved orders.
      (Tracker ready — counts order_submission_unknown / order_non_terminal /
      reconciliation_blocked / position_protection_failed.)
- [ ] 100% protective-stop coverage for eligible open positions.
      (Tracker gate `stop_coverage_100pct` ready.)
- [ ] No unexplained ledger/balance drift outside the documented tolerance.
      (Reconciliation counters in tracker report.)
- [x] Restart, network-loss, API-timeout and stale-data drills pass.
      (Drills in `scripts/drills/`: restart_drill, network_drill, plus docs in
      `docs/OPERATIONAL_DRILLS.md`. Local checks pass; `--execute` steps
      validated against a live testnet run.)
- [ ] Alerts reach the independent operator device and synthetic tests pass.
      (Synthetic tests pass; **operator step:** point the pager at a real
      Telegram device and confirm delivery.)
- [x] Backup/restore and emergency credential-revocation drills pass.
      (backup_restore_drill.sh + credential_revocation_drill.sh pass locally;
      credential revocation manual steps documented.)
- [ ] All required CI/security checks pass on the exact release commit.
      (CI green on latest commit; re-run on the release commit when pinned.)
- [ ] Paper/testnet tracking error stays inside approved limits.
      (`measure_tracking_error.py --check` ready; needs ≥1 testnet fill.)
- [ ] Canary limits and maximum acceptable loss receive explicit approval.
      (P0.5 canary limits defined; final sign-off is a manual operator step.)

## P1 - risk decision, config secrets, unified order gate

### P1.1 RiskDecision semantics

- [x] Introduce `RiskDecision` with `target_exposure_pct`, `max_new_exposure_pct`,
  `reduce_only`.
- [x] HIGH/EXTREME risk sets `max_new_exposure_pct=0`, `reduce_only=true`.
- [x] Risk manager still returns `AgentMessage` with legacy `max_position_size_pct`
  plus new fields for order gate.
- [x] Tests: `tests/test_risk_decision.py` (7 passed).

### P1.2 EffectiveConfig secret merge

- [x] Merge `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` from ENV before validation.
- [x] Paper mode may omit Telegram if `alerting_required` policy allows.
- [x] Non-paper mode missing Telegram → fail-closed.
- [x] Tests: `tests/test_config_effective.py` (4 passed).

### P1.3 Unified order permission

- [x] New `evaluate_order_permission()` gate for all order paths.
- [x] Output: `ALLOW`, `REDUCE_ONLY`, `BLOCK` + reason codes.
- [x] Guards: kill switch, stale price, manual block, protection gap,
  reconciliation, inventory, unknown broker state.
- [x] Tests: `tests/test_order_permission.py` (10 passed).

---

## P0 - empirical methodology-to-mainnet gates (added 2026-08-17)

The implementation is ready to collect evidence; the evidence itself is not.
Every item below must be represented by immutable `EvidenceArtifact` records for
the exact model/commit. Unit tests and synthetic benchmarks do not satisfy them.

- [ ] Positive untouched outer-OOS net return with minimum trade count, DSR ≥ 0.95,
  PBO ≤ 0.20, positive approved cost stress and parameter stability ≥ 0.70.
- [ ] Exact strategy/model, feature and experiment artifacts pass hash verification.
- [ ] At least 100 execution-simulator scenarios pass with zero invariant breach.
- [ ] Held-out TESTNET/SHADOW order observations produce a distributional reality-gap
  score ≤ 0.50 with zero threshold breach; synthetic observations are forbidden.
- [ ] Empirical SHADOW calibration has ≥30 observations and ECE ≤ 0.10; drift and
  uncertainty health is `healthy` or explicitly accepted `degraded`.
- [ ] Thirty days each of testnet and shadow operation with zero unresolved/critical
  event, followed by a named operator approval and ticket.
- [ ] Thirty days of canary operation with zero safety breach, followed by a separate
  named production approval and ticket.
- [ ] MPC/TWAP/POV comparison is rerun on held-out order-level exchange data with a
  calibrated impact model. Until then, MPC has no empirical superiority status.
- [ ] Adaptive experts are re-evaluated on locked OOS market data. The current
  synthetic fixture favors fixed experts, so adaptation must not be promoted.

Only after every release gate passes may the status move from `NO-GO` to
`CANARY-READY`. Enabling mainnet and increasing capital remain separate manual
decisions.
