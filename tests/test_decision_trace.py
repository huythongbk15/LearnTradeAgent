"""Tests for decision chain tracing (audit Phase 5)."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta

import polars as pl

from trading_agent.agents.orchestrator import AgentAnalysisReport, Orchestrator


class _Tracer(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def _fake_market_frame(rows: int = 50) -> pl.DataFrame:
    """Create a synthetic OHLCV frame for testing."""
    prices = [100.0 + i * 0.1 for i in range(rows)]
    return pl.DataFrame(
        {
            "timestamp": [
                datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i)
                for i in range(rows)
            ],
            "open": prices,
            "high": [p + 0.5 for p in prices],
            "low": [p - 0.5 for p in prices],
            "close": prices,
            "volume": [1.0] * rows,
        }
    )


def test_analyze_report_carries_trace_id() -> None:
    orch = Orchestrator(ablation_preset="A")
    df = _fake_market_frame(100)
    report = orch.analyze("BTC/USDT", "1h", df=df)
    assert isinstance(report, AgentAnalysisReport)
    assert re.fullmatch(r"[0-9a-f]{12}", report.trace_id)


def test_trace_logs_cover_all_stages() -> None:
    handler = _Tracer()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        orch = Orchestrator(ablation_preset="A")
        df = _fake_market_frame(100)
        report = orch.analyze("BTC/USDT", "1h", df=df)
    finally:
        root.removeHandler(handler)

    trace_lines = [
        line for line in handler.lines if line.startswith(f"TRACE[{report.trace_id}]")
    ]
    assert len(trace_lines) >= 5
    stages = [line.split("stage=")[1].split(" ")[0] for line in trace_lines]
    for expected in ("data", "indicators", "agent", "final"):
        assert any(s.startswith(expected) for s in stages)
