"""Trading Agent System — Web UI Backend (FastAPI).

REST API + WebSocket push, tái sử dụng business logic của trading_agent.
Chạy: uvicorn webui.backend.app:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from trading_agent.execution.paper_exchange import PaperExchange
from trading_agent.execution.risk_controller import RiskController

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = PROJECT_ROOT / "data"
DIST_DIR = PROJECT_ROOT / "webui" / "frontend" / "dist"

app = FastAPI(title="Trading Agent System — Web UI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Backend services (lazy init, singleton)
# ---------------------------------------------------------------------------
_services: dict = {}
_snapshot_cache: dict = {"data": None, "ts": 0.0}


def _run_async(coro):
    """Chạy coroutine an toàn cả từ sync lẫn async context.

    - Không có running loop → asyncio.run() trực tiếp.
    - Đang trong running loop (vd WebSocket handler) → chạy trong thread
      riêng với event loop riêng, join tối đa 25s.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    out: dict = {}

    def worker() -> None:
        out["v"] = asyncio.run(coro)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=25)
    return out.get("v")


def _fetch_live_snapshot() -> dict:
    """Gọi Alpaca paper → snapshot. Luôn chạy ở context an toàn."""

    from trading_agent.exchanges.alpaca_adapter import AlpacaAdapter, AlpacaConfig

    async def _connect_and_fetch():
        adapter = AlpacaAdapter(AlpacaConfig(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_API_SECRET"],
            paper=True,
        ))
        await adapter.connect()
        info = adapter.get_account_info()  # sync trên adapter
        positions = await adapter.fetch_positions()  # async → await trực tiếp
        return info, positions

    info, raw_positions = _run_async(_connect_and_fetch())
    snap = {
        "equity": float(info.get("equity", 0)),
        "cash": float(info.get("cash", 0)),
        "positions": [],
    }
    for p in raw_positions:
        try:
            mark = float(p.mark_price)
            entry = float(p.entry_price)
            qty = float(p.size)
            pnl = (mark - entry) * qty if p.is_long else (entry - mark) * qty
            snap["positions"].append({
                "symbol": p.symbol.pair,
                "qty": qty,
                "avg_price": entry,
                "current_price": mark,
                "market_value": float(p.notional),
                "unrealized_pnl": pnl,
                "pnl_pct": ((mark - entry) / entry * 100) if entry else 0.0,
            })
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
    return snap


def _live_snapshot(ttl: float = 20.0) -> dict:
    """Snapshot live paper (Alpaca) — equity, cash, positions, peak, DD.

    Cache theo ttl để WebSocket/REST không spam Alpaca API.
    """
    now = time.time()
    if _snapshot_cache["data"] and (now - _snapshot_cache["ts"]) < ttl:
        return _snapshot_cache["data"]

    snap = {"source": "alpaca_paper", "equity": 0.0, "cash": 0.0,
            "positions": [], "peak": 0.0, "dd": 0.0, "trading_allowed": True,
            "note": ""}
    try:
        import os

        if not os.environ.get("ALPACA_API_KEY") or not os.environ.get("ALPACA_API_SECRET"):
            snap["note"] = "ALPACA_API_KEY/SECRET not set in .env"
            _snapshot_cache.update(data=snap, ts=now)
            return snap

        fetched = _fetch_live_snapshot()
        snap.update(fetched)
        peak_file = PROJECT_ROOT / "data" / "live_peak_equity.json"
        peak = snap["equity"]
        if peak_file.exists():
            try:
                peak = float(json.loads(peak_file.read_text()).get("peak", snap["equity"]))
            except (ValueError, OSError):
                pass
        snap["peak"] = peak
        snap["dd"] = max(0.0, (peak - snap["equity"]) / peak) if peak else 0.0
        snap["trading_allowed"] = snap["dd"] < 0.10  # max DD guard 10%
        _snapshot_cache.update(data=snap, ts=now)
    except Exception as exc:  # noqa: BLE001
        snap["note"] = str(exc)[:200]
        _snapshot_cache.update(data=snap, ts=now)
    return snap


def _paper() -> PaperExchange:
    if "paper" not in _services:
        _services["paper"] = PaperExchange(exchange_name="paper", state_dir=str(STATE_DIR))
    return _services["paper"]


def _risk() -> RiskController:
    if "risk" not in _services:
        _services["risk"] = RiskController()
    return _services["risk"]


# ---------------------------------------------------------------------------
# Job registry (backtest / live runs chạy nền)
# ---------------------------------------------------------------------------
JOBS: dict[str, dict] = {}


def _spawn_job(job_id: str, fn, **kwargs) -> None:
    """Chạy fn trong thread; kết quả lưu vào JOBS[job_id]."""

    def worker() -> None:
        try:
            result = fn(**kwargs)
            JOBS[job_id] = {"status": "done", "result": result, "error": None}
        except Exception as exc:  # noqa: BLE001
            JOBS[job_id] = {"status": "error", "result": None, "error": str(exc)}

    JOBS[job_id] = {"status": "running", "result": None, "error": None}
    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
class BacktestRequest(BaseModel):
    strategy: str
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"


class CloseRequest(BaseModel):
    reason: str = "webui_kill_switch"


