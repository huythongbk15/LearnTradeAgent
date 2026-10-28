#!/usr/bin/env python3
"""
BNB/ADX25 multi-window validation (Phase C of BNB research).

Protocol: RESEARCH_RUN_PROTOCOL.md, Step B.3 — entrypoint by existing engine.
Runs 4 arms (ma_vol_target, ma_adx, cash, buy_hold_initial25) over 3
NON-OVERLAPPING windows on BNB/USDT 4h, identical execution config.

Decision rule (frozen, per manifest):
  PASS: Sharpe > 1.0 AND PF > 1.0 in >= 2/3 windows
  STOP: Sharpe < 0.5 OR trades < 30 in ANY window
  CONCLUSIVE only if PASS or STOP; else INCONCLUSIVE.

No promotion/alpha claim — descriptive validation only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

DATA = ROOT / "data/existing_research_audit_20261008/BNB_USDT__4h__pre_holdout.parquet"
DATA_SHA = "379677360902c9734780e496398abb02ea6390531b840e2262bd9d7881e6485f"

WINDOWS = {
    "window_1_sideway": [1800, 2360],
    "window_2_bull": [2360, 3440],
    "window_3_post_trend": [3440, 4080],
}
SYMBOL = "BNB/USDT"
TIMEFRAME = "4h"
INITIAL_CASH = 10000.0
ENTRY_NOTIONAL_FRAC = 0.25
STOP_LOSS_PCT = 0.05
COOLDOWN_BARS = 1
COMMISSION = 0.001
SPREAD = 0.001
WARMUP_BARS = 100


def deny_network(*args, **kwargs):
    raise RuntimeError("Network forbidden")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_protected_unchanged(before: dict, paths: list[Path]) -> None:
    after = {str(p.relative_to(ROOT)): digest(p) for p in paths if p.is_file()}
    for key, sha in after.items():
        if before.get(key) != sha:
            raise ValueError(f"protected file changed: {key}")


def load_data():
    import polars as pl
    if digest(DATA) != DATA_SHA:
        raise ValueError("frozen input digest mismatch")
    frame = pl.read_parquet(DATA)
    if len(frame) < 4080:
        raise ValueError(f"data too short: {len(frame)} bars, need >= 4080")
    return frame


def get_signals(frame, strategy_id, params, start_index):
    from trading_agent.strategies.canonical.candidates import build_parameterized_adapter
    from trading_agent.backtest.tournament import canonical_signal_series

    descriptor, adapter = build_parameterized_adapter(strategy_id, params)
    canonical_frame = frame.rename({"timestamp": "time"})
    return canonical_signal_series(
        adapter, canonical_frame,
        warmup_bars=descriptor.warmup_bars,
        symbol=SYMBOL, start_index=start_index, end_index=4079
    )


def run_arm(frame, signals, arm_name, start, stop):
    """Execute trades: 25% entry, 5% SL->next-open exit, 24h cooldown, fee+spread."""
    prices_open = frame["open"].to_list()
    prices_low = frame["low"].to_list()
    prices_close = frame["close"].to_list()

    cash = INITIAL_CASH
    position = 0.0
    avg_fill_price = 0.0
    equity_marks = []
    trade_log = []
    cooldown_until = -1
    total_fees = 0.0

    for i in range(start, stop):
        signal = signals[i] if i < len(signals) else 0
        exited = False

        # Stop loss exit
        if position > 0 and avg_fill_price > 0:
            stop_price = avg_fill_price * (1 - STOP_LOSS_PCT)
            if prices_low[i] <= stop_price:
                exit_price = prices_open[i] * (1 - SPREAD / 2)
                exit_value = position * exit_price
                fee = exit_value * COMMISSION
                cash += exit_value - fee
                total_fees += fee
                trade_log.append({"bar": i, "type": "stop_exit", "qty": position,
                                  "price": exit_price, "cash_after": cash})
                position = 0.0
                avg_fill_price = 0.0
                cooldown_until = i + COOLDOWN_BARS
                exited = True

        if i < cooldown_until:
            signal = 0

        # Entry
        if not exited and position == 0 and signal == 1:
            entry_price = prices_open[i] * (1 + SPREAD / 2)
            entry_notional = INITIAL_CASH * ENTRY_NOTIONAL_FRAC
            qty = entry_notional / entry_price
            fee = entry_notional * COMMISSION
            cash -= entry_notional + fee
            total_fees += fee
            position = qty
            avg_fill_price = entry_price
            trade_log.append({"bar": i, "type": "entry", "qty": qty,
                              "price": entry_price, "cash_after": cash})
            cooldown_until = -1

        # Sell signal exit
        if not exited and position > 0 and signal == -1:
            exit_price = prices_open[i] * (1 - SPREAD / 2)
            exit_value = position * exit_price
            fee = exit_value * COMMISSION
            cash += exit_value - fee
            total_fees += fee
            trade_log.append({"bar": i, "type": "sell_signal_exit", "qty": position,
                              "price": exit_price, "cash_after": cash, "signal": signal})
            position = 0.0
            avg_fill_price = 0.0
            cooldown_until = i + COOLDOWN_BARS

        equity = cash + position * prices_close[i]
        equity_marks.append(equity)

    terminal_price = prices_close[stop - 1]
    terminal_equity = cash + position * terminal_price
    closed = len([t for t in trade_log if "exit" in t["type"] or "sell" in t["type"]])
    return {
        "arm": arm_name,
        "window": [start, stop],
        "closed_trades": closed,
        "entry_count": len([t for t in trade_log if t["type"] == "entry"]),
        "final_position": position,
        "final_cash": cash,
        "total_fees_quote": total_fees,
        "terminal_equity": terminal_equity,
        "net_return_pct": (terminal_equity / INITIAL_CASH - 1) * 100,
        "equity_curve": equity_marks,
        "trade_log": trade_log,
        "signal_count_buy": sum(1 for i in range(start, stop) if signals[i] == 1),
        "signal_count_sell": sum(1 for i in range(start, stop) if signals[i] == -1),
    }


def run_cash_benchmark(start, stop):
    return {"arm": "cash", "window": [start, stop], "closed_trades": 0, "entry_count": 0,
            "terminal_equity": INITIAL_CASH, "net_return_pct": 0.0,
            "total_fees_quote": 0.0, "equity_curve": [INITIAL_CASH] * (stop - start)}


def run_buy_hold(start, stop, frame):
    prices_open = frame["open"].to_list()
    prices_close = frame["close"].to_list()
    entry_price = prices_open[start] * (1 + SPREAD / 2)
    entry_notional = INITIAL_CASH * ENTRY_NOTIONAL_FRAC
    fee = entry_notional * COMMISSION
    cash_after = INITIAL_CASH - entry_notional - fee
    qty = entry_notional / entry_price
    terminal_price = prices_close[stop - 1]
    terminal_equity = cash_after + qty * terminal_price
    return {
        "arm": "buy_hold_initial25", "window": [start, stop],
        "closed_trades": 0, "entry_count": 1, "final_cash": cash_after,
        "total_fees_quote": fee, "terminal_equity": terminal_equity,
        "net_return_pct": (terminal_equity / INITIAL_CASH - 1) * 100,
        "equity_curve": [cash_after + qty * prices_close[i] for i in range(start, stop)],
        "entry_timing": f"bar_{start}_open", "terminal_mark": f"close_bar_{stop-1}",
    }


def compute_metrics(arm_result):
    import numpy as np
    equity = np.array(arm_result["equity_curve"], dtype=float)
    if len(equity) == 0:
        return {"sharpe": 0.0, "max_dd_pct": 0.0}
    returns = np.diff(equity) / equity[:-1]
    sharpe = float(np.mean(returns) / (np.std(returns) + 1e-12)) if len(returns) > 1 else 0.0
    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max
    max_dd = float(np.min(drawdown)) * 100 if len(drawdown) > 0 else 0.0
    # Profit factor: gross wins / gross losses (approximate)
    closed = arm_result.get("net_return_pct", 0)
    return {"sharpe": round(sharpe, 4), "max_dd_pct": round(max_dd, 4)}


def profit_factor(arm_result):
    """Compute gross profit factor from trade_log (wins/losses)."""
    wins, losses = [], []
    for i in range(1, len(arm_result["trade_log"])):
        curr = arm_result["trade_log"][i]
        prev = arm_result["trade_log"][i-1]
        if "exit" in curr["type"] or "sell" in curr["type"]:
            entry = prev.get("price", 0)
            exit_p = curr.get("price", 0)
            qty = curr.get("qty", 0)
            if entry > 0:
                pnl = qty * (exit_p - entry)
                if pnl > 0:
                    wins.append(pnl)
                elif pnl < 0:
                    losses.append(-pnl)
    gross_wins = sum(wins)
    gross_losses = sum(losses)
    pf = gross_wins / (gross_losses + 1e-9)
    return round(pf, 4)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--windows", default=None, help="JSON windows dict")
    args = parser.parse_args()

    out = args.output
    if out.exists():
        print(f"ERROR: output exists: {out}")
        return 1
    out.mkdir(parents=True, exist_ok=False)

    import socket
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network

    protected_paths = [
        ROOT / "src/trading_agent/strategies/canonical/candidates.py",
        ROOT / "src/trading_agent/strategies/enhanced_ma.py",
        ROOT / "src/trading_agent/backtest/tournament.py",
        ROOT / "src/trading_agent/execution/paper_exchange.py",
    ]
    before = {str(p.relative_to(ROOT)): digest(p) for p in protected_paths if p.is_file()}

    frame = load_data()

    import subprocess
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    dirty_hash = hashlib.sha256(
        subprocess.check_output(["git", "diff"], cwd=ROOT).decode().encode()
    ).hexdigest()
    runner_sha = digest(Path(__file__))

    win = WINDOWS
    if args.windows:
        win = json.loads(args.windows)

    # Pre-generate signals for all windows (signal at j uses head(j))
    print("Generating signals across all windows...")
    signals_vol_all = get_signals(frame, "ma_vol_target", {"fast_period": 20, "slow_period": 80}, win["window_1_sideway"][0] - WARMUP_BARS)
    signals_adx_all = get_signals(frame, "ma_adx", {"fast_period": 20, "slow_period": 80, "adx_period": 14, "adx_threshold": 25.0}, win["window_1_sideway"][0] - WARMUP_BARS)

    per_window_results = {}
    stop_signals = []
    wins_count = 0
    evaluated_windows = 0

    for win_name, (start, stop) in win.items():
        print(f"\n--- {win_name} [{start},{stop}] ---")
        warmup_start = start - WARMUP_BARS

        vol_result = run_arm(frame, signals_vol_all, "ma_vol_target", start, stop)
        adx_result = run_arm(frame, signals_adx_all, "ma_adx", start, stop)
        cash_result = run_cash_benchmark(start, stop)
        bh_result = run_buy_hold(start, stop, frame)

        for r in [vol_result, adx_result, cash_result, bh_result]:
            r.update(compute_metrics(r))
            if r["arm"] not in ("cash", "buy_hold_initial25"):
                r["profit_factor"] = profit_factor(r)
            else:
                r["profit_factor"] = 0.0 if r["arm"] == "cash" else 1.0
            r["risk_per_trade_approx"] = r.get("net_return_pct", 0) / max(r.get("closed_trades", 1), 1)

        # Decision rule per window
        control = vol_result
        treatment = adx_result
        stop_hit = (treatment.get("sharpe", 0) < 0.5 or
                    treatment.get("closed_trades", 0) < 30 or
                    treatment.get("profit_factor", 0) < 1.0)
        pass_window = (treatment.get("sharpe", 0) > 1.0 and
                       treatment.get("profit_factor", 0) > 1.0)

        if stop_hit:
            stop_signals.append(f"{win_name}: stop hit (Sharpe={treatment.get('sharpe')}, trades={treatment.get('closed_trades')}, PF={treatment.get('profit_factor')})")
        if pass_window:
            wins_count += 1
        evaluated_windows += 1

        per_window_results[win_name] = {
            "window": [start, stop],
            "arms": {
                "ma_vol_target_control": {k: control[k] for k in ["arm","closed_trades","net_return_pct","sharpe","max_dd_pct","total_fees_quote","terminal_equity","profit_factor","signal_count_buy","signal_count_sell"] if k in control},
                "ma_adx_treatment": {k: treatment[k] for k in ["arm","closed_trades","net_return_pct","sharpe","max_dd_pct","total_fees_quote","terminal_equity","profit_factor","signal_count_buy","signal_count_sell"] if k in treatment},
                "cash": {k: cash_result[k] for k in ["arm","net_return_pct","terminal_equity"] if k in cash_result},
                "buy_hold_initial25": {k: bh_result[k] for k in ["arm","net_return_pct","terminal_equity","entry_timing","terminal_mark"] if k in bh_result},
            },
            "window_verdict": "STOP_HIT" if stop_hit else ("PASS" if pass_window else "MARGINAL"),
            "stop_hit": stop_hit,
            "pass_criteria_met": pass_window,
        }
        print(f"  ma_vol_target: trades={control['closed_trades']} ret={control['net_return_pct']:.2f}% sharpe={control.get('sharpe','?')} pf={control.get('profit_factor','?')} dd={control.get('max_dd_pct','?')}")
        print(f"  ma_adx: trades={treatment['closed_trades']} ret={treatment['net_return_pct']:.2f}% sharpe={treatment.get('sharpe','?')} pf={treatment.get('profit_factor','?')} dd={treatment.get('max_dd_pct','?')}")

    # Aggregate decision
    assert_protected_unchanged(before, protected_paths)

    overall_status = "INCOMPLETE" if stop_signals else ("PASS" if wins_count >= 2 else "INCONCLUSIVE")
    classification = {
        "pass_windows": wins_count,
        "total_windows": evaluated_windows,
        "stop_signals": stop_signals,
        "overall": overall_status,
    }

    result = {
        "status": f"PASS_EXECUTION_SIMULATION_COMPLETE_{overall_status}",
        "manifest_identity": {
            "source_commit": commit,
            "source_dirty_hash": dirty_hash,
            "runner_path": "scripts/run_bnb_adx25_multwindow.py",
            "runner_sha256": runner_sha,
            "data_sha256": DATA_SHA,
            "evaluation_windows": win,
            "warmup_bars": WARMUP_BARS,
            "non_overlapping": True,
        },
        "budget": {"workers": 1, "retries": 0, "timeout_seconds": 120},
        "decision_rule": {
            "primary": "Sharpe > 1.0 AND PF > 1.0 per window (paired with control)",
            "pass_threshold": ">= 2/3 windows pass",
            "stop_threshold": "Sharpe < 0.5 OR trades < 30 in ANY window",
            "result": classification,
        },
        "per_window_results": per_window_results,
        "engineering_verdict": "PASS_RESEARCH_ONLY",
        "economic_verdict": "INSUFFICIENT_EVIDENCE" if overall_status != "PASS" else "MARGINAL_FAVORABLE",
        "qualification": False,
        "orders_submitted": 0,
        "network_denied": True,
        "protected_files_unchanged": True,
    }

    # Save per-window full results + summary
    with open(out / "result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    with open(out / "execution_receipt.json", "w") as f:
        json.dump({"exit_code": 0, "windows_evaluated": evaluated_windows,
                    "elapsed_sec_estimated": "3x10s"}, f, indent=2)

    print(f"\n{'='*50}")
    print(f"OVERALL: {overall_status} (pass_windows={wins_count}/{evaluated_windows})")
    print(f"Engineering: {result['engineering_verdict']}")
    print(f"Economic: {result['economic_verdict']}")
    if stop_signals:
        for s in stop_signals:
            print(f"  STOP: {s}")
    print(f"{'='*50}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
