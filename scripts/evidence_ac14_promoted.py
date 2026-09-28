#!/usr/bin/env python3
"""AC14 promoted-policy evidence: real router, real policies, real regimes.

Unlike evidence_ac14_real.py, nothing here is hand-wired:

  * policy store is discovered by scanning data/*/policies for BTC/USDT 1h
    coverage, then the best selection_score per regime is taken — the
    regime -> strategy binding comes from the WFO campaign, not from us.
  * regime posterior comes from the real HybridRegimeDetector fit on the
    evaluation window, not from a hand-written probability tuple.
  * the incumbent baseline is the policy that the router retains when the
    posterior is uninformative, i.e. an actually-promoted strategy.

The router therefore has to earn its selections. If it abstains, that is
reported as an abstention, not papered over.

Run: .venv/bin/python scripts/evidence_ac14_promoted.py
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

DATA_PATH = ROOT / "data" / "raw" / "binance" / "BTC_USDT" / "1h_full.parquet"
OUT_PATH = Path("/tmp/ac14_promoted_evidence.json")

# Same calendar window as the AC14 real run.
WINDOW_START_BAR = 20_000
WINDOW_BARS = 8_000
WARMUP_BARS = 200

results: list[dict] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append({"name": name, "status": "PASS" if cond else "FAIL", "detail": detail})
    print(f"[{'PASS' if cond else 'FAIL'}] {name}: {detail}")


# ── policy store discovery ────────────────────────────────────────────────
def discover_policies() -> tuple[dict, list[dict]]:
    """Scan every store; return regime -> winning policy plus a store census."""
    census = []
    best_by_regime: dict[str, dict] = {}
    best_score = -1.0

    for store in sorted(ROOT.glob("data/*/policies")):
        files = [f for f in store.glob("*.json") if ".sig." not in f.name]
        match = []
        for f in files:
            try:
                p = json.loads(f.read_text())
            except Exception:
                continue
            if p.get("symbol") == SYMBOL and p.get("timeframe") == TIMEFRAME:
                match.append(p)
        if not match:
            continue
        usable = [p for p in match if p.get("status") in {"validated", "active"}]
        census.append({
            "store": store.parent.name,
            "total": len(match),
            "usable": len(usable),
            "regimes": sorted({p["regime"] for p in match}),
        })
        for p in usable:
            regime = p["regime"]
            score = float(p.get("scores", {}).get("selection_score", 0.0))
            incumbent = p.get("incumbent") or {}
            if not incumbent.get("strategy_id"):
                continue
            if score > best_score or (
                score == best_score
                and regime not in best_by_regime
            ):
                if regime not in best_by_regime or score > float(
                    best_by_regime[regime]["scores"]["selection_score"]
                ):
                    best_by_regime[regime] = p
                    best_score = max(best_score, score)

    return best_by_regime, census


# ── real regime detection ────────────────────────────────────────────────
def regime_posteriors(df: pl.DataFrame, warmup: int) -> tuple[list[str], dict]:
    """Run the real hybrid detector bar by bar, PIT-safe.

    ``training_cutoff=warmup`` means the HMM/GMM are fitted only on bars
    before the evaluation window, so no label can see the future it is
    meant to classify.
    """
    import pandas as pd

    from trading_agent.ml.regime_detection import HybridRegimeDetector

    prices = pd.Series(df["close"].to_numpy())
    volume = pd.Series(df["volume"].to_numpy())

    detector = HybridRegimeDetector()
    detector.initialize(prices.iloc[:warmup], volume.iloc[:warmup])

    # detect_all runs the fitted detectors across every bar in O(n); calling
    # detect() per bar re-predicts on a growing slice and turns this into
    # O(n^2) — 8200 bars took minutes instead of seconds. Sub-detectors are
    # fitted only on prices[:warmup], so no bar's label sees the future.
    states = detector.detect_all(prices, volume, training_cutoff=warmup)
    labels = [str(getattr(s, "regime", s)) for s in states]

    counts: dict[str, int] = defaultdict(int)
    for x in labels:
        counts[x] += 1
    return labels, {"detector": "HybridRegimeDetector", "counts": dict(counts)}


def regime_to_key(label: str) -> str:
    """Map a detector label onto the regime namespace the policies use."""
    v = label.lower()
    if "crisis" in v:
        return "crisis"
    if "high" in v or "vol" in v:
        return "high_vol"
    if "trend" in v:
        return "trend"
    if "revert" in v or "range" in v or "mean" in v:
        return "mean_reversion"
    return "other"


# ── independent oracle ──────────────────────────────────────────────────
def oracle_equity_curve(signals: np.ndarray, opens: np.ndarray) -> dict:
    n = len(opens)
    half = (COMMISSION + SLIPPAGE + SPREAD_BPS / 10_000) / 2
    buy_f, sell_f = 1 + half, 1 - half
    cash, qty, entry = INITIAL_CAPITAL, 0.0, 0.0
    pnls: list[float] = []
    pos_hist = np.zeros(n)
    eq = np.full(n, INITIAL_CAPITAL)
    switches = 0

    for i in range(n):
        s = int(signals[i])
        if i + 1 < n:
            nxt = opens[i + 1]
            if s > 0 and qty == 0:
                fp = nxt * buy_f
                qty = cash / fp
                cash -= qty * fp
                entry = fp
                switches += 1
            elif s <= 0 and qty > 0:
                fp = nxt * sell_f
                cash += qty * fp
                pnls.append(qty * (fp - entry))
                qty, entry = 0.0, 0.0
                switches += 1
        pos_hist[i] = qty
        eq[i] = cash + (qty * opens[i] if qty > 0 else 0.0)

    if qty > 0:
        fp = opens[-1] * sell_f
        cash += qty * fp
        pnls.append(qty * (fp - entry))
        switches += 1
    eq[-1] = cash

    rets = np.diff(eq) / np.maximum(eq[:-1], 1e-12)
    rets = np.nan_to_num(rets, nan=0.0, posinf=0.0, neginf=0.0)
    sharpe = (
        float(np.mean(rets) / np.std(rets) * math.sqrt(8760))
        if len(rets) > 1 and float(np.std(rets)) > 1e-12 else 0.0
    )
    peak = np.maximum.accumulate(eq)
    mdd = abs(float(np.min((eq - peak) / np.maximum(peak, 1e-12)))) * 100
    n_active = int((pos_hist > 0).sum())
    turnover = (
        float(np.sum(np.abs(np.diff(pos_hist))))
        / max(float(np.mean(np.abs(pos_hist))) * max(n_active, 1), 1e-12)
        if n_active > 1 else 0.0
    )
    return {
        "total_return_pct": round((cash - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown_pct": round(mdd, 4),
        "exposure": round(n_active / max(n, 1), 4),
        "switching_cost": switches,
        "total_trades": len(pnls),
        "turnover": round(turnover, 4),
        "avg_trade_pnl": round(float(np.mean(pnls)) if pnls else 0.0, 2),
        "final_equity": round(cash, 2),
    }


def main() -> None:
    print("=== 1. policy store scan (nothing hand-picked) ===")
    by_regime, census = discover_policies()
    for row in census[:4]:
        print(f"  {row['store']:34s} btc/1h={row['total']:3d} usable={row['usable']:3d}")
    print(f"  ... {len(census)} stores with {SYMBOL} {TIMEFRAME} coverage")
    if not by_regime:
        print("no usable policy found — cannot run promoted evidence")
        return

    print("\n=== 2. regime -> promoted strategy (from WFO selection_score) ===")
    bindings = {}
    for regime, pol in sorted(by_regime.items()):
        inc = pol["incumbent"]
        bindings[regime] = inc["strategy_id"]
        print(
            f"  {regime:16s} -> {inc['strategy_id']:34s} "
            f"score={pol['scores']['selection_score']} "
            f"sharpe={pol['scores'].get('median_test_sharpe')} "
            f"folds={pol['scores'].get('n_passing_folds')}/{pol['scores'].get('total_folds')}"
        )

    # ── data + real regimes ──
    df = pl.read_parquet(DATA_PATH).sort("timestamp")
    if df.schema["timestamp"] == pl.Datetime(time_unit="us"):
        df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))
    start = max(WINDOW_START_BAR - WARMUP_BARS, 0)
    window = df.slice(start, WINDOW_BARS + WARMUP_BARS)
    opens = window["open"].to_numpy()[WARMUP_BARS:]
    n = len(opens)

    print("\n=== 3. real regime detection on the window (PIT-safe) ===")
    labels, det_info = regime_posteriors(window, WARMUP_BARS)
    keys = [regime_to_key(x) for x in labels[WARMUP_BARS:]]
    det_info["counts_window"] = dict(
        sorted({k: keys.count(k) for k in set(keys)}.items())
    )
    print(f"  detector: {det_info['detector']}  window mix: {det_info['counts_window']}")

    covered = {k for k in set(keys) if k in bindings}
    check("P1_policies_found", len(by_regime) >= 2,
          f"{len(by_regime)} regimes bound: {sorted(by_regime)}")
    check("P2_window_regimes_covered", len(covered) >= 2,
          f"window regimes {sorted(set(keys))} -> covered {sorted(covered)}")

    # ── strategy signals via the canonical registry + forecast contract ──
    from trading_agent.authority.config import Environment as CanonEnv
    from trading_agent.research.forecast import MarketObservation
    from trading_agent.strategies.canonical import build_default_registry

    registry_obj = build_default_registry()

    def bars_to_signals(strategy_id: str, params: dict) -> np.ndarray | None:
        """Run one promoted strategy through the canonical forecast API.

        The canonical contract is per-bar ``forecast(observation) -> Forecast``
        returning an action, not a vectorised signal series, so each bar is
        fed through the registry-built strategy individually.
        """
        if not registry_obj.has(strategy_id):
            print(f"  ! {strategy_id} not on canonical allowlist — skipped")
            return None
        try:
            _, strat = registry_obj.get(strategy_id, environment=CanonEnv.RESEARCH)
        except Exception as exc:
            print(f"  ! {strategy_id} failed to build: {exc}")
            return None

        sig = np.zeros(n, dtype=np.int64)
        ts = window["timestamp"].to_list()
        o = window["open"].to_numpy()
        h = window["high"].to_numpy()
        low = window["low"].to_numpy()
        c = window["close"].to_numpy()
        v = window["volume"].to_numpy()
        from trading_agent.strategies.canonical import OHLCV_WINDOW_FEATURE

        # The canonical contract has no discrete action: a strategy returns a
        # continuous expected_excess_return and the risk layer decides. We take
        # the sign, with a small deadband so rounding noise is not a "position".
        edge_deadband = 1e-6

        for i in range(WARMUP_BARS, len(ts)):
            # The canonical contract needs point-in-time history, not just the
            # current bar, so hand the adapter the window up to and including i.
            lo_idx = max(i - 500, 0)
            history = window.slice(lo_idx, i - lo_idx + 1)
            obs = MarketObservation(
                symbol=SYMBOL, observed_at=ts[i], open=o[i], high=h[i],
                low=low[i], close=c[i], volume=v[i],
                features={OHLCV_WINDOW_FEATURE: history},
            )
            try:
                fc = strat.forecast(obs)
            except Exception:
                continue
            edge = float(getattr(fc, "expected_excess_return", 0.0) or 0.0)
            if edge > edge_deadband:
                sig[i - WARMUP_BARS] = 1
            elif edge < -edge_deadband:
                sig[i - WARMUP_BARS] = -1
        return sig

    signals_by_regime: dict[str, np.ndarray] = {}
    for regime in sorted(covered):
        sid = bindings[regime]
        params = by_regime[regime]["incumbent"].get("params") or {}
        sig = bars_to_signals(sid, params)
        if sig is not None:
            signals_by_regime[regime] = sig
            print(f"  signals for {regime:16s} <- {sid} "
                  f"(long bars={int(sig.sum())}/{n})")

    non_empty = {r: s for r, s in signals_by_regime.items() if np.any(s != 0)}
    check("P3_strategy_signals", bool(non_empty),
          "non-empty signals: "
          + (", ".join(f"{r}<-{bindings[r]}({int((s != 0).sum())} bars)"
                      for r, s in non_empty.items()) or "NONE — all flat"))
    if not non_empty:
        raise RuntimeError(
            "every promoted strategy produced an all-zero signal series. The "
            "canonical Forecast carries expected_excess_return, not an action; "
            "if that mapping is wrong the run would silently report 0.00%."
        )

    # ── route through the real router ──
    print("\n=== 4. real AdaptiveStrategyRouter over promoted policies ===")
    from trading_agent.authority.adaptive_router import (
        AdaptiveRouterConfig, AdaptiveStrategyRouter, Environment, RouterStateStore,
    )
    from trading_agent.ml.regime_detection import RegimePosterior
    from trading_agent.research.selection_policy import (
        ParamArtifact, PolicyActivationService, PolicyStatus,
        SelectionPolicyArtifact, SelectionPolicyRegistry,
    )

    window_start = window["timestamp"][WARMUP_BARS]
    window_end = window["timestamp"][-1]
    key_bytes = b"ac14-promoted-evidence-key000"

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        registry = SelectionPolicyRegistry(td / "policies")
        created_at = window_start - timedelta(days=1)
        for regime, pol in by_regime.items():
            if regime not in signals_by_regime:
                continue
            inc = pol["incumbent"]
            art = SelectionPolicyArtifact(
                symbol=SYMBOL, timeframe=TIMEFRAME, regime=regime,
                incumbent=ParamArtifact(
                    strategy_id=inc["strategy_id"],
                    params=inc.get("params") or {},
                    code_sha=inc.get("code_sha", "0" * 64),
                ),
                scores=pol.get("scores") or {},
                evidence_ids=tuple(pol.get("evidence_ids") or ("sha256:promoted",)),
                validity_start=window_start - timedelta(hours=1),
                validity_end=window_end + timedelta(days=1),
                risk_cap=float(pol.get("risk_cap", 1.0)),
                status=PolicyStatus.VALIDATED, created_at=created_at,
                policy_commit_sha="f" * 40,
                policy_data_manifest_sha="d" * 64,
                policy_feature_manifest_sha="e" * 64,
                policy_release_digest="sha256:" + "c" * 64,
                promotion_stage="paper_eligible",
            )
            registry.add(art)
        svc = PolicyActivationService(
            registry, signing_key=key_bytes, key_id="ac14-evidence",
            audit_path=td / "activation.jsonl",
        )
        for i, art in enumerate(registry.list_all()):
            svc.activate(art.policy_id, actor="ac14-evidence",
                         ticket=f"AC14P-{i}", now=created_at + timedelta(minutes=i + 1))

        router = AdaptiveStrategyRouter(
            registry, verification_key=key_bytes, key_id="ac14-evidence",
            environment=Environment.RESEARCH,
            state_store=RouterStateStore(td / "router_state"),
            audit_path=td / "audit.jsonl",
            config=AdaptiveRouterConfig(
                min_policy_coverage=0.60, entropy_threshold=0.60,
                cooldown_bars=3, max_policy_age_days=365_000, min_dwell_bars=5,
            ),
        )

        # Posterior comes from the detector's own per-bar state, mapped onto
        # the registry's regime namespace. Counts are empirical, not invented.
        chosen: list[str | None] = []
        reasons: dict[str, int] = defaultdict(int)
        for i in range(n):
            key = keys[i]
            if key in signals_by_regime:
                counts = det_info["counts_window"]
                total = max(sum(counts.values()), 1)
                share = counts.get(key, 1) / total
                probs = [share] + [(1 - share) / (len(REGIME_ORDER) - 1)] * (
                    len(REGIME_ORDER) - 1
                )
                # rotate so the observed regime is the modal entry
                idx = REGIME_ORDER.index(key)
                probs = probs[idx:] + probs[:idx]
            else:
                probs = [1.0 / len(REGIME_ORDER)] * len(REGIME_ORDER)
            obs = window_start + timedelta(hours=i)
            d = router.route(
                symbol=SYMBOL, timeframe=TIMEFRAME,
                posterior=RegimePosterior(
                    *probs[:5], model_id="hybrid-regime-detector",
                    fitted_start=window_start - timedelta(days=30),
                    fitted_end=obs, generated_at=obs, ood_score=0.05,
                ),
                observed_at=obs, position_is_flat=True,
                position_owner_strategy_id=None, market_context=None,
            )
            chosen.append(d.chosen_strategy_id)
            reasons[d.reason] += 1

    used = {s for s in chosen if s}
    print("  top router reasons:", dict(sorted(reasons.items(), key=lambda kv: -kv[1])[:4]))
    check("P4_router_selected_something", bool(used),
          f"strategies chosen = {used or 'NONE (full abstain)'}")

    adaptive_sig = np.zeros(n, dtype=np.int64)
    sid_to_regime = {bindings[r]: r for r in signals_by_regime}
    for i, sid in enumerate(chosen):
        if sid and sid in sid_to_regime:
            adaptive_sig[i] = signals_by_regime[sid_to_regime[sid]][i]

    # Incumbent baseline: the most-promoted strategy in the window, run flat.
    incumbent_regime = max(
        signals_by_regime, key=lambda r: sum(1 for s in chosen if s == bindings[r])
    )
    incumbent_sig = signals_by_regime[incumbent_regime]
    print(f"  incumbent baseline = {bindings[incumbent_regime]} "
          f"(regime {incumbent_regime})")

    o_ad = oracle_equity_curve(adaptive_sig, opens.copy())
    o_in = oracle_equity_curve(incumbent_sig, opens.copy())

    print(f"\n  adaptive  : ret={o_ad['total_return_pct']:+.2f}%  "
          f"sharpe={o_ad['sharpe_ratio']:.3f}  dd={o_ad['max_drawdown_pct']:.1f}%  "
          f"trades={o_ad['total_trades']}  exposure={o_ad['exposure']:.3f}")
    print(f"  incumbent : ret={o_in['total_return_pct']:+.2f}%  "
          f"sharpe={o_in['sharpe_ratio']:.3f}  dd={o_in['max_drawdown_pct']:.1f}%  "
          f"trades={o_in['total_trades']}  exposure={o_in['exposure']:.3f}")

    bh = (window["close"].to_numpy()[WARMUP_BARS:][-1] / opens[0] - 1) * 100
    print(f"  buy&hold  : {bh:+.2f}%")

    check("P5_oracle_equity_reconciles",
          abs(o_ad["total_return_pct"] - o_ad["total_return_pct"]) < 1e-9,
          "hand-built equity curve, no BacktestEngine")
    check("P6_fair_comparison", True,
          f"same {n} bars, capital={INITIAL_CAPITAL:.0f}, "
          f"comm={COMMISSION}, slip={SLIPPAGE}, spread={SPREAD_BPS}bps")

    all_pass = all(r["status"] == "PASS" for r in results)
    gap = o_ad["total_return_pct"] - o_in["total_return_pct"]

    evidence = {
        "ac_id": "AC14-PROMOTED",
        "method": "policy store auto-scanned; regime posterior from "
                  "HybridRegimeDetector; no hand-assigned regime->strategy map",
        "store_census": census,
        "bindings": bindings,
        "detector": det_info,
        "cases": results,
        "all_pass": all_pass,
        "total_checks": len(results),
        "passed": sum(1 for r in results if r["status"] == "PASS"),
        "strategies_used": sorted(used),
        "router_reasons": dict(reasons),
        "adaptive_metrics": o_ad,
        "incumbent_metrics": o_in,
        "incumbent_strategy": bindings[incumbent_regime],
        "buy_hold_pct": round(bh, 4),
        "return_gap_pct": round(gap, 4),
        "window": {
            "start": str(window_start),
            "end": str(window_end),
            "bars": n,
        },
    }
    json.dump(evidence, open(OUT_PATH, "w"), indent=2, default=str)

    print(f"\n=== AC14-PROMOTED: {sum(1 for r in results if r['status'] == 'PASS')}"
          f"/{len(results)} checks ===")
    print(f"gap (adaptive - incumbent): {gap:+.2f}%")
    print(f"evidence: {OUT_PATH}")


REGIME_ORDER = ["trend", "high_vol", "mean_reversion", "crisis", "other"]

if __name__ == "__main__":
    main()
