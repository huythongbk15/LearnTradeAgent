#!/usr/bin/env python3
"""
Option Chain Data Provider — unified interface for options data.

Supports:
1. CEX options (Binance, OKX via CCXT-like API)
2. Deribit-style REST + WebSocket
3. Greeks calculation (Black-Scholes, implied vol)
4. Options flow analysis (unusual volume, put/call ratio)
5. Volatility surface construction

Design:
    provider = OptionChainProvider(exchange="binance")
    chain = provider.get_chain("BTC", expiry="2026-09-25")
    surface = provider.get_vol_surface("BTC")
    flow = provider.analyze_flow("BTC", lookback_hours=24)
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ── Black-Scholes ─────────────────────────────────────────────

def _norm_cdf(x: float) -> float:
    """Standard normal CDF (no scipy dependency)."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_price(spot: float, strike: float, tte: float, rate: float, vol: float, option_type: str = "call") -> float:
    """Black-Scholes price."""
    if tte <= 0 or vol <= 0:
        return max(spot - strike, 0) if option_type == "call" else max(strike - spot, 0)
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol ** 2) * tte) / (vol * math.sqrt(tte))
    d2 = d1 - vol * math.sqrt(tte)
    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * tte) * _norm_cdf(d2)
    else:
        return strike * math.exp(-rate * tte) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def bs_greeks(spot: float, strike: float, tte: float, rate: float, vol: float, option_type: str = "call") -> dict:
    """Compute all Greeks."""
    if tte <= 0 or vol <= 0:
        return {"delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0}
    d1 = (math.log(spot / strike) + (rate + 0.5 * vol ** 2) * tte) / (vol * math.sqrt(tte))
    d2 = d1 - vol * math.sqrt(tte)
    pdf = _norm_pdf(d1)
    sqrt_t = math.sqrt(tte)
    sign = 1 if option_type == "call" else -1
    delta = sign * _norm_cdf(sign * d1)
    gamma = pdf / (spot * vol * sqrt_t)
    theta = (-(spot * pdf * vol) / (2 * sqrt_t)
             - sign * rate * strike * math.exp(-rate * tte) * _norm_cdf(sign * d2)
             + (1 - sign) * 0.5 * spot * pdf * vol / sqrt_t) / 365
    vega = spot * pdf * sqrt_t / 100
    rho = sign * strike * tte * math.exp(-rate * tte) * _norm_cdf(sign * d2) / 100
    return {"delta": round(delta, 6), "gamma": round(gamma, 8), "theta": round(theta, 4),
            "vega": round(vega, 4), "rho": round(rho, 4)}


def implied_vol(price: float, spot: float, strike: float, tte: float, rate: float,
                option_type: str = "call", tol: float = 1e-6, max_iter: int = 100) -> float:
    """Newton-Raphson implied volatility solver."""
    vol = 0.5  # initial guess
    for _ in range(max_iter):
        model_price = bs_price(spot, strike, tte, rate, vol, option_type)
        diff = model_price - price
        if abs(diff) < tol:
            return vol
        d1 = (math.log(spot / strike) + (rate + 0.5 * vol ** 2) * tte) / (vol * math.sqrt(tte))
        vega = spot * _norm_pdf(d1) * math.sqrt(tte)
        if vega < 1e-10:
            break
        vol -= diff / vega
        vol = max(0.01, min(vol, 5.0))
    return vol


# ── Data Structures ───────────────────────────────────────────

@dataclass
class OptionContract:
    symbol: str
    strike: float
    expiry: str           # "2026-09-25"
    option_type: str       # "call" or "put"
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0
    volume: int = 0
    open_interest: int = 0
    iv: float = 0.0
    greeks: dict = field(default_factory=dict)


@dataclass
class OptionChain:
    underlying: str
    spot: float
    expiry: str
    calls: list[OptionContract] = field(default_factory=list)
    puts: list[OptionContract] = field(default_factory=list)
    timestamp: float = 0.0


@dataclass
class VolSurfacePoint:
    strike: float
    expiry: str
    tte: float
    iv: float
    option_type: str


@dataclass
class OptionsFlow:
    symbol: str
    period_hours: int
    total_call_volume: int = 0
    total_put_volume: int = 0
    put_call_ratio: float = 0.0
    unusual_trades: list[dict] = field(default_factory=list)
    net_delta_exposure: float = 0.0
    skew_25d: float = 0.0
    timestamp: float = 0.0


# ── Provider ──────────────────────────────────────────────────

class OptionChainProvider:
    """
    Unified options data provider.

    In production, connects to Deribit/Binance Options API.
    In dry_run mode, generates synthetic chain for testing.
    """

    def __init__(self, exchange: str = "deribit", dry_run: bool = True, config: dict | None = None):
        self.exchange = exchange
        self.dry_run = dry_run
        self.config = config or {}
        self.rate = self.config.get("risk_free_rate", 0.04)

    def get_chain(self, underlying: str, expiry: str = "", spot: float = 0) -> OptionChain:
        """Get option chain for a given underlying + expiry."""
        if self.dry_run:
            return self._synthetic_chain(underlying, expiry, spot)
        # In production: fetch from exchange API
        raise NotImplementedError(f"Live {self.exchange} option chain not implemented")

    def get_vol_surface(self, underlying: str, spot: float = 0) -> list[VolSurfacePoint]:
        """Build volatility surface across strikes and expiries."""
        chain1 = self.get_chain(underlying, expiry="2026-09-25", spot=spot)
        chain2 = self.get_chain(underlying, expiry="2026-12-25", spot=spot)
        surface = []
        for c in chain1.calls + chain1.puts:
            if c.iv > 0:
                surface.append(VolSurfacePoint(
                    strike=c.strike, expiry=chain1.expiry,
                    tte=self._tte(chain1.expiry), iv=c.iv, option_type=c.option_type
                ))
        for c in chain2.calls + chain2.puts:
            if c.iv > 0:
                surface.append(VolSurfacePoint(
                    strike=c.strike, expiry=chain2.expiry,
                    tte=self._tte(chain2.expiry), iv=c.iv, option_type=c.option_type
                ))
        return surface

    def analyze_flow(self, underlying: str, lookback_hours: int = 24) -> OptionsFlow:
        """Analyze options flow — unusual volume, put/call ratio, delta exposure."""
        chain = self.get_chain(underlying)
        call_vol = sum(c.volume for c in chain.calls)
        put_vol = sum(p.volume for p in chain.puts)
        total_vol = call_vol + put_vol

        # Unusual volume: trades > 2x average
        avg_vol = total_vol / max(len(chain.calls) + len(chain.puts), 1)
        unusual = []
        for c in chain.calls + chain.puts:
            if c.volume > avg_vol * 2 and c.volume > 10:
                unusual.append({
                    "strike": c.strike, "type": c.option_type, "volume": c.volume,
                    "iv": c.iv, "oi": c.open_interest,
                })

        # Net delta exposure (approximate)
        net_delta = sum(c.greeks.get("delta", 0) * c.volume for c in chain.calls)
        net_delta += sum(c.greeks.get("delta", 0) * c.volume for c in chain.puts)

        # 25-delta skew
        skew = self._compute_skew(chain)

        return OptionsFlow(
            symbol=underlying, period_hours=lookback_hours,
            total_call_volume=call_vol, total_put_volume=put_vol,
            put_call_ratio=put_vol / max(call_vol, 1),
            unusual_trades=unusual, net_delta_exposure=net_delta,
            skew_25d=skew, timestamp=time.time(),
        )

    def _synthetic_chain(self, underlying: str, expiry: str, spot: float) -> OptionChain:
        """Generate synthetic option chain for testing."""
        import random
        if spot <= 0:
            spot = {"BTC": 100_000, "ETH": 3_500, "SOL": 180}.get(underlying, 100)
        if not expiry:
            expiry = "2026-09-25"
        tte = self._tte(expiry)
        base_iv = 0.5 + random.uniform(-0.1, 0.1)

        strikes = [spot * (1 + d) for d in [-0.2, -0.15, -0.1, -0.05, -0.025, 0, 0.025, 0.05, 0.1, 0.15, 0.2]]
        strikes = [round(s, 2) for s in strikes]

        calls, puts = [], []
        for k in strikes:
            moneyness = math.log(spot / k)
            smile_iv = base_iv * (1 + 0.3 * moneyness ** 2 + 0.1 * moneyness)
            for otype, lst in [("call", calls), ("put", puts)]:
                price = bs_price(spot, k, tte, self.rate, smile_iv, otype)
                greeks = bs_greeks(spot, k, tte, self.rate, smile_iv, otype)
                vol = random.randint(50, 5000)
                oi = random.randint(100, 20000)
                spread = price * 0.02
                lst.append(OptionContract(
                    symbol=f"{underlying}-{expiry}-{k:.0f}-{otype[0].upper()}",
                    strike=k, expiry=expiry, option_type=otype,
                    bid=max(price - spread, 0.01), ask=price + spread,
                    last=price, volume=vol, open_interest=oi,
                    iv=round(smile_iv, 4), greeks=greeks,
                ))
        return OptionChain(underlying=underlying, spot=spot, expiry=expiry,
                           calls=calls, puts=puts, timestamp=time.time())

    def _tte(self, expiry: str) -> float:
        """Time to expiry in years."""
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days = max((exp - now).days, 1)
            return days / 365.25
        except Exception:
            return 0.25

    def _compute_skew(self, chain: OptionChain) -> float:
        """25-delta put IV minus 25-delta call IV."""
        puts_25d = [p for p in chain.puts if 0.2 < abs(p.greeks.get("delta", 0)) < 0.3]
        calls_25d = [c for c in chain.calls if 0.2 < abs(c.greeks.get("delta", 0)) < 0.3]
        if puts_25d and calls_25d:
            return puts_25d[0].iv - calls_25d[0].iv
        return 0.0


if __name__ == "__main__":
    provider = OptionChainProvider(dry_run=True)
    chain = provider.get_chain("BTC", expiry="2026-09-25")
    print(f"\nBTC Options Chain — Spot: ${chain.spot:,.0f}, Expiry: {chain.expiry}")
    print(f"Calls: {len(chain.calls)}, Puts: {len(chain.puts)}")
    print(f"\nTop 5 Calls:")
    for c in sorted(chain.calls, key=lambda x: x.volume, reverse=True)[:5]:
        print(f"  K=${c.strike:>10,.0f}  IV={c.iv:.1%}  Vol={c.volume:>6}  Bid/Ask={c.bid:.2f}/{c.ask:.2f}")
    print(f"\nTop 5 Puts:")
    for p in sorted(chain.puts, key=lambda x: x.volume, reverse=True)[:5]:
        print(f"  K=${p.strike:>10,.0f}  IV={p.iv:.1%}  Vol={p.volume:>6}  Bid/Ask={p.bid:.2f}/{p.ask:.2f}")
    print(f"\nGreeks (ATM Call):")
    atm = [c for c in chain.calls if abs(c.strike - chain.spot) / chain.spot < 0.03]
    if atm:
        print(f"  {atm[0].greeks}")

    flow = provider.analyze_flow("BTC")
    print(f"\nOptions Flow:")
    print(f"  Call Vol: {flow.total_call_volume}, Put Vol: {flow.total_put_volume}")
    print(f"  P/C Ratio: {flow.put_call_ratio:.2f}")
    print(f"  Unusual trades: {len(flow.unusual_trades)}")
    print(f"  25Δ Skew: {flow.skew_25d:.4f}")
