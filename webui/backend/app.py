"""Trading Agent System — Web UI Backend (FastAPI).

REST API + WebSocket push, tái sử dụng business logic của trading_agent.
Chạy: uvicorn webui.backend.app:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from scripts.live_config import DRAWDOWN_TIERS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DIST_DIR = PROJECT_ROOT / "webui" / "frontend" / "dist"

app = FastAPI(title="Trading Agent System — Web UI", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "WEBUI_ALLOWED_ORIGINS",
            "http://127.0.0.1:8000,http://localhost:8000",
        ).split(",")
        if origin.strip()
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Backend services (lazy init, singleton)
# ---------------------------------------------------------------------------
_services: dict = {}
_snapshot_cache: dict = {"data": None, "ts": 0.0}
MAX_DRAWDOWN_FRACTION = max(
    (threshold for threshold, scale in DRAWDOWN_TIERS if scale <= 0),
    default=0.20,
)


def _require_admin(x_api_key: str | None = Header(default=None)) -> None:
    expected = os.getenv("WEBUI_API_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="WEBUI_API_KEY is not configured; administrative actions are disabled",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid administrative API key")


def _require_paper_execution() -> None:
    if os.getenv("TRADING_EXECUTION_ENABLED", "false").lower() != "true":
        raise HTTPException(status_code=403, detail="Trading execution is disabled")
    if os.getenv("TRADING_MODE", "paper").lower() != "paper":
        raise HTTPException(status_code=403, detail="Only paper trading is supported")


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

    async def _connect_and_fetch():
        adapter = await _alpaca()
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
        peak = max(peak, snap["equity"])
        snap["peak"] = peak
        snap["dd"] = max(0.0, (peak - snap["equity"]) / peak) if peak else 0.0
        snap["trading_allowed"] = snap["dd"] < MAX_DRAWDOWN_FRACTION
        _snapshot_cache.update(data=snap, ts=now)
    except Exception as exc:  # noqa: BLE001
        snap["note"] = str(exc)[:200]
        _snapshot_cache.update(data=snap, ts=now)
    return snap


async def _alpaca():
    """Return the single Alpaca Paper adapter used by portfolio and kill switch."""
    from trading_agent.exchanges.alpaca_adapter import AlpacaAdapter, AlpacaConfig

    if "alpaca" not in _services:
        adapter = AlpacaAdapter(AlpacaConfig(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_API_SECRET"],
            paper=True,
        ))
        await adapter.connect()
        _services["alpaca"] = adapter
    return _services["alpaca"]


# ---------------------------------------------------------------------------
# Job registry (backtest / live runs chạy nền)
# ---------------------------------------------------------------------------
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.RLock()
LIVE_JOB_LOCK = threading.Lock()
JOB_TTL_SECONDS = 3600


def _cleanup_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with JOBS_LOCK:
        expired = [
            job_id for job_id, job in JOBS.items()
            if job.get("created_at", 0) < cutoff and job.get("status") != "running"
        ]
        for job_id in expired:
            JOBS.pop(job_id, None)


def _set_progress(job_id: str, pct: int, stage: str) -> None:
    """Cập nhật tiến độ job (thread-safe qua dict)."""
    try:
        with JOBS_LOCK:
            JOBS[job_id]["progress"] = {"pct": max(0, min(100, int(pct))), "stage": stage[:120]}
    except KeyError:
        pass


def _add_job_line(job_id: str, line: str) -> None:
    """Append dòng log vào job (stream khi đang chạy)."""
    try:
        with JOBS_LOCK:
            lines = JOBS[job_id].setdefault("lines", [])
            if line:
                lines.append(line[:500])
            if len(lines) > 400:
                del lines[:-300]
    except KeyError:
        pass


def _spawn_job(job_id: str, fn, **kwargs) -> None:
    """Chạy fn trong thread; kết quả lưu vào JOBS[job_id]."""

    def worker() -> None:
        try:
            with JOBS_LOCK:
                JOBS[job_id]["progress"] = {"pct": 0, "stage": "bắt đầu"}
            result = fn(**kwargs)
            with JOBS_LOCK:
                JOBS[job_id] = {"status": "done", "result": result, "error": None,
                                "progress": {"pct": 100, "stage": "hoàn tất"},
                                "lines": JOBS.get(job_id, {}).get("lines") or [],
                                "created_at": JOBS.get(job_id, {}).get("created_at", time.time())}
        except Exception as exc:  # noqa: BLE001
            with JOBS_LOCK:
                JOBS[job_id] = {"status": "error", "result": None, "error": str(exc),
                                "progress": {"pct": 100, "stage": "lỗi"},
                                "lines": JOBS.get(job_id, {}).get("lines") or [],
                                "created_at": JOBS.get(job_id, {}).get("created_at", time.time())}

    _cleanup_jobs()
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "result": None, "error": None,
                        "progress": {"pct": 0, "stage": "khởi động"},
                        "created_at": time.time()}
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
    confirm: str = ""


class LiveRunRequest(BaseModel):
    live: bool = False
    confirm: str = ""


class ResetRequest(BaseModel):
    confirm: str = ""


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
        "llm": {
            "provider": (cfg.get("llm", {}) or {}).get("provider"),
            "model": (cfg.get("llm", {}) or {}).get("model"),
        },
        "alerts": {
            "telegram_configured": bool(
                os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID")
            ),
        },
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
    return {
        "trades": [],
        "source": "alpaca_paper",
        "note": "Alpaca trade-history synchronization is not implemented yet",
    }


@app.get("/api/risk")
def api_risk() -> dict:
    snap = _live_snapshot(ttl=5.0)
    return {
        "risk": {
            "equity": snap["equity"],
            "peak": snap["peak"],
            "drawdown_pct": round(snap["dd"] * 100, 2),
            "max_drawdown_pct": MAX_DRAWDOWN_FRACTION * 100,
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
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        return {"status": "not_found"}
    return job


@app.post("/api/positions/close", dependencies=[Depends(_require_admin)])
async def api_close(req: CloseRequest) -> dict:
    if req.confirm != "CLOSE_ALL_PAPER_POSITIONS":
        raise HTTPException(status_code=400, detail="Explicit close-all confirmation required")
    try:
        adapter = await _alpaca()
        detail = await adapter.close_all_positions(cancel_orders=True)
        remaining = await adapter.fetch_positions()
        return {
            "closed": len(remaining) == 0,
            "detail": detail,
            "remaining": [position.symbol.pair for position in remaining],
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Paper close-all failed: {exc}") from exc


@app.post("/api/live/run", dependencies=[Depends(_require_admin)])
def api_live_run(req: LiveRunRequest) -> dict:
    _require_paper_execution()
    if req.live:
        raise HTTPException(status_code=400, detail="Live-money mode is not supported")
    if req.confirm != "RUN_PAPER_CYCLE":
        raise HTTPException(status_code=400, detail="Explicit paper-cycle confirmation required")
    if not LIVE_JOB_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Another paper trading cycle is running")
    job_id = uuid.uuid4().hex[:8]

    def run() -> dict:
        import subprocess

        try:
            cmd = [sys.executable, "scripts/live_enhanced_ma.py", "--execute"]
            proc = subprocess.run(
                cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=600
            )
            out = ((proc.stdout or "") + (proc.stderr or ""))[-3000:]
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Paper cycle failed with exit code {proc.returncode}: {out}"
                )
            return {"exit_code": proc.returncode, "output": out, "mode": "paper"}
        finally:
            LIVE_JOB_LOCK.release()

    try:
        _spawn_job(job_id, run)
    except Exception:
        LIVE_JOB_LOCK.release()
        raise
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


# ===========================================================================
# Mở rộng: Agents / Data / Portfolio / System / LLM / Meta / Execution
# ===========================================================================
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _run_cli(args: list[str], timeout: int = 300, cwd: Path | None = None) -> dict:
    """Chạy lệnh CLI trading_agent trong subprocess, strip ANSI, trả text."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "trading_agent.cli", *args],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(cwd or PROJECT_ROOT),
    )
    return {
        "exit_code": proc.returncode,
        "stdout": _ANSI_RE.sub("", proc.stdout or "")[-8000:],
        "stderr": _ANSI_RE.sub("", proc.stderr or "")[-2000:],
    }


