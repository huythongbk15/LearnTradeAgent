"""USD-M perpetual execution on Binance.

The second market type of the core, next to spot. Orders leave only through
``venue.PerpDemoVenue``, which refuses any host but demo-fapi (rules:
docs/operations/PERP_DEMO_RUNNER.md), or ``venue.PerpLiveVenue``, mainnet for
perp tier R0 only, which refuses any host but fapi.binance.com (rules:
docs/operations/PERP_LIVE_R0.md).
"""
