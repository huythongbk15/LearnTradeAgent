import pytest

from trading_agent.execution.live_safety import (
    LiveAccountSession,
    LiveRiskLimits,
    LiveRiskStateStore,
    LiveSafetyError,
)


KEY = "local-account-session-integrity-key"


def session(
    tmp_path, *, account="venue-uid-1", state="risk.json", exchange="binance-testnet"
):
    return LiveAccountSession(
        account_id=account,
        exchange=exchange,
        state_path=tmp_path / state,
        integrity_key=KEY,
        root=tmp_path / "accounts",
    )


def test_same_account_different_state_paths_cannot_have_two_writers(tmp_path):
    with session(tmp_path):
        with pytest.raises(LiveSafetyError, match="lock"):
            with session(tmp_path, state="alternate.json"):
                pytest.fail("second writer admitted")
    with pytest.raises(LiveSafetyError, match="another risk-state path"):
        with session(tmp_path, state="alternate.json"):
            pytest.fail("fresh baseline admitted")
    assert not (tmp_path / "alternate.json").exists()


def test_accounts_and_exchange_modes_have_isolated_event_stores(tmp_path):
    with (
        session(tmp_path) as first,
        session(tmp_path, account="venue-uid-2", state="second.json") as second,
    ):
        assert first.event_store_path != second.event_store_path
    with session(tmp_path, exchange="binance-mainnet", state="mainnet.json") as mainnet:
        assert mainnet.event_store_path != first.event_store_path


def test_different_accounts_cannot_share_one_state_file_concurrently(tmp_path):
    with session(tmp_path):
        with pytest.raises(LiveSafetyError, match="lock"):
            with session(tmp_path, account="another-account"):
                pytest.fail("shared state writer admitted")


def test_restart_preserves_equity_baseline_and_signal_reservation(tmp_path):
    with session(tmp_path) as scoped:
        store = LiveRiskStateStore(scoped.state_path, integrity_key=KEY)
        store.bind_context(
            account=scoped.fingerprint, strategy="local-test", symbols=["BTC/USDT"]
        )
        store.observe_equity(1000.0, LiveRiskLimits())
        store.observe_equity(980.0, LiveRiskLimits())
        store.reserve_order(
            "client-key", canonical_intent_id="canonical-intent", signal_key="same-bar"
        )
    with session(tmp_path) as scoped:
        recovered = LiveRiskStateStore(scoped.state_path, integrity_key=KEY)
        assert recovered.metrics(980.0)["drawdown_pct"] == pytest.approx(0.02)
        assert (
            recovered.state.order_ledger["client-key"]["canonical_intent_id"]
            == "canonical-intent"
        )
        with pytest.raises(LiveSafetyError, match="signal already reserved"):
            recovered.reserve_order("different-client", signal_key="same-bar")


@pytest.mark.parametrize("fault", ["missing_state", "corrupt_state", "corrupt_binding"])
def test_missing_or_tampered_bound_state_never_bootstraps_a_new_baseline(
    tmp_path, fault
):
    with session(tmp_path) as scoped:
        state_path, binding = scoped.state_path, scoped.binding_path
    target = binding if fault == "corrupt_binding" else state_path
    if fault == "missing_state":
        target.unlink()
    else:
        target.write_text("{}", encoding="utf-8")
    with pytest.raises((LiveSafetyError, ValueError)):
        with session(tmp_path):
            pytest.fail("unsafe recovery admitted")
    if fault == "missing_state":
        assert not state_path.exists()


def test_missing_declared_account_identity_is_rejected(tmp_path):
    with pytest.raises(LiveSafetyError, match="LIVE_ACCOUNT_ID"):
        session(tmp_path, account="")
    assert not (tmp_path / "accounts").exists()
