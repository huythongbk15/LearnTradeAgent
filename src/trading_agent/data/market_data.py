#!/usr/bin/env python3
"""
Funding Rate Monitor — perpetual futures funding rate tracking + signals.

Features:
1. Real-time funding rate aggregation across exchanges
2. Historical funding rate analysis
3. Funding rate arbitrage signal (perp vs spot)
4. Funding rate z-score / percentile alerts
5. Basis spread calculation (perp premium)

Design:
    monitor = FundingRateMonitor(exchanges=["binance", "okx", "bybit"])
    rates = monitor.get_rates("BTC")
    signal = monitor.get_signal("BTC")
    arbitrage = monitor.funding_arbitrage("BTC")
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np


@dataclass
class FundingRate:
    exchange: str
    symbol: str
    rate: float  # 8h funding rate (e.g. 0.0001 = 0.01%)
    annualized: float  # rate * 3 * 365
    next_funding_ts: float
    mark_price: float = 0.0
    index_price: float = 0.0
    timestamp: float = 0.0


@dataclass
class FundingSignal:
    symbol: str
    current_rate: float  # weighted average across exchanges
    avg_7d: float
    avg_30d: float
    z_score: float  # vs 30d history
    percentile: float  # 0-100
    annualized_yield: float
    basis_spread_bps: float  # (perp - spot) / spot * 10000
    signal: (
        str  # "strong_positive", "positive", "neutral", "negative", "strong_negative"
    )
    exchanges: list[dict] = field(default_factory=list)


class FundingRateMonitor:
    """
    Monitors funding rates across multiple exchanges.

    In dry_run mode, generates synthetic data for testing.
    In production, fetches from Binance/OKX/Bybit funding endpoint.
    """

    FUNDING_INTERVAL_S = 8 * 3600  # 8 hours

    def __init__(
        self,
        exchanges: list[str] | None = None,
        dry_run: bool = True,
        config: dict | None = None,
    ):
        self.exchanges = exchanges or ["binance", "okx", "bybit"]
        self.dry_run = dry_run
        self.config = config or {}
        self._history: dict[str, list[float]] = {}  # symbol → list of rates

    def get_rates(self, symbol: str, spot_price: float = 0) -> list[FundingRate]:
        """Get current funding rates from all exchanges."""
        if self.dry_run:
            return self._synthetic_rates(symbol, spot_price)
        raise NotImplementedError("Live funding rate fetch not implemented")

    def get_signal(self, symbol: str, spot_price: float = 0) -> FundingSignal:
        """Compute funding rate signal with z-score and percentile."""
        rates = self.get_rates(symbol, spot_price)
        if not rates:
            return FundingSignal(
                symbol=symbol,
                current_rate=0,
                avg_7d=0,
                avg_30d=0,
                z_score=0,
                percentile=50,
                annualized_yield=0,
                basis_spread_bps=0,
                signal="neutral",
            )

        current = np.mean([r.rate for r in rates])

        # Update history
        if symbol not in self._history:
            self._history[symbol] = []
        self._history[symbol].append(current)
        self._history[symbol] = self._history[symbol][-360:]  # keep ~120 days (3x/day)

        hist = np.array(self._history[symbol])
        avg_7d = float(np.mean(hist[-21:])) if len(hist) >= 21 else float(np.mean(hist))
        avg_30d = (
            float(np.mean(hist[-90:])) if len(hist) >= 90 else float(np.mean(hist))
        )

        # Z-score vs 30d
        if len(hist) >= 90:
            std_30d = float(np.std(hist[-90:]))
            z_score = (current - avg_30d) / (std_30d + 1e-9)
        else:
            z_score = 0

        # Percentile
        if len(hist) >= 10:
            percentile = float(np.mean(hist <= current) * 100)
        else:
            percentile = 50

        # Basis spread
        mark = np.mean([r.mark_price for r in rates if r.mark_price > 0])
        idx = np.mean([r.index_price for r in rates if r.index_price > 0])
        basis_bps = ((mark - idx) / idx * 10_000) if idx > 0 else 0

        # Signal classification
        if z_score > 2:
            signal = "strong_positive"  # longs paying shorts heavily → overcrowded long
        elif z_score > 0.5:
            signal = "positive"
        elif z_score < -2:
            signal = "strong_negative"  # shorts paying longs → overcrowded short
        elif z_score < -0.5:
            signal = "negative"
        else:
            signal = "neutral"

        return FundingSignal(
            symbol=symbol,
            current_rate=current,
            avg_7d=avg_7d,
            avg_30d=avg_30d,
            z_score=z_score,
            percentile=percentile,
            annualized_yield=current * 3 * 365 * 100,
            basis_spread_bps=basis_bps,
            signal=signal,
            exchanges=[
                {"exchange": r.exchange, "rate": r.rate, "annualized": r.annualized}
                for r in rates
            ],
        )

    def funding_arbitrage(self, symbol: str) -> dict:
        """
        Funding rate arbitrage opportunity:
        Long spot + short perp = earn funding when rate > 0
        Short spot + long perp = earn funding when rate < 0
        """
        signal = self.get_signal(symbol)
        rates_sorted = sorted(signal.exchanges, key=lambda x: x["rate"])

        if not rates_sorted:
            return {"opportunity": False}

        # Best short perp (highest rate = earn most)
        best_long = rates_sorted[-1]  # highest rate → short perp, earn funding
        best_short = rates_sorted[0]  # lowest rate → long perp, earn funding

        return {
            "opportunity": abs(signal.current_rate) > 0.0001,
            "current_annualized": signal.annualized_yield,
            "strategy": {
                "long_spot_short_perp": {
                    "exchange": best_long["exchange"],
                    "rate": best_long["rate"],
                    "annualized": best_long["annualized"],
                    "edge_bps": best_long["rate"] * 10_000,
                },
                "short_spot_long_perp": {
                    "exchange": best_short["exchange"],
                    "rate": best_short["rate"],
                    "annualized": best_short["annualized"],
                    "edge_bps": abs(best_short["rate"]) * 10_000,
                },
            },
            "basis_spread_bps": signal.basis_spread_bps,
        }

    def _synthetic_rates(self, symbol: str, spot_price: float) -> list[FundingRate]:
        import random

        now = time.time()
        rates = []
        base_rate = random.uniform(-0.0005, 0.0005)
        for ex in self.exchanges:
            noise = random.uniform(-0.0002, 0.0002)
            rate = base_rate + noise
            annualized = rate * 3 * 365
            mark = spot_price * (1 + rate * 10) if spot_price > 0 else 0
            rates.append(
                FundingRate(
                    exchange=ex,
                    symbol=symbol,
                    rate=rate,
                    annualized=annualized,
                    next_funding_ts=now + random.randint(3600, 28800),
                    mark_price=mark,
                    index_price=spot_price,
                    timestamp=now,
                )
            )
        return rates


# ── Liquidation Feed ─────────────────────────────────────────


@dataclass
class LiquidationEvent:
    exchange: str
    symbol: str
    side: str  # "long" or "short"
    price: float
    qty: float
    value_usd: float
    timestamp: float


class LiquidationFeed:
    """
    Aggregated liquidation feed from multiple exchanges.

    In production: WebSocket streams from Binance/OKX/Bybit.
    In dry_run: synthetic events for testing.
    """

    def __init__(self, exchanges: list[str] | None = None, dry_run: bool = True):
        self.exchanges = exchanges or ["binance", "bybit"]
        self.dry_run = dry_run
        self._events: list[LiquidationEvent] = []
        self._stats: dict[str, dict] = {}

    def get_recent(
        self, symbol: str = "", lookback_s: float = 3600
    ) -> list[LiquidationEvent]:
        """Get recent liquidation events."""
        if self.dry_run:
            return self._synthetic_events(symbol)
        return [
            e
            for e in self._events
            if (not symbol or e.symbol == symbol)
            and time.time() - e.timestamp < lookback_s
        ]

    def get_stats(self, symbol: str, lookback_s: float = 86400) -> dict:
        """Liquidation stats: total longs vs shorts, largest, total value."""
        events = [
            e
            for e in self._events
            if (not symbol or e.symbol == symbol)
            and time.time() - e.timestamp < lookback_s
        ]
        longs = [e for e in events if e.side == "long"]
        shorts = [e for e in events if e.side == "short"]
        return {
            "total_events": len(events),
            "long_liquidations": len(longs),
            "short_liquidations": len(shorts),
            "total_long_value_usd": sum(e.value_usd for e in longs),
            "total_short_value_usd": sum(e.value_usd for e in shorts),
            "largest": max(events, key=lambda e: e.value_usd).__dict__
            if events
            else None,
        }

    def _synthetic_events(self, symbol: str) -> list[LiquidationEvent]:
        import random

        if not symbol:
            symbol = "BTC/USDT"
        events = []
        for _ in range(random.randint(2, 10)):
            side = random.choice(["long", "short"])
            price = random.uniform(95000, 105000)
            qty = random.uniform(0.01, 5.0)
            events.append(
                LiquidationEvent(
                    exchange=random.choice(self.exchanges),
                    symbol=symbol,
                    side=side,
                    price=price,
                    qty=qty,
                    value_usd=price * qty,
                    timestamp=time.time() - random.uniform(0, 3600),
                )
            )
        return events


# ── Cross-Exchange Arbitrage Detector ─────────────────────────


@dataclass
class ArbitrageOpportunity:
    symbol: str
    buy_exchange: str
    sell_exchange: str
    spread_bps: float
    buy_price: float
    sell_price: float
    estimated_profit_pct: float
    confidence: str  # "high", "medium", "low"
    timestamp: float


class ArbitrageDetector:
    """
    Detects cross-exchange price dislocations.

    Compares mid prices across exchanges to find arb opportunities.
    Accounts for fees and transfer time.
    """

    def __init__(
        self, fee_bps: float = 10.0, transfer_time_s: float = 60, dry_run: bool = True
    ):
        self.fee_bps = fee_bps
        self.transfer_time_s = transfer_time_s
        self.dry_run = dry_run
        self._prices: dict[
            str, dict[str, dict]
        ] = {}  # symbol → exchange → {bid, ask, mid}

    def update_prices(self, symbol: str, exchange: str, bid: float, ask: float):
        if symbol not in self._prices:
            self._prices[symbol] = {}
        self._prices[symbol][exchange] = {
            "bid": bid,
            "ask": ask,
            "mid": (bid + ask) / 2,
            "timestamp": time.time(),
        }

    def scan(self, min_spread_bps: float = 5.0) -> list[ArbitrageOpportunity]:
        """Scan all symbols for cross-exchange arb opportunities."""
        opps = []
        for symbol, ex_data in self._prices.items():
            exchanges = list(ex_data.keys())
            if len(exchanges) < 2:
                continue
            for i in range(len(exchanges)):
                for j in range(len(exchanges)):
                    if i == j:
                        continue
                    ex_buy = exchanges[i]
                    ex_sell = exchanges[j]
                    buy_ask = ex_data[ex_buy]["ask"]  # we buy at ask
                    sell_bid = ex_data[ex_sell]["bid"]  # we sell at bid
                    if buy_ask <= 0:
                        continue
                    spread_bps = (sell_bid - buy_ask) / buy_ask * 10_000
                    net_bps = spread_bps - 2 * self.fee_bps  # round-trip fees
                    if net_bps >= min_spread_bps:
                        confidence = (
                            "high"
                            if net_bps > 30
                            else "medium"
                            if net_bps > 15
                            else "low"
                        )
                        opps.append(
                            ArbitrageOpportunity(
                                symbol=symbol,
                                buy_exchange=ex_buy,
                                sell_exchange=ex_sell,
                                spread_bps=net_bps,
                                buy_price=buy_ask,
                                sell_price=sell_bid,
                                estimated_profit_pct=net_bps / 100,
                                confidence=confidence,
                                timestamp=time.time(),
                            )
                        )
        return sorted(opps, key=lambda o: o.spread_bps, reverse=True)

    def _synthetic_scan(self) -> list[ArbitrageOpportunity]:
        import random

        pairs = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
        exchanges = ["binance", "okx", "bybit", "gate"]
        opps = []
        for sym in pairs:
            for i in range(len(exchanges)):
                for j in range(i + 1, len(exchanges)):
                    if random.random() > 0.3:  # 70% chance of arb
                        spread = random.uniform(5, 50)
                        base = {
                            "BTC/USDT": 100000,
                            "ETH/USDT": 3500,
                            "SOL/USDT": 180,
                        }.get(sym, 100)
                        opps.append(
                            ArbitrageOpportunity(
                                symbol=sym,
                                buy_exchange=exchanges[i],
                                sell_exchange=exchanges[j],
                                spread_bps=spread,
                                buy_price=base,
                                sell_price=base * (1 + spread / 10000),
                                estimated_profit_pct=spread / 100,
                                confidence="high" if spread > 30 else "medium",
                                timestamp=time.time(),
                            )
                        )
        return opps


# ── On-Chain Whale Tracker ───────────────────────────────────


@dataclass
class WhaleTransfer:
    chain: str
    tx_hash: str
    from_address: str
    to_address: str
    token: str
    amount: float
    value_usd: float
    label: str = ""  # "exchange_deposit", "exchange_withdrawal", "unknown"
    timestamp: float = 0.0


class WhaleTracker:
    """
    Monitors large on-chain transfers.

    Tracks exchange inflows/outflows for BTC, ETH, SOL.
    Flags whale movements that may impact price.
    """

    def __init__(self, threshold_usd: float = 1_000_000, dry_run: bool = True):
        self.threshold_usd = threshold_usd
        self.dry_run = dry_run
        self._transfers: list[WhaleTransfer] = []
        self._known_exchanges: set[str] = {
            "0x28C6c06298d514Db089934071355E5743bf21d60",  # Binance
            "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549",  # Binance
        }

    def get_transfers(self, token: str = "", min_usd: float = 0) -> list[WhaleTransfer]:
        if self.dry_run:
            return self._synthetic_transfers()
        return [
            t
            for t in self._transfers
            if (not token or t.token == token)
            and t.value_usd >= (min_usd or self.threshold_usd)
        ]

    def get_summary(self, token: str = "", hours: float = 24) -> dict:
        transfers = [
            t
            for t in self.get_transfers(token)
            if time.time() - t.timestamp < hours * 3600
        ]
        inflows = [t for t in transfers if t.label == "exchange_deposit"]
        outflows = [t for t in transfers if t.label == "exchange_withdrawal"]
        return {
            "total_transfers": len(transfers),
            "total_value_usd": sum(t.value_usd for t in transfers),
            "exchange_inflows": len(inflows),
            "exchange_inflow_usd": sum(t.value_usd for t in inflows),
            "exchange_outflows": len(outflows),
            "exchange_outflow_usd": sum(t.value_usd for t in outflows),
            "net_flow_usd": sum(t.value_usd for t in outflows)
            - sum(t.value_usd for t in inflows),
            "signal": "bearish"
            if sum(t.value_usd for t in inflows)
            > sum(t.value_usd for t in outflows) * 1.5
            else "bullish"
            if sum(t.value_usd for t in outflows)
            > sum(t.value_usd for t in inflows) * 1.5
            else "neutral",
        }

    def _synthetic_transfers(self) -> list[WhaleTransfer]:
        import random

        tokens = ["BTC", "ETH", "SOL"]
        chains = ["ethereum", "bitcoin", "solana"]
        transfers = []
        for _ in range(random.randint(3, 8)):
            token = random.choice(tokens)
            chain = chains[tokens.index(token)]
            amount = (
                random.uniform(10, 500) if token == "BTC" else random.uniform(100, 5000)
            )
            price = {"BTC": 100000, "ETH": 3500, "SOL": 180}.get(token, 100)
            value = amount * price
            label = random.choice(
                ["exchange_deposit", "exchange_withdrawal", "unknown"]
            )
            transfers.append(
                WhaleTransfer(
                    chain=chain,
                    tx_hash=f"0x{random.randint(0, 2**64):016x}",
                    from_address=f"0x{random.randint(0, 2**160):040x}",
                    to_address=f"0x{random.randint(0, 2**160):04x}",
                    token=token,
                    amount=round(amount, 4),
                    value_usd=round(value, 2),
                    label=label,
                    timestamp=time.time() - random.uniform(0, 86400),
                )
            )
        return transfers


if __name__ == "__main__":
    print("=" * 60)
    print("DATA & ANALYTICS MODULES — DEMO")
    print("=" * 60)

    # 1. Funding Rate Monitor
    print("\n── Funding Rate Monitor ──")
    monitor = FundingRateMonitor(dry_run=True)
    signal = monitor.get_signal("BTC", spot_price=100_000)
    print(
        f"BTC Funding: rate={signal.current_rate:.6f} ({signal.annualized_yield:.2f}%/yr)"
    )
    print(f"  Z-score: {signal.z_score:.2f}, Signal: {signal.signal}")
    print(f"  Basis: {signal.basis_spread_bps:.1f} bps")
    for ex in signal.exchanges:
        print(f"  {ex['exchange']:10s}: {ex['rate']:.6f} ({ex['annualized']:.2f}%/yr)")

    arb = monitor.funding_arbitrage("BTC")
    print(f"  Arb opportunity: {arb['opportunity']}")

    # 2. Liquidation Feed
    print("\n── Liquidation Feed ──")
    liq = LiquidationFeed(dry_run=True)
    events = liq.get_recent("BTC/USDT")
    print(f"Recent liquidations: {len(events)}")
    for e in events[:3]:
        print(
            f"  {e.side:5s} {e.qty:.4f} @ ${e.price:,.0f} (${e.value_usd:,.0f}) on {e.exchange}"
        )

    # 3. Arbitrage Detector
    print("\n── Arbitrage Detector ──")
    arb_det = ArbitrageDetector(fee_bps=10, dry_run=True)
    opps = arb_det._synthetic_scan()
    print(f"Opportunities: {len(opps)}")
    for o in opps[:5]:
        print(
            f"  {o.symbol:12s} {o.buy_exchange}→{o.sell_exchange}: {o.spread_bps:.1f} bps ({o.confidence})"
        )

    # 4. Whale Tracker
    print("\n── Whale Tracker ──")
    whale = WhaleTracker(dry_run=True)
    summary = whale.get_summary()
    print(
        f"Transfers: {summary['total_transfers']}, Total: ${summary['total_value_usd']:,.0f}"
    )
    print(
        f"  Inflows: ${summary['exchange_inflow_usd']:,.0f}, Outflows: ${summary['exchange_outflow_usd']:,.0f}"
    )
    print(f"  Net flow: ${summary['net_flow_usd']:,.0f} ({summary['signal']})")
