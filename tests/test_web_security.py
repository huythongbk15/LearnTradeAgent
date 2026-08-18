from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

import webui.backend.app as backend


class FakePosition:
    def __init__(self, symbol, qty):
        self.symbol = symbol
        self.qty = qty


class FakeAlpaca:
    def __init__(self):
        self.close_calls: list[bool] = []
        self.orders: list[dict] = []

    async def close_all_positions(self, *, cancel_orders: bool):
        self.close_calls.append(cancel_orders)
        return {"requested": 1, "cancel_orders": cancel_orders, "account": "paper"}

    async def fetch_positions(self):
        return [FakePosition("BTC/USD", 1.0)]

    async def fetch_ticker(self, symbol):
        class Ticker:
            last = 50000.0
        return Ticker()

    async def create_order(self, order_req):
        self.orders.append(order_req)
        class Order:
            id = f"order-{len(self.orders)}"
        return Order()


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
    assert data["closed"] is True
    assert len(fake.orders) == 1
    assert fake.orders[0].symbol == "BTC/USD"


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
