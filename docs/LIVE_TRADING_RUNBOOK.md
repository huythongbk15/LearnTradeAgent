# Binance live-trading runbook

The live path is fail-closed. A successful test suite does **not** prove that a
strategy is profitable, and it is not permission to skip testnet or the
operational checklist below.

## Safety model

- Spot only; no margin, futures or withdrawals.
- A dedicated Binance account/sub-account may contain only USDT and the managed
  base assets. Unknown positive balances stop the runner.
- Only fully closed 1-hour candles produce signals.
- Any symbol/data/account/quote error aborts the whole batch before submission.
- Every order is checked against order, symbol, gross-exposure and cash-reserve
  limits immediately before submission. Exchange precision, minimum notional,
  current spread, executable book depth and expected slippage are rechecked.
- Spot cash and sell capacity use exchange-reported `free` balances. A sell may
  additionally use only the locked base quantity reserved by this runner's
  confirmed protective stop, because the exit hands that reservation over with
  Binance cancel-replace. Other locked balances remain unavailable.
- A process lock prevents two runners from sharing one state file. Unfinished
  client order IDs are reconciled before new orders; unknown, open or partial
  states stop the whole batch.
- Every eligible managed Spot position receives a Binance-native `STOP_LOSS`
  market order immediately after an entry fill. The stop remains at the
  exchange if the hourly strategy process or host dies. Later runs may tighten
  it but never widen it.
- Protective state retains both the last confirmed active stop and a pending
  replacement. Restart/timeout recovery queries both deterministic client
  order IDs before allowing another order. A risk-reducing market exit uses
  Binance cancel-replace to hand off the locked stop quantity to the exit.
- A partial entry or exit stops the batch, but only after the refreshed
  remaining position has received an acknowledged exchange-native stop. The
  sole exception is a deterministic `amount_zero`, minimum-amount or
  minimum-notional filter remainder no larger than `LIVE_MAX_DUST_USD` (default
  USD 5, hard cap USD 10). That remainder is signed in risk state and audited as
  `position_dust_classified`. Larger remainders and network, price, maximum or
  unknown errors still emit `position_protection_failed` and fail closed.
- Mainnet requires environment authorization and the same explicit phrase on
  the command line. `TRADING_KILL_SWITCH=true` overrides all other gates.
- The runner binds one of `testnet`, `mainnet-canary` or `mainnet-normal` to the
  signed state. Canary hard-caps orders at `min(USD 25, 0.25% equity)`, symbol
  exposure at 5%, gross exposure at 10%, cash reserve at 80%, daily loss at
  0.5% and drawdown at 2%. Environment values may tighten but cannot loosen
  those caps.
- Every profile/limit change is audited. Any less restrictive value is blocked
  unless the operator supplies `--confirm-risk-increase
  APPROVE_LIVE_RISK_INCREASE`; deployment or restart alone cannot raise risk.
- `TRADING_ENTRY_KILL_SWITCH=true` blocks new buys without trapping an existing
  position: strategy exits, ATR risk exits and circuit-breaker sells remain
  available after the normal quote, balance and exchange checks pass.
- Mainnet also requires a recent `data/live_strategy_evidence.json` whose
  cost-aware walk-forward folds pass every fixed threshold for the portfolio
  and every traded symbol. The evidence must contain six contiguous 90-day
  hourly folds, exact live allocations/costs, the deployed build SHA and a
  valid HMAC. The schema is shown in `config/live_strategy_evidence.example.json`.
- Peak equity, daily baseline, circuit-breaker lock and deterministic order
  identifiers, fill lifecycle and ATR trailing state are written atomically to
  `data/binance_live_risk_state.json`. The file is bound to the API-key
  fingerprint, strategy and symbols, then HMAC-authenticated. A corrupt,
  mismatched or missing state blocks mainnet execution.
- Durable run/order/reconciliation events are appended to
  `data/execution/binance_live_audit.jsonl`. Alert on `run_failed`,
  `order_unknown` and `reconciliation_blocked` from a separate supervisor.

## Required Binance setup

1. Create a dedicated sub-account and an API key restricted by IP allowlist.
2. Enable reading and Spot trading only. Disable withdrawals and futures.
3. Store secrets outside Git and ensure the process account alone can read them.
4. Configure Telegram/on-call monitoring independently from the trading process.
5. Synchronize the host clock and supervise the process with non-zero exit alerts.
6. Generate one random `LIVE_SAFETY_HMAC_KEY` of at least 32 characters and keep
   it in the secret manager. Never rotate it without a controlled state migration.

Run the independent audit watchdog from cron/systemd shortly after each hourly
runner invocation and page on its non-zero exit status:

```powershell
python scripts/check_live_audit.py --max-age-seconds 4500 --lookback-seconds 4500
```

## Stage 1 — tests and dry-run

```powershell
pytest -q
ruff check .
$env:TRADING_KILL_SWITCH = "true"
$env:LIVE_SAFETY_HMAC_KEY = "<load-from-secret-manager>"
python scripts/live_enhanced_ma_binance.py
```

The first mainnet dry-run creates the risk baseline. Review the computed account
equity, every managed position and the complete execution plan. Do not continue
if any asset or price is missing.

## Stage 2 — Binance Spot Testnet

Use a separate testnet key and deliberately small limits:

