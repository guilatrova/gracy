from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from ._models import GracyConfig

custom_config_context: ContextVar[GracyConfig | None] = ContextVar("gracy_context", default=None)
within_hook_context: ContextVar[bool] = ContextVar("within_hook_context", default=False)


@contextmanager
def custom_gracy_config(config: GracyConfig):
    token = custom_config_context.set(config)

    try:
        yield
    finally:
        custom_config_context.reset(token)


@contextmanager
def within_hook():
    token = within_hook_context.set(True)

    try:
        yield
    finally:
        within_hook_context.reset(token)
