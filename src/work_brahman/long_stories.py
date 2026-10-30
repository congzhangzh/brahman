"""Long stories: one agent session, followed and talked to from the owner's chats, one thread
per session (specs/long-story.md, families/long_story/FAMILY.md).

The session's own transcript is the single source of truth; the thread is a mirror of it.
Whoever drives the session resumes that same session id, one at a time:

- **The terminal holds it** (a `claude` started with BUDDY_LONG set, see scripts/buddy-long):
  its hooks report each turn to the thread. What the owner writes in the thread is typed into
  the session's screen window when it is idle, or handed to it by its Stop hook when it is busy.
  The buddy never runs a second process on it.
- **Nobody holds it** (the terminal closed it, or it was started from the phone): what the owner
  writes runs as a headless turn of the same session (`claude -p --resume`), with the buddy's
  own tools (read-only). `claude --resume <id>` in a terminal takes it back.

Codex (`buddy-long codex`) differs in two ways. Its hooks run in Codex's shared daemon, not
under the terminal, so the wrapper registers the session before it starts and SessionStart
claims it by folder. And what the owner writes goes in with `codex queue`, which the daemon
hands to the terminal that holds the session or runs itself: Codex keeps one writer per
session on its own. A permission it asks for shows on the card with Allow / Deny.

Each story is a small file both the hooks (`buddy long hook`) and `buddy serve` read:

    $XDG_STATE_HOME/work_brahman/long_stories/<number>.json
"""

import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from . import portable
from .channel import Channel
from .text import app, t

TURN_CHARS = 3500  # of a turn's last message in the thread; the transcript has it all
EVENTS_KEPT = 60   # a story's own log, for the workbench; the transcript has the rest


def _dir(state: Path) -> Path:
    d = state / "long_stories"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def _locked(state: Path, name: str):
    with portable.locked(_dir(state) / f".{name}.lock"):
        yield


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def load(state: Path, number: int) -> dict | None:
    p = _dir(state) / f"{number}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _save(state: Path, story: dict) -> None:
    p = _dir(state) / f"{story['id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(story, ensure_ascii=False, indent=1))
    tmp.replace(p)


def all_stories(state: Path) -> list[dict]:
    return sorted((json.loads(p.read_text()) for p in _dir(state).glob("*.json") if p.stem.isdigit()),
                  key=lambda s: s["id"])


def by_session(state: Path, session_id: str) -> dict | None:
    return next((s for s in all_stories(state) if s["session_id"] == session_id), None)


def find(state: Path, chat: str, handle: str) -> dict | None:
    """The story whose thread in chat is handle."""
    return next((s for s in all_stories(state) if s["threads"].get(chat) == handle), None)


def label(story: dict) -> str:
    return t("long.label", n=story["id"], title=story["title"])


def held(story: dict) -> bool:
    """A terminal holds the session: its process is still alive."""
    pid = story.get("pid")
    if story["status"] != "live" or not pid:
        return False
    return portable.alive(pid)


ENGINES = ("claude-code", "codex")
ENGINE_NAMES = {"claude-code": "Claude", "codex": "Codex"}


def _create(state: Path, channels: list[Channel], chats: list[str], *, title: str,
            session_id: str, cwd: str, transcript: str = "", engine: str = "claude-code") -> dict:
    with _locked(state, "next"):
        n = max([s["id"] for s in all_stories(state)] or [0]) + 1
        story = {"id": n, "title": title, "engine": engine, "session_id": session_id,
                 "cwd": cwd, "transcript": transcript, "started": _now(), "status": "away",
                 "state": "idle", "pid": None, "sty": "", "window": "", "prompt": "",
                 "threads": {}, "inbox": [], "turns": 0}
        _save(state, story)
    for chat in chats:  # a thread in every app that can hold one apart (topics.py's form)
        ch = next((c for c in channels if c.owns(chat)), None)
        if ch and hasattr(ch, "open_topic"):
            handle = ch.open_topic(chat, {"id": f"L{n}", "name": label(story)})
            if handle:
                story["threads"][chat] = handle
    _change(state, n, lambda s: s["threads"].update(story["threads"]))
    _mark(state, session_id)
    return load(state, n)