```powershell
$env:TRADING_KILL_SWITCH = "false"
$env:TRADING_ENTRY_KILL_SWITCH = "false"
$env:TRADING_EXECUTION_ENABLED = "true"
$env:TRADING_MODE = "testnet"
$env:LIVE_SAFETY_HMAC_KEY = "<load-from-secret-manager>"
$env:LIVE_MAX_ORDER_USD = "25"
$env:LIVE_MAX_DUST_USD = "5"
python scripts/live_enhanced_ma_binance.py --testnet --profile testnet --execute
```

Run for at least 30 calendar days and cover entries, exits, exchange rejection,
network timeout, restart/idempotency and alert delivery. Reconcile every fill
against the local state and exchange history.

For each managed position, verify in the Binance Spot Testnet UI/API that:

- exactly one protective sell is open with the expected quantity and stop;
- terminating the hourly runner leaves that order open at Binance;
- restarting the runner adopts the same client order ID instead of duplicating
  it;
- a tighter ATR trail produces one replacement and the old stop is terminal;
- a strategy exit replaces/cancels the stop before selling the locked balance;
- an unrelated locked balance cannot be sold, while the quantity reserved by
  the runner's confirmed protective stop can be handed to a market exit;
- a sub-threshold minimum-filter remainder is signed and audited as controlled
  dust, while the same case above the configured cap blocks the run;
- timeout-after-accept recovery blocks the batch unless either the old or new
  protective client ID is confirmed.

Do not manually cancel protective orders during normal operation. An alert
about unknown protection is an incident: disable new entries, inspect both
client order IDs and reconcile balances/order history before resuming. A
market stop can fill below its trigger during a gap; this is intentional and
preferred to leaving a stop-limit order unfilled.

## Stage 3 — mainnet canary

Before enabling mainnet, independently verify:

- walk-forward/out-of-sample results meet the pre-agreed acceptance criteria;
- testnet has zero unexplained duplicate, stale-data or reconciliation events;
- restore, kill-switch and incident drills passed;
- API IP restriction and withdrawal prohibition are confirmed in Binance;
- maximum acceptable capital loss is documented and funded with disposable
  risk capital only.

Refresh at least 18 months of local 1-hour data for every live symbol, then let
the fixed-parameter evaluator build the evidence file. It publishes the
mainnet artifact only if every symbol passes; otherwise it produces a rejected
artifact for review and exits non-zero.

```powershell
trading-agent data fetch BTC/USDT --timeframe 1h --since 2023-01-01
trading-agent data fetch SOL/USDT --timeframe 1h --since 2023-01-01
trading-agent data fetch AVAX/USDT --timeframe 1h --since 2023-01-01
```

The evidence generator requires the same HMAC secret as the runner and the
exact immutable 40-character commit that will execute:

```powershell
$env:TRADING_BUILD_SHA = (git rev-parse HEAD)
$env:LIVE_SAFETY_HMAC_KEY = "<load-from-secret-manager>"
python scripts/generate_live_strategy_evidence.py --weights 4,3,3 --build-sha $env:TRADING_BUILD_SHA
```

Then use the smallest practical canary and both confirmations:

```powershell
$env:TRADING_KILL_SWITCH = "false"
$env:TRADING_ENTRY_KILL_SWITCH = "false"
$env:TRADING_EXECUTION_ENABLED = "true"
$env:TRADING_MODE = "live"
$env:TRADING_LIVE_CONFIRMATION = "LIVE_TRADING_WITH_REAL_MONEY"
$env:TRADING_BUILD_SHA = "<the-signed-evidence-commit-sha>"
$env:LIVE_SAFETY_HMAC_KEY = "<load-from-secret-manager>"
$env:LIVE_MAX_ORDER_USD = "25"
python scripts/live_enhanced_ma_binance.py --profile mainnet-canary --weights 4,3,3 --execute --confirm-live LIVE_TRADING_WITH_REAL_MONEY
```

Moving a persisted account from canary to `mainnet-normal`, or loosening any
individual limit, is a separate reviewed action. Use the risk-increase phrase
only after the testnet soak and release gates pass; the change appears as
`risk_limits_changed` in the audit log.

Set `TRADING_KILL_SWITCH=true` only when every subsequent order submission must
stop, including exits. Prefer `TRADING_ENTRY_KILL_SWITCH=true` when the operator
must freeze exposure increases while retaining validated risk-reducing sells.
A persistent risk lock also permits risk-reducing sells, blocks buys and may
liquidate managed positions. Do not delete or edit the risk-state file to bypass
a lock; stop the service, reconcile the account and perform a documented
incident review first.

For GitHub deployments, configure `STAGING_KNOWN_HOSTS` and
`PRODUCTION_KNOWN_HOSTS` with the pinned SSH host-key lines. CD rejects images
that were not signed and SBOM-attested by `ci.yml` on `master`. The deployment
resolves the SHA tag to an immutable digest and verifies the image's embedded
commit label before use. Apply the branch ruleset and production-environment
review settings in `.github/BRANCH_PROTECTION.md`; CODEOWNERS alone does not
enforce approval.

The current production workflow still expects `trading-agent-blue` and
`trading-agent-green`, while the checked-in Compose topology does not define
those services. Its validation therefore blocks deployment by design. Treat
this as a P1.5 release blocker; do not remove the check or claim blue-green
support until the topology, health target, traffic switch and rollback drill
are implemented and acceptance-tested together.

## Go/no-go rule

Mainnet is **NO-GO** until all checklist items and the 30-day testnet soak pass.
Technical safeguards reduce execution risk; they do not turn a negative or
unvalidated strategy into a profitable one.
