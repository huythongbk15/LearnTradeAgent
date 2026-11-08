"""H-KRW-FADE-16 as book KRW: short the Binance USDT perp against BTCUSDT for 72 h after an Upbit KRW-market listing notice.

Public data only: the Upbit notice list (feed.py, one allowlisted path), the frozen event rule (rules.py), the research
trade on mainnet 1h bars and funding as a shadow row (shadow.py, through the NL-P allowlisted client) and the parity run
on the research packet (parity.py). The book that sends demo orders is src/trading_agent/execution/perp/krwfade.py.
Rules: docs/operations/PERP_DEMO_RUNNER.md, "Book KRW". Research description, not advice.
"""
