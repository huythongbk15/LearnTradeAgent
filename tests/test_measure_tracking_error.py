"""Tests for tracking-error measurement (P2 / P3 gate)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from scripts.measure_tracking_error import load_fills, summarize


def _fill_event(symbol: str, side: str, fill: float, signal: float) -> dict:
    return {
        "timestamp": datetime(2026, 8, 12, 10, 0, 0, tzinfo=UTC).isoformat(),
        "event": "order_filled",
        "details": {
            "symbol": symbol,
            "side": side,
            "filled_qty": 0.01,
            "average_fill_price": fill,
            "signal_price": signal,
        },
    }


def _write(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "audit.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    return path


def test_load_fills_extracts_slippage(tmp_path):
    path = _write(
        tmp_path,
        [
            _fill_event("BTC/USDT", "BUY", 100.10, 100.00),  # +10 bps
            _fill_event(
                "BTC/USDT", "SELL", 99.90, 100.00
            ),  # sell below signal = +10 bps
            {
                "timestamp": "2026-08-12T10:00:00+00:00",
                "event": "run_completed",
                "details": {},
            },
        ],
    )
    fills = load_fills(path)
    assert len(fills) == 2
    assert fills[0]["slippage_bps"] == pytest.approx(10.0)
    assert fills[1]["slippage_bps"] == pytest.approx(10.0)  # SELL sign normalization


def test_skips_fills_without_reference(tmp_path):
    path = _write(
        tmp_path,
        [
            {
                "timestamp": "2026-08-12T10:00:00+00:00",
                "event": "order_filled",
                "details": {
                    "symbol": "BTC/USDT",
                    "side": "BUY",
                    "average_fill_price": 100.0,
                },
            }
        ],
    )
    assert load_fills(path) == []


def test_summarize_mean_and_per_symbol(tmp_path):
    path = _write(
        tmp_path,
        [
            _fill_event("BTC/USDT", "BUY", 100.00, 100.00),  # 0 bps
            _fill_event("BTC/USDT", "BUY", 100.05, 100.00),  # 5 bps
            _fill_event("SOL/USDT", "BUY", 20.02, 20.00),  # 10 bps
        ],
    )
    report = summarize(load_fills(path))
    assert report["fills"] == 3
    assert report["mean_slippage_bps"] == pytest.approx(5.0)
    assert report["per_symbol"]["SOL/USDT"]["mean_slippage_bps"] == pytest.approx(10.0)
    assert report["per_symbol"]["BTC/USDT"]["fills"] == 2


def test_summarize_empty():
    report = summarize([])
    assert report["fills"] == 0
    assert report["mean_slippage_bps"] is None
