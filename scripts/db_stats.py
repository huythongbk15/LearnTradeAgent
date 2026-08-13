"""Print database statistics — trades, decisions, equity curve."""

from trading_agent.monitoring.database import (
    init_db,
    get_trade_stats,
    get_agent_decisions,
    get_equity_curve,
)


def main():
    init_db()

    stats = get_trade_stats()
    print(
        f"Trades: {stats['total_trades']} | Wins: {stats['wins']} | Losses: {stats['losses']}"
    )
    print(f"Win Rate: {stats['win_rate']:.1%} | Total P&L: ${stats['total_pnl']:+.2f}")

    eq = get_equity_curve(limit=2)
    _all_eq = get_equity_curve(limit=99999)
    print(f"Equity snapshots: {len(_all_eq)}")

    decisions = get_agent_decisions(limit=99999)
    print(f"Agent decisions logged: {len(decisions)}")


if __name__ == "__main__":
    main()
