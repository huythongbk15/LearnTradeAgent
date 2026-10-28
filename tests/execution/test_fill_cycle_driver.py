"""Driver and synthetic launcher proof. No market parquet."""
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from trading_agent.execution.position_cycle import (
    cycle_record_from_position,
    later_fill_must_keep_clock,
)
from trading_agent.execution.types import OrderSide, Position

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SYMBOL = "ETH/USDT"


def test_partial_remainder_and_restart_keep_the_first_fill():
    opened = datetime(2025, 1, 1, tzinfo=UTC)
    position = Position(
        SYMBOL, OrderSide.BUY, 1.0, 1100.0, current_price=1100.0,
        opened_at=opened, metadata={"entry_order_ids": ["entry-1"]},
    )
    restored = Position.from_dict(position.to_dict())
    restored.quantity = 0.4
    record = cycle_record_from_position(restored)
    assert record.first_fill_at == opened
    assert record.entry_order_id == "entry-1"
    assert record.quantity == pytest.approx(0.4)
    restored.metadata["entry_order_ids"] = ["entry-1", "entry-2"]
    assert later_fill_must_keep_clock(restored)


def test_second_entry_fill_does_not_move_the_simulator_clock():
    from scripts.full_system_backtest import FullSystemSimulator

    opened = datetime(2025, 1, 1, tzinfo=UTC)
    position = SimpleNamespace(
        is_active=True, opened_at=opened, updated_at=opened,
        metadata={
            "entry_order_ids": ["entry-1", "entry-2"],
            "simulation": {"entry_time": opened.isoformat()},
        },
    )
    simulator = object.__new__(FullSystemSimulator)
    simulator.symbol = SYMBOL
    simulator.engine = SimpleNamespace(
        exchange=SimpleNamespace(get_position=lambda symbol: position)
    )
    simulator._attach_entry_context(
        at=opened + timedelta(hours=3), bar_index=4, reference_price=1100.0,
        regime_info={}, strategy_id="own24_fill_context", preserve_first_fill=True,
    )
    assert position.opened_at == opened
    assert position.metadata["simulation"]["entry_time"] == opened.isoformat()


def test_precomputed_series_is_rejected(tmp_path):
    from trading_agent.backtest.fill_cycle_synthetic import reject_precomputed_signals

    assert reject_precomputed_signals(tmp_path) == 0
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "REJECTED"
    assert summary["reason"] == "precomputed_signal_series"
    assert summary["live_authorized"] is False
    assert summary["chain"] == ["launcher", "worker", "simulator", "report", "summary"]


def test_launcher_refuses_a_frozen_market_binding(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADING_FROZEN_RESEARCH_DATA", "{\"path\": \"market.parquet\"}")
    monkeypatch.setattr(sys, "argv", [
        "run_wfo_parallel.py", "--fill-cycle-synthetic", "--out", str(tmp_path),
    ])
    from scripts.run_wfo_parallel import main

    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code != 0


def test_launcher_worker_simulator_reports_a_24h_fill(tmp_path):
    env = os.environ.copy()
    env.pop("TRADING_FROZEN_RESEARCH_DATA", None)
    completed = subprocess.run(
        [
            sys.executable, "scripts/run_wfo_parallel.py",
            "--fill-cycle-synthetic", "--out", str(tmp_path),
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=240, check=False,
    )
    summary_path = tmp_path / "summary.json"
    detail = completed.stderr[-2000:] + completed.stdout[-2000:]
    assert summary_path.is_file(), detail
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert completed.returncode == 0, (summary, detail)
    assert summary["status"] == "SYNTHETIC_FILL_CYCLE_OBSERVED"
    assert summary["holding_hours"] == 24
    assert summary["live_authorized"] is False
    assert summary["qualification_status"] == "NOT_QUALIFIED"
    assert summary["market_run"] is False
    assert (tmp_path / "report.json").is_file()
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["total_trades"] == 1
    assert report["open_positions"] == 0