def _run_cli_stream(run_job_id: str, args: list[str], timeout: int = 300) -> dict:
    """Chạy CLI subprocess, stream từng dòng → stage + % tiến độ (ước lượng)."""
    import subprocess

    proc = subprocess.Popen(
        [sys.executable, "-m", "trading_agent.cli", *args],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, cwd=str(PROJECT_ROOT),
    )
    assert proc.stdout is not None
    lines: list[str] = []
    pct = 5
    for line in proc.stdout:
        clean = _ANSI_RE.sub("", line).strip()
        if clean:
            lines.append(clean)
            _add_job_line(run_job_id, clean)
            pct = min(92, pct + 4)
            _set_progress(run_job_id, pct, clean[:110])
    proc.wait(timeout=timeout)
    return {
        "exit_code": proc.returncode,
        "stdout": "\n".join(lines)[-8000:],
        "stderr": "",
    }


class AnalyzeRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"


class FetchRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    since: str | None = None
    exchange: str = "binance"
    save: bool = False


class OptimizeRequest(BaseModel):
    symbols: list[str] = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    method: str = "max_sharpe"
    lookback: int = 90


@app.post("/api/agents/analyze")
def api_agents_analyze(req: AnalyzeRequest) -> dict:
    """Chạy 4 AI agents → job; % tiến độ đếm theo agent_decisions trong DB."""
    job_id = uuid.uuid4().hex[:8]

    def count_decisions(symbol: str, timeframe: str) -> int:
        try:
            import sqlite3

            conn = sqlite3.connect(str(PROJECT_ROOT / "data" / "trading.db"))
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM agent_decisions WHERE symbol=? AND timeframe=?",
                    (symbol, timeframe),
                ).fetchone()
            finally:
                conn.close()
            return int(row[0] if row else 0)
        except Exception:  # noqa: BLE001
            return -1  # không xác định được → không dùng % thật

    def run(symbol: str, timeframe: str) -> dict:
        import threading as _t

        from trading_agent.agents.orchestrator import Orchestrator

        before = count_decisions(symbol, timeframe)
        orch = Orchestrator()
        holder: dict[str, object] = {}

        def _analyze() -> None:
            try:
                holder["report"] = orch.analyze(symbol=symbol, timeframe=timeframe)
            except Exception as exc:  # noqa: BLE001
                holder["error"] = exc

        t = _t.Thread(target=_analyze, daemon=True)
        t.start()
        # Poll DB — mỗi agent xong = 1 dòng decision mới (tối đa 4)
        while t.is_alive():
            t.join(timeout=1.5)
            delta = count_decisions(symbol, timeframe) - before
            if delta > 0:
                _set_progress(job_id, min(92, delta * 22), f"agent {min(delta, 4)}/4 đã xong")
        if "error" in holder:
            raise holder["error"]  # type: ignore[misc]

        report = holder["report"]  # type: ignore[assignment]
        agents = []
        for m in report.agent_messages:
            agents.append({
                "name": getattr(m, "agent_name", None) or m.role or "agent",
                "signal": m.signal,
                "confidence": float(m.confidence) if m.confidence is not None else None,
                "reasoning": m.reasoning,
                "details": {str(k): str(v)[:200] for k, v in (m.details or {}).items()},
            })
        d = report.final_decision
        return {
            "symbol": symbol, "timeframe": timeframe,
            "current_price": float(report.current_price),
            "decision": {
                "signal": d.signal, "confidence": float(d.confidence or 0),
                "reasoning": d.reasoning,
            },
            "agents": agents,
        }

    _spawn_job(job_id, run, symbol=req.symbol, timeframe=req.timeframe)
    return {"job_id": job_id}


