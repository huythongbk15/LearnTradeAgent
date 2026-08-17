"""Immutable calibration observations, datasets, and source-aware profiles."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Iterable, Protocol, Sequence


class CalibrationSource(str, Enum):
    SYNTHETIC = "SYNTHETIC"
    TESTNET = "TESTNET"
    SHADOW = "SHADOW"
    LIVE = "LIVE"


class CalibrationStatus(str, Enum):
    HEURISTIC = "HEURISTIC"
    EMPIRICAL = "EMPIRICAL"


@dataclass(frozen=True)
class BookLevel:
    price: float
    quantity: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.price) or self.price <= 0.0:
            raise ValueError("book price must be finite and positive")
        if not math.isfinite(self.quantity) or self.quantity < 0.0:
            raise ValueError("book quantity must be finite and non-negative")


@dataclass(frozen=True)
class BookSnapshot:
    observed_at: datetime
    sequence: int | None
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]

    def __post_init__(self) -> None:
        if not self.bids or not self.asks:
            raise ValueError("book snapshot requires bid and ask depth")
        if self.bids[0].price >= self.asks[0].price:
            raise ValueError("book snapshot must have a positive spread")


@dataclass(frozen=True)
class CalibrationObservation:
    timestamp: datetime
    symbol: str
    exchange: str
    book_snapshot: BookSnapshot
    best_bid: float
    best_ask: float
    bid_depth: float
    ask_depth: float
    spread_bps: float
    trade_flow: float
    order_side: str
    order_type: str
    requested_qty: float
    filled_qty: float
    fill_latency_ms: float
    partial_fills: int
    slippage_bps: float
    adverse_selection_100ms_bps: float
    adverse_selection_1s_bps: float
    adverse_selection_5s_bps: float
    adverse_selection_30s_bps: float
    client_order_id: str
    broker_order_id: str
    source: CalibrationSource

    def __post_init__(self) -> None:
        numeric = (
            self.best_bid,
            self.best_ask,
            self.bid_depth,
            self.ask_depth,
            self.spread_bps,
            self.trade_flow,
            self.requested_qty,
            self.filled_qty,
            self.fill_latency_ms,
            self.slippage_bps,
            self.adverse_selection_100ms_bps,
            self.adverse_selection_1s_bps,
            self.adverse_selection_5s_bps,
            self.adverse_selection_30s_bps,
        )
        if not all(math.isfinite(float(value)) for value in numeric):
            raise ValueError("calibration observation numeric fields must be finite")
        if self.best_bid <= 0.0 or self.best_ask <= self.best_bid:
            raise ValueError("best bid/ask must define a positive spread")
        if not math.isclose(self.best_bid, self.book_snapshot.bids[0].price) or not math.isclose(
            self.best_ask, self.book_snapshot.asks[0].price
        ):
            raise ValueError("best bid/ask must match the immutable book snapshot")
        if self.bid_depth < 0.0 or self.ask_depth < 0.0:
            raise ValueError("depth must be non-negative")
        if self.requested_qty <= 0.0 or not 0.0 <= self.filled_qty <= self.requested_qty:
            raise ValueError("filled quantity must be within requested quantity")
        if self.fill_latency_ms < 0.0 or self.partial_fills < 0:
            raise ValueError("latency and partial fill count must be non-negative")
        if not self.symbol or not self.exchange:
            raise ValueError("symbol and exchange are required")
        if not self.client_order_id or not self.broker_order_id:
            raise ValueError("client and broker order ids are required")


def _canonical_payload(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class CalibrationDataset:
    dataset_id: str
    observations: tuple[CalibrationObservation, ...]
    source: CalibrationSource
    exchange: str
    symbols: tuple[str, ...]
    data_start: datetime
    data_end: datetime
    data_hash: str
    created_at: datetime

    @classmethod
    def build(
        cls,
        observations: Iterable[CalibrationObservation],
        *,
        created_at: datetime | None = None,
    ) -> "CalibrationDataset":
        records = tuple(observations)
        if not records:
            raise ValueError("calibration dataset must not be empty")
        sources = {record.source for record in records}
        exchanges = {record.exchange for record in records}
        if len(sources) != 1 or len(exchanges) != 1:
            raise ValueError("one dataset cannot mix sources or exchanges")
        ordered = tuple(
            sorted(records, key=lambda record: (record.timestamp, record.broker_order_id))
        )
        payload = [asdict(record) for record in ordered]
        data_hash = hashlib.sha256(_canonical_payload(payload).encode("utf-8")).hexdigest()
        return cls(
            dataset_id=f"caldata_{data_hash[:32]}",
            observations=ordered,
            source=next(iter(sources)),
            exchange=next(iter(exchanges)),
            symbols=tuple(sorted({record.symbol for record in ordered})),
            data_start=min(record.timestamp for record in ordered),
            data_end=max(record.timestamp for record in ordered),
            data_hash=data_hash,
            created_at=created_at or datetime.now(UTC),
        )


@dataclass(frozen=True)
class CalibrationProfile:
    profile_id: str
    source: CalibrationSource
    status: CalibrationStatus
    exchange: str
    symbols: tuple[str, ...]
    data_start: datetime
    data_end: datetime
    sample_count: int
    data_hash: str
    spread_model_version: str
    depth_model_version: str
    fill_model_version: str
    latency_model_version: str
    impact_model_version: str
    adverse_selection_model_version: str
    created_at: datetime

    @classmethod
    def build(
        cls,
        dataset: CalibrationDataset,
        *,
        spread_model_version: str,
        depth_model_version: str,
        fill_model_version: str,
        latency_model_version: str,
        impact_model_version: str,
        adverse_selection_model_version: str,
    ) -> "CalibrationProfile":
        versions = {
            "spread_model_version": spread_model_version,
            "depth_model_version": depth_model_version,
            "fill_model_version": fill_model_version,
            "latency_model_version": latency_model_version,
            "impact_model_version": impact_model_version,
            "adverse_selection_model_version": adverse_selection_model_version,
        }
        if any(not version for version in versions.values()):
            raise ValueError("all calibration model versions are required")
        status = (
            CalibrationStatus.HEURISTIC
            if dataset.source == CalibrationSource.SYNTHETIC
            else CalibrationStatus.EMPIRICAL
        )
        identity = {
            "dataset_id": dataset.dataset_id,
            "source": dataset.source.value,
            **versions,
        }
        return cls(
            profile_id=f"calprof_{hashlib.sha256(_canonical_payload(identity).encode()).hexdigest()[:32]}",
            source=dataset.source,
            status=status,
            exchange=dataset.exchange,
            symbols=dataset.symbols,
            data_start=dataset.data_start,
            data_end=dataset.data_end,
            sample_count=len(dataset.observations),
            data_hash=dataset.data_hash,
            created_at=datetime.now(UTC),
            **versions,
        )


class CalibrationDatasetStore:
    """Append-only immutable JSON store for source-labelled observations."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def put(self, dataset: CalibrationDataset) -> Path:
        destination = self.path / f"{dataset.dataset_id}.json"
        payload = _canonical_payload(asdict(dataset))
        if destination.exists():
            if destination.read_text(encoding="utf-8") != payload:
                raise RuntimeError("calibration dataset id collision")
            return destination
        temporary = destination.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, destination)
        return destination


class ExchangeObservationProvider(Protocol):
    def observations(
        self,
        *,
        exchange: str,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
    ) -> Iterable[CalibrationObservation]: ...


def collect_exchange_observations(
    provider: ExchangeObservationProvider,
    *,
    exchange: str,
    symbols: Sequence[str],
    start: datetime,
    end: datetime,
) -> CalibrationDataset:
    """Collect actual exchange evidence; synthetic sources are rejected."""

    records = tuple(
        provider.observations(
            exchange=exchange,
            symbols=symbols,
            start=start,
            end=end,
        )
    )
    if any(record.source == CalibrationSource.SYNTHETIC for record in records):
        raise ValueError("exchange observation interface cannot accept SYNTHETIC records")
    requested_symbols = set(symbols)
    if any(record.exchange != exchange for record in records):
        raise ValueError("exchange observation provider returned the wrong exchange")
    if any(record.symbol not in requested_symbols for record in records):
        raise ValueError("exchange observation provider returned an unrequested symbol")
    if any(not start <= record.timestamp <= end for record in records):
        raise ValueError("exchange observation provider returned data outside the window")
    return CalibrationDataset.build(records)
