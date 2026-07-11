"""Drop-in compat adapters: swap ``import requests`` / ``import httpx`` for gracy.

Usage::

    from gracy.compat import requests   # sync, requests-shaped
    from gracy.compat import httpx      # async/sync, httpx-shaped

Submodules are loaded lazily via module ``__getattr__`` so importing one
adapter never drags in the other's optional dependencies. First access also
registers the real module under ``gracy.compat.<name>`` in ``sys.modules``,
so both ``from gracy.compat import requests`` and
``gracy.compat.requests.get(...)`` work.
"""

from __future__ import annotations

import importlib
import sys
import typing as t

__all__ = ["requests", "httpx"]

_SUBMODULES: t.Final[dict[str, str]] = {
    "requests": "gracy.compat._requests",
    "httpx": "gracy.compat._httpx",
}


def __getattr__(name: str) -> t.Any:
    target = _SUBMODULES.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = importlib.import_module(target)
    # Make `gracy.compat.requests` importable/resolvable as a real submodule
    # and cache it on the package so __getattr__ runs only once per name.
    sys.modules[f"{__name__}.{name}"] = module
    setattr(sys.modules[__name__], name, module)
    return module


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_SUBMODULES))
