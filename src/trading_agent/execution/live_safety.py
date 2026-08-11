"""Fail-closed safety primitives for real-money execution paths.

This module deliberately contains no broker-specific logic.  A live runner must
still fetch account/market state and submit orders, but it can share the same
authorization, persistent circuit-breaker and order validation rules.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import socket
import tempfile
from statistics import median
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping


LIVE_CONFIRMATION = "LIVE_TRADING_WITH_REAL_MONEY"
STATE_VERSION = 1


class LiveSafetyError(RuntimeError):
    """Raised when a live execution safety check fails."""


class DuplicateOrderError(LiveSafetyError):
    """Raised when an execution cycle tries to submit the same intent twice."""


class LiveExecutionLock:
    """Hold a non-blocking OS lock for the complete live execution cycle.

    Atomic state-file replacement prevents torn JSON, but it cannot prevent two
    schedulers from loading the same old state and both submitting orders.  This
    lock deliberately lives beside (not inside) the state file so the state can
    continue to be replaced atomically while the lock handle remains stable.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            raise LiveSafetyError(f"live execution lock is already held: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.chmod(self.path, 0o600)
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise LiveSafetyError(
                f"another live runner already holds the execution lock: {self.path}"
            ) from exc

        self._fd = fd
        metadata = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        encoded = (json.dumps(metadata, sort_keys=True) + "\n").encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, encoded)
        os.fsync(fd)

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            self._fd = None

    def __enter__(self) -> "LiveExecutionLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def _is_true(value: str | None) -> bool:
    return (value or "").strip().lower() == "true"


def _read_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise LiveSafetyError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise LiveSafetyError(f"{name} must be finite")
    return value


@dataclass(frozen=True)
class LiveRiskLimits:
    """Hard limits evaluated immediately before every real order."""

    max_order_notional_usd: float = 100.0
    max_symbol_exposure_pct: float = 0.25
    max_gross_exposure_pct: float = 0.50
    min_cash_reserve_pct: float = 0.25
    max_daily_loss_pct: float = 0.02
    max_drawdown_pct: float = 0.05
    max_quote_age_seconds: float = 15.0
    max_price_deviation_pct: float = 0.01
    max_spread_pct: float = 0.002
    max_book_slippage_pct: float = 0.003
    min_book_depth_multiple: float = 1.25

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LiveRiskLimits":
        source = os.environ if env is None else env
        limits = cls(
            max_order_notional_usd=_read_float(source, "LIVE_MAX_ORDER_USD", 100.0),
            max_symbol_exposure_pct=_read_float(source, "LIVE_MAX_SYMBOL_PCT", 0.25),
            max_gross_exposure_pct=_read_float(source, "LIVE_MAX_GROSS_EXPOSURE_PCT", 0.50),
            min_cash_reserve_pct=_read_float(source, "LIVE_MIN_CASH_RESERVE_PCT", 0.25),
            max_daily_loss_pct=_read_float(source, "LIVE_MAX_DAILY_LOSS_PCT", 0.02),
            max_drawdown_pct=_read_float(source, "LIVE_MAX_DRAWDOWN_PCT", 0.05),
            max_quote_age_seconds=_read_float(source, "LIVE_MAX_QUOTE_AGE_SECONDS", 15.0),
            max_price_deviation_pct=_read_float(source, "LIVE_MAX_PRICE_DEVIATION_PCT", 0.01),
            max_spread_pct=_read_float(source, "LIVE_MAX_SPREAD_PCT", 0.002),
            max_book_slippage_pct=_read_float(
                source, "LIVE_MAX_BOOK_SLIPPAGE_PCT", 0.003
            ),
            min_book_depth_multiple=_read_float(
                source, "LIVE_MIN_BOOK_DEPTH_MULTIPLE", 1.25
            ),
        )
        limits.validate()
        return limits

    def validate(self) -> None:
        if self.max_order_notional_usd <= 0:
            raise LiveSafetyError("LIVE_MAX_ORDER_USD must be positive")
        for name in (
            "max_symbol_exposure_pct",
            "max_gross_exposure_pct",
            "min_cash_reserve_pct",
            "max_daily_loss_pct",
            "max_drawdown_pct",
            "max_price_deviation_pct",
            "max_spread_pct",
            "max_book_slippage_pct",
        ):
            value = getattr(self, name)
            if not 0 < value < 1:
                raise LiveSafetyError(f"{name} must be between 0 and 1")
        if self.max_symbol_exposure_pct > self.max_gross_exposure_pct:
            raise LiveSafetyError("symbol exposure cannot exceed gross exposure")
        if self.max_quote_age_seconds <= 0:
            raise LiveSafetyError("LIVE_MAX_QUOTE_AGE_SECONDS must be positive")
        if not 1 <= self.min_book_depth_multiple <= 10:
            raise LiveSafetyError("LIVE_MIN_BOOK_DEPTH_MULTIPLE must be between 1 and 10")


@dataclass(frozen=True)
class StrategyEvidencePolicy:
    """Minimum walk-forward evidence required before mainnet execution."""

    min_folds: int = 6
    min_median_oos_sharpe: float = 0.50
    min_median_oos_return_pct: float = 0.0
    min_positive_fold_ratio: float = 0.60
    max_worst_oos_drawdown_pct: float = 15.0
    min_total_oos_trades: int = 20
    min_commission_bps: float = 10.0
    min_slippage_bps: float = 5.0
    min_spread_bps: float = 2.0
    min_fold_days: int = 90
    max_generated_age_hours: float = 24.0
    max_data_age_hours: float = 6.0


