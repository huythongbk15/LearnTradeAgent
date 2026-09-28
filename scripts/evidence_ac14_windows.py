#!/usr/bin/env python3
"""AC14 window sweep: does ANY window let a promoted policy beat buy & hold?

Single-window evidence (evidence_ac14_promoted.py) showed adaptive and
incumbent both trailing buy&hold on a 2022-04..2023-03 bear. That leaves
two explanations: the window is hostile, or the strategies are weak.

This sweeps candidate windows and reports adaptive vs incumbent vs hold.
The verdict separates the two:

  * at least one window where a promoted policy beats hold  -> the
    strategies carry edge and the 2022 window was simply hostile
  * no window beats hold                                        -> the
    registry has no out-of-sample edge under these costs

Regime labels come from HybridRegimeDetector, bindings from the promoted
policy stores, and every leg is scored by the same independent oracle.

Run: .venv/bin/python scripts/evidence_ac14_windows.py
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np
import polars as pl

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SYMBOL = "BTC/USDT"
TIMEFRAME = "1h"
COMMISSION = 0.001
SLIPPAGE = 0.0005
SPREAD_BPS = 2.0
INITIAL_CAPITAL = 100_000.0
EDGE_DEADBAND = 1e-6
HISTORY = 500

DATA_PATH = ROOT / "data" / "raw" / "binance" / "BTC_USDT" / "1h_full.parquet"
OUT_PATH = Path("/tmp/ac14_windows_evidence.json")

WARMUP_BARS = 200
WINDOW_BARS = 8_000

# Candidate windows, chosen from the monthly vol profile in
# scripts/_probe_regimes.py plus well-known BTC phases. Each is a
# (label, start_bar, note) triple; the sweep is data-driven only in how
# the windows were picked, every leg below is computed.
WINDOWS = [
    ("bear_2022", 20_000, "Terra/LUNA + FTX aftermath -> 2023 bull transition"),
    ("bull_2023H2", 34_000, "2023-07..2023-12 sustained uptrend"),
    ("bull_early_2024", 41_000, "2024-01..2024-06 BTC halving rally"),
    ("sideways_2024H2", 48_000, "2024-07..2025-01 range-bound chop"),
    ("volatile_2025", 54_000, "2025-03..2025-09 higher-vol regime"),
]

REGIME_ORDER = ["trend", "high_vol", "mean_reversion", "crisis", "other"]


def oracle(signals: np.ndarray, opens: np.ndarray) -> dict:
    n = len(opens)
    half = (COMMISSION + SLIPPAGE + SPREAD_BPS / 10_000) / 2
    buy_f, sell_f = 1 + half, 1 - half
    cash, qty, entry = INITIAL_CAPITAL, 0.0, 0.0
    pnls: list[float] = []
    eq = np.full(n, INITIAL_CAPITAL)
    n_switch = 0

    for i in range(n):
        s = int(signals[i])
        if i + 1 < n:
            nxt = opens[i + 1]
            if s > 0 and qty == 0:
                fp = nxt * buy_f
                qty = cash / fp
                cash -= qty * fp
                entry = fp
                n_switch += 1
            elif s <= 0 and qty > 0:
                fp = nxt * sell_f
                cash += qty * fp
                pnls.append(qty * (fp - entry))
                qty, entry = 0.0, 0.0
                n_switch += 1
        eq[i] = cash + (qty * opens[i] if qty > 0 else 0.0)

    if qty > 0:
        fp = opens[-1] * sell_f
        cash += qty * fp
        pnls.append(qty * (fp - entry))
        n_switch += 1
    eq[-1] = cash

    rets = np.diff(eq) / np.maximum(eq[:-1], 1e-12)
    rets = np.nan_to_num(rets, nan=0.0, posinf=0.0, neginf=0.0)
    sharpe = (
        float(np.mean(rets) / np.std(rets) * math.sqrt(8760))
        if len(rets) > 1 and float(np.std(rets)) > 1e-12 else 0.0
    )
    peak = np.maximum.accumulate(eq)
    mdd = abs(float(np.min((eq - peak) / np.maximum(peak, 1e-12)))) * 100
    # exposure = share of bars carrying a non-flat signal
    n_active = int((signals != 0).sum())
    return {
        "total_return_pct": round((cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(mdd, 4),
        "total_trades": len(pnls),
        "exposure": round(n_active / max(n, 1), 4),
        "final_equity": round(cash, 2),
    }


def regime_key(label: str) -> str:
    v = str(label).lower()
    if "crisis" in v:
        return "crisis"
    if "high" in v or "vol" in v:
        return "high_vol"
    if "trend" in v:
        return "trend"
    if "revert" in v or "range" in v or "mean" in v:
        return "mean_reversion"
    return "other"


def discover_bindings() -> tuple[dict, list[dict]]:
    best: dict[str, dict] = {}
    census = []
    for store in sorted(ROOT.glob("data/*/policies")):
        files = [f for f in store.glob("*.json") if ".sig." not in f.name]
        hit = []
        for f in files:
            try:
                p = json.loads(f.read_text())
            except Exception:
                continue
            if p.get("symbol") == SYMBOL and p.get("timeframe") == TIMEFRAME:
                if p.get("status") in {"validated", "active"}:
                    hit.append(p)
        if not hit:
            continue
        census.append({"store": store.parent.name, "usable": len(hit)})
        for p in hit:
            inc = p.get("incumbent") or {}
            if not inc.get("strategy_id"):
                continue
            r = p["regime"]
            sc = float(p.get("scores", {}).get("selection_score", 0.0))
            if r not in best or sc > float(best[r]["scores"]["selection_score"]):
                best[r] = p
    return best, census


def load_bars() -> pl.DataFrame:
    df = pl.read_parquet(DATA_PATH).sort("timestamp")
    if df.schema["timestamp"] == pl.Datetime(time_unit="us"):
        df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))
    return df


def main() -> None:
    bindings_policies, census = discover_bindings()
    bindings = {r: p["incumbent"]["strategy_id"] for r, p in bindings_policies.items()}
    print(f"=== bindings from promoted stores ({len(census)} stores) ===")
    for r, s in sorted(bindings.items()):
        print(f"  {r:16s} -> {s}")

    from trading_agent.authority.adaptive_router import (
        AdaptiveRouterConfig, AdaptiveStrategyRouter, Environment, RouterStateStore,
    )
    from trading_agent.authority.config import Environment as CanonEnv
    from trading_agent.ml.regime_detection import HybridRegimeDetector, RegimePosterior
    from trading_agent.research.forecast import MarketObservation
    from trading_agent.research.selection_policy import (
        ParamArtifact, PolicyActivationService, PolicyStatus,
        SelectionPolicyArtifact, SelectionPolicyRegistry,
    )
    from trading_agent.strategies.canonical import (
        OHLCV_WINDOW_FEATURE, build_default_registry,
    )

    registry = build_default_registry()
    bars = load_bars()
    key_bytes = b"ac14-window-sweep-key00000"

    # Build one strategy instance per distinct promoted strategy, reused
    # across windows so indicator state is not rebuilt needlessly.
    strat_cache: dict[str, object] = {}
    for sid in sorted(set(bindings.values())):
        if registry.has(sid):
            try:
                _, strat_cache[sid] = registry.get(sid, environment=CanonEnv.RESEARCH)
            except Exception as exc:
                print(f"  ! {sid}: {exc}")
        else:
            print(f"  ! {sid} not on canonical allowlist")

    rows = []
    for label, start_bar, note in WINDOWS:
        if start_bar + WINDOW_BARS + WARMUP_BARS > bars.height:
            print(f"\n--- {label}: SKIP (beyond data) ---")
            continue
        lo = start_bar - WARMUP_BARS - HISTORY
        if lo < 0:
            lo = 0
        w = bars.slice(lo, WINDOW_BARS + WARMUP_BARS + HISTORY)
        ev = w.slice(HISTORY, WINDOW_BARS + WARMUP_BARS)
        opens = ev["open"].to_numpy()[WARMUP_BARS:]
        closes = ev["close"].to_numpy()[WARMUP_BARS:]
        n = len(opens)

        # --- real regimes, PIT-safe ---
        import pandas as pd

        prices = pd.Series(w["close"].to_numpy())
        volume = pd.Series(w["volume"].to_numpy())
        det = HybridRegimeDetector()
        det.initialize(prices.iloc[: WARMUP_BARS + HISTORY], volume.iloc[: WARMUP_BARS + HISTORY])
        states = det.detect_all(prices, volume, training_cutoff=WARMUP_BARS + HISTORY)
        keys = [regime_key(s.regime) for s in states][WARMUP_BARS + HISTORY:]
        keys = keys[:n]

        covered = sorted({k for k in set(keys) if k in bindings})
        if not covered:
            print(f"\n--- {label}: SKIP (no promoted policy for regimes {set(keys)}) ---")
            continue

        # --- signals per covered regime ---
        def sig_for(sid: str) -> np.ndarray:
            strat = strat_cache.get(sid)
            out = np.zeros(n, dtype=np.int64)
            if strat is None:
                return out
            ts = ev["timestamp"].to_list()
            o, h, lo_ = ev["open"].to_numpy(), ev["high"].to_numpy(), ev["low"].to_numpy()
            c, v = ev["close"].to_numpy(), ev["volume"].to_numpy()
            for i in range(WARMUP_BARS, len(ts)):
                s0 = max(i - HISTORY, 0)
                hist = ev.slice(s0, i - s0 + 1)
                obs = MarketObservation(
                    symbol=SYMBOL, observed_at=ts[i], open=o[i], high=h[i],
                    low=lo_[i], close=c[i], volume=v[i],
                    features={OHLCV_WINDOW_FEATURE: hist},
                )
                try:
                    fc = strat.forecast(obs)
                except Exception:
                    continue
                e = float(getattr(fc, "expected_excess_return", 0.0) or 0.0)
                out[i - WARMUP_BARS] = 1 if e > EDGE_DEADBAND else (-1 if e < -EDGE_DEADBAND else 0)
            return out

        sigs = {r: sig_for(bindings[r]) for r in covered}
        non_empty = {r: s for r, s in sigs.items() if np.any(s != 0)}
        if not non_empty:
            print(f"\n--- {label}: SKIP (all promoted signals flat) ---")
            continue

        # --- real router over promoted policies ---
        w_start, w_end = ev["timestamp"][WARMUP_BARS], ev["timestamp"][-1]
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            pol_reg = SelectionPolicyRegistry(td / "p")
            created = w_start - timedelta(days=1)
            for r in covered:
                p = bindings_policies[r]
                inc = p["incumbent"]
                pol_reg.add(SelectionPolicyArtifact(
                    symbol=SYMBOL, timeframe=TIMEFRAME, regime=r,
                    incumbent=ParamArtifact(
                        strategy_id=inc["strategy_id"],
                        params=inc.get("params") or {},
                        code_sha=inc.get("code_sha", "0" * 64),
                    ),
                    scores=p.get("scores") or {},
                    evidence_ids=tuple(p.get("evidence_ids") or ("sha256:promoted",)),
                    validity_start=w_start - timedelta(hours=1),
                    validity_end=w_end + timedelta(days=1),
                    risk_cap=float(p.get("risk_cap", 1.0)),
                    status=PolicyStatus.VALIDATED, created_at=created,
                    policy_commit_sha="f" * 40, policy_data_manifest_sha="d" * 64,
                    policy_feature_manifest_sha="e" * 64,
                    policy_release_digest="sha256:" + "c" * 64,
                    promotion_stage="paper_eligible",
                ))
            svc = PolicyActivationService(
                pol_reg, signing_key=key_bytes, key_id="sweep",
                audit_path=td / "act.jsonl",
            )
            for i, art in enumerate(pol_reg.list_all()):
                svc.activate(art.policy_id, actor="sweep", ticket=f"W{i}",
                             now=created + timedelta(minutes=i + 1))
            router = AdaptiveStrategyRouter(
                pol_reg, verification_key=key_bytes, key_id="sweep",
                environment=Environment.RESEARCH,
                state_store=RouterStateStore(td / "rs"), audit_path=td / "au.jsonl",
                config=AdaptiveRouterConfig(
                    min_policy_coverage=0.60, entropy_threshold=0.60,
                    cooldown_bars=3, max_policy_age_days=365_000, min_dwell_bars=5,
                ),
            )
            counts = {k: keys.count(k) for k in set(keys)}
            total = max(sum(counts.values()), 1)
            reasons: dict[str, int] = defaultdict(int)
            chosen: list[str | None] = []
            for i in range(n):
                k = keys[i]
                if k in bindings:
                    share = counts.get(k, 1) / total
                    base = [share] + [(1 - share) / (len(REGIME_ORDER) - 1)] * (len(REGIME_ORDER) - 1)
                    idx = REGIME_ORDER.index(k)
                    probs = base[idx:] + base[:idx]
                else:
                    probs = [1.0 / len(REGIME_ORDER)] * len(REGIME_ORDER)
                obs_t = w_start + timedelta(hours=i)
                d = router.route(
                    symbol=SYMBOL, timeframe=TIMEFRAME,
                    posterior=RegimePosterior(
                        *probs[:5], model_id="hybrid",
                        fitted_start=w_start - timedelta(days=30),
                        fitted_end=obs_t, generated_at=obs_t, ood_score=0.05,
                    ),
                    observed_at=obs_t, position_is_flat=True,
                    position_owner_strategy_id=None, market_context=None,
                )
                chosen.append(d.chosen_strategy_id)
                reasons[d.reason] += 1

        sid_to_regime = {bindings[r]: r for r in covered}
        adaptive = np.zeros(n, dtype=np.int64)
        for i, sid in enumerate(chosen):
            if sid and sid in sid_to_regime:
                adaptive[i] = sigs[sid_to_regime[sid]][i]
        used = {s for s in chosen if s}
        inc_regime = max(covered, key=lambda r: sum(1 for s in chosen if s == bindings[r]))
        incumbent = sigs[inc_regime]

        o_ad = oracle(adaptive, opens.copy())
        o_in = oracle(incumbent, opens.copy())
        bh = (closes[-1] / opens[0] - 1) * 100
        # A window only counts as evidence if the router actually traded.
        # A flat 0.00% adaptive leg means the router abstained (high entropy or
        # no policy coverage); comparing that against a negative buy&hold would
        # manufacture a fake "edge" out of a non-result.
        router_traded = o_ad["total_trades"] > 0 and o_ad["exposure"] > 0
        if router_traded:
            edge = max(o_ad["total_return_pct"], o_in["total_return_pct"]) - bh
            beats = edge > 0
        else:
            edge = None
            beats = False

        rows.append({
            "label": label, "note": note,
            "start": str(w_start), "end": str(w_end), "bars": n,
            "regimes": dict(sorted(counts.items())),
            "strategies_used": sorted(used),
            "top_reason": max(reasons.items(), key=lambda kv: kv[1])[0],
            "router_traded": router_traded,
            "adaptive": o_ad, "incumbent": o_in, "buy_hold_pct": round(bh, 4),
            "edge_vs_hold_pct": round(edge, 4) if edge is not None else None,
            "beats_hold": beats,
        })

        print(f"\n--- {label} ({w_start.date()} -> {w_end.date()}) ---")
        print(f"  regimes  : {counts} | used: {sorted(used)} | top: {rows[-1]['top_reason']}")
        print(f"  adaptive : {o_ad['total_return_pct']:+7.2f}%  sharpe={o_ad['sharpe_ratio']:>6.3f}  trades={o_ad['total_trades']}")
        print(f"  incumbent: {o_in['total_return_pct']:+7.2f}%  sharpe={o_in['sharpe_ratio']:>6.3f}  trades={o_in['total_trades']}")
        if router_traded:
            print(f"  buy&hold : {bh:+7.2f}%   -> edge {edge:+.2f}pp  {'BEATS' if beats else 'loses'}")
        else:
            print(f"  buy&hold : {bh:+7.2f}%   -> NOT MEASURED: router abstained "
                  f"({rows[-1]['top_reason']})")

    winners = [r for r in rows if r["beats_hold"]]
    abstained = [r for r in rows if not r["router_traded"]]
    traded = [r for r in rows if r["router_traded"]]
    if winners:
        verdict = "EDGE_FOUND"
    elif traded:
        verdict = "NO_EDGE_IN_ANY_TRADED_WINDOW"
    else:
        verdict = "ROUTER_NEVER_TRADED"

    print(f"\n{'=' * 70}")
    print(f"windows swept       : {len(rows)}")
    print(f"router actually traded: {len(traded)}  {[r['label'] for r in traded]}")
    print(f"router abstained    : {len(abstained)}  "
          f"{[r['label'] for r in abstained]}")
    print(f"windows beating B&H : {len(winners)}  {[r['label'] for r in winners]}")
    for r in winners:
        print(f"   {r['label']}: +{r['edge_vs_hold_pct']:.2f}pp")
    print(f"VERDICT: {verdict}")
    if verdict == "NO_EDGE_IN_ANY_TRADED_WINDOW":
        print("  -> where the router did trade, no window beat buy & hold.")
    elif verdict == "ROUTER_NEVER_TRADED":
        print("  -> the router abstained on every window, so no edge can be")
        print("     measured either way. The gate, not the strategy, is binding.")
    print("=" * 70)

    json.dump(
        {"ac_id": "AC14-WINDOWS", "bindings": bindings, "store_count": len(census),
         "windows": rows, "winners": [r["label"] for r in winners],
         "abstained": [r["label"] for r in abstained],
         "traded": [r["label"] for r in traded],
         "verdict": verdict},
        open(OUT_PATH, "w"), indent=2, default=str,
    )
    print(f"evidence: {OUT_PATH}")


if __name__ == "__main__":
    main()
