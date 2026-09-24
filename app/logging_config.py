"""Structured-ish logging setup. Keeps a single configuration entry point."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    # stderr, never stdout: the MCP stdio transport owns stdout for JSON-RPC, and
    # any log line written there corrupts the protocol stream.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Quiet noisy third parties.
    for noisy in ("httpx", "httpcore", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