@app.get("/api/data/datasets")
def api_data_datasets() -> dict:
    raw = PROJECT_ROOT / "data" / "raw"
    out = []
    if raw.exists():
        for p in sorted(raw.rglob("*.parquet")):
            rel = p.relative_to(raw)
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            out.append({"path": str(rel), "size": size})
    return {"datasets": out[-100:]}


@app.post("/api/data/fetch")
def api_data_fetch(req: FetchRequest) -> dict:
    job_id = uuid.uuid4().hex[:8]
    args = ["data", "fetch", req.symbol, "-t", req.timeframe, "-e", req.exchange]
    if req.since:
        args += ["-s", req.since]
    if req.save:
        args += ["--save"]
    _spawn_job(job_id, _run_cli_stream, run_job_id=job_id, args=args, timeout=600)
    return {"job_id": job_id}


@app.post("/api/portfolio/optimize")
def api_portfolio_optimize(req: OptimizeRequest) -> dict:
    job_id = uuid.uuid4().hex[:8]
    args = ["portfolio", "optimize", *req.symbols, "-m", req.method,
            "--lookback", str(req.lookback)]
    _spawn_job(job_id, _run_cli, args=args, timeout=600)
    return {"job_id": job_id}


@app.get("/api/logs/tail")
def api_logs_tail(lines: int = 300, source: str = "trading") -> dict:
    """Đọc N dòng cuối của file log (source=trading|server)."""
    path = (
        PROJECT_ROOT / "logs" / "trading_agent.log"
        if source == "trading"
        else PROJECT_ROOT / ".webui" / "server.log"
    )
    if not path.exists():
        return {"lines": [], "source": source, "path": str(path)}
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 200 * 8192))
            data = f.read().decode("utf-8", "replace")
        out = data.splitlines()[-max(20, min(lines, 1000)):]
    except OSError as exc:
        return {"lines": [], "source": source, "path": str(path), "error": str(exc)}
    return {"lines": out, "source": source, "path": str(path)}


