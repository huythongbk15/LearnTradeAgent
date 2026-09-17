"""
Research Memory — persistent store for LLM-produced market context.

Stores MarketContext artifacts produced by the LLM enrichment layer so that:
1. Historical context can be replayed in deterministic backtests (no LLM calls).
2. A/B test results (deterministic vs LLM-enriched) are auditable.
3. Regime attribution is tracked across strategy evaluations.

Uses SQLite with WAL mode for concurrent access. Schema:

    CREATE TABLE market_contexts (
        symbol        TEXT,
        timeframe     TEXT,
        bar_timestamp TEXT,         -- ISO 8601
        context_json  TEXT,         -- serialized MarketContext
        deterministic INTEGER,      -- 1 if LLM was used, 0 if fallback
        created_at    TEXT,         -- when this row was written
        PRIMARY KEY (symbol, timeframe, bar_timestamp, deterministic)
    )

**A/B test flow:**
1. First run: LLM backtest mode ENABLED → MarketContext produced by LLM → stored
2. Second run: LLM backtest mode DISABLED → read MarketContext from SQLite →
   apply deterministically (no LLM calls during evaluation)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from trading_agent.llm.context_enrichment import MarketContext

logger = logging.getLogger(__name__)


class ResearchMemory:
    """Persistent store for LLM-produced MarketContext artifacts.

    Two modes:
    - ``write``: store MarketContext from active LLM enrichment layer
    - ``replay``: read stored MarketContext for deterministic A/B evaluation

    Disable with ``RESEARCH_MEMORY_DB=/dev/null`` (writes go nowhere)
    or ``RESEARCH_MEMORY_ENABLED=0`` (layer is bypassed entirely).
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        env_path = db_path or (
            Path.cwd() / "data" / "research_memory.sqlite3"
        )
        self._db_path = Path(env_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS market_contexts (
                    symbol        TEXT NOT NULL,
                    timeframe     TEXT NOT NULL,
                    bar_timestamp TEXT NOT NULL,
                    context_json  TEXT NOT NULL,
                    deterministic INTEGER NOT NULL DEFAULT 0,
                    created_at    TEXT NOT NULL,
                    PRIMARY KEY (symbol, timeframe, bar_timestamp, deterministic)
                );
                CREATE INDEX IF NOT EXISTS idx_mc_symbol_tf
                    ON market_contexts(symbol, timeframe);
                CREATE INDEX IF NOT EXISTS idx_mc_timestamp
                    ON market_contexts(bar_timestamp);
            """)

    def store(
        self,
        symbol: str,
        timeframe: str,
        bar_timestamp: datetime,
        context: MarketContext,
        deterministic: bool = False,
    ) -> None:
        """Store a MarketContext for later replay / A/B comparison."""
        ctx_dict = context.to_dict()
        ctx_dict["regime_tags"] = context.regime_tags
        ctx_dict["anomaly_flags"] = context.anomaly_flags
        ctx_dict["confidence_adjustment"] = context.confidence_adjustment
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO market_contexts
                     (symbol, timeframe, bar_timestamp, context_json, deterministic, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    symbol,
                    timeframe,
                    bar_timestamp.isoformat(),
                    json.dumps(ctx_dict, default=str),
                    1 if deterministic else 0,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def retrieve(
        self,
        symbol: str,
        timeframe: str,
        bar_timestamp: datetime,
        deterministic: bool = False,
    ) -> MarketContext | None:
        """Retrieve a stored MarketContext for A/B replay.

        Args:
            deterministic: If True, retrieve deterministic-fallback contexts.
                          If False, retrieve LLM-produced contexts.
        """
        with self._connect() as conn:
            row = conn.execute(
                """SELECT context_json FROM market_contexts
                   WHERE symbol = ? AND timeframe = ? AND bar_timestamp = ? AND deterministic = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (symbol, timeframe, bar_timestamp.isoformat(), 1 if deterministic else 0),
            ).fetchone()

        if row is None:
            return None

        ctx_dict = json.loads(row[0])
        return MarketContext(
            regime_tags=ctx_dict.get("regime_tags", {}),
            anomaly_flags=ctx_dict.get("anomaly_flags", []),
            cross_asset_signals=ctx_dict.get("cross_asset_signals", {}),
            confidence_adjustment=ctx_dict.get("confidence_adjustment", 1.0),
            reasoning=ctx_dict.get("reasoning", ""),
            details=ctx_dict.get("details", {}),
        )

    def retrieve_range(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        deterministic: bool = False,
    ) -> list[tuple[datetime, MarketContext]]:
        """Retrieve all MarketContexts in [start, end] for A/B comparison."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT bar_timestamp, context_json FROM market_contexts
                   WHERE symbol = ? AND timeframe = ? AND bar_timestamp >= ? AND bar_timestamp <= ?
                     AND deterministic = ?
                   ORDER BY bar_timestamp""",
                (
                    symbol,
                    timeframe,
                    start.isoformat(),
                    end.isoformat(),
                    1 if deterministic else 0,
                ),
            ).fetchall()

        results: list[tuple[datetime, MarketContext]] = []
        for ts_str, ctx_json in rows:
            ctx_dict = json.loads(ctx_json)
            ctx = MarketContext(
                regime_tags=ctx_dict.get("regime_tags", {}),
                anomaly_flags=ctx_dict.get("anomaly_flags", []),
                cross_asset_signals=ctx_dict.get("cross_asset_signals", {}),
                confidence_adjustment=ctx_dict.get("confidence_adjustment", 1.0),
                reasoning=ctx_dict.get("reasoning", ""),
                details=ctx_dict.get("details", {}),
            )
            results.append((datetime.fromisoformat(ts_str), ctx))

        return results

    def count(self, symbol: str | None = None, deterministic: bool | None = None) -> int:
        """Count stored contexts (optionally filtered)."""
        with self._connect() as conn:
            if symbol is None and deterministic is None:
                row = conn.execute("SELECT COUNT(*) FROM market_contexts").fetchone()
            elif symbol is None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM market_contexts WHERE deterministic = ?",
                    (1 if deterministic else 0,),
                ).fetchone()
            elif deterministic is None:
                row = conn.execute(
                    "SELECT COUNT(*) FROM market_contexts WHERE symbol = ?",
                    (symbol,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM market_contexts WHERE symbol = ? AND deterministic = ?",
                    (symbol, 1 if deterministic else 0),
                ).fetchone()
            return row[0] if row else 0

    def compare_runs(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        """Compare deterministic vs LLM runs for A/B test analysis.

        Returns:
            Dict with:
            - total_bars: int
            - llms_bars: int (how many bars have LLM context)
            - deterministic_bars: int
            - confidence_adjustments: list of (bar_ts, adjustment)
            - anomaly_coverage: dict mapping anomaly_flag → count
        """
        llm_contexts = self.retrieve_range(symbol, timeframe, start, end, deterministic=False)
        det_contexts = self.retrieve_range(symbol, timeframe, start, end, deterministic=True)

        anomaly_counts: dict[str, int] = {}
        adjustments: list[tuple[datetime, float]] = []

        for ts, ctx in llm_contexts:
            adjustments.append((ts, ctx.confidence_adjustment))
            for flag in ctx.anomaly_flags:
                anomaly_counts[flag] = anomaly_counts.get(flag, 0) + 1

        return {
            "total_bars": len(llm_contexts) + len(det_contexts),
            "llm_context_bars": len(llm_contexts),
            "deterministic_bars": len(det_contexts),
            "confidence_adjustments": adjustments,
            "anomaly_coverage": anomaly_counts,
            "avg_adjustment": (
                sum(a for _, a in adjustments) / len(adjustments) if adjustments else 0.0
            ),
        }
