#!/usr/bin/env python3
"""Evaluate L2 eligibility and ranking, and emit a D01 fragment.

docs/operations/L2_SELECTION_RULE.md freezes the order campaigns run in,
so that the subject is decided by a rule rather than by whoever reaches L2
first with a preference. The two measured candidates would rank nothing on
merit — 5 trades across 23 folds and a 0.50 payoff ratio — which is exactly
the situation where an unfrozen rule gets quietly overridden.

Campaign outcomes are deliberately not accepted as input. Eligibility is
computed from the repository: registration, signal emission, warmup versus
window, fold count, trading-window share, and data provenance. Ranking uses
those same measurements, descending by how much evidence a campaign could
produce.

Run: .venv/bin/python scripts/select_l2_candidate.py --symbol BTC/USDT --timeframe 1d
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import polars as pl

# Gate parameters, referenced from GATE_SEMANTICS_AND_RERANK.md. They are
# unresolved there; changing them here changes eligibility, so both values
# are surfaced in the output rather than buried.
GATE_N_MIN = 14
TRADING_WINDOW_FLOOR = 0.50


@dataclass
class Candidate:
    strategy_id: str
    registered: bool = False
    resolvable: bool = False
    emits_signal: bool = False
    warmup: int = 0
    window_bars: int = 0
    fold_count: int = 0
    trading_window_share: float = 0.0
    median_trades_per_window: float = 0.0
    input_path: str = ""
    input_sha256: str = ""
    reasons: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return not self.reasons

    def rank_key(self) -> tuple:
        """Higher is better; negation is applied for ascending keys."""
        return (
            self.fold_count,
            self.trading_window_share,
            self.median_trades_per_window,
            -self.warmup,
        )


def _binomial_tail(k: int, n: int) -> float:
    if n <= 0:
        return 1.0
    k = max(0, min(k, n))
    return sum(math.comb(n, i) for i in range(k, n + 1)) / (2 ** n)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def evaluate(strategy_id: str, data: pl.DataFrame, folds) -> Candidate:
    from trading_agent.strategies.canonical import build_default_registry
    from trading_agent.backtest.tournament import _research_env

    cand = Candidate(strategy_id=strategy_id)

    registry = build_default_registry()
    cand.registered = registry.has(strategy_id)
    if not cand.registered:
        cand.reasons.append("E1 not on the canonical allowlist")
        return cand
    descriptor = registry.describe(strategy_id)
    cand.warmup = int(getattr(descriptor, "warmup_bars", 0) or 0)

    try:
        registry.get(strategy_id, environment=_research_env())
        cand.resolvable = True
    except Exception as exc:  # pragma: no cover - registry dependent
        cand.reasons.append(f"E2 cannot resolve adapter: {type(exc).__name__}")

    if folds:
        cand.window_bars = folds[0].outer_test_end - folds[0].outer_test_start
        cand.fold_count = len(folds)

        # Signal emission and trading-window share, measured over each fold's
        # own test window rather than the full history.
        entries: set[int] = set()
        if cand.resolvable:
            try:

                cls = _resolve_legacy(strategy_id)
                if cls is not None:
                    strat = cls()
                    sig = strat.generate_signals(
                        strat.compute_indicators(data)
                    ).to_numpy()
                    entries = {
                        i for i in range(1, len(sig))
                        if sig[i] > 0 and sig[i - 1] <= 0
                    }
            except Exception:
                entries = set()

        if entries:
            cand.emits_signal = True
            per_window = [
                sum(1 for e in entries if f.outer_test_start <= e < f.outer_test_end)
                for f in folds
            ]
            trading = [c for c in per_window if c > 0]
            cand.trading_window_share = len(trading) / len(per_window) if per_window else 0.0
            cand.median_trades_per_window = (
                sorted(trading)[len(trading) // 2] if trading else 0.0
            )
    return cand


def _resolve_legacy(strategy_id: str):
    """Locate the legacy Strategy class a canonical id wraps."""
    from trading_agent.strategies.canonical import candidates as C

    for name in dir(C):
        if not name.endswith("_DESCRIPTOR"):
            continue
        descriptor = getattr(C, name)
        if getattr(descriptor, "strategy_id", None) != strategy_id:
            continue
        return _legacy_for(descriptor)
    return None


_LEGACY_MAP = {
    "ma_crossover": ("ma_crossover", "MaCrossover"),
    "enhanced_ma": ("enhanced_ma", "EnhancedMaCrossover"),
    "ma_adx": ("enhanced_ma", "MaAdxCrossover"),
    "ma_vol_target": ("enhanced_ma", "MaVolTargetCrossover"),
    "ensemble_ma_adx": ("enhanced_ma", "EnsembleMaAdx"),
    "rsi": ("rsi", "RsiStrategy"),
    "bbands": ("bbands", "BBandsStrategy"),
    "volatility_breakout": ("volatility_breakout", "VolatilityBreakoutStrategy"),
    "trend_pullback": ("trend_pullback", "TrendPullbackStrategy"),
    "range_mean_reversion": (
        "range_mean_reversion", "RangeMeanReversionStrategy",
    ),
    "regime_switching": ("regime_switching", "RegimeSwitchingStrategy"),
    "stat_arbitrage_lo": ("stat_arbitrage", "StatArbitrageLongOnlyStrategy"),
    "stat_arbitrage_ls": (
        "stat_arbitrage", "StatArbitrageLongShortStrategy",
    ),
    "cross_sectional_momentum_lo": (
        "cross_sectional_momentum", "CrossSectionalMomentumLongOnlyStrategy",
    ),
    "cross_sectional_momentum_ls": (
        "cross_sectional_momentum", "CrossSectionalMomentumLongShortStrategy",
    ),
    "ma_adx_regime": ("enhanced_ma", "MaAdxRegimeAware"),
    "funding_carry": ("funding_carry", "FundingCarryStrategy"),
}


def _legacy_for(descriptor):
    import importlib

    entry = _LEGACY_MAP.get(getattr(descriptor, "strategy_id", ""))
    if not entry:
        return None
    module, cls_name = entry
    try:
        module_obj = importlib.import_module(f"trading_agent.strategies.{module}")
    except ImportError:
        return None
    return getattr(module_obj, cls_name, None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC_USDT")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--train-months", type=int, default=12)
    ap.add_argument("--val-months", type=int, default=2)
    ap.add_argument("--test-months", type=int, default=2)
    ap.add_argument("--step-months", type=int, default=2)
    ap.add_argument("--strategies", nargs="*", default=None)
    args = ap.parse_args()

    from trading_agent.backtest.nested_wfo import _get_fold_indices
    from trading_agent.strategies.canonical import build_default_registry

    market = args.symbol.replace("_", "/")
    # Resolve through the canonical registry rather than building a path by
    # hand: three hourly files exist per symbol with different coverage, and a
    # hand-built path picks whichever the caller typed. B07 in
    # LIVE_READINESS_AGENT_PLAN.md.
    from trading_agent.data.canonical import resolve_canonical

    resolution = resolve_canonical(ROOT, args.symbol, args.timeframe)
    if resolution is None:
        print(f"no data for {args.symbol} {args.timeframe}")
        return 2
    path = resolution.path
    if not resolution.preferred:
        print(f"note: {args.symbol} {args.timeframe} has no extended file; "
              f"canonical resolution is {path.name}")
    data = pl.read_parquet(path)
    folds = _get_fold_indices(
        data.height, args.timeframe, args.train_months, args.val_months,
        args.test_months, args.step_months, 25, 25,
    )

    ids = args.strategies or sorted(build_default_registry().list_ids())
    rows = []
    for sid in ids:
        cand = evaluate(sid, data, folds)
        if not cand.registered:
            continue
        # E4
        if cand.window_bars and cand.window_bars < cand.warmup:
            cand.reasons.append(
                f"E4 test window {cand.window_bars} bars < warmup {cand.warmup}"
            )
        # E3
        if not cand.emits_signal:
            cand.reasons.append("E3 emits no signal on the target data")
        # E5
        if cand.fold_count < GATE_N_MIN:
            cand.reasons.append(
                f"E5 {cand.fold_count} folds < gate minimum {GATE_N_MIN}"
            )
        # E6
        if cand.trading_window_share < TRADING_WINDOW_FLOOR:
            cand.reasons.append(
                f"E6 only {cand.trading_window_share * 100:.0f}% of windows "
                f"trade, floor {TRADING_WINDOW_FLOOR * 100:.0f}%"
            )
        # E7
        cand.input_path = str(path.relative_to(ROOT))
        cand.input_sha256 = _sha256(path)
        rows.append(cand)

    print("=" * 78)
    print("L2 CANDIDATE SELECTION — eligibility and ranking, pre-campaign")
    print("=" * 78)
    print(f"  symbol/timeframe : {market} {args.timeframe}")
    print(f"  data             : {cand_path(rows, path, ROOT)}")
    print(f"  fold structure   : {args.train_months}/{args.val_months}/"
          f"{args.test_months}/{args.step_months} months "
          f"-> {len(folds)} folds, {folds[0].outer_test_end - folds[0].outer_test_start} bars/window")
    print(f"  gate parameters  : n_min={GATE_N_MIN}, "
          f"trading_window_floor={TRADING_WINDOW_FLOOR}")
    print()

    header = (f"{'strategy':28s} {'reg':>4} {'sig':>4} {'folds':>6} "
              f"{'trade%':>7} {'medTrd':>7}  eligible")
    print(header)
    print("-" * len(header))

    eligible = []
    for row in sorted(rows, key=lambda r: r.rank_key(), reverse=True):
        status = "YES" if row.eligible else "no"
        print(f"{row.strategy_id:28s} {str(row.registered):>4} "
              f"{str(row.emits_signal):>4} {row.fold_count:>6} "
              f"{row.trading_window_share * 100:>6.0f}% "
              f"{row.median_trades_per_window:>7.0f}  {status}")
        if row.eligible:
            eligible.append(row)

    print()
    print("=" * 78)
    if not eligible:
        print("RESULT: NOT_QUALIFIED")
        print()
        print("No candidate satisfies the eligibility contract. Per the rule this")
        print("blocks L3 and L4 and requires a new experiment rather than a")
        print("threshold change. Reasons per candidate:")
        for row in rows:
            if row.reasons:
                print(f"  {row.strategy_id:28s} {row.reasons[0]}")
    else:
        eligible.sort(key=lambda r: r.rank_key(), reverse=True)
        chosen = eligible[0]
        print(f"RESULT: campaign {chosen.strategy_id} first")
        print(f"  rank key: {chosen.rank_key()}")
        print(f"  data manifest: {chosen.input_path}")
        print(f"  sha256: {chosen.input_sha256[:32]}…")
        if len(eligible) > 1:
            print(f"  next: {[e.strategy_id for e in eligible[1:3]]}")
    print("=" * 78)

    fragment = {
        "gate_parameters": {"n_min": GATE_N_MIN,
                            "trading_window_floor": TRADING_WINDOW_FLOOR},
        "window": {"symbol": market, "timeframe": args.timeframe,
                   "train_months": args.train_months,
                   "val_months": args.val_months,
                   "test_months": args.test_months,
                   "step_months": args.step_months,
                   "folds": len(folds)},
        "data": {"input_path": str(path.relative_to(ROOT)),
                 "sha256": _sha256(path), "rows": data.height},
        "candidates": [
            {**asdict(row), "eligible": row.eligible,
             "rank_key": list(row.rank_key())}
            for row in sorted(rows, key=lambda r: r.rank_key(), reverse=True)
        ],
        "selected": chosen.strategy_id if eligible else "NOT_QUALIFIED",
    }
    out = ROOT / "data" / "l2_selection.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fragment, indent=2, default=str))
    print(f"\nD01 fragment: {out.relative_to(ROOT)}")
    return 0


def cand_path(rows, path, root):
    if rows and rows[0].input_path:
        return rows[0].input_path
    return str(path.relative_to(root))


if __name__ == "__main__":
    raise SystemExit(main())