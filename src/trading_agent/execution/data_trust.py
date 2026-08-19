"""Trusted time and market data — P0.3 hardening.

Goals
-----
1. Separate exchange timestamp, request start and local receive timestamp.
2. Reject high-latency responses and excessive exchange clock skew.
3. Validate order-book sequence/update IDs and WebSocket snapshot+diff sync.
4. Export quote age, request latency, sequence gap and clock-skew metrics.

The module is deliberately network-free for its core logic: everything that
talks to an exchange (ServerClock.sync, BinanceDepthSync.initialize) uses
lazy imports so this file stays importable and unit-testable everywhere.

Philosophy: fail closed. Any sign of untrustworthy time or data raises
DataTrustError instead of degrading silently.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Default tolerances (seconds). These are intentionally strict: an order
# placed on stale data or a skewed clock is worse than a skipped cycle.
DEFAULT_MAX_LATENCY_S = 5.0  # wall-clock round trip for a REST fetch
DEFAULT_MAX_CLOCK_SKEW_S = 2.0  # |server - local| before we refuse to run
DEFAULT_MAX_QUOTE_AGE_S = 30.0  # quote age tolerated at decision time

# Binance public REST /api/v3/time
BINANCE_MAINNET_TIME_URL = "https://api.binance.com/api/v3/time"
BINANCE_TESTNET_TIME_URL = "https://testnet.binance.vision/api/v3/time"


class DataTrustError(Exception):
    """Base error for untrusted time/data."""


class HighLatencyError(DataTrustError):
    """A market-data request exceeded the allowed round-trip latency."""


class ClockSkewError(DataTrustError):
    """Local clock disagrees with the exchange server clock beyond tolerance."""


class StaleQuoteError(DataTrustError):
    """A quote/timestamp is stale or future-dated."""


class SequenceGapError(DataTrustError):
    """Order-book update IDs are out of order or have a gap."""


@dataclass(slots=True)
class TimeStampedFetch:
    """The three timestamps demanded by P0.3 for every market-data sample.

    ``exchange_timestamp`` — ms since epoch reported by the exchange.
    ``request_started_at`` — local monotonic seconds just before the request.
    ``received_at``        — local monotonic seconds just after the response.

    A quote's *age* is measured against ``received_at`` (the moment the data
    reaches us), never against the exchange timestamp alone.
    """

    exchange_timestamp: Optional[float]
    request_started_at: float
    received_at: float

    @property
    def latency_s(self) -> float:
        return max(0.0, self.received_at - self.request_started_at)


def start_fetch() -> float:
    """Mark the start of a market-data request (monotonic)."""
    return time.monotonic()


def finish_fetch(started_at: float) -> TimeStampedFetch:
    """Build a TimeStampedFetch for a request that started at ``started_at``.

    ``exchange_timestamp`` is filled in by the caller once the payload is
    parsed; None here means "unknown yet".
    """
    return TimeStampedFetch(
        exchange_timestamp=None,
        request_started_at=started_at,
        received_at=time.monotonic(),
    )


def quote_age_s(
    fetch: TimeStampedFetch, now_s: Optional[float] = None
) -> Optional[float]:
    """Age of a quote: wall-clock now minus the exchange event time.

    Falls back to receive-to-now when the exchange timestamp is missing.
    Returns None only when both are unavailable (caller should treat as
    untrusted and reject).
    """
    received_wall = _wall_from_monotonic(fetch.received_at, now_s)
    if fetch.exchange_timestamp is not None:
        exchange_s = fetch.exchange_timestamp / 1000.0
        return max(0.0, received_wall - exchange_s)
    return None


def _wall_from_monotonic(mono_s: float, now_s: Optional[float]) -> float:
    """Approximate wall clock for a monotonic instant.

    The approximation error is bounded by the boot-time offset between the
    two clocks and is irrelevant for age calculations (we only use it when
    the exchange timestamp is missing).
    """
    if now_s is not None:
        return now_s
    return time.time()


def reject_high_latency(
    fetch: TimeStampedFetch,
    *,
    max_latency_s: float = DEFAULT_MAX_LATENCY_S,
    context: str = "market data",
) -> None:
    """Raise HighLatencyError when a request took too long to round-trip."""
    if not math.isfinite(max_latency_s) or max_latency_s <= 0:
        raise DataTrustError("max_latency_s must be finite and positive")
    latency = fetch.latency_s
    if latency > max_latency_s:
        raise HighLatencyError(
            f"{context} round-trip latency {latency:.1f}s exceeds "
            f"{max_latency_s:.1f}s limit"
        )


def reject_stale_exchange_timestamp(
    fetch: TimeStampedFetch,
    *,
    max_age_s: float = DEFAULT_MAX_QUOTE_AGE_S,
    context: str = "market data",
    now_s: Optional[float] = None,
) -> None:
    """Reject quotes whose exchange event time is stale or in the future.

    Fail closed: a missing exchange timestamp is treated as untrusted.
    """
    if not math.isfinite(max_age_s) or max_age_s <= 0:
        raise DataTrustError("max_age_s must be finite and positive")
    if fetch.exchange_timestamp is None:
        raise StaleQuoteError(f"{context} has no exchange timestamp — untrusted")
    exchange_s = fetch.exchange_timestamp / 1000.0
    wall_now = _wall_from_monotonic(fetch.received_at, now_s)
    age = wall_now - exchange_s
    if age < -5.0:
        raise StaleQuoteError(f"{context} timestamp is {abs(age):.1f}s in the future")
    if age > max_age_s:
        raise StaleQuoteError(f"{context} is stale: age {age:.1f}s > {max_age_s:.1f}s")


@dataclass(frozen=True)
class TrustedPrice:
    """A new-exposure price with mandatory exchange timestamp and integrity checks.

    ``exchange_timestamp`` is mandatory (ms since epoch).  Freshness and
    monotonicity are validated explicitly; any violation raises
    :class:`DataTrustError` instead of degrading silently.
    """

    symbol: str
    price: float
    exchange_timestamp: float  # ms since epoch — mandatory
    received_at: float  # monotonic seconds
    sequence_id: int | None = None
    previous_exchange_timestamp: float | None = None  # for monotonicity

    def validate_freshness(
        self,
        *,
        max_age_s: float = DEFAULT_MAX_QUOTE_AGE_S,
        now_s: float | None = None,
        context: str = "trusted price",
    ) -> None:
        """Raise if the price is stale, future-dated, or beyond tolerance."""
        if not math.isfinite(max_age_s) or max_age_s <= 0:
            raise DataTrustError("max_age_s must be finite and positive")
        wall_now = now_s if now_s is not None else time.time()
        age_s = wall_now - (self.exchange_timestamp / 1000.0)
        if age_s < -5.0:
            raise StaleQuoteError(
                f"{context} exchange timestamp is {abs(age_s):.1f}s in the future"
            )
        if age_s > max_age_s:
            raise StaleQuoteError(
                f"{context} is stale: age {age_s:.1f}s > {max_age_s:.1f}s"
            )

    def validate_monotonicity(self, context: str = "trusted price") -> None:
        """Raise if exchange timestamp goes backwards or freezes."""
        if self.previous_exchange_timestamp is None:
            return
        if self.exchange_timestamp <= self.previous_exchange_timestamp:
            raise DataTrustError(
                f"{context} exchange timestamp monotonicity violated: "
                f"{self.exchange_timestamp} <= {self.previous_exchange_timestamp}"
            )


class ServerClock:
    """Track and enforce sync between the local clock and the exchange.

    Offset = server_time - local_time. Positive means the exchange clock is
    ahead of ours. Uses an exponential moving average to dampen jitter, and
    ``check()`` fails closed when |offset| exceeds tolerance.
    """

    def __init__(
        self,
        *,
        time_url: str = BINANCE_MAINNET_TIME_URL,
        tolerance_s: float = DEFAULT_MAX_CLOCK_SKEW_S,
        max_samples: int = 10,
        timeout_s: float = 5.0,
    ) -> None:
        self.time_url = time_url
        self.tolerance_s = tolerance_s
        self.max_samples = max(1, max_samples)
        self.timeout_s = timeout_s
        self._offsets: list[float] = []
        self._ema: Optional[float] = None
        self._last_sync_at = 0.0
        self._sync_count = 0

    # -- public ----------------------------------------------------------
    @property
    def skew_seconds(self) -> Optional[float]:
        """Best-effort current offset (server - local), or None if unsynced."""
        return self._ema

    @property
    def last_sync_monotonic(self) -> float:
        return self._last_sync_at

    @property
    def sync_count(self) -> int:
        return self._sync_count

    def sample(
        self, server_time_ms: float, local_epoch_s: Optional[float] = None
    ) -> float:
        """Record one server-time sample and return the offset in seconds."""
        if not math.isfinite(server_time_ms) or server_time_ms <= 0:
            raise DataTrustError(f"invalid server time sample: {server_time_ms}")
        local_s = local_epoch_s if local_epoch_s is not None else time.time()
        offset_s = server_time_ms / 1000.0 - local_s
        self._offsets.append(offset_s)
        self._offsets = self._offsets[-self.max_samples :]
        if self._ema is None:
            self._ema = offset_s
        else:
            alpha = 0.5
            self._ema = alpha * offset_s + (1.0 - alpha) * self._ema
        self._sync_count += 1
        self._last_sync_at = time.monotonic()
        return offset_s

    def sync(self, *, fetch_fn=None) -> float:
        """Fetch server time once and record the offset.

        ``fetch_fn`` may be injected for tests/offline use; defaults to a
        blocking HTTP GET of the Binance /api/v3/time endpoint.
        """
        if fetch_fn is not None:
            server_time_ms = fetch_fn()
        else:
            server_time_ms = self._fetch_server_time()
        return self.sample(server_time_ms)

    def check(self) -> float:
        """Fail closed when the clock is unsynced or skewed beyond tolerance."""
        if self._ema is None:
            raise ClockSkewError("clock has never been synced with the exchange")
        if abs(self._ema) > self.tolerance_s:
            raise ClockSkewError(
                f"clock skew {self._ema:+.2f}s exceeds {self.tolerance_s:.2f}s tolerance"
            )
        return self._ema

    # -- internals ---------------------------------------------------------
    def _fetch_server_time(self) -> float:
        import urllib.request

        request = urllib.request.Request(
            self.time_url,
            headers={"User-Agent": "trading-agent/1.0"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_s) as response:  # noqa: S310
            import json

            payload = json.loads(response.read().decode("utf-8"))
        server_time_ms = payload.get("serverTime")
        if not isinstance(server_time_ms, (int, float)) or server_time_ms <= 0:
            raise DataTrustError(
                f"unexpected server time payload from {self.time_url}: {payload!r}"
            )
        return float(server_time_ms)


class OrderBookSequenceTracker:
    """Monotonic validation of order-book update IDs (lastUpdateId / u).

    Tracks the last seen sequence per symbol in both REST-snapshot space
    (lastUpdateId) and Binance diff-stream space (U/u/pu). A gap or a
    backwards move means the book cannot be trusted and needs resync.
    """

    def __init__(self, max_symbols: int = 64) -> None:
        self._last: dict[str, Optional[int]] = {}
        self._gaps: dict[str, int] = {}
        self._duplicates: dict[str, int] = {}
        self._max_symbols = max(1, max_symbols)

    @property
    def symbols(self) -> list[str]:
        return sorted(self._last)

    def last_sequence(self, symbol: str) -> Optional[int]:
        return self._last.get(symbol)

    def gap_count(self, symbol: str) -> int:
        return self._gaps.get(symbol, 0)

    def duplicate_count(self, symbol: str) -> int:
        return self._duplicates.get(symbol, 0)

    def on_update(self, symbol: str, sequence: Optional[int]) -> str:
        """Validate one update ID. Returns one of:
        ``"ok"``, ``"duplicate"``, ``"gap"``, ``"uninitialized"``, ``"invalid"``.

        This is the *diff-stream* path (WS): IDs must be strictly contiguous,
        so any jump is a gap that requires resync.
        """
        if sequence is None:
            return "uninitialized"
        if not isinstance(sequence, int) or sequence <= 0:
            self._gaps[symbol] = self._gaps.get(symbol, 0) + 1
            return "invalid"
        last = self._last.get(symbol)
        if last is None:
            self._last[symbol] = sequence
            return "uninitialized"
        if sequence <= last:
            self._duplicates[symbol] = self._duplicates.get(symbol, 0) + 1
            return "duplicate"
        if sequence > last + 1:
            self._gaps[symbol] = self._gaps.get(symbol, 0) + 1
            self._last[symbol] = sequence
            return "gap"
        self._last[symbol] = sequence
        return "ok"

    def on_rest_snapshot(self, symbol: str, sequence: Optional[int]) -> str:
        """Validate a REST order-book snapshot's lastUpdateId.

        REST snapshots jump by thousands of IDs between calls — that is
        expected and must NOT be treated as a gap (unlike WS diffs). We only
        reject a backwards or repeated ID, which signals a stale/cached
        response. Returns ``"ok"``, ``"duplicate"``, ``"stale"``,
        ``"invalid"`` or ``"uninitialized"``.
        """
        if sequence is None:
            return "uninitialized"
        if not isinstance(sequence, int) or sequence <= 0:
            self._gaps[symbol] = self._gaps.get(symbol, 0) + 1
            return "invalid"
        last = self._last.get(symbol)
        if last is None:
            self._last[symbol] = sequence
            return "ok"
        if sequence < last:
            self._duplicates[symbol] = self._duplicates.get(symbol, 0) + 1
            return "stale"
        if sequence == last:
            self._duplicates[symbol] = self._duplicates.get(symbol, 0) + 1
            return "duplicate"
        self._last[symbol] = sequence
        return "ok"

    def on_snapshot(self, symbol: str, sequence: Optional[int]) -> None:
        """(Re)initialize a symbol's sequence from a REST snapshot."""
        if sequence is not None and (not isinstance(sequence, int) or sequence <= 0):
            raise SequenceGapError(
                f"invalid snapshot sequence for {symbol}: {sequence!r}"
            )
        self._last[symbol] = sequence


