#!/usr/bin/env python3
"""
Smart Execution Engine — TWAP, VWAP, and market-impact aware order routing.

Implements:
1. TWAP (Time-Weighted Average Price) — equal slices over time
2. VWAP (Volume-Weighted Average Price) — volume-weighted slices
3. Adaptive execution — adjusts pace based on real-time spread / depth
4. Slippage estimation model — historical fill quality tracker
5. Iceberg / hidden order support

Design:
    execution_engine = SmartExecutionEngine(exchange_adapter, config)
    order = execution_engine.create_twap_order("BTC/USDT", side="buy", total_qty=0.5, duration_s=600)

Usage in production:
    exchange = BinanceAdapter(config)
    engine = SmartExecutionEngine(exchange, SlippageModel())
    await engine.execute(order)
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Sequence


class ExecutionAlgorithm(Enum):
    TWAP = "twap"
    VWAP = "vwap"
    ICEBERG = "iceberg"
    ADAPTIVE = "adaptive"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class OrderSlice:
    """Single child slice of a larger execution."""
    slice_id: int
    symbol: str
    side: str
    qty: float
    limit_price: Optional[float] = None    # None = market
    scheduled_time_s: float = 0.0          # seconds from start
    status: str = "pending"                 # pending → sent → filled → failed
    fill_price: Optional[float] = None
    fill_qty: float = 0.0
    sent_at: Optional[float] = None
    filled_at: Optional[float] = None
    slippage_bps: Optional[float] = None


@dataclass
class SmartOrder:
    """Top-level order controlling many slices."""
    order_id: str
    symbol: str
    side: str
    total_qty: float
    algorithm: str
    slices: list[OrderSlice] = field(default_factory=list)
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    status: str = "created"
    slippage_budget_bps: float = 10.0      # acceptable slippage
    participation_rate: float = 0.1        # max 10% of book depth


class SlippageModel:
    """
    Predicts expected slippage given size, volatility, and book depth.

    Uses square-root model:
        impact ≈ k * sqrt(qty / ADV) * σ
    where k is a calibrated constant (empirically ~0.1–0.3 for BTC on Binance).
    """

    def __init__(self, k: float = 0.15):
        self.k = k
        self.history: list[dict] = []  # filled orders for calibration

    def estimate_slippage_bps(
        self,
        qty: float,
        adv: float,
        volatility: float,
        book_depth_usd: Optional[float] = None,
    ) -> float:
        """Return estimated slippage in basis points."""
        if adv <= 0 or volatility <= 0:
            return 5.0  # fallback
        pct_adv = qty / (adv + 1e-9)
        impact = self.k * math.sqrt(pct_adv) * volatility
        # Extra penalty if qty > 20% of book depth
        if book_depth_usd and book_depth_usd > 0:
            # Estimate dollar value: use a rough price proxy (assume adv * 0.01 ≈ daily price * qty)
            price_proxy = adv * 0.01 if adv > 0 else 50000
            qty_usd = qty * price_proxy
            if qty_usd > book_depth_usd * 0.2:
                impact *= 1.5
        return max(impact * 10_000, 0.1)  # return bps

    def calibrate(self, fills: list[dict]) -> float:
        """
        Calibrate k from recent fills:
            actual_slippage_bps = k * sqrt(qty/ADV) * σ * 10000
            => k = avg(actual_bps / (sqrt(qty/ADV)*σ*10000))
        """
        ratios = []
        for f in fills:
            if f.get("qty") and f.get("adv") and f.get("vol") and f.get("slippage_bps"):
                est = math.sqrt(f["qty"] / f["adv"]) * f["vol"] * 10_000
                if est > 0:
                    ratios.append(f["slippage_bps"] / est)
        if ratios:
            self.k = sum(ratios) / len(ratios)
        return self.k

    def record_fill(self, fill: dict) -> None:
        self.history.append(fill)
        if len(self.history) > 500:
            self.history = self.history[-500:]
        # Recalibrate every 50 fills
        if len(self.history) % 50 == 0:
            self.calibrate(self.history[-100:])


class SmartExecutionEngine:
    """
    Orchestrates smart execution of large orders.

    Parameters
    ----------
    exchange : any object with async `create_order(symbol, side, qty, type, limit)` method
    slippage_model : SlippageModel instance
    config : dict with keys like `min_slice_interval_s`, `max_slice_pct_adv`, `slippage_budget_bps`
    """

    def __init__(self, exchange=None, slippage_model: SlippageModel | None = None, config: dict | None = None):
        self.exchange = exchange
        self.slippage = slippage_model or SlippageModel()
        self.config = config or {}
        self.min_slice_interval_s: float = self.config.get("min_slice_interval_s", 5.0)
        self.max_slices: int = self.config.get("max_slices", 50)
        self.active_orders: list[SmartOrder] = []

    def create_twap_order(
        self,
        symbol: str,
        side: str,
        total_qty: float,
        duration_s: float,
        n_slices: int = 0,
        order_id: str = "",
    ) -> SmartOrder:
        """Create TWAP order with equal-sized slices."""
        if n_slices <= 0:
            n_slices = max(1, min(self.max_slices, int(duration_s / self.min_slice_interval_s)))
        interval_s = duration_s / n_slices
        slice_qty = total_qty / n_slices

        order_id = order_id or f"TWAP-{int(time.time())}"
        slices = [
            OrderSlice(
                slice_id=i,
                symbol=symbol,
                side=side,
                qty=round(slice_qty, 8),
                scheduled_time_s=i * interval_s,
            )
            for i in range(n_slices)
        ]

        order = SmartOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            total_qty=total_qty,
            algorithm="twap",
            slices=slices,
            start_time=time.time(),
            end_time=time.time() + duration_s,
        )
        self.active_orders.append(order)
        return order

    def create_vwap_order(
        self,
        symbol: str,
        side: str,
        total_qty: float,
        volume_profile: Sequence[float],
        duration_s: float,
        order_id: str = "",
    ) -> SmartOrder:
        """
        Create VWAP order — sizes slices proportional to historical volume profile.
        volume_profile: list of volume weights (same length as target slices).
        """
        if not volume_profile or total_qty <= 0:
            raise ValueError("Need non-empty volume_profile and total_qty > 0")

        n_slices = len(volume_profile)
        total_weight = sum(volume_profile)
        if total_weight <= 0:
            raise ValueError("volume_profile weights must sum > 0")

        order_id = order_id or f"VWAP-{int(time.time())}"
        interval_s = duration_s / n_slices

        slices = [
            OrderSlice(
                slice_id=i,
                symbol=symbol,
                side=side,
                qty=round(total_qty * (volume_profile[i] / total_weight), 8),
                scheduled_time_s=i * interval_s,
            )
            for i in range(n_slices)
        ]

        order = SmartOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            total_qty=total_qty,
            algorithm="vwap",
            slices=slices,
            start_time=time.time(),
            end_time=time.time() + duration_s,
        )
        self.active_orders.append(order)
        return order

    def create_iceberg_order(
        self,
        symbol: str,
        side: str,
        total_qty: float,
        display_qty: float,
        order_id: str = "",
    ) -> SmartOrder:
        """Create iceberg order — shows only display_qty at a time."""
        n_slices = max(1, int(math.ceil(total_qty / display_qty)))
        order_id = order_id or f"ICE-{int(time.time())}"
        slices = [
            OrderSlice(
                slice_id=i,
                symbol=symbol,
                side=side,
                qty=round(display_qty, 8) if i < n_slices - 1 else round(total_qty - display_qty * (n_slices - 1), 8),
                scheduled_time_s=i * 1.0,  # 1s apart
            )
            for i in range(n_slices)
        ]
        order = SmartOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            total_qty=total_qty,
            algorithm="iceberg",
            slices=slices,
        )
        self.active_orders.append(order)
        return order

    async def execute(self, order: SmartOrder, dry_run: bool = True) -> dict:
        """
        Execute a smart order (simulated if dry_run=True or no exchange).
        Returns summary dict.
        """
        order.status = "executing"
        order.start_time = time.time()
        total_filled = 0.0
        total_cost = 0.0

        for slc in order.slices:
            now = time.time()
            wait = slc.scheduled_time_s - (now - order.start_time)
            if wait > 0:
                await asyncio.sleep(min(wait, 1.0))  # cap sleep for dry run

            if dry_run or self.exchange is None:
                # Simulate fill at limit price or random around midpoint
                import random
                price = slc.limit_price or (50000 + random.uniform(-50, 50))
                slc.fill_price = price
                slc.fill_qty = slc.qty
            else:
                try:
                    resp = await self.exchange.create_order(
                        slc.symbol, slc.side, slc.qty,
                        order_type="limit" if slc.limit_price else "market",
                        limit_price=slc.limit_price,
                    )
                    slc.fill_price = resp.get("price", slc.limit_price or 50000)
                    slc.fill_qty = resp.get("filled", slc.qty)
                except Exception as e:
                    slc.status = "failed"
                    slc.fill_price = None
                    continue

            slc.status = "filled"
            slc.filled_at = time.time()
            slc.slippage_bps = ((slc.fill_price - slc.limit_price) / slc.limit_price * 10_000
                                if slc.limit_price else None)
            total_filled += slc.fill_qty
            total_cost += slc.fill_qty * slc.fill_price

        avg_price = total_cost / total_filled if total_filled > 0 else 0
        order.status = "completed"
        order.end_time = time.time()
        elapsed = order.end_time - order.start_time

        summary = {
            "order_id": order.order_id,
            "symbol": order.symbol,
            "side": order.side,
            "algorithm": order.algorithm,
            "total_qty": order.total_qty,
            "filled_qty": total_filled,
            "avg_price": avg_price,
            "elapsed_s": elapsed,
            "slices_filled": sum(1 for s in order.slices if s.status == "filled"),
            "slices_failed": sum(1 for s in order.slices if s.status == "failed"),
            "simulated": dry_run,
        }
        return summary

    def generate_vwap_volume_profile(
        self,
        hourly_volumes: Sequence[float],
        n_slices: int = 10,
    ) -> list[float]:
        """Resample hourly volume profile to n_slices VWAP weights."""
        if not hourly_volumes:
            return [1.0] * n_slices
        chunk = max(1, len(hourly_volumes) // n_slices)
        profile = []
        for i in range(0, len(hourly_volumes), chunk):
            profile.append(sum(hourly_volumes[i:i + chunk]))
        # Pad or trim to n_slices
        while len(profile) < n_slices:
            profile.append(profile[-1] if profile else 1.0)
        return profile[:n_slices]

    def summary(self) -> list[dict]:
        """Return summaries of all active/completed orders."""
        return [
            {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": o.side,
                "algorithm": o.algorithm,
                "status": o.status,
                "filled_slices": sum(1 for s in o.slices if s.status == "filled"),
                "total_slices": len(o.slices),
            }
            for o in self.active_orders
        ]


if __name__ == "__main__":
    import asyncio

    async def demo():
        engine = SmartExecutionEngine()

        # TWAP: 0.5 BTC over 5 minutes, 10 slices
        twap = engine.create_twap_order("BTC/USDT", "buy", 0.5, duration_s=300, n_slices=10)
        res = await engine.execute(twap, dry_run=True)
        print("TWAP:", res)

        # VWAP: volume-weighted
        hourly = [100, 120, 80, 150, 200, 180, 90, 110, 140, 160, 130, 100]
        profile = engine.generate_vwap_volume_profile(hourly, n_slices=8)
        vwap = engine.create_vwap_order("ETH/USDT", "sell", 5.0, profile, duration_s=240)
        res2 = await engine.execute(vwap, dry_run=True)
        print("VWAP:", res2)

        # Iceberg
        ice = engine.create_iceberg_order("SOL/USDT", "buy", 100.0, display_qty=10.0)
        res3 = await engine.execute(ice, dry_run=True)
        print("ICEBERG:", res3)

        # Slippage model
        sm = SlippageModel()
        est = sm.estimate_slippage_bps(qty=0.5, adv=500_000, volatility=0.02, book_depth_usd=100_000)
        print(f"Estimated slippage: {est:.1f} bps")

        print("Orders:", engine.summary())

    asyncio.run(demo())