def _mark(state: Path, session_id: str) -> None:
    """A file per session that has a story: the hook's cheap test (scripts/buddy-long)."""
    if session_id:
        (_dir(state) / "sessions").mkdir(exist_ok=True)
        (_dir(state) / "sessions" / session_id).touch()


def register(state: Path, *, pid: int, cwd: str, title: str, sty: str = "", window: str = "") -> None:
    """`buddy-long codex`: this terminal's Codex is a long story; its SessionStart claims it."""
    d = _dir(state) / "pending"
    d.mkdir(exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps({"pid": pid, "cwd": os.path.realpath(cwd), "title": title,
                                               "sty": sty, "window": window, "at": time.time()}))


def claim(state: Path, cwd: str) -> dict | None:
    """The newest registration of a live terminal in cwd, taken (or None). Dead ones go."""
    d, mine = _dir(state) / "pending", []
    for p in sorted(d.glob("*.json")) if d.exists() else []:
        try:
            r = json.loads(p.read_text())
            if not portable.alive(r["pid"]):
                raise OSError("gone")
        except (OSError, ValueError, KeyError):
            p.unlink(missing_ok=True)
            continue
        if r["cwd"] == os.path.realpath(cwd):
            mine.append((r["at"], p, r))
    if not mine:
        return None
    _, p, r = max(mine, key=lambda x: x[0])
    p.unlink(missing_ok=True)
    return r


def attach(state: Path, channels: list[Channel], chats: list[str], *, session_id: str, cwd: str,
           transcript: str, title: str, pid: int | None, sty: str, window: str,
           source: str = "startup", engine: str = "claude-code") -> dict:
    """SessionStart in a terminal: the session's story (a new one for a new session) is held
    by it now. After /clear the session id changes but the window is the same: one story."""
    story = by_session(state, session_id)
    if story is None and source in ("clear", "compact") and sty:
        story = next((s for s in all_stories(state) if s["status"] == "live"
                      and (s["sty"], s["window"]) == (sty, window)), None)
    if story is None:
        story = _create(state, channels, chats, title=title or os.path.basename(cwd), session_id=session_id,
                        cwd=cwd, transcript=transcript, engine=engine)

    def hold(s):
        s.update(session_id=session_id, transcript=transcript, cwd=cwd, status="live", state="idle",
                 pid=pid, sty=sty, window=window)
        _event(s, "terminal", f"{sty} {window}".strip() or "-")
    story = _change(state, story["id"], hold)
    _mark(state, session_id)
    where = t("long.window", sty=sty.split(".", 1)[-1], window=window) if sty else t("long.no_window")
    _post(channels, story, t("long.joined", where=where, cwd=cwd), title=t("long.title.terminal"))
    return story


def busy(state: Path, session_id: str, prompt: str) -> None:
    """UserPromptSubmit: a turn started (typed in the terminal, or from the thread)."""
    if s := by_session(state, session_id):
        _change(state, s["id"], lambda s: s.update(state="busy", prompt=prompt))


def turn(state: Path, channels: list[Channel], session_id: str, text: str) -> str:
    """Stop: post the turn's last message, and return what the owner wrote meanwhile (for the
    Stop hook to hand over, so the session carries on), or ""."""
    story = by_session(state, session_id)
    if story is None:
        return ""
    waiting = []

    def end(s):
        s["turns"] += 1
        s["state"] = "idle"
        _event(s, "turn", _cut(text, 120))
        if s.get("engine") == "codex":
            return  # codex queue delivered it already
        waiting.extend(m for m in s["inbox"] if not m["done"])
        for m in waiting:
            m["done"], m["how"] = True, "stop-hook"
        if waiting:
            s["state"] = "busy"
    story = _change(state, story["id"], end)
    _post(channels, story, _cut(text) or t("long.no_text"), title=t("long.title.turn", n=story["turns"]),
          subtitle=_cut(story.get("prompt", ""), 80))
    return "\n\n".join(t("long.handed", where=app(m["where"]), text=m["text"]) for m in waiting)


def waiting(state: Path, channels: list[Channel], session_id: str, message: str) -> None:
    """Notification: the session waits for a person (a permission). Its idle reminder is not
    posted: the turn's own message already said it ended."""
    story = by_session(state, session_id)
    if story is None or "waiting for your input" in message:
        return
    story = _change(state, story["id"], lambda s: s.update(state="waiting") or _event(s, "wait", message))
    shown = _dialog(_screen(story["sty"], story["window"])) if story["sty"] else ""
    _post(channels, story, t("long.waiting.screen", message=message, screen=shown) if shown
          else t("long.waiting", message=message), title=t("long.title.waiting"))


