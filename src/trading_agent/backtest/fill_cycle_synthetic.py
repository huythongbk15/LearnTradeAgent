"""Synthetic launcher proof for the fill-cycle timer. No market files."""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

CHAIN = ("launcher", "worker", "simulator", "report", "summary")
HOLDING = timedelta(hours=24)


def synthetic_frame(bars: int = 280) -> pl.DataFrame:
    """Flat history, one step up, then a flat hold long enough for a 24h exit."""
    if bars < 280:
        raise ValueError("synthetic fill-cycle frame is shorter than the locked proof")
    start = datetime(2024, 1, 1, tzinfo=UTC)
    prices = [1000.0 if index < 230 else 1100.0 for index in range(bars)]
    return pl.DataFrame({
        "timestamp": [start + timedelta(hours=index) for index in range(bars)],
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": [1.0] * bars,
    })


def _simulator_type():
    try:
        from scripts.full_system_backtest import FullSystemSimulator
    except ImportError:
        root = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(root))
        from scripts.full_system_backtest import FullSystemSimulator
    return FullSystemSimulator


def _write_summary(out_root: Path, payload: dict) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    body = {
        "chain": list(CHAIN),
        "evidence_class": "SYNTHETIC_TEST_ONLY",
        "live_authorized": False,
        "qualification_status": "NOT_QUALIFIED",
        "market_run": False,
        **payload,
    }
    (out_root / "summary.json").write_text(
        json.dumps(body, indent=2, default=str) + "\n", encoding="utf-8"
    )


def run_fill_cycle_synthetic(
    out_root: Path, *, arm: str = "A0", btc_context: str = "missing",
) -> int:
    """Worker used by the launcher. Returns 0 only when the 24h proof is present."""
    out_root = Path(out_root)
    if arm not in {"A0", "PLUS"} or btc_context not in {"up", "down", "missing"}:
        raise ValueError("unsupported synthetic arm/context")
    if (out_root / "report.json").exists() or (out_root / "summary.json").exists():
        raise ValueError("synthetic packet already contains evidence; use a new directory")
    if os.environ.get("TRADING_FROZEN_RESEARCH_DATA"):
        _write_summary(out_root, {
            "status": "REJECTED",
            "reason": "frozen_market_input",
        })
        return 2
    from trading_agent.strategies.own24_fill_context import Own24FillContextStrategy

    frame = synthetic_frame()
    if btc_context != "missing":
        direction = 1 if btc_context == "up" else -1
        frame = frame.with_columns(pl.Series(
            "btc_close", [100.0 * (1.001 ** (direction * i)) for i in range(frame.height)],
        ))
    simulator_type = _simulator_type()
    simulator = simulator_type(
        fresh=True,
        symbol="ETH/USDT",
        timeframe="1h",
        state_dir=out_root / "execution",
        report_path=out_root / "report.json",
        run_id="fill_cycle_synthetic",
        data_manifest_id="synthetic-fill-cycle-v1",
        gap_policy="reject",
        source_df_override=frame,
        execution_cycle_strategy=Own24FillContextStrategy({"arm": arm}),
        execution_cycle_synthetic_only=True,
        funnel_enabled=False,
    )
    try:
        report = simulator.run(start=240, end=280)
    except Exception as exc:
        _write_summary(out_root, {
            "status": "REJECTED",
            "reason": f"{type(exc).__name__}: {exc}",
        })
        return 2
    finally:
        store = getattr(getattr(simulator, "engine", None), "store", None)
        if store is not None:
            store.close()
    if arm == "PLUS" and btc_context != "up":
        return _accept_blocked_report(out_root, report, arm=arm, btc_context=btc_context)
    return _accept_report(out_root, report, arm=arm, btc_context=btc_context)


