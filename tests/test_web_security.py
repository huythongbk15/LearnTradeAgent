from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import webui.backend.app as backend
from trading_agent.exchanges.models import AssetClass, MarketType, Symbol


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVENT_DB = PROJECT_ROOT / "data" / "execution" / "events.db"


@pytest.fixture(autouse=True)
def _clean_event_db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "execution").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ALPACA_API_KEY", "test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "test-secret")
    if EVENT_DB.exists():
        EVENT_DB.unlink()
    yield


class FakePosition:
    def __init__(self, symbol, qty):
        self.symbol = symbol
        self.size = qty
        self.entry_price = 50_000.0
        self.mark_price = 50_000.0
        self.unrealized_pnl = 0.0
        self.realized_pnl = 0.0
        self.notional = qty * self.mark_price


class FakeAlpaca:
    def __init__(self):
        self.close_calls: list[bool] = []
        self.orders: list[object] = []
        self._position_open = True

    async def close_all_positions(self, *, cancel_orders: bool):
        self.close_calls.append(cancel_orders)
        return {"requested": 1, "cancel_orders": cancel_orders, "account": "paper"}

    async def fetch_positions(self):
        if not self._position_open:
            return []
        symbol = Symbol(
            "BTC", "USD", AssetClass.CRYPTO, MarketType.SPOT, "alpaca"
        )
        return [FakePosition(symbol, 1.0)]

    async def fetch_ticker(self, symbol):
        class Ticker:
            last = 50000.0
            timestamp = datetime.now(UTC)

        return Ticker()

    def get_account_info(self):
        return {"equity": 100_000.0, "cash": 100_000.0}

    async def create_order(self, order_req):
        self.orders.append(order_req)
        self._position_open = False
        return SimpleNamespace(
            id=f"order-{len(self.orders)}",
            client_order_id=order_req.client_order_id,
            status="filled",
            filled_size=order_req.size,
            avg_fill_price=50_000.0,
            error=None,
        )


def test_health_route_is_available():
    response = TestClient(backend.app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_admin_endpoint_is_disabled_without_server_key(monkeypatch):
    monkeypatch.delenv("WEBUI_API_KEY", raising=False)
    response = TestClient(backend.app).post(
        "/api/positions/close",
        json={"confirm": "CLOSE_ALL_PAPER_POSITIONS"},
    )
    assert response.status_code == 503


def test_kill_switch_requires_key_and_explicit_confirmation(monkeypatch):
    monkeypatch.setenv("WEBUI_API_KEY", "test-secret")
    client = TestClient(backend.app)

    assert (
        client.post(
            "/api/positions/close",
            json={"confirm": "CLOSE_ALL_PAPER_POSITIONS"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/positions/close",
            headers={"X-API-Key": "test-secret"},
            json={"confirm": ""},
        ).status_code
        == 400
    )


def test_kill_switch_uses_shared_paper_adapter_and_verifies_empty(monkeypatch):
    monkeypatch.setenv("WEBUI_API_KEY", "test-secret")
    fake = FakeAlpaca()

    async def fake_alpaca():
        return fake

    monkeypatch.setattr(backend, "_alpaca", fake_alpaca)
    response = TestClient(backend.app).post(
        "/api/positions/close",
        headers={"X-API-Key": "test-secret"},
        json={"confirm": "CLOSE_ALL_PAPER_POSITIONS", "reason": "test"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["closed"] is True, data
    assert len(fake.orders) == 1
    assert fake.orders[0].symbol.pair == "BTC/USD"


def test_paper_cycle_is_off_by_default_and_rejects_live_money(monkeypatch):
    monkeypatch.setenv("WEBUI_API_KEY", "test-secret")
    monkeypatch.delenv("TRADING_EXECUTION_ENABLED", raising=False)
    client = TestClient(backend.app)
    headers = {"X-API-Key": "test-secret"}

    disabled = client.post(
        "/api/live/run",
        headers=headers,
        json={"live": False, "confirm": "RUN_PAPER_CYCLE"},
    )
    assert disabled.status_code == 403

    monkeypatch.setenv("TRADING_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    live_money = client.post(
        "/api/live/run",
        headers=headers,
        json={"live": True, "confirm": "RUN_PAPER_CYCLE"},
    )
    assert live_money.status_code == 400
    assert backend.JOBS == {} or all(
        job.get("status") != "running" for job in backend.JOBS.values()
    )


def test_second_paper_cycle_is_rejected_before_spawning(monkeypatch):
    monkeypatch.setenv("WEBUI_API_KEY", "test-secret")
    monkeypatch.setenv("TRADING_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("TRADING_MODE", "paper")
    assert backend.LIVE_JOB_LOCK.acquire(blocking=False)
    try:
        response = TestClient(backend.app).post(
            "/api/live/run",
            headers={"X-API-Key": "test-secret"},
            json={"live": False, "confirm": "RUN_PAPER_CYCLE"},
        )
    finally:
        backend.LIVE_JOB_LOCK.release()

    assert response.status_code == 409


def test_local_reset_passes_non_interactive_flag_after_confirmation(monkeypatch):
    monkeypatch.setenv("WEBUI_API_KEY", "test-secret")
    calls = []

    def fake_run_cli(args, timeout):
        calls.append((args, timeout))
        return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(backend, "_run_cli", fake_run_cli)
    response = TestClient(backend.app).post(
        "/api/execution/reset",
        headers={"X-API-Key": "test-secret"},
        json={"confirm": "RESET_LOCAL_PAPER_STATE"},
    )

    assert response.status_code == 200
    assert calls == [(["execution", "reset", "--yes"], 120)]
