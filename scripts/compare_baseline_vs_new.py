#!/usr/bin/env python3
"""Compare old 1h multi-pair baseline vs new WFO 4h results."""

import json
import statistics
from pathlib import Path

ROOT = Path(__file__).parent.parent
OLD = ROOT / "data/research_runs/multi_pair_3y_2023-08-17_2026-08-17.json"
NEW = ROOT / "data/wfo_results/wfo_10symbol_4h_20260818_030444.json"


def load_json(p: Path) -> dict:
    return json.loads(p.read_text())


def summarize_old(d: dict) -> dict:
    pf = d.get("portfolio_1x_cost", {})
    ps = d.get("per_symbol_1x_cost", {})
    return {
        "universe": d.get("universe", {}).get("symbols", []),
        "timeframe": d.get("window", {}).get("timeframe"),
        "years": d.get("window", {}).get("years"),
        "total_return_pct": pf.get("total_return_pct"),
        "annualized_sharpe": pf.get("annualized_sharpe"),
        "max_drawdown_pct": pf.get("max_drawdown_pct"),
        "win_rate_pct": pf.get("win_rate_pct"),
        "profit_factor": pf.get("profit_factor"),
        "closed_trades": pf.get("closed_trades"),
        "commission_paid_usd": pf.get("commission_paid_usd"),
        "positive_months": pf.get("positive_months"),
        "negative_months": pf.get("negative_months"),
        "per_symbol": {
            sym: {
                "return_contribution_pct_points": m.get(
                    "return_contribution_pct_points"
                ),
                "sharpe": m.get("sharpe"),
                "max_drawdown_pct": m.get("max_drawdown_pct"),
                "win_rate_pct": m.get("win_rate_pct"),
                "closed_trades": m.get("closed_trades"),
            }
            for sym, m in ps.items()
        },
    }


def summarize_new(d: dict) -> dict:
    results = d.get("results", {})
    all_folds = []
    for sym, strats in results.items():
        for strategy_name, folds in strats.items():
            for fold in folds:
                m = fold.get("oos_metrics", {})
                if m.get("sharpe") is None:
                    continue
                all_folds.append(
                    {
                        "symbol": sym,
                        "strategy": strategy_name,
                        "fold": fold.get("fold"),
                        "sharpe": m.get("sharpe"),
                        "return_pct": m.get("total_return_pct"),
                        "max_dd_pct": m.get("max_drawdown_pct"),
                        "win_rate": m.get("win_rate"),
                        "num_trades": m.get("num_trades"),
                    }
                )
    sharpe_vals = [m["sharpe"] for m in all_folds]
    return_vals = [m["return_pct"] for m in all_folds]
    profitable = [m for m in all_folds if m["return_pct"] > 0]
    return {
        "symbols": d.get("symbols", []),
        "timeframe": d.get("timeframe"),
        "fold_records": len(all_folds),
        "profitable_folds": len(profitable),
        "profitable_pct": len(profitable) / len(all_folds) * 100 if all_folds else 0,
        "sharpe_avg": statistics.mean(sharpe_vals) if sharpe_vals else None,
        "sharpe_median": statistics.median(sharpe_vals) if sharpe_vals else None,
        "return_avg": statistics.mean(return_vals) if return_vals else None,
        "return_median": statistics.median(return_vals) if return_vals else None,
        "best_sharpe": max(sharpe_vals) if sharpe_vals else None,
        "worst_sharpe": min(sharpe_vals) if sharpe_vals else None,
        "best_return": max(return_vals) if return_vals else None,
        "worst_return": min(return_vals) if return_vals else None,
    }


def main() -> None:
    old = summarize_old(load_json(OLD))
    new = summarize_new(load_json(NEW))

    print("=" * 70)
    print("COMPARISON: OLD 1H MULTI-PAIR BASELINE vs NEW 4H WFO")
    print("=" * 70)

    print("\n[OLD] 1h multi-pair 3y")
    print(f"  Universe: {old['universe']}")
    print(f"  Return: {old['total_return_pct']:.2f}%")
    print(f"  Sharpe: {old['annualized_sharpe']:.2f}")
    print(f"  MaxDD: {old['max_drawdown_pct']:.2f}%")
    print(f"  Win rate: {old['win_rate_pct']:.2f}%")
    print(f"  Profit factor: {old['profit_factor']:.2f}")
    print(f"  Trades: {old['closed_trades']}")
    print(f"  Commission: ${old['commission_paid_usd']:.2f}")
    print(f"  Pos/Neg months: {old['positive_months']}/{old['negative_months']}")

    print("\n[NEW] 4h WFO 10 symbols")
    print(f"  Universe: {new['symbols']}")
    print(f"  Fold records: {new['fold_records']}")
    print(
        f"  Profitable folds: {new['profitable_folds']} ({new['profitable_pct']:.1f}%)"
    )
    print(
        f"  Return avg/median: {new['return_avg']:.2f}% / {new['return_median']:.2f}%"
    )
    print(f"  Sharpe avg/median: {new['sharpe_avg']:.2f} / {new['sharpe_median']:.2f}")
    print(f"  Best/Worst Sharpe: {new['best_sharpe']:.2f} / {new['worst_sharpe']:.2f}")
    print(
        f"  Best/Worst Return: {new['best_return']:.2f}% / {new['worst_return']:.2f}%"
    )

    print("\n[ASSESSMENT]")
    if (
        old["annualized_sharpe"] < 0
        and new["sharpe_avg"] is not None
        and new["sharpe_avg"] > old["annualized_sharpe"]
    ):
        print("  ✅ New system improves Sharpe vs old negative baseline")
    else:
        print("  ⚠️ Sharpe improvement unclear")

    if (
        old["total_return_pct"] < 0
        and new["return_avg"] is not None
        and new["return_avg"] > old["total_return_pct"]
    ):
        print("  ✅ New system improves return vs old negative baseline")
    else:
        print("  ⚠️ Return improvement unclear")

    print(
        "\n[NOTE] Direct 1h full-system comparison blocked by backtest execution timeout."
    )
    print(
        "  Root cause: canonical execution pipeline requires market observation + evidence"
    )
    print(
        "  for new exposure; legacy backtest path currently times out on full 1h history."
    )
    print(
        "  Next: either run smaller 1h slice, or fix backtest pipeline to match old baseline runtime."
    )


if __name__ == "__main__":
    main()
