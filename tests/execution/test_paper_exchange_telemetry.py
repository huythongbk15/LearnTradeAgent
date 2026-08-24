from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from trading_agent.execution import paper_exchange as paper_module
from trading_agent.execution.paper_exchange import PaperExchange
from trading_agent.execution.types import Trade


@dataclass
class RecordingTelemetry:
    trades: list[Trade] = field(default_factory=list)
    equity_snapshots: list[dict[str, float]] = field(default_factory=list)

    def record_trade(self, trade: Trade) -> None:
        self.trades.append(trade)

    def record_equity_snapshot(self, **snapshot: float) -> None:
        self.equity_snapshots.append(snapshot)


def _complete_roundtrip(exchange: PaperExchange, symbol: str) -> None:
    exchange.update_prices({symbol: 100.0})
    exchange.place_order(symbol, "buy", amount=1.0)
    for _ in range(19):
        exchange.update_prices({symbol: 110.0})
    exchange.place_order(symbol, "sell", amount=1.0)


def test_telemetry_sinks_are_isolated_per_exchange(tmp_path):
    first_sink = RecordingTelemetry()
    second_sink = RecordingTelemetry()
    first = PaperExchange(
        exchange_name="first",
        state_dir=tmp_path / "first",
        telemetry=first_sink,
    )
    second = PaperExchange(
        exchange_name="second",
        state_dir=tmp_path / "second",
        telemetry=second_sink,
    )

    _complete_roundtrip(first, "BTC/USDT")
    _complete_roundtrip(second, "ETH/USDT")

    assert [trade.symbol for trade in first_sink.trades] == ["BTC/USDT"]
    assert [trade.symbol for trade in second_sink.trades] == ["ETH/USDT"]
    assert len(first_sink.equity_snapshots) == 1
    assert len(second_sink.equity_snapshots) == 1


def test_none_telemetry_disables_global_database_writes(tmp_path, monkeypatch):
    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("global monitoring database must not be used")

    monkeypatch.setattr(paper_module, "_log_trade_to_db", fail_if_called)
    monkeypatch.setattr(paper_module, "_log_equity_snapshot", fail_if_called)
    exchange = PaperExchange(
        exchange_name="disabled",
        state_dir=tmp_path,
        telemetry=None,
    )

    _complete_roundtrip(exchange, "BTC/USDT")

    assert len(exchange.trades) == 1


def test_default_telemetry_preserves_monitoring_database_behavior(
    tmp_path, monkeypatch
):
    calls: list[str] = []
    monkeypatch.setattr(
        paper_module,
        "_log_trade_to_db",
        lambda *args, **kwargs: calls.append("trade"),
    )
    monkeypatch.setattr(
        paper_module,
        "_log_equity_snapshot",
        lambda *args, **kwargs: calls.append("equity"),
    )
    exchange = PaperExchange(exchange_name="default", state_dir=tmp_path)

    _complete_roundtrip(exchange, "BTC/USDT")

    assert calls == ["equity", "trade"]
