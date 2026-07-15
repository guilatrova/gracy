"""Command outcomes and the shared command dispatcher (`gracy explore` / `gracy x`)."""

from __future__ import annotations

import typing as t
from dataclasses import asdict, dataclass, field

from gracy.explore._parser import HELP_TEXT, Command
from gracy.explore._session import ExploreSession, StepResult, split_segments


@dataclass
class Outcome:
    """One executed command: machine dict (for --json), plain line, extras."""

    kind: str
    data: dict[str, t.Any]
    human: str
    step: StepResult | None = None
    hints: list[str] = field(default_factory=list)
    panel: tuple[str, str] | None = None  # (title, python source) for show model/class


# --------------------------------------------------------------------------- dispatch


async def execute_command(session: ExploreSession, cmd: Command) -> Outcome:
    """Run one parsed command against the session (shared by REPL and one-shot)."""
    handler = _HANDLERS.get(cmd.kind)
    if handler is None:  # unreachable through parse_command; belt and braces
        raise ValueError(f"unhandled command kind {cmd.kind!r}")
    return await handler(session, cmd)


async def _do_request(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.method is not None and cmd.path is not None
    result = await session.execute(
        cmd.method,
        cmd.path,
        query=cmd.query or None,
        headers=cmd.headers or None,
        body=cmd.body,
        body_json=cmd.body_json,
    )
    hints: list[str] = []
    if result.template_proposal:
        hints.append(
            f"✨ one segment differs from an existing endpoint - template proposal: "
            f"{result.template_proposal} (run `endpoint <name>` to fold it in)"
        )
    hints.extend(f"✨ {line}" for line in result.model_drift)
    if result.matched_endpoint is None and result.template_proposal is None and result.error is None:
        hints.append(f"✨ tip: `endpoint {_suggest_name(result.path)}` to turn this request into an endpoint")

    status = str(result.status) if result.status is not None else "ERROR"
    human = f"{result.method} {result.url} -> {status} ({result.elapsed_ms:.1f} ms)"
    if result.error:
        human += f" - {result.error}"
    return Outcome("request", asdict(result), human, step=result, hints=hints)


def _suggest_name(path: str) -> str:
    from gracy.explore._infer import pascal

    segments = split_segments(path)
    return pascal(segments[0]) if segments else "Endpoint"


async def _do_endpoint(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.name is not None
    existed = cmd.name in session.endpoints()
    template = session.name_endpoint(cmd.name)
    verb = "folded into" if existed else "created endpoint"
    hints: list[str] = []
    params = session.endpoints()[cmd.name]["params"]
    if params:
        listed = ", ".join(f"{{{p['name']}}} (segment {p['index']})" for p in params)
        hints.append(f"✨ params: {listed} - rename with `param <index> as <name>`")
    return Outcome(
        "endpoint",
        {"ok": True, "endpoint": cmd.name, "template": template, "created": not existed},
        f"{verb} '{cmd.name}' -> {template}",
        hints=hints,
    )


async def _do_rename(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.target is not None and cmd.name is not None and cmd.value is not None
    if cmd.target == "endpoint":
        session.rename_endpoint(cmd.name, cmd.value)
    else:
        session.rename_model(cmd.name, cmd.value)
    return Outcome(
        "rename",
        {"ok": True, "target": cmd.target, "old": cmd.name, "new": cmd.value},
        f"renamed {cmd.target} '{cmd.name}' -> '{cmd.value}'",
    )


async def _do_drop(session: ExploreSession, cmd: Command) -> Outcome:
    if cmd.target == "endpoint":
        assert cmd.name is not None
        info = session.drop_endpoint(cmd.name)
        freed, absorbed = info["freed"], info["absorbed"]
        if absorbed:
            note = " (" + ", ".join(f"{n} step -> {ep}" for ep, n in absorbed.items()) + ")"
        elif freed:
            note = f" ({freed} step{'' if freed == 1 else 's'} now unnamed)"
        else:
            note = ""
        return Outcome(
            "drop",
            {"ok": True, "target": "endpoint", "name": cmd.name, **info},
            f"dropped endpoint '{cmd.name}'{note}",
        )
    assert cmd.index is not None
    info = session.drop_step(cmd.index)
    tail = f" (was in {info['endpoint']})" if info["endpoint"] else " (unnamed)"
    return Outcome(
        "drop",
        {"ok": True, "target": "step", **info},
        f"dropped step {cmd.index} {info['path']}{tail}",
    )


async def _do_prune(session: ExploreSession, cmd: Command) -> Outcome:
    info = session.prune_steps()
    n = info["removed"]
    human = f"pruned {n} unnamed step{'' if n == 1 else 's'}" if n else "no unnamed steps to prune"
    return Outcome("prune", {"ok": True, **info}, human)


async def _do_model(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.name is not None
    session.set_model_name(cmd.name)
    which = "request" if cmd.name.endswith("!request") else "response"
    plain = cmd.name.removesuffix("!request")
    return Outcome("model", {"ok": True, "model": plain, "target": which}, f"{which} model named '{plain}'")


async def _do_on(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.status is not None and cmd.action is not None
    endpoint = session._last_endpoint()  # noqa: SLF001 - same package
    session.set_on(endpoint, cmd.status, cmd.action)
    return Outcome(
        "on",
        {"ok": True, "endpoint": endpoint, "status": cmd.status, "action": cmd.action},
        f"{endpoint}: on {cmd.status} -> {cmd.action}",
    )


async def _do_param(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.index is not None and cmd.name is not None
    endpoint = session._last_endpoint()  # noqa: SLF001 - same package
    template = session.set_param_name(endpoint, cmd.index, cmd.name)
    return Outcome(
        "param",
        {"ok": True, "endpoint": endpoint, "template": template},
        f"{endpoint}: {template}",
    )


async def _do_set(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.name is not None and cmd.path is not None
    value = session.capture(cmd.name, cmd.path)
    return Outcome(
        "set",
        {"ok": True, "name": cmd.name, "value": value, "path": cmd.path},
        f"captured {cmd.name} = {value} (from {cmd.path})",
    )


async def _do_peek(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.path is not None
    value = session.peek(cmd.path)  # resolve-only, no capture
    return Outcome(
        "peek",
        {"ok": True, "path": cmd.path, "value": value},
        f"{cmd.path} = {value!r}",
    )


async def _do_policy(session: ExploreSession, cmd: Command) -> Outcome:
    kwargs: dict[str, t.Any] = {}
    if cmd.kind == "retry":
        kwargs["retry"] = cmd.spec
    elif cmd.kind == "throttle":
        kwargs["throttle"] = cmd.spec
    elif cmd.kind == "timeout":
        kwargs["timeout"] = float(t.cast(str, cmd.value))
    elif cmd.kind == "auth":
        kwargs["auth"] = cmd.spec
    elif cmd.kind == "header":
        kwargs["header"] = (t.cast(str, cmd.name), t.cast(str, cmd.value))
    elif cmd.kind == "base":
        kwargs["base_url"] = cmd.value
    line = session.set_policy(**kwargs)
    return Outcome(cmd.kind, {"ok": True, "policy": line}, line)


async def _do_show(session: ExploreSession, cmd: Command) -> Outcome:
    if cmd.target == "last":
        history = session.history()
        if not history:
            raise ValueError("no steps yet - run a request first")
        last = history[-1]
        step = StepResult(**last)
        status = str(step.status) if step.status is not None else "ERROR"
        human = f"{step.method} {step.url} -> {status} ({step.elapsed_ms:.1f} ms)"
        return Outcome("request", last, human, step=step)
    if cmd.target == "model":
        source = session.model_preview(cmd.name)
        return Outcome("show_model", {"ok": True, "source": source}, source, panel=(cmd.name or "models", source))
    if cmd.target == "class":
        source = session.class_preview()
        return Outcome("show_class", {"ok": True, "source": source}, source, panel=("class", source))
    if cmd.target == "endpoints":
        from gracy.explore._session import template_matches

        endpoints = session.endpoints()

        def _shadowed_by(name: str, ep: dict[str, t.Any]) -> str | None:
            # another endpoint whose template already covers this one's path
            # (e.g. /pokemon/{pokemon} covers the literal /pokemon/pikachu)
            for other, o in endpoints.items():
                if (
                    other != name
                    and o["method"] == ep["method"]
                    and o["template"] != ep["template"]
                    and template_matches(o["template"], ep["template"])
                ):
                    return other
            return None

        lines = []
        for name, ep in endpoints.items():
            shadow = _shadowed_by(name, ep)
            note = f"  (redundant: covered by {shadow}, `drop endpoint {name}`)" if shadow else ""
            lines.append(
                f"{ep['method']:6} {ep['template']}  -> {name}"
                f" (steps={ep['steps']}, on={ep['on'] or '{}'},"
                f" response={ep['response_model'] or '(auto)'}){note}"
            )
        unnamed = sum(1 for s in session.history() if s["matched_endpoint"] is None)
        body = "\n".join(lines) or "(no endpoints yet)"
        if unnamed:
            body += (
                f"\n+ {unnamed} unnamed step{'' if unnamed == 1 else 's'} not in any endpoint"
                " (`show history` to see, `prune` to clear)"
            )
        return Outcome("endpoints", {"ok": True, "endpoints": endpoints, "unnamed_steps": unnamed}, body)
    if cmd.target == "history":
        history = session.history()
        lines = [
            f"{h['step_id']:>3}  {h['method']:6} {h['path']}  "
            f"{h['status'] if h['status'] is not None else 'ERR'}  {h['elapsed_ms']:.0f}ms"
            + (f"  [{h['matched_endpoint']}]" if h["matched_endpoint"] else "")
            for h in history
        ]
        return Outcome("history", {"ok": True, "history": history}, "\n".join(lines) or "(no steps yet)")
    if cmd.target == "captures":
        captures = session.captures
        lines = [f"{name} = {value}" for name, value in captures.items()]
        return Outcome("captures", {"ok": True, "captures": dict(captures)}, "\n".join(lines) or "(none yet)")
    raise ValueError(f"unknown show target {cmd.target!r}")


async def _do_undo(session: ExploreSession, cmd: Command) -> Outcome:
    line = session.undo()
    return Outcome("undo", {"ok": True, "undo": line}, line)


async def _do_export(session: ExploreSession, cmd: Command) -> Outcome:
    assert cmd.path is not None
    paths = session.save_code(cmd.path, tests=cmd.tests)
    written = [str(p) for p in paths]
    return Outcome("export", {"ok": True, "written": written}, "wrote " + ", ".join(written))


async def _do_help(session: ExploreSession, cmd: Command) -> Outcome:
    return Outcome("help", {"ok": True, "help": HELP_TEXT}, HELP_TEXT)


async def _do_quit(session: ExploreSession, cmd: Command) -> Outcome:
    return Outcome("quit", {"ok": True}, "bye")


_HANDLERS: t.Final[dict[str, t.Callable[[ExploreSession, Command], t.Awaitable[Outcome]]]] = {
    "request": _do_request,
    "endpoint": _do_endpoint,
    "rename": _do_rename,
    "drop": _do_drop,
    "prune": _do_prune,
    "model": _do_model,
    "on": _do_on,
    "param": _do_param,
    "set": _do_set,
    "peek": _do_peek,
    "retry": _do_policy,
    "throttle": _do_policy,
    "timeout": _do_policy,
    "auth": _do_policy,
    "header": _do_policy,
    "base": _do_policy,
    "show": _do_show,
    "undo": _do_undo,
    "export": _do_export,
    "help": _do_help,
    "quit": _do_quit,
}
