"""Rotating file log + console when run with --console."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import log_dir


def setup(console: bool = False) -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = RotatingFileHandler(log_dir() / "agent.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if console and sys.stderr:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return logging.getLogger("agent")
