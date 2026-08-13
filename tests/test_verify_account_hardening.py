"""Tests for account-hardening verifier (P1.6)."""

from __future__ import annotations

import scripts.verify_account_hardening as module
from scripts.verify_account_hardening import check_env, check_manual


def test_check_env_accepts_testnet_with_kill_switch():
    assert check_env({"TRADING_MODE": "testnet", "TRADING_KILL_SWITCH": "true"}) == []


def test_check_env_rejects_mainnet():
    failures = check_env({"TRADING_MODE": "mainnet", "TRADING_KILL_SWITCH": "true"})
    assert any("mainnet" in failure for failure in failures)


def test_check_env_rejects_unset_kill_switch():
    failures = check_env({"TRADING_MODE": "testnet", "TRADING_KILL_SWITCH": ""})
    assert any("TRADING_KILL_SWITCH" in failure for failure in failures)


def test_check_manual_all_confirmed():
    env = {key: "true" for key in module.MANUAL_CONFIRMATIONS}
    assert check_manual(env) == []


def test_check_manual_lists_pending():
    pending = check_manual({})
    assert len(pending) == len(module.MANUAL_CONFIRMATIONS)
    assert any("BINANCE_SUBACCOUNT_CONFIRMED" in item for item in pending)


def test_check_manual_partial():
    pending = check_manual({"BINANCE_SUBACCOUNT_CONFIRMED": "yes"})
    assert len(pending) == len(module.MANUAL_CONFIRMATIONS) - 1
