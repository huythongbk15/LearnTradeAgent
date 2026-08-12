# Account Hardening (P1.6)

Before any mainnet capital, the exchange account must be hardened. The live
runner is **Binance Spot Testnet only** today; these steps apply to the
mainnet subaccount that will replace it.

## Required controls

| Control | Where | Evidence |
|---|---|---|
| Dedicated spot subaccount | Binance sub-account (never the main account) | `BINANCE_SUBACCOUNT_CONFIRMED=true` |
| Withdrawals disabled | Subaccount → withdrawal disabled permanently | `BINANCE_WITHDRAWALS_DISABLED=true` |
| IP allowlist | API key created with IP allowlist restricted to the runner host(s) | `BINANCE_IP_ALLOWLIST=true` |
| Read-only monitoring credentials | A second API key with **read-only** permissions used only by monitoring | `BINANCE_READONLY_MONITORING=true` |
| Production approval | Explicit operator sign-off documented in `docs/LIVE_TRADING_TODO.md` | `BINANCE_PRODUCTION_APPROVAL=true` |

## Verifier

```bash
python scripts/verify_account_hardening.py
python scripts/verify_account_hardening.py --require-operator-confirmation
```

The verifier fails closed when `TRADING_MODE != testnet` or
`TRADING_KILL_SWITCH != true`, and reports manual Binance-UI confirmations
as `[UNVERIFIED]` until the operator exports the corresponding environment
variables. CI should run it with `--require-operator-confirmation` on the
mainnet deployment path only; the testnet path must always stay green.

## Kill-switch policy

- `TRADING_KILL_SWITCH=true` is the default in `.env` and blocks every order
  submission (`require_execution_authorization`).
- Removing it is a manual, audited step performed only together with
  `BINANCE_PRODUCTION_APPROVAL=true` after every P3 release gate passes.

## Read-only monitoring

The pager (`scripts/alert_pager.py`), watchdog (`scripts/check_live_audit.py`)
and health checks must use credentials that cannot place orders. If the
exchange API key is ever reused for monitoring, the key must be rotated.
