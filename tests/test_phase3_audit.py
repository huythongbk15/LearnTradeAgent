"""Tests for audit Phase 3 items: ablation harness, Risk agent rule-based
behavior, and absence of the Risk->Orchestrator circular dependency."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from trading_agent.agents.base import AnalysisContext
from trading_agent.agents.orchestrator import AblationConfig
from trading_agent.agents.risk import RiskManager


def _ohlcv(closes: list[float]) -> pl.DataFrame:
    n = len(closes)
    start = pl.datetime(2026, 1, 1)
    timestamps = [start + pl.duration(hours=i) for i in range(n)]
    return pl.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1000.0] * n,
        }
    )


class TestAblationConfig:
    def test_presets_exist(self) -> None:
        assert set(AblationConfig.PRESETS) == {"A", "B", "C", "D"}

    def test_preset_a_runs_all(self) -> None:
        cfg = AblationConfig("A")
        assert cfg.should_run("technical")
        assert cfg.should_run("sentiment")
        assert cfg.should_run("risk")

    def test_preset_b_skips_sentiment(self) -> None:
        cfg = AblationConfig("B")
        assert cfg.should_run("technical")
        assert not cfg.should_run("sentiment")
        assert cfg.should_run("risk")

    def test_preset_d_technical_only(self) -> None:
        cfg = AblationConfig("D")
        assert cfg.should_run("technical")
        assert not cfg.should_run("sentiment")
        assert not cfg.should_run("risk")

    def test_unknown_preset_falls_back_to_a(self) -> None:
        cfg = AblationConfig("Z")
        assert cfg.preset_name == "A"

    def test_custom_dict(self) -> None:
        cfg = AblationConfig({"technical": False, "sentiment": True, "risk": True})
        assert not cfg.should_run("technical")
        assert cfg.should_run("sentiment")
        assert cfg.preset_name == "custom"


class TestRiskManager:
    def _risk(self, context: AnalysisContext) -> RiskManager:
        return RiskManager()

    def test_high_volatility_blocks_position(self, monkeypatch) -> None:
        monkeypatch.setattr("trading_agent.agents.risk.llm_enabled", lambda: False)
        # Volatile series: big alternating swings -> daily vol >> 3%.
        closes = [100.0] + [100 + 15.0 * (1 if i % 2 else -1) for i in range(1, 60)]
        context = AnalysisContext(
            symbol="BTC/USDT",
            timeframe="1h",
            current_price=100.0,
            ohlcv=_ohlcv(closes),
            indicators={"_extra": {}},
        )
        msg = self._risk(context).analyze(context)
        assert msg.details["risk_level"] == "HIGH"
        assert msg.max_position_size_pct == 0.0

    def test_low_volatility_allows_buy(self, monkeypatch) -> None:
        monkeypatch.setattr("trading_agent.agents.risk.llm_enabled", lambda: False)
        closes = [100.0 + 0.1 * i for i in range(60)]
        context = AnalysisContext(
            symbol="BTC/USDT",
            timeframe="1h",
            current_price=103.0,
            ohlcv=_ohlcv(closes),
            indicators={"_extra": {}},
        )
        msg = self._risk(context).analyze(context)
        assert msg.details["risk_level"] == "LOW"
        assert msg.signal == "BUY"
        assert msg.max_position_size_pct > 0.0

    def test_missing_ohlcv_uses_conservative_size(self, monkeypatch) -> None:
        monkeypatch.setattr("trading_agent.agents.risk.llm_enabled", lambda: False)
        context = AnalysisContext(
            symbol="BTC/USDT",
            timeframe="1h",
            current_price=100.0,
            ohlcv=None,
            indicators={"_extra": {}},
        )
        msg = self._risk(context).analyze(context)
        assert msg.max_position_size_pct >= 0.0

    def test_high_risk_exit_when_in_position(self, monkeypatch) -> None:
        monkeypatch.setattr("trading_agent.agents.risk.llm_enabled", lambda: False)
        closes = [100.0] + [100 + 15.0 * (1 if i % 2 else -1) for i in range(1, 60)]
        context = AnalysisContext(
            symbol="BTC/USDT",
            timeframe="1h",
            current_price=100.0,
            ohlcv=_ohlcv(closes),
            indicators={"_extra": {}},
            current_position_pct=0.3,
        )
        msg = self._risk(context).analyze(context)
        assert msg.signal == "SELL"


def test_risk_module_has_no_orchestrator_import() -> None:
    """Circular dependency guard: RiskManager must not import orchestrator."""
    source = Path("src/trading_agent/agents/risk.py").read_text(encoding="utf-8")
    assert "from trading_agent.agents.orchestrator" not in source
    assert "import orchestrator" not in source
