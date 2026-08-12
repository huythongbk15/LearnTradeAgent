"""Allow `python -m trading_agent.cli` (same as the legacy module)."""

from trading_agent.cli.app import main

if __name__ == "__main__":
    main()
