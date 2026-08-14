#!/usr/bin/env python3
"""
Execution Quality & Operations Dashboard — real-time monitoring.

Components:
1. ExecutionQualityMonitor — fill quality, slippage tracking, rejections
2. LatencyProfiler — order round-trip latency, exchange response times
3. OrderBookDepthMonitor — spread, depth, imbalance alerts
4. RealTimeSlippageTracker — per-symbol slippage statistics

Design:
    monitor = ExecutionQualityMonitor()
    monitor.record_fill(order_id="X", expected_price=100, fill_price=100.5, ...)
    report = monitor.get_report(symbol="BTC")

    profiler = LatencyProfiler()
    profiler.record("order_submit", 15.2)  # ms
    stats = profiler.get_stats("order_submit")
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

# ── Execution Quality Monitor ─────────────────────────────────


@dataclass
class FillRecord:
    order_id: str
    symbol: str
    side: str
    expected_price: float
    fill_price: float
    qty: float
    slippage_bps: float
    fill_time_ms: float
    status: str  # "filled", "partial", "rejected", "cancelled"
    exchange: str = ""
    timestamp: float = 0.0


class ExecutionQualityMonitor:
    """Tracks execution quality: slippage, fill rates, reject rates."""

    def __init__(self, max_history: int = 10_000):
        self._fills: deque[FillRecord] = deque(maxlen=max_history)

    def record_fill(
        self,
        order_id: str,
        symbol: str,
        side: str,
        expected_price: float,
        fill_price: float,
        qty: float,
        fill_time_ms: float = 0,
        status: str = "filled",
        exchange: str = "",
    ) -> FillRecord:
        slippage_bps = (
            (fill_price - expected_price) / expected_price * 10_000
            if expected_price > 0 and status == "filled"
            else 0
        )
        if side == "sell":
            slippage_bps = -slippage_bps  # positive slippage is bad for both sides
        rec = FillRecord(
            order_id=order_id,
            symbol=symbol,
            side=side,
            expected_price=expected_price,
            fill_price=fill_price,
            qty=qty,
            slippage_bps=slippage_bps,
            fill_time_ms=fill_time_ms,
            status=status,
            exchange=exchange,
            timestamp=time.time(),
        )
        self._fills.append(rec)
        return rec

    def get_report(self, symbol: str = "", lookback_s: float = 86400) -> dict:
        cutoff = time.time() - lookback_s
        fills = [
            f
            for f in self._fills
            if f.timestamp > cutoff and (not symbol or f.symbol == symbol)
        ]
        if not fills:
            return {"symbol": symbol, "n_fills": 0}

        slippages = [f.slippage_bps for f in fills if f.status == "filled"]
        fill_times = [f.fill_time_ms for f in fills if f.fill_time_ms > 0]
        total_vol = sum(f.qty * f.fill_price for f in fills)

        return {
            "symbol": symbol,
            "n_fills": len(fills),
            "n_filled": sum(1 for f in fills if f.status == "filled"),
            "n_rejected": sum(1 for f in fills if f.status == "rejected"),
            "n_partial": sum(1 for f in fills if f.status == "partial"),
            "fill_rate": sum(1 for f in fills if f.status == "filled") / len(fills),
            "avg_slippage_bps": sum(slippages) / len(slippages) if slippages else 0,
            "median_slippage_bps": sorted(slippages)[len(slippages) // 2]
            if slippages
            else 0,
            "max_slippage_bps": max(slippages) if slippages else 0,
            "total_volume_usd": total_vol,
            "avg_fill_time_ms": sum(fill_times) / len(fill_times) if fill_times else 0,
            "p95_fill_time_ms": sorted(fill_times)[int(len(fill_times) * 0.95)]
            if fill_times
            else 0,
        }

    def get_slippage_distribution(self, symbol: str = "", n_buckets: int = 20) -> dict:
        fills = [
            f
            for f in self._fills
            if f.status == "filled" and (not symbol or f.symbol == symbol)
        ]
        if not fills:
            return {"buckets": [], "counts": []}
        slippages = [f.slippage_bps for f in fills]
        lo, hi = min(slippages), max(slippages)
        if hi == lo:
            return {"buckets": [lo], "counts": [len(slippages)]}
        step = (hi - lo) / n_buckets
        buckets = [lo + i * step for i in range(n_buckets + 1)]
        counts = [0] * n_buckets
        for s in slippages:
            idx = min(int((s - lo) / step), n_buckets - 1)
            counts[idx] += 1
        return {"buckets": buckets, "counts": counts}


# ── Latency Profiler ─────────────────────────────────────────


class LatencyProfiler:
    """Tracks latency of various operations (order submit, fill, API call)."""

    def __init__(self, max_history: int = 10_000):
        self._records: dict[str, deque[float]] = {}

    def record(self, operation: str, latency_ms: float):
        if operation not in self._records:
            self._records[operation] = deque(maxlen=10_000)
        self._records[operation].append(latency_ms)

    def get_stats(self, operation: str) -> dict:
        vals = list(self._records.get(operation, []))
        if not vals:
            return {"operation": operation, "n": 0}
        vals_s = sorted(vals)
        n = len(vals_s)
        return {
            "operation": operation,
            "n": n,
            "mean_ms": sum(vals) / n,
            "median_ms": vals_s[n // 2],
            "p95_ms": vals_s[int(n * 0.95)],
            "p99_ms": vals_s[int(n * 0.99)],
            "min_ms": vals_s[0],
            "max_ms": vals_s[-1],
            "std_ms": (sum((x - sum(vals) / n) ** 2 for x in vals) / n) ** 0.5,
        }

    def get_all_stats(self) -> dict:
        return {op: self.get_stats(op) for op in self._records}


# ── Order Book Depth Monitor ─────────────────────────────────


@dataclass
class OrderBookSnapshot:
    symbol: str
    bids: list[tuple[float, float]]  # [(price, qty), ...]
    asks: list[tuple[float, float]]
    timestamp: float
    spread_bps: float = 0.0
    bid_depth_usd: float = 0.0
    ask_depth_usd: float = 0.0
    imbalance: float = 0.0  # bid_depth / (bid_depth + ask_depth)


class OrderBookDepthMonitor:
    """Monitors order book depth, spread, and imbalance."""

    def __init__(self, depth_levels: int = 20):
        self.depth_levels = depth_levels
        self._snapshots: dict[str, list[OrderBookSnapshot]] = {}

    def update(
        self, symbol: str, bids: list[tuple], asks: list[tuple]
    ) -> OrderBookSnapshot:
        bids_sorted = sorted(bids, key=lambda x: x[0], reverse=True)[
            : self.depth_levels
        ]
        asks_sorted = sorted(asks, key=lambda x: x[0])[: self.depth_levels]

        best_bid = bids_sorted[0][0] if bids_sorted else 0
        best_ask = asks_sorted[0][0] if asks_sorted else 0
        mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0
        spread_bps = (best_ask - best_bid) / mid * 10_000 if mid > 0 else 0

        bid_depth = sum(p * q for p, q in bids_sorted)
        ask_depth = sum(p * q for p, q in asks_sorted)
        total = bid_depth + ask_depth
        imbalance = bid_depth / total if total > 0 else 0.5

        snap = OrderBookSnapshot(
            symbol=symbol,
            bids=bids_sorted,
            asks=asks_sorted,
            timestamp=time.time(),
            spread_bps=spread_bps,
            bid_depth_usd=bid_depth,
            ask_depth_usd=ask_depth,
            imbalance=imbalance,
        )
        if symbol not in self._snapshots:
            self._snapshots[symbol] = []
        self._snapshots[symbol].append(snap)
        if len(self._snapshots[symbol]) > 1000:
            self._snapshots[symbol] = self._snapshots[symbol][-1000:]
        return snap

    def get_alerts(
        self,
        symbol: str,
        spread_threshold_bps: float = 20,
        imbalance_threshold: float = 0.7,
    ) -> list[dict]:
        snaps = self._snapshots.get(symbol, [])
        if not snaps:
            return []
        latest = snaps[-1]
        alerts = []
        if latest.spread_bps > spread_threshold_bps:
            alerts.append(
                {
                    "type": "wide_spread",
                    "symbol": symbol,
                    "value": latest.spread_bps,
                    "threshold": spread_threshold_bps,
                }
            )
        if latest.imbalance > imbalance_threshold:
            alerts.append(
                {
                    "type": "bid_heavy",
                    "symbol": symbol,
                    "value": latest.imbalance,
                    "threshold": imbalance_threshold,
                }
            )
        elif latest.imbalance < (1 - imbalance_threshold):
            alerts.append(
                {
                    "type": "ask_heavy",
                    "symbol": symbol,
                    "value": latest.imbalance,
                    "threshold": 1 - imbalance_threshold,
                }
            )
        return alerts

    def get_depth_summary(self, symbol: str) -> dict:
        snaps = self._snapshots.get(symbol, [])
        if not snaps:
            return {"symbol": symbol, "n_snapshots": 0}
        latest = snaps[-1]
        spreads = [s.spread_bps for s in snaps[-100:]]
        return {
            "symbol": symbol,
            "spread_bps": latest.spread_bps,
            "avg_spread_bps": sum(spreads) / len(spreads),
            "bid_depth_usd": latest.bid_depth_usd,
            "ask_depth_usd": latest.ask_depth_usd,
            "imbalance": latest.imbalance,
            "n_snapshots": len(snaps),
        }


# ── Real-Time Slippage Tracker ───────────────────────────────


class RealTimeSlippageTracker:
    """Per-symbol slippage statistics with rolling windows."""

    def __init__(self, window_size: int = 500):
        self.window_size = window_size
        self._data: dict[str, deque[float]] = {}

    def record(self, symbol: str, slippage_bps: float):
        if symbol not in self._data:
            self._data[symbol] = deque(maxlen=self.window_size)
        self._data[symbol].append(slippage_bps)

    def get_stats(self, symbol: str) -> dict:
        vals = list(self._data.get(symbol, []))
        if not vals:
            return {"symbol": symbol, "n": 0}
        return {
            "symbol": symbol,
            "n": len(vals),
            "mean_bps": sum(vals) / len(vals),
            "std_bps": (sum((x - sum(vals) / len(vals)) ** 2 for x in vals) / len(vals))
            ** 0.5,
            "min_bps": min(vals),
            "max_bps": max(vals),
            "current_bps": vals[-1],
            "ema_bps": self._ema(vals),
        }

    def _ema(self, values: list[float], span: int = 20) -> float:
        if not values:
            return 0
        alpha = 2 / (span + 1)
        ema = values[0]
        for v in values[1:]:
            ema = alpha * v + (1 - alpha) * ema
        return ema


if __name__ == "__main__":
    import random

    print("=" * 60)
    print("EXECUTION QUALITY & OPERATIONS — DEMO")
    print("=" * 60)

    # Execution Quality
    eqm = ExecutionQualityMonitor()
    for i in range(100):
        eqm.record_fill(
            order_id=f"ORD-{i}",
            symbol="BTC/USDT",
            side=random.choice(["buy", "sell"]),
            expected_price=100_000 + random.uniform(-500, 500),
            fill_price=100_000 + random.uniform(-500, 500),
            qty=random.uniform(0.01, 1.0),
            fill_time_ms=random.uniform(5, 200),
            status=random.choices(
                ["filled", "rejected", "partial"], weights=[90, 5, 5]
            )[0],
        )
    report = eqm.get_report("BTC/USDT")
    print(
        f"\nExecution Quality: {report['n_fills']} fills, fill rate={report['fill_rate']:.1%}"
    )
    print(f"  Avg slippage: {report['avg_slippage_bps']:.2f} bps")
    print(f"  Median fill time: {report['median_slippage_bps']:.1f} ms")

    # Latency Profiler
    lp = LatencyProfiler()
    for _ in range(200):
        lp.record("order_submit", random.uniform(5, 50))
        lp.record("order_fill", random.uniform(50, 500))
        lp.record("api_call", random.uniform(10, 100))
    print("\nLatency Profile:")
    for op, stats in lp.get_all_stats().items():
        print(
            f"  {op:20s}: mean={stats['mean_ms']:.1f}ms p95={stats['p95_ms']:.1f}ms p99={stats['p99_ms']:.1f}ms"
        )

    # Order Book
    obm = OrderBookDepthMonitor(depth_levels=10)
    bids = [(100000 - i * 10, random.uniform(0.1, 5)) for i in range(10)]
    asks = [(100005 + i * 10, random.uniform(0.1, 5)) for i in range(10)]
    snap = obm.update("BTC/USDT", bids, asks)
    print(
        f"\nOrder Book: spread={snap.spread_bps:.1f}bps, imbalance={snap.imbalance:.3f}"
    )
    alerts = obm.get_alerts("BTC/USDT")
    print(f"  Alerts: {len(alerts)}")

    # Slippage Tracker
    st = RealTimeSlippageTracker()
    for _ in range(100):
        st.record("BTC/USDT", random.gauss(0, 2))
    print(f"\nSlippage Tracker: {st.get_stats('BTC/USDT')}")
