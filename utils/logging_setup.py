"""Logging setup. Never logs secret values - only whether a key is present."""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    debug = os.getenv("UFC_RADAR_DEBUG", "0") == "1"
    resolved = level or ("DEBUG" if debug else "INFO")
    logging.basicConfig(
        level=getattr(logging, resolved.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # These libraries are chatty at DEBUG level and drown out our own logs.
    for noisy in ("urllib3", "trafilatura", "charset_normalizer", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
