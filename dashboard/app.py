"""
📊 Trading Agent System — Streamlit Dashboard

Live monitoring: portfolio, trades, agent decisions, risk metrics.

Run with:
    poetry run streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import polars as pl

from trading_agent.monitoring.database import (
    DEFAULT_DB_PATH,
    get_trade_stats,
    get_trades,
    get_equity_curve,
    get_agent_decisions,
)
from trading_agent.monitoring.metrics import compute_static_metrics, rolling_metrics
from trading_agent.log_config import setup_logging
from trading_agent.execution.engine import ExecutionEngine
from trading_agent.config.loader import config as app_config

setup_logging(level="WARNING")  # reduce noise

# ── Page Config ──────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Trading Agent Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📊 Trading Agent System — Dashboard")
st.caption("Real-time monitoring for multi-agent crypto trading system")


# ── Sidebar ──────────────────────────────────────────────────────────────

st.sidebar.header("⚙️ Controls")
refresh_interval = st.sidebar.selectbox(
    "Auto-refresh (s)", [0, 5, 10, 30, 60], index=0, format_func=lambda x: f"{x}s" if x > 0 else "Manual"
)

if refresh_interval > 0:
    st.sidebar.info(f"Auto-refresh every {refresh_interval}s")
    from streamlit_autorefresh import st_autorefresh
    st_autorefresh(interval=refresh_interval * 1000, key="data_refresh")
else:
    if st.sidebar.button("🔄 Refresh Now"):
        st.cache_data.clear()
        st.rerun()


# ── Load Data ────────────────────────────────────────────────────────────

@st.cache_data(ttl=5, show_spinner=False)
def load_live_summary():
    """Load current execution summary."""
    try:
        engine = ExecutionEngine()
        summary = engine.get_summary()
        positions = engine.get_positions_summary()
        return summary, positions
    except Exception as e:
        return {"error": str(e)}, []


@st.cache_data(ttl=5, show_spinner=False)
def load_db_data():
    """Load cached DB data."""
    stats = get_trade_stats()
    trades = get_trades(limit=100)
    eq = get_equity_curve(limit=5000)
    decisions = get_agent_decisions(limit=50)
    metrics = compute_static_metrics()
    rolling = rolling_metrics(window=30)
    return stats, trades, eq, decisions, metrics, rolling


summary, positions = load_live_summary()
stats, trades, eq_data, decisions, metrics, rolling = load_db_data()


# ── Tabs ─────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4 = st.tabs(
    ["📈 Overview", "📋 Trades", "🤖 Agents", "⚠️ Risk"]
)


# ══════════════════════════════════════════════════════════════════════════
# TAB 1: Overview
# ══════════════════════════════════════════════════════════════════════════

with tab1:
    col1, col2, col3, col4 = st.columns(4)

    if "error" not in summary:
        equity = summary.get("equity", 0)
        initial = app_config.initial_capital
        return_pct = ((equity / initial) - 1) * 100 if initial > 0 else 0

        col1.metric("💰 Equity", f"${equity:,.2f}",
                     f"{return_pct:+.2f}%")
        col2.metric("💵 Cash", f"${summary.get('cash', 0):,.2f}")
        col3.metric("📈 Positions",
                     f"{summary.get('open_positions', 0)} open",
                     f"${summary.get('positions_value', 0):,.0f}")
        col4.metric("🔄 Trades",
                     f"{summary.get('total_trades', 0)} total",
                     f"{summary.get('open_orders', 0)} pending")
    else:
        col1.error(f"⚠️ {summary.get('error', 'Unknown error')}")

    # Metrics row
    col1, col2, col3, col4, col5 = st.columns(5)
    sm = metrics
    col1.metric("🏆 Total P&L", f"${sm.get('total_pnl', 0):,.2f}")
    col2.metric("📊 Win Rate", f"{sm.get('win_rate', 0):.1%}")
    col3.metric("⚡ Sharpe", f"{sm.get('sharpe_ratio', 0):.2f}")
    col4.metric("📉 Max DD", f"{sm.get('max_drawdown_pct', 0):.2f}%")
    col5.metric("🎯 Profit Factor", f"{sm.get('profit_factor', 0):.2f}")

    # Rolling metrics
    st.subheader("📊 Rolling Metrics (last 30 trades)")
    rcols = st.columns(4)
    rcols[0].metric("Rolling Return", f"{rolling.get('rolling_return_pct', 0):.2f}%")
    rcols[1].metric("Rolling Win Rate", f"{rolling.get('rolling_win_rate', 0):.1%}")
    rcols[2].metric("Rolling Sharpe", f"{rolling.get('rolling_sharpe', 0):.2f}")
    rcols[3].metric("Trades in Window", f"{rolling.get('trades_in_window', 0)}")

    # Equity curve chart
    st.subheader("📈 Equity Curve")
    if eq_data:
        eq_df = pl.DataFrame(
            [{"timestamp": e["timestamp"], "equity": e["equity"]}
             for e in reversed(eq_data)]
        )
        st.line_chart(eq_df, x="timestamp", y="equity", height=300)
    else:
        st.info("No equity data yet. Start paper trading to see the curve.")

    # Open positions
    st.subheader("📌 Open Positions")
    if positions:
        pos_df = pl.DataFrame(positions)
        st.dataframe(pos_df.to_pandas(), use_container_width=True)
    else:
        st.info("No open positions.")


# ══════════════════════════════════════════════════════════════════════════
# TAB 2: Trades
# ══════════════════════════════════════════════════════════════════════════

with tab2:
    st.subheader(f"📋 Recent Trades ({stats.get('total_trades', 0)} total)")

    # Summary cards
    tcols = st.columns(6)
    tcols[0].metric("Total", stats.get("total_trades", 0))
    tcols[1].metric("✅ Wins", stats.get("wins", 0))
    tcols[2].metric("❌ Losses", stats.get("losses", 0))
    tcols[3].metric("Win Rate", f"{stats.get('win_rate', 0):.1%}")
    tcols[4].metric("Avg Win", f"${stats.get('avg_win', 0):,.2f}")
    tcols[5].metric("Avg Loss", f"${stats.get('avg_loss', 0):,.2f}")

    # Trade table
    if trades:
        trade_rows = []
        for t in trades:
            pnl = t.get("pnl")
            pnl_str = f"${pnl:+.2f}" if pnl else "—"
            pnl_color = "🟢" if (pnl or 0) > 0 else "🔴" if (pnl or 0) < 0 else "⚪"
            pnl_pct = t.get("pnl_pct")
            pct_str = f"{pnl_pct:+.2%}" if pnl_pct else "—"
            trade_rows.append({
                "ID": t["trade_id"][:16],
                "Symbol": t["symbol"],
                "Side": t["side"],
                "Entry $": f"${t.get('entry_price', 0):,.0f}" if t.get("entry_price") else "—",
                "Exit $": f"${t.get('exit_price', 0):,.0f}" if t.get("exit_price") else "—",
                f"{pnl_color} P&L": pnl_str,
                "P&L %": pct_str,
                "Reason": t.get("reason", "") or "",
                "Exit Time": str(t.get("exit_time", ""))[:16] if t.get("exit_time") else "",
            })

        tdf = pl.DataFrame(trade_rows)
        st.dataframe(tdf.to_pandas(), use_container_width=True, hide_index=True)
    else:
        st.info("No trades recorded yet. Run `trading-agent execution run BTC/USDT` to start.")

    # Win/Loss distribution
    if trades:
        st.subheader("📊 P&L Distribution")
        pnls = [t.get("pnl", 0) or 0 for t in trades if t.get("pnl") is not None]
        if pnls:
            pnl_df = pl.DataFrame({"pnl": pnls})
            st.bar_chart(pnl_df, y="pnl", height=250)


# ══════════════════════════════════════════════════════════════════════════
# TAB 3: Agent Decisions
# ══════════════════════════════════════════════════════════════════════════

with tab3:
    st.subheader("🤖 Recent Agent Decisions")

    if decisions:
        dec_df = pl.DataFrame(
            [
                {
                    "Time": d["timestamp"][11:19],
                    "Agent": d["agent_name"],
                    "Symbol": d["symbol"],
                    "Signal": d["signal"],
                    "Confidence": f"{d.get('confidence', 0)*100:.0f}%",
                    "Price": f"${d.get('price', 0):,.0f}" if d.get("price") else "—",
                    "Reasoning": (d.get("reasoning") or "")[:120] + "…" if d.get("reasoning") and len(d["reasoning"]) > 120 else (d.get("reasoning") or ""),
                }
                for d in decisions
            ]
        )
        st.dataframe(dec_df.to_pandas(), use_container_width=True, hide_index=True)

        # Agent agreement chart
        st.subheader("📊 Agent Signal Distribution")
        signals = pl.DataFrame(
            [{"agent": d["agent_name"], "signal": d["signal"]} for d in decisions]
        )
        if not signals.is_empty():
            counts = signals.group_by(["agent", "signal"]).len().sort("agent")
            st.bar_chart(
                counts.to_pandas().pivot(
                    index="agent", columns="signal", values="len"
                ).fillna(0),
                height=250,
            )
    else:
        st.info("No agent decisions yet. Run `trading-agent agents analyze BTC/USDT` to see decisions here.")


# ══════════════════════════════════════════════════════════════════════════
# TAB 4: Risk
# ══════════════════════════════════════════════════════════════════════════

with tab4:
    st.subheader("🛡️ Risk Controller Status")

    try:
        from trading_agent.execution.risk_controller import RiskController

        engine = ExecutionEngine()
        rc = RiskController(engine)
        status = rc.get_status()

        risk_cols = st.columns(4)
        cb = "⛔ Active" if status.get("circuit_breaker_active") else "✅ OK"
        dd_current = status.get("drawdown_pct", 0)
        dd_limit = status.get("max_drawdown_limit_pct", 15)
        dl_current = status.get("daily_loss_pct", 0)
        dl_limit = status.get("daily_loss_limit_pct", 8)
        cooldown = "⏳ Yes" if status.get("cooldown_active") else "✅ No"
        risk_items = [
            ("🔒 Circuit Breaker", cb, "✅" if not status.get("circuit_breaker_active") else "❌"),
            ("📉 Drawdown", f"{dd_current:.2f}% / {dd_limit:.0f}%",
             "✅" if dd_current < dd_limit else "❌"),
            ("⚠️ Daily Loss", f"{dl_current:.2f}% / {dl_limit:.0f}%",
             "✅" if dl_current < dl_limit else "❌"),
            ("⏳ Cooldown", cooldown, "✅" if not status.get("cooldown_active") else "⏳"),
        ]
        for i, (label, value, icon) in enumerate(risk_items):
            risk_cols[i].metric(label, value, icon)

        # Drawdown chart
        st.subheader("📉 Drawdown History")
        if eq_data:
            eq_values = [e["equity"] for e in reversed(eq_data)]
            peak = eq_values[0] if eq_values else 0
            dd_values = []
            for v in eq_values:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak * 100 if peak > 0 else 0
                dd_values.append(dd)
            dd_df = pl.DataFrame({
                "timestamp": [e["timestamp"] for e in reversed(eq_data)],
                "drawdown_pct": dd_values,
            })
            st.area_chart(dd_df, x="timestamp", y="drawdown_pct", height=200)
        else:
            st.info("No equity data for drawdown chart.")

    except Exception as e:
        st.error(f"Could not load risk controller: {e}")

    # Risk rules reference
    with st.expander("📖 Risk Rules Reference"):
        st.markdown("""
        | Check | Limit | Action |
        |-------|-------|--------|
        | Max Drawdown | 15% | Circuit breaker → close all |
        | Daily Loss | 8% | Circuit breaker → close all |
        | Position Concentration | 50% of portfolio | Warning |
        | Cooldown | 24h | No new trades after stop-loss |
        """)

    # Quick actions
    st.subheader("⚡ Quick Actions")
    acols = st.columns(3)
    if acols[0].button("🔄 Refresh Now"):
        st.cache_data.clear()
        st.rerun()
    if acols[1].button("📋 View Execution Status"):
        st.code(json.dumps(summary, indent=2, default=str), language="json")
    if acols[2].button("🛑 Close All Positions"):
        st.warning("⚠️ This would close all positions. Confirm via CLI: `trading-agent execution close --all`")


# ── Footer ───────────────────────────────────────────────────────────────

st.divider()
st.caption(
    "Trading Agent System v0.3.0 · "
    "Dashboard tự động refresh mỗi khi tab được focus · "
    f"Dữ liệu: {stats.get('total_trades', 0)} trades, {len(eq_data)} equity snapshots, {len(decisions)} agent decisions"
)
