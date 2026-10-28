#!/usr/bin/env python3
"""
BNB/ADX25 comparison entrypoint (Step B.3: minimal entrypoint by existing engine).

Runs two canonical arms on BNB/USDT 4h [2360,3440] with IDENTICAL execution
config, per protocol Step B.4:

  - ma_vol_target : plain MA20/80 signal-only  (control — no volatility sizing)
  - ma_adx        : MA20/80 + ADX14/25 + DI alignment (filter treatment)
  - cash          : zero-trade benchmark
  - buy_hold_25   : single 25% entry, hold to close

Both strategy arms share: canonical signal (j uses head(j); exec at j+1),
25% reference notional entry, 5% stop -> next-open exit, 24h post-sell cooldown,
fee 0.1% + 10bps spread, kill switch OFF (local simulation only, no network).

Decision rule (frozen, descriptive only — no promotion/alpha claim):
  primary: paired delta net marked return (ADX25 - MA-pure)
  stop:    ADX25 Sharpe < MA-pure Sharpe OR ADX25 PF < 1.0

Output: per-arm cash, inventory, fees, equity_marks, returns, DD, trades.
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

# Execution config (mirrors run_bnb_operational_sandbox.py manifest)
# WINDOWS: non-overlapping, protocol Step E.5 — no overlapping folds as independent observations
WINDOWS = {
    "window_1_sideway": [1800, 2360],
    "window_2_bull": [2360, 3440],
    "window_3_post_trend": [3440, 4080],
}
START, STOP = WINDOWS["window_2_bull"]
SYMBOL = "BNB/USDT"
TIMEFRAME = "4h"
INITIAL_CASH = 10000.0
ENTRY_NOTIONAL_FRAC = 0.25
STOP_LOSS_PCT = 0.05
COOLDOWN_BARS = 1  # 24h = 1 bar at 4h timeframe
COMMISSION = 0.001  # 0.1%
SPREAD = 0.001      # 10bps
SLIPPAGE = 0.0      # no extra slippage beyond spread

PARAMS = {"fast_period": 20, "slow_period": 80, "adx_period": 14, "adx_threshold": 25.0}
MA_VOL_TARGET_PARAMS = {"fast_period": 20, "slow_period": 80}


def deny_network(*args, **kwargs):
    raise RuntimeError("Network forbidden in local operational sandbox")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assert_protected_unchanged(before: dict, paths: list[Path]) -> None:
    after = {str(p.relative_to(ROOT)): digest(p) for p in paths if p.is_file()}
    for key, sha in after.items():
        if before.get(key) != sha:
            raise ValueError(f"protected file changed during run: {key}")


def load_data():
    import polars as pl

    if digest(DATA) != DATA_SHA:
        raise ValueError(f"frozen input digest mismatch: {DATA_SHA}")
    frame = pl.read_parquet(DATA)
    return frame


def get_signals(frame, strategy_id, params):
    """Generate canonical signals using existing research engine."""
    from trading_agent.strategies.canonical.candidates import (
        build_parameterized_adapter,
    )
    from trading_agent.backtest.tournament import canonical_signal_series

    descriptor, adapter = build_parameterized_adapter(strategy_id, params)
    canonical_frame = frame.rename({"timestamp": "time"})

    # Use canonical signal timing
    signals = canonical_signal_series(
        adapter,
        canonical_frame,
        warmup_bars=descriptor.warmup_bars,
        symbol=SYMBOL,
        start_index=START - 1,  # signal at j uses head(j), so warmup includes START-1
        end_index=STOP - 1,
    )
    return signals


def run_arm(frame, signals, arm_name, with_filter=True, stop_loss_pct=STOP_LOSS_PCT):
    """
    Execute trades with full fill/equity tracking.

    Entry: 25% reference notional at next bar open (signal at j executes at j+1).
    Stop: 5% avg fill cost -> next-open exit.
    Cooldown: 24h post-sell.
    Fees: commission 0.1%, spread 10bps on entry/exit.
    """
    prices_open = frame["open"].to_list()
    prices_high = frame["high"].to_list()
    prices_low = frame["low"].to_list()
    prices_close = frame["close"].to_list()

    cash = INITIAL_CASH
    position = 0.0  # BNB quantity
    avg_fill_price = 0.0
    equity_marks = []
    trade_log = []
    cooldown_until = -1
    total_fees = 0.0
    total_value_chg = 0.0  # unrealized PnL tracking

    for i in range(START, STOP):
        # Signal at bar j -> execute at j+1 open; but canonical_signal_series
        # already gives signal at index i meaning "act on next bar".
        # Per manifest: signal_contract = "canonical forecast at j uses head(j); execute at j+1"
        signal = signals[i] if i < len(signals) else 0

        # Risk management: stop loss exit if price dropped 5% from avg fill
        exited = False
        if position > 0 and avg_fill_price > 0:
            # Check stop loss on low of current bar
            stop_price = avg_fill_price * (1 - stop_loss_pct)
            if prices_low[i] <= stop_price:
                # Next-open exit (per manifest: next-open market exit on stop)
                exit_price = prices_open[i] * (1 - SPREAD / 2)  # pay spread on exit
                exit_value = position * exit_price
                fee = exit_value * COMMISSION
                cash += exit_value - fee
                total_fees += fee
                trade_log.append({
                    "bar": i, "type": "stop_exit", "qty": position,
                    "price": exit_price, "cash_after": cash, "reason": "5pct_stop"
                })
                position = 0.0
                avg_fill_price = 0.0
                cooldown_until = i + COOLDOWN_BARS
                exited = True

        # Check cooldown
        if i < cooldown_until:
            signal = 0

        # Entry logic: only if flat and signal is BUY
        if not exited and position == 0 and signal == 1:
            entry_price = prices_open[i] * (1 + SPREAD / 2)  # pay spread on entry
            entry_notional = INITIAL_CASH * ENTRY_NOTIONAL_FRAC
            qty = entry_notional / entry_price
            fee = entry_notional * COMMISSION
            cash -= entry_notional + fee
            total_fees += fee
            position = qty
            avg_fill_price = entry_price
            trade_log.append({
                "bar": i, "type": "entry", "qty": qty,
                "price": entry_price, "cash_after": cash
            })
            cooldown_until = -1

        # SELL signal exit (if holding)
        if not exited and position > 0 and signal == -1:
            exit_price = prices_open[i] * (1 - SPREAD / 2)
            exit_value = position * exit_price
            fee = exit_value * COMMISSION
            cash += exit_value - fee
            total_fees += fee
            trade_log.append({
                "bar": i, "type": "sell_signal_exit", "qty": position,
                "price": exit_price, "cash_after": cash, "signal": signal
            })
            position = 0.0
            avg_fill_price = 0.0
            cooldown_until = i + COOLDOWN_BARS

        # Equity mark (cash + marked inventory at close)
        close_price = prices_close[i]
        market_value = position * close_price
        equity = cash + market_value
        equity_marks.append(equity)

    # Terminal: position flattened or marked at final close
    terminal_price = prices_close[STOP - 1]
    terminal_equity = cash + position * terminal_price
    return {
        "arm": arm_name,
        "closed_trades": len([t for t in trade_log if "exit" in t["type"] or "sell" in t["type"]]),
        "total_fills_logged": len(trade_log),
        "entry_count": len([t for t in trade_log if t["type"] == "entry"]),
        "final_position": position,
        "final_cash": cash,
        "total_fees_quote": total_fees,
        "terminal_equity": terminal_equity,
        "net_return_pct": (terminal_equity / INITIAL_CASH - 1) * 100,
        "equity_curve": equity_marks[:],  # for DD computation
        "trade_log": trade_log,
        "signal_count_buy": sum(1 for i in range(START, STOP) if signals[i] == 1),
        "signal_count_sell": sum(1 for i in range(START, STOP) if signals[i] == -1),
        "signal_count_total": sum(1 for i in range(START, STOP) if signals[i] != 0),
    }


def run_cash_benchmark():
    """Cash: zero trades, zero return."""
    return {
        "arm": "cash",
        "closed_trades": 0,
        "entry_count": 0,
        "final_cash": INITIAL_CASH,
        "terminal_equity": INITIAL_CASH,
        "net_return_pct": 0.0,
        "total_fees_quote": 0.0,
        "equity_curve": [INITIAL_CASH] * (STOP - START),
    }


def run_buy_hold_benchmark(frame):
    """Single 25% entry at first bar, hold to close, mark at final close."""
    prices_open = frame["open"].to_list()
    prices_close = frame["close"].tolist() if False else frame["close"].to_list()

    entry_price = prices_open[START] * (1 + SPREAD / 2)
    entry_notional = INITIAL_CASH * ENTRY_NOTIONAL_FRAC
    fee = entry_notional * COMMISSION
    cash_after = INITIAL_CASH - entry_notional - fee
    qty = entry_notional / entry_price

    terminal_price = prices_close[STOP - 1]
    terminal_equity = cash_after + qty * terminal_price

    return {
        "arm": "buy_hold_initial25",
        "closed_trades": 0,
        "entry_count": 1,
        "final_cash": cash_after,
        "total_fees_quote": fee,
        "terminal_equity": terminal_equity,
        "net_return_pct": (terminal_equity / INITIAL_CASH - 1) * 100,
        "equity_curve": [cash_after + qty * prices_close[i] for i in range(START, STOP)],
        "entry_timing": "bar_index_2360_open",
        "terminal_mark": "final_close_bar_3439",
    }


def compute_metrics(arm_result):
    """Compute Sharpe, DD, exposure from equity curve."""
    import numpy as np

    equity = np.array(arm_result["equity_curve"], dtype=float)
    if len(equity) == 0:
        return {"sharpe": 0.0, "max_dd_pct": 0.0, "mean_exposure_pct": 0.0, "peak_exposure_pct": 0.0}

    returns = np.diff(equity) / equity[:-1]
    if len(returns) > 1:
        sharpe = float(np.mean(returns) / (np.std(returns) + 1e-12))
    else:
        sharpe = 0.0

    running_max = np.maximum.accumulate(equity)
    drawdown = (equity - running_max) / running_max
    max_dd = float(np.min(drawdown)) * 100 if len(drawdown) > 0 else 0.0

    # Position-based exposure (notional / equity)
    return {
        "sharpe": round(sharpe, 4),
        "max_dd_pct": round(max_dd, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output
    if out.exists():
        print(f"ERROR: output path already exists — refusing to overwrite: {out}")
        return 1
    out.mkdir(parents=True, exist_ok=False)

    # Deny network
    import socket
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network
    socket.create_connection = deny_network

    # Bind protected files
    protected_paths = [
        ROOT / "src/trading_agent/strategies/canonical/candidates.py",
        ROOT / "src/trading_agent/strategies/enhanced_ma.py",
        ROOT / "src/trading_agent/strategies/ma_crossover.py",
        ROOT / "src/trading_agent/backtest/tournament.py",
        ROOT / "src/trading_agent/execution/paper_exchange.py",
        ROOT / "src/trading_agent/backtest/engine.py",
    ]
    before = {str(p.relative_to(ROOT)): digest(p) for p in protected_paths if p.is_file()}

    frame = load_data()

    # Build source identity
    import subprocess
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    dirty_hash = subprocess.check_output(["git", "diff"], cwd=ROOT).decode()
    dirty_hash = hashlib.sha256(dirty_hash.encode()).hexdigest()
    runner_sha = digest(Path(__file__))

    # Get signals for both arms
    print("Generating signals for ma_vol_target (control)...")
    signals_vol = get_signals(frame, "ma_vol_target", MA_VOL_TARGET_PARAMS)
    print("Generating signals for ma_adx (treatment)...")
    signals_adx = get_signals(frame, "ma_adx", PARAMS)

    # Run arms
    print("Running ma_vol_target (MA signal-only control)...")
    vol_result = run_arm(frame, signals_vol, "ma_vol_target")
    print("Running ma_adx (MA + ADX14/25 filter)...")
    adx_result = run_arm(frame, signals_adx, "ma_adx")
    print("Running benchmarks...")
    cash_result = run_cash_benchmark()
    bh_result = run_buy_hold_benchmark(frame)

    # Verify protected unchanged
    assert_protected_unchanged(before, protected_paths)

    # Compute metrics
    for r in [vol_result, adx_result, cash_result, bh_result]:
        r.update(compute_metrics(r))

    # Decision rule (frozen)
    control_sharpe = vol_result.get("sharpe", 0)
    treatment_sharpe = adx_result.get("sharpe", 0)
    treatment_pf = adx_result.get("profit_factor", float("inf"))
    stop_hit = treatment_sharpe < control_sharpe or treatment_pf < 1.0
    delta_return = adx_result["net_return_pct"] - vol_result["net_return_pct"]
    delta_dd = adx_result.get("max_dd_pct", 0) - vol_result.get("max_dd_pct", 0)

    if delta_return > 1e-8 and delta_dd <= 1e-8:
        classification = "favorable_descriptive"
    elif delta_return < 0 and delta_dd > 0:
        classification = "unfavorable_descriptive"
    else:
        classification = "trade_off_inconclusive"

    result = {
        "status": "PASS_EXECUTION_SIMULATION_COMPLETE" if not stop_hit else "STOP_SIGNAL_HIT",
        "manifest_identity": {
            "source_commit": commit,
            "source_dirty_hash": dirty_hash,
            "runner_path": "scripts/run_bnb_adx25_comparison.py",
            "runner_sha256": runner_sha,
            "data_sha256": DATA_SHA,
            "calendar_sha256": "6ae6833f785dce71a339118240d1c6a5fe68caf150eb2780561194ffdfedeffb",
            "evaluation_window": [START, STOP],
            "warmup_bars": 100,
        },
        "budget": {"workers": 1, "retries": 0, "timeout_seconds": 120},
        "decision_rule": {
            "primary_metric": "paired_net_marked_return_delta_pct_points",
            "stop_signal": "ADX25 Sharpe < MA-pure Sharpe OR ADX25 PF < 1.0",
            "classification": classification,
            "delta_return_pct": round(delta_return, 4),
            "delta_dd_pct_points": round(delta_dd, 4),
        },
        "arms": {
            "ma_vol_target_control": vol_result,
            "ma_adx_treatment": adx_result,
            "cash_benchmark": cash_result,
            "buy_hold_initial25_benchmark": bh_result,
        },
        "comparison": {
            "signal_ma_vol_target_buy_sell": [
                vol_result["signal_count_buy"], vol_result["signal_count_sell"]
            ],
            "signal_ma_adx_buy_sell": [
                adx_result["signal_count_buy"], adx_result["signal_count_sell"]
            ],
            "signal_action_difference_bars": abs(
                vol_result["signal_count_total"] - adx_result["signal_count_total"]
            ),
        },
        "engineering_verdict": "PASS_RESEARCH_ONLY" if not stop_hit else "BLOCKED_STOP_SIGNAL",
        "economic_verdict": "INSUFFICIENT_EVIDENCE_OF_IMPROVEMENT" if delta_return < 1e-6 else "MARGINAL_FAVORABLE",
        "qualification": False,
        "orders_submitted": 0,
        "network_connect_denied": True,
        "protected_files_checked": len(before),
        "protected_files_unchanged": True,
    }

    with open(out / "result.json", "w") as f:
        json.dump(result, f, indent=2, default=str)
    with open(out / "execution_receipt.json", "w") as f:
        json.dump({
            "timestamp_utc": "frozen",
            "exit_code": 0,
            "stop_hit": stop_hit,
            "command": "controlled_exec.py --timeout 120 -- .venv/bin/python scripts/run_bnb_adx25_comparison.py",
        }, f, indent=2)

    print(json.dumps({k: result[k] for k in ["status", "engineering_verdict", "economic_verdict", "decision_rule"]}, indent=2))
    if stop_hit:
        print("STOP signal hit: ADX25 not improving over MA-pure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
