"""SelectionAudit — immutable audit trail for strategy routing decisions.

Every call to :meth:`StrategyTournament.route` is recorded with full context:
regime posterior, chosen strategy, confidence adjustment, anomaly flags, and
the reasoning chain.  Entries are stored in a SQLite journal for replay and
regulatory traceability.

Design principles:
- **Immutable**: entries cannot be modified after creation.
- **Deterministic IDs**: SHA-256 of the (symbol, timeframe, observed_at, decision_id) tuple.
- **Zero LLM calls**: the audit store is purely recording/replaying.
- **Low overhead**: append-only writes, batched when possible.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from trading_agent.authority.adaptive_router import RoutingDecision
from trading_agent.ml.regime_detection import RegimePosterior

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEntry:
    """Immutable record of one routing decision."""

    entry_id: str
    symbol: str
    timeframe: str
    observed_at: datetime
    decision_id: str
    chosen_strategy_id: str | None
    incumbent_strategy_id: str | None
    challenger_strategy_id: str | None
    reason: str
    allow_new_exposure: bool
    exposure_multiplier: float
    posterior_entropy: float
    posterior_fingerprint: str
    regime_tags: dict[str, Any]
    anomaly_flags: list[str]
    confidence_adjustment: float
    cross_asset_signals: dict[str, Any]
    reasoning_snippet: str
    shadow_sharpe: float | None = None
    shadow_sharpe_delta_vs_incumbent: float | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, sort_keys=True)


class SelectionAudit:
    """SQLite-backed audit trail for tournament routing decisions.

    Usage::

        audit = SelectionAudit(Path("/data/tournament_audit.sqlite3"))
        audit.append(decision, posterior, market_ctx=...)

        entries = audit.query(
            symbol="BTC/USDT",
            start=datetime(2024, 7, 1, tzinfo=UTC),
        )
    """

    def __init__(self, db_path: Path | str) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS entries (
                entry_id           TEXT PRIMARY KEY,
                symbol             TEXT NOT NULL,
                timeframe          TEXT NOT NULL,
                observed_at        TEXT NOT NULL,
                decision_id        TEXT NOT NULL,
                chosen_strategy_id TEXT,
                incumbent_strategy_id TEXT,
                challenger_strategy_id TEXT,
                reason             TEXT NOT NULL,
                allow_new_exposure INTEGER NOT NULL,
                exposure_multiplier  REAL NOT NULL,
                posterior_entropy    REAL NOT NULL,
                posterior_fingerprint TEXT NOT NULL,
                regime_tags        TEXT NOT NULL,
                anomaly_flags      TEXT NOT NULL,
                confidence_adjustment REAL NOT NULL,
                cross_asset_signals TEXT NOT NULL,
                reasoning_snippet   TEXT NOT NULL,
                shadow_sharpe       REAL,
                shadow_sharpe_delta REAL,
                created_at         TEXT NOT NULL DEFAULT (datetime('now', 'utc'))
            );
            CREATE INDEX IF NOT EXISTS idx_entries_symbol_tf ON entries(symbol, timeframe);
            CREATE INDEX IF NOT EXISTS idx_entries_observed_at ON entries(observed_at);
            CREATE INDEX IF NOT EXISTS idx_entries_chosen ON entries(chosen_strategy_id);
            """
        )
        self._conn.commit()

    def append(
        self,
        decision: RoutingDecision,
        posterior: RegimePosterior,
        *,
        regime_tags: dict[str, Any] | None = None,
        anomaly_flags: list[str] | None = None,
        confidence_adjustment: float = 1.0,
        cross_asset_signals: dict[str, Any] | None = None,
        reasoning_snippet: str = "",
        shadow_sharpe: float | None = None,
        shadow_sharpe_delta_vs_incumbent: float | None = None,
    ) -> AuditEntry:
        """Record one routing decision with full context.

        Parameters
        ----------
        decision:
            The RoutingDecision from AdaptiveStrategyRouter or StrategyTournament.
        posterior:
            RegimePosterior that was routed.
        regime_tags / anomaly_flags / confidence_adjustment:
            Optional LLM enrichment metadata from MarketContext.
            These are **advisory** — they do not affect the routing decision.
        shadow_sharpe / shadow_sharpe_delta_vs_incumbent:
            Optional shadow-mode performance metrics.
        """
        regime_tags = regime_tags or {}
        anomaly_flags = anomaly_flags or []
        cross_asset_signals = cross_asset_signals or {}

        entry_id = self._compute_id(
            decision.symbol, decision.timeframe,
            decision.observed_at, decision.decision_id,
        )

        entry = AuditEntry(
            entry_id=entry_id,
            symbol=decision.symbol,
            timeframe=decision.timeframe,
            observed_at=decision.observed_at,
            decision_id=decision.decision_id,
            chosen_strategy_id=decision.chosen_strategy_id,
            incumbent_strategy_id=decision.incumbent_strategy_id,
            challenger_strategy_id=decision.challenger_strategy_id,
            reason=decision.reason,
            allow_new_exposure=decision.allow_new_exposure,
            exposure_multiplier=decision.exposure_multiplier,
            posterior_entropy=posterior.normalized_entropy,
            posterior_fingerprint=decision.posterior_fingerprint,
            regime_tags=regime_tags,
            anomaly_flags=anomaly_flags,
            confidence_adjustment=confidence_adjustment,
            cross_asset_signals=cross_asset_signals,
            reasoning_snippet=reasoning_snippet,
            shadow_sharpe=shadow_sharpe,
            shadow_sharpe_delta_vs_incumbent=shadow_sharpe_delta_vs_incumbent,
        )

        self._insert(entry)
        return entry

    def _compute_id(
        self, symbol: str, timeframe: str, observed_at: datetime,
        decision_id: str,
    ) -> str:
        payload = f"{symbol}|{timeframe}|{observed_at.isoformat()}|{decision_id}"
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def _insert(self, entry: AuditEntry) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO entries (
                entry_id, symbol, timeframe, observed_at, decision_id,
                chosen_strategy_id, incumbent_strategy_id, challenger_strategy_id,
                reason, allow_new_exposure, exposure_multiplier,
                posterior_entropy, posterior_fingerprint,
                regime_tags, anomaly_flags, confidence_adjustment,
                cross_asset_signals, reasoning_snippet,
                shadow_sharpe, shadow_sharpe_delta
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.entry_id,
                entry.symbol,
                entry.timeframe,
                entry.observed_at.isoformat(),
                entry.decision_id,
                entry.chosen_strategy_id,
                entry.incumbent_strategy_id,
                entry.challenger_strategy_id,
                entry.reason,
                int(entry.allow_new_exposure),
                entry.exposure_multiplier,
                entry.posterior_entropy,
                entry.posterior_fingerprint,
                json.dumps(entry.regime_tags, default=str, sort_keys=True),
                json.dumps(entry.anomaly_flags, default=str, sort_keys=True),
                entry.confidence_adjustment,
                json.dumps(entry.cross_asset_signals, default=str, sort_keys=True),
                entry.reasoning_snippet,
                entry.shadow_sharpe,
                entry.shadow_sharpe_delta_vs_incumbent,
            ),
        )
        self._conn.commit()

    def query(
        self,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        chosen_strategy_id: str | None = None,
        min_posterior_entropy: float | None = None,
        limit: int = 1000,
    ) -> list[AuditEntry]:
        """Query audit entries with optional filters."""
        where: list[str] = []
        params: list[Any] = []

        if symbol is not None:
            where.append("symbol = ?")
            params.append(symbol)
        if timeframe is not None:
            where.append("timeframe = ?")
            params.append(timeframe)
        if start is not None:
            where.append("observed_at >= ?")
            params.append(start.isoformat())
        if end is not None:
            where.append("observed_at <= ?")
            params.append(end.isoformat())
        if chosen_strategy_id is not None:
            where.append("chosen_strategy_id = ?")
            params.append(chosen_strategy_id)
        if min_posterior_entropy is not None:
            where.append("posterior_entropy >= ?")
            params.append(min_posterior_entropy)

        where_clause = "WHERE " + " AND ".join(where) if where else ""
        rows = self._conn.execute(
            f"SELECT * FROM entries {where_clause} ORDER BY observed_at ASC LIMIT ?",
            [*params, limit],
        ).fetchall()

        return [self._row_to_entry(row) for row in rows]

    def _row_to_entry(self, row: sqlite3.Row) -> AuditEntry:
        cols = [d[0] for d in self._conn.execute("SELECT * FROM entries LIMIT 1").description]
        data = dict(zip(cols, row))
        return AuditEntry(
            entry_id=data["entry_id"],
            symbol=data["symbol"],
            timeframe=data["timeframe"],
            observed_at=datetime.fromisoformat(data["observed_at"]),
            decision_id=data["decision_id"],
            chosen_strategy_id=data["chosen_strategy_id"],
            incumbent_strategy_id=data["incumbent_strategy_id"],
            challenger_strategy_id=data["challenger_strategy_id"],
            reason=data["reason"],
            allow_new_exposure=bool(data["allow_new_exposure"]),
            exposure_multiplier=float(data["exposure_multiplier"]),
            posterior_entropy=float(data["posterior_entropy"]),
            posterior_fingerprint=data["posterior_fingerprint"],
            regime_tags=json.loads(data["regime_tags"]),
            anomaly_flags=json.loads(data["anomaly_flags"]),
            confidence_adjustment=float(data["confidence_adjustment"]),
            cross_asset_signals=json.loads(data["cross_asset_signals"]),
            reasoning_snippet=data["reasoning_snippet"],
            shadow_sharpe=data["shadow_sharpe"],
            shadow_sharpe_delta_vs_incumbent=data["shadow_sharpe_delta"],
        )

    def close(self) -> None:
        self._conn.close()

    def __del__(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
