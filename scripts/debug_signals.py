#!/usr/bin/env python3
"""Debug: kiểm tra tín hiệu agents tại các mốc thị trường khác nhau (đúng window)."""
import sys
import os
sys.path.insert(0, '/home/huythong/.qwenpaw/workspaces/trading')
os.environ['USE_LLM'] = 'false'

import polars as pl
from trading_agent.agents.orchestrator import Orchestrator
from trading_agent.data.storage import load_ohlcv

df = load_ohlcv('binance', 'BTC/USDT', '1h').sort('timestamp')
ts_col = df['timestamp'].cast(pl.Utf8).str.slice(0, 19)

for ts in ['2024-08-05 12:00:00', '2024-11-15 00:00:00', '2026-02-05 20:00:00', '2026-07-29 15:00:00']:
    idx = int(df.with_row_index().filter(ts_col == ts)['index'][0])
    orch = Orchestrator()
    r = orch.analyze(symbol='BTC/USDT', timeframe='1h', current_position_pct=0.0,
                     df=df.head(idx + 1))
    d = r.final_decision
    ma20 = r.indicators.get('ma_20')
    ma50 = r.indicators.get('ma_50')
    rsi = r.indicators.get('rsi')
    sigs = {m.role: f'{m.signal}/{m.confidence:.2f}' for m in r.agent_messages}
    print(f'{ts} | MA20={ma20:.0f} MA50={ma50:.0f} RSI={rsi:.1f} | final={d.signal} conf={d.confidence:.2f} risk={d.risk_level}')
    print(f'   {sigs}')