def detach(state: Path, channels: list[Channel], session_id: str, reason: str = "") -> None:
    """SessionEnd: the terminal let go. The story stays: the thread can carry the session on."""
    story = by_session(state, session_id)
    if story is None:
        return
    if story.get("engine") == "codex" and held(story):
        return  # Codex ends a session after 30 idle minutes, or when a codex exec ends: the terminal still holds it
    story = _change(state, story["id"], lambda s: s.update(status="away", state="idle", pid=None)
                    or _event(s, "away", reason or "-"))
    if story.get("engine") == "codex":
        return  # nothing to say: what the owner writes still reaches it (codex queue)
    _post(channels, story, t("long.left", cwd=story["cwd"], id=story["session_id"]),
          title=t("long.title.away"))


def say(state: Path, channels: list[Channel], number: int, text: str, *, where: str, who: str = "",
        engine=None) -> str:
    """What the owner wrote in the thread, for the session. Returns what to tell him."""
    story = load(state, number)
    if story is None:
        return t("long.no_such", n=number)
    if story.get("engine") == "codex":
        return _queue(state, story, text, where=where, who=who)
    if held(story):
        how = []
        # A hook can be missed (an interrupted turn fires no Stop): the screen says what the
        # session is doing now, so the stored state never wedges the story.
        seen = screen_state(_screen(story["sty"], story["window"])) if story["sty"] else ""

        def take(s):
            m = {"at": _now(), "where": where, "who": who, "text": text, "done": False}
            s["state"] = seen or s["state"]
            key = text.strip()
            if s["state"] == "waiting" and s["sty"] and key.isdigit() and len(key) == 1 \
                    and _type(s["sty"], s["window"], key, enter=False):  # a choice in its dialog
                m["done"], m["how"] = True, "pressed"
                s["state"] = "busy"
            elif s["state"] == "idle" and s["sty"] and _type(s["sty"], s["window"], text):
                m["done"], m["how"] = True, "typed"
                s["state"], s["prompt"] = "busy", text
            s["inbox"].append(m)
            how.append(m.get("how", ""))
            _event(s, "say", _cut(text, 120), how=m.get("how", "queued"), where=where)
        _change(state, number, take)
        return {"typed": t("long.typed"), "pressed": t("long.pressed", key=text.strip())}.get(
            how[0], t("long.waits") if seen == "waiting" else t("long.queued"))
    if engine is None:
        return t("long.no_engine")
    if not os.path.isdir(story["cwd"]):  # resume finds a session through the folder it ran in
        return t("long.no_cwd", cwd=story["cwd"])
    # Nobody holds it: a headless turn of the same session, with the buddy's own tools.
    _change(state, number, lambda s: s.update(state="busy", prompt=text))
    try:
        r = engine.run(text, cwd=story["cwd"], session_id=story["session_id"])
    except Exception as e:  # never leave the story busy: the next message must run again
        _change(state, number, lambda s: s.update(state="idle"))
        return t("long.failed", e=e)

    def done(s):
        s["turns"] += 1
        s["state"] = "idle"
        if r.session_id:
            s["session_id"] = r.session_id
        _event(s, "say", _cut(text, 120), how="headless", where=where)
        _event(s, "turn", _cut(r.text, 120), ok=r.ok, engine="claude-code", cost=r.cost)
    story = _change(state, number, done)
    note = t("denied", tools=", ".join(r.denied)) if r.denied else ""
    _post(channels, story, _cut(r.text) or t("long.no_text"), title=t("long.title.headless", n=story["turns"]),
          subtitle=_cut(text, 80), failed=not r.ok, note=note)
    return ""  # the answer is in the thread already