class LiveRunRequest(BaseModel):
    live: bool = False


@app.get("/api/system")
def api_system() -> dict:
    cfg_path = PROJECT_ROOT / "config" / "config.yaml"
    cfg = {}
    try:
        import yaml

        cfg = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:  # noqa: BLE001
        pass
    return {
        "name": "Trading Agent System",
        "version": "1.0.0",
        "strategy_count": 10,
        "strategies": [
            "ma_crossover", "rsi", "bbands", "enhanced_ma", "ma_adx",
            "ma_vol_target", "ensemble_ma_adx", "ma_adx_regime",
            "regime_switching", "agent_ensemble",
        ],
        "symbols": (cfg.get("symbols", {}).get("binance") or [])[:12],
        "llm": (cfg.get("llm", {}) or {}),
        "alerts": (cfg.get("alerts", {}) or {}),
        "timeframes": (cfg.get("data", {}).get("timeframes") or []),
    }


@app.get("/api/portfolio")
def api_portfolio() -> dict:
    snap = _live_snapshot(ttl=5.0)
    return {
        "equity": snap["equity"],
        "cash": snap["cash"],
        "positions": snap["positions"],
        "source": snap["source"],
        "note": snap["note"],
    }


@app.get("/api/trades")
def api_trades(limit: int = 20) -> dict:
    try:
        trades = _paper().get_trade_history(limit=limit)  # type: ignore[attr-defined]
        return {"trades": trades}
    except Exception:  # noqa: BLE001
        return {"trades": [], "note": "paper exchange unavailable"}


@app.get("/api/risk")
def api_risk() -> dict:
    snap = _live_snapshot(ttl=5.0)
    return {
        "risk": {
            "equity": snap["equity"],
            "peak": snap["peak"],
            "drawdown_pct": round(snap["dd"] * 100, 2),
            "max_drawdown_pct": 10.0,
            "trading_allowed": snap["trading_allowed"],
            "note": snap["note"],
        }
    }


@app.post("/api/backtest")
def api_backtest(req: BacktestRequest) -> dict:
    job_id = uuid.uuid4().hex[:8]

    def run(strategy: str, symbol: str, timeframe: str) -> dict:
        from trading_agent.backtest.engine import run_backtest

        res = run_backtest(strategy_name=strategy, symbol=symbol, timeframe=timeframe)
        if hasattr(res, "metrics"):
            m = res.metrics
            return {
                "strategy": strategy, "symbol": symbol, "timeframe": timeframe,
                "total_return": float(m.get("total_return", 0)),
                "sharpe": float(m.get("sharpe", 0)),
                "profit_factor": float(m.get("profit_factor", 0)),
                "max_drawdown": float(m.get("max_drawdown", 0)),
                "win_rate": float(m.get("win_rate", 0)),
                "trades": int(m.get("trades", 0)),
            }
        return {"strategy": strategy, "raw": str(res)[:500]}

    _spawn_job(job_id, run, strategy=req.strategy, symbol=req.symbol, timeframe=req.timeframe)
    return {"job_id": job_id}


@app.get("/api/backtest/{job_id}")
def api_backtest_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if not job:
        return {"status": "not_found"}
    return job


@app.post("/api/positions/close")
def api_close(req: CloseRequest) -> dict:
    try:
        closed = _paper().close_all_positions(reason=req.reason)  # type: ignore[attr-defined]
        return {"closed": True, "detail": str(closed)[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"closed": False, "error": str(exc)}


@app.post("/api/live/run")
def api_live_run(req: LiveRunRequest) -> dict:
    job_id = uuid.uuid4().hex[:8]
    flag = "--live" if req.live else "--execute"

    def run(live: bool) -> dict:
        import subprocess

        cmd = ["python", "scripts/live_cron_runner.py", "--execute"]
        if live:
            cmd.append("--live")
        proc = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=600
        )
        out = (proc.stdout or "")[-3000:]
        return {"exit_code": proc.returncode, "output": out, "live": live}

    _spawn_job(job_id, run, live=req.live)
    return {"job_id": job_id}


@app.get("/api/live/status")
def api_live_status() -> dict:
    """Snapshot nhanh (equity/cash/positions/risk) — không đặt lệnh."""
    try:
        from scripts.live_status_report import main as live_status_main

        out = live_status_main()  # type: ignore[call-arg]
        return {"ok": True, "report": str(out)[:2000]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# WebSocket — realtime snapshot push
# ---------------------------------------------------------------------------
class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:  # noqa: BLE001
                self.disconnect(ws)


manager = ConnectionManager()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await manager.connect(ws)
    try:
        while True:
            snapshot = {
                "ts": time.time(),
                "system": api_system(),
                "portfolio": api_portfolio(),
                "trades": api_trades(limit=10),
                "risk": api_risk(),
            }
            await ws.send_json(snapshot)
            await asyncio.sleep(5)
    except WebSocketDisconnect:
        manager.disconnect(ws)


# ---------------------------------------------------------------------------
# Static frontend (production build) + catch-all
# ---------------------------------------------------------------------------
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        f = DIST_DIR / full_path
        if full_path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": time.time()}