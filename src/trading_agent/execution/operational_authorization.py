"""Operator-signed authorization for the testnet operational (E2E) track.

The track drives the whole order path (admission, planner, permission,
lifecycle, ledger, exchange-native stops, reconciliation) with a deterministic
driver instead of a researched strategy, to prove the system works end to end.
It exists on testnet only, claims no edge and never produces research or
promotion evidence (docs/operations/OPERABLE_SYSTEM_PLAN.md).

The operator creates the file with scripts/ops/authorize_operational_track.py.
The runner loads it every cycle and keeps entries locked when it is missing,
badly signed, expired, or bound to another account or driver version.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCHEMA = "operational-authorization/v1"
ENVIRONMENT = "testnet"
EXCHANGE = "binance-testnet"
DRIVER = "lifecycle_exerciser"
DEFAULT_PATH = Path("data/execution/operational_authorization.json")
# Hard ceilings: a signed file cannot authorize more than this.
MAX_ORDER_USD_CEILING = 25.0
MAX_VALIDITY = timedelta(days=30)
MAX_TIGHT_STOP_PCT = 0.02


class OperationalAuthorizationError(ValueError):
    """An authorization file exists but must not be used."""


def _aware(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.utcoffset() is None:
        raise ValueError(f"timestamp must carry a timezone: {value}")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class OperationalAuthorization:
    authorization_id: str
    account_fingerprint: str
    symbols: tuple[str, ...]
    driver_code_sha: str
    hold_minutes: float
    tight_stop_every: int
    tight_stop_pct: float
    max_order_usd: float
    max_open_positions: int
    max_lifecycles: int
    max_daily_loss_usd: float
    issued_at: datetime
    expires_at: datetime
    approver: str
    reason: str

    def payload(self) -> dict:
        return {
            "schema": SCHEMA,
            "environment": ENVIRONMENT,
            "exchange": EXCHANGE,
            "driver": DRIVER,
            "authorization_id": self.authorization_id,
            "account_fingerprint": self.account_fingerprint,
            "symbols": list(self.symbols),
            "driver_code_sha": self.driver_code_sha,
            "hold_minutes": float(self.hold_minutes),
            "tight_stop_every": int(self.tight_stop_every),
            "tight_stop_pct": float(self.tight_stop_pct),
            "max_order_usd": float(self.max_order_usd),
            "max_open_positions": int(self.max_open_positions),
            "max_lifecycles": int(self.max_lifecycles),
            "max_daily_loss_usd": float(self.max_daily_loss_usd),
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "approver": self.approver,
            "reason": self.reason,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> OperationalAuthorization:
        for key, expected in (
            ("schema", SCHEMA),
            ("environment", ENVIRONMENT),
            ("exchange", EXCHANGE),
            ("driver", DRIVER),
        ):
            if payload.get(key) != expected:
                raise OperationalAuthorizationError(f"{key} must be {expected!r}")
        try:
            authorization = cls(
                authorization_id=str(payload["authorization_id"]),
                account_fingerprint=str(payload["account_fingerprint"]),
                symbols=tuple(str(symbol) for symbol in payload["symbols"]),
                driver_code_sha=str(payload["driver_code_sha"]),
                hold_minutes=float(payload["hold_minutes"]),
                tight_stop_every=int(payload["tight_stop_every"]),
                tight_stop_pct=float(payload["tight_stop_pct"]),
                max_order_usd=float(payload["max_order_usd"]),
                max_open_positions=int(payload["max_open_positions"]),
                max_lifecycles=int(payload["max_lifecycles"]),
                max_daily_loss_usd=float(payload["max_daily_loss_usd"]),
                issued_at=_aware(payload["issued_at"]),
                expires_at=_aware(payload["expires_at"]),
                approver=str(payload["approver"]),
                reason=str(payload["reason"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OperationalAuthorizationError(
                f"malformed authorization: {exc}"
            ) from exc
        authorization.check_limits()
        return authorization

    def check_limits(self) -> None:
        problems = []
        if not self.authorization_id.strip():
            problems.append("authorization_id is empty")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            problems.append("symbols must be non-empty and unique")
        if not 0 < self.max_order_usd <= MAX_ORDER_USD_CEILING:
            problems.append(f"max_order_usd must be in (0, {MAX_ORDER_USD_CEILING:g}]")
        if not 1 <= self.max_open_positions <= len(self.symbols):
            problems.append("max_open_positions must be between 1 and the symbol count")
        if self.max_lifecycles < 1:
            problems.append("max_lifecycles must be at least 1")
        if not self.max_daily_loss_usd > 0:
            problems.append("max_daily_loss_usd must be positive")
        if not self.hold_minutes > 0:
            problems.append("hold_minutes must be positive")
        if self.tight_stop_every < 0:
            problems.append("tight_stop_every cannot be negative")
        if not 0 < self.tight_stop_pct <= MAX_TIGHT_STOP_PCT:
            problems.append(f"tight_stop_pct must be in (0, {MAX_TIGHT_STOP_PCT:g}]")
        if not self.issued_at < self.expires_at <= self.issued_at + MAX_VALIDITY:
            problems.append(f"validity must be positive and at most {MAX_VALIDITY.days} days")
        if not self.approver.strip() or not self.reason.strip():
            problems.append("approver and reason are required")
        if problems:
            raise OperationalAuthorizationError("; ".join(problems))


def signature(payload: dict, key: str) -> str:
    if not key:
        raise OperationalAuthorizationError("signing key is empty")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    # Domain-separated from every other use of the same key.
    return hmac.new(
        key.encode(), f"{SCHEMA}|{body}".encode(), hashlib.sha256
    ).hexdigest()


def write_authorization(
    path: str | Path, authorization: OperationalAuthorization, *, key: str
) -> None:
    """Sign and write atomically, readable by the owner only."""
    authorization.check_limits()
    payload = authorization.payload()
    document = {**payload, "signature": signature(payload, key)}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def load_authorization(
    path: str | Path,
    *,
    key: str,
    account_fingerprint: str,
    driver_code_sha: str,
    now: datetime | None = None,
) -> OperationalAuthorization | None:
    """Return the authorization in force, or None when there is no file.

    Raises OperationalAuthorizationError when a file exists but must not be
    used: unreadable, badly signed, out of limits, for another account or
    driver version, not yet valid, or expired.
    """
    source = Path(path)
    if not source.is_file():
        return None
    try:
        document = json.loads(source.read_text())
    except (OSError, ValueError) as exc:
        raise OperationalAuthorizationError(
            f"unreadable authorization: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise OperationalAuthorizationError("authorization must be a JSON object")
    supplied = str(document.pop("signature", ""))
    if not hmac.compare_digest(supplied, signature(document, key)):
        raise OperationalAuthorizationError("signature does not match")
    authorization = OperationalAuthorization.from_payload(document)
    if authorization.account_fingerprint != account_fingerprint:
        raise OperationalAuthorizationError("authorization is for another account")
    if authorization.driver_code_sha != driver_code_sha:
        raise OperationalAuthorizationError(
            "driver source changed since it was authorized; re-authorize"
        )
    current = now or datetime.now(UTC)
    if current < authorization.issued_at:
        raise OperationalAuthorizationError("authorization is not valid yet")
    if current >= authorization.expires_at:
        raise OperationalAuthorizationError(
            f"authorization expired at {authorization.expires_at.isoformat()}"
        )
    return authorization