def reject_precomputed_signals(out_root: Path) -> int:
    """Unsupported price-only series must fail before any bar is simulated."""
    from trading_agent.strategies.own24_fill_context import Own24FillContextStrategy

    try:
        _simulator_type()(
            fresh=True,
            symbol="ETH/USDT",
            timeframe="1h",
            state_dir=out_root / "rejected-execution",
            report_path=out_root / "rejected-report.json",
            run_id="fill_cycle_rejected",
            data_manifest_id="synthetic-fill-cycle-v1",
            gap_policy="reject",
            source_df_override=synthetic_frame(),
            signal_series=[0, 1, -1],
            execution_cycle_strategy=Own24FillContextStrategy({"arm": "A0"}),
            execution_cycle_synthetic_only=True,
            funnel_enabled=False,
        )
    except ValueError as exc:
        if "precomputed signal series" not in str(exc):
            _write_summary(out_root, {"status": "REJECTED", "reason": str(exc)})
            return 2
        _write_summary(out_root, {
            "status": "REJECTED",
            "reason": "precomputed_signal_series",
            "detail": str(exc),
        })
        return 0
    _write_summary(out_root, {
        "status": "REJECTED",
        "reason": "precomputed_series_was_accepted",
    })
    return 2


def _accept_report(
    out_root: Path, report: dict, *, arm: str = "A0", btc_context: str = "missing",
) -> int:
    trades = list(report.get("trades") or [])
    reason = None
    holding = None
    if not (out_root / "report.json").is_file():
        reason = "missing_report"
    elif report.get("open_positions") != 0:
        reason = "position_left_open"
    elif len(trades) != 1:
        reason = f"expected_one_closed_trade:{len(trades)}"
    else:
        entry = datetime.fromisoformat(trades[0]["entry_time"])
        exit_at = datetime.fromisoformat(trades[0]["exit_time"])
        holding = exit_at - entry
        if holding != HOLDING:
            reason = f"holding:{holding}"
        elif not trades[0].get("entry_order_id"):
            reason = "missing_entry_order_id"
        elif report.get("measured_accounting") is None:
            reason = "missing_measured_accounting"
        else:
            error = float((report.get("cost_attribution") or {}).get("reconciliation_error", 1))
            if not math.isfinite(error) or abs(error) > 1e-6:
                reason = f"accounting_error:{error}"
    if reason:
        _write_summary(out_root, {
            "status": "REJECTED",
            "reason": reason,
            "trades": len(trades),
            "report_status": report.get("status"),
        })
        return 2
    _write_summary(out_root, {
        "status": "SYNTHETIC_FILL_CYCLE_OBSERVED",
        "arm": arm, "btc_context": btc_context,
        "holding_hours": 24,
        "entry_time": trades[0]["entry_time"],
        "exit_time": trades[0]["exit_time"],
        "entry_order_id": trades[0]["entry_order_id"],
        "quantity": trades[0]["quantity"],
        "pnl": trades[0]["pnl"],
        "total_trades": report.get("total_trades"),
        "measured_accounting": True,
        "verdict": "TIMER_PATH_OBSERVED_NOT_AN_EDGE",
    })
    return 0


def _accept_blocked_report(out_root: Path, report: dict, *, arm: str, btc_context: str) -> int:
    """A negative control must have a real empty report, not a fabricated PASS."""
    reason = None
    if not (out_root / "report.json").is_file():
        reason = "missing_report"
    elif report.get("total_trades") != 0 or report.get("trades"):
        reason = "blocked_context_generated_trades"
    elif report.get("open_positions") != 0 or report.get("open_orders") != 0:
        reason = "blocked_context_left_inventory_or_orders"
    elif report.get("measured_accounting") is None:
        reason = "missing_measured_accounting"
    _write_summary(out_root, {
        "status": "REJECTED" if reason else "SYNTHETIC_CONTEXT_BLOCK_OBSERVED",
        "reason": reason, "arm": arm, "btc_context": btc_context,
        "total_trades": report.get("total_trades"),
        "verdict": "CONTEXT_WIRING_ONLY_NOT_AN_EDGE",
    })
    return 2 if reason else 0
