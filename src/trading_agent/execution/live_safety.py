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
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Mapping

from trading_agent.execution.correlation import get_correlation_id


LIVE_CONFIRMATION = "LIVE_TRADING_WITH_REAL_MONEY"
RISK_INCREASE_CONFIRMATION = "APPROVE_LIVE_RISK_INCREASE"
STATE_VERSION = 1

ORDER_LEDGER_STATUSES = frozenset({
    "reserved", "submitted", "acknowledged", "open", "partial", "filled",
    "cancelled", "rejected", "expired", "reconciling",
    "manual_intervention", "unknown",
})
ORDER_LEDGER_TERMINAL_STATUSES = frozenset({
    "filled", "cancelled", "rejected", "expired",
})
ORDER_LEDGER_TRANSITIONS = {
    "reserved": {
        "submitted", "reconciling", "manual_intervention",
    },
    "submitted": {
        "acknowledged", "reconciling", "manual_intervention",
    },
    "acknowledged": {
        "open", "partial", "filled", "cancelled", "rejected", "expired",
        "reconciling", "manual_intervention",
    },
    "open": {
        "partial", "filled", "cancelled", "rejected", "expired",
        "reconciling", "manual_intervention",
    },
    "partial": {
        "filled", "cancelled", "expired", "reconciling",
        "manual_intervention",
    },
    "reconciling": {
        "acknowledged", "manual_intervention",
    },
    "manual_intervention": {"reconciling"},
    # Kept only to migrate signed state written by older releases.
    "unknown": {"reconciling", "manual_intervention"},
    "filled": set(),
    "cancelled": set(),
    "rejected": set(),
    "expired": set(),
}


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
    if raw is None:
        return default
    # os.environ always yields str, but from_env(env=dict) accepts programmatic
    # envs too — non-str must fail closed with LiveSafetyError, never crash
    # with a raw AttributeError (which would be an unhandled fail-open).
    if isinstance(raw, str):
        if not raw.strip():
            return default
        raw = raw.strip()
    elif not isinstance(raw, (int, float)):
        raise LiveSafetyError(f"{name} must be a number")
    try:
        value = float(raw)
    except (ValueError, TypeError) as exc:
        raise LiveSafetyError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise LiveSafetyError(f"{name} must be finite")
    return value


@dataclass(frozen=True)
class LiveRiskLimits:
    """Hard limits evaluated immediately before every real order."""

    max_order_notional_usd: float = 100.0
    max_order_equity_pct: float = 1.0
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
    max_dust_notional_usd: float = 5.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LiveRiskLimits":
        source = os.environ if env is None else env
        limits = cls(
            max_order_notional_usd=_read_float(source, "LIVE_MAX_ORDER_USD", 100.0),
            max_order_equity_pct=_read_float(
                source, "LIVE_MAX_ORDER_EQUITY_PCT", 1.0
            ),
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
            max_dust_notional_usd=_read_float(
                source, "LIVE_MAX_DUST_USD", 5.0
            ),
        )
        limits.validate()
        return limits

    @classmethod
    def for_profile(
        cls,
        profile: str,
        env: Mapping[str, str] | None = None,
    ) -> "LiveRiskLimits":
        """Build hard limits for an explicit deployment profile."""

        normalized = profile.strip().lower()
        if normalized not in {"testnet", "mainnet-canary", "mainnet-normal"}:
            raise LiveSafetyError(f"unsupported live trading profile: {profile}")
        configured = cls.from_env(env)
        if normalized != "mainnet-canary":
            return configured
        canary = replace(
            configured,
            max_order_notional_usd=min(configured.max_order_notional_usd, 25.0),
            max_order_equity_pct=min(configured.max_order_equity_pct, 0.0025),
            max_symbol_exposure_pct=min(configured.max_symbol_exposure_pct, 0.05),
            max_gross_exposure_pct=min(configured.max_gross_exposure_pct, 0.10),
            min_cash_reserve_pct=max(configured.min_cash_reserve_pct, 0.80),
            max_daily_loss_pct=min(configured.max_daily_loss_pct, 0.005),
            max_drawdown_pct=min(configured.max_drawdown_pct, 0.02),
        )
        canary.validate()
        return canary

    def effective_max_order_notional(self, equity: float) -> float:
        if not math.isfinite(equity) or equity <= 0:
            raise LiveSafetyError("account equity must be finite and positive")
        return min(self.max_order_notional_usd, equity * self.max_order_equity_pct)

    def validate(self) -> None:
        if self.max_order_notional_usd <= 0:
            raise LiveSafetyError("LIVE_MAX_ORDER_USD must be positive")
        if not 0 < self.max_order_equity_pct <= 1:
            raise LiveSafetyError("LIVE_MAX_ORDER_EQUITY_PCT must be between 0 and 1")
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
        if not 0 <= self.max_dust_notional_usd <= min(
            self.max_order_notional_usd,
            10.0,
        ):
            raise LiveSafetyError(
                "LIVE_MAX_DUST_USD must be between 0 and min(LIVE_MAX_ORDER_USD, 10)"
            )


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
    payload: dict[str, object] = {
        "timestamp": current.astimezone(UTC).isoformat(),
        "event": event,
        "pid": os.getpid(),
        "details": dict(details or {}),
    }
    # P1.2: tag every audit event with the active run correlation ID so all
    # events from one runner invocation can be traced end-to-end.
    correlation_id = get_correlation_id()
    if correlation_id:
        payload["correlation_id"] = correlation_id
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
                actual = float(allocations[symbol])
            except (TypeError, ValueError) as exc:
                raise LiveSafetyError("strategy evidence allocations are invalid") from exc
            if not math.isfinite(actual) or not math.isclose(actual, expected, abs_tol=1e-12):
                raise LiveSafetyError(f"strategy evidence allocation does not match for {symbol}")
            normalized_allocations[symbol] = actual

    evidence_symbols = raw.get("symbols")
    if not isinstance(evidence_symbols, dict):
        raise LiveSafetyError("strategy evidence symbols are missing")
    summaries: dict[str, dict[str, float]] = {}
    reference_layout: list[tuple[object, object, object]] | None = None
    for symbol in expected_symbols:
        item = evidence_symbols.get(symbol)
        folds = item.get("folds") if isinstance(item, dict) else None
        if not isinstance(folds, list) or len(folds) < selected_policy.min_folds:
            raise LiveSafetyError(
                f"strategy evidence for {symbol} needs at least {selected_policy.min_folds} folds"
            )
        layout = [
            (
                fold.get("start") if isinstance(fold, dict) else None,
                fold.get("end") if isinstance(fold, dict) else None,
                fold.get("bars") if isinstance(fold, dict) else None,
            )
            for fold in folds
        ]
        if reference_layout is None:
            reference_layout = layout
        elif layout != reference_layout:
            raise LiveSafetyError("strategy evidence symbol folds do not align")
        if normalized_allocations is not None:
            try:
                item_allocation = float(item.get("allocation"))
            except (TypeError, ValueError) as exc:
                raise LiveSafetyError(f"strategy evidence allocation is invalid for {symbol}") from exc
            if not math.isclose(
                item_allocation,
                normalized_allocations[symbol],
                abs_tol=1e-12,
            ):
                raise LiveSafetyError(f"strategy evidence allocation does not match for {symbol}")
        summaries[symbol] = _validate_evidence_folds(symbol, folds, selected_policy)

    if normalized_allocations is not None:
        portfolio = raw.get("portfolio")
        portfolio_folds = portfolio.get("folds") if isinstance(portfolio, dict) else None
        if not isinstance(portfolio_folds, list):
            raise LiveSafetyError("portfolio OOS evidence is missing")
        portfolio_layout = [
            (
                fold.get("start") if isinstance(fold, dict) else None,
                fold.get("end") if isinstance(fold, dict) else None,
                fold.get("bars") if isinstance(fold, dict) else None,
            )
            for fold in portfolio_folds
        ]
        if portfolio_layout != reference_layout:
            raise LiveSafetyError("portfolio OOS folds do not align with symbols")
        summaries["__portfolio__"] = _validate_evidence_folds(
            "portfolio",
            portfolio_folds,
            selected_policy,
        )
    return summaries