@app.get("/api/system/daily")
def api_system_daily() -> dict:
    return _run_cli(["system", "daily"], timeout=90)


@app.get("/api/system/health")
def api_system_health() -> dict:
    return _run_cli(["system", "health"], timeout=180)


@app.get("/api/llm/cache-stats")
def api_llm_cache_stats() -> dict:
    return _run_cli(["llm", "cache-stats"], timeout=60)


@app.get("/api/meta/regimes")
def api_meta_regimes() -> dict:
    raw = PROJECT_ROOT / "data" / "raw"
    return _run_cli(["meta", "regimes", str(raw)], timeout=300)


@app.post("/api/execution/reset", dependencies=[Depends(_require_admin)])
def api_execution_reset(req: ResetRequest) -> dict:
    if req.confirm != "RESET_LOCAL_PAPER_STATE":
        raise HTTPException(status_code=400, detail="Explicit reset confirmation required")
    return _run_cli(["execution", "reset", "--yes"], timeout=120)


class BacktestCompareRequest(BaseModel):
    strategies: list[str] = ["ma_crossover", "rsi", "bbands", "enhanced_ma"]
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"


class PortfolioWeightsRequest(BaseModel):
    symbols: list[str] = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    method: str = "max_sharpe"
    lookback: int = 90


@app.post("/api/backtest/compare")
def api_backtest_compare(req: BacktestCompareRequest) -> dict:
    """Chạy backtest nhiều strategy cùng lúc → bảng metrics so sánh."""
    job_id = uuid.uuid4().hex[:8]

    def run(strategies: list[str], symbol: str, timeframe: str) -> dict:
        from trading_agent.backtest.engine import run_backtest

        rows = []
        errors = {}
        for i, name in enumerate(strategies):
            _set_progress(job_id, int(i / max(1, len(strategies)) * 88), f"backtest: {name} ({i + 1}/{len(strategies)})")
            try:
                r = run_backtest(name, symbol=symbol, timeframe=timeframe)
                rows.append({
                    "strategy": r.strategy_name,
                    "params": {str(k): str(v) for k, v in (r.params or {}).items()},
                    "total_return_pct": round(r.total_return_pct, 2),
                    "annualized_return_pct": round(r.annualized_return_pct, 2),
                    "sharpe_ratio": round(r.sharpe_ratio, 2),
                    "sortino_ratio": round(r.sortino_ratio, 2),
                    "max_drawdown_pct": round(r.max_drawdown_pct, 2),
                    "win_rate": round(r.win_rate, 3),
                    "profit_factor": round(r.profit_factor, 2),
                    "total_trades": r.total_trades,
                    "calmar_ratio": round(r.calmar_ratio, 2),
                    "avg_hold_bars": round(r.avg_hold_bars, 1),
                })
            except Exception as exc:  # noqa: BLE001
                errors[name] = str(exc)[:200]
        return {"rows": rows, "errors": errors, "symbol": symbol, "timeframe": timeframe}

    _spawn_job(job_id, run, strategies=req.strategies, symbol=req.symbol, timeframe=req.timeframe)
    return {"job_id": job_id}


