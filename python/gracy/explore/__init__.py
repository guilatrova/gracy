"""gracy explore — the `gracy -i` interactive API explorer engine.

:class:`ExploreSession` records real requests (full gracy pipeline), names
endpoints, infers pydantic models, and generates a typed Gracy client
(+ replay tests) from the recorded session.
"""

from __future__ import annotations

from gracy.explore._session import ExploreSession, StepResult

__all__ = ["ExploreSession", "StepResult"]
