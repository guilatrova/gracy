"""Tab completion, ghost text, and live-context (rprompt/toolbar) helpers for the REPL."""

from __future__ import annotations

import json
import typing as t
from pathlib import Path

from gracy.explore._parser import METHODS, SHOW_TARGETS, USAGE, Command, ParseError, parse_command
from gracy.explore._session import _CAPTURE_RE, ExploreSession

HISTORY_FILE: t.Final = Path.home() / ".gracy_history"

# Top-level command words offered by Tab completion (kept in sync with the parser).
# `endpoint` before `ep` so the ghost hint prefers the full, clearer word.
COMMANDS: t.Final = (
    *METHODS,
    "endpoint", "ep", "model", "rename", "drop", "prune", "on", "param", "set", "peek", "retry", "throttle",
    "timeout", "auth", "header", "base", "show", "list", "ls", "undo", "export", "help", "quit", "exit",
)


def _seen_paths(session: ExploreSession) -> list[str]:
    return sorted({step["path"] for step in session.history() if step.get("path")})


def _model_names(session: ExploreSession) -> list[str]:
    names = {ep["response_model"] for ep in session.endpoints().values() if ep.get("response_model")}
    return sorted(n for n in names if n)


def candidates_for(session: ExploreSession, leading: str, text: str) -> list[str]:
    """Context-aware completion candidates for the current word.

    ``leading`` is the line up to (not including) the word being typed; ``text``
    is that word. Shared by the readline completer and the prompt_toolkit
    completer + autosuggest so all three stay consistent.
    """
    parts = leading.split()

    if text.startswith("{{"):  # a capture ref: complete {{name}} from stored captures
        inner = text[2:].lstrip()
        return ["{{" + name + "}}" for name in session.captures if name.startswith(inner)]

    if not parts:  # first word -> command names
        return [c + " " for c in COMMANDS if c.startswith(text)]

    cmd = parts[0].lower()
    if cmd in METHODS:  # paths already seen this session
        return [p for p in _seen_paths(session) if p.startswith(text)]
    if cmd == "show":
        if len(parts) == 1:
            return [t_ + " " for t_ in SHOW_TARGETS if t_.startswith(text)]
        if len(parts) == 2 and parts[1] == "model":
            return [n for n in _model_names(session) if n.startswith(text)]
    if cmd in ("endpoint", "ep") and len(parts) == 1:  # a NEW name, or an existing one to fold into
        return [n for n in session.endpoints() if n.startswith(text)]
    if cmd == "rename":
        if len(parts) == 1:
            return [w + " " for w in ("endpoint", "model") if w.startswith(text)]
        if len(parts) == 2 and parts[1] == "endpoint":  # <old> position
            return [n for n in session.endpoints() if n.startswith(text)]
        if len(parts) == 2 and parts[1] == "model":
            return [n for n in _model_names(session) if n.startswith(text)]
    if cmd == "drop":
        if len(parts) == 1:
            return [w + " " for w in ("endpoint", "step") if w.startswith(text)]
        if len(parts) == 2 and parts[1] == "endpoint":
            return [n for n in session.endpoints() if n.startswith(text)]
    if cmd == "on" and len(parts) == 2:  # the action position
        return [a for a in ("none", "raise:") if a.startswith(text)]
    if cmd == "auth" and len(parts) == 1:
        return [s + " " for s in ("bearer", "basic") if s.startswith(text)]
    if cmd == "param" and len(parts) == 2:
        return ["as "] if "as".startswith(text) else []
    if cmd in ("export", "save"):
        import glob

        files = [p for p in glob.glob(text + "*") if p.endswith(".py") or Path(p).is_dir()]
        return files + (["--tests"] if "--tests".startswith(text) else [])
    return []


class _Completer:
    """readline (fallback) Tab completion adapter over ``candidates_for``."""

    def __init__(self, session: ExploreSession) -> None:
        self.session = session
        self._matches: list[str] = []

    def complete(self, text: str, state: int) -> str | None:
        if state == 0:
            try:
                self._matches = self._candidates(text)
            except Exception:  # noqa: BLE001 - completion must never break the prompt
                self._matches = []
        return self._matches[state] if state < len(self._matches) else None

    def _candidates(self, text: str) -> list[str]:
        import readline

        buffer = readline.get_line_buffer()
        return candidates_for(self.session, buffer[: readline.get_begidx()], text)


