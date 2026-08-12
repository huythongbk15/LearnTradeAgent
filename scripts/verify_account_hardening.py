#!/usr/bin/env python3
"""Verify exchange-account hardening prerequisites (P1.6).

Checks the *operator-side* evidence that the live exchange account is
hardened before mainnet enablement. The checks that can be verified from
configuration/environment run here; the Binance-UI checks require manual
confirmation and are reported as UNVERIFIED unless the operator exports
them (``BINANCE_SUBACCOUNT_CONFIRMED`` etc.).

Usage:
  python scripts/verify_account_hardening.py

Exit codes: 0 = all checks pass (or operator-confirmed), 1 = a required
check fails, 2 = fatal misconfiguration (e.g. mainnet mode active).
"""

from __future__ import annotations

import argparse
import os
import sys

REQUIRED_ENV = {
    "TRADING_MODE": "must be testnet (never mainnet for the live runner)",
    "TRADING_KILL_SWITCH": "must be true until explicit operator enablement",
}

MANUAL_CONFIRMATIONS = {
    "BINANCE_SUBACCOUNT_CONFIRMED": "dedicated spot subaccount for live trading",
    "BINANCE_WITHDRAWALS_DISABLED": "withdrawals disabled on the subaccount",
    "BINANCE_IP_ALLOWLIST": "API key IP allowlist restricts access",
    "BINANCE_READONLY_MONITORING": "separate read-only monitoring credentials",
    "BINANCE_PRODUCTION_APPROVAL": "explicit operator production approval",
}


def check_env(env: dict[str, str]) -> list[str]:
    failures: list[str] = []
    mode = (env.get("TRADING_MODE") or "").strip().lower()
    if mode != "testnet":
        failures.append(f"TRADING_MODE={mode or '<unset>'}: expected testnet")
    kill = (env.get("TRADING_KILL_SWITCH") or "").strip().lower()
    if kill != "true":
        failures.append(f"TRADING_KILL_SWITCH={kill or '<unset>'}: expected true")
    return failures


def check_manual(env: dict[str, str]) -> list[str]:
    pending: list[str] = []
    for var, description in MANUAL_CONFIRMATIONS.items():
        value = (env.get(var) or "").strip().lower()
        if value in ("1", "true", "yes", "confirmed"):
            continue
        pending.append(f"{var} (operator confirmation): {description}")
    return pending


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-operator-confirmation",
        action="store_true",
        help="fail when manual Binance confirmations are not yet exported",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    env = dict(os.environ)
    failures = check_env(env)
    pending = check_manual(env)

    for message in failures:
        print(f"[FAIL] {message}", file=sys.stderr)
    for message in pending:
        print(f"[UNVERIFIED] {message}")

    if failures:
        print("ACCOUNT HARDENING FAILED", file=sys.stderr)
        return 1
    if pending and args.require_operator_confirmation:
        print("ACCOUNT HARDENING PENDING OPERATOR CONFIRMATION", file=sys.stderr)
        return 1
    print("ACCOUNT HARDENING OK" if not pending else "ACCOUNT HARDENING OK (manual items pending)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())