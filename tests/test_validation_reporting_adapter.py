"""Compatibility at the launcher/reporting boundary, without market data."""

import pytest

from scripts import run_wfo_parallel as launcher
from trading_agent.backtest import validation_reporting


def test_adapter_resolves_collaborators_at_call_time(monkeypatch):
    args = tuple(object() for _ in range(5))
    expected = {"status": "INCOMPLETE", "live_authorized": False}
    callbacks = [object(), object(), object()]
    monkeypatch.setattr(launcher, "_verify_validation_protocol", callbacks[0])
    monkeypatch.setattr(launcher, "_verify_native_report", callbacks[1])
    monkeypatch.setattr(launcher, "audit_parallel_trial_history", callbacks[2])
    calls = []

    def verify(*received, **collaborators):
        calls.append((received, collaborators))
        return expected

    monkeypatch.setattr(validation_reporting, "verify_validation_only_coverage", verify)
    assert launcher.verify_validation_only_coverage(*args) is expected
    assert calls == [(args, {
        "verify_protocol": callbacks[0],
        "verify_native_report": callbacks[1],
        "audit_trial_history": callbacks[2],
    })]


def test_adapter_does_not_reclassify_integrity_failure(monkeypatch):
    error = ValueError("completed report integrity failure")

    def reject(*args, **kwargs):
        raise error

    monkeypatch.setattr(validation_reporting, "verify_validation_only_coverage", reject)
    with pytest.raises(ValueError) as caught:
        launcher.verify_validation_only_coverage(*(object() for _ in range(5)))
    assert caught.value is error