def _setup_readline(session: ExploreSession) -> None:
    try:
        import atexit
        import readline

        if HISTORY_FILE.exists():
            readline.read_history_file(str(HISTORY_FILE))
        readline.set_history_length(1000)
        atexit.register(lambda: _write_history(readline))

        completer = _Completer(session)
        readline.set_completer(completer.complete)
        readline.set_completer_delims(" ")  # only spaces split words, so "/path" completes whole
        # libedit (macOS default) vs GNU readline bind syntax differ
        if "libedit" in (getattr(readline, "__doc__", "") or ""):
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
    except Exception:  # noqa: BLE001 - readline is best-effort (absent on some builds)
        pass


def _write_history(readline: t.Any) -> None:
    try:
        readline.write_history_file(str(HISTORY_FILE))
    except OSError:
        pass


# --------------------------------------------------------------------------- ghost text (prompt_toolkit)


def suggest_suffix(session: ExploreSession, text_before: str, history: t.Sequence[str] = ()) -> str:
    """The inline 'ghost text' to show after the cursor: the completion of the
    current word (e.g. 'g' -> 'et'), falling back to the most recent matching
    history line. Returns '' when there is nothing to suggest. Pure/testable."""
    if not text_before or text_before.endswith(" "):
        pass  # mid-space: only history can suggest a full-line continuation
    else:
        leading, _, word = text_before.rpartition(" ")
        leading = leading + " " if leading else ""
        for cand in candidates_for(session, leading, word):
            cand = cand.rstrip()
            if cand.startswith(word) and len(cand) > len(word):
                return cand[len(word):]
    for past in reversed(history):  # fish-style: newest matching history line
        if past.startswith(text_before) and len(past) > len(text_before):
            return past[len(text_before):]
    return ""


# --------------------------------------------------------------------------- live context (rprompt + toolbar)

FormattedText = t.List[t.Tuple[str, str]]


def active_endpoint(session: ExploreSession) -> str | None:
    """The endpoint that implicit commands (model/on/param) will affect: the
    endpoint of the MOST RECENT request (not skipping back to older named ones,
    which would silently edit something off-screen). None when the last request
    is unnamed or there are no requests yet."""
    steps = session.history()
    if not steps:
        return None
    return t.cast("str | None", steps[-1].get("matched_endpoint"))


def _last_request(session: ExploreSession) -> dict[str, t.Any] | None:
    steps = session.history()
    return steps[-1] if steps else None


def rprompt_text(session: ExploreSession) -> FormattedText:
    """Right-aligned context on the input line: what implicit commands act on."""
    last = _last_request(session)
    if last is None:
        base = session.base_url
        return [("class:rprompt", f"[{base}]" if base else "[no base_url]")]
    ep = last.get("matched_endpoint")
    if ep:
        steps = sum(1 for s in session.history() if s.get("matched_endpoint") == ep)
        return [
            ("class:rprompt", "active "),
            ("class:rprompt.ep", ep),
            ("class:rprompt", f" · {steps} step{'s' if steps != 1 else ''}"),
        ]
    # last request is unnamed: implicit commands have no target, show it plainly
    return [
        ("class:rprompt", f"{last.get('method')} {last.get('path')} · "),
        ("class:rprompt.warn", "unnamed"),
    ]


def _seg(cls: str, text: str) -> tuple[str, str]:
    return (f"class:{cls}", text)


def _capture_refs(cmd: Command) -> list[str]:
    """Capture names referenced as {{name}} in a request's path / query / headers / body."""
    parts: list[str] = [cmd.path or ""]
    parts.extend(str(v) for v in cmd.query.values())
    parts.extend(str(v) for v in cmd.headers.values())
    if cmd.body:
        parts.append(cmd.body)
    if cmd.body_json is not None:
        parts.append(json.dumps(cmd.body_json))
    names: list[str] = []
    for part in parts:
        for match in _CAPTURE_RE.finditer(part):
            if match.group(1) not in names:
                names.append(match.group(1))
    return names