@app.post("/api/portfolio/weights")
def api_portfolio_weights(req: PortfolioWeightsRequest) -> dict:
    """Tối ưu portfolio → trả weights JSON (cho pie chart)."""
    job_id = uuid.uuid4().hex[:8]

    def run(symbols: list[str], method: str, lookback: int) -> dict:
        import pandas as pd

        from trading_agent.config.loader import config
        from trading_agent.data.storage import load_ohlcv
        from trading_agent.exchanges.models import AssetClass, MarketType, Symbol
        from trading_agent.portfolio.portfolio_optimizer import (
            OptimizationConstraints,
            OptimizerMethod,
            PortfolioOptimizer,
        )

        returns_data, symbol_objs = {}, []
        for i, sym_str in enumerate(symbols):
            _set_progress(job_id, int(i / max(1, len(symbols)) * 60), f"tải dữ liệu {sym_str} ({i + 1}/{len(symbols)})")
            df = load_ohlcv(config.default_exchange, sym_str, "1d")
            close = pd.Series(df["close"].to_numpy())
            returns_data[sym_str] = close.pct_change().dropna()
            base, quote = sym_str.split("/") if "/" in sym_str else (sym_str, "USDT")
            symbol_objs.append(Symbol(base, quote, AssetClass.CRYPTO, MarketType.SPOT, "binance"))

        _set_progress(job_id, 68, "xây dựng covariance & universe")
        returns_df = pd.DataFrame(returns_data).dropna()
        current_weights = {s: 1.0 / len(symbol_objs) for s in symbol_objs}
        constraints = OptimizationConstraints(current_weights=current_weights)
        optimizer = PortfolioOptimizer(
            method=OptimizerMethod(method), constraints=constraints, lookback=lookback,
        ).set_universe(symbol_objs, returns_df, current_weights)

        _set_progress(job_id, 85, f"tối ưu {method}…")
        try:
            result = optimizer.optimize()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:300]}

        if not result.success:
            return {"error": result.message}

        return {
            "method": method,
            "symbols": [f"{s.base}/{s.quote}" for s in result.weights],
            "weights": [round(float(w), 4) for w in result.weights.values()],
            "expected_return": round(float(result.expected_return), 4),
            "expected_volatility": round(float(result.expected_volatility), 4),
            "sharpe_ratio": round(float(result.sharpe_ratio), 3),
            "diversification_ratio": round(float(result.diversification_ratio), 3),
            "var_95": round(float(result.var_95), 4),
            "cvar_95": round(float(result.cvar_95), 4),
        }

    _spawn_job(job_id, run, symbols=req.symbols, method=req.method, lookback=req.lookback)
    return {"job_id": job_id}


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
@app.get("/health")
def health() -> dict:
    return {"status": "ok", "time": time.time()}


if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        f = DIST_DIR / full_path
        if full_path and f.is_file():
            return FileResponse(f)
        return FileResponse(DIST_DIR / "index.html")
