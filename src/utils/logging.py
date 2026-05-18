"""Shared logger factory. Console output + rolling per-day file under logs/."""

from __future__ import annotations

import logging
from datetime import date
from logging.handlers import RotatingFileHandler

from src.config import get_settings

_CONFIGURED: set[str] = set()


def get_logger(name: str = "stockfinder") -> logging.Logger:
    logger = logging.getLogger(name)
    if name in _CONFIGURED:
        return logger

    settings = get_settings()
    logger.setLevel(settings.log_level)

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_path = settings.log_dir / f"{date.today().isoformat()}.log"
    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3)
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.propagate = False
    _CONFIGURED.add(name)
    return logger
