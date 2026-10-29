#!/usr/bin/env python3
"""Authorize, show or revoke the testnet operational (E2E) track.

The operator signs a time-limited authorization that lets the Binance testnet
runner drive small orders with the lifecycle exerciser: no strategy, no claim
of edge, no research or promotion evidence
(docs/operations/OPERABLE_SYSTEM_PLAN.md). The authorization is bound to this
account and to the driver source in this checkout; changing either needs a
new one.

    .venv/bin/python scripts/ops/authorize_operational_track.py --approver NAME --reason TEXT
    .venv/bin/python scripts/ops/authorize_operational_track.py --show
    .venv/bin/python scripts/ops/authorize_operational_track.py --revoke

Signing prints the terms and asks for confirmation before writing anything.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]

from trading_agent.execution.live_safety import account_fingerprint  # noqa: E402
from trading_agent.execution.operational_authorization import (  # noqa: E402
    DEFAULT_PATH,
    EXCHANGE,
    OperationalAuthorization,
    OperationalAuthorizationError,
    load_authorization,
    write_authorization,
)
from trading_agent.execution.operational_driver import driver_code_sha  # noqa: E402

CONFIRMATION = "AUTHORIZE"


def _terms(authorization: OperationalAuthorization) -> str:
    return "\n".join(
        [
            f"  id                {authorization.authorization_id}",
            f"  account           {authorization.account_fingerprint[:8]}… ({EXCHANGE})",
            f"  symbols           {', '.join(authorization.symbols)}",
            f"  per order         ≤ ${authorization.max_order_usd:g}",
            f"  open positions    ≤ {authorization.max_open_positions}",
            f"  lifecycles        ≤ {authorization.max_lifecycles}",
            f"  daily loss stop   ${authorization.max_daily_loss_usd:g}",
            f"  hold              {authorization.hold_minutes:g} min, then sell",
            f"  venue-stop exits  every {authorization.tight_stop_every} lifecycle(s), "
            f"stop {authorization.tight_stop_pct:.2%} below price",
            f"  valid             {authorization.issued_at:%Y-%m-%d %H:%MZ} → "
            f"{authorization.expires_at:%Y-%m-%d %H:%MZ}",
            f"  driver source     {authorization.driver_code_sha[:12]}",
            f"  approver          {authorization.approver}",
            f"  reason            {authorization.reason}",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--show", action="store_true", help="verify and print the current one")
    action.add_argument("--revoke", action="store_true", help="withdraw it (entries lock again)")
    parser.add_argument("--approver")
    parser.add_argument("--reason")
    parser.add_argument("--symbols", default="BTC/USDT,SOL/USDT,AVAX/USDT")
    parser.add_argument("--days", type=float, default=14.0)
    parser.add_argument("--max-order-usd", type=float, default=20.0)
    parser.add_argument("--max-open-positions", type=int)
    parser.add_argument("--max-lifecycles", type=int, default=120)
    parser.add_argument("--max-daily-loss-usd", type=float, default=10.0)
    parser.add_argument("--hold-minutes", type=float, default=230.0)
    parser.add_argument("--tight-stop-every", type=int, default=3)
    parser.add_argument("--tight-stop-pct", type=float, default=0.003)
    parser.add_argument("--yes", action="store_true", help="skip the typed confirmation")
    args = parser.parse_args(argv)

    os.chdir(ROOT)
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    path = Path(os.getenv("LIVE_OPERATIONAL_AUTHORIZATION", str(DEFAULT_PATH)))
    key = os.getenv("LIVE_SAFETY_HMAC_KEY", "")
    account_id = os.getenv("LIVE_ACCOUNT_ID", "").strip()
    if not key or not account_id:
        print("FATAL: LIVE_SAFETY_HMAC_KEY and LIVE_ACCOUNT_ID must be set in .env")
        return 1
    fingerprint = account_fingerprint(exchange=EXCHANGE, api_key=account_id)

    if args.revoke:
        if not path.is_file():
            print(f"No authorization at {path}; entries are already locked.")
            return 0
        revoked = path.with_name(f"{path.name}.revoked-{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
        path.rename(revoked)
        print(f"Revoked: moved to {revoked}. The next cycle locks entries and still exits.")
        return 0

    if args.show:
        try:
            current = load_authorization(
                path, key=key, account_fingerprint=fingerprint, driver_code_sha=driver_code_sha()
            )
        except OperationalAuthorizationError as exc:
            print(f"INVALID ({path}): {exc}")
            return 1
        if current is None:
            print(f"Not authorized: no file at {path}")
            return 1
        print(f"Authorized ({path}):\n{_terms(current)}")
        return 0

    if not (args.approver or "").strip() or not (args.reason or "").strip():
        parser.error("--approver and --reason are required to sign")
    symbols = tuple(symbol.strip() for symbol in args.symbols.split(",") if symbol.strip())
    issued = datetime.now(UTC)
    authorization = OperationalAuthorization(
        authorization_id=f"ops-{issued:%Y%m%d}-{secrets.token_hex(4)}",
        account_fingerprint=fingerprint,
        symbols=symbols,
        driver_code_sha=driver_code_sha(),
        hold_minutes=args.hold_minutes,
        tight_stop_every=args.tight_stop_every,
        tight_stop_pct=args.tight_stop_pct,
        max_order_usd=args.max_order_usd,
        max_open_positions=args.max_open_positions or len(symbols),
        max_lifecycles=args.max_lifecycles,
        max_daily_loss_usd=args.max_daily_loss_usd,
        issued_at=issued,
        expires_at=issued + timedelta(days=args.days),
        approver=args.approver.strip(),
        reason=args.reason.strip(),
    )
    try:
        authorization.check_limits()
    except OperationalAuthorizationError as exc:
        print(f"REFUSED: {exc}")
        return 1
    print(f"Testnet operational track — terms to sign:\n{_terms(authorization)}")
    if path.is_file():
        print(f"This replaces the authorization at {path}.")
    if not args.yes and input(f"Type {CONFIRMATION} to sign: ").strip() != CONFIRMATION:
        print("Not signed.")
        return 1
    write_authorization(path, authorization, key=key)
    if path == DEFAULT_PATH:
        print(f"Signed: {path}. The next scheduled cycle starts the track.")
    else:
        print(f"Signed: {path}. Only runners given this path will use it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
