from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    resolved = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=resolved,
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
