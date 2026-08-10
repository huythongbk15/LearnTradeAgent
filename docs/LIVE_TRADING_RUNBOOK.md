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
  limits immediately before submission.
- Mainnet requires environment authorization and the same explicit phrase on
  the command line. `TRADING_KILL_SWITCH=true` overrides all other gates.
- Mainnet also requires a recent `data/live_strategy_evidence.json` whose
  cost-aware walk-forward folds pass every fixed threshold for every traded
  symbol. The schema is shown in `config/live_strategy_evidence.example.json`.
- Peak equity, daily baseline, circuit-breaker lock and deterministic order
  identifiers are written atomically to `data/binance_live_risk_state.json`.
  A corrupt or missing state blocks mainnet execution.

## Required Binance setup

1. Create a dedicated sub-account and an API key restricted by IP allowlist.
2. Enable reading and Spot trading only. Disable withdrawals and futures.
3. Store secrets outside Git and ensure the process account alone can read them.
4. Configure Telegram/on-call monitoring independently from the trading process.
5. Synchronize the host clock and supervise the process with non-zero exit alerts.

## Stage 1 — tests and dry-run

```powershell
pytest -q
ruff check .
$env:TRADING_KILL_SWITCH = "true"
python scripts/live_enhanced_ma_binance.py
```

The first mainnet dry-run creates the risk baseline. Review the computed account
equity, every managed position and the complete execution plan. Do not continue
if any asset or price is missing.

## Stage 2 — Binance Spot Testnet

Use a separate testnet key and deliberately small limits:

```powershell
$env:TRADING_KILL_SWITCH = "false"
$env:TRADING_EXECUTION_ENABLED = "true"
$env:TRADING_MODE = "testnet"
$env:LIVE_MAX_ORDER_USD = "25"
python scripts/live_enhanced_ma_binance.py --testnet --execute
```

Run for at least 30 calendar days and cover entries, exits, exchange rejection,
network timeout, restart/idempotency and alert delivery. Reconcile every fill
against the local state and exchange history.

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
python scripts/generate_live_strategy_evidence.py
```

Then use the smallest practical canary and both confirmations:

```powershell
$env:TRADING_KILL_SWITCH = "false"
$env:TRADING_EXECUTION_ENABLED = "true"
$env:TRADING_MODE = "live"
$env:TRADING_LIVE_CONFIRMATION = "LIVE_TRADING_WITH_REAL_MONEY"
$env:LIVE_MAX_ORDER_USD = "25"
python scripts/live_enhanced_ma_binance.py --execute --confirm-live LIVE_TRADING_WITH_REAL_MONEY
```

Set `TRADING_KILL_SWITCH=true` to prevent subsequent order submissions. A
persistent risk lock permits risk-reducing sells but blocks buys. Do not delete
or edit the risk-state file to bypass a lock; stop the service, reconcile the
account and perform a documented incident review first.

## Go/no-go rule

Mainnet is **NO-GO** until all checklist items and the 30-day testnet soak pass.
Technical safeguards reduce execution risk; they do not turn a negative or
unvalidated strategy into a profitable one.
