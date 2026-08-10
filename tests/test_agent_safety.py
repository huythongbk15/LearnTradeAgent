"""Safety invariants for agent messages and orchestration."""

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.agents.base import AgentMessage, AnalysisContext
from trading_agent.agents.orchestrator import Orchestrator


class StubAgent:
    def __init__(self, message: AgentMessage) -> None:
        self.message = message

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        return self.message


def market_frame(rows: int = 25) -> pl.DataFrame:
    prices = [100.0 + i for i in range(rows)]
    return pl.DataFrame({
        "timestamp": [
            datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i)
            for i in range(rows)
        ],
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": [1.0] * rows,
    })


def test_agent_message_normalizes_untrusted_output() -> None:
    message = AgentMessage(
        role="risk_manager",
        signal="launch",
        confidence=4.2,
        reasoning="invalid payload",
        max_position_size_pct=-2,
        risk_level="unknown",
    )

    assert message.signal == "HOLD"
    assert message.confidence == 1.0
    assert message.max_position_size_pct == 0.0
    assert message.risk_level == "MEDIUM"
    assert message.warnings


def test_non_finite_payload_and_non_list_warnings_fail_closed() -> None:
    message = AgentMessage(
        role="trader",
        signal="launch",
        confidence=float("nan"),
        reasoning="invalid payload",
        max_position_size_pct=float("inf"),
        warnings="plugin warning",
    )

    assert message.signal == "HOLD"
    assert message.confidence == 0.0
    assert message.max_position_size_pct == 0.0
    assert "plugin warning" in message.warnings


def test_high_risk_is_a_hard_gate_and_trader_is_reported_once(monkeypatch) -> None:
    orchestrator = Orchestrator()
    orchestrator.technical = StubAgent(AgentMessage(
        role="technical_analyst", signal="BUY", confidence=1.0, reasoning="buy"
    ))
    orchestrator.sentiment = StubAgent(AgentMessage(
        role="sentiment_analyst", signal="BUY", confidence=1.0, reasoning="buy"
    ))
    orchestrator.risk = StubAgent(AgentMessage(
        role="risk_manager",
        signal="HOLD",
        confidence=1.0,
        reasoning="unsafe",
        risk_level="HIGH",
        max_position_size_pct=0.5,
    ))
    # Deliberately malicious/buggy custom trader: the orchestrator must still
    # enforce the Risk Manager's decision.
    orchestrator.trader = StubAgent(AgentMessage(
        role="trader",
        signal="BUY",
        confidence=1.0,
        reasoning="ignore risk",
        risk_level="LOW",
        max_position_size_pct=1.0,
    ))
    monkeypatch.setattr(orchestrator, "_compute_indicators", lambda df: df)
    monkeypatch.setattr(
        orchestrator,
        "_build_context",
        lambda df, symbol, timeframe, price, current_position, portfolio: AnalysisContext(
            symbol=symbol,
            timeframe=timeframe,
            current_price=price,
            ohlcv=df,
            indicators={"_extra": {"volatility_20_annualized": 20.0}},
            current_position_pct=current_position,
            portfolio_value=portfolio,
        ),
    )

    report = orchestrator.analyze(df=market_frame(), current_position_pct=0.0)

    assert report.final_decision.signal == "HOLD"
    assert report.final_decision.risk_level == "HIGH"
    assert report.final_decision.max_position_size_pct == 0.0
    assert report.final_decision.details["risk_gate"] is True
    assert [message.role for message in report.agent_messages].count("trader") == 1


@pytest.mark.parametrize(
    ("timeframe", "expected_daily_bars"),
    [("15m", 96), ("1h", 24), ("4h", 6), ("1d", 1)],
)
def test_timeframe_conversion(timeframe: str, expected_daily_bars: int) -> None:
    assert 1440 // Orchestrator._timeframe_minutes(timeframe) == expected_daily_bars