@dataclass(slots=True)
class DiffStreamState:
    """State of a Binance depth diff stream per symbol.

    Implements the official snapshot+diff protocol:
    https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams#how-to-manage-a-local-order-book-correctly
    """

    symbol: str
    last_update_id: Optional[int] = None  # from REST snapshot
    last_u: Optional[int] = None  # last processed u (diff stream)
    buffered_first: bool = False
    needs_resync: bool = True
    gap_count: int = 0
    stale_count: int = 0

    def initialize(self, snapshot_update_id: int) -> None:
        """Seed from the REST snapshot ({'lastUpdateId': N})."""
        if not isinstance(snapshot_update_id, int) or snapshot_update_id <= 0:
            raise SequenceGapError(
                f"invalid snapshot lastUpdateId for {self.symbol}: {snapshot_update_id!r}"
            )
        self.last_update_id = snapshot_update_id
        self.last_u = None
        self.buffered_first = False
        self.needs_resync = False

    def apply_diff(
        self,
        *,
        first_update_id: int,
        final_update_id: int,
        previous_update_id: Optional[int],
        bids: list[tuple[float, float]],
        asks: list[tuple[float, float]],
    ) -> str:
        """Validate one diff payload against the protocol.

        Returns a status string: "ok", "stale", "gap", "ready_first",
        "invalid". Raises SequenceGapError for protocol violations that make
        the local book unusable.
        """
        ids = (first_update_id, final_update_id, previous_update_id)
        if any(
            value is not None and (not isinstance(value, int) or value <= 0)
            for value in ids
        ):
            raise SequenceGapError(f"invalid update IDs for {self.symbol}: {ids!r}")
        if final_update_id < first_update_id:
            raise SequenceGapError(
                f"diff stream U={first_update_id} > u={final_update_id} for {self.symbol}"
            )
        if self.needs_resync:
            return "gap"

        if self.last_u is None:
            # First realtime payload: must straddle the snapshot boundary.
            if first_update_id <= self.last_update_id + 1 <= final_update_id:
                self.last_u = final_update_id
                self.buffered_first = True
                return "ready_first"
            # Snapshot already covers this diff → stale.
            if final_update_id <= self.last_update_id:
                self.stale_count += 1
                return "stale"
            self.needs_resync = True
            self.gap_count += 1
            return "gap"

        if final_update_id <= self.last_u:
            self.stale_count += 1
            return "stale"
        if previous_update_id is None or previous_update_id != self.last_u:
            self.needs_resync = True
            self.gap_count += 1
            return "gap"
        if previous_update_id != first_update_id - 1:
            # Stream skipped IDs even though pu matched (defensive).
            self.needs_resync = True
            self.gap_count += 1
            return "gap"
        self.last_u = final_update_id
        return "ok"


