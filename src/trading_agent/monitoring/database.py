"""
Trade Database — SQLite for persistent trade history, equity snapshots,
and agent decision logs.

Schema
------
trades:         Every filled/closed trade
equity_snapshots: Periodic portfolio snapshots (P&L tracking)
agent_decisions: Every agent analysis result
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trading_agent.log_config import get_logger

logger = get_logger(__name__)

# Default path relative to project root
DEFAULT_DB_PATH = "data/trading.db"

# Thread-local connections for safety
_local = threading.local()


def _get_connection(db_path: str) -> sqlite3.Connection:
    """Get a thread-local SQLite connection."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(db_path)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        TEXT UNIQUE NOT NULL,
    symbol          TEXT NOT NULL,
    side            TEXT NOT NULL,          -- buy / sell
    entry_price     REAL,
    exit_price      REAL,
    amount          REAL NOT NULL,
    entry_time      TEXT NOT NULL,          -- ISO 8601
    exit_time       TEXT,
    entry_order_id  TEXT,
    exit_order_id   TEXT,
    pnl             REAL,                   -- realized P&L (quote currency)
    pnl_pct         REAL,                   -- realized P&L %
    fee             REAL DEFAULT 0,
    reason          TEXT,                   -- what closed this trade
    strategy        TEXT DEFAULT 'agent',   -- agent / ma_crossover / rsi / bbands
    tags            TEXT DEFAULT '[]',      -- JSON list
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,           -- ISO 8601
    equity          REAL NOT NULL,
    cash            REAL NOT NULL,
    position_value  REAL NOT NULL,
    unrealized_pnl  REAL DEFAULT 0,
    drawdown_pct    REAL DEFAULT 0,          -- from peak
    peak_equity     REAL,                    -- running peak
    symbol          TEXT                     -- NULL = portfolio total
);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    timeframe       TEXT,
    agent_name      TEXT NOT NULL,           -- technical / sentiment / risk / trader
    signal          TEXT,                    -- BUY / SELL / HOLD
    confidence      REAL,
    reasoning       TEXT,                    -- full text
    price           REAL,
    metadata_json   TEXT DEFAULT '{}'        -- extra fields
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_entry ON trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_equity_ts ON equity_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_ts ON agent_decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON agent_decisions(symbol);
"""


def init_db(db_path: str = DEFAULT_DB_PATH) -> str:
    """Initialize database schema. Returns the db path."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_connection(str(path))
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    logger.info("Database initialized: %s", path)
    return str(path)


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


def insert_trade(
    trade_id: str,
    symbol: str,
    side: str,
    amount: float,
    entry_price: float | None = None,
    entry_time: str | None = None,
    entry_order_id: str | None = None,
    strategy: str = "agent",
    tags: list[str] | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Insert a new trade record."""
    conn = _get_connection(db_path)
    conn.execute(
        """
        INSERT INTO trades (trade_id, symbol, side, amount, entry_price,
                            entry_time, entry_order_id, strategy, tags)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade_id,
            symbol,
            side,
            amount,
            entry_price,
            entry_time or _now_iso(),
            entry_order_id,
            strategy,
            json.dumps(tags or []),
        ),
    )
    conn.commit()


def close_trade(
    trade_id: str,
    exit_price: float,
    exit_time: str | None = None,
    exit_order_id: str | None = None,
    pnl: float | None = None,
    pnl_pct: float | None = None,
    fee: float = 0.0,
    reason: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Close a trade with exit details and realized P&L."""
    conn = _get_connection(db_path)
    conn.execute(
        """
        UPDATE trades
        SET exit_price = ?, exit_time = ?, exit_order_id = ?,
            pnl = ?, pnl_pct = ?, fee = ?, reason = ?
        WHERE trade_id = ?
        """,
        (
            exit_price,
            exit_time or _now_iso(),
            exit_order_id,
            pnl,
            pnl_pct,
            fee,
            reason,
            trade_id,
        ),
    )
    conn.commit()


def get_trades(
    symbol: str | None = None,
    limit: int = 100,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Query trades, newest first."""
    conn = _get_connection(db_path)
    if symbol:
        rows = conn.execute(
            "SELECT * FROM trades WHERE symbol = ? ORDER BY entry_time DESC LIMIT ?",
            (symbol, limit),
        )
    else:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY entry_time DESC LIMIT ?", (limit,)
        )
    return [dict(r) for r in rows.fetchall()]


