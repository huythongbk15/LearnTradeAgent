"""Research cells may relax event-store fsync. Live callers stay FULL."""

from __future__ import annotations

import pytest

from trading_agent.execution.lifecycle.store import ExecutionEventStore


def test_event_store_defaults_to_full_sync(tmp_path):
    store = ExecutionEventStore(tmp_path / "events.db").connect()
    mode = store._conn.execute("PRAGMA synchronous").fetchone()[0]
    assert mode == 2


def test_event_store_accepts_normal_sync(tmp_path):
    store = ExecutionEventStore(tmp_path / "events.db", synchronous="NORMAL").connect()
    mode = store._conn.execute("PRAGMA synchronous").fetchone()[0]
    assert mode == 1


def test_event_store_rejects_off_sync(tmp_path):
    with pytest.raises(ValueError):
        ExecutionEventStore(tmp_path / "events.db", synchronous="OFF")


def test_deferred_paper_state_flushes_once(tmp_path):
    from trading_agent.execution.paper_exchange import PaperExchange

    exchange = PaperExchange(state_dir=tmp_path, defer_durable_state=True, telemetry=None)
    exchange._save_state()
    assert list(tmp_path.glob("paper_*.json")) == []
    exchange.flush_state()
    assert list(tmp_path.glob("paper_*.json"))
