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
- Remote PR #2 is merged; its CI and Phase 6 workflow runs completed
  successfully.
- Python: 3.12.13.
- Full test suite after P0.1: **279 passed, 3 skipped**.
- Critical live-path suite after P0.1: **41 passed**.
- `trading_agent.execution.live_safety` coverage: **78.08%** (gate: 75%).
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
| Intrabar protective stop | Simulated | Not continuously protected | **Blocked** | P0.1 exchange-native protection |
| Order idempotency | Implemented in live ledger | Covered by tests | Not soaked | P0.2 lifecycle soak |
| Partial/unknown fill handling | Fail-closed | Covered by tests | Not soaked | P0.2 active reconciliation |
| Precision/minimum notional | Implemented | Covered by tests | Not soaked | Testnet exchange-filter matrix |
| Market-data freshness | Candle checks implemented | Order-book timestamp is incomplete | **Blocked** | P0.3 timestamp/sequence hardening |
| Risk-reducing sell | Not applicable | Notional-based validation | **Blocked** | P0.4 quantity/free-balance validation |
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
- [ ] Keep risk-reducing exits available while the entry kill switch is active.
- [ ] Handle partial fills, dust, exchange precision and minimum notional.
- [x] Audit create, acknowledge, replace, cancel, fill and recovery events.
- [x] Add unit, integration, restart and timeout-after-accept regression tests.
- [ ] Add an opt-in Binance Spot Testnet acceptance test; never run it in unit CI.
- [x] Document exchange-held protection and operator reconciliation procedure.

Acceptance: every protectable position has exactly one valid protective order;
restart and timeout recovery are idempotent; loss of the hourly strategy runner
does not remove protection already held by the exchange.

### P0.2 Order lifecycle and fill accounting

- [ ] Add eçÏ8ÚÚ$z{-®éÜj×Ì ¤(€€€½É‘•È€ô=É‘•È (€€€€€€€¥ôˆˆ°(€€€€€€€±¥•¹Ñ}½É‘•É}¥ô‰±Ñ„µÁÌ´Äˆ°(€€€€€€€Íåµ‰½°õ‰Ñ}Íåµ‰½° ¤°(€€€€€€€Í¥‘”õ=É‘•ÉM¥‘”¹M10°(€€€€€€€ÑåÁ”õ=É‘•ÉQåÁ”¹MQ=@°(€€€€€€€Í¥é”õ•¥µ…° ˆÀ¸ÀÄˆ¤°(€€€€€€€ÍÑ½Á}ÁÉ¥”õ•¥µ…° ˆäÀˆ¤°(€€€€¤(€€€…ÍÍ•ÉĞ…‘…ÁÑ•È¹}áÑ}½É‘•É}ÑåÁ”¡½É‘•È¤€ôô€‰µ…É­•Ğˆ(€€€…ÍÍ•ÉĞ…‘…ÁÑ•È¹}½É‘•É}Ñ½}áÑ}Á…É…µÌ¡½É‘•È¤€ôôì(€€€€€€€€‰ÍÑ½Á1½ÍÍAÉ¥”ˆè€äÀ¸À°(€€€€€€€€‰±¥•¹Ñ=É‘•É%ˆè€‰±Ñ„µÁÌ´Äˆ°(€€€ô(()‘•˜Ñ•ÍÑ}áÑ}ÍÑ½Á}±½ÍÍ}É•ÍÁ½¹Í•}¥Í}Á…ÉÍ•‘}…Í}ÁÉ½Ñ•Ñ¥Ù•}ÍÑ½À ¤è(€€€Á…ÉÍ•€ô…‘…ÁÑ•É}İ¥Ñ¡}™¥±Ñ•ÉÌ ¤¹}Á…ÉÍ•}½É‘•È (€€€€€€€ì(€€€€€€€€€€€€‰¥ˆè€‰ÍÑ½À´Äˆ°(€€€€€€€€€€€€‰±¥•¹Ñ=É‘•É%ˆè€‰±Ñ„µÁÌ´Äˆ°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰½Á•¸ˆ°(€€€€€€€€€€€€‰Íåµ‰½°ˆè€‰	Q½UMPˆ°(€€€€€€€€€€€€‰Í¥‘”ˆè€‰Í•±°ˆ°(€€€€€€€€€€€€‰ÑåÁ”ˆè€‰ÍÑ½Á}±½ÍÌˆ°(€€€€€€€€€€€€‰…µ½Õ¹Ğˆè€À¸ÀÄ°(€€€€€€€€€€€€‰™¥±±•ˆè€À°(€€€€€€€€€€€€‰…Ù•É…”ˆè9½¹”°(€€€€€€€€€€€€‰ÁÉ¥”ˆè9½¹”°(€€€€€€€€€€€€‰ÍÑ½ÁAÉ¥”ˆè€äÀ°(€€€€€€€€€€€€‰™•”ˆè9½¹”°(€€€€€€€€€€€€‰Ñ¥µ•%¹½É”ˆè9½¹”°(€€€€€€€€€€€€‰Ñ¥µ•ÍÑ…µÀˆè9½¹”°(€€€€€€€€€€€€‰±…ÍÑQÉ…‘•Q¥µ•ÍÑ…µÀˆè9½¹”°(€€€€€€€ô°(€€€€€€€‰Ñ}Íåµ‰½° ¤°(€€€€¤(€€€…ÍÍ•ÉĞÁ…ÉÍ•¹ÑåÁ”€ôô=É‘•ÉQåÁ”¹MQ=@(€€€…ÍÍ•ÉĞÁ…ÉÍ•¹ÍÑ½Á}ÁÉ¥”€ôô•¥µ…° ˆäÀˆ¤(