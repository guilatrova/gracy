"""Decoding responses into endpoint return annotations.

`decode_response()` is the single resolution point the pipeline calls after
the retry loop settles (see gracy.pipeline.decode_result). Built-in handling
covers Response passthrough, Optional/Union arms, JSON containers, scalars,
and plain dataclasses; anything richer plugs in via the Decoder protocol
(PydanticDecoder / MsgspecDecoder ship here as lazy extras).
"""

from __future__ import annotations

import dataclasses
import inspect
import types
import typing as t

from gracy._protocols import Decoder
from gracy._types import Response
from gracy.exceptions import GracyConfigError, GracyParseFailed

__all__ = [
    "decode_response",
    "JsonDecoder",
    "PydanticDecoder",
    "MsgspecDecoder",
]

_NONE_TYPE = type(None)
_JSON_LIKE_BASES = (dict, list, str, bytes, int, float, bool)


def _type_name(tp: t.Any) -> str:
    name = getattr(tp, "__name__", None)
    return name if isinstance(name, str) else repr(tp)


def _is_json_like(tp: t.Any) -> bool:
    origin = t.get_origin(tp)
    if origin is t.Union or origin is types.UnionType:  # e.g. list[int | None] args
        return all(arg is _NONE_TYPE or _is_json_like(arg) for arg in t.get_args(tp))
    base = origin if origin is not None else tp
    if base not in _JSON_LIKE_BASES:
        return False
    if origin is None:
        return True
    # A parametrized container is json-like only when every type arg is itself
    # json-like (or Any/Ellipsis): list[Model] / dict[str, Model] must fall
    # through to the configured Decoder instead of returning raw json.
    return all(arg is t.Any or arg is Ellipsis or _is_json_like(arg) for arg in t.get_args(tp))


def _decode_json_like(tp: t.Any, response: Response) -> t.Any:
    """dict/list (and their generics) -> json(); str -> text; bytes -> body; scalars -> cast json()."""
    origin = t.get_origin(tp)
    base = origin if origin is not None else tp
    try:
        if base is dict or base is list:
            return response.json()
        if base is str:
            return response.text
        if base is bytes:
            return response.body
        if base is int or base is float or base is bool:
            return base(response.json())
    except Exception as exc:
        raise GracyParseFailed(
            f"Failed to decode response from {response.url} as {_type_name(tp)}: {exc}", response
        ) from exc
    raise GracyConfigError(f"JsonDecoder cannot decode {tp!r}; it only handles dict/list/str/bytes/int/float/bool")


def _decode_concrete(response: Response, return_type: t.Any, decoder: Decoder | None) -> t.Any:
    """Steps 4-8 of the resolution order: a single non-Union, non-Response type."""
    if _is_json_like(return_type):
        return _decode_json_like(return_type, response)

    if decoder is not None and decoder.handles(return_type):
        try:
            return decoder.decode(return_type, response)
        except (GracyParseFailed, ImportError):
            raise
        except Exception as exc:
            raise GracyParseFailed(
                f"{type(decoder).__name__} failed to decode response from {response.url} "
                f"as {_type_name(return_type)}: {exc}",
                response,
            ) from exc

    if isinstance(return_type, type) and dataclasses.is_dataclass(return_type):
        try:
            return return_type(**response.json())
        except Exception as exc:
            raise GracyParseFailed(
                f"Failed to build dataclass {_type_name(return_type)} from response "
                f"from {response.url}: {exc}",
                response,
            ) from exc

    raise GracyConfigError(
        f"Don't know how to decode a response into {return_type!r}. "
        f"Plug a Decoder into your config, e.g. GracyConfig(decoder=PydanticDecoder()) "
        f"(from gracy.parsing; requires pydantic) or MsgspecDecoder() (requires msgspec), "
        f"or implement the gracy Decoder protocol (handles/decode) for this type."
    )


