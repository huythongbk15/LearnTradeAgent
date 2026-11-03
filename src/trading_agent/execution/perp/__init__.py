"""USD-M perpetual execution on Binance Demo Trading.

The second market type of the core, next to spot. Orders leave only through
``venue.PerpDemoVenue``, which refuses any host but demo-fapi. Rules:
docs/operations/PERP_DEMO_RUNNER.md.
"""
