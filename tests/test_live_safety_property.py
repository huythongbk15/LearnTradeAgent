"""Property-based tests for live execution safety invariants
(REPO_TRUTH Phase C: live execution safety invariants + property tests).

Invariants:
1. from_env either yields a VALID LiveRiskLimits or raises LiveSafetyError
   (fail-closed) — never silently accepts garbage config.
2. mainnet-canary profile is always strictly more conservative than the
   configured limits (≤ on caps, ≥ on reserves).
3. effective_max_order_notional is bounded by both the USD cap and the
   equity-% cap for any positive equity.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from trading_agent.execution.live_safety import (
    LiveRiskLimits,
    LiveSafetyError,
)

LIMIT_NAMES = (
    "LIVE_MAX_ORDER_USD",
    "LIVE_MAX_ORDER_EQUITY_PCT",
    "LIVE_MAX_SYMBOL_PCT",
    "LIVE_MAX_GROSS_EXPOSURE_PCT",
    "LIVE_MIN_CASH_RESERVE_PCT",
    "LIVE_MAX_DAILY_LOSS_PCT",
    "LIVE_MAX_DRAWDOWN_PCT",
    "LIVE_MAX_QUOTE_AGE_SECONDS",
    "LIVE_MAX_PRICE_DEVIATION_PCT",
    "LIVE_MAX_SPREAD_PCT",
    "LIVE_MAX_BOOK_SLIPPAGE_PCT",
    "LIVE_MIN_BOOK_DEPTH_MULTIPLE",
    "LIVE_MAX_DUST_USD",
)

# Mix of plausible, extreme and invalid values to prove fail-closed behavior.
_ENV_VALUES = st.one_of(
    st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.floats(min_value=-1e6, max_value=-1e-6, allow_nan=False, allow_infinity=False),
    st.just("0.5"),
    st.just("abc"),
    st.just(""),
    st.just("nan"),
    st.just("inf"),
)


@given(env=st.fixed_dictionaries({name: _ENV_VALUES for name in LIMIT_NAMES}))
@settings(max_examples=150, deadline=None)
def test_from_env_fail_closed_or_valid(env: dict) -> None:
    try:
        limits = LiveRiskLimits.from_env(env)
    except LiveSafetyError:
        return  # fail-closed: garbage config is rejected, never half-applied
    # If it parsed, it must be internally valid.
    limits.validate()


@given(env=st.fixed_dictionaries({name: _ENV_VALUES for name in LIMIT_NAMES}))
@settings(max_examples=150, deadline=None)
def test_canary_is_never_looser_than_configured(env: dict) -> None:
    try:
        configured = LiveRiskLimits.from_env(env)
    except LiveSafetyError:
        return
    canary = LiveRiskLimits.for_profile("mainnet-canary", env)
    assert canary.max_order_notional_usd <= configured.max_order_notional_usd
    assert canary.max_order_equity_pct <= configured.max_order_equity_pct
    assert canary.max_symbol_exposure_pct <= configured.max_symbol_exposure_pct
    assert canary.max_gross_exposure_pct <= configured.max_gross_exposure_pct
    assert canary.min_cash_reserve_pct >= configured.min_cash_reserve_pct
    assert canary.max_daily_loss_pct <= configured.max_daily_loss_pct
    assert canary.max_drawdown_pct <= configured.max_drawdown_pct


@given(
    equity=st.floats(
        min_value=1e-3, max_value=1e9, allow_nan=False, allow_infinity=False
    ),
    cap=st.floats(min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False),
    equity_pct=st.floats(
        min_value=1e-6, max_value=1.0, allow_nan=False, allow_infinity=False
    ),
)
@settings(max_examples=150, deadline=None)
def test_effective_notional_bounded_by_both_caps(
    equity: float, cap: float, equity_pct: float
) -> None:
    limits = LiveRiskLimits(
        max_order_notional_usd=cap,
        max_order_equity_pct=equity_pct,
    )
    effective = limits.effective_max_order_notional(equity)
    assert effective <= cap
    assert effective <= equity * equity_pct
    assert effective > 0


@given(
    equity=st.floats(
        min_value=-1e6, max_value=0.0, allow_nan=False, allow_infinity=False
    )
)
@settings(max_examples=50, deadline=None)
def test_effective_notional_rejects_non_positive_equity(equity: float) -> None:
    limits = LiveRiskLimits()
    try:
        limits.effective_max_order_notional(equity)
    except LiveSafetyError:
        return
    raise AssertionError("non-positive equity must raise LiveSafetyError")
