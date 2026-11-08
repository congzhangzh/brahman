"""Long stories: one agent session, followed and talked to from the owner's chats, one thread
per session (specs/long-story.md, families/long_story/FAMILY.md).

The session's own transcript is the single source of truth; the thread is a mirror of it.
Whoever drives the session resumes that same session id, one at a time:

- **The terminal holds it** (a `claude` started with BUDDY_LONG set, see scripts/buddy-long):
  its hooks report each turn to the thread. What the owner writes in the thread is typed into
  the session's window (pys, or GNU screen) when it is idle, or handed to it by its Stop hook
  when it is busy. The buddy is only its puppet: it never runs a second process on it, and the
  session lives or ends on its own.
- **The buddy holds it** (long_live.py, specs/long-story-protocol.md): /newlong, ▶ Resume and
  /takeback run it as one live process over the stream-json protocol, a unit under the
  umbrella; a message goes in as one JSON line, a permission comes out as ✅ Allow / ⛔ Deny.
  /handover and `buddy-long take <n>` give it to a terminal: one writer at a time.
- **Nobody holds it**: what the owner writes waits, and a card asks first whether to resume it,
  since a resume loads the whole conversation again (tokens).

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

from . import portable, procs
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


def pick(engines: dict, kind: str, mode: str = "") -> object | None:
    """The engine a story of this kind runs on in this mode: "open" is its own engine
    (engine.long_engines), missing when [gate] open = false; anything else is the sandbox."""
    return engines.get(f"{kind}/open") if mode == "open" else engines.get(kind)


def held(story: dict) -> bool:
    """A terminal holds the session: its process is still alive."""
    pid = story.get("pid")
    if story["status"] != "live" or not pid:
        return False
    return portable.alive(pid)


ENGINES = ("claude-code", "codex")
ENGINE_NAMES = {"claude-code": "Claude", "codex": "Codex"}


def _create(state: Path, channels: list[Channel], chats: list[str], *, title: str,
            session_id: str, cwd: str, transcript: str = "", engine: str = "claude-code",
            mode: str = "") -> dict:
    with _locked(state, "next"):
        n = max([s["id"] for s in all_stories(state)] or [0]) + 1
        story = {"id": n, "title": title, "engine": engine, "session_id": session_id,
                 "cwd": cwd, "transcript": transcript, "started": _now(), "status": "away",
                 "state": "idle", "pid": None, "mux": "", "sty": "", "window": "", "prompt": "",
                 "threads": {}, "inbox": [], "turns": 0}
        if mode:  # "sandbox" or "open": the owner's choice at the start, kept for every later turn
            story["mode"] = mode
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
           source: str = "startup", engine: str = "claude-code", mux: str = "") -> dict:
    """SessionStart in a terminal: the session's story (a new one for a new session) is held
    by it now. After /clear the session id changes but the window is the same: one story.
    mux: "pys" (window is $PYS_WINDOW), "screen" (sty and window), or "" (neither)."""
    mux = mux or ("screen" if sty else "")
    story = by_session(state, session_id)
    if story is None and source in ("clear", "compact") and mux:
        story = next((s for s in all_stories(state) if s["status"] == "live"
                      and (s.get("mux") or ("screen" if s["sty"] else ""), s["sty"], s["window"])
                      == (mux, sty, window)), None)
    if story is None:
        story = _create(state, channels, chats, title=title or os.path.basename(cwd), session_id=session_id,
                        cwd=cwd, transcript=transcript, engine=engine)

    # the pys window the buddy opened for it (/newlong, ▶ Resume): it said so already
    known = mux == "pys" and story["status"] == "live" and (story.get("mux"), story["window"]) == (mux, window)

    from . import long_live
    gap = long_live.alive(story)  # the buddy holds it too: it steps back, the terminal has it now
    if gap:
        long_live.handover(state, channels, story["id"])
        _change(state, story["id"], lambda s: s.update(takeback=False))
        _post(channels, story, t("long.live.gap", n=story["id"]), title=t("long.title.terminal"))

    def hold(s):
        s.pop("reserved", None)
        s.pop("takeback", None)
        s.update(session_id=session_id, transcript=transcript, cwd=cwd, status="live", state="idle",
                 pid=pid, mux=mux, sty=sty, window=window)
        _event(s, "terminal", f"{'pys' if mux == 'pys' else sty} {window}".strip() or "-")
    story = _change(state, story["id"], hold)
    _mark(state, session_id)
    where = (t("long.window_pys", window=window) if mux == "pys"
             else t("long.window", sty=sty.split(".", 1)[-1], window=window) if sty else t("long.no_window"))
    if not known:
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
    shown = _dialog(_look(story))
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
    from . import long_live
    h = long_live.holder(story)
    if h == "buddy":  # its live process: one JSON line, straight in
        return long_live.send(state, channels, number, text, where=where, who=who)
    if h in ("releasing", "reserved"):  # handed over to the owner's terminal: it waits for him
        _change(state, number, lambda s: s["inbox"].append(
            {"at": _now(), "where": where, "who": who, "text": text, "done": False, "how": "waits"})
            or _event(s, "say", _cut(text, 120), how="waits", where=where))
        return t("long.waits_terminal", n=number)
    if held(story):
        how = []
        # A hook can be missed (an interrupted turn fires no Stop): the screen says what the
        # session is doing now, so the stored state never wedges the story.
        seen = screen_state(_look(story))

        def take(s):
            m = {"at": _now(), "where": where, "who": who, "text": text, "done": False}
            s["state"] = seen or s["state"]
            key = text.strip()
            if s["state"] == "waiting" and key.isdigit() and len(key) == 1 \
                    and _keys(s, key, enter=False):  # a choice in its dialog
                m["done"], m["how"] = True, "pressed"
                s["state"] = "busy"
            elif s["state"] == "idle" and _keys(s, text):
                m["done"], m["how"] = True, "typed"
                s["state"], s["prompt"] = "busy", text
            s["inbox"].append(m)
            how.append(m.get("how", ""))
            _event(s, "say", _cut(text, 120), how=m.get("how", "queued"), where=where)
        _change(state, number, take)
        return {"typed": t("long.typed"), "pressed": t("long.pressed", key=text.strip())}.get(
            how[0], t("long.waits") if seen == "waiting" else t("long.queued"))
    if story.get("headless"):  # a headless turn it was resumed for still runs: this is its next one
        return _headless(state, channels, number, text, where=where, who=who, engine=engine)
    # Nobody holds it: the message waits, and the owner says first whether to resume the
    # session, since a resume loads the whole conversation again (tokens). One card at a time.
    asked = []

    def wait(s):
        s["inbox"].append({"at": _now(), "where": where, "who": who, "text": text, "done": False,
                           "how": "asked"})
        _event(s, "say", _cut(text, 120), how="asked", where=where)
        if not s.get("resume_asked"):
            s["resume_asked"] = _now()
            asked.append(True)
    story = _change(state, number, wait)
    if not asked:
        return t("long.kept")
    from . import asks
    asks.create(state, channels, list(story["threads"]), t("long.resume_ask", label=label(story)),
                title=t("long.title.away"),
                options=[["resume", t("long.resume_btn")], ["later", t("long.later_btn")]],
                then={"resume": ["long", "resume", number], "later": ["long", "later", number]})
    return ""


def later(state: Path, number: int) -> None:
    """⏸ Not now: the messages stay; the next one asks again, with them."""
    _change(state, number, lambda s: s.pop("resume_asked", None) or _event(s, "later", "-"))


def resume(state: Path, channels: list[Channel], number: int, *, engine=None, where: str = "") -> str:
    """▶ Resume: what waited goes to the session, which comes back to life as the buddy's live
    session (long_live.start); an engine without one (Codex's) runs one headless turn."""
    story = load(state, number)
    if story is None:
        return t("long.no_such", n=number)
    got = []

    def take(s):
        s.pop("resume_asked", None)
        for m in s.get("inbox", []):
            if m.get("how") == "asked" and not m.get("done"):
                m["done"], m["how"] = True, "resumed"
                got.append(m)
    story = _change(state, number, take)
    if held(story):  # it came back on its own meanwhile (the owner took it in a terminal)
        for m in got:
            say(state, channels, number, m["text"], where=m["where"], who=m.get("who", ""))
        return ""
    text = "\n\n".join(m["text"] for m in got)
    where = got[-1]["where"] if got else where
    if engine is not None and hasattr(engine, "live_cmd"):  # the live session (long_live.py)
        from . import long_live
        return long_live.start(state, channels, number, engine, text, where=where,
                               who=got[-1].get("who", "") if got else "")
    if not os.path.isdir(story["cwd"]):  # resume finds a session through the folder it ran in
        return t("long.no_cwd", cwd=story["cwd"])
    if not text:
        return ""
    return _headless(state, channels, number, text, where=where, who=got[-1].get("who", "") if got else "",
                     engine=engine)


def _headless(state: Path, channels: list[Channel], number: int, text: str, *, where: str, who: str = "",
              engine=None) -> str:
    """One headless turn of the session, with the buddy's own tools, and then what came while
    it ran. Its unit is named after the story (procs.py): one turn of a story at a time, and it
    survives a restart of the buddy, which then delivers it (adopted)."""
    story = load(state, number)
    if engine is None:
        return t("long.no_engine")
    if not os.path.isdir(story["cwd"]):  # resume finds a session through the folder it ran in
        return t("long.no_cwd", cwd=story["cwd"])
    while True:
        _change(state, number, lambda s: s.update(state="busy", prompt=text, headless=True))
        try:
            env = {"BUDDY_SHORT": str(story["short"])} if story.get("short") else {}
            r = engine.run(text, cwd=story["cwd"], session_id=story["session_id"],
                           **({"extra_env": env} if env else {}), **_unit(number, text, where))
        except procs.Busy:  # its last turn still runs: this message is its next one (a
            # different instance of the buddy runs it, so the flag stays until it ends)
            def next_(s):
                s["inbox"].append({"at": _now(), "where": where, "who": who, "text": text,
                                   "done": False, "how": "next"})
                _event(s, "say", _cut(text, 120), how="next", where=where)
            _change(state, number, next_)
            return t("long.queued")
        except Exception as e:  # never leave the story busy: the next message must run again
            _change(state, number, lambda s: s.update(state="idle") or s.pop("headless", None))
            return t("long.failed", e=e)
        _done(state, channels, number, text, where, r)
        text, where = _next(state, number, where)
        if not text:
            _change(state, number, lambda s: s.pop("headless", None))
            return ""  # the answer is in the thread already
        story = load(state, number)


def _unit(number: int, text: str, where: str) -> dict:
    """procs.run's arguments for a long story's headless turn (only when the umbrella is on,
    so an engine without them still works off Linux)."""
    if not procs.umbrella():
        return {}
    return {"kind": "long", "unit": f"buddy-long-{number}",
            "tag": {"story": number, "text": _cut(text, 2000), "where": where}}


def _done(state: Path, channels: list[Channel], number: int, text: str, where: str, r) -> None:
    """A headless turn ended: count it, keep its session id, post its answer in the thread."""
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


def _next(state: Path, number: int, where: str) -> tuple[str, str]:
    """The messages that came while a turn ran (how "next"), as one: the next turn's text."""
    got = []

    def take(s):
        for m in s.get("inbox", []):
            if m.get("how") == "next" and not m.get("done"):
                m["done"], m["how"] = True, "headless"
                got.append(m)
    _change(state, number, take)
    if not got:
        return "", where
    return "\n\n".join(m["text"] for m in got), got[-1].get("where") or where


def adopted(state: Path, channels: list[Channel], meta: dict, p, engines: dict) -> None:
    """A headless turn the last instance of the buddy started and a restart left running
    (procs.adopt): post its answer as if nothing happened, then run what came meanwhile."""
    from .engine import ClaudeCode
    tag = meta.get("tag") or {}
    number = tag.get("story")
    story = load(state, number) if number else None
    if story is None:
        return
    r = ClaudeCode.parse(p, tag.get("session_id") or story["session_id"], bool(tag.get("resumed")))
    _done(state, channels, number, tag.get("text", ""), tag.get("where", ""), r)
    text, where = _next(state, number, tag.get("where", ""))
    if text:
        _headless(state, channels, number, text, where=where,
                  engine=pick(engines, "claude-code", story.get("mode")))
    else:
        _change(state, number, lambda s: s.pop("headless", None))


def new(state: Path, channels: list[Channel], chats: list[str], text: str, *, cwd: str,
        where: str, engine, kind: str = "claude-code", short: str = "", mode: str = "sandbox",
        who: str = "") -> dict:
    """/newlong: a session started from the phone, its first turn headless; a terminal can
    take it over (`claude --resume <id>`, `codex resume <id>`). It may write in its folder, so
    that is never your home folder or /. The gate checks the start first (gate.check_new);
    ValueError says why, and nothing is written.

    short: a short story's step this session works for ("12:test"). The buddy opens the child
    story of that step first, so its card is there even if the session never starts, and the
    session gets its number as BUDDY_SHORT (and in its first prompt); a start that fails ends
    the child as failed, so a dead call never looks alive.

    mode: "sandbox" (the gate's sandbox) or "open" (the owner's own environment and files, the
    buddy's keys still hidden): the owner picks it at the start, and engine must be the one for
    it (pick()); the story keeps it, so its later turns run the same way."""
    from . import gate
    from . import short_stories as shorts
    wrapped = bool(getattr(engine, "wrap", None))
    live = kind == "claude-code" and hasattr(engine, "live_cmd")
    how = ("open" if wrapped else "open-nobwrap") if mode == "open" else ("bwrap" if wrapped else "none")
    gate.check_new(state, text, cwd, short, sandbox=how, who=f"{who}@{where}" if who else where)
    story = _create(state, channels, chats, title=text[:40], session_id="", cwd=cwd, engine=kind, mode=mode)
    _post(channels, story, t("long.made_live" if live else "long.made", where=app(where), cwd=cwd)
          + ("\n\n" + t("long.mode.open") if mode == "open" else ""), title=t("long.title.new"))
    env, prompt = {}, text
    if short:
        child = shorts.start(state, channels, chats, text[:60], [], who=f"📟 #{story['id']}", parent=short)
        env = {"BUDDY_SHORT": str(child["id"])}
        prompt = t("long.short_context", n=child["id"], parent=short) + "\n\n" + text
        story = _change(state, story["id"], lambda s: s.update(short=child["id"]))
    if live:  # the buddy's live session: its answers come through the pump (long_live.py)
        from . import long_live
        story = _change(state, story["id"], lambda s: _event(s, "new", _cut(text, 120), where=where))
        why = long_live.start(state, channels, story["id"], engine, prompt, where=where, who=who)
        if why:
            if env:
                shorts.finish(state, channels, int(env["BUDDY_SHORT"]), failed=True, note=why)
            _post(channels, story, why, title=t("long.title.away"), failed=True)
            raise ValueError(why)
        return load(state, story["id"])
    try:
        r = engine.run(prompt, cwd=cwd, **({"extra_env": env} if env else {}))
    except Exception as e:
        if env:
            shorts.finish(state, channels, int(env["BUDDY_SHORT"]), failed=True, note=t("long.failed", e=e))
        raise
    if env and not r.ok:
        shorts.finish(state, channels, int(env["BUDDY_SHORT"]), failed=True, note=_cut(r.text, 120))

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


def _scratch() -> set[str]:
    """Where scratch folders live: /tmp, /var/tmp and the system's temp dir."""
    return {"/tmp", "/var/tmp", os.path.realpath(tempfile.gettempdir())}


def _temporary(folder: str) -> bool:
    """A scratch folder: a test's, never worth a button."""
    real = os.path.realpath(folder)
    return any(real == d or real.startswith(d + os.sep) for d in _scratch())


def folders(state: Path, home: Path | None = None, n: int = 6) -> list[str]:
    """Folders to offer when a long story starts from the phone: those of earlier stories,
    then the projects touched last (~/projects/*, newest first). Only ones that exist, and no
    scratch folders (a test's long story in /tmp would push a real project off the card).
    Any other folder is one tap on 📂 Other folder… away (find_folder)."""
    home = home or Path.home()
    seen = [s["cwd"] for s in reversed(all_stories(state)) if s.get("cwd")]
    projects = sorted((p for p in (home / "projects").glob("*") if p.is_dir() and not p.name.startswith(".")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for f in seen + [str(p) for p in projects]:
        if f not in out and os.path.isdir(f) and not _temporary(f):
            out.append(f)
    return out[:n]


def find_folder(typed: str, home: Path | None = None, n: int = 12) -> tuple[str | None, str, list[str]]:
    """A folder typed on the phone, for 📂 Other folder…: an absolute path, ~/…, or a bare name
    or relative path under ~/projects. Returns (the folder, "", []) when it exists, else
    (None, the nearest folder that does, its subfolders), so the reply can list where to go
    next. A path ending in / (or nothing) lists that folder. The subfolders are those whose
    name holds the first missing part, if any does (a typo or a fragment works as a search:
    `avc444`), else all of them; newest first, no hidden ones, the first n and "+k"."""
    home = home or Path.home()
    word = typed.strip().strip("`\"'")
    if word.startswith("~"):
        path = Path(str(home) + word[1:])
    elif word.startswith("/"):
        path = Path(word)
    else:
        path = home / "projects" / word
    if path.is_dir() and word and not word.endswith("/"):
        return os.path.realpath(path), "", []
    near = path
    while not near.is_dir() and near != near.parent:
        near = near.parent
    try:
        dirs = sorted((p for p in near.iterdir() if p.is_dir() and not p.name.startswith(".")),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:  # not readable: the folder alone, nothing listed
        dirs = []
    if near != path:
        needle = path.relative_to(near).parts[0].lower()
        dirs = [p for p in dirs if needle in p.name.lower()] or dirs
    subs = [p.name for p in dirs]
    return None, str(near), subs[:n] + ([f"+{len(subs) - n}"] if len(subs) > n else [])


def board(state: Path) -> str:
    """Every long story, one line each, for /long and `buddy long list`."""
    ss = all_stories(state)
    if not ss:
        return t("long.none")
    return "\n".join(t("long.board_line", label=label(s), who=t("long.held") if held(s) else t("long.away"),
                       turns=s["turns"], cwd=s["cwd"]) + f" · {s.get('engine', 'claude-code')}"
                     + (" · 🔓" if s.get("mode") == "open" else "") for s in ss)


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
    line = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", " ".join(text.split()))  # no Ctrl-C, ESC, ^D...
    for ch in ("\\", "^", "$"):
        line = line.replace(ch, "\\" + ch)
    stuff = lambda keys: subprocess.run(["screen", "-S", sty, "-p", window, "-X", "stuff", keys],
                                        capture_output=True, text=True, timeout=10).returncode == 0
    try:
        if line and not stuff(line):
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


def _keys(story: dict, text: str, enter: bool = True) -> bool:
    """Type into the window that holds the session: pys or GNU screen. False when none does."""
    if story.get("mux") == "pys":
        return _pys_type(story["window"], text, enter, story.get("session", ""))
    if story.get("sty"):
        return _type(story["sty"], story["window"], text) if enter else _type(story["sty"], story["window"], text, enter=False)
    return False


def _look(story: dict) -> list[str]:
    """What the session's window shows now, or [] when no window holds it."""
    if story.get("mux") == "pys":
        return _pys_screen(story["window"], story.get("session", ""))
    return _screen(story["sty"], story["window"]) if story.get("sty") else []


def _pys_env(session: str) -> dict | None:
    """session: a pys server's runtime dir ($PYS_SESSION), when it may not be the default one."""
    return {**os.environ, "PYS_DIR": session} if session else None


def _pys_type(window: str, text: str, enter: bool = True, session: str = "") -> bool:
    """Type text into a pys window (`pys send`), then Enter on its own: Claude Code reads a
    burst of keys as a paste, where Enter is text. One line, no control keys, as for screen;
    no escaping (argv), and `--` so text starting with "-" is never a flag."""
    line = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", " ".join(text.split()))
    send = lambda *a: subprocess.run(["pys", "send", *a], capture_output=True, text=True, timeout=10,
                                     env=_pys_env(session)).returncode == 0
    try:
        if line and not send(window, "--", line):
            return False
        if enter:
            time.sleep(PASTE_GAP)
            return send("--enter", window, "")
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _pys_view(window: str, session: str = "") -> tuple[list[str], bool]:
    """What a pys window shows (blank lines left out), and whether its cursor sits at the very
    start of Claude Code's input box: only the ❯ to its left. A ghost text there (the "Try …"
    placeholder, a suggested prompt) is not typed text, which would have moved the cursor on."""
    try:
        p = subprocess.run(["pys", "capture", "--json", window], capture_output=True, text=True,
                           timeout=10, env=_pys_env(session))
        d = json.loads(p.stdout) if p.returncode == 0 else {}
        col, row = d.get("cursor") or (0, -1)
        lines = d.get("lines") or []
        start = 0 <= row < len(lines) and lines[row][:col].strip() == "❯"
        return [x.rstrip() for x in lines if x.strip()], start
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        return [], False


def _erase(story: dict, n: int) -> bool:
    """n backspaces into the window: takes back what wake() typed, from just behind the cursor."""
    keys = "\x7f" * n
    try:
        if story.get("mux") == "pys":
            return subprocess.run(["pys", "send", story["window"], "--", keys], capture_output=True,
                                  text=True, timeout=10, env=_pys_env(story.get("session", ""))).returncode == 0
        if story.get("sty"):
            return subprocess.run(["screen", "-S", story["sty"], "-p", story["window"], "-X", "stuff", keys],
                                  capture_output=True, text=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass
    return False


def _pys_titles(window: str, session: str = "") -> dict | None:
    """A pys window's titles from `pys ls --json`: name (what shows), title (set by hand),
    agent_title (`pys title`), app_title (OSC 2 from its app); None when it is not there."""
    try:
        p = subprocess.run(["pys", "ls", "--json"], capture_output=True, text=True, timeout=10,
                           env=_pys_env(session))
        return next((w for w in json.loads(p.stdout)["windows"] if str(w["wid"]) == str(window)), None)
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None


def _pys_title(window: str, session: str, title: str, owner: int) -> bool:
    """Set a pys window's agent title, owned by a process: pys drops it once that process is
    gone (it checks its start time too), so a session that ends leaves no title behind. False
    when this pys has no `title` (an older one): then the caller writes OSC 2 instead."""
    title = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", title)
    try:
        return subprocess.run(["pys", "title", "--wid", str(window), "--owner", str(owner), "--", title],
                              capture_output=True, text=True, timeout=10, env=_pys_env(session)).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _set_title(tty: str, title: str) -> bool:
    """Set the title of the terminal on tty ("pts/3") by writing OSC 2 to it, as the app in it
    would: pys takes it as the window's title; one set by hand (C-a ,) still wins. No control
    keys in it: a story's title is anyone's text, and an ESC would reach the terminal."""
    title = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", title)
    try:
        with open(f"/dev/{tty}", "w") as f:
            f.write(f"\033]2;{title}\007")
        return True
    except OSError:
        return False


def _pys_screen(window: str, session: str = "") -> list[str]:
    """What a pys window shows now (`pys capture`), its blank lines left out."""
    try:
        p = subprocess.run(["pys", "capture", window], capture_output=True, text=True, timeout=10,
                           env=_pys_env(session))
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [x.rstrip() for x in p.stdout.splitlines() if x.strip()] if p.returncode == 0 else []


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


def box(lines: list[str]) -> str | None:
    """What Claude Code's input box holds ("" when empty), or None when no box is on the screen:
    a dialog, its transcript view, or not Claude Code at all. The box is the last two solid rules
    near the bottom with a line starting ❯ right after the first. A dialog's cursor is a ❯ too,
    but it has its choice beside it. A ghost suggestion can't be told from typed text in a capture
    (it has no colours): it reads as a box that holds something."""
    rules = [i for i, x in enumerate(lines) if len(x.strip()) >= 10 and set(x.strip()) == {"─"}]
    if len(rules) < 2 or len(lines) - rules[-1] > 6:
        return None
    held = [x.strip() for x in lines[rules[-2] + 1:rules[-1]]]
    if not held or not held[0].startswith("❯"):
        return None
    return " ".join([held[0][1:].strip(), *held[1:]]).strip()


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


def wake(window: dict, line: str) -> bool:
    """Type one fixed line into the window of a Claude Code session that is not ours to drive, as
    a nudge: only when it cannot go wrong, else False and nothing was sent. window: its mux, sty,
    window and session, the pid of its `claude` and when that started.

    Typing is blind, so each way it could go wrong has its check. The window now holds a plain
    shell: that `claude` is gone or stopped (Ctrl-Z), or another process took the number or the
    terminal's foreground. A dialog, a turn running, the owner's half-written prompt: the screen
    is not an idle, empty box. If he starts typing in the moment between, the box then holds more
    than the line, so no Enter: it stays there, in front of him, and nothing is sent. A Codex
    session is not typed into: _wake_codex queues the line instead.

    An empty box often shows a ghost text (a placeholder, a suggested prompt), which a capture
    can't tell from typed text by its look. In pys the cursor tells: at the box's start, it is
    ghost text, or his draft with the cursor moved back (Home). Typing tells those two apart: a
    ghost text gives way, a draft does not. When the box then holds more than the line, the line
    is taken back with as many backspaces, the cursor being just after it, and his draft is as
    it was. screen has no cursor in its hardcopy: there the box must read empty."""
    if window.get("engine") == "codex":
        return _wake_codex(window, line)
    p = portable.process(window.get("pid") or 0)
    if not p or p["comm"] != "claude" or p["started"] != window.get("started") or p["pgid"] != p["tpgid"]:
        return False
    lines, start = _pys_view(window["window"], window.get("session", "")) if window.get("mux") == "pys" \
        else (_look(window), False)
    held = box(lines)
    if screen_state(lines) != "idle" or held is None or (held and not start):
        return False
    line = " ".join(line.split())
    if not _keys(window, line, enter=False):
        return False
    bare = "".join(line.split())
    for _ in range(WAKE_TRIES):
        time.sleep(PASTE_GAP)
        if "".join((box(_look(window)) or "").split()) == bare:
            return _keys(window, "")  # Enter alone
    _erase(window, len(line))
    return False


WAKE_TRIES = 3  # looks for the typed line in the box before leaving it unsent


def _wake_codex(window: dict, line: str) -> bool:
    """A Codex session gets the line through `codex queue`, no typing: Codex hands it to the
    terminal that holds the thread, after the turn running there. With no terminal holding it,
    Codex's daemon would run the turn itself, unattended, which the owner ruled out (2026-11-07):
    so only while the daemon has the thread loaded and a Codex terminal runs in its folder
    (codexd: loaded alone lags a terminal's quit by up to a minute)."""
    from . import codexd
    t = codexd.thread(window.get("thread", ""))
    if not t or (t.get("status") or {}).get("type") not in ("idle", "active") \
            or not codexd.terminal_in(t.get("cwd", "")):
        return False
    return _codex_queue(window["thread"], line)


def _codex_queue(thread: str, line: str) -> bool:
    try:
        return subprocess.run(["codex", "queue", "--thread", thread, "--message", line],
                              capture_output=True, text=True, timeout=60).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


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
