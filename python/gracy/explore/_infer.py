"""JSON samples -> pydantic model tree. Pure functions, no I/O.

Merging rules (the whole lattice):

* field missing in some sample  -> ``Optional[...] = None``
* explicit ``None`` value       -> ``Optional[...]``
* ``int`` + ``float``           -> ``float``
* conflicting scalar types      -> ``Any``
* ``dict``                      -> nested :class:`InferredModel` (name = parent + FieldPascal)
* ``list[dict]``                -> ``list[Nested]``
* empty list                    -> ``list[Any]``

``render_pydantic()`` emits deduped, deterministic source: models with an
identical structural shape collapse into ONE class (the first name wins).
"""

from __future__ import annotations

import keyword
import re
import typing as t
from dataclasses import dataclass, field

__all__ = ["InferredModel", "InferredType", "dedupe_models", "infer", "render_pydantic"]

_SCALARS: t.Final = ("str", "int", "float", "bool")


# --------------------------------------------------------------------------- data model


@dataclass
class InferredType:
    """One field's inferred type. ``kind`` is the tag of a small closed lattice."""

    kind: str  # "any" | "none" | "str" | "int" | "float" | "bool" | "model" | "list"
    model: InferredModel | None = None  # kind == "model"
    item: InferredType | None = None  # kind == "list"; None = list[Any] (only empty lists seen)
    optional: bool = False


@dataclass
class InferredModel:
    name: str
    fields: dict[str, InferredType] = field(default_factory=dict)  # insertion order = first-seen


# --------------------------------------------------------------------------- naming


def pascal(text: str) -> str:
    """"base_stats" -> "BaseStats"; "pokemon-form" -> "PokemonForm"."""
    words = [w for w in re.split(r"[^0-9a-zA-Z]+", text) if w]
    out = "".join(w[:1].upper() + w[1:] for w in words)
    if not out:
        return "Field"
    if out[0].isdigit():
        out = "F" + out
    return out


