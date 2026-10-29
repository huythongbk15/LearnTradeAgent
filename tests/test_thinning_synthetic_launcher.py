"""Synthetic launcher protocol and fail-closed coverage contracts."""
import json

import polars as pl
import pytest

from trading_agent.backtest import thinning_synthetic as lane


def test_plan_has_exact_36_unique_cells_and_calibration():
    cells = lane.cells()
    assert len(cells) == len({c["id"] for c in cells}) == 36
    assert sum(c["arm"] == "PLUS" for c in cells) == 6
    assert sum(c["arm"] == "THINNED" for c in cells) == 30
    assert lane.calibration_counts(lane.fixture()) == {"numerator": 5, "denominator": 10, "missing": 0}
    for pair in lane.PAIRS:
        assert len([c for c in cells if c["pair"] == pair]) == 18


def test_calibration_never_uses_evaluation_values():
    original = lane.fixture()
    changed = original.with_columns(
        pl.Series("close", original["close"].to_list()[:240] + [0.01] * 40),
        pl.Series("btc_close", original["btc_close"].to_list()[:240] + [999999.0] * 40))
    assert lane.calibration_counts(changed) == lane.calibration_counts(original)


@pytest.fixture
def protocol(tmp_path):
    return lane.freeze(tmp_path / "packet")


@pytest.mark.parametrize("field", ["calibration", "cells", "budget", "scope", "frame_sha256", "native_contracts"])
def test_even_rehashed_tamper_rejected_before_worker(protocol, field):
    plan = json.loads(protocol.read_bytes())
    plan[field] = {} if isinstance(plan[field], dict) else []
    plan["fingerprint"] = lane.digest({k: v for k, v in plan.items() if k != "fingerprint"})
    protocol.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="binding mismatch"):
        lane.verify(protocol)
    assert not (protocol.parent / "cells").exists()


def test_missing_cell_cannot_pass_coverage(protocol):
    summary = lane.coverage(protocol)
    assert summary["status"] == "INCOMPLETE"
    assert len(summary["coverage"]) == 36
    assert not any(r["verified"] for r in summary["coverage"])
    assert not summary["market_run"] and not summary["qualified"]


def test_fresh_packet_only(protocol):
    with pytest.raises(ValueError, match="fresh"):
        lane.freeze(protocol.parent)


def test_market_binding_rejected(protocol, monkeypatch):
    monkeypatch.setenv("TRADING_FROZEN_RESEARCH_DATA", "not-allowed")
    with pytest.raises(ValueError, match="market data binding"):
        lane.verify(protocol)


def test_actual_worker_respects_synthetic_admission_and_cannot_retry(protocol):
    cell = lane.cells()[0]
    lane.worker(protocol, cell["id"])
    out = protocol.parent / "cells" / cell["id"]
    result = json.loads((out / "result.json").read_bytes())
    report = json.loads((out / "report.json").read_bytes())
    assert result["status"] == "COMPLETED" and not result["market_run"]
    assert report["total_trades"] >= 1
    assert report["open_positions"] == report["open_orders"] == 0
    with pytest.raises(ValueError, match="retry or overwrite"):
        lane.worker(protocol, cell["id"])