def new(state: Path, channels: list[Channel], chats: list[str], text: str, *, cwd: str,
        where: str, engine, kind: str = "claude-code") -> dict:
    """/long new: a session started from the phone, its first turn headless; a terminal can
    take it over (`claude --resume <id>`, `codex resume <id>`)."""
    story = _create(state, channels, chats, title=text[:40], session_id="", cwd=cwd, engine=kind)
    _post(channels, story, t("long.made", where=app(where), cwd=cwd), title=t("long.title.new"))
    r = engine.run(text, cwd=cwd)

    def done(s):
        s.update(session_id=r.session_id or "", turns=1)
        _event(s, "new", _cut(text, 120), where=where)
        _event(s, "turn", _cut(r.text, 120), ok=r.ok, engine=kind, cost=r.cost)
    story = _change(state, story["id"], done)
    _mark(state, r.session_id or "")
    note = t("denied", tools=", ".join(r.denied)) if r.denied else ""
    take = t("long.take.codex" if kind == "codex" else "long.take", cwd=cwd, id=r.session_id)
    _post(channels, story, (_cut(r.text) or t("long.no_text")) + "\n\n" + take,
          title=t("long.title.headless", n=1), subtitle=_cut(text, 80), failed=not r.ok, note=note)
    return story


def _queue(state: Path, story: dict, text: str, *, where: str, who: str) -> str:
    """Codex: the message goes to the session through `codex queue`; its turn's answer comes
    back through the Stop hook, whoever runs it (the terminal, or Codex's daemon)."""
    try:
        p = subprocess.run(["codex", "queue", "--thread", story["session_id"], "--message", text],
                           capture_output=True, text=True, timeout=60)
        ok, why = p.returncode == 0, (p.stderr or p.stdout).strip()[-300:]
    except (OSError, subprocess.TimeoutExpired) as e:
        ok, why = False, str(e)
    _change(state, story["id"], lambda s: s["inbox"].append(
        {"at": _now(), "where": where, "who": who, "text": text, "done": ok, "how": "codex queue"})
        or _event(s, "say", _cut(text, 120), how="codex queue" if ok else "failed", where=where))
    return t("long.sent_codex") if ok else t("long.failed", e=why)


def permission(state: Path, channels: list[Channel], chats: list[str], session_id: str, *, tool: str,
               what: str, wait: float) -> str:
    """PermissionRequest (Codex): a card with Allow / Deny in the owner's chats, and the
    first answer within `wait` seconds: "allow", "deny", or "" (none: the terminal asks)."""
    from . import asks
    story = by_session(state, session_id)
    if story is None:
        return ""
    req = f"{story['id']}-{time.time_ns()}"
    _change(state, story["id"], lambda s: s.update(state="waiting") or _event(s, "wait", f"{tool}: {_cut(what, 100)}"))
    asks.create(state, channels, chats, t("long.perm", label=label(story), tool=tool, what=_cut(what, 1500)),
                title=t("long.title.waiting"), options=[["allow", t("long.allow")], ["deny", t("long.deny")]],
                then={"allow": ["long", "decide", story["id"], req, "allow"],
                      "deny": ["long", "decide", story["id"], req, "deny"]})
    end = time.monotonic() + wait
    while time.monotonic() < end:
        said = (load(state, story["id"]) or {}).get("decisions", {}).get(req)
        if said:
            return said
        time.sleep(1)
    return ""


def decide(state: Path, number: int, req: str, decision: str) -> None:
    """An Allow / Deny press: the waiting PermissionRequest hook reads it from the story."""
    _change(state, number, lambda s: s.setdefault("decisions", {}).update({req: decision})
            or _event(s, "decide", decision))


