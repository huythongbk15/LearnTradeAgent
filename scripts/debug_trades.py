#!/usr/bin/env python3
"""Kiểm tra toàn bộ trade history + pnl thật."""
import sys
sys.path.insert(0, '/home/huythong/.qwenpaw/workspaces/trading')
from trading_agent.execution.paper_exchange import PaperExchange

ex = PaperExchange(exchange_name='binance', initial_balance=10000)
trades = ex.get_trade_history(limit=100)
print(f"Tổng trades: {len(trades)}")
wins = 0
tot_pnl = 0
for t in sorted(trades, key=lambda x: x.entry_time or ''):
    tot_pnl += t.pnl
    mark = '🟢' if t.pnl > 0 else ('🔴' if t.pnl < 0 else '⚪')
    if t.pnl > 0:
        wins += 1
    print(f"{mark} {t.quantity:.4f} @ {t.entry_price:>10,.2f} → {t.exit_price:>10,.2f} "
          f"pnl {t.pnl:>+10.2f} ({t.pnl_pct:+.1f}%) [{t.reason}]")
print(f"\nWin rate: {wins}/{len(trades)} = {wins/len(trades)*100:.1f}%")
print(f"Total realized PnL: {tot_pnl:+.2f}")