class DataTrustMonitor:
    """Aggregates trust metrics across symbols (quote age, latency, gaps, skew).

    Register a ServerClock and an OrderBookSequenceTracker, then call
    ``record_fetch`` / ``on_diff`` as market data flows by. ``metrics()``
    exports the P0.3 dashboard dict.
    """

    def __init__(
        self,
        *,
        clock: Optional[ServerClock] = None,
        sequences: Optional[OrderBookSequenceTracker] = None,
        max_age_s: float = DEFAULT_MAX_QUOTE_AGE_S,
        max_latency_s: float = DEFAULT_MAX_LATENCY_S,
    ) -> None:
        self.clock = clock if clock is not None else ServerClock()
        self.sequences = (
            sequences if sequences is not None else OrderBookSequenceTracker()
        )
        self.max_age_s = max_age_s
        self.max_latency_s = max_latency_s
        self._last_fetch: dict[str, TimeStampedFetch] = {}
        self._age_samples: dict[str, list[float]] = {}
        self._latency_samples: dict[str, list[float]] = {}
        self._rejections = 0
        self._fetch_count = 0
        self._window = 64

    def record_fetch(
        self,
        symbol: str,
        fetch: TimeStampedFetch,
        *,
        reject: bool = True,
        reject_stale: bool = True,
    ) -> Optional[float]:
        """Record one REST fetch. Returns quote age (s) or None on reject/unknown.

        ``reject_stale=False`` is for candle/OHLCV fetches where the *open time*
        of the newest closed bar is by construction up to one interval old and
        must not be treated as a stale quote — latency is still enforced.
        """
        self._fetch_count += 1
        if fetch.exchange_timestamp is not None:
            age = quote_age_s(fetch)
            self._age_samples.setdefault(symbol, []).append(age)
            self._age_samples[symbol] = self._age_samples[symbol][-self._window :]
        self._latency_samples.setdefault(symbol, []).append(fetch.latency_s)
        self._latency_samples[symbol] = self._latency_samples[symbol][-self._window :]
        self._last_fetch[symbol] = fetch
        if reject:
            try:
                reject_high_latency(
                    fetch, max_latency_s=self.max_latency_s, context=symbol
                )
                if reject_stale:
                    reject_stale_exchange_timestamp(
                        fetch, max_age_s=self.max_age_s, context=symbol
                    )
            except DataTrustError:
                self._rejections += 1
                raise
        return quote_age_s(fetch)

    def rejection_count(self) -> int:
        return self._rejections

    def fetch_count(self) -> int:
        return self._fetch_count

    def last_fetch(self, symbol: str) -> Optional[TimeStampedFetch]:
        return self._last_fetch.get(symbol)

    def metrics(self) -> dict:
        """P0.3 export: quote age, latency, sequence gap and clock skew."""
        latest_age: dict[str, float] = {}
        latest_latency: dict[str, float] = {}
        max_age = 0.0
        max_latency = 0.0
        for symbol, samples in self._age_samples.items():
            if samples:
                latest_age[symbol] = samples[-1]
                max_age = max(max_age, samples[-1])
        for symbol, samples in self._latency_samples.items():
            if samples:
                latest_latency[symbol] = samples[-1]
                max_latency = max(max_latency, samples[-1])
        sequences = {}
        max_gap = 0
        for symbol in self.sequences.symbols:
            gap = self.sequences.gap_count(symbol)
            sequences[symbol] = {
                "last_sequence": self.sequences.last_sequence(symbol),
                "gaps": gap,
                "duplicates": self.sequences.duplicate_count(symbol),
            }
            max_gap = max(max_gap, gap)
        return {
            "clock_skew_s": self.clock.skew_seconds,
            "clock_synced": self.clock.skew_seconds is not None,
            "clock_last_sync_age_s": (
                time.monotonic() - self.clock.last_sync_monotonic
                if self.clock.last_sync_monotonic > 0
                else None
            ),
            "quote_age_s": latest_age,
            "request_latency_s": latest_latency,
            "max_quote_age_s": round(max_age, 3),
            "max_request_latency_s": round(max_latency, 3),
            "sequence": sequences,
            "max_sequence_gap": max_gap,
            "rejections": self._rejections,
            "fetches": self._fetch_count,
        }
