#!/usr/bin/env python3
"""Extract and print latest backtest metrics from trading_agent.db"""

import sqlite3
import sys


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/trading.db"
    days_back = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute(f"""
            SELECT strategy, MAX(total_return_pct) as best_return, MAX(sharpe) as best_sharpe
            FROM backtest_results
            WHERE created_at > datetime('now', '-{days_back} days')
            GROUP BY strategy
        """)
        for row in cur.fetchall():
            print(f"{row[0]}: return={row[1]:.2f}%, sharpe={row[2]:.2f}")
    except sqlite3.OperationalError as e:
        print(f"Metrics table not yet available: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
