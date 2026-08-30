#!/usr/bin/env python3
"""
Full System Real-Time Simulation — chạy toàn bộ hệ thống như thật trên dữ liệu lịch sử.

Mô phỏng đầy đủ pipeline production:
  data → canonical strategy (fixed or injected adaptive router) → ExecutionEngine (paper) → RiskController

Cách dùng:
  python3 scripts/full_system_backtest.py                 # full 3 năm
  python3 scripts/full_system_backtest.py --freq 1        # phân tích mỗi bar (1h)
  python3 scripts/full_system_backtest.py --fresh         # reset paper state trước khi chạy
  python3 scripts/full_system_backtest.py --strategy-artifact path/to/artifact.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Do not disable logging process-wide at import time.  The simulator is also
# imported as a library by tournament/WFO runs, where INFO-level decision trace
# records are audit evidence.  CLI noise can be controlled by normal handler
# levels without suppressing those records globally.

# TẮT LLM — dùng rule-based fallback cho tốc độ
os.environ["USE_LLM"] = os.environ.get("USE_LLM", "false")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import polars as pl

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.promotion_store import PromotionStateStore
from trading_agent.backtest.reporting import (
    DataQualityReport,
    GapPolicy,
    assess_ohlcv,
    calculate_cost_attribution,
    calculate_performance_metrics,
    calendar_returns,
    fingerprint_payload,
    fixed_allocation_buy_and_hold,
)
from trading_agent.config.loader import config
from trading_agent.data.storage import load_ohlcv
from trading_agent.execution import risk_controller as rc_module
from trading_agent.execution.canonical.market_observation import (
    EnrichedMarketObservation,
)
from trading_agent.execution.canonical.instrument_registry import (
    get_instrument_rules,
)
from trading_agent.execution.engine import ExecutionEngine
from trading_agent.execution.risk_controller import RiskController
from trading_agent.research.artifact import (
    PersistentArtifactStore,
    StrategyArtifact,
    canonical_params,
    sha256_hex,
)
from trading_agent.research.promotion import ResearchPromotionEvent, ResearchStage
from trading_agent.strategies.canonical.candidates import (
    FIRST_WAVE_DESCRIPTORS,
    build_legacy_candidate,
)


class _SimClock(datetime):
    """Đồng hồ giả lập: datetime.now(UTC) trả về timestamp của bar hiện tại."""

    current: datetime | None = None

    @classmethod
    def now(cls, tz=None):  # noqa: D102
        return cls.current or datetime.now(tz or UTC)


# ── Config ────────────────────────────────────────────────────────────
SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
EXCHANGE = os.getenv("EXCHANGE", "binance")
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "10000"))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "0.15"))
DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "0.08"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.50"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.05"))
COOLDOWN_HOURS = float(os.getenv("COOLDOWN_HOURS", "24"))
MAX_POS_SIZE_PCT = float(os.getenv("MAX_POS_SIZE_PCT", "0.25"))

# Strategy params (tuned from parameter sweep)
FAST_MA = int(os.getenv("FAST_MA", "15"))
SLOW_MA = int(os.getenv("SLOW_MA", "50"))
ADX_THRESHOLD = float(os.getenv("ADX_THRESHOLD", "40"))
ATR_SL_MULT = float(os.getenv("ATR_SL_MULT", "2.0"))
ATR_TP_MULT = float(os.getenv("ATR_TP_MULT", "3.0"))


def _timeframe_delta(timeframe: str) -> timedelta:
    """Convert a compact timeframe such as 15m/1h/4h/1d to a duration."""
    if len(timeframe) < 2 or not timeframe[:-1].isdigit():
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    amount = int(timeframe[:-1])
    unit = timeframe[-1].lower()
    if amount <= 0 or unit not in {"m", "h", "d"}:
        raise ValueError(f"Unsupported timeframe: {timeframe!r}")
    return {
        "m": timedelta(minutes=amount),
        "h": timedelta(hours=amount),
        "d": timedelta(days=amount),
    }[unit]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _load_strategy_artifact(path: str | Path) -> StrategyArtifact:
    """Load and integrity-check a persisted StrategyArtifact manifest.

    The CLI must consume the exact artifact produced by research/WFO.  It is
    therefore not enough to read ``strategy_name`` and silently rebuild the
    parameters: the content-addressed id, code hash and parameter hash are
    checked before the simulator is allowed to start.
    """

    manifest_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid strategy artifact manifest: {manifest_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("strategy artifact manifest must be a JSON object")
    metadata = payload.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("strategy artifact metadata must be a JSON object")
    try:
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        artifact = StrategyArtifact(
            strategy_name=str(payload["strategy_name"]),
            code_sha=str(payload["code_sha"]),
            data_manifest_sha=str(payload["data_manifest_sha"]),
            parameter_hash=str(payload["parameter_hash"]),
            execution_model_version=str(payload.get("execution_model_version", "")),
            framework_version=str(payload.get("framework_version", "")),
            created_at=created_at,
            prev_artifact_id=payload.get("prev_artifact_id"),
            metadata=dict(metadata),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"strategy artifact manifest is incomplete: {exc}") from exc
    supplied_id = payload.get("artifact_id")
    if supplied_id is not None and str(supplied_id) != artifact.artifact_id:
        raise ValueError(
            "strategy artifact integrity failure: artifact_id does not match content"
        )
    return artifact


def _git_commit_sha() -> str:
    """Resolve the exact source commit for golden-run provenance.

    Precedence: GIT_COMMIT_SHA env override > `git rev-parse HEAD`.
    Fails open to "unknown" (e.g. running from a tarball) but never fabricates.
    """
    override = os.getenv("GIT_COMMIT_SHA", "").strip()
    if override:
        return override
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


class FullSystemSimulator:
    def __init__(
        self,
        fresh: bool = False,
        symbol: str | None = None,
        timeframe: str | None = None,
        state_dir: str | Path | None = None,
        report_path: str | Path | None = None,
        run_id: str | None = None,
        allow_new_exposure: bool = True,
        state_flush_bars: int = 100,
        data_manifest_id: str | None = None,
        gap_policy: GapPolicy = "record",
        strategy_name: str = "enhanced_ma",
        strategy_params_override: dict[str, object] | None = None,
        signal_series: list[int] | None = None,
        commission: float | None = None,
        slippage: float | None = None,
        strategy_runtime: object | None = None,
        runtime_factory=None,
        strategy_artifact: StrategyArtifact | None = None,
        adaptive_router=None,
        adaptive_posterior_provider=None,
        adaptive_runtime_provider=None,
    ):
        resolved_symbol = symbol or os.getenv("SYMBOL", SYMBOL)
        resolved_timeframe = timeframe or os.getenv("TIMEFRAME", TIMEFRAME)
        if not resolved_symbol or not resolved_timeframe:
            raise ValueError("symbol and timeframe are required")
        self.symbol: str = resolved_symbol
        self.timeframe: str = resolved_timeframe
        self.exchange = EXCHANGE
        self.timeframe_delta = _timeframe_delta(self.timeframe)
        self.gap_policy = gap_policy
        self.commit_sha = _git_commit_sha()
        adaptive_parts = (
            adaptive_router,
            adaptive_posterior_provider,
            adaptive_runtime_provider,
        )
        if any(part is not None for part in adaptive_parts) and not all(
            part is not None for part in adaptive_parts
        ):
            raise ValueError(
                "adaptive_router, adaptive_posterior_provider and "
                "adaptive_runtime_provider must be provided together"
            )
        self._adaptive_router = adaptive_router
        self._adaptive_posterior_provider = adaptive_posterior_provider
        self._adaptive_runtime_provider = adaptive_runtime_provider
        self.adaptive_enabled = adaptive_router is not None
        self.routing_decisions: list[dict[str, object]] = []

        safe_symbol = self.symbol.replace("/", "_").replace(":", "_")
        resolved_run_id = (
            run_id
            or os.getenv("BACKTEST_RUN_ID")
            or datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
        )
        self.run_id = resolved_run_id
        if state_dir is None:
            self.run_dir = (
                ROOT
                / "data"
                / "backtests"
                / "full_system"
                / resolved_run_id
                / safe_symbol
            )
            self.state_dir = self.run_dir / "execution"
        else:
            self.state_dir = Path(state_dir).resolve()
            self.run_dir = self.state_dir.parent
        self.report_path = (
            Path(report_path).resolve()
            if report_path is not None
            else self.run_dir / "report.json"
        )

        if fresh and self.state_dir.exists() and any(self.state_dir.iterdir()):
            backup = self.state_dir.with_name(
                f"{self.state_dir.name}.bak-"
                f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S_%fZ')}"
            )
            self.state_dir.rename(backup)
            print(f"🗑  Paper state reset (backup → {backup.name})")
        self.state_dir.mkdir(parents=True, exist_ok=True)

        data_path = (
            config.storage_abs_path
            / self.exchange
            / safe_symbol
            / f"{self.timeframe}.parquet"
        )
        self.data_manifest_id = data_manifest_id or _sha256_file(data_path)

        # Load data
        print(f"📥 Loading {self.symbol} {self.timeframe} from {self.exchange}...")
        source_df = load_ohlcv(self.exchange, self.symbol, self.timeframe)
        self.source_data_quality = assess_ohlcv(
            source_df,
            expected_interval=self.timeframe_delta,
            gap_policy=self.gap_policy,
        )
        self.df = source_df.sort("timestamp")
        print(
            f"   {self.df.height} bars: {self.df['timestamp'].min()} → {self.df['timestamp'].max()}"
        )

        # Initialize strategy (canonical tournament cells may override both
        # the artifact identity and the signal series; defaults keep the
        # historical enhanced_ma behaviour byte-identical).
        if strategy_artifact is not None:
            artifact_symbol = strategy_artifact.metadata.get("symbol")
            artifact_timeframe = strategy_artifact.metadata.get("timeframe")
            if artifact_symbol not in (None, self.symbol):
                raise ValueError("strategy artifact symbol does not match simulator symbol")
            if artifact_timeframe not in (None, self.timeframe):
                raise ValueError(
                    "strategy artifact timeframe does not match simulator timeframe"
                )
            strategy_name = strategy_artifact.strategy_name
            artifact_params = strategy_artifact.metadata.get("parameters", {})
            if not isinstance(artifact_params, Mapping):
                raise ValueError("strategy artifact metadata.parameters must be an object")
            if strategy_params_override is not None and dict(strategy_params_override) != dict(
                artifact_params
            ):
                raise ValueError(
                    "strategy params override conflicts with the supplied strategy artifact"
                )
            strategy_params_override = dict(artifact_params)
        self.strategy_name = strategy_name
        self.strategy_params: dict[str, object] = {
            "target_exposure_pct": MAX_POS_SIZE_PCT,
        }
        if strategy_name in {"enhanced_ma", "ma_adx", "ma_vol_target"}:
            self.strategy_params.update(
                {
                    "fast_period": FAST_MA,
                    "slow_period": SLOW_MA,
                    "adx_threshold": ADX_THRESHOLD,
                    "atr_sl_mult": ATR_SL_MULT,
                    "atr_tp_mult": ATR_TP_MULT,
                }
            )
        if strategy_params_override:
            self.strategy_params.update(strategy_params_override)
        self._injected_signals = signal_series
        self.commission = commission
        self.slippage = slippage
        self.strategy_descriptor, self.strategy = build_legacy_candidate(
            self.strategy_name, self.strategy_params
        )
        if strategy_artifact is not None:
            expected_parameter_hash = sha256_hex(canonical_params(self.strategy_params))
            if strategy_artifact.code_sha != self.strategy_descriptor.code_sha:
                raise ValueError(
                    "strategy artifact code_sha does not match the allowlisted strategy"
                )
            if strategy_artifact.data_manifest_sha != self.data_manifest_id:
                raise ValueError(
                    "strategy artifact data_manifest_sha does not match the loaded dataset"
                )
            if strategy_artifact.parameter_hash != expected_parameter_hash:
                raise ValueError(
                    "strategy artifact parameter_hash does not match its parameters"
                )
        feature_identity = json.dumps(
            {
                "data_manifest_id": self.data_manifest_id,
                "strategy": self.strategy_name,
                "params": self.strategy_params,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        self.feature_artifact_id = (
            f"sha256:{hashlib.sha256(feature_identity).hexdigest()}"
        )

        # Bind the backtest to a local, immutable, paper-eligible strategy artifact.
        self.artifact_store = PersistentArtifactStore(self.state_dir / "artifacts")
        self.promotion_store = PromotionStateStore(self.state_dir / "promotion.db")
        if strategy_artifact is None:
            strategy_artifact = StrategyArtifact(
                strategy_name=self.strategy_name,
                code_sha=self.strategy_descriptor.code_sha,
                data_manifest_sha=self.data_manifest_id,
                parameter_hash=sha256_hex(canonical_params(self.strategy_params)),
                execution_model_version="full-system-v2",
                framework_version="authority-chain-v1",
                metadata={
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "parameters": self.strategy_params,
                    "calibration_state": "KNOWN",
                    "ood_state": "KNOWN",
                    "regime_state": "KNOWN",
                    "source": "full_system_backtest",
                },
            )
        self.artifact_store.add(strategy_artifact)
        self.promotion_store.upsert_from_event(
            ResearchPromotionEvent(
                subject_artifact_id=strategy_artifact.artifact_id,
                from_stage=ResearchStage.RESEARCH_VALIDATED,
                to_stage=ResearchStage.PAPER_ELIGIBLE,
                evidence_ids=(self.data_manifest_id, self.feature_artifact_id),
                actor="full_system_backtest",
                timestamp=datetime.now(UTC),
            )
        )
        self.strategy_artifact_id = strategy_artifact.artifact_id

        # Pre-compute indicators on full dataset
        print("🔧 Computing strategy indicators...")
        self.df = self.strategy.compute_indicators(self.df)

        # Generate all signals upfront (tournament cells inject their own
        # canonical per-bar signal series instead).
        if self._injected_signals is not None:
            print("🔧 Using injected canonical signal series...")
            self.signals = list(self._injected_signals)
        else:
            print("🔧 Generating signals...")
            signal_series = self.strategy.generate_signals(self.df).rename("signal")
            if "signal" not in self.df.columns:
                self.df = self.df.with_columns(signal_series)
            self.signals = self.df.select(pl.col("signal")).to_series().to_list()

        # Khởi tạo execution engine + risk controller
        authority_config = AuthorityConfig.for_environment(Environment.PAPER)
        authority_config.exposure.max_single_strategy_exposure = MAX_POS_SIZE_PCT
        authority_config.exposure.max_portfolio_exposure = MAX_POSITION_PCT
        self.engine = ExecutionEngine(
            exchange_name=EXCHANGE,
            initial_capital=INITIAL_CAPITAL,
            commission=self.commission,
            slippage=self.slippage,
            instrument_rules=get_instrument_rules(self.symbol),
            state_dir=self.state_dir,
            event_store_path=self.state_dir / "events.db",
            allow_backtest_new_exposure=allow_new_exposure,
            paper_price_persist_interval=state_flush_bars,
            disable_paper_telemetry=True,
            protective_stop_loss_pct=STOP_LOSS_PCT,
            authority_config=authority_config,
            promotion_store=self.promotion_store,
            artifact_store=self.artifact_store,
        )
        if self.engine.resolver is None:
            raise RuntimeError("paper strategy resolver was not initialized")
        self.strategy_runtime = None
        if strategy_runtime is not None:
            # Tournament cells with canonical-only strategies bring their
            # own runtime (bridge over the canonical contract); the artifact
            # above was registered + promoted through the same stores, so
            # every authority check still applies.
            self.strategy_runtime = strategy_runtime
        elif runtime_factory is not None:
            self.strategy_runtime = runtime_factory(
                self.strategy_artifact_id,
                dict(strategy_artifact.metadata),
            )
        if self.strategy_runtime is None:
            self.strategy_runtime = self.engine.resolver.resolve_for(
                self.symbol,
                self.timeframe,
                Environment.PAPER,
            )
        if self.strategy_runtime is None:
            raise RuntimeError(
                f"paper-eligible {self.strategy_name} artifact could not be resolved"
            )
        self.risk = RiskController(
            self.engine,
            max_drawdown_pct=MAX_DRAWDOWN_PCT,
            daily_loss_limit_pct=DAILY_LOSS_LIMIT_PCT,
            max_position_pct=MAX_POSITION_PCT,
            default_stop_loss_pct=STOP_LOSS_PCT,
            cooldown_hours=COOLDOWN_HOURS,
        )

        # Thay datetime.now(UTC) trong risk_controller bằng đồng hồ giả lập
        rc_module.datetime = _SimClock

        # Tracking
        self.equity_curve: list[tuple] = []
        self.trade_log: list[dict] = []
        self.signal_log: list[dict] = []
        self.circuit_breakers: list[str] = []
        self._breaker_active = False
        self._entry_state: dict[str, dict] = {}
        self._stamped_trade_ids: set[str] = set()
        self._run_start = 0
        self._run_end = 0
        self._run_data_quality: DataQualityReport | None = None

    def _position_pct(self, price: float) -> float:
        """% portfolio đang nằm trong vị thế."""
        pos = self.engine.exchange.get_position(self.symbol)
        if not pos or not pos.is_active:
            return 0.0
        equity = self.engine.exchange.get_total_equity()
        return (pos.quantity * price) / equity if equity > 0 else 0.0

    def _attach_entry_context(
        self,
        *,
        at: datetime,
        bar_index: int,
        reference_price: float,
        regime_info: dict[str, object],
        strategy_id: str | None = None,
    ) -> None:
        """Attach deterministic simulation evidence to the active position."""
        position = self.engine.exchange.get_position(self.symbol)
        if not position or not position.is_active:
            return
        position.opened_at = at
        position.updated_at = at
        position.metadata["sizing_method"] = "artifact_target_authority_capped"
        if strategy_id:
            position.metadata["strategy_id"] = strategy_id
        position.metadata["simulation"] = {
            "time_source": "simulated_bar",
            "entry_time": at.isoformat(),
            "entry_bar_index": bar_index,
            "entry_reference_price": reference_price,
            "entry_reference_type": "bar_open",
            "min_reference_price": reference_price,
            "max_reference_price": reference_price,
            "entry_regime": regime_info,
        }

    def _update_position_excursion(self, reference_price: float) -> None:
        position = self.engine.exchange.get_position(self.symbol)
        if not position or not position.is_active:
            return
        simulation = position.metadata.get("simulation")
        if not isinstance(simulation, dict):
            return
        simulation["min_reference_price"] = min(
            float(simulation.get("min_reference_price", reference_price)),
            reference_price,
        )
        simulation["max_reference_price"] = max(
            float(simulation.get("max_reference_price", reference_price)),
            reference_price,
        )

    def _stamp_new_trades(
        self,
        *,
        at: datetime,
        bar_index: int,
        reference_price: float,
        reference_type: str,
    ) -> None:
        """Replace wall-clock broker timestamps with simulated-bar evidence."""
        for trade in self.engine.exchange.get_trade_history(limit=1_000_000):
            if trade.id in self._stamped_trade_ids:
                continue
            metadata = trade.metadata
            simulation = metadata.get("simulation")
            if not isinstance(simulation, dict):
                simulation = {}
                metadata["simulation"] = simulation
            entry_time_value = simulation.get("entry_time")
            if isinstance(entry_time_value, str):
                entry_time = datetime.fromisoformat(entry_time_value)
            else:
                entry_time = trade.entry_time or at
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=UTC)
            else:
                entry_time = entry_time.astimezone(UTC)
            trade.entry_time = entry_time
            trade.exit_time = at
            duration_seconds = max(0.0, (at - entry_time).total_seconds())
            holding_bars = int(
                round(duration_seconds / self.timeframe_delta.total_seconds())
            )
            entry_price = float(trade.entry_price)
            minimum = float(simulation.get("min_reference_price", entry_price))
            maximum = float(simulation.get("max_reference_price", entry_price))
            simulation.update(
                {
                    "time_source": "simulated_bar",
                    "entry_time": entry_time.isoformat(),
                    "exit_time": at.isoformat(),
                    "exit_bar_index": bar_index,
                    "exit_reference_price": reference_price,
                    "exit_reference_type": reference_type,
                    "exit_time_precision": (
                        "exact_bar_open"
                        if reference_type == "bar_open"
                        else "bar_close_proxy"
                    ),
                    "holding_bars": holding_bars,
                    "mae_pct": (minimum / entry_price - 1.0) * 100,
                    "mfe_pct": (maximum / entry_price - 1.0) * 100,
                }
            )
            self._stamped_trade_ids.add(trade.id)

    def _active_config(self) -> dict[str, object]:
        exchange = self.engine.exchange
        payload: dict[str, object] = {
            "strategy": {
                "strategy_id": self.strategy_name,
                "strategy_artifact_id": self.strategy_artifact_id,
                "parameters": self.strategy_params,
                "feature_artifact_id": self.feature_artifact_id,
                "routing_mode": "adaptive" if self.adaptive_enabled else "fixed",
            },
            "protection": {
                "stop_loss": {"enabled": True, "distance_pct": STOP_LOSS_PCT * 100},
                "take_profit": {"enabled": False},
                "trailing_stop": {"enabled": False},
            },
            "sizing": {
                "method": "artifact_target_authority_capped",
                "requested_target_exposure_pct": MAX_POS_SIZE_PCT * 100,
                "max_target_exposure_pct": MAX_POS_SIZE_PCT * 100,
                "max_position_pct": MAX_POSITION_PCT * 100,
            },
            "risk": {
                "max_drawdown_pct": MAX_DRAWDOWN_PCT * 100,
                "daily_loss_limit_pct": DAILY_LOSS_LIMIT_PCT * 100,
                "cooldown_hours": COOLDOWN_HOURS,
            },
            "cost_model": {
                "commission_rate": float(exchange.commission),
                "slippage_rate": float(exchange.slippage),
                "spread_rate": 0.0,
                "market_impact_rate": 0.0,
                "estimated_round_trip_cost_bps": float(
                    2.0 * (exchange.commission + exchange.slippage) * 10_000
                ),
            },
            "data_policy": {
                "gap_policy": self.gap_policy,
                "imputation": "disabled",
            },
            "execution_timing": "decision_on_closed_bar_execute_next_bar_open",
            "intrabar_price_path": "open_then_low_then_close",
            "provenance": {
                "commit_sha": self.commit_sha,
            },
        }
        return {"config_id": fingerprint_payload(payload), **payload}

    def run(
        self, start: int = 0, end: int | None = None, freq: int = 1
    ) -> dict[str, object]:
        if start < 0 or freq <= 0:
            raise ValueError("start must be non-negative and freq must be positive")
        end = end if end is not None else self.df.height
        end = min(end, self.df.height)
        if end <= start:
            raise ValueError(f"Invalid bar window: start={start}, end={end}")
        n = end - start
        self._run_start = start
        self._run_end = end
        self._run_data_quality = assess_ohlcv(
            self.df.slice(start, n),
            expected_interval=self.timeframe_delta,
            gap_policy=self.gap_policy,
        )
        print(f"🚀 Simulating bars {start}→{end} ({n} bars, decision mỗi {freq}h)")
        print(
            f"   SL={STOP_LOSS_PCT:.0%} | TP=off | Trail=off | "
            f"Position cap={MAX_POS_SIZE_PCT:.0%} | Cooldown={COOLDOWN_HOURS:.0f}h\n"
        )

        for i in range(start, end):
            row = self.df.row(i, named=True)
            ts = row["timestamp"]
            price = float(row["open"])
            signal_index = i - 1
            decision_row = self.df.row(signal_index, named=True) if i > start else row
            decision_ts = decision_row["timestamp"]
            signal = (
                int(self.signals[signal_index])
                if i > start and self.signals[signal_index] is not None
                else 0
            )

            # The prior closed bar may execute only at this bar's open.
            bar_open_at = datetime.fromisoformat(str(ts))
            if bar_open_at.tzinfo is None:
                bar_open_at = bar_open_at.replace(tzinfo=UTC)
            else:
                bar_open_at = bar_open_at.astimezone(UTC)
            bar_close_at = bar_open_at + self.timeframe_delta
            decision_bar_open_at = datetime.fromisoformat(str(decision_ts))
            if decision_bar_open_at.tzinfo is None:
                decision_bar_open_at = decision_bar_open_at.replace(tzinfo=UTC)
            else:
                decision_bar_open_at = decision_bar_open_at.astimezone(UTC)
            decision_bar_close_at = decision_bar_open_at + self.timeframe_delta
            _SimClock.current = bar_open_at

            # 1. Mark at the next tradable open before any decision.
            self._update_position_excursion(price)
            self.engine.update_prices({self.symbol: price})
            self._stamp_new_trades(
                at=bar_open_at,
                bar_index=i,
                reference_price=price,
                reference_type="bar_open",
            )

            # 2. Risk checks (max DD, daily loss, circuit breaker)
            alerts = self.risk.check_all()
            breaker_on = any("CIRCUIT BREAKER ACTIVE" in a for a in alerts)
            if breaker_on and not self._breaker_active:
                self.circuit_breakers.append(f"{ts}: {alerts[0]}")
                print(
                    f"   🚨 CIRCUIT BREAKER ON @ {ts} — đóng toàn bộ vị thế, tạm dừng {COOLDOWN_HOURS:.0f}h"
                )
            elif not breaker_on and self._breaker_active:
                print(f"   ✅ CIRCUIT BREAKER OFF @ {ts} — giao dịch trở lại")
            self._breaker_active = breaker_on

            active_runtime = self.strategy_runtime
            routing_decision = None
            adaptive_manage_existing = False
            if (
                self.adaptive_enabled
                and i > start
                and signal_index % freq == 0
                and not breaker_on
            ):
                position = self.engine.exchange.get_position(self.symbol)
                position_is_flat = not (position and position.is_active and position.quantity > 0)
                position_owner = None
                if not position_is_flat and position is not None:
                    owner_value = position.metadata.get("strategy_id")
                    if owner_value is None:
                        owner_value = position.metadata.get("strategy_name")
                    position_owner = str(owner_value) if owner_value else None
                if self._adaptive_posterior_provider is None or self._adaptive_router is None:
                    raise RuntimeError("adaptive routing providers are not initialized")
                posterior = self._adaptive_posterior_provider(
                    self.symbol,
                    self.timeframe,
                    decision_row,
                    decision_bar_close_at,
                )
                routing_decision = self._adaptive_router.route(
                    symbol=self.symbol,
                    timeframe=self.timeframe,
                    posterior=posterior,
                    observed_at=decision_bar_close_at,
                    position_is_flat=position_is_flat,
                    position_owner_strategy_id=position_owner,
                )
                self.routing_decisions.append(routing_decision.to_dict())
                chosen = routing_decision.chosen_strategy_id
                can_manage_existing_position = (
                    not position_is_flat and chosen is not None and chosen == position_owner
                )
                adaptive_manage_existing = can_manage_existing_position
                if chosen is not None and (
                    routing_decision.allow_new_exposure or can_manage_existing_position
                ):
                    if self._adaptive_runtime_provider is None:
                        raise RuntimeError("adaptive runtime provider is not initialized")
                    active_runtime = self._adaptive_runtime_provider(routing_decision)
                    if active_runtime is None:
                        raise RuntimeError(
                            f"no executable runtime for routed strategy {chosen}"
                        )
                    if getattr(active_runtime, "strategy_name", None) != chosen:
                        raise RuntimeError(
                            "adaptive runtime strategy does not match routing decision"
                        )
                    if getattr(active_runtime, "symbol", self.symbol) != self.symbol:
                        raise RuntimeError("adaptive runtime symbol does not match simulator")
                    if getattr(active_runtime, "timeframe", self.timeframe) != self.timeframe:
                        raise RuntimeError(
                            "adaptive runtime timeframe does not match simulator"
                        )
                    # The selected runtime is the authority for the actual
                    # BUY/SELL forecast.  A non-zero sentinel only opens the
                    # existing execution path; HOLD remains a no-op.
                    signal = 1
                else:
                    signal = 0

            # 3. Execute signal theo chu kỳ (chỉ khi chưa bị chặn)
            if (
                i > start
                and signal_index % freq == 0
                and not breaker_on
                and not any("Cooldown" in a for a in alerts)
            ):
                pos_pct = self._position_pct(price)

                # Only act on crossover signals (non-zero)
                if signal != 0:
                    # Get regime info
                    regime_info = {
                        "vol_regime": decision_row.get("vol_regime"),
                        "trend_regime": decision_row.get("trend_regime"),
                        "trend_dir": decision_row.get("trend_dir"),
                        "adx": decision_row.get("adx"),
                        "atr_pctl": decision_row.get("atr_pctl"),
                    }

                    position = self.engine.exchange.get_position(self.symbol)
                    if signal == 1:  # BUY
                        if (
                            position
                            and position.is_active
                            and position.quantity > 0
                            and not adaptive_manage_existing
                        ):
                            signal = 0  # Already long, skip
                    elif signal == -1:  # SELL (exit to flat)
                        if (
                            not position
                            or not position.is_active
                            or position.quantity <= 0
                        ):
                            # No position to exit, skip SELL signal
                            signal = 0

                    if signal != 0:
                        signal_name = "BUY" if signal == 1 else "SELL"

                        self.signal_log.append(
                            {
                                "timestamp": str(decision_ts),
                                "executed_at": str(ts),
                                "price": price,
                                "position_pct": pos_pct,
                                "signal": signal_name,
                                "confidence": 0.5,
                                "risk": "authority_chain",
                                "max_pos": MAX_POS_SIZE_PCT if signal == 1 else 0.0,
                                "adaptive": self.adaptive_enabled,
                                "routing_decision_id": (
                                    routing_decision.decision_id
                                    if routing_decision is not None
                                    else None
                                ),
                            }
                        )

                        # Build observation from current bar for canonical pipeline
                        observation = EnrichedMarketObservation(
                            observation_id=f"obs-{self.symbol}-{signal_index}",
                            symbol=self.symbol,
                            observed_at=decision_bar_close_at,
                            open=float(decision_row["open"]),
                            high=float(decision_row["high"]),
                            low=float(decision_row["low"]),
                            close=float(decision_row["close"]),
                            volume=float(decision_row.get("volume", 0.0)),
                            features={
                                k: float(decision_row[k])
                                for k in ["fast_ma", "slow_ma", "adx", "atr"]
                                if k in decision_row and decision_row[k] is not None
                            },
                            venue=self.exchange,
                            source="historical_parquet",
                            timeframe=self.timeframe,
                            bar_open_at=decision_bar_open_at,
                            bar_close_at=decision_bar_close_at,
                            is_closed=True,
                            data_manifest_id=self.data_manifest_id,
                            feature_artifact_id=self.feature_artifact_id,
                        )

                        # Execute
                        market_data = self.df.slice(0, signal_index + 1).select(
                            "timestamp", "open", "high", "low", "close", "volume"
                        )
                        orders = self.engine.execute_strategy(
                            active_runtime,
                            market_data,
                            observation,
                        )
                        self.signal_log[-1]["authority_trace"] = dict(
                            self.engine.last_strategy_execution
                        )
                        for order in orders:
                            side = order.side.value
                            amount = float(order.filled_amount or order.amount)
                            if side == "buy":
                                self._attach_entry_context(
                                    at=bar_open_at,
                                    bar_index=i,
                                    reference_price=price,
                                    regime_info=regime_info,
                                    strategy_id=getattr(
                                        active_runtime, "strategy_name", self.strategy_name
                                    ),
                                )
                                self._entry_state[self.symbol] = {
                                    "price": price,
                                "amount": amount,
                                "strategy_id": getattr(
                                    active_runtime, "strategy_name", self.strategy_name
                                ),
                                }
                                pnl = 0.0
                            elif side == "sell":
                                entry = self._entry_state.pop(self.symbol, None)
                                if entry:
                                    pnl = (price - entry["price"]) * entry["amount"]
                                else:
                                    pnl = 0.0
                            else:
                                pnl = 0.0
                            self.trade_log.append(
                                {
                                    "timestamp": str(ts),
                                    "side": side,
                                    "amount": amount,
                                    "price": price,
                                    "pnl": pnl,
                                    "equity": float(
                                        self.engine.exchange.get_total_equity()
                                    ),
                                }
                            )
                            side = "🟢 BUY" if order.side.value == "buy" else "🔴 SELL"
                            print(
                                f"   {side} {order.filled_amount or order.amount:.4f} @ ${price:,.2f} @ {ts}"
                            )
                        self._stamp_new_trades(
                            at=bar_open_at,
                            bar_index=i,
                            reference_price=price,
                            reference_type="bar_open",
                        )

            # 4. Simulate adverse excursion, then mark at this bar's close.
            _SimClock.current = bar_close_at
            low_price = float(row["low"])
            close_price = float(row["close"])
            if low_price < price:
                self._update_position_excursion(low_price)
                self.engine.update_prices({self.symbol: low_price})
                self._stamp_new_trades(
                    at=bar_close_at,
                    bar_index=i,
                    reference_price=low_price,
                    reference_type="bar_low",
                )
            self._update_position_excursion(close_price)
            self.engine.update_prices({self.symbol: close_price})
            self._stamp_new_trades(
                at=bar_close_at,
                bar_index=i,
                reference_price=close_price,
                reference_type="bar_close",
            )
            self.equity_curve.append(
                (bar_close_at.isoformat(), self.engine.exchange.get_total_equity())
            )

            # Progress
            if (i - start) % 2000 == 0 and i > start:
                eq = self.equity_curve[-1][1]
                print(
                    f"   ... {i - start}/{n} bars — equity ${eq:,.2f} "
                    f"({(eq / INITIAL_CAPITAL - 1) * 100:+.2f}%)"
                )

        print("\n✅ Simulation complete")
        self.engine.exchange.flush_state()
        return self._report()

    # ── Báo cáo ────────────────────────────────────────────────────────
    def _report(self) -> dict[str, object]:
        if not self.equity_curve:
            print("❌ No data")
            return {}

        self.trade_log = [
            trade.to_dict()
            for trade in self.engine.exchange.get_trade_history(limit=1_000_000)
        ]
        metrics = calculate_performance_metrics(
            self.equity_curve,
            initial_capital=INITIAL_CAPITAL,
            timeframe_delta=self.timeframe_delta,
            trades=self.trade_log,
        )
        cost_attribution = calculate_cost_attribution(self.trade_log)
        window = self.df.slice(self._run_start, self._run_end - self._run_start)
        benchmark = fixed_allocation_buy_and_hold(
            [float(value) for value in window["close"].to_list()],
            entry_reference_price=float(window["open"][0]),
            initial_capital=INITIAL_CAPITAL,
            allocation_pct=MAX_POS_SIZE_PCT,
            commission_rate=float(self.engine.exchange.commission),
            slippage_rate=float(self.engine.exchange.slippage),
            timeframe_delta=self.timeframe_delta,
        )
        yearly_returns = calendar_returns(
            self.equity_curve, initial_capital=INITIAL_CAPITAL
        )
        active_config = self._active_config()
        first_bar_at = datetime.fromisoformat(str(window["timestamp"][0]))
        last_bar_at = datetime.fromisoformat(str(window["timestamp"][-1]))
        if first_bar_at.tzinfo is None:
            first_bar_at = first_bar_at.replace(tzinfo=UTC)
        if last_bar_at.tzinfo is None:
            last_bar_at = last_bar_at.replace(tzinfo=UTC)
        simulation_window = {
            "start_at": first_bar_at.astimezone(UTC).isoformat(),
            "end_at": (last_bar_at.astimezone(UTC) + self.timeframe_delta).isoformat(),
            "bar_count": len(window),
            "timeframe_seconds": int(self.timeframe_delta.total_seconds()),
        }

        print(f"\n{'=' * 55}")
        print(
            f"📊 KETA QUẢ FULL SYSTEM — {self.symbol} {self.timeframe} ({self.exchange})"
        )
        print(f"{'=' * 55}")
        print(f"   Vốn ban đầu:      ${INITIAL_CAPITAL:,.2f}")
        print(f"   Vốn cuối:         ${metrics['final_equity']:,.2f}")
        print(f"   Tổng lợi nhuận:   {metrics['total_return_pct']:+.2f}%")
        print(f"   CAGR:             {metrics['cagr_pct']:+.2f}%")
        print(f"   Sharpe năm hóa:   {metrics['sharpe']:.2f}")
        print(f"   Sortino:          {metrics['sortino']:.2f}")
        print(f"   Max Drawdown:     {metrics['max_drawdown_pct']:.2f}%")
        print(f"   Tổng trades:      {metrics['total_trades']}")
        print(f"   Win rate:         {metrics['win_rate_pct']:.1f}%")
        print(f"   Avg win:          ${metrics['average_win']:,.2f}")
        print(f"   Avg loss:         ${abs(metrics['average_loss']):,.2f}")
        profit_factor = metrics["profit_factor"]
        profit_factor_display = (
            f"{profit_factor:.2f}" if profit_factor is not None else "N/A"
        )
        print(f"   Profit factor:    {profit_factor_display}")
        print(f"   Chi phí mô hình:  ${cost_attribution['total_cost']:,.2f}")
        print(f"   Circuit breakers: {len(self.circuit_breakers)}")

        # Phân bố theo năm
        print("\n📅 PHÂN BỐ THEO NĂM")
        for year, value in yearly_returns.items():
            print(f"   {year}: {value:+.2f}%")

        # 10 trades gần nhất
        open_positions = self.engine.exchange.get_all_positions()
        open_orders = self.engine.exchange.get_open_orders()
        protected_symbols = {
            order.symbol
            for order in open_orders
            if order.side.value == "sell"
            and order.type.value in {"stop_loss", "stop_loss_limit", "stop"}
        }
        # Tolerance for dust positions (e.g., from partial fill rounding)
        # Position quantity in base currency (e.g., SOL). Residual from
        # partial fill: 69.3415 - 69.3410 = 0.0005 SOL
        MIN_POSITION_QTY = 1e-3
        unprotected_positions = sorted(
            position.symbol
            for position in open_positions
            if position.quantity > MIN_POSITION_QTY
            and position.symbol not in protected_symbols
        )
        lifecycle_state = self.engine.lifecycle.state
        manual_intent_ids = sorted(lifecycle_state.unresolved_manual_intents)
        unknown_order_ids = sorted(
            intent_id
            for intent_id, order in lifecycle_state.orders.items()
            if order.status.value == "manual"
        )
        trade_evidence_complete = all(
            isinstance(trade.get("metadata"), dict)
            and isinstance(trade["metadata"].get("simulation"), dict)
            and trade["metadata"]["simulation"].get("time_source") == "simulated_bar"
            and trade["metadata"]["simulation"].get("entry_reference_price") is not None
            and trade["metadata"]["simulation"].get("exit_reference_price") is not None
            for trade in self.trade_log
        )
        execution_health = {
            "status": lifecycle_state.execution_health.value,
            "unknown_orders": len(unknown_order_ids),
            "unknown_order_ids": unknown_order_ids,
            "manual_interventions": len(manual_intent_ids),
            "manual_intent_ids": manual_intent_ids,
            "active_sell_reservations": float(
                self.engine.lifecycle.active_sell_reservations()
            ),
            "unprotected_positions": unprotected_positions,
            "trade_evidence_complete": trade_evidence_complete,
        }
        if self._run_data_quality is None:
            raise RuntimeError("run data-quality evidence was not initialized")
        run_passed = (
            execution_health["status"] == "normal"
            and not unknown_order_ids
            and not manual_intent_ids
            and not unprotected_positions
            and trade_evidence_complete
            and self._run_data_quality.accepted
            and bool(cost_attribution["complete"])
            and abs(float(cost_attribution["reconciliation_error"])) <= 1e-8
        )

        print("\n🧾 10 TRADES GẦN NHẤT")
        for t in self.trade_log[-10:]:
            pnl = t.get("pnl", 0)
            side = "🟢" if pnl > 0 else "🔴"
            exit_price = float(t.get("exit_price") or 0.0)
            print(
                f"   {side} {t['side']} {t['quantity']:.4f} "
                f"${t['entry_price']:,.2f} → ${exit_price:,.2f} "
                f"pnl ${pnl:+.2f} ({t.get('pnl_pct', 0.0):+.1f}%) "
                f"[{t.get('reason') or 'signal'}] "
                f"{str(t.get('exit_time') or '')[:19]}"
            )

        report: dict[str, object] = {
            "schema_version": 2,
            "report_type": "full_system_backtest",
            "run_id": self.run_id,
            "status": "passed" if run_passed else "failed",
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "exchange": EXCHANGE,
            "initial_capital": INITIAL_CAPITAL,
            "final_equity": metrics["final_equity"],
            "total_return_pct": metrics["total_return_pct"],
            "sharpe": metrics["sharpe"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "total_trades": metrics["total_trades"],
            "win_rate_pct": metrics["win_rate_pct"],
            "profit_factor": profit_factor,
            "circuit_breakers": len(self.circuit_breakers),
            "signals_seen": len(self.signal_log),
            "signals": self.signal_log,
            "routing_decisions": self.routing_decisions,
            "open_positions": len(open_positions),
            "open_orders": len(open_orders),
            "execution_timing": "decision_on_closed_bar_execute_next_bar_open",
            "execution_health": execution_health,
            "simulation_window": simulation_window,
            "active_config": active_config,
            "data_quality": {
                "source": self.source_data_quality.to_dict(),
                "window": self._run_data_quality.to_dict(),
            },
            "metrics": metrics,
            "cost_attribution": cost_attribution,
            "benchmarks": {
                "fixed_allocation_buy_and_hold": benchmark,
            },
            "calendar_returns_pct": yearly_returns,
            "data_manifest_id": self.data_manifest_id,
            "feature_artifact_id": self.feature_artifact_id,
            "commit_sha": self.commit_sha,
            "state_dir": str(self.state_dir),
            "report_path": str(self.report_path),
            "equity_curve": [[str(ts), float(eq)] for ts, eq in self.equity_curve],
            "trades": self.trade_log,
        }
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        with self.report_path.open("w", encoding="utf-8") as output:
            json.dump(report, output, indent=2, allow_nan=False)
        print(f"\n💾 Saved → {self.report_path}")
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fresh", action="store_true", help="Reset paper state trước khi chạy"
    )
    parser.add_argument("--start", type=int, default=0, help="Bar bắt đầu (mặc định 0)")
    parser.add_argument(
        "--end", type=int, default=None, help="Bar kết thúc (mặc định hết)"
    )
    parser.add_argument(
        "--freq", type=int, default=1, help="Phân tích mỗi N bar (mặc định 1h)"
    )
    parser.add_argument("--symbol", default=None, help="Symbol, vd BTC/USDT")
    parser.add_argument("--timeframe", default=None, help="Timeframe, vd 1h/4h")
    parser.add_argument(
        "--strategy",
        choices=tuple(sorted(FIRST_WAVE_DESCRIPTORS)),
        default="enhanced_ma",
        help="Canonical strategy candidate to run",
    )
    parser.add_argument(
        "--strategy-params-json",
        default=None,
        help="JSON object overriding the selected strategy parameters",
    )
    parser.add_argument(
        "--strategy-artifact",
        default=None,
        help="Path to an immutable StrategyArtifact JSON manifest",
    )
    parser.add_argument(
        "--state-dir", default=None, help="Isolated paper state directory"
    )
    parser.add_argument("--report-path", default=None, help="Output JSON report path")
    parser.add_argument("--run-id", default=None, help="Stable identifier for this run")
    parser.add_argument(
        "--state-flush-bars",
        type=int,
        default=int(os.getenv("BACKTEST_STATE_FLUSH_BARS", "100")),
        help="Persist mark-to-market state every N bars",
    )
    parser.add_argument(
        "--tail-bars",
        type=int,
        default=None,
        help="Run only the most recent N bars",
    )
    parser.add_argument(
        "--allow-new-exposure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Explicitly authorize new exposure for this paper backtest only",
    )
    parser.add_argument(
        "--gap-policy",
        choices=("record", "reject"),
        default=os.getenv("BACKTEST_GAP_POLICY", "record"),
        help="Record timestamp gaps as evidence or reject the run",
    )
    args = parser.parse_args()

    strategy_params = None
    if args.strategy_params_json is not None:
        try:
            strategy_params = json.loads(args.strategy_params_json)
        except json.JSONDecodeError as exc:
            parser.error(f"--strategy-params-json is invalid JSON: {exc}")
        if not isinstance(strategy_params, dict):
            parser.error("--strategy-params-json must decode to a JSON object")

    strategy_artifact = None
    if args.strategy_artifact is not None:
        try:
            strategy_artifact = _load_strategy_artifact(args.strategy_artifact)
        except ValueError as exc:
            parser.error(str(exc))
        if args.strategy != "enhanced_ma" and args.strategy != strategy_artifact.strategy_name:
            parser.error("--strategy conflicts with --strategy-artifact")
        artifact_parameters = strategy_artifact.metadata.get("parameters", {})
        if not isinstance(artifact_parameters, dict):
            parser.error("strategy artifact metadata.parameters must be a JSON object")
        if strategy_params is not None and strategy_params != artifact_parameters:
            parser.error("--strategy-params-json conflicts with --strategy-artifact")
        strategy_params = dict(artifact_parameters)

    sim = FullSystemSimulator(
        fresh=args.fresh,
        symbol=args.symbol,
        timeframe=args.timeframe,
        state_dir=args.state_dir,
        report_path=args.report_path,
        run_id=args.run_id,
        allow_new_exposure=args.allow_new_exposure,
        state_flush_bars=args.state_flush_bars,
        gap_policy=args.gap_policy,
        strategy_name=(strategy_artifact.strategy_name if strategy_artifact else args.strategy),
        strategy_params_override=strategy_params,
        strategy_artifact=strategy_artifact,
    )
    start = args.start
    if args.tail_bars is not None:
        if args.tail_bars <= 0:
            parser.error("--tail-bars must be positive")
        if args.start != 0 or args.end is not None:
            parser.error("--tail-bars cannot be combined with --start/--end")
        start = max(0, sim.df.height - args.tail_bars)
    report = sim.run(start=start, end=args.end, freq=args.freq)
    if report.get("status") != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
