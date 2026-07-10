"""Gracy 2.0 — Python's most graceful API Client Framework, Rust-powered."""

from __future__ import annotations

import logging

from gracy._core import engine_version

__version__ = "2.0.0a0"

logging.getLogger("gracy").addHandler(logging.NullHandler())

__all__ = ["__version__", "engine_version"]
