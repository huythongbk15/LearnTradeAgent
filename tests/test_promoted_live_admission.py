"""Local fixtures only: real registry/stores/authorities/planner, no network IO."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import os
import sys
import time

import polars as pl
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import live_enhanced_ma_binance as runner

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.portfolio import PortfolioSnapshot, ReconciliationState
from trading_agent.authority.promotion_store import PromotionRecord, PromotionStateStore
from trading_agent.authority.resolver import RuntimeStrategyResolver
from trading_agent.execution.batch_models import wrap_market_data
from trading_agent.execution.live_admission import PromotedLiveAdmissionProducer
from trading_agent.execution.live_safety import (
    LiveRiskLimits,
    LiveRiskStateStore,
    LiveSafetyError,
)
from trading_agent.research.artifact import (
    PersistentArtifactStore,
    StrategyArtifact,
    canonical_params,
    sha256_hex,
)
from trading_agent.research.promotion import ResearchPromotionEvent, ResearchStage
from trading_agent.strategies.canonical import build_default_registry


def promoted_stores(tmp_path, *, state="KNOWN", fingerprint="local-fixture-forecast"):
    artifacts = PersistentArtifactStore(tmp_path / "artifacts.db")
    promotions = PromotionStateStore(tmp_path / "promotions.db")
    params = {"fast_period": 1, "slow_period": 3}
    artifact = StrategyArtifact(
        strategy_name="ma_crossover",
        code_sha=build_default_registry().describe("ma_crossover").code_sha,
        data_manifest_sha=sha256_hex("local-fixture-training-data-not-live-evidence"),
        parameter_hash=sha256_hex(canonical_params(params)),
        execution_model_version="local-test",
        framework_version="local-test",
        metadata={
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "parameters": params,
            "calibration_state": state,
            "calibration_ece": 0.02,
            "ood_state": "KNOWN",
            "ood_score": 0.1,
            "regime_state": "KNOWN",
            "regime_entropy": 0.2,
            "interval_width": 0.05,
            "forecast_fingerprint": fingerprint,
            "calibration_artifact_id": "local-fixture-calibration-not-live-evidence",
        },
    )
    artifacts.add(artifact)
    event = ResearchPromotionEvent(
        subject_artifact_id=artifact.artifact_id,
        from_stage=ResearchStage.RESEARCH_VALIDATED,
        to_stage=ResearchStage.TESTNET_ELIGIBLE,
        evidence_ids=("local-fixture-evidence",),
        actor="local-test-only",
        timestamp=datetime.now(UTC),
    )
    promotions.upsert(
        PromotionRecord(
            artifact_id=artifact.artifact_id,
            stage=ResearchStage.TESTNET_ELIGIBLE,
            latest_event=event,
            updated_at=datetime.now(UTC),
        )
    )
    config = AuthorityConfig(environment=Environment.TESTNET)
    return (
        artifacts,
        promotions,
        PromotedLiveAdmissionProducer(
            RuntimeStrategyResolver(
                config, promotion_store=promotions, artifact_store=artifacts
            )
        ),
    )


def market_frame(*, sell=False):
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    close = (
        [100.0] * 397 + [99.0, 100.0, 98.0] if sell else [90.0] * 398 + [80.0, 100.0]
    )
    return pl.DataFrame(
        {
            "timestamp": [
                end - timedelta(hours=len(close) - i) for i in range(len(close))
            ],
            "open": close,
            "high": [p + 0.5 for p in close],
            "low": [p - 0.5 for p in close],
            "close": close,
            "volume": [10000.0] * len(close),
        }
    )


class AccountingBroker:
    """Decimal cash/inventory/fees oracle at the exchange boundary only."""

    def __init__(self):
        self.cash = Decimal("1000")
        self.quantity = Decimal("0")
        self.price = Decimal("100")
        self.orders = {}
        self.transactions = []
        self.cancel_calls = 0

    def get_account(self):
        return {
            "equity": float(self.cash + self.quantity * self.price),
            "cash": float(self.cash),
        }

    def get_positions(self):
        if self.quantity == 0:
            return []
        locked = sum(
            Decimal(str(order["qty"]))
            for order in self.orders.values()
            if order["type"] == "stop" and order["status"] == "open"
        )
        return [
            {
                "symbol": "BTC/USDT",
                "qty": float(self.quantity),
                "free_qty": float(max(Decimal(0), self.quantity - locked)),
                "locked_qty": float(locked),
                "market_value": float(self.quantity * self.price),
                "current_price": float(self.price),
            }
        ]

    def get_ticker(self, symbol):
        started = time.monotonic()
        return {
            "timestamp": datetime.now(UTC),
            "request_started_at": started,
            "received_at": time.monotonic(),
            "last": float(self.price),
            "bid": float(self.price),
            "ask": float(self.price),
        }

    def get_order_book(self, symbol, limit=50):
        started = time.monotonic()
        return {
            "timestamp": datetime.now(UTC),
            "request_started_at": started,
            "received_at": time.monotonic(),
            "bids": [(float(self.price), 1000.0)],
            "asks": [(float(self.price), 1000.0)],
        }

    def normalize_order_amount(self, symbol, amount, *, reference_price):
        return float(Decimal(str(amount)).quantize(Decimal("0.00001")))

    def place_order(self, order):
        qty = Decimal(str(order.size)).quantize(Decimal("0.00001"))
        stop = order.type.value == "stop"
        side = order.side.value
        fee = Decimal(0) if stop else qty * self.price * Decimal("0.001")
        if not stop:
            signed = qty if side == "buy" else -qty
            self.quantity += signed
            self.cash -= signed * self.price + fee
            assert self.quantity >= 0 and self.cash >= 0
            self.transactions.append((side, qty, self.price, fee))
        result = {
            "id": "local-order-" + str(len(self.orders) + 1),
            "client_order_id": order.client_order_id,
            "symbol": order.symbol.pair,
            "side": side,
            "type": order.type.value,
            "qty": float(qty),
            "status": "open" if stop else "filled",
            "filled_qty": 0.0 if stop else float(qty),
            "avg_fill_price": 0.0 if stop else float(self.price),
            "fees": {"USDT": float(fee)},
            "quote_cost": float(qty * self.price),
            "stop_price": float(order.stop_price) if stop else None,
            "error": None,
        }
        self.orders[order.client_order_id] = result
        return dict(result)

    def get_order_by_client_id(self, client_order_id, symbol):
        value = self.orders.get(client_order_id)
        return dict(value) if value else None

    def cancel_order(self, order_id, symbol):
        self.cancel_calls += 1
        for order in self.orders.values():
            if order["id"] == order_id:
                order["status"] = "cancelled"
                return True
        return False


def produce(tmp_path, producer, *, sell=False, **bindings):
    broker = AccountingBroker()
    frame = market_frame(sell=sell)
    quote = runner.trusted_broker_quote(broker, "BTC/USDT", buy=True)
    lifecycle, _ = runner.build_canonical_execution_stack(
        broker, event_store_path=str(tmp_path / "events.db")
    )
    snapshot = PortfolioSnapshot(
        equity=1000,
        available_cash=1000,
        positions={},
        symbol_exposures={},
        gross_exposure=0,
        reconciliation_state=ReconciliationState.RECONCILED,
    )
    return producer.produce(
        inputs={"BTC/USDT": wrap_market_data("BTC/USDT", "1h", frame, "local-fixture")},
        quotes={"BTC/USDT": quote},
        snapshot=snapshot,
        allocations={"BTC/USDT": 0.2},
        lifecycle=lifecycle,
        environment=Environment.TESTNET,
        limits=LiveRiskLimits.for_profile("testnet"),
        states={"BTC/USDT": {"atr": 5.0, "recent_high": 100.0}},
        **bindings,
    )


def test_real_promoted_producer_creates_attributable_canonical_buy(tmp_path):
    _, _, producer = promoted_stores(tmp_path)
    decisions = produce(tmp_path, producer)
    assert len(decisions) == 1
    assert decisions[0]["action"] == "BUY"
    risk = decisions[0]["risk_decision"]
    assert decisions[0]["permission_context"].risk_decision is risk
    assert risk.forecast_fingerprint == "local-fixture-forecast"
    assert len(risk.authority_chain) >= 3


@pytest.mark.parametrize("field", ["artifact", "strategy", "parameters"])
def test_signed_runtime_subject_mismatch_blocks_admission(tmp_path, field):
    artifacts, _, producer = promoted_stores(tmp_path)
    runtime = producer.resolver.resolve_for("BTC/USDT", "1h", Environment.TESTNET)
    bindings = {
        "expected_artifact_ids": {"BTC/USDT": runtime.artifact_id},
        "expected_strategy_name": runtime.strategy_name,
        "expected_parameters": runtime.parameters,
    }
    if field == "artifact":
        bindings["expected_artifact_ids"] = {"BTC/USDT": "wrong-subject"}
    elif field == "strategy":
        bindings["expected_strategy_name"] = "enhanced_ma"
    else:
        bindings["expected_parameters"] = {"fast_period": 2, "slow_period": 3}
    with pytest.raises(LiveSafetyError, match="signed strategy evidence"):
        produce(tmp_path, producer, **bindings)


@pytest.mark.parametrize("fault", ["missing", "stale", "future", "missing_start"])
def test_actual_ccxt_receipt_must_be_observed_and_fresh(fault):
    broker = AccountingBroker()
    quote = broker.get_ticker("BTC/USDT")
    if fault == "missing":
        quote.pop("received_at")
    elif fault == "stale":
        quote["received_at"] -= 61
    elif fault == "future":
        quote["received_at"] += 5
    else:
        quote.pop("request_started_at")
    broker.get_ticker = lambda symbol: quote
    assert runner.trusted_broker_quote(broker, "BTC/USDT", buy=True) is None


@pytest.mark.parametrize(
    "fault", ["missing_promotion", "missing_calibration", "missing_forecast"]
)
def test_producer_has_no_allow_fallback(tmp_path, fault):
    artifacts, promotions, producer = promoted_stores(
        tmp_path,
        state="MISSING" if fault == "missing_calibration" else "KNOWN",
        fingerprint="" if fault == "missing_forecast" else "local-fixture-forecast",
    )
    if fault == "missing_promotion":
        producer = PromotedLiveAdmissionProducer(
            RuntimeStrategyResolver(
                producer.config,
                promotion_store=PromotionStateStore(tmp_path / "empty.db"),
                artifact_store=artifacts,
            )
        )
    with pytest.raises(LiveSafetyError):
        produce(tmp_path, producer)


def install_exchange_boundary(monkeypatch, tmp_path, broker, frame):
    class NoNetworkAdapter:
        def __init__(self, config):
            self.config = config

        async def connect(self):
            pass

        async def disconnect(self):
            pass

    for name, value in {
        "BINANCE_TESTNET_API_KEY": "local-fixture-key",
        "BINANCE_TESTNET_API_SECRET": "local-fixture-secret",
        "LIVE_ACCOUNT_ID": "local-fixture-venue-uid",
        "LIVE_SAFETY_HMAC_KEY": "local-fixture-integrity-key-not-for-live",
        "TRADING_EXECUTION_ENABLED": "true",
        "TRADING_MODE": "testnet",
        "TRADING_KILL_SWITCH": "false",
        "TRADING_ENTRY_KILL_SWITCH": "false",
        "LIVE_PROMOTION_DB": str(tmp_path / "promotions.db"),
        "LIVE_ARTIFACT_DB": str(tmp_path / "artifacts.db"),
        # Never pick up a real operator authorization from the working tree.
        "LIVE_OPERATIONAL_AUTHORIZATION": str(tmp_path / "operational_authorization.json"),
    }.items():
        monkeypatch.setenv(name, value)
    original_session = runner.LiveAccountSession

    def isolated_session(**kwargs):
        kwargs["root"] = tmp_path / "accounts"
        return original_session(**kwargs)

    monkeypatch.setattr(runner, "LiveAccountSession", isolated_session)
    monkeypatch.setattr(runner, "CCXTAdapter", NoNetworkAdapter)
    monkeypatch.setattr(runner, "LiveBroker", lambda *args, **kwargs: broker)
    monkeypatch.setattr(
        runner.ServerClock, "_fetch_server_time", lambda self: time.time() * 1000
    )
    monkeypatch.setattr(runner, "get_recent_df", lambda pair, monitor=None: frame)


def test_full_run_default_producer_buy_fill_stop_restart_and_sell_against_decimal_oracle(
    tmp_path, monkeypatch
):
    promoted_stores(tmp_path)
    broker = AccountingBroker()
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame())
    args = runner.build_parser().parse_args(
        [
            "--execute",
            "--testnet",
            "--symbols",
            "BTC/USDT",
            "--weights",
            "20",
            "--state-file",
            str(tmp_path / "risk.json"),
            "--audit-log",
            str(tmp_path / "audit.jsonl"),
        ]
    )
    assert (
        runner.run(args) == 0
    )  # No injected admission; default producer uses real stores.
    assert len(broker.transactions) == 1
    side, qty, price, fee = broker.transactions[0]
    assert side == "buy"
    assert broker.quantity == qty
    assert broker.cash == Decimal("1000") - qty * price - fee
    store = LiveRiskStateStore(
        tmp_path / "risk.json", integrity_key="local-fixture-integrity-key-not-for-live"
    )
    buy = next(
        record
        for record in store.state.order_ledger.values()
        if record["side"] == "BUY"
    )
    assert buy["filled_quantity"] == pytest.approx(float(qty))
    assert buy["average_fill_price"] == pytest.approx(float(price))
    assert buy["fees"]["USDT"] == pytest.approx(float(fee))
    protection = store.protective_order_state("BTC/USDT")["active"]
    assert protection["quantity"] == pytest.approx(float(qty))
    assert protection["status"] == "open"

    # Restart/replay the actual entry point. If a same-bar second slice is
    # considered, the persisted duplicate guard must stop it before broker IO.
    try:
        runner.run(args)
    except LiveSafetyError as exc:
        assert "reserved" in str(exc) or "signal already" in str(exc)
    assert len(broker.transactions) == 1

    broker.price = Decimal("98")
    monkeypatch.setattr(
        runner, "get_recent_df", lambda pair, monitor=None: market_frame(sell=True)
    )
    assert runner.run(args) == 0
    assert len(broker.transactions) == 2
    sell_side, sell_qty, sell_price, sell_fee = broker.transactions[-1]
    assert sell_side == "sell" and sell_qty == qty and broker.quantity == 0
    expected_cash = Decimal("1000") + qty * (sell_price - price) - fee - sell_fee
    assert broker.cash == expected_cash
    assert broker.get_account()["equity"] == pytest.approx(float(expected_cash))
    reloaded = LiveRiskStateStore(
        tmp_path / "risk.json", integrity_key="local-fixture-integrity-key-not-for-live"
    )
    assert reloaded.unfinished_orders() == {}
    assert not reloaded.protective_order_state("BTC/USDT").get("active")
    assert broker.cancel_calls >= 1


@pytest.mark.parametrize(
    "fault", ["missing_promotion", "missing_calibration", "missing_forecast"]
)
def test_full_entrypoint_invalid_authority_creates_zero_exchange_orders(
    tmp_path, monkeypatch, fault
):
    if fault == "missing_promotion":
        PersistentArtifactStore(tmp_path / "artifacts.db")
        PromotionStateStore(tmp_path / "promotions.db")
    else:
        promoted_stores(
            tmp_path,
            state="MISSING" if fault == "missing_calibration" else "KNOWN",
            fingerprint="" if fault == "missing_forecast" else "local-fixture-forecast",
        )
    broker = AccountingBroker()
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame())
    args = runner.build_parser().parse_args(
        [
            "--execute",
            "--testnet",
            "--symbols",
            "BTC/USDT",
            "--weights",
            "20",
            "--state-file",
            str(tmp_path / "risk.json"),
            "--audit-log",
            str(tmp_path / "audit.jsonl"),
        ]
    )
    if fault == "missing_promotion":
        # Testnet with nothing promoted locks entries and completes the cycle
        # (operator decision 2026-10-29); it still opens nothing.
        assert runner.run(args) == 0
    else:
        # A promoted runtime with broken authority still fails closed.
        with pytest.raises(LiveSafetyError):
            runner.run(args)
    assert broker.orders == {} and broker.transactions == []


class SimulatedProcessDeath(BaseException):
    """Bypass ordinary retry handling to reproduce a post-accept process crash."""


def local_run_args(tmp_path):
    return runner.build_parser().parse_args(
        [
            "--execute",
            "--testnet",
            "--symbols",
            "BTC/USDT",
            "--weights",
            "20",
            "--state-file",
            str(tmp_path / "risk.json"),
            "--audit-log",
            str(tmp_path / "audit.jsonl"),
        ]
    )


@pytest.mark.parametrize("lookup_missing", [False, True])
def test_full_entrypoint_crash_after_venue_accept_reconciles_without_duplicate(
    tmp_path, monkeypatch, lookup_missing
):
    promoted_stores(tmp_path)

    class CrashBroker(AccountingBroker):
        crashed = False

        def place_order(self, order):
            result = super().place_order(order)
            if order.type.value == "market" and not self.crashed:
                self.crashed = True
                raise SimulatedProcessDeath("venue accepted before process died")
            return result

    broker = CrashBroker()
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame())
    args = local_run_args(tmp_path)
    with pytest.raises(SimulatedProcessDeath):
        runner.run(args)
    assert len(broker.transactions) == 1
    if lookup_missing:
        monkeypatch.setenv("LIVE_ORDER_RECONCILE_TIMEOUT_SECONDS", "1")
        broker.get_order_by_client_id = lambda *args: None
        with pytest.raises(LiveSafetyError):
            runner.run(args)
        assert len(broker.transactions) == 1
        state = LiveRiskStateStore(
            tmp_path / "risk.json",
            integrity_key="local-fixture-integrity-key-not-for-live",
        )
        assert state.unfinished_orders()
        return
    assert runner.run(args) == 0
    assert len(broker.transactions) == 1
    state = LiveRiskStateStore(
        tmp_path / "risk.json", integrity_key="local-fixture-integrity-key-not-for-live"
    )
    assert not state.unfinished_orders()
    assert state.protective_order_state("BTC/USDT")["active"][
        "quantity"
    ] == pytest.approx(float(broker.quantity))


def test_full_entrypoint_partial_sell_cancels_residual_and_protects_inventory(
    tmp_path, monkeypatch
):
    promoted_stores(tmp_path)

    class PartialBroker(AccountingBroker):
        def place_order(self, order):
            if order.type.value != "market" or order.side.value != "sell":
                return super().place_order(order)
            from dataclasses import replace

            result = super().place_order(replace(order, size=order.size / 2))
            result.update(status="partial", qty=float(order.size))
            self.orders[order.client_order_id] = result
            return dict(result)

    broker = PartialBroker()
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame())
    args = local_run_args(tmp_path)
    assert runner.run(args) == 0
    bought = broker.quantity
    # Candle breaches the ATR trail, but the fresh quote has recovered above it.
    # Residual inventory therefore still admits a valid replacement stop.
    broker.price = Decimal("98.5")
    monkeypatch.setattr(
        runner, "get_recent_df", lambda pair, monitor=None: market_frame(sell=True)
    )
    with pytest.raises(LiveSafetyError, match="cancelled; batch stopped"):
        runner.run(args)
    assert len(broker.transactions) == 2
    sold = broker.transactions[-1][1]
    assert broker.quantity == bought - sold and broker.quantity > 0
    expected_cash = Decimal("1000") - sum(
        qty * price * (Decimal(1) if side == "buy" else Decimal(-1)) + fee
        for side, qty, price, fee in broker.transactions
    )
    assert broker.cash == expected_cash
    state = LiveRiskStateStore(
        tmp_path / "risk.json", integrity_key="local-fixture-integrity-key-not-for-live"
    )
    assert not state.unfinished_orders()
    active = state.protective_order_state("BTC/USDT")["active"]
    assert active["quantity"] == pytest.approx(float(broker.quantity))
    assert broker.cancel_calls >= 2


# ── Testnet without a promoted runtime: entries locked, exits managed ─────


def _testnet_args(tmp_path):
    return runner.build_parser().parse_args(
        [
            "--execute", "--testnet", "--symbols", "BTC/USDT", "--weights", "20",
            "--state-file", str(tmp_path / "risk.json"),
            "--audit-log", str(tmp_path / "audit.jsonl"),
        ]
    )


def _audit_events(tmp_path):
    import json

    return [
        json.loads(line)["event"]
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
        if line.strip()
    ]


@pytest.mark.parametrize("stores", ["absent", "empty"])
def test_testnet_without_promoted_runtime_locks_entries_and_completes(
    tmp_path, monkeypatch, stores
):
    if stores == "empty":
        PromotionStateStore(tmp_path / "promotions.db")
        PersistentArtifactStore(tmp_path / "artifacts.db")
    broker = AccountingBroker()
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame())

    assert runner.run(_testnet_args(tmp_path)) == 0

    assert broker.transactions == []  # a buy signal, but nothing may open exposure
    events = _audit_events(tmp_path)
    assert "entries_locked_no_promoted_runtime" in events
    assert "run_failed" not in events


def test_testnet_without_promoted_runtime_still_exits_a_held_position(
    tmp_path, monkeypatch
):
    broker = AccountingBroker()
    broker.quantity = Decimal("2")
    broker.price = Decimal("98")  # the sell frame closes at 98
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame(sell=True))
    # No ATR trail exit: the sell must come from the strategy signal through
    # the entry-locked path, not from the forced safety-exit path.
    monkeypatch.setattr(runner, "apply_atr_protection", lambda **kwargs: None)

    assert runner.run(_testnet_args(tmp_path)) == 0

    sides = [side for side, *_ in broker.transactions]
    assert "sell" in sides and "buy" not in sides
    assert "entries_locked_no_promoted_runtime" in _audit_events(tmp_path)


# ── Run identity, stages and interruption (I1-I3) ──────────────────────────


def _main_argv(tmp_path):
    return [
        "live_enhanced_ma_binance.py", "--execute", "--testnet", "--symbols", "BTC/USDT",
        "--weights", "20", "--state-file", str(tmp_path / "risk.json"),
        "--audit-log", str(tmp_path / "audit.jsonl"),
    ]


def _audit_records(tmp_path):
    import json

    return [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
        if line.strip()
    ]


def test_main_ties_start_and_end_and_records_identity_and_stages(tmp_path, monkeypatch):
    broker = AccountingBroker()
    install_exchange_boundary(monkeypatch, tmp_path, broker, market_frame())
    monkeypatch.setattr(sys, "argv", _main_argv(tmp_path))

    assert runner.main() == 0

    records = _audit_records(tmp_path)
    start, end = records[0], records[-1]
    assert start["event"] == "run_started" and end["event"] == "run_completed"
    assert start["correlation_id"] and start["correlation_id"] == end["correlation_id"]
    assert {"boot_id", "starttime_ticks", "source_commit"} <= set(start["details"])
    stages = [r["details"]["stage"] for r in records if r["event"] == "run_stage"]
    assert stages[:2] == ["account_read", "signals_computed"]
    assert all(r["correlation_id"] == start["correlation_id"] for r in records)


@pytest.mark.parametrize(("signame", "code"), [("SIGTERM", 143), ("SIGHUP", 129)])
def test_signal_during_a_run_records_run_interrupted(tmp_path, monkeypatch, signame, code):
    import signal as os_signal

    def killed_mid_run(args):
        os.kill(os.getpid(), getattr(os_signal, signame))
        time.sleep(5)
        raise AssertionError("SIGTERM did not interrupt the run")

    monkeypatch.setattr(runner, "run", killed_mid_run)
    monkeypatch.setattr(sys, "argv", _main_argv(tmp_path))

    assert runner.main() == code

    records = _audit_records(tmp_path)
    assert [r["event"] for r in records] == ["run_started", "run_interrupted"]
    assert records[1]["details"]["signal"] == signame
    assert records[1]["correlation_id"] == records[0]["correlation_id"]
    assert os_signal.getsignal(os_signal.SIGTERM) is os_signal.SIG_DFL
    assert os_signal.getsignal(os_signal.SIGHUP) is os_signal.SIG_DFL
