"""Response validation: status policies (strict/allow/default) + the Validator protocol.

Validators are plain sync objects exposing `check(response) -> None` that raise
to signal failure (see gracy._protocols.Validator). Whatever they raise feeds
the retry `on=` matching in pipeline.py, exactly like v1's GracefulValidator.
"""

from __future__ import annotations

import typing as t

from gracy._protocols import Validator
from gracy._types import UNSET, Response
from gracy.config import StatusPolicy
from gracy.exceptions import GracyConfigError, NonOkResponse, UnexpectedResponse

__all__ = [
    "Validator",
    "check_status_policy",
    "normalize_validators",
]


def check_status_policy(policy: StatusPolicy, response: Response) -> None:
    """Enforce a StatusPolicy against a response. Raises on violation.

    - "strict": ONLY the listed codes pass (even 200 fails if absent) -> UnexpectedResponse.
    - "allow": 2xx OR any listed code passes -> NonOkResponse otherwise.
    - "default": 2xx passes -> NonOkResponse otherwise.
    """
    kind = policy.kind

    if kind == "strict":
        if response.status in policy.codes:
            return
        raise UnexpectedResponse(
            f"{response.url} returned {response.status}, expected one of {policy.codes}",
            response,
            expected=policy.codes,
        )

    if kind == "allow":
        if response.is_success or response.status in policy.codes:
            return
        raise NonOkResponse(
            f"{response.url} returned {response.status} (not 2xx nor in allowed {policy.codes})",
            response,
        )

    if kind == "default":
        if response.is_success:
            return
        raise NonOkResponse(f"{response.url} returned {response.status}", response)

    raise GracyConfigError(  # pragma: no cover - StatusPolicy.kind is a Literal
        f"Unknown status policy kind: {kind!r}"
    )


def _is_validator(obj: t.Any) -> bool:
    return callable(getattr(obj, "check", None))


def normalize_validators(value: t.Any) -> tuple[Validator, ...]:
    """Normalize GracyConfig.validators into a tuple of Validator objects.

    Accepts UNSET/None (-> ()), a single validator, or an iterable of them.
    A "validator" is anything with a callable `check(response) -> None`.
    """
    if value is UNSET or value is None:
        return ()

    if _is_validator(value):
        return (t.cast(Validator, value),)

    if isinstance(value, t.Iterable) and not isinstance(value, (str, bytes)):
        validators: list[Validator] = []
        for item in value:
            if not _is_validator(item):
                raise GracyConfigError(
                    f"Invalid validator {item!r}: validators must implement "
                    f"`check(response: Response) -> None` (raise to signal failure); "
                    f"see gracy.validators.Validator"
                )
            validators.append(t.cast(Validator, item))
        return tuple(validators)

    raise GracyConfigError(
        f"Invalid validators value {value!r}: expected a Validator (an object with a "
        f"callable `check(response: Response) -> None` that raises on failure), an "
        f"iterable of them, or None; see gracy.validators.Validator"
    )
