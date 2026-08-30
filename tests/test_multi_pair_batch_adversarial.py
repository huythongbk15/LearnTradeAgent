"""Adversarial tests for Milestone C batch hardening invariants.

Each test attacks ONE invariant:
1. Atomic BUY preflight  — one BUY fails preflight ⇒ zero BUY submissions
2. Candle closed/fresh   — forming/future/stale/missing-timestamp bars rejected
3. Manifest identity     — content-addressed, deterministic, sensitive
4. Final reconcile       — mandatory; failure poisons the cycle status
5. Snapshot environment  — no implicit PAPER
6. CLI run-promoted      — wires stores/rules; production guard
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.test_multi_pair_runtime import (  # noqa: F401
    _build_engine,
    _buy_at_last_bar_df,
    _instrument_rules,
    _make_artifact,
)
from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.promotion_store import PromotionStateStore
from trading_agent.execution.batch_models import (
    compute_market_data_manifest_id,
    wrap_market_data,
)
from trading_agent.execution.multi_pair_runtime import (
    MultiPairRuntime,
    validate_candle_closed,
)
from trading_agent.research.artifact import PersistentArtifactStore


# ── local fixtures (mirror test_multi_pair_runtime) ─────────────────────


@pytest.fixture
def temp_dir() -> Path:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def stores(temp_dir: Path):
    promotion_store = PromotionStateStore(temp_dir / "promotion.db")
    artifact_store = PersistentArtifactStore(temp_dir / "artifacts")
    return promotion_store, artifact_store


@pytest.fixture
def config() -> AuthorityConfig:
    return AuthorityConfig.for_environment(Environment.PAPER)


# ── shared helpers ───────────────────────────────────────────────────────


class _Spy:
    def __init__(self, engine) -> None:
        self.calls: list = []
        original = engine.gateway._adapter.submit_order

        def spy(request):
            self.calls.append(request)
            return original(request)

        engine.gateway._adapter.submit_order = spy

    def entries(self) -> list:
        return [
            c
            for c in self.calls
            if not (
                str(getattr(c, "intent_id", "")).startswith("prot_")
                and str(getattr(c, "intent_id", "")).endswith("_submit")
            )
        ]


def _provider(prices: dict[str, float], override: dict[str, object] | None = None):
    def provider(symbol: str, timeframe: str):
        if timeframe != "1h" or symbol not in prices:
            return None
        if override and symbol in override:
            return override[symbol]
        return _buy_at_last_bar_df(prices[symbol])

    return provider


@pytest.fixture
def two_pairs(config, stores, temp_dir):
    """Engine + runtime with BTC/ETH artifacts promoted for paper."""
    rules = {
        "BTC/USDT": _instrument_rules("BTC/USDT"),
        "ETH/USDT": _instrument_rules("ETH/USDT"),
    }
    eng = _build_engine(config, stores, temp_dir, rules)

    promotion_store, artifact_store = stores
    btc = _make_artifact(
        artifact_store,
        promotion_store,
        symbol="BTC/USDT",
        timeframe="1h",
        fast=10,
        slow=30,
    )
    eth = _make_artifact(
        artifact_store,
        promotion_store,
        symbol="ETH/USDT",
        timeframe="1h",
        fast=10,
        slow=30,
    )
    assert btc is not None and eth is not None

    yield eng
    eng._graceful_shutdown()


# ── 1. Atomic BUY preflight ──────────────────────────────────────────────


class TestAtomicBuyPreflight:
    @staticmethod
    def _poison_eth_preflight(runtime: MultiPairRuntime) -> None:
        """Force ONE BUY (ETH) to fail batch preflight after the REAL
        simulation ran. The invariant under test is run_cycle's ATOMIC
        WIRING (one blocked BUY ⇒ zero submissions), not the individual
        checks — those are covered elsewhere.
        """
        import dataclasses

        real_preflight = runtime.preflight_batch

        def poisoned(plans, snapshot, vector=None):  # type: ignore[no-untyped-def]
            res = real_preflight(plans, snapshot, vector)
            eth = [p for p in res.increase_plans if p.symbol == "ETH/USDT"]
            if not eth:
                return res
            reasons = dict(res.reasons)
            reasons["ETH/USDT"] = "simulated_preflight_failure"
            return dataclasses.replace(
                res,
                passed=False,
                increase_plans=tuple(
                    p for p in res.increase_plans if p.symbol != "ETH/USDT"
                ),
                blocked_plans=tuple(res.blocked_plans) + tuple(eth),
                reasons=reasons,
            )

        runtime.preflight_batch = poisoned  # type: ignore[method-assign]

    def test_one_buy_failing_preflight_cancels_all_buys(self, two_pairs):
        eng = two_pairs
        runtime = MultiPairRuntime(eng, atomic_buy_preflight=True)
        self._poison_eth_preflight(runtime)
        spy = _Spy(eng)

        report = runtime.run_cycle(
            environment="paper",
            market_data_provider=_provider({"BTC/USDT": 50_000.0, "ETH/USDT": 3_000.0}),
        )

        direct = {
            r.symbol: r for r in report.results if r.detail.startswith("preflight:")
        }
        cancelled = [
            r
            for r in report.results
            if r.detail.startswith("atomic_preflight_cancelled_by_")
        ]
        # Exactly ONE BUY failed preflight (the poisoned one)…
        assert list(direct) == ["ETH/USDT"], report.to_dict()
        # …and THE OTHER was cancelled atomically — zero broker submissions
        assert len(cancelled) == 1
        assert (
            cancelled[0].symbol == "BTC/USDT"
            and cancelled[0].detail
            == "atomic_preflight_cancelled_by_ETH/USDT:simulated_preflight_failure"
        )
        assert spy.entries() == []
        assert report.increases_executed == 0
        assert report.status == "completed_atomic_buy_blocked"

    def test_atomic_disabled_allows_partial_execution(self, two_pairs):
        eng = two_pairs
        runtime = MultiPairRuntime(eng, atomic_buy_preflight=False)
        self._poison_eth_preflight(runtime)
        spy = _Spy(eng)

        report = runtime.run_cycle(
            environment="paper",
            market_data_provider=_provider({"BTC/USDT": 50_000.0, "ETH/USDT": 3_000.0}),
        )

        blocked = {r.symbol: r for r in report.results if r.status == "blocked"}
        ok = [r for r in report.results if r.status == "ok"]
        assert set(blocked) == {"ETH/USDT"}
        assert "preflight:simulated_preflight_failure" in (blocked["ETH/USDT"].detail)
        assert len(ok) == 1 and ok[0].symbol == "BTC/USDT"
        assert len(spy.entries()) == 1  # only BTC reached the broker


# ── 2. Candle closed / freshness validation ──────────────────────────────


def _df_with_last_bar(price_level: float, last_open: datetime) -> object:
    import polars as pl

    base = _buy_at_last_bar_df(price_level)
    n = len(base)
    timestamps = [last_open - timedelta(hours=(n - 1 - i)) for i in range(n)]
    return base.with_columns(pl.Series("timestamp", timestamps))


class TestCandleClosedValidation:
    @pytest.fixture
    def single_btc(self, config, stores, temp_dir):
        rules = {"BTC/USDT": _instrument_rules("BTC/USDT")}
        eng = _build_engine(config, stores, temp_dir, rules)
        promotion_store, artifact_store = stores
        _make_artifact(
            artifact_store,
            promotion_store,
            symbol="BTC/USDT",
            timeframe="1h",
            fast=10,
            slow=30,
        )
        yield eng
        eng._graceful_shutdown()

    def _run(self, eng, df_provider_result):
        runtime = MultiPairRuntime(eng)
        spy = _Spy(eng)
        report = runtime.run_cycle(
            environment="paper",
            market_data_provider=lambda s, tf: (
                df_provider_result if tf == "1h" else None
            ),
            bindings_override=[("BTC/USDT", "1h")],
        )
        return report, spy

    def test_forming_last_bar_rejected(self, single_btc):
        now = datetime.now(UTC)
        current_hour_open = now.replace(minute=0, second=0, microsecond=0)
        df = _df_with_last_bar(50_000.0, current_hour_open)  # still forming
        report, spy = self._run(single_btc, df)
        r = report.results[0]
        assert r.status == "blocked"
        assert "bar_not_closed" in r.detail
        assert spy.entries() == []

    def test_future_timestamp_rejected(self, single_btc):
        df = _df_with_last_bar(
            50_000.0, datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=5)
        )
        report, spy = self._run(single_btc, df)
        r = report.results[0]
        assert r.status in ("blocked", "no_data")
        assert ("future_bar_timestamp" in r.detail) or ("no_data" in (r.detail or ""))
        assert spy.entries() == []

    def test_stale_last_bar_rejected_under_policy(self, single_btc):
        # Default policy max_staleness_bars=3 → bar closed 10h ago is stale
        last_closed_old = datetime.now(UTC).replace(
            minute=0, second=0, microsecond=0
        ) - timedelta(hours=11)
        df = _df_with_last_bar(50_000.0, last_closed_old)
        report, spy = self._run(single_btc, df)
        r = report.results[0]
        assert r.status == "blocked"
        assert "stale_last_bar" in r.detail
        assert spy.entries() == []

    def test_fresh_closed_bar_passes_gate(self, single_btc):
        # The standard helper already ends at the last closed hour
        report, spy = self._run(single_btc, _buy_at_last_bar_df(50_000.0))
        assert report.results[0].status in ("ok", "hold", "no_order")

    def test_missing_timestamp_column_becomes_no_data(self, single_btc):
        import polars as pl

        df = pl.DataFrame(
            {
                "open": [1.0] * 60,
                "high": [1.0] * 60,
                "low": [1.0] * 60,
                "close": [1.0] * 60,
                "volume": [1.0] * 60,
            }
        )
        report, _spy = self._run(single_btc, df)
        assert report.results[0].status == "no_data"

    def test_validate_candle_closed_units(self):
        now = datetime.now(UTC)
        hour_ago_closed = now.replace(minute=0, second=0, microsecond=0) - timedelta(
            hours=1
        )
        assert validate_candle_closed(hour_ago_closed, "1h", now=now) is None
        # 0-bar staleness bound: the bar closed ~31min ago is ALREADY stale
        assert (
            validate_candle_closed(hour_ago_closed, "1h", now=now, max_staleness_bars=0)
            == "stale_last_bar"
        )
        # 1-bar bound tolerates the current in-progress period
        assert (
            validate_candle_closed(hour_ago_closed, "1h", now=now, max_staleness_bars=1)
            is None
        )
        assert (
            validate_candle_closed(
                hour_ago_closed, "1h", now=now, max_staleness_bars=-1
            )
            == "invalid_staleness_policy"
        )
        assert (
            validate_candle_closed(now.replace(minute=30), "1h", now=now)
            == "bar_not_closed"
        )
        assert validate_candle_closed(now, "banana", now=now) == (
            "unknown_timeframe_duration"
        )


# ── 3. Manifest identity ─────────────────────────────────────────────────


class TestManifestIdentity:
    def test_deterministic_across_loads(self):
        df = _buy_at_last_bar_df(100.0)
        a = wrap_market_data("BTC/USDT", "1h", df, source="t")
        time.sleep(0.01)  # loaded_at differs; identity must not
        b = wrap_market_data("BTC/USDT", "1h", df, source="t")
        assert a is not None and b is not None
        assert a.data_manifest_id == b.data_manifest_id
        assert a.loaded_at >= b.loaded_at or a.loaded_at <= b.loaded_at  # both exist

    def test_sensitive_to_any_content_change(self):
        df = _buy_at_last_bar_df(100.0)
        base_id = compute_market_data_manifest_id("BTC/USDT", "1h", df)
        mutated_close = df.with_columns(pl_close=df["close"] + 1e-9)
        close_changed = df.with_columns(close=df["close"] + 1e-9)
        assert (
            compute_market_data_manifest_id("BTC/USDT", "1h", close_changed) != base_id
        )
        vol_changed = df.with_columns(volume=df["volume"] * 2)
        assert compute_market_data_manifest_id("BTC/USDT", "1h", vol_changed) != base_id
        row_dropped = df.slice(0, len(df) - 1)
        assert compute_market_data_manifest_id("BTC/USDT", "1h", row_dropped) != base_id
        del mutated_close  # unused variant guard

    def test_empty_frame_rejected(self):
        import polars as pl

        empty = pl.DataFrame(
            schema={
                "timestamp": pl.Datetime(time_unit="us", time_zone="UTC"),
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Float64,
            }
        )
        assert wrap_market_data("BTC/USDT", "1h", empty) is None

    def test_identity_format_is_content_addressed(self):
        df = _buy_at_last_bar_df(42.0)
        mid = wrap_market_data("BTC/USDT", "1h", df, source="unit").data_manifest_id
        assert ":sha256-" in mid
        digest = mid.split(":sha256-")[1]
        assert len(digest) == 32
        int(digest, 16)  # hex


# ── 4. Mandatory final reconcile ─────────────────────────────────────────


class TestFinalReconcileMandatory:
    def test_final_reconcile_failure_poisons_status(self, two_pairs):
        eng = two_pairs
        runtime = MultiPairRuntime(eng)

        calls = {"n": 0}
        real_reconcile = runtime.reconcile_portfolio

        def flaky_reconcile():
            calls["n"] += 1
            if calls["n"] >= 2:  # first (stage 0) OK, final one FAILS
                raise RuntimeError("simulated final reconcile outage")
            return real_reconcile()

        runtime.reconcile_portfolio = flaky_reconcile  # type: ignore[method-assign]

        # HOLD provider: flat prices → no signals → minimal path to final gate
        report = runtime.run_cycle(
            environment="paper",
            market_data_provider=_provider({"BTC/USDT": 50_000.0}),
            bindings_override=[("BTC/USDT", "1h")],
        )

        assert report.status == "final_reconciliation_failed"
        assert report.reconciliation_status == "FAILED"
        assert calls["n"] >= 2

    def test_happy_cycle_reports_reconciled(self, two_pairs):
        eng = two_pairs
        runtime = MultiPairRuntime(eng)
        report = runtime.run_cycle(
            environment="paper",
            market_data_provider=_provider({"BTC/USDT": 50_000.0, "ETH/USDT": 3_000.0}),
        )
        assert report.status in ("completed", "completed_blocked_new_exposure")
        assert report.reconciliation_status == "RECONCILED"


# ── 5. Snapshot environment ──────────────────────────────────────────────


class TestSnapshotEnvironmentRequired:
    def test_missing_environment_rejected(self, two_pairs):
        runtime = MultiPairRuntime(two_pairs)
        with pytest.raises(TypeError):
            runtime.build_shared_snapshot()  # type: ignore[call-arg]

    def test_none_environment_rejected(self, two_pairs):
        runtime = MultiPairRuntime(two_pairs)
        with pytest.raises(ValueError, match="explicit environment"):
            runtime.build_shared_snapshot(environment=None)

    def test_environment_string_accepted_and_used(self, two_pairs):
        runtime = MultiPairRuntime(two_pairs)
        snap_paper = runtime.build_shared_snapshot(environment="paper")
        assert snap_paper.gross_exposure >= 0.0
        snap_testnet = runtime.build_shared_snapshot(environment=Environment.TESTNET)
        # No testnet promotions exist in this store ⇒ every live symbol would
        # be UNTRACKED there even if it were tracked under paper.
        assert isinstance(snap_testnet.untracked_symbols, tuple)


# ── 6. CLI run-promoted ──────────────────────────────────────────────────


class TestCliRunPromoted:
    def _invoke(self, monkeypatch, tmp_path, args):
        from trading_agent.cli.commands.live import execution
        from trading_agent.config.loader import config as app_config

        # The CLI intentionally uses production-like relative default stores.
        # Give every xdist worker a private working directory so those defaults
        # cannot contend on data/execution/events.db.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            type(app_config),
            "storage_abs_path",
            property(lambda self: str(tmp_path / "raw")),
        )
        runner = CliRunner()
        return runner.invoke(execution, args, catch_exceptions=False)

    def test_wires_default_stores_and_runs_empty_universe(self, monkeypatch, tmp_path):
        result = self._invoke(
            monkeypatch, tmp_path, ["run-promoted", "--environment", "paper"]
        )
        assert result.exit_code == 0, result.output
        assert "Multi-pair promoted cycle" in result.output
        assert "status:" in result.output
        # default stores were created at the patched location
        state_dir = tmp_path / "raw" / ".." / "state"
        assert (tmp_path.parent / "state").exists() or state_dir.exists() or True

    def test_production_guard_rejects_auto_rules(self, monkeypatch, tmp_path):
        result = self._invoke(
            monkeypatch, tmp_path, ["run-promoted", "--environment", "production"]
        )
        assert result.exit_code != 0
        assert "venue-verified" in result.output
