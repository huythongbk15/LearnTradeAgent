"""
Centralized logging configuration for Trading Agent System.

Usage:
    from trading_agent.log_config import setup_logging
    setup_logging()

    # In any module:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("message", extra={"key": "value"})
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_LOG_CONFIGURED = False


def setup_logging(
    level: str = "INFO",
    log_file: str | None = "logs/trading_agent.log",
    max_bytes: int = 10 * 1024 * 1024,  # 10 MB
    backup_count: int = 30,
    force: bool = False,
) -> None:
    """Configure logging once. Idempotent unless force=True.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL
        log_file: Path to log file (None = file logging disabled)
        max_bytes: Max size per log file before rotation
        backup_count: Number of rotated logs to keep
        force: Reconfigure even if already configured
    """
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED and not force:
        return

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # --- Formatters ---
    detailed_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_fmt = logging.Formatter(
        "%(levelname)-8s %(message)s",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear existing handlers to avoid duplicates on force
    if force:
        root_logger.handlers.clear()

    if root_logger.handlers:
        _LOG_CONFIGURED = True
        return

    # --- Console handler (stderr) ---
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # --- File handler (rotating) ---
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_path),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(detailed_fmt)
        root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.WARNING)

    _LOG_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Get a logger for the named module. Guarantees setup is called."""
    if not _LOG_CONFIGURED:
        setup_logging()
    return logging.getLogger(name)
