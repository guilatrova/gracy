from contextlib import contextmanager
from contextvars import ContextVar

from .models import GracyConfig

gracy_context: ContextVar[GracyConfig | None] = ContextVar("gracy_context", default=None)


@contextmanager
def custom_gracy_config(config: GracyConfig):
    token = gracy_context.set(config)

    try:
        yield
    finally:
        gracy_context.reset(token)
