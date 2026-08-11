"""Tests for decision chain tracing (audit Phase 5)."""

from __future__ import annotations

import logging
import re

from trading_agent.agents.orchestrator import AgentAnalysisReport, Orchestrator


class _Tracer(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_analyze_report_carries_trace_id() -> None:
    orch = Orchestrator(ablation_preset="A")
    report = orch.analyze("BTC/USDT", "1h")
    assert isinstance(report, AgentAnalysisReport)
    assert re.fullmatch(r"[0-9a-f]{12}", report.trace_id)


def test_trace_logs_cover_all_stages() -> None:
    handler = _Tracer()
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        orch = Orchestrator(ablation_preset="A")
        report = orch.analyze("BTC/USDT", "1h")
    finally:
        root.removeHandler(handler)

    trace_lines = [line for line in handler.lines if line.startswith(f"TRACE[{report.trace_id}]")]
    assert len(trace_lines) >= 5
    stages = [line.split("stage=")[1].split(" ")[0] for line in trace_lines]
    for expected in ("data", "indicators", "agent", "final"):
        assert any(s.startswith(expected) for s in stages)
