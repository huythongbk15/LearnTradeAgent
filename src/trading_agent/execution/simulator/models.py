"""Data models for the Execution Simulator V2.

Everything in this module is plain, deterministic, seed-controllable data.
No uncontrolled randomness is ever introduced here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from trading_agent.execution.simulator.versions import (
    EXECUTION_MODEL_VERSION,
    FEE_MODEL_VERSION,
    FILL_MODEL_VERSION,
    IMPACT_MODEL_VERSION,
)


class SimSide(Enum):
    BUY = "buy"
    SELL = "sell"


class SimOrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"


class SimOrderStatus(Enum):
    PENDING = "pending"  # waiting for submission latency
    SUBMITTED = "submitted"  # on the book / being worked
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELED = "canceled"
    REJECTED = "rejected"  # fail-closed: stale quote, sequence gap, min notional, insufficient funds


class RejectReason(Enum):
    NONE = "none"
    STALE_QUOTE = "stale_quote"
    SEQUENCE_GAP = "sequence_gap"
    MIN_NOTIONAL = "min_notional"
    BELOW_MIN_QTY = "below_min_qty"
    INSUFFICIENT_LIQUIDITY = "insufficient_liquidity"
    INSUFFICIENT_CASH = "insufficient_cash"
    INSUFFICIENT_INVENTORY = "insufficient_inventory"
    INVALID_ORDER = "invalid_order"


@dataclass(frozen=True)
class SimulationConfig:
    """Typed, validated configuration for a simulation run.

    Fields marked "versioned" are part of the execution-model fingerprint:
    changing any of them invalidates comparability with older artifacts.
    """

    # ── Versioning (Section 3) ──────────────────────────────────────────
    execution_model_version: str = EXECUTION_MODEL_VERSION
    fill_model_version: str = FILL_MODEL_VERSION
    impact_model_version: str = IMPACT_MODEL_VERSION
    fee_model_version: str = FEE_MODEL_VERSION
    market_data_manifest: str = ""  # sha256 of the input market data
    random_seed: int = 42

    # ── Market structure (OHLCV-derived book, no L2 data required) ──────
    spread_bps: float = 5.0  # base spread (5 bps = 0.05%)
    depth_levels: int = 5  # levels per side
    depth_volume_share: float = 0.25  # book size per side = share * previous-bar volume
    tick_size: float = 0.01  # price grid
    step_size: float = 1e-6  # quantity grid
    min_qty: float = 0.0  # min order quantity (base)
    min_notional: float = 0.0  # min order notional (quote)

    # ── Fees ────────────────────────────────────────────────────────────
    taker_fee: float = 0.0005  # 5 bps
    maker_fee: float = 0.0002  # 2 bps
    fee_asset: str = "quote"  # "quote" | "base"
    min_fee: float = 0.0

    # ── Latency (milliseconds) ──────────────────────────────────────────
    submit_latency_ms: float = 50.0
    ack_latency_ms: float = 20.0
    cancel_latency_ms: float = 100.0
    network_latency_ms: float = 30.0

    # ── Fill model ──────────────────────────────────────────────────────
    queue_position_base: float = 0.5  # deterministic fraction of level size ahead of us
    passive_fill_prob: float = (
        0.30  # per-bar probability a resting limit fills (seeded)
    )

    # ── Impact model ────────────────────────────────────────────────────
    impact_coeff: float = 1.0  # multiplier on sqrt impact
    impact_decay_half_life_bars: float = 3.0
    adverse_selection_bps: float = 2.0  # base adverse mid move after aggressive fill

    # ── Staleness / safety ──────────────────────────────────────────────
    max_book_age_seconds: float = 60.0

    def validate(self) -> None:
        """Fail closed on any invalid configuration value."""
        if self.random_seed is None:
            raise ValueError("random_seed must be set (determinism requirement)")
        if not 0 <= self.spread_bps:
            raise ValueError(f"spread_bps must be >= 0, got {self.spread_bps}")
        if self.depth_levels < 1:
            raise ValueError(f"depth_levels must be >= 1, got {self.depth_levels}")
        if not 0 < self.depth_volume_share <= 1:
            raise ValueError(
                f"depth_volume_share must be in (0, 1], got {self.depth_volume_share}"
            )
        if self.tick_size <= 0:
            raise ValueError(f"tick_size must be > 0, got {self.tick_size}")
        if self.step_size <= 0:
            raise ValueError(f"step_size must be > 0, got {self.step_size}")
        if self.min_qty < 0:
            raise ValueError(f"min_qty must be >= 0, got {self.min_qty}")
        if self.min_notional < 0:
            raise ValueError(f"min_notional must be >= 0, got {self.min_notional}")
        if not 0 <= self.taker_fee < 1:
            raise ValueError(f"taker_fee must be in [0, 1), got {self.taker_fee}")
        if not 0 <= self.maker_fee < 1:
            raise ValueError(f"maker_fee must be in [0, 1), got {self.maker_fee}")
        if self.fee_asset not in ("quote", "base"):
            raise ValueError(
                f"fee_asset must be 'quote' or 'base', got {self.fee_asset!r}"
            )
        if self.min_fee < 0:
            raise ValueError(f"min_fee must be >= 0, got {self.min_fee}")
        if (
            self.submit_latency_ms < 0
            or self.ack_latency_ms < 0
            or self.cancel_latency_ms < 0
        ):
            raise ValueError("latencies must be >= 0")
        if self.network_latency_ms < 0:
            raise ValueError("network_latency_ms must be >= 0")
        if not 0 <= self.queue_position_base <= 1:
            raise ValueError(
                f"queue_position_base must be in [0, 1], got {self.queue_position_base}"
            )
        if not 0 <= self.passive_fill_prob <= 1:
            raise ValueError(
                f"passive_fill_prob must be in [0, 1], got {self.passive_fill_prob}"
            )
        if self.impact_coeff < 0:
            raise ValueError(f"impact_coeff must be >= 0, got {self.impact_coeff}")
        if self.impact_decay_half_life_bars <= 0:
            raise ValueError(
                f"impact_decay_half_life_bars must be > 0, got {self.impact_decay_half_life_bars}"
            )
        if self.adverse_selection_bps < 0:
            raise ValueError(
                f"adverse_selection_bps must be >= 0, got {self.adverse_selection_bps}"
            )
        if self.max_book_age_seconds <= 0:
            raise ValueError(
                f"max_book_age_seconds must be > 0, got {self.max_book_age_seconds}"
            )

    def fingerprint(self) -> str:
        """Stable hash of the versioned parameters (excludes bookkeeping)."""
        payload = {
            "execution_model_version": self.execution_model_version,
            "fill_model_version": self.fill_model_version,
            "impact_model_version": self.impact_model_version,
            "fee_model_version": self.fee_model_version,
            "random_seed": self.random_seed,
            "spread_bps": self.spread_bps,
            "depth_levels": self.depth_levels,
            "depth_volume_share": self.depth_volume_share,
            "tick_size": self.tick_size,
            "step_size": self.step_size,
            "min_qty": self.min_qty,
            "min_notional": self.min_notional,
            "taker_fee": self.taker_fee,
            "maker_fee": self.maker_fee,
            "fee_asset": self.fee_asset,
            "min_fee": self.min_fee,
            "submit_latency_ms": self.submit_latency_ms,
            "ack_latency_ms": self.ack_latency_ms,
            "cancel_latency_ms": self.cancel_latency_ms,
            "network_latency_ms": self.network_latency_ms,
            "queue_position_base": self.queue_position_base,
            "passive_fill_prob": self.passive_fill_prob,
            "impact_coeff": self.impact_coeff,
            "impact_decay_half_life_bars": self.impact_decay_half_life_bars,
            "adverse_selection_bps": self.adverse_selection_bps,
            "max_book_age_seconds": self.max_book_age_seconds,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.fingerprint_dict(),
            "market_data_manifest": self.market_data_manifest,
            "random_seed": self.random_seed,
        }

    def fingerprint_dict(self) -> dict[str, Any]:
        return {
            "execution_model_version": self.execution_model_version,
            "fill_model_version": self.fill_model_version,
            "impact_model_version": self.impact_model_version,
            "fee_model_version": self.fee_model_version,
            "spread_bps": self.spread_bps,
            "depth_levels": self.depth_levels,
            "depth_volume_share": self.depth_volume_share,
            "tick_size": self.tick_size,
            "step_size": self.step_size,
            "min_qty": self.min_qty,
            "min_notional": self.min_notional,
            "taker_fee": self.taker_fee,
            "maker_fee": self.maker_fee,
            "fee_asset": self.fee_asset,
            "min_fee": self.min_fee,
            "submit_latency_ms": self.submit_latency_ms,
            "ack_latency_ms": self.ack_latency_ms,
            "cancel_latency_ms": self.cancel_latency_ms,
            "network_latency_ms": self.network_latency_ms,
            "queue_position_base": self.queue_position_base,
            "passive_fill_prob": self.passive_fill_prob,
            "impact_coeff": self.impact_coeff,
            "impact_decay_half_life_bars": self.impact_decay_half_life_bars,
            "adverse_selection_bps": self.adverse_selection_bps,
            "max_book_age_seconds": self.max_book_age_seconds,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SimulationConfig":
        allowed = {
            f.name
            for f in cls.__dataclass_fields__.values()  # type: ignore[attr-defined]
        }
        kwargs = {k: v for k, v in d.items() if k in allowed}
        cfg = cls(**kwargs)
        cfg.validate()
        return cfg


@dataclass(frozen=True)
class BookLevel:
    """One price level of the order book."""

    price: float
    size: float

    def to_dict(self) -> dict[str, float]:
        return {"price": self.price, "size": self.size}


@dataclass
class OrderIntent:
    """An order the strategy wants to submit at a given bar."""

    order_id: str
    side: SimSide
    order_type: SimOrderType
    quantity: float
    limit_price: float | None = None  # required for LIMIT
    client_order_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    submit_latency_override_ms: float | None = None


@dataclass
class Fill:
    """A single executed fill (possibly one of many for an order)."""

    order_id: str
    bar_index: int
    timestamp: datetime
    side: SimSide
    quantity: float
    price: float
    fee: float
    fee_asset: str
    aggressor: str  # "market" | "limit_passive"
    level_price: float  # book level price before impact adjustment
    impact_bps: float = 0.0
    mid_before: float = 0.0
    mid_after: float = 0.0  # post-fill mid (adverse selection window start)
    is_partial: bool = False

    @property
    def notional(self) -> float:
        return self.quantity * self.price

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "bar_index": self.bar_index,
            "timestamp": self.timestamp.isoformat(),
            "side": self.side.value,
            "quantity": self.quantity,
            "price": self.price,
            "fee": self.fee,
            "fee_asset": self.fee_asset,
            "aggressor": self.aggressor,
            "level_price": self.level_price,
            "impact_bps": self.impact_bps,
            "mid_before": self.mid_before,
            "mid_after": self.mid_after,
            "is_partial": self.is_partial,
        }


@dataclass
class OrderResult:
    """Outcome of one submitted order."""

    order_id: str
    intent: OrderIntent
    status: SimOrderStatus
    fills: list[Fill] = field(default_factory=list)
    reject_reason: RejectReason = RejectReason.NONE
    submit_time: datetime | None = None
    first_fill_time: datetime | None = None
    cancel_time: datetime | None = None
    queue_approx: float | None = None
    # P&L attribution prices (Section 4)
    decision_price: float | None = None  # strategy decision mid at signal bar
    arrival_price: float | None = None  # mid at submission bar open
    submit_price: float | None = None  # mid at actual submission time
    fill_vwap: float | None = None
    post_fill_mid: float | None = None  # mid shortly after the last fill

    @property
    def filled_quantity(self) -> float:
        return sum(f.quantity for f in self.fills)

    @property
    def remaining_quantity(self) -> float:
        return max(0.0, self.intent.quantity - self.filled_quantity)

    @property
    def fill_ratio(self) -> float:
        return (
            self.filled_quantity / self.intent.quantity if self.intent.quantity else 0.0
        )

    @property
    def total_fee(self) -> float:
        return sum(f.fee for f in self.fills)

    @property
    def avg_fill_price(self) -> float | None:
        qty = self.filled_quantity
        if qty <= 0:
            return None
        return sum(f.quantity * f.price for f in self.fills) / qty

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "status": self.status.value,
            "reject_reason": self.reject_reason.value,
            "fills": [f.to_dict() for f in self.fills],
            "submit_time": self.submit_time.isoformat() if self.submit_time else None,
            "first_fill_time": self.first_fill_time.isoformat()
            if self.first_fill_time
            else None,
            "decision_price": self.decision_price,
            "arrival_price": self.arrival_price,
            "submit_price": self.submit_price,
            "fill_vwap": self.fill_vwap,
            "post_fill_mid": self.post_fill_mid,
            "queue_approx": self.queue_approx,
        }


def quantize_price(price: float, tick_size: float) -> float:
    """Round a price to the exchange tick grid (deterministic)."""
    if tick_size <= 0:
        raise ValueError("tick_size must be > 0")
    return round(price / tick_size) * tick_size


def quantize_qty(qty: float, step_size: float) -> float:
    """Round a quantity down to the exchange step grid (deterministic).

    Downward rounding keeps orders conservative: we never overspend or
    over-sell relative to the intended quantity.
    """
    if step_size <= 0:
        raise ValueError("step_size must be > 0")
    steps = int(qty / step_size)
    return steps * step_size


def now_utc() -> datetime:
    return datetime.now(UTC)
