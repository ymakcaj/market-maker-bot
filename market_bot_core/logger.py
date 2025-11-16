"""Logging helpers shared across bots."""

from __future__ import annotations

import logging
from typing import Optional


def setup_basic_logging(level: int = logging.INFO) -> None:
    """Ensure basic logging configuration is applied once."""
    configured = getattr(setup_basic_logging, "_configured", False)
    if configured:
        return
    logging.basicConfig(level=level)
    setattr(setup_basic_logging, "_configured", True)


def get_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    """Return a module-level logger with optional custom level."""
    setup_basic_logging()
    logger = logging.getLogger(name)
    if level is not None:
        logger.setLevel(level)
    return logger


__all__ = ["get_logger", "setup_basic_logging"]