def _parse_utc_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LiveSafetyError(f"strategy evidence {field_name} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LiveSafetyError(f"strategy evidence {field_name} is invalid") from exc
    if parsed.tzinfo is None:
        raise LiveSafetyError(f"strategy evidence {field_name} must include a timezone")
    return parsed.astimezone(UTC)


def _payload_hmac(payload: Mapping[str, object], key: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hmac.new(key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"hmac-sha256:{digest}"


def validate_integrity_key(key: str | None) -> str:
    normalized = key or ""
    lowered = normalized.lower()
    if len(normalized) < 32 or any(
        marker in lowered for marker in ("replace-with", "changeme", "placeholder")
    ):
        raise LiveSafetyError(
            "LIVE_SAFETY_HMAC_KEY must be a non-placeholder value of at least 32 characters"
        )
    return normalized


def sign_strategy_evidence(payload: Mapping[str, object], key: str) -> dict[str, object]:
    key = validate_integrity_key(key)
    signed = dict(payload)
    signed.pop("integrity", None)
    signed["integrity"] = _payload_hmac(signed, key)
    return signed


def account_fingerprint(*, exchange: str, api_key: str) -> str:
    if not api_key:
        raise LiveSafetyError("cannot fingerprint an empty API key")
    digest = hashlib.sha256(f"{exchange}|{api_key}".encode("utf-8")).hexdigest()
    return digest[:24]


def strategy_fingerprint(
    *,
    strategy: str,
    params: Mapping[str, object],
    allocations: Mapping[str, float],
) -> str:
    payload = {
        "strategy": strategy,
        "params": dict(params),
        "allocations": dict(sorted(allocations.items())),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def validate_build_sha(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if not 7 <= len(normalized) <= 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise LiveSafetyError("TRADING_BUILD_SHA must be a 7-64 character hexadecimal commit SHA")
    return normalized


def append_live_audit_event(
    path: str | Path,
    event: str,
    details: Mapping[str, object] | None = None,
    *,
    now: datetime | None = None,
) -> None:
    """Append a durable local JSONL event without including credentials."""

    if not event.strip():
        raise LiveSafetyError("audit event name cannot be empty")
    current = now or datetime.now(UTC)
    payload = {
        "timestamp": current.astimezone(UTC).isoformat(),
        "event": event,
        "pid": os.getpid(),
        "details": dict(details or {}),
    }
    audit_path = Path(path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(audit_path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.chmod(audit_path, 0o600)
        os.write(
            fd,
            (json.dumps(payload, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"),
        )
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_strategy_evidence(
    path: str | Path,
    *,
    expected_symbols: list[str],
    expected_params: Mapping[str, object],
    expected_allocations: Mapping[str, float] | None = None,
    expected_build_sha: str | None = None,
    integrity_key: str | None = None,
    policy: StrategyEvidencePolicy | None = None,
    now: datetime | None = None,
) -> dict[str, dict[str, float]]:
    """Validate recent, cost-aware walk-forward evidence for every live symbol."""

    evidence_path = Path(path)
    if not evidence_path.exists():
        raise LiveSafetyError(f"mainnet strategy evidence is missing: {evidence_path}")
    try:
        raw = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise LiveSafetyError(f"mainnet strategy evidence is corrupt: {evidence_path}") from exc
    if not isinstance(raw, dict):
        raise LiveSafetyError(f"mainnet strategy evidence is corrupt: {evidence_path}")
    if integrity_key is not None:
        supplied_integrity = raw.get("integrity")
        unsigned = dict(raw)
        unsigned.pop("integrity", None)
        expected_integrity = _payload_hmac(unsigned, integrity_key)
        if not isinstance(supplied_integrity, str) or not hmac.compare_digest(
            supplied_integrity,
            expected_integrity,
        ):
            raise LiveSafetyError("strategy evidence integrity check failed")
    if raw.get("version") != 1 or raw.get("strategy") != "enhanced_ma":
        raise LiveSafetyError("strategy evidence schema or strategy does not match")
    if raw.get("strategy_params") != dict(expected_params):
        raise LiveSafetyError("strategy evidence parameters do not match the live strategy")
    if expected_build_sha is not None and raw.get("build_sha") != expected_build_sha:
        raise LiveSafetyError("strategy evidence build SHA does not match the live build")

    selected_policy = policy or StrategyEvidencePolicy()
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    generated_at = _parse_utc_datetime(raw.get("generated_at"), "generated_at")
    data_end = _parse_utc_datetime(raw.get("data_end"), "data_end")
    age_limits = {
        "generated_at": selected_policy.max_generated_age_hours,
        "data_end": selected_policy.max_data_age_hours,
    }
    for field_name, timestamp in (("generated_at", generated_at), ("data_end", data_end)):
        age_hours = (current.astimezone(UTC) - timestamp).total_seconds() / 3_600
        if age_hours < -1:
            raise LiveSafetyError(f"strategy evidence {field_name} is future-dated")
        if age_hours > age_limits[field_name]:
            raise LiveSafetyError(
                f"strategy evidence {field_name} is stale: {age_hours:.1f} hours"
            )

    costs = raw.get("costs")
    if not isinstance(costs, dict):
        raise LiveSafetyError("strategy evidence costs are missing")
    try:
        commission_bps = float(costs.get("commission_bps", 0))
        slippage_bps = float(costs.get("slippage_bps", 0))
        spread_bps = float(costs.get("spread_bps", 0))
    except (TypeError, ValueError) as exc:
        raise LiveSafetyError("strategy evidence costs are invalid") from exc
    if commission_bps < selected_policy.min_commission_bps:
        raise LiveSafetyError("strategy evidence commission assumption is too low")
    if slippage_bps < selected_policy.min_slippage_bps:
        raise LiveSafetyError("strategy evidence slippage assumption is too low")
    if spread_bps < selected_policy.min_spread_bps:
        raise LiveSafetyError("strategy evidence spread assumption is too low")

    normalized_allocations: dict[str, float] | None = None
    if expected_allocations is not None:
        allocations = raw.get("allocations")
        if not isinstance(allocations, dict):
            raise LiveSafetyError("strategy evidence allocations are missing")
        normalized_allocations = {}
        if set(allocations) != set(expected_allocations):
            raise LiveSafetyError("strategy evidence allocation symbols do not match")
        for symbol, expected in expected_allocations.items():
            try:
                ë{¶‰žËkºwµç@É•ÑÕÉ¸ì‰É•Ù¥Í¥½¸ˆè€À°€‰…Ñ¥Ù”ˆè9½¹”°€‰Á•¹‘¥¹œˆè9½¹•ô(€€€€€€€…Ñ¥Ù”€ôÁÉ½Ñ•Ñ¥½¸¹•Ð ‰…Ñ¥Ù”ˆ¤(€€€€€€€Á•¹‘¥¹œ€ôÁÉ½Ñ•Ñ¥½¸¹•Ð ‰Á•¹‘¥¹œˆ¤(€€€€€€€É•ÑÕÉ¸ì(€€€€€€€€€€€€‰É•Ù¥Í¥½¸ˆè¥¹Ð¡ÁÉ½Ñ•Ñ¥½¸¹•Ð ‰É•Ù¥Í¥½¸ˆ°€À¤¤°(€€€€€€€€€€€€‰…Ñ¥Ù”ˆè‘¥Ð¡…Ñ¥Ù”¤¥˜¥Í¥¹ÍÑ…¹”¡…Ñ¥Ù”°‘¥Ð¤•±Í”9½¹”°(€€€€€€€€€€€€‰Á•¹‘¥¹œˆè‘¥Ð¡Á•¹‘¥¹œ¤¥˜¥Í¥¹ÍÑ…¹”¡Á•¹‘¥¹œ°‘¥Ð¤•±Í”9½¹”°(€€€€€€€ô((€€€‘•˜É•Í•ÉÙ•}ÁÉ½Ñ•Ñ¥Ù•}½É‘•È (€€€€€€€Í•±˜°(€€€€€€€Íåµ‰½°èÍÑÈ°(€€€€€€€€¨°(€€€€€€€ÅÕ…¹Ñ¥Ñäè™±½…Ð°(€€€€€€€ÍÑ½Á}ÁÉ¥”è™±½…Ð°(€€€€€€€¹½Üè‘…Ñ•Ñ¥µ”ð9½¹”€ô9½¹”°(€€€€¤€´ø‘¥ÑmÍÑÈ°½‰©•Ñtè(€€€€€€€€ˆˆ‰A•ÉÍ¥ÍÐ„ÁÉ½Ñ•Ñ¥Ù”¥¹Ñ•¹ÐÝ¡¥±”É•Ñ…¥¹¥¹œÑ¡”±…ÍÐ…Ñ¥Ù”½É‘•È¸ˆˆˆ((€€€€€€€¥˜…¹ä (€€€€€€€€€€€¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡Ù…±Õ”¤½ÈÙ…±Õ”€ðô€À(€€€€€€€€€€€™½ÈÙ…±Õ”¥¸€¡ÅÕ…¹Ñ¥Ñä°ÍÑ½Á}ÁÉ¥”¤(€€€€€€€€¤è(€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰ÁÉ½Ñ•Ñ¥Ù”ÅÕ…¹Ñ¥Ñä…¹ÍÑ½ÀÁÉ¥”µÕÍÐ‰”Á½Í¥Ñ¥Ù”ˆ¤(€€€€€€€É•½É€ôÍ•±˜¹ÍÑ…Ñ”¹Á½Í¥Ñ¥½¹}É¥Í¬¹•Ð¡Íåµ‰½°¤(€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡É•½É°‘¥Ð¤è(€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰Á½Í¥Ñ¥½¸É¥Í¬¥Ì¹½Ð¥¹¥Ñ¥…±¥é•™½ÈíÍåµ‰½±ôˆ¤(€€€€€€€ÕÉÉ•¹Ð€ô¹½Ü½È‘…Ñ•Ñ¥µ”¹¹½Ü¡UQ¤(€€€€€€€ÁÉ½Ñ•Ñ¥½¸€ôÉ•½É¹•Ð ‰ÁÉ½Ñ•Ñ¥Ù•}½É‘•Èˆ¤(€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡ÁÉ½Ñ•Ñ¥½¸°‘¥Ð¤è(€€€€€€€€€€€ÁÉ½Ñ•Ñ¥½¸€ôì‰É•Ù¥Í¥½¸ˆè€À°€‰…Ñ¥Ù”ˆè9½¹”°€‰Á•¹‘¥¹œˆè9½¹•ô(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡ÁÉ½Ñ•Ñ¥½¸¹•Ð ‰Á•¹‘¥¹œˆ¤°‘¥Ð¤è(€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰ÁÉ½Ñ•Ñ¥Ù”½É‘•ÈÍÕ‰µ¥ÍÍ¥½¸¥Ì…±É•…‘äÁ•¹‘¥¹œ™½ÈíÍåµ‰½±ôˆ¤(€€€€€€€É•Ù¥Í¥½¸€ô¥¹Ð¡ÁÉ½Ñ•Ñ¥½¸¹•Ð ‰É•Ù¥Í¥½¸ˆ°€À¤¤€¬€Ä(€€€€€€€±¥•¹Ñ}½É‘•É}¥€ôµ…­•}ÁÉ½Ñ•Ñ¥Ù•}½É‘•É}­•ä (€€€€€€€€€€€Íåµ‰½°õÍåµ‰½°°(€€€€€€€€€€€É•Ù¥Í¥½¸õÉ•Ù¥Í¥½¸°(€€€€€€€€€€€ÍÑ½Á}ÁÉ¥”õÍÑ½Á}ÁÉ¥”°(€€€€€€€€¤(€€€€€€€Á•¹‘¥¹œè‘¥ÑmÍÑÈ°½‰©•Ñt€ôì(€€€€€€€€€€€€‰±¥•¹Ñ}½É‘•É}¥ˆè±¥•¹Ñ}½É‘•É}¥°(€€€€€€€€€€€€‰•á¡…¹•}½É‘•É}¥ˆè€ˆˆ°(€€€€€€€€€€€€‰ÅÕ…¹Ñ¥ÑäˆèÅÕ…¹Ñ¥Ñä°(€€€€€€€€€€€€‰ÍÑ½Á}ÁÉ¥”ˆèÍÑ½Á}ÁÉ¥”°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè€‰ÍÕ‰µ¥ÑÑ¥¹œˆ°(€€€€€€€€€€€€‰•ÉÉ½Èˆè€ˆˆ°(€€€€€€€€€€€€‰É•…Ñ•‘}…ÐˆèÕÉÉ•¹Ð¹…ÍÑ¥µ•é½¹”¡UQ¤¹¥Í½™½Éµ…Ð ¤°(€€€€€€€€€€€€‰ÕÁ‘…Ñ•‘}…ÐˆèÕÉÉ•¹Ð¹…ÍÑ¥µ•é½¹”¡UQ¤¹¥Í½™½Éµ…Ð ¤°(€€€€€€€ô(€€€€€€€ÁÉ½Ñ•Ñ¥½¸¹ÕÁ‘…Ñ”¡ì‰É•Ù¥Í¥½¸ˆèÉ•Ù¥Í¥½¸°€‰Á•¹‘¥¹œˆèÁ•¹‘¥¹ô¤(€€€€€€€É•½É‘l‰ÁÉ½Ñ•Ñ¥Ù•}½É‘•È‰t€ôÁÉ½Ñ•Ñ¥½¸(€€€€€€€Í•±˜¹Í…Ù”¡¹½ÜõÕÉÉ•¹Ð¤(€€€€€€€É•ÑÕÉ¸‘¥Ð¡Á•¹‘¥¹œ¤((€€€‘•˜ÕÁ‘…Ñ•}Á•¹‘¥¹}ÁÉ½Ñ•Ñ¥Ù•}½É‘•È (€€€€€€€Í•±˜°(€€€€€€€Íåµ‰½°èÍÑÈ°(€€€€€€€€¨°(€€€€€€€ÍÑ…ÑÕÌèÍÑÈ°(€€€€€€€•á¡…¹•}½É‘•É}¥èÍÑÈ€ô€ˆˆ°(€€€€€€€•ÉÉ½ÈèÍÑÈ€ô€ˆˆ°(€€€€€€€¹½Üè‘…Ñ•Ñ¥µ”ð9½¹”€ô9½¹”°(€€€€¤€´ø9½¹”è(€€€€€€€ÁÉ½Ñ•Ñ¥½¸€ôÍ•±˜¹ÁÉ½Ñ•Ñ¥Ù•}½É‘•É}ÍÑ…Ñ”¡Íåµ‰½°¤(€€€€€€€Á•¹‘¥¹œ€ôÁÉ½Ñ•Ñ¥½¸¹•Ð ‰Á•¹‘¥¹œˆ¤(€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡Á•¹‘¥¹œ°‘¥Ð¤è(€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰ÁÉ½Ñ•Ñ¥Ù”½É‘•È¥¹Ñ•¹Ð¥Ì¹½ÐÁ•¹‘¥¹œ™½ÈíÍåµ‰½±ôˆ¤(€€€€€€€¹½Éµ…±¥é•€ôÍÑ…ÑÕÌ¹ÍÑÉ¥À ¤¹±½Ý•È ¤(€€€€€€€…±±½Ý•€ôì(€€€€€€€€€€€€‰ÍÕ‰µ¥ÑÑ¥¹œˆ°€‰½Á•¸ˆ°€‰Á…ÉÑ¥…°ˆ°€‰™¥±±•ˆ°€‰…¹•±±•ˆ°(€€€€€€€€€€€€‰É•©•Ñ•ˆ°€‰•áÁ¥É•ˆ°€‰Õ¹­¹½Ý¸ˆ°(€€€€€€€ô(€€€€€€€¥˜¹½Éµ…±¥é•¹½Ð¥¸…±±½Ý•è(€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰Õ¹ÍÕÁÁ½ÉÑ•ÁÉ½Ñ•Ñ¥Ù”½É‘•ÈÍÑ…ÑÕÌèíÍÑ…ÑÕÍôˆ¤(€€€€€€€ÕÉÉ•¹Ð€ô¹½Ü½È‘…Ñ•Ñ¥µ”¹¹½Ü¡UQ¤(€€€€€€€Á•¹‘¥¹œ¹ÕÁ‘…Ñ”¡ì(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè¹½Éµ…±¥é•°(€€€€€€€€€€€€‰•á¡…¹•}½É‘•É}¥ˆè•á¡…¹•}½É‘•É}¥½ÈÁ•¹‘¥¹œ¹•Ð ‰•á¡…¹•}½É‘•É}¥ˆ°€ˆˆ¤°(€€€€€€€€€€€€‰•ÉÉ½Èˆè•ÉÉ½È°(€€€€€€€€€€€€‰ÕÁ‘…Ñ•‘}…ÐˆèÕÉÉ•¹Ð¹…ÍÑ¥µ•é½¹”¡UQ¤¹¥Í½™½Éµ…Ð ¤°(€€€€€€€ô¤(€€€€€€€É•½É€ôÍ•±˜¹ÍÑ…Ñ”¹Á½Í¥Ñ¥½¹}É¥Í­mÍåµ‰½±t(€€€€€€€É…Ý}ÁÉ½Ñ•Ñ¥½¸€ôÉ•½É‘l‰ÁÉ½Ñ•Ñ¥Ù•}½É‘•È‰t(€€€€€€€É…Ý}ÁÉ½Ñ•Ñ¥½¹l‰Á•¹‘¥¹œ‰t€ôÁ•¹‘¥¹œ(€€€€€€€Í•±˜¹Í…Ù”¡¹½ÜõÕÉÉ•¹Ð¤((€€€‘•˜…Ñ¥Ù…Ñ•}Á•¹‘¥¹}ÁÉ½Ñ•Ñ¥Ù•}½É‘•È (€€€€€€€Í•±˜°(€€€€€€€Íåµ‰½°èÍÑÈ°(€€€€€€€€¨°(€€€€€€€•á¡…¹•}½É‘•É}¥èÍÑÈ°(€€€€€€€ÍÑ…ÑÕÌèÍÑÈ€ô€‰½Á•¸ˆ°(€€€€€€€ÅÕ…¹Ñ¥Ñäè™±½…Ðð9½¹”€ô9½¹”°(€€€€€€€ÍÑ½Á}ÁÉ¥”è™±½…Ðð9½¹”€ô9½¹”°(€€€€€€€¹½Üè‘…Ñ•Ñ¥µ”ð9½¹”€ô9½¹”°(€€€€¤€´ø‘¥ÑmÍÑÈ°½‰©•Ñtè(€€€€€€€€ˆˆ‰AÉ½µ½Ñ”„½¹™¥Éµ•Á•¹‘¥¹œÍÑ½ÀÝ¡¥±”…Ñ½µ¥…±±ä™½É•ÑÑ¥¹œÑ¡”½±½¹”¸ˆˆˆ((€€€€€€€¹½Éµ…±¥é•€ôÍÑ…ÑÕÌ¹ÍÑÉ¥À ¤¹±½Ý•È ¤(€€€€€€€¥˜¹½Éµ…±¥é•¹½Ð¥¸ì‰½Á•¸ˆ°€‰Á…ÉÑ¥…°‰ôè(€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰…¹¹½Ð…Ñ¥Ù…Ñ”Ñ•Éµ¥¹…°ÁÉ½Ñ•Ñ¥Ù”½É‘•ÈèíÍÑ…ÑÕÍôˆ¤(€€€€€€€ÁÉ½Ñ•Ñ¥½¸€ôÍ•±˜¹ÁÉ½Ñ•Ñ¥Ù•}½É‘•É}ÍÑ…Ñ”¡Íåµ‰½°¤(€€€€€€€Á•¹‘¥¹œ€ôÁÉ½Ñ•Ñ¥½¸¹•Ð ‰Á•¹‘¥¹œˆ¤(€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡Á•¹‘¥¹œ°‘¥Ð¤è(€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰ÁÉ½Ñ•Ñ¥Ù”½É‘•È¥¹Ñ•¹Ð¥Ì¹½ÐÁ•¹‘¥¹œ™½ÈíÍåµ‰½±ôˆ¤(€€€€€€€ÕÉÉ•¹Ð€ô¹½Ü½È‘…Ñ•Ñ¥µ”¹¹½Ü¡UQ¤(€€€€€€€¥˜ÅÕ…¹Ñ¥Ñä¥Ì¹½Ð9½¹”è(€€€€€€€€€€€¥˜¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡ÅÕ…¹Ñ¥Ñä¤½ÈÅÕ…¹Ñ¥Ñä€ðô€Àè(€€€€€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰½¹™¥Éµ•ÁÉ½Ñ•Ñ¥Ù”ÅÕ…¹Ñ¥ÑäµÕÍÐ‰”Á½Í¥Ñ¥Ù”ˆ¤(€€€€€€€€€€€Á•¹‘¥¹l‰ÅÕ…¹Ñ¥Ñä‰t€ôÅÕ…¹Ñ¥Ñä(€€€€€€€¥˜ÍÑ½Á}ÁÉ¥”¥Ì¹½Ð9½¹”è(€€€€€€€€€€€¥˜¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡ÍÑ½Á}ÁÉ¥”¤½ÈÍÑ½Á}ÁÉ¥”€ðô€Àè(€€€€€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰½¹™¥Éµ•ÁÉ½Ñ•Ñ¥Ù”ÍÑ½ÀÁÉ¥”µÕÍÐ‰”Á½Í¥Ñ¥Ù”ˆ¤(€€€€€€€€€€€Á•¹‘¥¹l‰ÍÑ½Á}ÁÉ¥”‰t€ôÍÑ½Á}ÁÉ¥”(€€€€€€€Á•¹‘¥¹œ¹ÕÁ‘…Ñ”¡ì(€€€€€€€€€€€€‰•á¡…¹•}½É‘•É}¥ˆè•á¡…¹•}½É‘•É}¥°(€€€€€€€€€€€€‰ÍÑ…ÑÕÌˆè¹½Éµ…±¥é•°(€€€€€€€€€€€€‰•ÉÉ½Èˆè€ˆˆ°(€€€€€€€€€€€€‰ÕÁ‘…Ñ•‘}…ÐˆèÕÉÉ•¹Ð¹…ÍÑ¥µ•é½¹”¡UQ¤¹¥Í½™½Éµ…Ð ¤°(€€€€€€€ô¤(€€€€€€€É•½É€ôÍ•±˜¹ÍÑ…Ñ”¹Á½Í¥Ñ¥½¹}É¥Í­mÍåµ‰½±t(€€€€€€€É…Ý}ÁÉ½Ñ•Ñ¥½¸€ôÉ•½É‘l‰ÁÉ½Ñ•Ñ¥Ù•}½É‘•È‰t(€€€€€€€É…Ý}ÁÉ½Ñ•Ñ¥½¹l‰…Ñ¥Ù”‰t€ôÁ•¹‘¥¹œ(€€€€€€€É…Ý}ÁÉ½Ñ•Ñ¥½¹l‰Á•¹‘¥¹œ‰t€ô9½¹”(€€€€€€€Í•±˜¹Í…Ù”¡¹½ÜõÕÉÉ•¹Ð¤(€€€€€€€É•ÑÕÉ¸‘¥Ð¡Á•¹‘¥¹œ¤((€€€‘•˜…‰…¹‘½¹}Á•¹‘¥¹}ÁÉ½Ñ•Ñ¥Ù•}½É‘•È (€€€€€€€Í•±˜°(€€€€€€€Íåµ‰½°èÍÑÈ°(€€€€€€€€¨°(€€€€€€€¹½Üè‘…Ñ•Ñ¥µ”ð9½¹”€ô9½¹”°(€€€€¤€´ø9½¹”è(€€€€€€€É•½É€ôÍ•±˜¹ÍÑ…Ñ”¹Á½Í¥Ñ¥½¹}É¥Í¬¹•Ð¡Íåµ‰½°¤(€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡É•½É°‘¥Ð¤è(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€ÁÉ½Ñ•Ñ¥½¸€ôÉ•½É¹•Ð ‰ÁÉ½Ñ•Ñ¥Ù•}½É‘•Èˆ¤(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡ÁÉ½Ñ•Ñ¥½¸°‘¥Ð¤…¹ÁÉ½Ñ•Ñ¥½¸¹•Ð ‰Á•¹‘¥¹œˆ¤¥Ì¹½Ð9½¹”è(€€€€€€€€€€€ÁÉ½Ñ•Ñ¥½¹l‰Á•¹‘¥¹œ‰t€ô9½¹”(€€€€€€€€€€€Í•±˜¹Í…Ù”¡¹½Üõ¹½Ü¤((€€€‘•˜±•…É}…Ñ¥Ù•}ÁÉ½Ñ•Ñ¥Ù•}½É‘•È (€€€€€€€Í•±˜°(€€€€€€€Íåµ‰½°èÍÑÈ°(€€€€€€€€¨°(€€€€€€€¹½Üè‘…Ñ•Ñ¥µ”ð9½¹”€ô9½¹”°(€€€€¤€´ø9½¹”è(€€€€€€€É•½É€ôÍ•±˜¹ÍÑ…Ñ”¹Á½Í¥Ñ¥½¹}É¥Í¬¹•Ð¡Íåµ‰½°¤(€€€€€€€¥˜¹½Ð¥Í¥¹ÍÑ…¹”¡É•½É°‘¥Ð¤è(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€ÁÉ½Ñ•Ñ¥½¸€ôÉ•½É¹•Ð ‰ÁÉ½Ñ•Ñ¥Ù•}½É‘•Èˆ¤(€€€€€€€¥˜¥Í¥¹ÍÑ…¹”¡ÁÉ½Ñ•Ñ¥½¸°‘¥Ð¤…¹ÁÉ½Ñ•Ñ¥½¸¹•Ð ‰…Ñ¥Ù”ˆ¤¥Ì¹½Ð9½¹”è(€€€€€€€€€€€ÁÉ½Ñ•Ñ¥½¹l‰…Ñ¥Ù”‰t€ô9½¹”(€€€€€€€€€€€Í•±˜¹Í…Ù”¡¹½Üõ¹½Ü¤((€€€‘•˜±•…É}Á½Í¥Ñ¥½¹}É¥Í¬¡Í•±˜°Íåµ‰½°èÍÑÈ°€¨°¹½Üè‘…Ñ•Ñ¥µ”ð9½¹”€ô9½¹”¤€´ø9½¹”è(€€€€€€€¥˜Íåµ‰½°¥¸Í•±˜¹ÍÑ…Ñ”¹Á½Í¥Ñ¥½¹}É¥Í¬è(€€€€€€€€€€€Í•±˜¹ÍÑ…Ñ”¹Á½Í¥Ñ¥½¹}É¥Í¬¹Á½À¡Íåµ‰½°°9½¹”¤(€€€€€€€€€€€Í•±˜¹Í…Ù”¡¹½Üõ¹½Ü¤((€€€‘•˜Í…Ù”¡Í•±˜°€¨°¹½Üè‘…Ñ•Ñ¥µ”ð9½¹”€ô9½¹”¤€´ø9½¹”è(€€€€€€€ÕÉÉ•¹Ð€ô¹½Ü½È‘…Ñ•Ñ¥µ”¹¹½Ü¡UQ¤(€€€€€€€Í•±˜¹ÍÑ…Ñ”¹ÕÁ‘…Ñ•‘}…Ð€ôÕÉÉ•¹Ð¹…ÍÑ¥µ•é½¹”¡UQ¤¹¥Í½™½Éµ…Ð ¤(€€€€€€€Í•±˜¹Á…Ñ ¹Á…É•¹Ð¹µ­‘¥È¡Á…É•¹ÑÌõQÉÕ”°•á¥ÍÑ}½¬õQÉÕ”¤(€€€€€€€Á…å±½…€ô…Í‘¥Ð¡Í•±˜¹ÍÑ…Ñ”¤(€€€€€€€Á…å±½…¹Á½À ‰¥¹Ñ•É¥Ñäˆ°9½¹”¤(€€€€€€€¥˜Í•±˜¹¥¹Ñ•É¥Ñå}­•ä¥Ì¹½Ð9½¹”è(€€€€€€€€€€€Í•±˜¹ÍÑ…Ñ”¹¥¹Ñ•É¥Ñä€ô}Á…å±½…‘}¡µ…Œ¡Á…å±½…°Í•±˜¹¥¹Ñ•É¥Ñå}­•ä¤(€€€€€€€•±¥˜Í•±˜¹ÍÑ…Ñ”¹¥¹Ñ•É¥Ñäè(€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰…¹¹½Ðµ½‘¥™ä„Í¥¹•±¥Ù”É¥Í¬ÍÑ…Ñ”Ý¥Ñ¡½ÕÐ¥ÑÌ¥¹Ñ•É¥Ñä­•äˆ¤(€€€€€€€Á…å±½…‘l‰¥¹Ñ•É¥Ñä‰t€ôÍ•±˜¹ÍÑ…Ñ”¹¥¹Ñ•É¥Ñä(€€€€€€€™°Ñ•µÁ}¹…µ”€ôÑ•µÁ™¥±”¹µ­ÍÑ•µÀ (€€€€€€€€€€€ÁÉ•™¥àõ˜ˆ¹íÍ•±˜¹Á…Ñ ¹¹…µ•ô¸ˆ°ÍÕ™™¥àôˆ¹ÑµÀˆ°‘¥ÈõÍ•±˜¹Á…Ñ ¹Á…É•¹Ð(€€€€€€€€¤(€€€€€€€ÑÉäè(€€€€€€€€€€€Ý¥Ñ ½Ì¹™‘½Á•¸¡™°€‰Üˆ°•¹½‘¥¹œô‰ÕÑ˜´àˆ¤…Ì¡…¹‘±”è(€€€€€€€€€€€€€€€©Í½¸¹‘ÕµÀ¡Á…å±½…°¡…¹‘±”°¥¹‘•¹ÐôÈ°Í½ÉÑ}­•åÌõQÉÕ”¤(€€€€€€€€€€€€€€€¡…¹‘±”¹™±ÕÍ  ¤(€€€€€€€€€€€€€€€½Ì¹™Íå¹Œ¡¡…¹‘±”¹™¥±•¹¼ ¤¤(€€€€€€€€€€€½Ì¹¡µ½¡Ñ•µÁ}¹…µ”°€Á¼ØÀÀ¤(€€€€€€€€€€€½Ì¹É•Á±…”¡Ñ•µÁ}¹…µ”°Í•±˜¹Á…Ñ ¤(€€€€€€€™¥¹…±±äè(€€€€€€€€€€€A…Ñ ¡Ñ•µÁ}¹…µ”¤¹Õ¹±¥¹¬¡µ¥ÍÍ¥¹}½¬õQÉÕ”¤(()‘•˜Ù…±¥‘…Ñ•}™É•Í¡}ÅÕ½Ñ” (€€€€¨°(€€€Í¥¹…±}ÁÉ¥”è™±½…Ð°(€€€ÅÕ½Ñ•}ÁÉ¥”è™±½…Ð°(€€€ÅÕ½Ñ•}Ñ¥µ•ÍÑ…µÀè‘…Ñ•Ñ¥µ”°(€€€±¥µ¥ÑÌè1¥Ù•I¥Í­1¥µ¥ÑÌ°(€€€¹½Üè‘…Ñ•Ñ¥µ”ð9½¹”€ô9½¹”°(¤€´ø9½¹”è(€€€€ˆˆ‰I•©•ÐÍÑ…±”°™ÕÑÕÉ”µ‘…Ñ•½ÈÍ¡…ÉÁ±ä‘¥Ù•É•¹ÐÅÕ½Ñ•Ì¸ˆˆˆ((€€€¥˜¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡Í¥¹…±}ÁÉ¥”¤½ÈÍ¥¹…±}ÁÉ¥”€ðô€Àè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰Í¥¹…°ÁÉ¥”µÕÍÐ‰”™¥¹¥Ñ”…¹Á½Í¥Ñ¥Ù”ˆ¤(€€€¥˜¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡ÅÕ½Ñ•}ÁÉ¥”¤½ÈÅÕ½Ñ•}ÁÉ¥”€ðô€Àè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰ÅÕ½Ñ”ÁÉ¥”µÕÍÐ‰”™¥¹¥Ñ”…¹Á½Í¥Ñ¥Ù”ˆ¤(€€€ÕÉÉ•¹Ð€ô¹½Ü½È‘…Ñ•Ñ¥µ”¹¹½Ü¡UQ¤(€€€¥˜ÕÉÉ•¹Ð¹Ñé¥¹™¼¥Ì9½¹”è(€€€€€€€ÕÉÉ•¹Ð€ôÕÉÉ•¹Ð¹É•Á±…”¡Ñé¥¹™¼õUQ¤(€€€Ñ¥µ•ÍÑ…µÀ€ôÅÕ½Ñ•}Ñ¥µ•ÍÑ…µÀ(€€€¥˜Ñ¥µ•ÍÑ…µÀ¹Ñé¥¹™¼¥Ì9½¹”è(€€€€€€€Ñ¥µ•ÍÑ…µÀ€ôÑ¥µ•ÍÑ…µÀ¹…ÍÑ¥µ•é½¹” ¤¹…ÍÑ¥µ•é½¹”¡UQ¤(€€€•±Í”è(€€€€€€€Ñ¥µ•ÍÑ…µÀ€ôÑ¥µ•ÍÑ…µÀ¹…ÍÑ¥µ•é½¹”¡UQ¤(€€€…”€ô€¡ÕÉÉ•¹Ð¹…ÍÑ¥µ•é½¹”¡UQ¤€´Ñ¥µ•ÍÑ…µÀ¤¹Ñ½Ñ…±}Í•½¹‘Ì ¤(€€€¥˜…”€ð€´Ôè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰ÅÕ½Ñ”Ñ¥µ•ÍÑ…µÀ¥Ìí…‰Ì¡…”¤è¸Å™õÌ¥¸Ñ¡”™ÕÑÕÉ”ˆ¤(€€€¥˜…”€ø±¥µ¥ÑÌ¹µ…á}ÅÕ½Ñ•}…•}Í•½¹‘Ìè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È (€€€€€€€€€€€˜‰ÅÕ½Ñ”¥ÌÍÑ…±”èí…”è¸Å™õÌ€øí±¥µ¥ÑÌ¹µ…á}ÅÕ½Ñ•}…•}Í•½¹‘Ìè¸Å™õÌˆ(€€€€€€€€¤(€€€‘•Ù¥…Ñ¥½¸€ô…‰Ì¡ÅÕ½Ñ•}ÁÉ¥”€´Í¥¹…±}ÁÉ¥”¤€¼Í¥¹…±}ÁÉ¥”(€€€¥˜‘•Ù¥…Ñ¥½¸€ø±¥µ¥ÑÌ¹µ…á}ÁÉ¥•}‘•Ù¥…Ñ¥½¹}ÁÐè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È (€€€€€€€€€€€˜‰ÅÕ½Ñ”‘•Ù¥…Ñ¥½¸Ñ½¼±…É”èí‘•Ù¥…Ñ¥½¸è¸È•ô€ø€ˆ(€€€€€€€€€€€˜‰í±¥µ¥ÑÌ¹µ…á}ÁÉ¥•}‘•Ù¥…Ñ¥½¹}ÁÐè¸È•ôˆ(€€€€€€€€¤(()‘•˜Ù…±¥‘…Ñ•}ÍÁÉ•… ¨°‰¥è™±½…Ð°…Í¬è™±½…Ð°±¥µ¥ÑÌè1¥Ù•I¥Í­1¥µ¥ÑÌ¤€´ø9½¹”è(€€€¥˜…¹ä¡¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡Ù…±Õ”¤½ÈÙ…±Õ”€ðô€À™½ÈÙ…±Õ”¥¸€¡‰¥°…Í¬¤¤è(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰‰¥…¹…Í¬µÕÍÐ‰”™¥¹¥Ñ”…¹Á½Í¥Ñ¥Ù”ˆ¤(€€€¥˜…Í¬€ð‰¥è(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰É½ÍÍ•µ…É­•ÐÅÕ½Ñ”¥Ì¥¹Ù…±¥ˆ¤(€€€µ¥‘Á½¥¹Ð€ô€¡‰¥€¬…Í¬¤€¼€È(€€€ÍÁÉ•…€ô€¡…Í¬€´‰¥¤€¼µ¥‘Á½¥¹Ð(€€€¥˜ÍÁÉ•…€ø±¥µ¥ÑÌ¹µ…á}ÍÁÉ•…‘}ÁÐè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È (€€€€€€€€€€€˜‰ÍÁÉ•…¥ÌÑ½¼Ý¥‘”èíÍÁÉ•…è¸È•ô€øí±¥µ¥ÑÌ¹µ…á}ÍÁÉ•…‘}ÁÐè¸È•ôˆ(€€€€€€€€¤(()‘•˜Ù…±¥‘…Ñ•}½É‘•É}‰½½­}‘•ÁÑ  (€€€€¨°(€€€Í¥‘”èÍÑÈ°(€€€ÅÕ…¹Ñ¥Ñäè™±½…Ð°(€€€‰¥‘Ìè±¥ÍÑmÑÕÁ±•m™±½…Ð°™±½…Ñut°(€€€…Í­Ìè±¥ÍÑmÑÕÁ±•m™±½…Ð°™±½…Ñut°(€€€‰½½­}Ñ¥µ•ÍÑ…µÀè‘…Ñ•Ñ¥µ”°(€€€±¥µ¥ÑÌè1¥Ù•I¥Í­1¥µ¥ÑÌ°(€€€¹½Üè‘…Ñ•Ñ¥µ”ð9½¹”€ô9½¹”°(¤€´ø™±½…Ðè(€€€€ˆˆ‰I•ÑÕÉ¸•áÁ•Ñ•Y]@…™Ñ•ÈÉ•©•Ñ¥¹œÍÑ…±”°Ñ¡¥¸½È¡¥ µ¥µÁ…Ð‰½½­Ì¸ˆˆˆ((€€€¥˜¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡ÅÕ…¹Ñ¥Ñä¤½ÈÅÕ…¹Ñ¥Ñä€ðô€Àè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰½É‘•Èµ‰½½¬ÅÕ…¹Ñ¥ÑäµÕÍÐ‰”™¥¹¥Ñ”…¹Á½Í¥Ñ¥Ù”ˆ¤(€€€ÕÉÉ•¹Ð€ô¹½Ü½È‘…Ñ•Ñ¥µ”¹¹½Ü¡UQ¤(€€€¥˜ÕÉÉ•¹Ð¹Ñé¥¹™¼¥Ì9½¹”è(€€€€€€€ÕÉÉ•¹Ð€ôÕÉÉ•¹Ð¹É•Á±…”¡Ñé¥¹™¼õUQ¤(€€€Ñ¥µ•ÍÑ…µÀ€ô‰½½­}Ñ¥µ•ÍÑ…µÀ(€€€¥˜Ñ¥µ•ÍÑ…µÀ¹Ñé¥¹™¼¥Ì9½¹”è(€€€€€€€Ñ¥µ•ÍÑ…µÀ€ôÑ¥µ•ÍÑ…µÀ¹É•Á±…”¡Ñé¥¹™¼õUQ¤(€€€…”€ô€¡ÕÉÉ•¹Ð¹…ÍÑ¥µ•é½¹”¡UQ¤€´Ñ¥µ•ÍÑ…µÀ¹…ÍÑ¥µ•é½¹”¡UQ¤¤¹Ñ½Ñ…±}Í•½¹‘Ì ¤(€€€¥˜…”€ð€´Ô½È…”€ø±¥µ¥ÑÌ¹µ…á}ÅÕ½Ñ•}…•}Í•½¹‘Ìè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰½É‘•È‰½½¬Ñ¥µ•ÍÑ…µÀ¥Ì¥¹Ù…±¥½ÈÍÑ…±”èí…”è¸Å™õÌˆ¤((€€€¹½Éµ…±¥é•‘}Í¥‘”€ôÍ¥‘”¹ÕÁÁ•È ¤(€€€¥˜¹½Éµ…±¥é•‘}Í¥‘”¹½Ð¥¸ì‰	Udˆ°€‰M10‰ôè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰Õ¹ÍÕÁÁ½ÉÑ•½É‘•Èµ‰½½¬Í¥‘”èíÍ¥‘•ôˆ¤(€€€±•Ù•±Ì€ô…Í­Ì¥˜¹½Éµ…±¥é•‘}Í¥‘”€ôô€‰	Udˆ•±Í”‰¥‘Ì(€€€¥˜¹½Ð±•Ù•±Ìè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰•á•ÕÑ…‰±”½É‘•È‰½½¬¥Ì•µÁÑäˆ¤(€€€±•…¹•è±¥ÍÑmÑÕÁ±•m™±½…Ð°™±½…Ñut€ômt(€€€™½ÈÁÉ¥”°Í¥é”¥¸±•Ù•±Ìè(€€€€€€€¥˜…¹ä¡¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡Ù…±Õ”¤½ÈÙ…±Õ”€ðô€À™½ÈÙ…±Õ”¥¸€¡ÁÉ¥”°Í¥é”¤¤è(€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰½É‘•È‰½½¬½¹Ñ…¥¹Ì¥¹Ù…±¥±•Ù•±Ìˆ¤(€€€€€€€±•…¹•¹…ÁÁ•¹ ¡ÁÉ¥”°Í¥é”¤¤(€€€ÁÉ¥•Ì€ômÁÉ¥”™½ÈÁÉ¥”°|¥¸±•…¹•‘t(€€€¥˜¹½Éµ…±¥é•‘}Í¥‘”€ôô€‰	Udˆ…¹ÁÉ¥•Ì€„ôÍ½ÉÑ•¡ÁÉ¥•Ì¤è(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰…Í¬±•Ù•±Ì…É”¹½ÐÍ½ÉÑ•ˆ¤(€€€¥˜¹½Éµ…±¥é•‘}Í¥‘”€ôô€‰M10ˆ…¹ÁÉ¥•Ì€„ôÍ½ÉÑ•¡ÁÉ¥•Ì°É•Ù•ÉÍ”õQÉÕ”¤è(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰‰¥±•Ù•±Ì…É”¹½ÐÍ½ÉÑ•ˆ¤((€€€É•ÅÕ¥É•‘}‘•ÁÑ €ôÅÕ…¹Ñ¥Ñä€¨±¥µ¥ÑÌ¹µ¥¹}‰½½­}‘•ÁÑ¡}µÕ±Ñ¥Á±”(€€€Ñ½Ñ…±}‘•ÁÑ €ôÍÕ´¡Í¥é”™½È|°Í¥é”¥¸±•…¹•¤(€€€¥˜Ñ½Ñ…±}‘•ÁÑ €¬€Å”´ÄÈ€ðÉ•ÅÕ¥É•‘}‘•ÁÑ è(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È (€€€€€€€€€€€˜‰½É‘•Èµ‰½½¬‘•ÁÑ ¥Ì¥¹ÍÕ™™¥¥•¹ÐèíÑ½Ñ…±}‘•ÁÑ è¸á™ô€ðíÉ•ÅÕ¥É•‘}‘•ÁÑ è¸á™ôˆ(€€€€€€€€¤(€€€É•µ…¥¹¥¹œ€ôÅÕ…¹Ñ¥Ñä(€€€¹½Ñ¥½¹…°€ô€À¸À(€€€™½ÈÁÉ¥”°Í¥é”¥¸±•…¹•è(€€€€€€€™¥±±•€ôµ¥¸¡É•µ…¥¹¥¹œ°Í¥é”¤(€€€€€€€¹½Ñ¥½¹…°€¬ô™¥±±•€¨ÁÉ¥”(€€€€€€€É•µ…¥¹¥¹œ€´ô™¥±±•(€€€€€€€¥˜É•µ…¥¹¥¹œ€ðô€Å”´ÄÈè(€€€€€€€€€€€‰É•…¬(€€€¥˜É•µ…¥¹¥¹œ€ø€Å”´ÄÈè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰½É‘•È‰½½¬…¹¹½Ð™¥±°Ñ¡”É•ÅÕ•ÍÑ•ÅÕ…¹Ñ¥Ñäˆ¤(€€€•áÁ•Ñ•‘}ÙÝ…À€ô¹½Ñ¥½¹…°€¼ÅÕ…¹Ñ¥Ñä(€€€‰•ÍÐ€ô±•…¹•‘lÁulÁt(€€€¥µÁ…Ð€ô…‰Ì¡•áÁ•Ñ•‘}ÙÝ…À€´‰•ÍÐ¤€¼‰•ÍÐ(€€€¥˜¥µÁ…Ð€ø±¥µ¥ÑÌ¹µ…á}‰½½­}Í±¥ÁÁ…•}ÁÐè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È (€€€€€€€€€€€˜‰•áÁ•Ñ•‰½½¬Í±¥ÁÁ…”¥ÌÑ½¼¡¥ è€ˆ(€€€€€€€€€€€˜‰í¥µÁ…Ðè¸È•ô€øí±¥µ¥ÑÌ¹µ…á}‰½½­}Í±¥ÁÁ…•}ÁÐè¸È•ôˆ(€€€€€€€€¤(€€€É•ÑÕÉ¸•áÁ•Ñ•‘}ÙÝ…À(()‘•˜Ù…±¥‘…Ñ•}½É‘•É}É¥Í¬ (€€€€¨°(€€€Í¥‘”èÍÑÈ°(€€€¹½Ñ¥½¹…±}ÕÍè™±½…Ð°(€€€•ÅÕ¥Ñäè™±½…Ð°(€€€…Í è™±½…Ð°(€€€ÕÉÉ•¹Ñ}Íåµ‰½±}¹½Ñ¥½¹…°è™±½…Ð°(€€€É½ÍÍ}•áÁ½ÍÕÉ”è™±½…Ð°(€€€±¥µ¥ÑÌè1¥Ù•I¥Í­1¥µ¥ÑÌ°(€€€±½­•‘}É•…Í½¸èÍÑÈð9½¹”°(¤€´ø9½¹”è(€€€€ˆˆ‰Y…±¥‘…Ñ”„ÁÉ½Á½Í•½É‘•È……¥¹ÍÐ…½Õ¹Ðµ±•Ù•°¡…É±¥µ¥ÑÌ¸ˆˆˆ((€€€Ù…±Õ•Ì€ô€¡¹½Ñ¥½¹…±}ÕÍ°•ÅÕ¥Ñä°…Í °ÕÉÉ•¹Ñ}Íåµ‰½±}¹½Ñ¥½¹…°°É½ÍÍ}•áÁ½ÍÕÉ”¤(€€€¥˜…¹ä¡¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡Ù…±Õ”¤½ÈÙ…±Õ”€ð€À™½ÈÙ…±Õ”¥¸Ù…±Õ•Ì¤½È•ÅÕ¥Ñä€ðô€Àè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰¥¹Ù…±¥…½Õ¹Ð½È½É‘•ÈÙ…±Õ”ˆ¤(€€€¹½Éµ…±¥é•‘}Í¥‘”€ôÍ¥‘”¹ÕÁÁ•È ¤(€€€¥˜¹½Éµ…±¥é•‘}Í¥‘”¹½Ð¥¸ì‰	Udˆ°€‰M10‰ôè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰Õ¹ÍÕÁÁ½ÉÑ•½É‘•ÈÍ¥‘”èíÍ¥‘•ôˆ¤(€€€¥˜¹½Ñ¥½¹…±}ÕÍ€ðô€Àè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰½É‘•È¹½Ñ¥½¹…°µÕÍÐ‰”Á½Í¥Ñ¥Ù”ˆ¤((€€€¥˜¹½Éµ…±¥é•‘}Í¥‘”€ôô€‰M10ˆè(€€€€€€€¥˜¹½Ñ¥½¹…±}ÕÍ€øÕÉÉ•¹Ñ}Íåµ‰½±}¹½Ñ¥½¹…°€¨€Ä¸ÀÄè(€€€€€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰Í•±°½É‘•È•á••‘ÌÑ¡”ÕÉÉ•¹ÐÁ½Í¥Ñ¥½¸ˆ¤(€€€€€€€É•ÑÕÉ¸((€€€¥˜±½­•‘}É•…Í½¸è(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È¡˜‰É¥Í¬¥ÉÕ¥Ð‰É•…­•È¥Ì±½­•èí±½­•‘}É•…Í½¹ôˆ¤(€€€¥˜¹½Ñ¥½¹…±}ÕÍ€ø±¥µ¥ÑÌ¹µ…á}½É‘•É}¹½Ñ¥½¹…±}ÕÍè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È (€€€€€€€€€€€˜‰½É‘•È¹½Ñ¥½¹…°€‘í¹½Ñ¥½¹…±}ÕÍè°¸É™ô•á••‘Ì€ˆ(€€€€€€€€€€€˜ˆ‘í±¥µ¥ÑÌ¹µ…á}½É‘•É}¹½Ñ¥½¹…±}ÕÍè°¸É™ôˆ(€€€€€€€€¤(€€€¥˜ÕÉÉ•¹Ñ}Íåµ‰½±}¹½Ñ¥½¹…°€¬¹½Ñ¥½¹…±}ÕÍ€ø•ÅÕ¥Ñä€¨±¥µ¥ÑÌ¹µ…á}Íåµ‰½±}•áÁ½ÍÕÉ•}ÁÐè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰Á½ÍÐµÑÉ…‘”Íåµ‰½°•áÁ½ÍÕÉ”•á••‘Ì±¥µ¥Ðˆ¤(€€€¥˜É½ÍÍ}•áÁ½ÍÕÉ”€¬¹½Ñ¥½¹…±}ÕÍ€ø•ÅÕ¥Ñä€¨±¥µ¥ÑÌ¹µ…á}É½ÍÍ}•áÁ½ÍÕÉ•}ÁÐè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰Á½ÍÐµÑÉ…‘”É½ÍÌ•áÁ½ÍÕÉ”•á••‘Ì±¥µ¥Ðˆ¤(€€€¥˜…Í €´¹½Ñ¥½¹…±}ÕÍ€ð•ÅÕ¥Ñä€¨±¥µ¥ÑÌ¹µ¥¹}…Í¡}É•Í•ÉÙ•}ÁÐè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰Á½ÍÐµÑÉ…‘”…Í É•Í•ÉÙ”Ý½Õ±™…±°‰•±½Ü±¥µ¥Ðˆ¤(()‘•˜µ…­•}½É‘•É}­•ä ¨°Íåµ‰½°èÍÑÈ°Í¥‘”èÍÑÈ°…¹‘±•}Ñ¥µ•ÍÑ…µÀè‘…Ñ•Ñ¥µ”¤€´øÍÑÈè(€€€€ˆˆ‰	Õ¥±„ÍÑ…‰±”	¥¹…¹”µ½µÁ…Ñ¥‰±”±¥•¹Ð½É‘•È¥‘•¹Ñ¥™¥•È¸ˆˆˆ((€€€Ñ¥µ•ÍÑ…µÀ€ô…¹‘±•}Ñ¥µ•ÍÑ…µÀ(€€€¥˜Ñ¥µ•ÍÑ…µÀ¹Ñé¥¹™¼¥Ì9½¹”è(€€€€€€€Ñ¥µ•ÍÑ…µÀ€ôÑ¥µ•ÍÑ…µÀ¹É•Á±…”¡Ñé¥¹™¼õUQ¤(€€€É…Ü€ô˜‰•¹¡…¹•µµ„µØÅñíÍåµ‰½°¹ÕÁÁ•È ¥õñíÍ¥‘”¹ÕÁÁ•È ¥õñíÑ¥µ•ÍÑ…µÀ¹…ÍÑ¥µ•é½¹”¡UQ¤¹¥Í½™½Éµ…Ð ¥ôˆ(€€€‘¥•ÍÐ€ô¡…Í¡±¥ˆ¹Í¡„ÈÔØ¡É…Ü¹•¹½‘” ‰ÕÑ˜´àˆ¤¤¹¡•á‘¥•ÍÐ ¥lèÈÑt(€€€É•ÑÕÉ¸˜‰±Ñ„µí‘¥•ÍÑôˆ(()‘•˜µ…­•}ÁÉ½Ñ•Ñ¥Ù•}½É‘•É}­•ä (€€€€¨°(€€€Íåµ‰½°èÍÑÈ°(€€€É•Ù¥Í¥½¸è¥¹Ð°(€€€ÍÑ½Á}ÁÉ¥”è™±½…Ð°(¤€´øÍÑÈè(€€€€ˆˆ‰	Õ¥±„Õ¹¥ÅÕ”°É•ÑÉäµÍÑ…‰±”	¥¹…¹”±¥•¹Ð%™½È½¹”ÍÑ½ÀÉ•Ù¥Í¥½¸¸ˆˆˆ((€€€¥˜É•Ù¥Í¥½¸€ðô€Àè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰ÁÉ½Ñ•Ñ¥Ù”½É‘•ÈÉ•Ù¥Í¥½¸µÕÍÐ‰”Á½Í¥Ñ¥Ù”ˆ¤(€€€¥˜¹½Ðµ…Ñ ¹¥Í™¥¹¥Ñ”¡ÍÑ½Á}ÁÉ¥”¤½ÈÍÑ½Á}ÁÉ¥”€ðô€Àè(€€€€€€€É…¥Í”1¥Ù•M…™•ÑåÉÉ½È ‰ÁÉ½Ñ•Ñ¥Ù”ÍÑ½ÀÁÉ¥”µÕÍÐ‰”™¥¹¥Ñ”…¹Á½Í¥Ñ¥Ù”ˆ¤(€€€É…Ü€ô˜‰•¹¡…¹•µµ„µÍÑ½ÀµØÅñíÍåµ‰½°¹ÕÁÁ•È ¥õñíÉ•Ù¥Í¥½¹õñíÍÑ½Á}ÁÉ¥”è¸ÄÉôˆ(€€€‘¥•ÍÐ€ô¡…Í¡±¥ˆ¹Í¡„ÈÔØ¡É…Ü¹•¹½‘” ‰ÕÑ˜´àˆ¤¤¹¡•á‘¥•ÍÐ ¥lèÈÁt(€€€É•ÑÕÉ¸˜‰±Ñ„µÁÌµíÉ•Ù¥Í¥½¹ôµí‘¥•ÍÑô‰lèÌÙt(