def safe_identifier(name: str) -> str:
    """Best-effort valid python identifier for a JSON field name."""
    cleaned = re.sub(r"\W", "_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = "f_" + cleaned
    if keyword.iskeyword(cleaned):
        cleaned += "_"
    return cleaned


# --------------------------------------------------------------------------- inference


def _infer_value(value: t.Any, name_hint: str) -> InferredType:
    if value is None:
        return InferredType("none", optional=True)
    if isinstance(value, bool):  # BEFORE int: bool subclasses int
        return InferredType("bool")
    if isinstance(value, int):
        return InferredType("int")
    if isinstance(value, float):
        return InferredType("float")
    if isinstance(value, str):
        return InferredType("str")
    if isinstance(value, dict):
        return InferredType("model", model=_infer_dict(value, name_hint))
    if isinstance(value, list):
        item: InferredType | None = None
        for entry in value:
            entry_type = _infer_value(entry, name_hint)
            item = entry_type if item is None else merge_types(item, entry_type)
        return InferredType("list", item=item)
    return InferredType("any")


def _infer_dict(sample: dict[str, t.Any], name: str) -> InferredModel:
    model = InferredModel(name=name)
    for key, value in sample.items():
        model.fields[str(key)] = _infer_value(value, name + pascal(str(key)))
    return model


def _copy(tp: InferredType, *, optional: bool | None = None) -> InferredType:
    return InferredType(
        kind=tp.kind,
        model=None if tp.model is None else _copy_model(tp.model),
        item=None if tp.item is None else _copy(tp.item),
        optional=tp.optional if optional is None else optional,
    )


def _copy_model(model: InferredModel) -> InferredModel:
    return InferredModel(name=model.name, fields={k: _copy(v) for k, v in model.fields.items()})


def merge_models(a: InferredModel, b: InferredModel) -> InferredModel:
    """Field-wise union; fields present in only one side become optional."""
    merged = InferredModel(name=a.name)
    for key, a_type in a.fields.items():
        if key in b.fields:
            merged.fields[key] = merge_types(a_type, b.fields[key])
        else:
            merged.fields[key] = _copy(a_type, optional=True)
    for key, b_type in b.fields.items():
        if key not in merged.fields:
            merged.fields[key] = _copy(b_type, optional=True)
    return merged


def merge_types(a: InferredType, b: InferredType) -> InferredType:
    optional = a.optional or b.optional

    if a.kind == "none" and b.kind == "none":
        return InferredType("none", optional=True)
    if a.kind == "none":
        return _copy(b, optional=True)
    if b.kind == "none":
        return _copy(a, optional=True)

    if a.kind == "any" or b.kind == "any":
        return InferredType("any", optional=optional)

    if a.kind in _SCALARS and b.kind in _SCALARS:
        if a.kind == b.kind:
            return InferredType(a.kind, optional=optional)
        if {a.kind, b.kind} == {"int", "float"}:
            return InferredType("float", optional=optional)
        return InferredType("any", optional=optional)

    if a.kind == "model" and b.kind == "model":
        assert a.model is not None and b.model is not None
        return InferredType("model", model=merge_models(a.model, b.model), optional=optional)

    if a.kind == "list" and b.kind == "list":
        if a.item is None:
            item = None if b.item is None else _copy(b.item)
        elif b.item is None:
            item = _copy(a.item)
        else:
            item = merge_types(a.item, b.item)
        return InferredType("list", item=item, optional=optional)

    return InferredType("any", optional=optional)


def infer(samples: list[t.Any], name: str) -> InferredModel:
    """Merge JSON dict samples into one model tree. Raises TypeError on non-dict samples."""
    if not samples:
        return InferredModel(name=name)
    merged: InferredModel | None = None
    for sample in samples:
        if not isinstance(sample, dict):
            raise TypeError(f"infer() needs dict samples; got {type(sample).__name__}")
        model = _infer_dict(sample, name)
        merged = model if merged is None else merge_models(merged, model)
    assert merged is not None
    return merged


# --------------------------------------------------------------------------- rendering


def _fingerprint(tp: InferredType) -> t.Any:
    """Structural (name-free) shape of a type - the dedup key."""
    if tp.kind == "model":
        assert tp.model is not None
        return ("model", _model_fingerprint(tp.model), tp.optional)
    if tp.kind == "list":
        return ("list", None if tp.item is None else _fingerprint(tp.item), tp.optional)
    return (tp.kind, tp.optional)


def _model_fingerprint(model: InferredModel) -> t.Any:
    return tuple((name, _fingerprint(tp)) for name, tp in model.fields.items())


def _walk_post_order(model: InferredModel, out: list[InferredModel]) -> None:
    for tp in model.fields.values():
        _walk_field(tp, out)
    out.append(model)


def _walk_field(tp: InferredType, out: list[InferredModel]) -> None:
    if tp.kind == "model" and tp.model is not None:
        _walk_post_order(tp.model, out)
    elif tp.kind == "list" and tp.item is not None:
        _walk_field(tp.item, out)


def dedupe_models(models: t.Sequence[InferredModel]) -> tuple[list[InferredModel], dict[str, str]]:
    """(unique models in definition order, original name -> canonical name).

    Identical structural shapes collapse into the FIRST model encountered
    (children always precede their parents so references stay valid).
    """
    ordered: list[InferredModel] = []
    for model in models:
        _walk_post_order(model, ordered)

    by_shape: dict[t.Any, str] = {}
    name_map: dict[str, str] = {}
    unique: list[InferredModel] = []
    used_names: set[str] = set()

    for model in ordered:
        shape = _model_fingerprint(model)
        if shape in by_shape:
            name_map[model.name] = by_shape[shape]
            continue
        final = model.name
        while final in used_names:  # same name, different shape: disambiguate
            final += "_"
        used_names.add(final)
        by_shape[shape] = final
        name_map[model.name] = final
        if final != model.name:
            model = InferredModel(name=final, fields=model.fields)
        unique.append(model)
    return unique, name_map


def type_source(tp: InferredType, name_map: dict[str, str]) -> str:
    if tp.kind == "any":
        base = "t.Any"
    elif tp.kind == "none":
        base = "None"
    elif tp.kind in _SCALARS:
        base = tp.kind
    elif tp.kind == "model":
        assert tp.model is not None
        base = name_map.get(tp.model.name, tp.model.name)
    elif tp.kind == "list":
        inner = "t.Any" if tp.item is None else type_source(tp.item, name_map)
        base = f"list[{inner}]"
    else:  # pragma: no cover - closed lattice
        raise ValueError(f"unknown kind {tp.kind!r}")
    if tp.optional and tp.kind != "none":
        base += " | None"
    return base


def render_pydantic(models: t.Sequence[InferredModel]) -> str:
    """Deduped pydantic class source (no imports; deterministic ordering)."""
    unique, name_map = dedupe_models(models)
    blocks: list[str] = []
    for model in unique:
        lines = [f"class {model.name}(BaseModel):"]
        if not model.fields:
            lines.append("    pass")
        for raw_name, tp in model.fields.items():
            attr = safe_identifier(raw_name)
            annotation = type_source(tp, name_map)
            default = ""
            if tp.optional or tp.kind == "none":
                default = " = None"
            if attr != raw_name:
                alias = f'Field(default=None, alias="{raw_name}")' if default else f'Field(alias="{raw_name}")'
                lines.append(f"    {attr}: {annotation} = {alias}")
            else:
                lines.append(f"    {attr}: {annotation}{default}")
        blocks.append("\n".join(lines))
    return "\n\n\n".join(blocks) + ("\n" if blocks else "")
