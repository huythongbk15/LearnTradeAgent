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
        ):
            value = getattr(self, name)
            if not 0 < value < 1:
                raise LiveSafetyError(f"{name} must be between 0 and 1")
        if self.max_symbol_exposure_pct > self.max_gross_exposure_pct:
            raise LiveSafetyError("symbol exposure cannot exceed gross exposure")
        if self.max_quote_age_seconds <= 0:
            raise LiveSafetyError("LIVE_MAX_QUOTE_AGE_SECONDS must be positive")


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
    max_age_days: int = 31


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


def validate_strategy_evidence(
    path: str | Path,
    *,
    expected_symbols: list[str],
    expected_params: Mapping[str, object],
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
    if raw.get("version") != 1 or raw.get("strategy") != "enhanced_ma":
        raise LiveSafetyError("strategy evidence schema or strategy does not match")
    if raw.get("strategy_params") != dict(expected_params):
        raise LiveSafetyError("strategy evidence parameters do not match the live strategy")

    selected_policy = policy or StrategyEvidencePolicy()
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    generated_at = _parse_utc_datetime(raw.get("generated_at"), "generated_at")
    data_end = _parse_utc_datetime(raw.get("data_end"), "data_end")
    for field_name, timestamp in (("generated_at", generated_at), ("data_end", data_end)):
        age_days = (current.astimezone(UTC) - timestamp).total_seconds() / 86_400
        if age_days < -1:
            raise LiveSafetyError(f"strategy evidence {field_name} is future-dated")
        if age_days > selected_policy.max_age_days:
            raise LiveSafetyError(
                f"strategy evidence {field_name} is stale: {age_days:.1f} days"
            )

    costs = raw.get("costs")
    if not isinstance(costs, dict):
        raise LiveSafetyError("strategy evidence costs are missing")
    try:
        commission_bps = float(costs.get("commission_bps", 0))
        slippage_bps = float(costs.get("slippage_bps", 0))
    except (TypeError, ValueError) as exc:
        raise LiveSafetyError("strategy evidence costs are invalid") from exc
    if commission_bps < selected_policy.min_commission_bps:
        raise LiveSafetyError("strategy evidence commission assumption is too low")
    if slippage_bps < selected_policy.min_slippage_bps:
        raise LiveSafetyError("strategy evidence slippage assumption is too low")

    evidence_symbols = raw.get("symbols")
    if not isinstance(evidence_symbols, dict):
        raise LiveSafetyError("strategy evidence symbols are missing")
    summaries: dict[str, dict[str, float]] = {}
    for symbol in expected_symbols:
        item = evidence_symbols.get(symbol)
        folds = item.get("folds") if isinstance(item, dict) else None
        if not isinstance(folds, list) or len(folds) < selected_policy.min_folds:
            raise LiveSafetyError(
                f"strategy evidence for {symbol} needs at least {selected_policy.min_folds} folds"
            )
        try:
            sharpes = [float(fold["sharpe"]) for fold in folds]
            returns = [float(fold["return_pct"]) for fold in folds]
            drawdowns = [abs(float(fold["max_drawdown_pct"])) for fold in folds]
            trades = [int(fold["trades"]) for fold in folds]
        except (KeyError, TypeError, ValueError) as exc:
            raise LiveSafetyError(f"strategy evidence metrics are invalid for {symbol}") from exc
        all_metrics = [*sharpes, *returns, *drawdowns, *trades]
        if any(not math.isfinite(value) for value in all_metrics):
            raise LiveSafetyError(f"strategy evidence contains non-finite metrics for {symbol}")

        median_sharpe = median(sharpes)
        median_return = median(returns)
        positive_ratio = sum(value > 0 for value in returns) / len(returns)
        worst_drawdown = max(drawdowns)
        total_trades = sum(trades)
        if median_sharpe < selected_policy.min_median_oos_sharpe:
            raise LiveSafetyError(f"{symbol} median OOS Sharpe does not pass")
        if median_return <= selected_policy.min_median_oos_return_pct:
            raise LiveSafetyError(f"{symbol} median OOS return does not pass")
        if positive_ratio < selected_policy.min_positive_fold_ratio:
            raise LiveSafetyError(f"{symbol} positive-fold ratio does not pass")
        if worst_drawdown > selected_policy.max_worst_oos_drawdown_pct:
            raise LiveSafetyError(f"{symbol} worst OOS drawdown does not pass")
        if total_trades < selected_policy.min_total_oos_trades:
            raise LiveSafetyError(f"{symbol} OOS trade count does not pass")
        summaries[symbol] = {
            "median_sharpe": median_sharpe,
            "median_return_pct": median_return,
            "positive_fold_ratio": positive_ratio,
            "worst_drawdown_pct": worst_drawdown,
            "total_trades": float(total_trades),
        }
    return summaries


def require_execution_authorization(
    *,
    execute: bool,
    testnet: bool,
    cli_confirmation: str | None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Require independent environment and CLI gates before order submission."""

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


@dataclass
class LiveRiskState:
    version: int = STATE_VERSION
    peak_equity: float = 0.0
    daily_start_equity: float = 0.0
    trading_day: str = ""
    locked_reason: str | None = None
    reserved_orders: dict[str, str] = field(default_factory=dict)
    updated_at: str = ""


class LiveRiskStateStore:
    """Atomic persistent state for loss limits and order idempotency."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.existed = self.path.exists()
        self.state = self._load()

    def _load(self) -> LiveRiskState:
        if not self.path.exists():
            return LiveRiskState()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
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
        return state

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

    def reserve_order(self, order_key: str, *, now: datetime | None = None) -> None:
        """Persist an intent before submission so crashes cannot duplicate an order."""

        if order_key in self.state.reserved_orders:
            raise DuplicateOrderError(f"order intent already reserved: {order_key}")
        current = now or datetime.now(UTC)
        timestamp = current.astimezone(UTC).isoformat()
        self.state.reserved_orders[order_key] = timestamp
        if len(self.state.reserved_orders) > 1000:
            oldest = sorted(self.state.reserved_orders.items(), key=lambda item: item[1])[:-1000]
            for key, _ in oldest:
                self.state.reserved_orders.pop(key, None)
        self.save(now=current)

    def save(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        self.state.updated_at = current.astimezone(UTC).isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(asdict(self.state), handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
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
    if notional_usd > limits.max_order_notional_usd:
        raise LiveSafetyError(
            f"order notional ${notional_usd:,.2f} exceeds "
            f"${limits.max_order_notional_usd:,.2f}"
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
