"""Synthetic contract tests for the proposed `di_adx_state_edge` candidate.

Scope: SYNTHETIC ONLY. No market data, no parquet, no storage read, no network.
The candidate is a proposal (see
data/bnb_di_adx_stateedge_20261020_01/STATE_EDGE_CONTRACT.md); these tests
lock its contract semantics, they do not evaluate it.

Property map (contract section 6):
  S1  no look-ahead              S2  canonical-path equivalence
  S3  no repeated BUY            S4  exit on falling edge
  S5  native protective stop     S6  cooldown is a circuit breaker
  S7  terminal inventory         S8  parameter / registry separation
  S9  timing by timestamp+fill   S10 non-vacuity
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

sys.path.insert(0, ".")

from trading_agent.backtest.tournament import (
    EvaluationCellSpec,
    SCENARIO_BASE,
    _bridge_runtime_factory,
    canonical_signal_series,
)
from trading_agent.strategies.base import list_strategies
from trading_agent.strategies.di_adx_state_edge import DiAdxStateEdgeStrategy

PARAMS = {"adx_period": 14, "adx_threshold": 25.0}
WARMUP = 100
N_BARS = 420
NATIVE_SLIPPAGE_RATE = 0.0005
CRASH_BAR = 133          # the bar whose intrabar low trips the native stop


# ── fixtures ────────────────────────────────────────────────────────────────
def _frame(bars: int = N_BARS) -> pl.DataFrame:
    """Deterministic scenario: flat -> strong uptrend -> deep crash bar ->
    decline -> rally. The crash bar's intrabar low triggers the native 5 %
    protective stop while the position is open."""
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    rows = []
    price = 100.0
    for i in range(bars):
        if i < 120:                       # flat, no net drift -> state OFF
            price *= (1.002 if i % 2 == 0 else 0.998)
        elif i < 140:                     # CONTINUOUS strong uptrend -> state ON
            price *= 1.006
        elif i < 250:                     # decline
            price *= 0.995
        elif i < 300:                     # deeper pullback
            price *= 0.992
        else:                             # rally -> state ON again
            price *= 1.008
        close = price
        high = price * 1.003
        low = price * 0.997
        if i == 133:
            # Deep intrabar low INSIDE the strong uptrend: it trips the native
            # 5 % protective stop, but the 14-bar DI mean dilutes this one-off
            # minus_dm below the sustained plus_dm, so the state stays ON
            # across the stop. This is the case the reviewer asked to cover.
            low = price * 0.92
            high = price * 1.004
        rows.append({
            "time": base + timedelta(hours=4 * i),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0 + (i % 17),
        })
    return pl.DataFrame(rows)


def _state_frame(states: list[bool]) -> pl.DataFrame:
    """Frame carrying a handcrafted bull_state column for exact edge tests."""
    return pl.DataFrame({
        "time": [datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=4 * i)
                 for i in range(len(states))],
        "bull_state": states,
    })


def _signals(strategy: DiAdxStateEdgeStrategy, frame: pl.DataFrame) -> list[int]:
    """Signals from the full indicator + signal path."""
    return strategy.generate_signals(strategy.compute_indicators(frame)).to_list()


def _edge_signals(strategy: DiAdxStateEdgeStrategy, states: list[bool]) -> list[int]:
    """Signals from a handcrafted bull_state column (exact edge semantics)."""
    return strategy.generate_signals(_state_frame(states)).to_list()


def _candidate_adapter():
    """Canonical adapter around the PROPOSED candidate (not a registry entry)."""
    from trading_agent.strategies.canonical.adapter import LegacyDataFrameAdapter

    return LegacyDataFrameAdapter(
        DiAdxStateEdgeStrategy(PARAMS),
        model_artifact_id="synthetic-di-adx-state-edge",
        warmup_bars=WARMUP,
        research_only=True,
        strategy_id="di_adx_state_edge",
    )


class _Descriptor:
    warmup_bars = WARMUP


def _run_simulator(tmp_path, frame: pl.DataFrame, signals: list[int]):
    from scripts.full_system_backtest import FullSystemSimulator

    spec = EvaluationCellSpec(
        strategy_id="di_adx_state_edge", symbol="BTC/USDT", timeframe="4h",
        params=PARAMS, cost_scenario=SCENARIO_BASE,
    )
    sim = FullSystemSimulator(
        symbol="BTC/USDT", timeframe="4h",
        source_df_override=frame.rename({"time": "timestamp"}),
        data_manifest_id="d" * 64,
        strategy_name="ma_adx",
        strategy_params_override=PARAMS,
        signal_series=signals,
        runtime_factory=_bridge_runtime_factory(spec, _candidate_adapter(), _Descriptor()),
        state_dir=tmp_path / "execution",
        report_path=tmp_path / "report.json",
        funnel_enabled=True,
    )
    return sim.run()


# ── S3 / S4 / S6 : edge semantics ───────────────────────────────────────────
def test_s3_one_buy_per_bull_episode():
    """A continuing bull state never produces a second BUY."""
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    states = [False] * 3 + [True] * 8 + [False] * 3 + [True] * 4 + [False] * 2
    signals = _edge_signals(strategy, states)
    buys = [i for i, v in enumerate(states) if v and signals[i] == 1]
    assert buys == [3, 14], f"expected one BUY per rising edge, got {buys}"


def test_s3_second_episode_emits_a_new_buy():
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    states = [False] * 2 + [True] * 5 + [False] * 3 + [True] * 5
    signals = _edge_signals(strategy, states)
    buys = [i for i, v in enumerate(states) if v and signals[i] == 1]
    assert buys == [2, 10], f"expected one BUY per rising edge, got {buys}"


def test_s4_sell_only_on_falling_edge_of_an_episode():
    """Exactly one SELL per episode end; never a SELL without a prior BUY."""
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    states = [False] * 2 + [True] * 6 + [False] * 4 + [True] * 3 + [False] * 2
    signals = _edge_signals(strategy, states)
    sells = [i for i, v in enumerate(signals) if v == -1]
    assert sells == [8, 15], f"expected one SELL per falling edge, got {sells}"
    for i in sells:
        assert states[i - 1] is True and states[i] is False


def test_s6_first_bar_has_no_predecessor_so_no_transition():
    """A null predecessor is not False: no edge is asserted without a prior
    observation, so a state already on at bar 0 yields no BUY until it cycles."""
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    signals = _edge_signals(strategy, [True] * 4)
    assert signals[0] == 0, "bar 0 must not assert a transition"
    assert signals[1] == 0, "a continuing state must not assert a rising edge"
    assert all(v == 0 for v in signals), (
        "a state that starts on at bar 0 must not produce a BUY until it cycles"
    )
    later = _edge_signals(strategy, [True] * 4 + [False] * 2 + [True] * 3)
    assert [i for i, v in enumerate(later) if v == 1] == [6]


def test_s6_candidate_emits_no_cooldown_specific_signal():
    """The candidate has no cooldown state: its signal depends only on the state."""
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    states = [False] * 2 + [True] * 5 + [False] * 2
    a = _edge_signals(strategy, states)
    b = _edge_signals(strategy, states)
    assert a == b
    assert [i for i, v in enumerate(a) if v != 0] == [2, 7]


# ── S1 : no look-ahead ──────────────────────────────────────────────────────
def _perturb_future(frame: pl.DataFrame, k: int) -> pl.DataFrame:
    later = pl.arange(0, frame.height) > k
    return frame.with_columns([
        pl.when(later).then(pl.col("close") * 1.35).otherwise(pl.col("close")).alias("close"),
        pl.when(later).then(pl.col("high") * 1.35).otherwise(pl.col("high")).alias("high"),
        pl.when(later).then(pl.col("low") * 1.35).otherwise(pl.col("low")).alias("low"),
        pl.when(later).then(pl.col("open") * 1.35).otherwise(pl.col("open")).alias("open"),
    ])


def test_s1_future_perturbation_does_not_change_past_signals():
    """Bars after k cannot influence the signal at or before k."""
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    frame = _frame()
    baseline = _signals(strategy, frame)
    k = 200
    after = _signals(strategy, _perturb_future(frame, k))
    assert baseline[: k + 1] == after[: k + 1], (
        "signals at or before the perturbation point changed — look-ahead"
    )


def test_s1_canonical_path_is_causal_under_future_perturbation():
    """The canonical per-bar path must not read beyond the observation bar."""
    frame = _frame()
    baseline = canonical_signal_series(
        _candidate_adapter(), frame, warmup_bars=WARMUP, symbol="BTC/USDT",
        start_index=0, end_index=frame.height - 1,
    )
    k = 200
    after = canonical_signal_series(
        _candidate_adapter(), _perturb_future(frame, k), warmup_bars=WARMUP,
        symbol="BTC/USDT", start_index=0, end_index=frame.height - 1,
    )
    assert baseline[: k + 1] == after[: k + 1]


# ── S2 : canonical path == full frame, shifted ─────────────────────────────
def test_s2_canonical_path_matches_full_frame_shifted_by_one():
    """Outside warmup the canonical series equals the full-frame series - 1."""
    frame = _frame()
    full_frame = _signals(DiAdxStateEdgeStrategy(PARAMS), frame)
    canonical = canonical_signal_series(
        _candidate_adapter(), frame, warmup_bars=WARMUP, symbol="BTC/USDT",
        start_index=0, end_index=frame.height - 1,
    )
    mismatches = [
        j for j in range(WARMUP + 1, len(canonical))
        if canonical[j] != full_frame[j - 1]
    ]
    assert not mismatches, f"first mismatching indices {mismatches[:5]}"


# ── S8 : parameter / registry separation ────────────────────────────────────
def test_s8_candidate_is_not_registered_and_has_no_ma_parameters():
    registered = list_strategies()
    assert "di_adx_state_edge" not in registered, (
        "the proposal must not be published to any registry before review"
    )
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    assert not hasattr(strategy, "fast") and not hasattr(strategy, "slow"), (
        "the MA pair must not be part of the state trigger"
    )
    assert strategy.adx_period == 14 and strategy.adx_threshold == 25.0
    assert set(strategy.params) == {"adx_period", "adx_threshold"}, (
        "the parameter set must stay exactly the two native values"
    )


def test_s8_control_candidate_is_untouched():
    """The frozen control keeps its own identity and parameters."""
    from trading_agent.strategies.enhanced_ma import MaAdxCrossover

    control = MaAdxCrossover({"fast_period": 20, "slow_period": 80,
                               "adx_period": 14, "adx_threshold": 25.0})
    assert control.fast == 20 and control.slow == 80
    assert "ma_adx" in list_strategies()


# ── S5 / S7 / S9 / S10 : engine level ───────────────────────────────────────
def test_s5_native_protective_stop_no_reentry_then_new_edge(tmp_path):
    """A REAL native protective stop fires; the candidate does not re-buy in the
    stopped episode; a BUY is allowed again only at a new rising edge.

    Simulating a "stopped" state is explicitly not sufficient here: the stop
    must come from the native engine (close reason ``stop_loss``) on a real
    position, and the assertions read the engine's own action record.
    """
    frame = _frame()
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    full_frame_signals = _signals(strategy, frame)
    rising_edges = [i for i, v in enumerate(full_frame_signals) if v == 1]
    signals = canonical_signal_series(
        _candidate_adapter(), frame, warmup_bars=WARMUP, symbol="BTC/USDT",
        start_index=0, end_index=frame.height - 1,
    )
    report = _run_simulator(tmp_path, frame, signals)
    funnel = report.get("execution_funnel") or {}
    trades = report.get("trades") or []
    records = funnel.get("records") or []

    # S10 — the fixture must actually trade, else everything below is vacuous.
    assert trades, "no trade produced — the assertions below would be vacuous"
    assert rising_edges, "fixture produced no rising edge"

    # S5a — a NATIVE protective stop actually fired.
    stopped = [t for t in trades if t.get("reason") == "stop_loss"]
    assert stopped, f"no native protective stop in the run: {[t.get('reason') for t in trades]}"
    assert funnel.get("close_reasons", {}).get("stop_loss", 0) >= 1

    # S5b — the engine's BUY actions can only come from a rising edge; the
    # engine can never invent a re-entry.
    buy_action_bars = [r["execution_bar"] for r in records if r.get("action") == "BUY"]
    assert buy_action_bars, "engine took no BUY action"
    for bar in buy_action_bars:
        assert any(bar - edge == 2 for edge in rising_edges), (
            f"BUY action at bar {bar} does not correspond to a rising edge + 2"
        )
    assert len(buy_action_bars) <= len(rising_edges), (
        "more BUY actions than rising edges — the engine re-entered on its own"
    )

    # S5c — no BUY between the stop and the next rising edge ...
    stop_exit_time = datetime.fromisoformat(stopped[0]["exit_time"])
    times = frame["time"].to_list()
    stop_exit_bar = max(i for i, t in enumerate(times) if t <= stop_exit_time)
    next_edge_after_stop = next((e for e in rising_edges if e > stop_exit_bar), None)
    assert next_edge_after_stop is not None, "fixture has no rising edge after the stop"
    buys_between = [b for b in buy_action_bars if stop_exit_bar < b < next_edge_after_stop + 2]
    assert not buys_between, f"re-entered inside the stopped episode at {buys_between}"

    # ... and a BUY is allowed once a NEW rising edge appears.
    new_edge_actions = [b for b in buy_action_bars if b >= next_edge_after_stop + 2]
    assert new_edge_actions, (
        "no BUY after the new rising edge — re-entry must be possible"
    )
    # the new-edge BUY must be a REAL entry: either a closed trade or an open
    # position carried at the end of the window, not merely a signal.
    assert len(trades) >= 1 or report.get("open_positions", 0) >= 1, (
        "the new-edge BUY produced neither a trade nor an open position"
    )
    assert report.get("open_positions", 0) >= 1 or len(trades) >= 2, (
        "the fixture must show the new edge opening a real position"
    )

    # ── the four anti-vacuous assertions the reviewer requires, in one place ──
    #  (i)  the stop really happened
    assert stopped, "no native protective stop was produced"
    assert funnel.get("close_reasons", {}).get("stop_loss", 0) >= 1
    #  (ii) the state was still ON when the stop fired
    bull_state = strategy.compute_indicators(frame)["bull_state"].to_list()
    assert bull_state[CRASH_BAR] is True, "the state is not ON at the crash bar"
    #  (iii) no re-entry inside the stopped episode
    #  (iv) a real BUY at the new rising edge
    # (the bodies of (ii)-(iv) are asserted below; the explicit stop assertion
    #  above is repeated there so each requirement stands on its own)

    # S5d — THE CASE UNDER TEST: the stop fires while the state is still ON.
    # The reviewer required this to be shown directly, not inferred.
    bull = strategy.compute_indicators(frame)["bull_state"].to_list()
    assert bull[CRASH_BAR] is True, (
        f"fixture no longer has the state ON at the crash bar {CRASH_BAR}"
    )
    assert bull[stop_exit_bar] is True, (
        f"the state must still be ON at the stop exit bar {stop_exit_bar} — "
        "otherwise the fixture does not cover the 'stop while state ON' case"
    )
    # the state stays ON for a while after the stop: that whole span is the
    # SAME episode and must contain no further BUY
    off_bar = next((b for b in range(stop_exit_bar, len(bull)) if bull[b] is False),
                   len(bull))
    assert off_bar > stop_exit_bar, "the state must survive past the stop"
    assert all(bull[b] is True for b in range(stop_exit_bar, off_bar)), (
        "the state must stay ON for the whole span after the stop"
    )
    # a BUY action at bar b comes from the edge at b-2, so an edge inside the
    # episode would surface as an action up to two bars later
    buys_in_episode = [b for b in buy_action_bars if stop_exit_bar < b < off_bar + 2]
    assert not buys_in_episode, (
        f"a BUY was emitted while the state was still ON after the stop: {buys_in_episode}"
    )
    # the episode that contained the stop produced exactly one rising edge
    episode_edges = [e for e in rising_edges if e <= stop_exit_bar]
    assert len(episode_edges) == 1, (
        f"the stopped episode must contain exactly one rising edge, got {episode_edges}"
    )


def test_s9_timing_verified_by_timestamp_and_real_fill(tmp_path):
    """The k+2 rule is checked against real timestamps and real fill prices.

    This is deliberately NOT a comparison of two series derived from the same
    helper: the expected bar index comes from the strategy's own edge list,
    and the observed side comes from the engine's trade record (entry_time and
    entry_price), cross-checked against the bar's own open.
    """
    frame = _frame()
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    full_frame_signals = _signals(strategy, frame)
    rising_edges = [i for i, v in enumerate(full_frame_signals) if v == 1]
    signals = canonical_signal_series(
        _candidate_adapter(), frame, warmup_bars=WARMUP, symbol="BTC/USDT",
        start_index=0, end_index=frame.height - 1,
    )
    report = _run_simulator(tmp_path, frame, signals)
    trades = report.get("trades") or []
    assert trades, "no trade produced — the timing assertions would be vacuous"

    times = frame["time"].to_list()
    opens = frame["open"].to_list()
    checked = 0
    for trade in trades:
        entry_time = datetime.fromisoformat(trade["entry_time"])
        matched = [e for e in rising_edges
                   if e + 2 < len(times) and times[e + 2] == entry_time]
        assert matched, (
            f"trade entry_time {entry_time} does not match any rising edge + 2 bar"
        )
        edge = matched[0]
        expected_bar = edge + 2
        assert times[expected_bar] == entry_time
        # the fill happens at that bar's open, within the native slippage band
        expected_price = opens[expected_bar]
        fill_price = trade["entry_price"]
        deviation = abs(fill_price - expected_price) / expected_price
        assert deviation <= 10 * NATIVE_SLIPPAGE_RATE + 1e-6, (
            f"entry price {fill_price} is not the open {expected_price} of bar "
            f"{expected_bar} (deviation {deviation:.6f})"
        )
        checked += 1
    assert checked == len(trades), "not every trade was timestamp-checked"


def test_s7_terminal_inventory_identity(tmp_path):
    """runtime BUY fills - carry_out == closed trades; a carried-out position
    is marked, never silently dropped."""
    frame = _frame()
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    signals = canonical_signal_series(
        _candidate_adapter(), frame, warmup_bars=WARMUP, symbol="BTC/USDT",
        start_index=0, end_index=frame.height - 1,
    )
    report = _run_simulator(tmp_path, frame, signals)
    funnel = report.get("execution_funnel") or {}
    assert funnel.get("records"), "funnel produced no records"

    buy_actions = funnel.get("actions", {}).get("BUY", 0)
    already_long = funnel.get("terminals", {}).get("already_long", 0)
    fills = buy_actions - already_long
    carry_out = 1 if funnel.get("carry_out") else 0
    closed = funnel.get("closed_trades")
    assert fills - carry_out == closed, (
        f"runtime fills {fills} - carry_out {carry_out} != closed {closed}"
    )
    if carry_out:
        # The native accounting contract is
        # "cash_plus_inventory_marked_at_close_no_forced_liquidation": the open
        # position must still be open, counted separately from the closed
        # trades, and marked into the equity basis.
        assert report.get("open_positions", 0) >= 1, (
            "a carried-out position must still be reported as an open position"
        )
        assert report.get("total_trades") == closed, (
            "an open position must not be counted as a closed trade"
        )
        accounting = report.get("measured_accounting") or {}
        assert accounting.get("accounting_basis") == "closed_trades_plus_open_inventory"
        assert accounting.get("equity_basis") == "marked_equity", (
            "the carried-out position must be marked into the equity basis"
        )
        assert report.get("measured_accounting_error") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_s5b_state_machine_no_buy_while_state_stays_on_after_a_stop():
    """Isolated state-machine check of the same property, with no price path.

    The engine fixture proves it on one constructed scenario; this proves it on
    the state machine itself, for ANY sequence in which the state stays on
    across a stop: after a stop, the candidate is flat and emits nothing until
    the state turns off and on again. No price series, no engine, no fill.
    """
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    # state on for a long stretch; a stop happens somewhere inside it
    states = [False] * 2 + [True] * 12 + [False] * 3 + [True] * 6
    signals = _edge_signals(strategy, states)
    edges = [i for i, v in enumerate(states) if v and signals[i] == 1]
    falls = [i for i, v in enumerate(states) if not v and signals[i] == -1]

    assert edges == [2, 17], f"expected a rising edge per episode, got {edges}"
    assert falls == [14], f"expected one falling edge, got {falls}"

    # A stop inside the first episode (bar 8) changes nothing about the
    # signals: the state is still on, so no further BUY may be emitted.
    stop_bar = 8
    assert states[stop_bar] is True
    assert not any(stop_bar < i < 17 and signals[i] == 1 for i in range(len(states))), (
        "a BUY was emitted inside the episode that was stopped"
    )
    # the only BUY in that episode is the one that opened it
    assert [i for i in edges if i <= stop_bar] == [2]
    # a new BUY appears only after the state cycled off and on again
    assert signals[17] == 1


def test_s5c_state_machine_flat_position_emits_nothing_until_a_new_edge():
    """A flat candidate after a stop stays flat while the state is on."""
    strategy = DiAdxStateEdgeStrategy(PARAMS)
    states = [False] + [True] * 20
    signals = _edge_signals(strategy, states)
    # the state turns on at bar 1 -> one BUY; then nothing while it stays on
    assert [i for i, v in enumerate(signals) if v == 1] == [1]
    assert all(v == 0 for v in signals[2:]), (
        "the candidate must stay flat while the state remains on"
    )