def get_trade_stats(
    symbol: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Aggregate trade statistics."""
    conn = _get_connection(db_path)
    where = "WHERE exit_price IS NOT NULL"
    params: list[str] = []
    if symbol:
        where += " AND symbol = ?"
        params.append(symbol)

    row = conn.execute(
        f"""
        SELECT
            COUNT(*)                                          AS total_trades,
            COALESCE(SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END), 0) AS wins,
            COALESCE(SUM(CASE WHEN pnl < 0 THEN 1 ELSE 0 END), 0) AS losses,
            ROUND(COALESCE(AVG(CASE WHEN pnl > 0 THEN pnl END), 0), 2) AS avg_win,
            ROUND(COALESCE(AVG(CASE WHEN pnl < 0 THEN pnl END), 0), 2) AS avg_loss,
            ROUND(COALESCE(SUM(pnl), 0), 2)                       AS total_pnl,
            ROUND(COALESCE(AVG(pnl_pct), 0), 4)                   AS avg_pnl_pct,
            ROUND(COALESCE(SUM(fee), 0), 2)                        AS total_fees
        FROM trades
        {where}
        """,
        params,
    ).fetchone()

    stats = dict(row) if row else {}
    if stats.get("total_trades", 0) > 0:
        stats["win_rate"] = round(stats["wins"] / stats["total_trades"], 4)
    else:
        stats["win_rate"] = 0.0
    if stats.get("avg_loss", 0) != 0 and stats.get("avg_win", 0) != 0:
        stats["profit_factor"] = round(abs(stats["avg_win"] / stats["avg_loss"]), 2)
    else:
        stats["profit_factor"] = 0.0
    return stats


# ---------------------------------------------------------------------------
# Equity snapshots
# ---------------------------------------------------------------------------


def save_equity_snapshot(
    equity: float,
    cash: float,
    position_value: float,
    unrealized_pnl: float = 0.0,
    drawdown_pct: float = 0.0,
    peak_equity: float | None = None,
    symbol: str | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Record a portfolio snapshot."""
    conn = _get_connection(db_path)
    conn.execute(
        """
        INSERT INTO equity_snapshots
            (timestamp, equity, cash, position_value, unrealized_pnl,
             drawdown_pct, peak_equity, symbol)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _now_iso(),
            equity,
            cash,
            position_value,
            unrealized_pnl,
            drawdown_pct,
            peak_equity,
            symbol,
        ),
    )
    conn.commit()


def get_equity_curve(
    limit: int = 5000,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Get equity curve data for charting."""
    conn = _get_connection(db_path)
    rows = conn.execute(
        """
        SELECT timestamp, equity, cash, position_value,
               unrealized_pnl, drawdown_pct
        FROM equity_snapshots
        WHERE symbol IS NULL
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(r) for r in rows.fetchall()]


# ---------------------------------------------------------------------------
# Agent decisions
# ---------------------------------------------------------------------------


def save_agent_decision(
    symbol: str,
    agent_name: str,
    signal: str | None,
    confidence: float | None,
    reasoning: str,
    price: float | None = None,
    timeframe: str | None = None,
    metadata: dict | None = None,
    db_path: str = DEFAULT_DB_PATH,
) -> None:
    """Record an agent's analysis result."""
    conn = _get_connection(db_path)
    conn.execute(
        """
        INSERT INTO agent_decisions
            (timestamp, symbol, timeframe, agent_name, signal,
             confidence, reasoning, price, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _now_iso(),
            symbol,
            timeframe,
            agent_name,
            signal,
            confidence,
            reasoning,
            price,
            json.dumps(metadata or {}),
        ),
    )
    conn.commit()


def get_agent_decisions(
    symbol: str | None = None,
    limit: int = 50,
    db_path: str = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Query recent agent decisions."""
    conn = _get_connection(db_path)
    if symbol:
        rows = conn.execute(
            """SELECT * FROM agent_decisions
               WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?""",
            (symbol, limit),
        )
    else:
        rows = conn.execute(
            "SELECT * FROM agent_decisions ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
    return [dict(r) for r in rows.fetchall()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def close(db_path: str = DEFAULT_DB_PATH) -> None:
    """Close the connection for the current thread."""
    if hasattr(_local, "conn") and _local.conn:
        _local.conn.close()
        _local.conn = None
        logger.debug("Database connection closed")
