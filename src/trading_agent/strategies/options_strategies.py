#!/usr/bin/env python3
"""
Options Strategies Module — Vol selling, Gamma scalping, Dispersion trading.

Implements systematic options strategies for production use:
1. Covered Call / Put Selling — systematic premium collection
2. Short Straddle/Strangle — vol selling with delta hedging
3. Iron Condor — defined risk vol selling
4. Gamma Scalping — dynamic delta hedging to capture realized vol
5. Dispersion Trading — index vs single-stock vol arb
6. Calendar Spreads — term structure arbitrage
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from trading_agent.data.options_provider import (
    OptionChain,
    OptionChainProvider,
    OptionContract,
)


class OptionStrategyType(Enum):
    COVERED_CALL = "covered_call"
    CASH_SECURED_PUT = "cash_secured_put"
    SHORT_STRADDLE = "short_straddle"
    SHORT_STRANGLE = "short_strangle"
    IRON_CONDOR = "iron_condor"
    GAMMA_SCALP = "gamma_scalp"
    CALENDAR_SPREAD = "calendar_spread"
    DISPERSION = "dispersion"


@dataclass
class Position:
    """Option position with greeks."""

    contract: OptionContract
    qty: int  # positive = long, negative = short
    entry_price: float
    entry_time: float

    @property
    def delta(self) -> float:
        return self.contract.greeks.get("delta", 0) * self.qty

    @property
    def gamma(self) -> float:
        return self.contract.greeks.get("gamma", 0) * self.qty

    @property
    def theta(self) -> float:
        return self.contract.greeks.get("theta", 0) * self.qty

    @property
    def vega(self) -> float:
        return self.contract.greeks.get("vega", 0) * self.qty

    @property
    def notional(self) -> float:
        return abs(self.qty) * self.contract.strike


@dataclass
class OptionsStrategy:
    """Base options strategy."""

    name: str
    underlying: str
    provider: OptionChainProvider
    spot: float = 0
    positions: list[Position] = field(default_factory=list)
    cash: float = 0
    config: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.spot <= 0:
            chain = self.provider.get_chain(self.underlying)
            self.spot = chain.spot

    def add_position(self, contract: OptionContract, qty: int, price: float) -> None:
        pos = Position(
            contract=contract,
            qty=qty,
            entry_price=price,
            entry_time=time.time(),
        )
        self.positions.append(pos)

    def total_delta(self) -> float:
        return sum(p.delta for p in self.positions)

    def total_gamma(self) -> float:
        return sum(p.gamma for p in self.positions)

    def total_theta(self) -> float:
        return sum(p.theta for p in self.positions)

    def total_vega(self) -> float:
        return sum(p.vega for p in self.positions)

    def mark_to_market(self, spot: float, chain: OptionChain) -> dict:
        """Mark positions to market and compute P&L."""
        contracts = {f"{c.strike}-{c.option_type}": c for c in chain.calls + chain.puts}

        pnl = 0.0
        new_positions = []
        for pos in self.positions:
            key = f"{pos.contract.strike}-{pos.contract.option_type}"
            if key in contracts:
                current = contracts[key]
                mid = (current.bid + current.ask) / 2
                unrealized = (mid - pos.entry_price) * pos.qty * 100
                pnl += unrealized
                pos.contract = current
            new_positions.append(pos)
        self.positions = new_positions
        return {
            "unrealized_pnl": pnl,
            "total_delta": self.total_delta(),
            "total_gamma": self.total_gamma(),
            "total_theta": self.total_theta(),
            "total_vega": self.total_vega(),
        }


# ══════════════════════════════════════════════════════════════════════════
# 1. Covered Call / Cash-Secured Put
# ══════════════════════════════════════════════════════════════════════════


class CoveredCallStrategy(OptionsStrategy):
    """
    Covered Call: Long underlying + Short OTM Call.
    Collect premium, capped upside, full downside risk.

    Config:
        - delta_target: 0.15-0.30 (OTM call delta)
        - dte_min: 7, dte_max: 45
        - roll_threshold: 0.02 (moneyness from strike)
    """

    def __init__(
        self,
        underlying: str,
        provider: OptionChainProvider,
        config: dict | None = None,
        spot: float = 0,
    ):
        super().__init__("CoveredCall", underlying, provider, spot, config=config)
        self.cash = self.config.get("initial_capital", 100_000)
        self.shares = self.config.get("initial_shares", 0)

    def generate_signals(self, chain: OptionChain) -> list[dict]:
        """Find best OTM call to sell."""
        signals = []
        dte = self._dte(chain.expiry)
        if not (self.config.get("dte_min", 7) <= dte <= self.config.get("dte_max", 45)):
            return signals

        target_delta = self.config.get("delta_target", 0.20)

        for call in chain.calls:
            delta = abs(call.greeks.get("delta", 0))
            if 0.05 <= delta <= target_delta + 0.05:
                mid = (call.bid + call.ask) / 2
                if mid > 0.01:
                    premium_yield = mid / chain.spot * (365 / dte)
                    if premium_yield >= self.config.get("min_annual_yield", 0.05):
                        signals.append(
                            {
                                "action": "SELL_CALL",
                                "contract": call,
                                "price": call.bid,
                                "shares_needed": 100,
                                "premium_yield_annual": premium_yield,
                                "delta": delta,
                            }
                        )
        return sorted(signals, key=lambda x: x["premium_yield_annual"], reverse=True)[
            :3
        ]

    def roll_call(self, chain: OptionChain, current_strike: float) -> dict | None:
        """Roll short call to higher strike if ITM risk."""
        if current_strike <= self.spot * (1 + self.config.get("roll_threshold", 0.02)):
            return None
        for call in chain.calls:
            if call.strike > current_strike and abs(call.greeks.get("delta", 0)) < 0.3:
                return {
                    "action": "ROLL_CALL",
                    "from_strike": current_strike,
                    "to_strike": call.strike,
                    "buy_back_price": call.ask,
                    "sell_new_price": call.bid,
                }
        return None

    def _dte(self, expiry: str) -> int:
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return max((exp - datetime.now(timezone.utc)).days, 1)
        except Exception:
            return 30


class CashSecuredPutStrategy(OptionsStrategy):
    """
    Cash-Secured Put: Short OTM Put, secured by cash.
    Collect premium, assigned shares if ITM at expiry.
    """

    def __init__(
        self,
        underlying: str,
        provider: OptionChainProvider,
        config: dict | None = None,
        spot: float = 0,
    ):
        super().__init__("CashSecuredPut", underlying, provider, spot, config=config)
        self.cash = self.config.get("initial_capital", 100_000)

    def generate_signals(self, chain: OptionChain) -> list[dict]:
        signals = []
        dte = self._dte(chain.expiry)
        if not (self.config.get("dte_min", 7) <= dte <= self.config.get("dte_max", 45)):
            return signals

        target_delta = self.config.get("delta_target", 0.20)

        for put in chain.puts:
            delta = abs(put.greeks.get("delta", 0))
            if 0.05 <= delta <= target_delta + 0.05:
                mid = (put.bid + put.ask) / 2
                if mid > 0.01:
                    cash_secured = put.strike * 100
                    premium_yield = mid * 100 / cash_secured * (365 / dte)
                    if premium_yield >= self.config.get("min_annual_yield", 0.05):
                        signals.append(
                            {
                                "action": "SELL_PUT",
                                "contract": put,
                                "price": put.bid,
                                "cash_required": cash_secured,
                                "premium_yield_annual": premium_yield,
                                "delta": delta,
                            }
                        )
        return sorted(signals, key=lambda x: x["premium_yield_annual"], reverse=True)[
            :3
        ]

    def _dte(self, expiry: str) -> int:
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return max((exp - datetime.now(timezone.utc)).days, 1)
        except Exception:
            return 30


# ══════════════════════════════════════════════════════════════════════════
# 2. Short Straddle / Strangle / Iron Condor
# ══════════════════════════════════════════════════════════════════════════


class ShortStraddleStrategy(OptionsStrategy):
    """Short Straddle: Sell ATM Call + ATM Put (same strike)."""

    def __init__(
        self,
        underlying: str,
        provider: OptionChainProvider,
        config: dict | None = None,
        spot: float = 0,
    ):
        super().__init__("ShortStraddle", underlying, provider, spot, config=config)

    def generate_signals(self, chain: OptionChain) -> list[dict]:
        signals = []
        dte = self._dte(chain.expiry)
        if not (self.config.get("dte_min", 7) <= dte <= self.config.get("dte_max", 30)):
            return signals

        atm_call = min(chain.calls, key=lambda c: abs(c.strike - chain.spot))
        atm_put = min(chain.puts, key=lambda p: abs(p.strike - chain.spot))

        if abs(atm_call.strike - atm_put.strike) / chain.spot < 0.01:
            call_mid = (atm_call.bid + atm_call.ask) / 2
            put_mid = (atm_put.bid + atm_put.ask) / 2
            total_premium = (call_mid + put_mid) * 100

            signals.append(
                {
                    "action": "SELL_STRADDLE",
                    "call": atm_call,
                    "put": atm_put,
                    "call_price": atm_call.bid,
                    "put_price": atm_put.bid,
                    "total_premium": total_premium,
                    "max_profit": total_premium,
                    "breakeven_up": atm_call.strike + call_mid + put_mid,
                    "breakeven_dn": atm_call.strike - call_mid - put_mid,
                    "dte": dte,
                }
            )
        return signals

    def _dte(self, expiry: str) -> int:
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return max((exp - datetime.now(timezone.utc)).days, 1)
        except Exception:
            return 30


class ShortStrangleStrategy(OptionsStrategy):
    """Short Strangle: Sell OTM Call + OTM Put (different strikes)."""

    def __init__(
        self,
        underlying: str,
        provider: OptionChainProvider,
        config: dict | None = None,
        spot: float = 0,
    ):
        super().__init__("ShortStrangle", underlying, provider, spot, config=config)

    def generate_signals(self, chain: OptionChain) -> list[dict]:
        signals = []
        dte = self._dte(chain.expiry)
        if not (self.config.get("dte_min", 7) <= dte <= self.config.get("dte_max", 45)):
            return signals

        delta_target = self.config.get("delta_target", 0.15)

        otm_calls = [
            c
            for c in chain.calls
            if c.strike > chain.spot
            and 0.05 < abs(c.greeks.get("delta", 0)) < delta_target
        ]
        otm_puts = [
            p
            for p in chain.puts
            if p.strike < chain.spot
            and 0.05 < abs(p.greeks.get("delta", 0)) < delta_target
        ]

        for call in otm_calls[:3]:
            for put in otm_puts[:3]:
                call_mid = (call.bid + call.ask) / 2
                put_mid = (put.bid + put.ask) / 2
                total_premium = (call_mid + put_mid) * 100
                breakeven_up = call.strike + call_mid + put_mid
                breakeven_dn = put.strike - call_mid - put_mid

                signals.append(
                    {
                        "action": "SELL_STRANGLE",
                        "call": call,
                        "put": put,
                        "call_price": call.bid,
                        "put_price": put.bid,
                        "total_premium": total_premium,
                        "max_profit": total_premium,
                        "breakeven_up": breakeven_up,
                        "breakeven_dn": breakeven_dn,
                        "width_pct": (call.strike - put.strike) / chain.spot,
                        "dte": dte,
                    }
                )
        return sorted(signals, key=lambda x: x["total_premium"], reverse=True)[:5]

    def _dte(self, expiry: str) -> int:
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return max((exp - datetime.now(timezone.utc)).days, 1)
        except Exception:
            return 30


class IronCondorStrategy(OptionsStrategy):
    """Iron Condor: Short Strangle + Long Wings (defined risk)."""

    def __init__(
        self,
        underlying: str,
        provider: OptionChainProvider,
        config: dict | None = None,
        spot: float = 0,
    ):
        super().__init__("IronCondor", underlying, provider, spot, config=config)

    def generate_signals(self, chain: OptionChain) -> list[dict]:
        signals = []
        dte = self._dte(chain.expiry)
        if not (
            self.config.get("dte_min", 14) <= dte <= self.config.get("dte_max", 60)
        ):
            return signals

        delta_short = self.config.get("delta_short", 0.15)
        delta_long = self.config.get("delta_long", 0.05)

        otm_calls = [c for c in chain.calls if c.strike > chain.spot]
        otm_puts = [p for p in chain.puts if p.strike < chain.spot]

        short_calls = [
            c for c in otm_calls if 0.05 < abs(c.greeks.get("delta", 0)) < delta_short
        ]
        short_puts = [
            p for p in otm_puts if 0.05 < abs(p.greeks.get("delta", 0)) < delta_short
        ]

        for sc in short_calls[:3]:
            for sp in short_puts[:3]:
                long_calls = [
                    c
                    for c in otm_calls
                    if c.strike > sc.strike
                    and abs(c.greeks.get("delta", 0)) < delta_long
                ]
                long_puts = [
                    p
                    for p in otm_puts
                    if p.strike < sp.strike
                    and abs(p.greeks.get("delta", 0)) < delta_long
                ]

                for lc in long_calls[:2]:
                    for lp in long_puts[:2]:
                        sc_mid = (sc.bid + sc.ask) / 2
                        sp_mid = (sp.bid + sp.ask) / 2
                        lc_mid = (lc.bid + lc.ask) / 2
                        lp_mid = (lp.bid + lp.ask) / 2

                        credit = (sc_mid + sp_mid - lc_mid - lp_mid) * 100
                        max_loss = (lc.strike - sc.strike) * 100 - credit
                        prob_profit = 1 - (
                            abs(sc.greeks.get("delta", 0))
                            + abs(sp.greeks.get("delta", 0))
                        )

                        if credit > 0 and max_loss > 0 and prob_profit > 0.5:
                            signals.append(
                                {
                                    "action": "SELL_IRON_CONDOR",
                                    "short_call": sc,
                                    "short_put": sp,
                                    "long_call": lc,
                                    "long_put": lp,
                                    "credit": credit,
                                    "max_loss": max_loss,
                                    "max_profit": credit,
                                    "prob_profit": prob_profit,
                                    "risk_reward": credit / max_loss,
                                    "dte": dte,
                                }
                            )
        return sorted(signals, key=lambda x: x["risk_reward"], reverse=True)[:5]

    def _dte(self, expiry: str) -> int:
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return max((exp - datetime.now(timezone.utc)).days, 1)
        except Exception:
            return 30


# ══════════════════════════════════════════════════════════════════════════
# 3. Gamma Scalping — Dynamic Delta Hedging
# ══════════════════════════════════════════════════════════════════════════


class GammaScalpStrategy(OptionsStrategy):
    """
    Gamma Scalping: Buy ATM Straddle + Dynamic Delta Hedge.
    Profit from realized vol > implied vol.
    P&L ≈ 0.5 * Γ * (ΔS)² - θ * Δt per rebalance.
    """

    def __init__(
        self,
        underlying: str,
        provider: OptionChainProvider,
        config: dict | None = None,
        spot: float = 0,
    ):
        super().__init__("GammaScalp", underlying, provider, spot, config=config)
        self.hedge_qty = 0.0
        self.last_spot = self.spot
        self.total_scalp_pnl = 0.0
        self.rebalance_count = 0

    def enter_straddle(self, chain: OptionChain) -> dict | None:
        atm_call = min(chain.calls, key=lambda c: abs(c.strike - chain.spot))
        atm_put = min(chain.puts, key=lambda p: abs(p.strike - chain.spot))

        if abs(atm_call.strike - atm_put.strike) / chain.spot < 0.01:
            call_mid = (atm_call.bid + atm_call.ask) / 2
            put_mid = (atm_put.bid + atm_put.ask) / 2
            total_cost = (call_mid + put_mid) * 100

            self.add_position(atm_call, -1, call_mid)
            self.add_position(atm_put, -1, put_mid)
            self.last_spot = chain.spot

            total_delta = self.total_delta()
            self.hedge_qty = -total_delta
            self.rebalance_count = 1

            return {
                "action": "ENTER_STRADDLE",
                "strike": atm_call.strike,
                "call_price": call_mid,
                "put_price": put_mid,
                "total_cost": total_cost,
                "initial_delta": total_delta,
                "initial_gamma": self.total_gamma(),
                "hedge_qty": self.hedge_qty,
            }
        return None

    def rebalance_delta(self, new_spot: float, chain: OptionChain) -> dict:
        old_hedge = self.hedge_qty
        self.total_scalp_pnl += old_hedge * (new_spot - self.last_spot)

        total_delta = self.total_delta()
        self.hedge_qty = -total_delta
        self.last_spot = new_spot
        self.rebalance_count += 1

        return {
            "action": "REBALANCE",
            "spot": new_spot,
            "old_hedge": old_hedge,
            "new_hedge": self.hedge_qty,
            "delta_change": total_delta,
            "scalp_pnl": self.total_scalp_pnl,
            "rebalance_count": self.rebalance_count,
        }

    def estimate_daily_pnl(self, realized_vol: float, implied_vol: float) -> float:
        gamma = self.total_gamma()
        dt = 1 / 252
        pnl = 0.5 * gamma * self.spot**2 * (realized_vol**2 - implied_vol**2) * dt
        return pnl

    def exit_straddle(self, chain: OptionChain) -> dict:
        pnl = 0.0
        for pos in self.positions:
            key = f"{pos.contract.strike}-{pos.contract.option_type}"
            for c in chain.calls + chain.puts:
                if f"{c.strike}-{c.option_type}" == key:
                    mid = (c.bid + c.ask) / 2
                    pnl += (pos.entry_price - mid) * pos.qty * 100
                    break

        self.total_scalp_pnl += pnl
        result = {
            "action": "EXIT_STRADDLE",
            "straddle_pnl": pnl,
            "scalp_pnl": self.total_scalp_pnl - pnl,
            "total_pnl": self.total_scalp_pnl,
            "rebalance_count": self.rebalance_count,
        }
        self.positions = []
        self.hedge_qty = 0.0
        return result


# ══════════════════════════════════════════════════════════════════════════
# 4. Calendar Spread (Term Structure Arb)
# ══════════════════════════════════════════════════════════════════════════


class CalendarSpreadStrategy(OptionsStrategy):
    """Calendar Spread: Sell near-term, Buy longer-term (same strike)."""

    def __init__(
        self,
        underlying: str,
        provider: OptionChainProvider,
        config: dict | None = None,
        spot: float = 0,
    ):
        super().__init__("CalendarSpread", underlying, provider, spot, config=config)

    def generate_signals(self, spot: float) -> list[dict]:
        signals = []
        chain_near = self.provider.get_chain(
            self.underlying, expiry="2026-09-25", spot=spot
        )
        chain_far = self.provider.get_chain(
            self.underlying, expiry="2026-12-25", spot=spot
        )

        near_strikes = {c.strike for c in chain_near.calls + chain_near.puts}
        far_strikes = {c.strike for c in chain_far.calls + chain_far.puts}
        common = near_strikes & far_strikes

        for k in sorted(common):
            near_call = next(c for c in chain_near.calls if c.strike == k)
            far_call = next(c for c in chain_far.calls if c.strike == k)

            near_mid = (near_call.bid + near_call.ask) / 2
            far_mid = (far_call.bid + far_call.ask) / 2

            if near_mid > 0 and far_mid > 0:
                cost = (far_mid - near_mid) * 100
                theta_near = near_call.greeks.get("theta", 0)
                theta_far = far_call.greeks.get("theta", 0)

                signals.append(
                    {
                        "action": "BUY_CALENDAR_CALL",
                        "strike": k,
                        "near_expiry": "2026-09-25",
                        "far_expiry": "2026-12-25",
                        "near_price": near_mid,
                        "far_price": far_mid,
                        "net_debit": cost,
                        "theta_carry": theta_near - theta_far,
                        "iv_near": near_call.iv,
                        "iv_far": far_call.iv,
                        "term_structure": far_call.iv - near_call.iv,
                    }
                )

            near_put = next(p for p in chain_near.puts if p.strike == k)
            far_put = next(p for p in chain_far.puts if p.strike == k)

            near_mid = (near_put.bid + near_put.ask) / 2
            far_mid = (far_put.bid + far_put.ask) / 2

            if near_mid > 0 and far_mid > 0:
                cost = (far_mid - near_mid) * 100
                theta_near = near_put.greeks.get("theta", 0)
                theta_far = far_put.greeks.get("theta", 0)

                signals.append(
                    {
                        "action": "BUY_CALENDAR_PUT",
                        "strike": k,
                        "near_expiry": "2026-09-25",
                        "far_expiry": "2026-12-25",
                        "near_price": near_mid,
                        "far_price": far_mid,
                        "net_debit": cost,
                        "theta_carry": theta_near - theta_far,
                        "iv_near": near_put.iv,
                        "iv_far": far_put.iv,
                        "term_structure": far_put.iv - near_put.iv,
                    }
                )

        return sorted(signals, key=lambda x: x.get("theta_carry", 0), reverse=True)[:10]

    def _dte(self, expiry: str) -> int:
        try:
            exp = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return max((exp - datetime.now(timezone.utc)).days, 1)
        except Exception:
            return 30


# ══════════════════════════════════════════════════════════════════════════
# 5. Dispersion Trading (Index vs Components)
# ══════════════════════════════════════════════════════════════════════════


class DispersionStrategy(OptionsStrategy):
    """
    Dispersion Trading: Sell index vol, Buy single-stock vol.
    Index vol typically cheaper due to correlation < 1.
    """

    def __init__(
        self,
        index_underlying: str,
        component_underlyings: list[str],
        provider: OptionChainProvider,
        config: dict | None = None,
    ):
        super().__init__("Dispersion", index_underlying, provider, config=config)
        self.components = component_underlyings
        self.index_spot = 0
        self.component_spots = {}

    def generate_signals(
        self, index_spot: float, component_spots: dict[str, float]
    ) -> list[dict]:
        self.index_spot = index_spot
        self.component_spots = component_spots

        signals = []
        idx_chain = self.provider.get_chain(self.underlying, spot=index_spot)

        # Index ATM straddle
        idx_call = min(idx_chain.calls, key=lambda c: abs(c.strike - index_spot))
        idx_put = min(idx_chain.puts, key=lambda p: abs(p.strike - index_spot))
        idx_iv = (idx_call.iv + idx_put.iv) / 2

        # Component IVs
        comp_ivs = []
        for comp in self.components:
            comp_spot = component_spots.get(comp, 0)
            if comp_spot <= 0:
                continue
            comp_chain = self.provider.get_chain(comp, spot=comp_spot)
            c_call = min(comp_chain.calls, key=lambda c: abs(c.strike - comp_spot))
            c_put = min(comp_chain.puts, key=lambda p: abs(p.strike - comp_spot))
            comp_ivs.append((c_call.iv + c_put.iv) / 2)

        if comp_ivs:
            avg_comp_iv = sum(comp_ivs) / len(comp_ivs)
            dispersion = avg_comp_iv - idx_iv

            if dispersion > 0.05:  # 5% vol spread
                signals.append(
                    {
                        "action": "SELL_INDEX_BUY_COMPONENTS",
                        "index_iv": idx_iv,
                        "avg_component_iv": avg_comp_iv,
                        "dispersion_spread": dispersion,
                        "implied_correlation": (idx_iv**2)
                        / (sum(iv**2 for iv in comp_ivs) / len(comp_ivs))
                        if comp_ivs
                        else 0,
                    }
                )

        return signals


# ══════════════════════════════════════════════════
