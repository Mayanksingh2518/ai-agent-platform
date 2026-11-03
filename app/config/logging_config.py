"""Logging to a file and stderr. Never stdout, which MCP STDIO reserves for protocol messages."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from app.config.settings import Settings


def configure_logging(settings: Settings) -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers: list[logging.Handler] = [
        RotatingFileHandler(settings.log_dir / "agents.log", maxBytes=2_000_000, backupCount=2),
        logging.StreamHandler(sys.stderr),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    # The HTTP client logs every request at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
