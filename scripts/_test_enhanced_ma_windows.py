#!/usr/bin/env python3
"""Does enhanced_ma (9/9 WFO folds) actually have edge, or only in a bear?

Runs the strategy through the canonical forecast contract on several
BTC/USDT 1h windows and compares against buy & hold. Uses the promoted
default params (20/80 with ADX filter, ATR SL/TP) rather than tuned ones,
so the result reflects what the system would actually trade.

Long-only accounting to match the rest of the AC14 evidence; the forecast
sign also drives an informational short leg so a bear-only edge is visible
as such rather than hidden.

Run: .venv/bin/python scripts/_test_enhanced_ma_windows.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SYMBOL = "BTC/USDT"
COMMISSION = 0.001
SLIPPAGE = 0.0005
SPREAD_BPS = 2.0
INITIAL_CAPITAL = 100_000.0
HISTORY = 500

OUT = Path("/tmp/enhanced_ma_windows.json")

# (label, start_bar, note). Bars are 1h; data spans 2020-01..2026-09.
WINDOWS = [
    ("bear_2022", 20_000, "Terra/FTX aftermath - deep bear"),
    ("recovery_2023", 32_000, "post-FTZ recovery, first leg up"),
    ("bull_2023H2", 34_000, "2023-07..12 sustained uptrend"),
    ("bull_early_2024", 41_000, "2024-01..06 halving rally"),
    ("sideways_2024H2", 48_000, "2024-07..2025-01 range-bound"),
    ("bear_2025", 54_000, "2025 drawdown"),
]


def oracle(signals: np.ndarray, opens: np.ndarray) -> dict:
    n = len(opens)
    half = (COMMISSION + SLIPPAGE + SPREAD_BPS / 10_000) / 2
    buy_f, sell_f = 1 + half, 1 - half
    cash, qty, entry = INITIAL_CAPITAL, 0.0, 0.0
    pnls: list[float] = []
    eq = np.full(n, INITIAL_CAPITAL)
    n_tr = 0

    for i in range(n):
        s = int(signals[i])
        if i + 1 < n:
            nxt = opens[i + 1]
            if s > 0 and qty == 0:
                fp = nxt * buy_f
                qty = cash / fp
                cash -= qty * fp
                entry = fp
            elif s <= 0 and qty > 0:
                fp = nxt * sell_f
                cash += qty * fp
                pnls.append(qty * (fp - entry))
                qty, entry = 0.0, 0.0
                n_tr += 1
        eq[i] = cash + (qty * opens[i] if qty > 0 else 0.0)
    if qty > 0:
        fp = opens[-1] * sell_f
        cash += qty * fp
        pnls.append(qty * (fp - entry))
        n_tr += 1
    eq[-1] = cash

    rets = np.diff(eq) / np.maximum(eq[:-1], 1e-12)
    rets = np.nan_to_num(rets, nan=0.0, posinf=0.0, neginf=0.0)
    sharpe = (
        float(np.mean(rets) / np.std(rets) * math.sqrt(8760))
        if len(rets) > 1 and float(np.std(rets)) > 1e-12 else 0.0
    )
    peak = np.maximum.accumulate(eq)
    mdd = abs(float(np.min((eq - peak) / np.maximum(peak, 1e-12)))) * 100
    return {
        "ret": round((cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 3),
        "sharpe": round(sharpe, 3),
        "mdd": round(mdd, 2),
        "trades": n_tr,
        "exposure": round(float((signals != 0).mean()), 3),
    }


def main() -> None:
    from trading_agent.authority.config import Environment
    from trading_agent.research.forecast import MarketObservation
    from trading_agent.strategies.canonical import (
        OHLCV_WINDOW_FEATURE, build_default_registry,
    )

    bars = pl.read_parquet(ROOT / "data/raw/binance/BTC_USDT/1h_full.parquet").sort(
        "timestamp"
    )
    if bars.schema["timestamp"] == pl.Datetime(time_unit="us"):
        bars = bars.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))

    registry = build_default_registry()
    _, strat = registry.get("enhanced_ma", environment=Environment.RESEARCH)
    print("strategy: enhanced_ma (promoted default params: 20/80 MA + ADX, ATR SL/TP)\n")

    rows = []
    for label, start, note in WINDOWS:
        lo = max(start - HISTORY, 0)
        if start + 8000 > bars.height:
            print(f"{label:18s} SKIP (beyond data)")
            continue
        w = bars.slice(lo, 8000 + HISTORY)
        ev = w.slice(HISTORY, 8000)
        opens = ev["open"].to_numpy()
        closes = ev["close"].to_numpy()
        n = len(opens)

        ts = ev["timestamp"].to_list()
        o, h, low = ev["open"].to_numpy(), ev["high"].to_numpy(), ev["low"].to_numpy()
        c, v = ev["close"].to_numpy(), ev["volume"].to_numpy()

        sig = np.zeros(n, dtype=np.int64)
        for i in range(n):
            hist = w.slice(i, HISTORY + 1)  # trailing HISTORY+1 bars ending at i
            obs = MarketObservation(
                symbol=SYMBOL, observed_at=ts[i], open=o[i], high=h[i],
                low=low[i], close=c[i], volume=v[i],
                features={OHLCV_WINDOW_FEATURE: hist},
            )
            try:
                fc = strat.forecast(obs)
            except Exception:
                continue
            e = float(getattr(fc, "expected_excess_return", 0.0) or 0.0)
            sig[i] = 1 if e > 1e-6 else (-1 if e < -1e-6 else 0)

        if not np.any(sig != 0):
            print(f"{label:18s} FLAT - no signals emitted, skipping (not an 'edge')")
            continue

        long_m = oracle(sig, opens.copy())
        flat = np.zeros(n, dtype=np.int64)
        long_m["ret_if_short"] = oracle(-sig, opens.copy())["ret"]
        bh = (closes[-1] / opens[0] - 1) * 100
        edge = long_m["ret"] - bh
        rows.append({
            "label": label, "note": note,
            "start": str(ts[0])[:10], "end": str(ts[-1])[:10],
            "buy_hold": round(bh, 3), "edge_vs_hold": round(edge, 3),
            "long": long_m, "beats_hold": edge > 0,
            "long_signal_bars": int((sig > 0).sum()),
            "short_signal_bars": int((sig < 0).sum()),
        })
        print(f"{label:18s} {rows[-1]['start']}..{rows[-1]['end']}  "
              f"long {long_m['ret']:+7.2f}% (sh {long_m['sharpe']:>6.2f}, "
              f"DD {long_m['mdd']:>5.1f}%, {long_m['trades']:>3d} trades)  "
              f"B&H {bh:+7.2f}%  edge {edge:+7.2f}pp  "
              f"{'BEATS' if edge > 0 else 'loses'}")

    beats = [r for r in rows if r["beats_hold"]]
    bull = [r for r in rows if "bull" in r["label"]]
    bull_beats = [r for r in bull if r["beats_hold"]]

    print("\n" + "=" * 72)
    print(f"windows tested         : {len(rows)}")
    print(f"windows beating hold   : {len(beats)}  {[r['label'] for r in beats]}")
    print(f"bull windows           : {len(bull)}  {[r['label'] for r in bull]}")
    print(f"bull windows beating   : {len(bull_beats)}  {[r['label'] for r in bull_beats]}")
    if bull and not bull_beats:
        print("\nVERDICT: edge is bear-only. enhanced_ma avoids losses but does")
        print("         not capture an uptrend - it is a defensive filter, not an")
        print("         alpha source. It beats hold by losing less, not by winning.")
    elif bull_beats:
        print("\nVERDICT: edge holds in a bull market too - the WFO folds are real.")
    elif not rows:
        print("\nVERDICT: inconclusive - no window produced signals.")
    print("=" * 72)

    json.dump({"windows": rows, "bull_beats": [r["label"] for r in bull_beats]},
              open(OUT, "w"), indent=2, default=str)
    print(f"evidence: {OUT}")


if __name__ == "__main__":
    main()
