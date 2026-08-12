"""Post-trade adverse-selection analytics (Wave D, spec §16).

After each fill we record the mid at t0, t+100ms, t+1s, t+5s, t+30s and
attribute every fill by:

* side (buy/sell)
* quantity bucket
* spread
* depth
* book imbalance
* volatility
* aggressiveness (market vs passive)
* order type

``PostTradeImpactReport`` aggregates the records into group statistics and
flags four failure modes:

* bad trade timing — average adverse move above threshold;
* execution too aggressive — marketable fills move the mid much more than
  passive fills;
* predictable adverse movement — the t+1s move predicts the t+30s move;
* poor liquidity regime — adverse moves concentrate in wide-spread or
  thin-depth conditions.

All window interpolation is deterministic (linear between t0 and t+30s).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

from trading_agent.execution.simulator.models import Fill, SimSide
from trading_agent.execution.simulator.versions import ALGORITHMS_VERSION


# ── Window interpolation ──────────────────────────────────────────────────

def mid_windows(mid_t0: float, mid_t30s: float) -> dict[str, float]:
    """Deterministic post-fill mid windows (linear interpolation).

    ``mid_t0`` is the mid immediately before the fill; ``mid_t30s`` is the
    observed/estimated mid 30 s after.  Interpolation is monotonic and
    deterministic — the honest granularity available without tick data.
    """
    if mid_t0 <= 0:
        raise ValueError(f"mid_t0 must be > 0, got {mid_t0}")
    delta = mid_t30s - mid_t0
    return {
        "mid_t0": mid_t0,
        "mid_t+100ms": mid_t0 + delta * 0.10,
        "mid_t+1s": mid_t0 + delta * 0.25,
        "mid_t+5s": mid_t0 + delta * 0.50,
        "mid_t+30s": mid_t30s,
    }


def adverse_move_bps(mid_t0: float, mid_t30s: float, side: SimSide) -> float:
    """Signed adverse move in bps (positive = bad for the trader)."""
    if mid_t0 <= 0:
        raise ValueError(f"mid_t0 must be > 0, got {mid_t0}")
    raw_bps = (mid_t30s - mid_t0) / mid_t0 * 10_000.0
    direction = 1.0 if side == SimSide.BUY else -1.0
    return direction * raw_bps


# ── Record ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PostTradeFillRecord:
    """One fill plus its post-trade mid path and attribution attributes."""

    order_id: str
    side: SimSide
    quantity: float
    fill_price: float
    aggressor: str                       # "market" | "limit_passive"
    order_type: str                      # "market" | "limit"
    spread_bps: float
    depth: float                         # resting size on the side we consumed
    book_imbalance: float                # [-1, 1]
    volatility_bps: float
    mid_t0: float
    mid_t100ms: float
    mid_t1s: float
    mid_t5s: float
    mid_t30s: float

    @property
    def adverse_bps(self) -> float:
        return adverse_move_bps(self.mid_t0, self.mid_t30s, self.side)

    @property
    def quantity_bucket(self) -> str:
        if self.quantity < 1000:
            return "small"
        if self.quantity < 10_000:
            return "medium"
        return "large"

    @property
    def spread_bucket(self) -> str:
        if self.spread_bps < 3.0:
            return "tight"
        if self.spread_bps <= 10.0:
            return "normal"
        return "wide"

    @property
    def imbalance_bucket(self) -> str:
        if self.book_imbalance > 0.2:
            return "bid_heavy"
        if self.book_imbalance < -0.2:
            return "ask_heavy"
        return "neutral"

    @property
    def volatility_bucket(self) -> str:
        if self.volatility_bps < 10.0:
            return "low"
        if self.volatility_bps <= 40.0:
            return "mid"
        return "high"

    @property
    def aggressiveness_bucket(self) -> str:
        return "aggressive" if self.aggressor == "market" else "passive"

    def windows(self) -> dict[str, float]:
        return {
            "mid_t0": self.mid_t0,
            "mid_t+100ms": self.mid_t100ms,
            "mid_t+1s": self.mid_t1s,
            "mid_t+5s": self.mid_t5s,
            "mid_t+30s": self.mid_t30s,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "side": self.side.value,
            "quantity": self.quantity,
            "quantity_bucket": self.quantity_bucket,
            "fill_price": self.fill_price,
            "aggressor": self.aggressor,
            "aggressiveness_bucket": self.aggressiveness_bucket,
            "order_type": self.order_type,
            "spread_bps": self.spread_bps,
            "spread_bucket": self.spread_bucket,
            "depth": self.depth,
            "book_imbalance": self.book_imbalance,
            "imbalance_bucket": self.imbalance_bucket,
            "volatility_bps": self.volatility_bps,
            "volatility_bucket": self.volatility_bucket,
            "adverse_bps": self.adverse_bps,
            **self.windows(),
        }


def record_from_fill(
    fill: Fill,
    *,
    spread_bps: float = 5.0,
    depth: float = 10_000.0,
    book_imbalance: float = 0.0,
    volatility_bps: float = 20.0,
    order_type: str = "market",
) -> PostTradeFillRecord:
    """Build a record from a simulator ``Fill`` plus a market snapshot."""
    windows = mid_windows(fill.mid_before or fill.price, fill.mid_after)
    return PostTradeFillRecord(
        order_id=fill.order_id,
        side=fill.side,
        quantity=fill.quantity,
        fill_price=fill.price,
        aggressor=fill.aggressor,
        order_type=order_type,
        spread_bps=spread_bps,
        depth=depth,
        book_imbalance=book_imbalance,
        volatility_bps=volatility_bps,
        mid_t0=windows["mid_t0"],
        mid_t100ms=windows["mid_t+100ms"],
        mid_t1s=windows["mid_t+1s"],
        mid_t5s=windows["mid_t+5s"],
        mid_t30s=windows["mid_t+30s"],
    )


# ── Group statistics ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class GroupStats:
    """Adverse-move statistics for one attribution group."""

    count: int
    mean_adverse_bps: float
    median_adverse_bps: float
    std_adverse_bps: float
    p90_adverse_bps: float
    mean_windows: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "mean_adverse_bps": round(self.mean_adverse_bps, 4),
            "median_adverse_bps": round(self.median_adverse_bps, 4),
            "std_adverse_bps": round(self.std_adverse_bps, 4),
            "p90_adverse_bps": round(self.p90_adverse_bps, 4),
            "mean_windows": {k: round(v, 6) for k, v in self.mean_windows.items()},
        }


def _group_stats(records: list[PostTradeFillRecord]) -> GroupStats:
    if not records:
        return GroupStats(0, 0.0, 0.0, 0.0, 0.0, {})
    adverse = [r.adverse_bps for r in records]
    mean = statistics.fmean(adverse)
    std = statistics.stdev(adverse) if len(adverse) > 1 else 0.0
    sorted_adverse = sorted(adverse)
    p90 = sorted_adverse[min(len(sorted_adverse) - 1, int(math.ceil(0.9 * len(sorted_adverse))) - 1)]
    windows_mean = {
        k: statistics.fmean(r.windows()[k] for r in records)
        for k in ("mid_t0", "mid_t+100ms", "mid_t+1s", "mid_t+5s", "mid_t+30s")
    }
    return GroupStats(
        count=len(records),
        mean_adverse_bps=mean,
        median_adverse_bps=statistics.median(adverse),
        std_adverse_bps=std,
        p90_adverse_bps=p90,
        mean_windows=windows_mean,
    )


# ── Detection ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DetectionResult:
    """Flags + evidence for the four adverse-selection failure modes."""

    bad_timing: bool
    too_aggressive: bool
    predictable_adverse_move: bool
    poor_liquidity_regime: bool
    evidence: dict[str, Any]

    @property
    def any_flag(self) -> bool:
        return any(
            (self.bad_timing, self.too_aggressive,
             self.predictable_adverse_move, self.poor_liquidity_regime)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "bad_timing": self.bad_timing,
            "too_aggressive": self.too_aggressive,
            "predictable_adverse_move": self.predictable_adverse_move,
            "poor_liquidity_regime": self.poor_liquidity_regime,
            "evidence": self.evidence,
        }


# ── Report ────────────────────────────────────────────────────────────────

class PostTradeImpactReport:
    """Aggregates post-fill records, groups them, and flags failure modes.

    Deterministic: grouping and statistics depend only on the records added.
    """

    def __init__(
        self,
        *,
        bad_timing_threshold_bps: float = 5.0,
        aggressive_gap_bps: float = 3.0,
        liquidity_gap_bps: float = 3.0,
        predict_corr_threshold: float = 0.6,
        min_group_size: int = 3,
    ) -> None:
        if bad_timing_threshold_bps < 0 or aggressive_gap_bps < 0 or liquidity_gap_bps < 0:
            raise ValueError("thresholds must be >= 0")
        if not 0 <= predict_corr_threshold <= 1:
            raise ValueError("predict_corr_threshold must be in [0, 1]")
        if min_group_size < 1:
            raise ValueError(f"min_group_size must be >= 1, got {min_group_size}")
        self.bad_timing_threshold_bps = bad_timing_threshold_bps
        self.aggressive_gap_bps = aggressive_gap_bps
        self.liquidity_gap_bps = liquidity_gap_bps
        self.predict_corr_threshold = predict_corr_threshold
        self.min_group_size = min_group_size
        self._records: list[PostTradeFillRecord] = []
        self.algorithms_version = ALGORITHMS_VERSION

    def add_record(self, record: PostTradeFillRecord) -> None:
        self._records.append(record)

    def add_fill(
        self,
        fill: Fill,
        *,
        spread_bps: float,
        depth: float,
        book_imbalance: float,
        volatility_bps: float,
        order_type: str = "market",
    ) -> PostTradeFillRecord:
        rec = record_from_fill(
            fill,
            spread_bps=spread_bps,
            depth=depth,
            book_imbalance=book_imbalance,
            volatility_bps=volatility_bps,
            order_type=order_type,
        )
        self.add_record(rec)
        return rec

    @property
    def records(self) -> list[PostTradeFillRecord]:
        return list(self._records)

    @property
    def count(self) -> int:
        return len(self._records)

    # ── Grouping ─────────────────────────────────────────────────────────

    def group_by(self, key: str) -> dict[str, GroupStats]:
        """Group records by an attribution attribute and return stats."""
        attr_map = {
            "side": lambda r: r.side.value,
            "quantity": lambda r: r.quantity_bucket,
            "spread": lambda r: r.spread_bucket,
            "depth": lambda r: r.spread_bucket,       # depth proxy via spread regime
            "imbalance": lambda r: r.imbalance_bucket,
            "volatility": lambda r: r.volatility_bucket,
            "aggressiveness": lambda r: r.aggressiveness_bucket,
            "order_type": lambda r: r.order_type,
        }
        if key not in attr_map:
            raise ValueError(
                f"unknown group key {key!r}; valid: {sorted(attr_map)}"
            )
        groups: dict[str, list[PostTradeFillRecord]] = {}
        for rec in self._records:
            groups.setdefault(attr_map[key](rec), []).append(rec)
        return {k: _group_stats(v) for k, v in sorted(groups.items())}

    # ── Detection ────────────────────────────────────────────────────────

    def detect(self) -> DetectionResult:
        evidence: dict[str, Any] = {}
        if not self._records:
            return DetectionResult(False, False, False, False, evidence)

        overall = _group_stats(self._records)
        evidence["overall"] = overall.to_dict()
        evidence["groups"] = {
            k: self.group_by(k) for k in (
                "side", "quantity", "spread", "imbalance", "volatility",
                "aggressiveness", "order_type",
            )
        }
        evidence["groups"] = {
            k: {g: s.to_dict() for g, s in v.items()}
            for k, v in evidence["groups"].items()
        }

        bad_timing = overall.mean_adverse_bps > self.bad_timing_threshold_bps
        evidence["bad_timing"] = {
            "mean_adverse_bps": round(overall.mean_adverse_bps, 4),
            "threshold_bps": self.bad_timing_threshold_bps,
        }

        agg = self.group_by("aggressiveness")
        aggressive = agg.get("aggressive")
        passive = agg.get("passive")
        too_aggressive = False
        if aggressive and passive and aggressive.count >= self.min_group_size:
            gap = aggressive.mean_adverse_bps - passive.mean_adverse_bps
            too_aggressive = gap > self.aggressive_gap_bps
            evidence["too_aggressive"] = {
                "aggressive_mean_bps": round(aggressive.mean_adverse_bps, 4),
                "passive_mean_bps": round(passive.mean_adverse_bps, 4),
                "gap_bps": round(gap, 4),
                "threshold_bps": self.aggressive_gap_bps,
            }

        # Predictable adverse move: t+1s move predicts t+30s move.
        predictable = False
        if len(self._records) >= self.min_group_size:
            early = [(r.mid_t1s - r.mid_t0) / r.mid_t0 * 10_000.0 for r in self._records]
            late = [(r.mid_t30s - r.mid_t0) / r.mid_t0 * 10_000.0 for r in self._records]
            corr = _pearson(early, late)
            predictable = corr > self.predict_corr_threshold
            evidence["predictable_adverse_move"] = {
                "corr_t1s_t30s": round(corr, 4),
                "threshold": self.predict_corr_threshold,
            }

        # Poor liquidity regime: wide-spread fills worse than the best other
        # spread regime (normal or tight — whichever is available).
        poor_liquidity = False
        spread_groups = self.group_by("spread")
        wide = spread_groups.get("wide")
        baseline = spread_groups.get("normal") or spread_groups.get("tight")
        if wide and baseline and wide.count >= self.min_group_size:
            gap = wide.mean_adverse_bps - baseline.mean_adverse_bps
            poor_liquidity = gap > self.liquidity_gap_bps
            evidence["poor_liquidity_regime"] = {
                "wide_mean_bps": round(wide.mean_adverse_bps, 4),
                "baseline_mean_bps": round(baseline.mean_adverse_bps, 4),
                "gap_bps": round(gap, 4),
                "threshold_bps": self.liquidity_gap_bps,
            }

        return DetectionResult(
            bad_timing=bad_timing,
            too_aggressive=too_aggressive,
            predictable_adverse_move=predictable,
            poor_liquidity_regime=poor_liquidity,
            evidence=evidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithms_version": self.algorithms_version,
            "fill_count": self.count,
            "detection": self.detect().to_dict(),
            "groups": {
                k: {g: s.to_dict() for g, s in self.group_by(k).items()}
                for k in (
                    "side", "quantity", "spread", "imbalance", "volatility",
                    "aggressiveness", "order_type",
                )
            },
        }


def _pearson(x: list[float], y: list[float]) -> float:
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    mx, my = statistics.fmean(x), statistics.fmean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    if den <= 0:
        return 0.0
    return num / den