def folders(state: Path, home: Path | None = None, n: int = 6) -> list[str]:
    """Folders to offer when a long story starts from the phone: those of earlier stories,
    then the projects touched last (~/projects/*, newest first). Only ones that exist."""
    home = home or Path.home()
    seen = [s["cwd"] for s in reversed(all_stories(state)) if s.get("cwd")]
    projects = sorted((p for p in (home / "projects").glob("*") if p.is_dir() and not p.name.startswith(".")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in seen + [str(p) for p in projects]:
        if f not in out and os.path.isdir(f):
            out.append(f)
    return out[:n]


def board(state: Path) -> str:
    """Every long story, one line each, for /long and `buddy long list`."""
    ss = all_stories(state)
    if not ss:
        return t("long.none")
    return "\n".join(t("long.board_line", label=label(s), who=t("long.held") if held(s) else t("long.away"),
                       turns=s["turns"], cwd=s["cwd"]) + f" · {s.get('engine', 'claude-code')}" for s in ss)


def last_message(transcript: str) -> str:
    """What the session said at the end of its last turn, from its transcript (JSON lines,
    newest last, one content block per line): the run of text after its last tool call or
    prompt. What it said between tool calls stays in the transcript."""
    try:
        lines = Path(transcript).read_text(errors="replace").splitlines()
    except OSError:
        return ""
    said = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") not in ("assistant", "user"):
            continue  # summaries, file snapshots, system lines
        content = (e.get("message") or {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
        if e["type"] == "user" or any(b.get("type") == "tool_use" for b in blocks if isinstance(b, dict)):
            if said:
                break
            continue
        said += [b["text"] for b in reversed(blocks) if isinstance(b, dict) and b.get("type") == "text"
                 and b.get("text", "").strip()]
    return "\n\n".join(reversed(said))


def _type(sty: str, window: str, text: str, enter: bool = True) -> bool:
    """Type text (and Enter) into a screen window, as if at its keyboard. One line: a newline
    would send it early. screen reads \\ ^ and $ in `stuff`, so they are escaped. A dialog's
    choice is one key, without Enter: the menu takes it at once."""
    line = " ".join(text.split())
    for ch in ("\\", "^", "$"):
        line = line.replace(ch, "\\" + ch)
    stuff = lambda keys: subprocess.run(["screen", "-S", sty, "-p", window, "-X", "stuff", keys],
                                        capture_output=True, text=True, timeout=10).returncode == 0
    try:
        if not stuff(line):
            return False
        if enter:  # on its own: Claude Code reads a burst of keys as a paste, where Enter is text
            time.sleep(PASTE_GAP)
            return stuff("\r")
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


PASTE_GAP = 0.4  # seconds between the text and its Enter


def _screen(sty: str, window: str) -> list[str]:
    """What the window shows now (screen's hardcopy), its blank lines left out."""
    with tempfile.NamedTemporaryFile(suffix=".txt") as f:
        try:
            subprocess.run(["screen", "-S", sty, "-p", window, "-X", "hardcopy", f.name],
                           capture_output=True, timeout=10)
            return [x.rstrip() for x in Path(f.name).read_text(errors="replace").splitlines() if x.strip()]
        except (OSError, subprocess.TimeoutExpired):
            return []


SPINNER = re.compile(r"^\S\s+\w[\w ]*…")  # "✽ Sprouting… (2s)": a turn is running


def screen_state(lines: list[str]) -> str:
    """waiting (a dialog asks), busy (a turn runs), idle, or "" when nothing was read.
    Claude Code's screen: the input box sits at the bottom; above it a spinner while working,
    "✻ Cogitated for 10s" once done, or a dialog in place of the box."""
    if not lines:
        return ""
    tail = lines[-14:]
    if any("Do you want to proceed?" in x or "Esc to cancel" in x for x in tail):
        return "waiting"
    if any(SPINNER.match(x.strip()) for x in tail):
        return "busy"
    return "idle"


def _dialog(lines: list[str]) -> str:
    """The dialog on screen, from what it asks to its choices, for the thread."""
    ask = next((i for i in range(len(lines) - 1, -1, -1) if "Do you want" in lines[i]), None)
    if ask is None:
        return ""
    start = max(0, ask - 16)
    for i in range(ask - 1, start - 1, -1):  # from the dialog's top rule, if it is near
        if set(lines[i].strip()) <= set("─━"):  # the solid rule; ╌ boxes the command
            start = i + 1
            break
    return "\n".join(x for x in lines[start:ask + 7] if not set(x.strip()) <= set("─━╌│"))


def _event(story: dict, step: str, text: str, **kw) -> None:
    """One line of the story's own log: what the workbench shows as the steps of its run."""
    story.setdefault("events", []).append({"step": step, "at": _now(), "text": text, **kw})
    del story["events"][:-EVENTS_KEPT]


def _cut(text: str, limit: int = TURN_CHARS) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " …"


def _change(state: Path, number: int, fn) -> dict | None:
    with _locked(state, str(number)):
        story = load(state, number)
        if story is None:
            return None
        fn(story)
        _save(state, story)
    return story


def _post(channels: list[Channel], story: dict, markdown: str, *, title: str, **kw) -> None:
    for chat, handle in story["threads"].items():
        ch = next((c for c in channels if c.owns(chat)), None)
        if ch:
            ch.post(chat, markdown, title=f"{label(story)} · {title}", thread=handle, **kw)
