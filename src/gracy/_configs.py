from contextlib import contextmanager
from contextvars import ContextVar

from ._models import GracyConfig

custom_config_context: ContextVar[GracyConfig | None] = ContextVar("gracy_context", default=None)


@contextmanager
def custom_gracy_config(config: GracyConfig):
    token = custom_config_context.set(config)

    try:
        yield
    finally:
        custom_config_context.reset(token)