def decode_response(response: Response, return_type: t.Any, decoder: Decoder | None = None) -> t.Any:
    """Decode `response` into `return_type` (an endpoint's return annotation).

    Resolution order: missing/None/Any -> the Response itself; Response ->
    passthrough; Optional/Union -> first arm that decodes; json containers and
    scalars -> built-in; then the configured Decoder; then plain dataclasses;
    else GracyConfigError. Decode failures raise GracyParseFailed.
    """
    if (
        return_type is None
        or return_type is _NONE_TYPE  # "-> None" annotation resolved by get_type_hints: no decode
        or return_type is inspect.Signature.empty
        or return_type is t.Any
    ):
        return response
    if return_type is Response:
        return response

    origin = t.get_origin(return_type)
    if origin is t.Union or origin is types.UnionType:
        arms = [arm for arm in t.get_args(return_type) if arm is not _NONE_TYPE]
        if not arms:
            return response
        if len(arms) == 1:
            return decode_response(response, arms[0], decoder)
        errors: list[Exception] = []
        for arm in arms:
            try:
                return decode_response(response, arm, decoder)
            except (GracyParseFailed, GracyConfigError) as arm_exc:
                errors.append(arm_exc)
        if all(isinstance(e, GracyConfigError) for e in errors):
            raise errors[-1]
        raise GracyParseFailed(
            f"Response from {response.url} decoded into no arm of {return_type!r}", response
        ) from errors[-1]

    return _decode_concrete(response, return_type, decoder)


# --------------------------------------------------------------------------- decoders


class JsonDecoder(Decoder):
    """Built-in stdlib decoder: dict/list (and generics), str, bytes, int, float, bool."""

    def handles(self, tp: t.Any) -> bool:
        return _is_json_like(tp)

    def decode(self, tp: t.Any, response: Response) -> t.Any:
        return _decode_json_like(tp, response)


class PydanticDecoder(Decoder):
    """Decodes into pydantic BaseModel subclasses; with adapt_any=True (default),
    handles ANY annotation via a cached pydantic.TypeAdapter."""

    def __init__(self, adapt_any: bool = True) -> None:
        self._adapter_everything = adapt_any
        self._adapters: dict[t.Any, t.Any] = {}

    @staticmethod
    def _pydantic() -> t.Any:
        try:
            import pydantic
        except ImportError as exc:
            raise ImportError(
                "PydanticDecoder requires pydantic. Install it with: pip install 'gracy[pydantic]' "
                "(or pip install pydantic)"
            ) from exc
        return pydantic

    def handles(self, tp: t.Any) -> bool:
        if self._adapter_everything:
            return True
        pydantic = self._pydantic()
        return isinstance(tp, type) and issubclass(tp, pydantic.BaseModel)

    def decode(self, tp: t.Any, response: Response) -> t.Any:
        pydantic = self._pydantic()
        if isinstance(tp, type) and issubclass(tp, pydantic.BaseModel):
            try:
                return tp.model_validate_json(response.body)
            except Exception:  # noqa: BLE001 - fall back to the parsed-python path
                return tp.model_validate(response.json())
        try:
            adapter = self._adapters[tp]
        except KeyError:
            adapter = pydantic.TypeAdapter(tp)
            self._adapters[tp] = adapter
        except TypeError:  # unhashable annotation: skip the cache
            adapter = pydantic.TypeAdapter(tp)
        try:
            return adapter.validate_json(response.body)
        except Exception:  # noqa: BLE001 - fall back to the parsed-python path
            return adapter.validate_python(response.json())


class MsgspecDecoder(Decoder):
    """Decodes via msgspec.json.decode(body, type=tp). Handles msgspec.Struct
    subclasses; with adapt_any=True (default), handles ANY annotation."""

    def __init__(self, adapt_any: bool = True) -> None:
        self._adapter_everything = adapt_any

    @staticmethod
    def _msgspec() -> t.Any:
        try:
            import msgspec
        except ImportError as exc:
            raise ImportError(
                "MsgspecDecoder requires msgspec. Install it with: pip install 'gracy[msgspec]' "
                "(or pip install msgspec)"
            ) from exc
        return msgspec

    def handles(self, tp: t.Any) -> bool:
        if self._adapter_everything:
            return True
        msgspec = self._msgspec()
        return isinstance(tp, type) and issubclass(tp, msgspec.Struct)

    def decode(self, tp: t.Any, response: Response) -> t.Any:
        msgspec = self._msgspec()
        return msgspec.json.decode(response.body, type=tp)