def _validate_evidence_folds(
    label: str,
    folds: list[object],
    policy: StrategyEvidencePolicy,
) -> dict[str, float]:
    if len(folds) < policy.min_folds:
        raise LiveSafetyError(f"strategy evidence for {label} needs at least {policy.min_folds} folds")
    sharpes: list[float] = []
    returns: list[float] = []
    drawdowns: list[float] = []
    trades: list[int] = []
    previous_end: datetime | None = None
    for fold in folds:
        if not isinstance(fold, dict):
            raise LiveSafetyError(f"strategy evidence metrics are invalid for {label}")
        try:
            start = _parse_utc_datetime(fold["start"], f"{label}.fold.start")
            end = _parse_utc_datetime(fold["end"], f"{label}.fold.end")
            bars = int(fold["bars"])
            sharpes.append(float(fold["sharpe"]))
            returns.append(float(fold["return_pct"]))
            drawdowns.append(abs(float(fold["max_drawdown_pct"])))
            trades.append(int(fold["trades"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise LiveSafetyError(f"strategy evidence metrics are invalid for {label}") from exc
        duration_hours = (end - start).total_seconds() / 3_600
        if duration_hours < policy.min_fold_days * 24:
            raise LiveSafetyError(f"strategy evidence fold is too short for {label}")
        if bars != int(duration_hours):
            raise LiveSafetyError(f"strategy evidence fold contains hourly gaps for {label}")
        if previous_end is not None and start != previous_end:
            raise LiveSafetyError(f"strategy evidence folds are not contiguous for {label}")
        previous_end = end
    all_metrics = [*sharpes, *returns, *drawdowns, *trades]
    if any(not math.isfinite(value) for value in all_metrics) or any(value < 0 for value in trades):
        raise LiveSafetyError(f"strategy evidence contains invalid metrics for {label}")

    median_sharpe = median(sharpes)
    median_return = median(returns)
    positive_ratio = sum(value > 0 for value in returns) / len(returns)
    worst_drawdown = max(drawdowns)
    total_trades = sum(trades)
    if median_sharpe < policy.min_median_oos_sharpe:
        raise LiveSafetyError(f"{label} median OOS Sharpe does not pass")
    if median_return <= policy.min_median_oos_return_pct:
        raise LiveSafetyError(f"{label} median OOS return does not pass")
    if positive_ratio < policy.min_positive_fold_ratio:
        raise LiveSafetyError(f"{label} positive-fold ratio does not pass")
    if worst_drawdown > policy.max_worst_oos_drawdown_pct:
        raise LiveSafetyError(f"{label} worst OOS drawdown does not pass")
    if total_trades < policy.min_total_oos_trades:
        raise LiveSafetyError(f"{label} OOS trade count does not pass")
    return {
        "median_sharpe": median_sharpe,
        "median_return_pct": median_return,
        "positive_fold_ratio": positive_ratio,
        "worst_drawdown_pct": worst_drawdown,
        "total_trades": float(total_trades),
    }


def require_execution_authorization(
    *,
    execute: bool,
    testnet: bool,
    cli_confirmation: str | None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Require independent environment and CLI gates before order submission.

    ``TRADING_KILL_SWITCH`` is the hard stop and blocks every submission.  The
    entry-only switch is evaluated separately so an authorized runner can
    still submit a risk-reducing sell.
    """

    if not execute:
        return
    source = os.environ if env is None else env
    if _is_true(source.get("TRADING_KILL_SWITCH")):
        raise LiveSafetyError("TRADING_KILL_SWITCH is active")
    if not _is_true(source.get("TRADING_EXECUTION_ENABLED")):
        raise LiveSafetyError("TRADING_EXECUTION_ENABLED is not true")

    expected_mode = "testnet" if testnet else "live"
    actual_mode = (source.get("TRADING_MODE") or "").strip().lower()
    if actual_mode != expected_mode:
        raise LiveSafetyError(f"TRADING_MODE must be {expected_mode!r}")

    if testnet:
        return
    env_confirmation = source.get("TRADING_LIVE_CONFIRMATION", "")
    if not hmac.compare_digest(env_confirmation, LIVE_CONFIRMATION):
        raise LiveSafetyError("TRADING_LIVE_CONFIRMATION is missing or invalid")
    if not hmac.compare_digest(cli_confirmation or "", LIVE_CONFIRMATION):
        raise LiveSafetyError("--confirm-live is missing or invalid")


def configured_entry_lock_reason(
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Return the configured entry lock without disabling risk-reducing exits."""

    source = os.environ if env is None else env
    if _is_true(source.get("TRADING_ENTRY_KILL_SWITCH")):
        return "TRADING_ENTRY_KILL_SWITCH is active"
    return None


@dataclass
class LiveRiskState:
    version: int = STATE_VERSION
    peak_equity: float = 0.0
    daily_start_equity: float = 0.0
    trading_day: str = ""
    locked_reason: str | None = None
    reserved_orders: dict[str, str] = field(default_factory=dict)
    order_ledger: dict[str, dict[str, object]] = field(default_factory=dict)
    position_risk: dict[str, dict[str, object]] = field(default_factory=dict)
    account_fingerprint: str = ""
    strategy_fingerprint: str = ""
    managed_symbols: list[str] = field(default_factory=list)
    risk_profile: str = ""
    risk_limits: dict[str, float] = field(default_factory=dict)
    integrity: str = ""
    updated_at: str = ""


class LiveRiskStateStore:
    """Atomic persistent state for loss limits and order idempotency."""

    def __init__(self, path: str | Path, *, integrity_key: str | None = None):
        self.path = Path(path)
        self.integrity_key = (
            validate_integrity_key(integrity_key) if integrity_key is not None else None
        )
        self.existed = self.path.exists()
        self.state = self._load()

    def _load(self) -> LiveRiskState:
        if not self.path.exists():
            return LiveRiskState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if self.integrity_key is not None:
                supplied_integrity = raw.get("integrity")
                unsigned = dict(raw)
                unsigned.pop("integrity", None)
                expected_integrity = _payload_hmac(unsigned, self.integrity_key)
                if not isinstance(supplied_integrity, str) or not hmac.compare_digest(
                    supplied_integrity,
                    expected_integrity,
                ):
                    raise LiveSafetyError(f"live risk state integrity check failed: {self.path}")
            state = LiveRiskState(**raw)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise LiveSafetyError(f"corrupt live risk state: {self.path}") from exc
        if state.version != STATE_VERSION:
            raise LiveSafetyError(f"unsupported live risk state version: {state.version}")
        numeric = (state.peak_equity, state.daily_start_equity)
        if any(not math.isfinite(value) or value < 0 for value in numeric):
            raise LiveSafetyError(f"invalid equity values in live risk state: {self.path}")
        if not isinstance(state.reserved_orders, dict):
            raise LiveSafetyError(f"invalid reserved_orders in live risk state: {self.path}")
        if not isinstance(state.order_ledger, dict):
            raise LiveSafetyError(f"invalid order_ledger in live risk state: {self.path}")
        if not isinstance(state.position_risk, dict):
            raise LiveSafetyError(f"invalid position_risk in live risk state: {self.path}")
        if not isinstance(state.managed_symbols, list):
            raise LiveSafetyError(f"invalid managed_symbols in live risk state: {self.path}")
        if not isinstance(state.risk_limits, dict):
            raise LiveSafetyError(f"invalid risk_limits in live risk state: {self.path}")
        allowed_profiles = {"", "testnet", "mainnet-canary", "mainnet-normal"}
        if state.risk_profile not in allowed_profiles:
            raise LiveSafetyError(f"invalid risk_profile in live risk state: {self.path}")
        if bool(state.risk_profile) != bool(state.risk_limits):
            raise LiveSafetyError(f"incomplete risk profile in live risk state: {self.path}")
        if state.risk_limits:
            try:
                LiveRiskLimits(**state.risk_limits).validate()
            except (TypeError, LiveSafetyError) as exc:
                raise LiveSafetyError(
                    f"invalid risk limits in live risk state: {self.path}"
                ) from exc
        return state

    def bind_context(
        self,
        *,
        account: str,
        strategy: str,
        symbols: list[str],
    ) -> None:
        """Bind a state file to one account and one immutable strategy context."""

        normalized_symbols = sorted(symbols)
        existing = (
            self.state.account_fingerprint,
            self.state.strategy_fingerprint,
            sorted(self.state.managed_symbols),
        )
        requested = (account, strategy, normalized_symbols)
        if any(existing) and existing != requested:
            raise LiveSafetyError(
                "live risk state belongs to a different account, strategy or symbol set"
            )
        self.state.account_fingerprint = account
        self.state.strategy_fingerprint = strategy
        self.state.managed_symbols = normalized_symbols
        self.save()

    def bind_risk_limits(
        self,
        *,
        profile: str,
        limits: LiveRiskLimits,
        approve_increase: bool = False,
    ) -> dict[str, object] | None:
        """Persist limits and block any silent increase in allowed risk."""

        normalized_profile = profile.strip().lower()
        if normalized_profile not in {"testnet", "mainnet-canary", "mainnet-normal"}:
            raise LiveSafetyError(f"unsupported live trading profile: {profile}")
        limits.validate()
        requested = {
            key: float(value)
            for key, value in asdict(limits).items()
        }
        previous = self.state.risk_limits
        previous_profile = self.state.risk_profile
        if previous and set(previous) != set(requested):
            raise LiveSafetyError("stored live risk limits use an unsupported schema")
        if previous_profile == normalized_profile and previous == requested:
            return None

        increases: list[str] = []
        if previous:
            greater_is_riskier = {
                "max_order_notional_usd",
                "max_order_equity_pct",
                "max_symbol_exposure_pct",
                "max_gross_exposure_pct",
                "max_daily_loss_pct",
                "max_drawdown_pct",
                "max_quote_age_seconds",
                "max_price_deviation_pct",
                "max_spread_pct",
                "max_book_slippage_pct",
                "max_dust_notional_usd",
            }
            lower_is_riskier = {
                "min_cash_reserve_pct",
                "min_book_depth_multiple",
            }
            for name in greater_is_riskier:
                if requested[name] > float(previous[name]) + 1e-12:
                    increases.append(name)
            for name in lower_is_riskier:
                if requested[name] < float(previous[name]) - 1e-12:
                    increases.append(name)
        if increases and not approve_increase:
            raise LiveSafetyError(
                "risk-limit increase requires explicit confirmation: "
                + ", ".join(sorted(increases))
            )

        change: dict[str, object] = {
            "previous_profile": previous_profile,
            "profile": normalized_profile,
            "previous_limits": dict(previous),
            "limits": requested,
            "risk_increases": sorted(increases),
        }
        self.state.risk_profile = normalized_profile
        self.state.risk_limits = requested
        self.save()
        return change

    def observe_equity(
        self,
        equity: float,
        limits: LiveRiskLimits,
        *,
        now: datetime | None = None,
    ) -> str | None:
        """Update baselines and persist a circuit breaker when a loss limit trips."""

        if not math.isfinite(equity) or equity <= 0:
            raise LiveSafetyError("account equity must be finite and positive")
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        day = current.astimezone(UTC).date().isoformat()
        if self.state.trading_day != day:
            self.state.trading_day = day
            self.state.daily_start_equity = equity
        if self.state.peak_equity <= 0:
            self.state.peak_equity = equity
        self.state.peak_equity = max(self.state.peak_equity, equity)

        drawdown = (self.state.peak_equity - equity) / self.state.peak_equity
        daily_loss = (
            (self.state.daily_start_equity - equity) / self.state.daily_start_equity
            if self.state.daily_start_equity > 0
            else 0.0
        )
        if self.state.locked_reason is None and drawdown >= limits.max_drawdown_pct:
            self.state.locked_reason = (
                f"max drawdown breached: {drawdown:.2%} >= {limits.max_drawdown_pct:.2%}"
            )
        if self.state.locked_reason is None and daily_loss >= limits.max_daily_loss_pct:
            self.state.locked_reason = (
                f"daily loss breached: {daily_loss:.2%} >= {limits.max_daily_loss_pct:.2%}"
            )
        self.save(now=current)
        return self.state.locked_reason

    def metrics(self, equity: float) -> dict[str, float | str | None]:
        drawdown = (
            (self.state.peak_equity - equity) / self.state.peak_equity
            if self.state.peak_equity > 0
            else 0.0
        )
        daily_loss = (
            (self.state.daily_start_equity - equity) / self.state.daily_start_equity
            if self.state.daily_start_equity > 0
            else 0.0
        )
        return {
            "drawdown_pct": max(drawdown, 0.0),
            "daily_loss_pct": max(daily_loss, 0.0),
            "locked_reason": self.state.locked_reason,
        }

    def reserve_order(
        self,
        order_key: str,
        *,
        symbol: str = "",
        side: str = "",
        quantity: float = 0.0,
        signal_timestamp: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        """Persist an intent before submission so crashes cannot duplicate an order."""

        if order_key in self.state.reserved_orders:
            raise DuplicateOrderError(f"order intent already reserved: {order_key}")
        current = now or datetime.now(UTC)
        timestamp = current.astimezone(UTC).isoformat()
        self.state.reserved_orders[order_key] = timestamp
        self.state.order_ledger[order_key] = {
            "client_order_id": order_key,
            "exchange_order_id": "",
            "symbol": symbol,
            "side": side.upper(),
            "quantity": quantity,
            "filled_quantity": 0.0,
            "average_fill_price": 0.0,
            "quote_cost": 0.0,
            "fees": {},
            "trade_ids": [],
            "exchange_status": "",
            "signal_timestamp": (
                signal_timestamp.astimezone(UTC).isoformat()
                if signal_timestamp is not None
                else ""
            ),
            "status": "reserved",
            "status_history": [{
                "status": "reserved",
                "exchange_status": "",
                "filled_quantity": 0.0,
                "quote_cost": 0.0,
                "at": timestamp,
            }],
            "error": "",
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        if len(self.state.reserved_orders) > 1000:
            excess = len(self.state.reserved_orders) - 1000
            terminal = [
                item
                for item in sorted(
                    self.state.reserved_orders.items(),
                    key=lambda item: item[1],
                )
                if str(
                    (self.state.order_ledger.get(item[0]) or {}).get("status") or ""
                ).lower() in ORDER_LEDGER_TERMINAL_STATUSES
            ]
            # Never prune an uncertain intent just to enforce a storage target.
            for key, _ in terminal[:excess]:
                self.state.reserved_orders.pop(key, None)
                self.state.order_ledger.pop(key, None)
        self.save(now=current)

    def update_order(
        self,
        order_key: str,
        *,
        status: str,
        exchange_order_id: str = "",
        filled_quantity: float | None = None,
        average_fill_price: float | None = None,
        quote_cost: float | None = None,
        fees: Mapping[str, float] | None = None,
        trade_ids: list[str] | tuple[str, ...] | None = None,
        exchange_status: str = "",
        error: str = "",
        now: datetime | None = None,
    ) -> None:
        """Persist monotonic cumulative fill evidence and lifecycle history."""

        record = self.state.order_ledger.get(order_key)
        if not isinstance(record, dict):
            raise LiveSafetyError(f"order intent is not present in ledger: {order_key}")
        normalized = status.strip().lower()
        if normalized not in ORDER_LEDGER_STATUSES:
            raise LiveSafetyError(f"unsupported order ledger status: {status}")
        previous_status = str(record.get("status") or "reserved").strip().lower()
        if previous_status not in ORDER_LEDGER_STATUSES:
            raise LiveSafetyError(
                f"corrupt order ledger status for {order_key}: {previous_status}"
            )
        if (
            normalized != previous_status
            and normalized not in ORDER_LEDGER_TRANSITIONS[previous_status]
        ):
            raise LiveSafetyError(
                f"invalid order status transition for {order_key}: "
                f"{previous_status} -> {normalized}"
            )

        previous_filled = float(record.get("filled_quantity") or 0.0)
        previous_average = float(record.get("average_fill_price") or 0.0)
        previous_quote_cost = float(record.get("quote_cost") or 0.0)
        next_filled = previous_filled if filled_quantity is None else filled_quantity
        next_average = previous_average if average_fill_price is None else average_fill_price
        next_quote_cost = previous_quote_cost if quote_cost is None else quote_cost
        numeric = (next_filled, next_average, next_quote_cost)
        if any(not math.isfinite(value) or value < 0 for value in numeric):
            raise LiveSafetyError("order fill accounting must be finite and non-negative")
        if next_filled + 1e-12 < previous_filled:
            raise LiveSafetyError("cumulative filled quantity cannot decrease")
        if next_quote_cost + 1e-9 < previous_quote_cost:
            raise LiveSafetyError("cumulative quote cost cannot decrease")
        try:
            intended_quantity = float(record.get("quantity") or 0.0)
        except (TypeError, ValueError) as exc:
            raise LiveSafetyError("order ledger contains an invalid quantity") from exc
        if intended_quantity > 0 and next_filled > intended_quantity + 1e-12:
            raise LiveSafetyError("cumulative filled quantity exceeds intended quantity")

        previous_fees = record.get("fees")
        if not isinstance(previous_fees, dict):
            previous_fees = {}
        next_fees = dict(previous_fees)
        if fees is not None:
            normalized_fees: dict[str, float] = {}
            for raw_currency, raw_cost in fees.items():
                currency = str(raw_currency).strip().upper()
                if not currency:
                    raise LiveSafetyError("fee currency cannot be empty")
                try:
                    cost = float(raw_cost)
                except (TypeError, ValueError) as exc:
                    raise LiveSafetyError("fees must be numeric") from exc
                if not math.isfinite(cost) or cost < 0:
                    raise LiveSafetyError("fees must be finite and non-negative")
                normalized_fees[currency] = cost
            for currency, previous_cost in previous_fees.items():
                if currency not in normalized_fees:
                    raise LiveSafetyError(
                        f"cumulative fee snapshot omitted currency {currency}"
                    )
                if normalized_fees[currency] + 1e-12 < float(previous_cost):
                    raise LiveSafetyError(
                        f"cumulative fee for {currency} cannot decrease"
                    )
            next_fees = normalized_fees

        previous_trade_ids = record.get("trade_ids")
        if not isinstance(previous_trade_ids, list):
            previous_trade_ids = []
        next_trade_ids = list(dict.fromkeys(str(value) for value in previous_trade_ids))
        if trade_ids is not None:
            for raw_trade_id in trade_ids:
                trade_id = str(raw_trade_id).strip()
                if not trade_id:
                    raise LiveSafetyError("trade IDs cannot be empty")
                if trade_id not in next_trade_ids:
                    next_trade_ids.append(trade_id)

        current = now or datetime.now(UTC)
        timestamp = current.astimezone(UTC).isoformat()
        raw_exchange_status = exchange_status.strip().lower()
        previous_exchange_status = str(record.get("exchange_status") or "")
        changed = any((
            normalized != previous_status,
            raw_exchange_status and raw_exchange_status != previous_exchange_status,
            next_filled != previous_filled,
            next_quote_cost != previous_quote_cost,
            next_fees != previous_fees,
            next_trade_ids != previous_trade_ids,
        ))
        record.update({
            "status": normalized,
            "exchange_order_id": exchange_order_id or record.get("exchange_order_id", ""),
            "exchange_status": raw_exchange_status or previous_exchange_status,
            "filled_quantity": next_filled,
            "average_fill_price": next_average,
            "quote_cost": next_quote_cost,
            "fees": next_fees,
            "trade_ids": next_trade_ids,
            "error": error,
            "updated_at": timestamp,
        })
        if changed:
            history = record.get("status_history")
            if not isinstance(history, list):
                history = []
            history.append({
                "status": normalized,
                "exchange_status": raw_exchange_status or previous_exchange_status,
                "filled_quantity": next_filled,
                "quote_cost": next_quote_cost,
                "at": timestamp,
            })
            record["status_history"] = history[-100:]
        self.save(now=current)

    def unfinished_orders(self) -> dict[str, dict[str, object]]:
        unfinished = {
            "reserved", "submitted", "acknowledged", "open", "partial",
            "reconciling", "manual_intervention", "unknown",
        }
        return {
            key: dict(record)
            for key, record in self.state.order_ledger.items()
            if isinstance(record, dict) and str(record.get("status", "")).lower() in unfinished
        }

    def observe_position_risk(
        self,
        symbol: str,
        *,
        quantity: float,
        observed_high: float,
        atr: float,
        atr_multiplier: float,
        now: datetime | None = None,
    ) -> tuple[float, float]:
        """Persist a peak and a trailing stop that can tighten but never widen."""

        if not math.isfinite(quantity) or quantity <= 0:
            raise LiveSafetyError("position quantity must be finite and positive")
        if not math.isfinite(observed_high) or observed_high <= 0:
            raise LiveSafetyError("observed position high must be finite and positive")
        if not math.isfinite(atr) or atr <= 0:
            raise LiveSafetyError("position ATR must be finite and positive")
        if not math.isfinite(atr_multiplier) or atr_multiplier <= 0:
            raise LiveSafetyError("ATR multiplier must be finite and positive")
        current = now or datetime.now(UTC)
        record = self.state.position_risk.get(symbol)
        previous_peak = 0.0
        previous_stop = 0.0
        if isinstance(record, dict):
            try:
                previous_peak = float(record.get("peak_price", 0.0))
                previous_stop = float(record.get("trailing_stop", 0.0))
            except (TypeError, ValueError) as exc:
                raise LiveSafetyError(f"invalid position risk state for {symbol}") from exc
        peak = max(previous_peak, observed_high)
        trailing_stop = max(previous_stop, peak - atr_multiplier * atr)
        updated_record = dict(record) if isinstance(record, dict) else {}
        updated_record.update({
            "quantity": quantity,
            "peak_price": peak,
            "trailing_stop": trailing_stop,
            "updated_at": current.astimezone(UTC).isoformat(),
        })
        self.state.position_risk[symbol] = updated_record
        self.save(now=current)
        return peak, trailing_stop

    def protective_order_state(self, symbol: str) -> dict[str, object]:
        """Return a copy of active/pending protection and controlled dust state."""

        record = self.state.position_risk.get(symbol)
        if not isinstance(record, dict):
            return {"revision": 0, "active": None, "pending": None, "dust": None}
        protection = record.get("protective_order")
        if not isinstance(protection, dict):
            return {"revision": 0, "active": None, "pending": None, "dust": None}
        active = protection.get("active")
        pending = protection.get("pending")
        dust = protection.get("dust")
        return {
            "revision": int(protection.get("revision", 0)),
            "active": dict(active) if isinstance(active, dict) else None,
            "pending": dict(pending) if isinstance(pending, dict) else None,
            "dust": dict(dust) if isinstance(dust, dict) else None,
        }

    def mark_position_dust(
        self,
        symbol: str,
        *,
        quantity: float,
        estimated_notional: float,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Persist a signed marker for a deterministic, bounded dust position."""

        if not math.isfinite(quantity) or quantity <= 0:
            raise LiveSafetyError("dust quantity must be finite and positive")
        if not math.isfinite(estimated_notional) or estimated_notional < 0:
            raise LiveSafetyError("dust notional must be finite and non-negative")
        if not reason.strip():
            raise LiveSafetyError("dust reason is required")
        record = self.state.position_risk.get(symbol)
        if not isinstance(record, dict):
            raise LiveSafetyError(f"position risk is not initialized for {symbol}")
        protection = record.get("protective_order")
        if not isinstance(protection, dict):
            protection = {
                "revision": 0,
                "active": None,
                "pending": None,
                "dust": None,
            }
        if isinstance(protection.get("pending"), dict):
            raise LiveSafetyError(
                f"cannot classify dust while protection is pending for {symbol}"
            )
        current = now or datetime.now(UTC)
        existing = protection.get("dust")
        first_observed_at = (
            existing.get("first_observed_at")
            if isinstance(existing, dict)
            else current.astimezone(UTC).isoformat()
        )
        dust: dict[str, object] = {
            "status": "controlled_dust",
            "quantity": quantity,
            "estimated_notional": estimated_notional,
            "reason": reason.strip(),
            "first_observed_at": first_observed_at,
            "updated_at": current.astimezone(UTC).isoformat(),
        }
        protection["dust"] = dust
        record["protective_order"] = protection
        self.save(now=current)
        return dict(dust)

    def clear_position_dust(
        self,
        symbol: str,
        *,
        now: datetime | None = None,
    ) -> None:
        record = self.state.position_risk.get(symbol)
        if not isinstance(record, dict):
            return
        protection = record.get("protective_order")
        if isinstance(protection, dict) and protection.get("dust") is not None:
            protection["dust"] = None
            self.save(now=now)

    def reserve_protective_order(
        self,
        symbol: str,
        *,
        quantity: float,
        stop_price: float,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Persist a protective intent while retaining the last active order."""

        if any(
            not math.isfinite(value) or value <= 0
            for value in (quantity, stop_price)
        ):
            raise LiveSafetyError("protective quantity and stop price must be positive")
        record = self.state.position_risk.get(symbol)
        if not isinstance(record, dict):
            raise LiveSafetyError(f"position risk is not initialized for {symbol}")
        current = now or datetime.now(UTC)
        protection = record.get("protective_order")
        if not isinstance(protection, dict):
            protection = {
                "revision": 0,
                "active": None,
                "pending": None,
                "dust": None,
            }
        if isinstance(protection.get("pending"), dict):
            raise LiveSafetyError(f"protective order submission is already pending for {symbol}")
        revision = int(protection.get("revision", 0)) + 1
        client_order_id = make_protective_order_key(
            symbol=symbol,
            revision=revision,
            stop_price=stop_price,
        )
        pending: dict[str, object] = {
            "client_order_id": client_order_id,
            "exchange_order_id": "",
            "quantity": quantity,
            "stop_price": stop_price,
            "status": "submitting",
            "error": "",
            "created_at": current.astimezone(UTC).isoformat(),
            "updated_at": current.astimezone(UTC).isoformat(),
        }
        protection.update({"revision": revision, "pending": pending})
        record["protective_order"] = protection
        self.save(now=current)
        return dict(pending)

    def update_pending_protective_order(
        self,
        symbol: str,
        *,
        status: str,
        exchange_order_id: str = "",
        error: str = "",
        now: datetime | None = None,
    ) -> None:
        protection = self.protective_order_state(symbol)
        pending = protection.get("pending")
        if not isinstance(pending, dict):
            raise LiveSafetyError(f"protective order intent is not pending for {symbol}")
        normalized = status.strip().lower()
        allowed = {
            "submitting", "open", "partial", "filled", "cancelled",
            "rejected", "expired", "unknown",
        }
        if normalized not in allowed:
            raise LiveSafetyError(f"unsupported protective order status: {status}")
        current = now or datetime.now(UTC)
        pending.update({
            "status": normalized,
            "exchange_order_id": exchange_order_id or pending.get("exchange_order_id", ""),
            "error": error,
            "updated_at": current.astimezone(UTC).isoformat(),
        })
        record = self.state.position_risk[symbol]
        raw_protection = record["protective_order"]
        raw_protection["pending"] = pending
        self.save(now=current)

    def activate_pending_protective_order(
        self,
        symbol: str,
        *,
        exchange_order_id: str,
        status: str = "open",
        quantity: float | None = None,
        stop_price: float | None = None,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Promote a confirmed pending stop while atomically forgetting the old one."""

        normalized = status.strip().lower()
        if normalized not in {"open", "partial"}:
            raise LiveSafetyError(f"cannot activate terminal protective order: {status}")
        protection = self.protective_order_state(symbol)
        pending = protection.get("pending")
        if not isinstance(pending, dict):
            raise LiveSafetyError(f"protective order intent is not pending for {symbol}")
        current = now or datetime.now(UTC)
        if quantity is not None:
            if not math.isfinite(quantity) or quantity <= 0:
                raise LiveSafetyError("confirmed protective quantity must be positive")
            pending["quantity"] = quantity
        if stop_price is not None:
            if not math.isfinite(stop_price) or stop_price <= 0:
                raise LiveSafetyError("confirmed protective stop price must be positive")
            pending["stop_price"] = stop_price
        pending.update({
            "exchange_order_id": exchange_order_id,
            "status": normalized,
            "error": "",
            "updated_at": current.astimezone(UTC).isoformat(),
        })
        record = self.state.position_risk[symbol]
        raw_protection = record["protective_order"]
        raw_protection["active"] = pending
        raw_protection["pending"] = None
        raw_protection["dust"] = None
        self.save(now=current)
        return dict(pending)

    def abandon_pending_protective_order(
        self,
        symbol: str,
        *,
        now: datetime | None = None,
    ) -> None:
        record = self.state.position_risk.get(symbol)
        if not isinstance(record, dict):
            return
        protection = record.get("protective_order")
        if isinstance(protection, dict) and protection.get("pending") is not None:
            protection["pending"] = None
            self.save(now=now)

    def clear_active_protective_order(
        self,
        symbol: str,
        *,
        now: datetime | None = None,
    ) -> None:
        record = self.state.position_risk.get(symbol)
        if not isinstance(record, dict):
            return
        protection = record.get("protective_order")
        if isinstance(protection, dict) and protection.get("active") is not None:
            protection["active"] = None
            self.save(now=now)

    def clear_position_risk(self, symbol: str, *, now: datetime | None = None) -> None:
        if symbol in self.state.position_risk:
            self.state.position_risk.pop(symbol, None)
            self.save(now=now)

    def save(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        self.state.updated_at = current.astimezone(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self.state)
        payload.pop("integrity", None)
        if self.integrity_key is not None:
            self.state.integrity = _payload_hmac(payload, self.integrity_key)
        elif self.state.integrity:
            raise LiveSafetyError("cannot modify a signed live risk state without its integrity key")
        payload["integrity"] = self.state.integrity
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        finally:
            Path(temp_name).unlink(missing_ok=True)


def validate_fresh_quote(
    *,
    signal_price: float,
    quote_price: float,
    quote_timestamp: datetime,
    limits: LiveRiskLimits,
    now: datetime | None = None,
) -> None:
    """Reject stale, future-dated or sharply divergent quotes."""

    if not math.isfinite(signal_price) or signal_price <= 0:
        raise LiveSafetyError("signal price must be finite and positive")
    if not math.isfinite(quote_price) or quote_price <= 0:
        raise LiveSafetyError("quote price must be finite and positive")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    timestamp = quote_timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone().astimezone(UTC)
    else:
        timestamp = timestamp.astimezone(UTC)
    age = (current.astimezone(UTC) - timestamp).total_seconds()
    if age < -5:
        raise LiveSafetyError(f"quote timestamp is {abs(age):.1f}s in the future")
    if age > limits.max_quote_age_seconds:
        raise LiveSafetyError(
            f"quote is stale: {age:.1f}s > {limits.max_quote_age_seconds:.1f}s"
        )
    deviation = abs(quote_price - signal_price) / signal_price
    if deviation > limits.max_price_deviation_pct:
        raise LiveSafetyError(
            f"quote deviation too large: {deviation:.2%} > "
            f"{limits.max_price_deviation_pct:.2%}"
        )


def validate_spread(*, bid: float, ask: float, limits: LiveRiskLimits) -> None:
    if any(not math.isfinite(value) or value <= 0 for value in (bid, ask)):
        raise LiveSafetyError("bid and ask must be finite and positive")
    if ask < bid:
        raise LiveSafetyError("crossed market quote is invalid")
    midpoint = (bid + ask) / 2
    spread = (ask - bid) / midpoint
    if spread > limits.max_spread_pct:
        raise LiveSafetyError(
            f"spread is too wide: {spread:.2%} > {limits.max_spread_pct:.2%}"
        )


def validate_order_book_depth(
    *,
    side: str,
    quantity: float,
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    book_timestamp: datetime,
    limits: LiveRiskLimits,
    now: datetime | None = None,
) -> float:
    """Return expected VWAP after rejecting stale, thin or high-impact books."""

    if not math.isfinite(quantity) or quantity <= 0:
        raise LiveSafetyError("order-book quantity must be finite and positive")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    timestamp = book_timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    age = (current.astimezone(UTC) - timestamp.astimezone(UTC)).total_seconds()
    if age < -5 or age > limits.max_quote_age_seconds:
        raise LiveSafetyError(f"order book timestamp is invalid or stale: {age:.1f}s")

    normalized_side = side.upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise LiveSafetyError(f"unsupported order-book side: {side}")
    levels = asks if normalized_side == "BUY" else bids
    if not levels:
        raise LiveSafetyError("executable order book is empty")
    cleaned: list[tuple[float, float]] = []
    for price, size in levels:
        if any(not math.isfinite(value) or value <= 0 for value in (price, size)):
            raise LiveSafetyError("order book contains invalid levels")
        cleaned.append((price, size))
    prices = [price for price, _ in cleaned]
    if normalized_side == "BUY" and prices != sorted(prices):
        raise LiveSafetyError("ask levels are not sorted")
    if normalized_side == "SELL" and prices != sorted(prices, reverse=True):
        raise LiveSafetyError("bid levels are not sorted")

    required_depth = quantity * limits.min_book_depth_multiple
    total_depth = sum(size for _, size in cleaned)
    if total_depth + 1e-12 < required_depth:
        raise LiveSafetyError(
            f"order-book depth is insufficient: {total_depth:.8f} < {required_depth:.8f}"
        )
    remaining = quantity
    notional = 0.0
    for price, size in cleaned:
        filled = min(remaining, size)
        notional += filled * price
        remaining -= filled
        if remaining <= 1e-12:
            break
    if remaining > 1e-12:
        raise LiveSafetyError("order book cannot fill the requested quantity")
    expected_vwap = notional / quantity
    best = cleaned[0][0]
    impact = abs(expected_vwap - best) / best
    if impact > limits.max_book_slippage_pct:
        raise LiveSafetyError(
            f"expected book slippage is too high: "
            f"{impact:.2%} > {limits.max_book_slippage_pct:.2%}"
        )
    return expected_vwap


def validate_order_risk(
    *,
    side: str,
    notional_usd: float,
    equity: float,
    cash: float,
    current_symbol_notional: float,
    gross_exposure: float,
    limits: LiveRiskLimits,
    locked_reason: str | None,
) -> None:
    """Validate a proposed order against account-level hard limits."""

    values = (notional_usd, equity, cash, current_symbol_notional, gross_exposure)
    if any(not math.isfinite(value) or value < 0 for value in values) or equity <= 0:
        raise LiveSafetyError("invalid account or order value")
    normalized_side = side.upper()
    if normalized_side not in {"BUY", "SELL"}:
        raise LiveSafetyError(f"unsupported order side: {side}")
    if notional_usd <= 0:
        raise LiveSafetyError("order notional must be positive")

    if normalized_side == "SELL":
        if notional_usd > current_symbol_notional * 1.01:
            raise LiveSafetyError("sell order exceeds the current position")
        return

    if locked_reason:
        raise LiveSafetyError(f"risk circuit breaker is locked: {locked_reason}")
    effective_order_limit = limits.effective_max_order_notional(equity)
    if notional_usd > effective_order_limit:
        raise LiveSafetyError(
            f"order notional ${notional_usd:,.2f} exceeds "
            f"${effective_order_limit:,.2f}"
        )
    if current_symbol_notional + notional_usd > equity * limits.max_symbol_exposure_pct:
        raise LiveSafetyError("post-trade symbol exposure exceeds limit")
    if gross_exposure + notional_usd > equity * limits.max_gross_exposure_pct:
        raise LiveSafetyError("post-trade gross exposure exceeds limit")
    if cash - notional_usd < equity * limits.min_cash_reserve_pct:
        raise LiveSafetyError("post-trade cash reserve would fall below limit")


def make_order_key(*, symbol: str, side: str, candle_timestamp: datetime) -> str:
    """Build a stable Binance-compatible client order identifier."""

    timestamp = candle_timestamp
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    raw = f"enhanced-ma-v1|{symbol.upper()}|{side.upper()}|{timestamp.astimezone(UTC).isoformat()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"lta-{digest}"


def make_protective_order_key(
    *,
    symbol: str,
    revision: int,
    stop_price: float,
) -> str:
    """Build a unique, retry-stable Binance client ID for one stop revision."""

    if revision <= 0:
        raise LiveSafetyError("protective order revision must be positive")
    if not math.isfinite(stop_price) or stop_price <= 0:
        raise LiveSafetyError("protective stop price must be finite and positive")
    raw = f"enhanced-ma-stop-v1|{symbol.upper()}|{revision}|{stop_price:.12g}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"lta-ps-{revision}-{digest}"[:36]
