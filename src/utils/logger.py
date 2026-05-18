"""Centralized loguru logger setup.

Importing this module configures the global logger as a side effect.
Other modules can simply `from loguru import logger` after this has been imported once
(typically via `from src.utils.logger import logger`).
"""

import sys

from loguru import logger

from src.config import LOGS_DIR


def setup_logger() -> None:
    """Configure loguru sinks: colored stderr + daily-rotating file in logs/."""
    logger.remove()
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        level="INFO",
    )
    logger.add(
        LOGS_DIR / "agent_{time:YYYY-MM-DD}.log",
        rotation="00:00",  # New file at midnight local time
        retention="30 days",
        encoding="utf-8",
        level="DEBUG",
    )


setup_logger()

__all__ = ["logger"]
