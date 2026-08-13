"""Opt-in Binance Spot Testnet acceptance test for the live runner (P0.1).

Never runs in unit CI.  Opt in with ``LIVE_TESTNET_ACCEPTANCE=1`` and provide
``BINANCE_TESTNET_API_KEY`` / ``BINANCE_TESTNET_API_SECRET``.

Scope: connect to testnet.binance.vision, verify clock sync and account
readability, then exercise the exchange-native protective-stop lifecycle
against the testnet account's *existing* positions.  The test never opens a
new position: if the account holds no eligible position it is skipped with
guidance.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import live_enhanced_ma_binance as runner
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter, ExchangeConfig
from trading_agent.exchanges.live_broker import LiveBroker
from trading_agent.exchanges.models import MarketType
from trading_agent.execution.data_trust import (
    DataTrustError,
    DataTrustMonitor,
    ServerClock,
)
from trading_agent.execution.live_safety import (
    LiveRiskLimits,
    LiveRiskStateStore,
)

pytestmark = pytest.mark.skipif(
    os.getenv("LIVE_TESTNET_ACCEPTANCE") != "1",
    reason=(
        "opt-in Binance Spot Testnet acceptance; set LIVE_TESTNET_ACCEPTANCE=1 "
        "with BINANCE_TESTNET_API_KEY/BINANCE_TESTNET_API_SECRET"
    ),
)

BINANCE_TESTNET_TIME_URL = "https://testnet.binance.vision/api/v3/time"


@pytest.fixture(scope="module")
def testnet_broker():
    api_key = os.getenv("BINANCE_TESTNET_API_KEY", "")
    secret = os.getenv("BINANCE_TESTNET_API_SECRET", "")
    if not api_key or not secret:
        pytest.fail(
            "LIVE_TESTNET_ACCEPTANCE=1 requires BINANCE_TESTNET_API_KEY and "
            "BINANCE_TESTNET_API_SECRET"
        )
    adapter = CCXTAdapter(
        ExchangeConfig(
            id="binance",
            name="Binance",
            api_key=api_key,
            secret=secret,
            testnet=True,
            enable_rate_limit=True,
            markets=[MarketType.SPOT],
            options={"defaultType": "spot"},
        )
    )
    asyncio.run(adapter.connect())
    broker = LiveBroker(
        "binance",
        adapter,
        pricing_symbols=[],
        strict_pricing=True,
    )
    try:
        yield broker, adapter
    finally:
        asyncio.run(adapter.disconnect())


def _eligible_positions(broker: LiveBroker) -> list[dict]:
    positions = broker.get_positions()
    return [position for position in positions if float(position.get("qty") or 0.0) > 0]


def _cancel_acceptance_stops(broker: LiveBroker, pair: str) -> None:
    """Cancel every live-runner protective order still open for one pair.

    Keeps the testnet account clean so the acceptance lifecycle is repeatable:
    stale stops from earlier runs would collide with the deterministic
    client order ID of the next run (Binance rejects them as duplicates).
    """
    symbol = runner.exchange_symbol(pair)
    try:
        orders = asyncio.run(broker.adapter.fetch_open_orders(symbol))
    except Exception:
        return
    for order in orders:
        client_id = str(getattr(order, "client_order_id", "") or "")
        order_id = str(getattr(order, "id", "") or "")
        if client_id.startswith("lta-ps-") and order_id:
            broker.cancel_order(order_id, symbol)


def test_clock_sync_and_account_readable(testnet_broker):
    """P0.3 prerequisite: trusted time and account access on testnet."""
    broker, _ = testnet_broker
    clock = ServerClock(
        time_url=BINANCE_TESTNET_TIME_URL,
        tolerance_s=runner.DEFAULT_CLOCK_SKEW_S,
    )
    monitor = DataTrustMonitor(clock=clock)
    try:
        clock.sync()
        skew = clock.check()
    except DataTrustError as exc:
        pytest.fail(f"clock sync failed on testnet: {exc}")
    assert monitor is not None
    account = broker.get_account()
    assert float(account.get("equity") or 0.0) >= 0.0


def test_protective_stop_lifecycle_on_existing_positions(testnet_broker):
    """P0.1 acceptance: idempotent exchange-native stop on testnet positions."""
    broker, _ = testnet_broker
    positions = _eligible_positions(broker)
    if not positions:
        pytest.skip(
            "testnet account has no open position; deposit funds and open a "
            "position on testnet.binance.vision before running the acceptance "
            "lifecycle"
        )
    # Exercise the lifecycle on the largest position only; running it for the
    # whole account would place stops for every dust balance and slow the test.
    position = max(positions, key=lambda p: float(p.get("market_value") or 0.0))
    pair = position["symbol"]
    current = float(position["current_price"])
    _cancel_acceptance_stops(broker, pair)

    limits = LiveRiskLimits.for_profile("testnet")
    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "risk_state.json")
        audit_path = os.path.join(tmp, "audit.jsonl")
        store = LiveRiskStateStore(state_path)
        store.bind_context(
            account="acceptance-test",
            strategy="acceptance-test",
            symbols=[pair],
        )
        store.bind_risk_limits(profile="testnet", limits=limits)
        store.observe_position_risk(
            pair,
            quantity=float(position["qty"]),
            observed_high=current,
            atr=current * 0.02,
            atr_multiplier=2.5,
        )

        states = {pair: {"atr_stop": current * 0.95, "price": current}}
        try:
            # First pass: create/tighten a protective stop for the position.
            runner.ensure_protective_stops(
                states=states,
                positions=[position],
                broker=broker,
                store=store,
                limits=limits,
                audit_log_path=audit_path,
            )
            first_state = store.protective_order_state(pair)
            active = first_state.get("active") or {}
            assert active.get("exchange_order_id"), (
                f"protective order missing exchange ID for {pair}"
            )
            first_exchange_id = active["exchange_order_id"]

            # Second pass must be idempotent: same exchange order, no duplicate.
            runner.ensure_protective_stops(
                states=states,
                positions=[position],
                broker=broker,
                store=store,
                limits=limits,
                audit_log_path=audit_path,
            )
            second_state = store.protective_order_state(pair)
            second_active = second_state.get("active") or {}
            assert second_active.get("exchange_order_id") == first_exchange_id, (
                f"protective stop for {pair} was duplicated on the exchange"
            )
        finally:
            _cancel_acceptance_stops(broker, pair)

        # Audit trail must contain the placement/replacement events.
        with open(audit_path, encoding="utf-8") as handle:
            events = [line for line in handle if line.strip()]
        operations = {json.loads(line).get("event") for line in events}
        assert "protective_stop_placed" in operations or (
            "protective_stop_replaced" in operations
        ), f"expected protective event in audit trail, got {operations}"


def test_risk_state_is_signed_and_loadable(testnet_broker):
    """State produced on testnet round-trips through signature validation."""
    broker, _ = testnet_broker
    integrity_key = runner.validate_integrity_key(
        os.getenv("LIVE_SAFETY_HMAC_KEY", "acceptance-test-key")
    )
    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "risk_state.json")
        store = LiveRiskStateStore(state_path, integrity_key=integrity_key)
        store.bind_context(
            account="acceptance-test",
            strategy="acceptance-test",
            symbols=["BTC/USDT"],
        )
        store.save()
        reloaded = LiveRiskStateStore(state_path, integrity_key=integrity_key)
        loaded = reloaded._load()
        assert loaded.account_fingerprint == "acceptance-test"
        assert loaded.strategy_fingerprint == "acceptance-test"