def _action_desc(action: str) -> str:
    if action == "none":
        return "returns None"
    if action.startswith("raise:"):
        return f"raises {action[len('raise:'):]}"
    return f"returns {action}"


_UNRESOLVED: t.Final = object()


def _short_repr(value: t.Any, limit: int = 60) -> str:
    r = repr(value)
    return r if len(r) <= limit else r[: limit - 3] + "..."


def _resolve_preview(session: ExploreSession, path: str) -> t.Any:
    """A short repr of what <path> resolves to in the last response, or the
    _UNRESOLVED sentinel when it can't be read. Used to preview set/peek live."""
    if not path:
        return ""
    try:
        return _short_repr(session.peek(path))
    except Exception:  # noqa: BLE001 - the toolbar must never break
        return _UNRESOLVED


def describe_impact(session: ExploreSession, line: str) -> FormattedText:
    """Live 'what will this command do' preview for the bottom toolbar. Pure:
    inspects session state, never mutates. Assembled as styled segments."""
    line = line.strip()
    if not line:
        return [_seg("tb.muted", "type a command · Tab lists · → accepts the grey hint · help")]
    try:
        cmd = parse_command(line)
    except ParseError:
        head = line.split()[0].lower()
        hint = USAGE.get({"ls": "list", "ep": "endpoint", "save": "export"}.get(head, head))
        return [_seg("tb.muted", hint or "keep typing…")]

    ep = active_endpoint(session)
    arrow = _seg("tb.muted", " → ")

    def _no_target() -> FormattedText:
        last = _last_request(session)
        if last is None:
            return [_seg("tb.warn", "no request yet: run one first")]
        return [
            _seg("tb.warn", f"{last.get('method')} {last.get('path')} isn't an endpoint yet: "),
            _seg("tb.verb", "run "), _seg("tb.value", "endpoint <name>"), _seg("tb.verb", " first"),
        ]

    if cmd.kind == "request":
        segs = [_seg("tb.verb", "send "), _seg("tb.value", f"{cmd.method} {cmd.path}")]
        match = session._match_endpoint(cmd.method or "", cmd.path or "")  # noqa: SLF001
        if match:
            segs += [_seg("tb.muted", " · matches "), _seg("tb.target", match)]
        captures = session.captures
        for name in _capture_refs(cmd):
            ref = "{{" + name + "}}"
            if name in captures:
                segs += [_seg("tb.muted", f" · {ref}="), _seg("tb.value", str(captures[name]))]
            else:
                segs += [_seg("tb.warn", f" · {ref} not set")]
        return segs
    if cmd.kind in ("set", "peek"):
        verb = "captures" if cmd.kind == "set" else "shows"
        resolved = _resolve_preview(session, cmd.path or "")
        if resolved is _UNRESOLVED:
            return [_seg("tb.warn", f"{cmd.path} not found in the last response")]
        return [
            _seg("tb.verb", f"{verb} "), _seg("tb.muted", cmd.path or ""),
            _seg("tb.muted", " = "), _seg("tb.value", resolved),
        ]
    if cmd.kind == "endpoint":
        last = _last_request(session)
        if cmd.name in session.endpoints():
            return [_seg("tb.verb", "folds the last request into "), _seg("tb.target", cmd.name or "")]
        if last is None:
            return [_seg("tb.warn", "run a request first (nothing to name)")]
        return [
            _seg("tb.verb", "creates endpoint "), _seg("tb.target", cmd.name or ""),
            _seg("tb.muted", f" from {last.get('method')} {last.get('path')}"),
        ]
    if cmd.kind == "model":
        if ep is None:
            return _no_target()
        which = "request-body" if (cmd.name or "").endswith("!request") else "response"
        plain = (cmd.name or "").removesuffix("!request")
        return [
            _seg("tb.verb", f"names the {which} model of "), _seg("tb.target", ep),
            arrow, _seg("tb.value", plain),
        ]
    if cmd.kind == "on":
        if ep is None:
            return _no_target()
        return [
            _seg("tb.target", ep), _seg("tb.verb", f": status {cmd.status} "),
            arrow, _seg("tb.value", _action_desc(cmd.action or "")),
        ]
    if cmd.kind == "param":
        if ep is None:
            return _no_target()
        return [
            _seg("tb.target", ep), _seg("tb.verb", f": rename param {cmd.index} "),
            arrow, _seg("tb.value", "{" + (cmd.name or "") + "}"),
        ]
    if cmd.kind == "rename":
        exists = (cmd.name in session.endpoints()) if cmd.target == "endpoint" else True
        segs = [
            _seg("tb.verb", f"renames {cmd.target} "), _seg("tb.target", cmd.name or ""),
            arrow, _seg("tb.value", cmd.value or ""),
        ]
        if cmd.target == "endpoint" and not exists:
            return [_seg("tb.warn", f"no endpoint named '{cmd.name}'")]
        return segs
    if cmd.kind == "drop":
        if cmd.target == "endpoint":
            from gracy.explore._session import template_matches

            eps = session.endpoints()
            ep = eps.get(cmd.name or "")
            if ep is None:
                return [_seg("tb.warn", f"no endpoint named '{cmd.name}'")]
            absorb: dict[str, int] = {}
            orphan = 0
            for s in (h for h in session.history() if h["matched_endpoint"] == cmd.name):
                home = next(
                    (o for o, oe in eps.items()
                     if o != cmd.name and oe["method"] == s["method"]
                     and template_matches(oe["template"], s["path"])),
                    None,
                )
                if home:
                    absorb[home] = absorb.get(home, 0) + 1
                else:
                    orphan += 1
            segs = [
                _seg("tb.verb", "removes endpoint "), _seg("tb.target", cmd.name or ""),
                _seg("tb.muted", f" ({ep['template']})"),
            ]
            if absorb:
                moved = ", ".join(f"{n} into {o}" for o, n in absorb.items())
                segs.append(_seg("tb.muted", f" · folds {moved}"))
            if orphan:
                segs.append(_seg("tb.muted", f" · frees {orphan} step{'' if orphan == 1 else 's'} (kept in history)"))
            if not absorb and not orphan:
                segs.append(_seg("tb.muted", " · no steps"))
            return segs
        step = next((s for s in session.history() if s["step_id"] == cmd.index), None)
        if step is None:
            return [_seg("tb.warn", f"no step with id {cmd.index}")]
        where = step["matched_endpoint"] or "unnamed"
        return [
            _seg("tb.verb", "removes step "), _seg("tb.value", str(cmd.index)),
            _seg("tb.muted", " · "), _seg("tb.target", f"{step['method']} {step['path']}"),
            _seg("tb.muted", f" · {where}"),
        ]
    if cmd.kind == "prune":
        n = sum(1 for s in session.history() if s["matched_endpoint"] is None)
        if not n:
            return [_seg("tb.muted", "no unnamed steps to prune")]
        return [
            _seg("tb.verb", "removes "), _seg("tb.value", f"{n} unnamed step{'' if n == 1 else 's'}"),
            _seg("tb.muted", " (folded-in requests stay)"),
        ]
    if cmd.kind in ("retry", "throttle", "timeout", "auth", "header", "base"):
        detail = cmd.spec or cmd.value or (f"{cmd.name}={cmd.value}" if cmd.name else "")
        label = {"base": "base_url"}.get(cmd.kind, cmd.kind)
        return [_seg("tb.verb", f"sets {label} "), arrow, _seg("tb.value", str(detail))]
    if cmd.kind == "export":
        n_ep = len(session.endpoints())
        extra = " + tests + cassette" if cmd.tests else ""
        return [
            _seg("tb.verb", "writes "), _seg("tb.value", cmd.path or "the client"),
            _seg("tb.verb", extra), _seg("tb.muted", f" · {n_ep} endpoint{'s' if n_ep != 1 else ''}"),
        ]
    if cmd.kind == "show":
        return [_seg("tb.verb", f"shows {cmd.target}")]
    descriptions = {"undo": "undoes the last change", "help": "lists commands", "quit": "leaves the explorer"}
    return [_seg("tb.muted", descriptions.get(cmd.kind, cmd.kind))]
