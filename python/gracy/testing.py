"""Test switches: kill retries/throttling inside a `with` block, plus test helpers.

Usage::

    with gracy.testing.retries_off(), gracy.testing.throttle_off():
        async with PokeAPI(transport=gracy.testing.MockTransport({...})) as api:
            ...

Two layers make the switches effective everywhere:

1. **Build time** — the client calls :func:`apply_test_overrides` on the
   resolved config before compiling the plan, stripping ``retry=`` and
   ``throttle=`` so nothing is compiled into the scheduler plan.
2. **Runtime** — throttle rules already compiled into a *running* scheduler
   can't be un-compiled, so ``client._call`` paths must also check
   :func:`throttle_is_disabled` (i.e. ``_throttle_disabled``) per request and
   submit with ``no_throttle=True``, making :func:`throttle_off` work even for
   clients built OUTSIDE the ``with`` block.
"""

from __future__ import annotations

import dataclasses
import typing as t
from contextlib import contextmanager
from contextvars import ContextVar

from gracy.config import GracyConfig
from gracy.pipeline import in_hook_context
from gracy.transports import MockTransport

__all__ = [
    "MockTransport",
    "apply_test_overrides",
    "in_hook_context",
    "retries_disabled",
    "retries_off",
    "throttle_is_disabled",
    "throttle_off",
]

# The client imports these ContextVars directly (they are defined HERE).
_retries_disabled: ContextVar[bool] = ContextVar("gracy_retries_disabled", default=False)
_throttle_disabled: ContextVar[bool] = ContextVar("gracy_throttle_disabled", default=False)


@contextmanager
def retries_off() -> t.Iterator[None]:
    """Disable ALL retry policies for clients built (or requests issued) inside the block."""
    token = _retries_disabled.set(True)
    try:
        yield
    finally:
        _retries_disabled.reset(token)


@contextmanager
def throttle_off() -> t.Iterator[None]:
    """Disable ALL throttling inside the block.

    Works at build time (rules stripped from the compiled plan) AND at runtime:
    the client checks :func:`throttle_is_disabled` per request and submits with
    ``no_throttle=True``, so already-built clients are covered too.
    """
    token = _throttle_disabled.set(True)
    try:
        yield
    finally:
        _throttle_disabled.reset(token)


def retries_disabled() -> bool:
    """True while inside a :func:`retries_off` block."""
    return _retries_disabled.get()


def throttle_is_disabled() -> bool:
    """True while inside a :func:`throttle_off` block.

    Client ``_call`` paths use this to pass ``no_throttle=True`` on submit,
    bypassing throttle rules already compiled into the scheduler plan.
    """
    return _throttle_disabled.get()


def apply_test_overrides(config: GracyConfig) -> GracyConfig:
    """Strip retry/throttle from `config` according to the active test switches."""
    if _retries_disabled.get():
        config = dataclasses.replace(config, retry=None)
    if _throttle_disabled.get():
        config = dataclasses.replace(config, throttle=None)
    return config
