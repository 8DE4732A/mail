"""Application logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from mailtui.paths import ensure_dirs, state_dir


def setup_logging(debug: bool = False) -> None:
    ensure_dirs()
    logger = logging.getLogger()
    if logger.handlers:
        return
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    handler = RotatingFileHandler(
        state_dir() / "mail.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
