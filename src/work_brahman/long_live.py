"""Long stories over the protocol (specs/long-story-protocol.md): the buddy holds a Claude
session as one live process that speaks stream-json, and hands it to a terminal and back.

One writer at a time. Who holds a session (holder()):

    🤖 buddy      its live unit runs: a message goes in at once, one JSON line
    ⏳ releasing  the buddy ends its running turn, then closes (for /handover)
    📌 reserved   handed over, kept free for the owner's terminal: messages wait
    👤 terminal   a terminal's `claude` holds it (long_stories.held); hooks mirror it
    💤 nobody     no process: a card asks before it resumes (long_stories.say)

The live unit is `buddy-live-<n>` in buddy-long.slice (procs.py's umbrella, so a restart of
the buddy kills none and ■ STOP stops it), running longexec.py on the folder
<state>/long_stories/live/<n>/: `in` a FIFO the launcher holds open, `out` the event stream,
`rc` when it ended. `buddy serve` reads `out` from the offset kept in the story (pump), so a
restart loses no event; a permission the session needs becomes ✅ Allow / ⛔ Deny, and the
press goes back as a control_response line (answer).
"""

import errno
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from . import long_stories as L
from . import portable, procs
from .text import t

LAUNCHER = Path(__file__).with_name("longexec.py")
SLICE = "buddy-long.slice"
READER_WAIT = 15  # seconds a write waits for a launcher that just started to open its FIFO
PUMP_EVERY = 0.5
log = logging.getLogger("buddy.long")
_locks: dict[int, threading.Lock] = {}
_guard = threading.Lock()


def _lock(number: int) -> threading.Lock:
    with _guard:
        return _locks.setdefault(number, threading.Lock())


def folder(state: Path, number: int) -> Path:
    return Path(state) / "long_stories" / "live" / str(number)


def alive(story: dict) -> bool:
    """The story's live process still runs (its launcher's pid, no rc written yet)."""
    live = story.get("live") or {}
    if not live.get("pid") or (Path(live.get("dir", "")) / "rc").exists():
        return False
    return portable.alive(live["pid"])


def holder(story: dict) -> str:
    """buddy, releasing, reserved, terminal or nobody: processes first, flags after."""
    if alive(story):
        return "releasing" if story["live"].get("release") else "buddy"
    if L.held(story):
        return "terminal"
    return "reserved" if story.get("reserved") else "nobody"


def holders(session_id: str) -> list[int]:
    """The pids of live processes that have this session open, as Claude Code itself lists
    them (`claude agents --json`: interactive, background and -p sessions alike)."""
    try:
        p = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=20)
        rows = json.loads(p.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    return [int(r["pid"]) for r in rows
            if r.get("sessionId") == session_id and str(r.get("pid", "")).isdigit()]


def start(state: Path, channels: list, number: int, engine, prompt: str = "", *, where: str = "",
          who: str = "") -> str:
    """Start the story's session as the buddy's live unit, and send prompt as its first
    message. Returns "" when it runs, or why not (nothing started)."""
    drain(state, channels, number)  # a run that just ended: its last events first, then the folder goes
    story = L.load(state, number)
    if story is None:
        return t("long.no_such", n=number)
    if alive(story):
        return send(state, channels, number, prompt, where=where, who=who) if prompt else t("long.live.already")
    if L.held(story):
        return t("long.live.terminal", pid=story.get("pid"))
    if not os.path.isdir(story["cwd"]):
        return t("long.no_cwd", cwd=story["cwd"])
    if engine is None or not hasattr(engine, "live_cmd"):
        return t("long.no_engine")
    sid = story.get("session_id") or ""
    others = holders(sid) if sid else []
    if others:  # two writers fork a session (tested): never a second one
        return t("long.live.held_elsewhere", pids=", ".join(map(str, others)))
    new_id = "" if sid else str(uuid.uuid4())
    cmd = engine.live_cmd(session_id=sid, new_id=new_id)
    env = {**engine.env(), "BUDDY_LONG_SELF": "1", "SPEAK": "off"}  # SELF: its hooks stay quiet (scripts/buddy-long)
    if story.get("short"):
        env["BUDDY_SHORT"] = str(story["short"])
    if getattr(engine, "wrap", None):
        cmd, env = engine.wrap(cmd, story["cwd"], env)
    d = folder(state, number)
    shutil.rmtree(d, ignore_errors=True)  # the last run's stream: read to its end already (drain)
    d.mkdir(parents=True)
    os.mkfifo(d / "in", 0o600)
    (d / "out").touch()
    fd = os.open(d / "spec.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # the env may carry tokens
    with os.fdopen(fd, "w") as f:
        json.dump({"cmd": cmd, "cwd": story["cwd"], "env": env}, f)
    try:
        pid, unit = _launch(d, number)
    except OSError as e:
        return t("long.failed", e=e)

    def hold(s):
        s["live"] = {"dir": str(d), "pid": pid, "unit": unit, "offset": 0, "said": [], "perms": {},
                     "started": L._now()}
        s.pop("reserved", None)
        s.pop("takeback", None)
        s.pop("resume_asked", None)
        s["state"] = "idle"
        if new_id:
            s["session_id"] = new_id
        L._event(s, "live", unit or str(pid))
    story = L._change(state, number, hold)
    L._mark(state, story["session_id"])
    L._post(channels, story, t("long.live.started", unit=unit or f"pid {pid}", cwd=story["cwd"]),
            title=t("long.title.live"))
    if prompt:
        said = send(state, channels, number, prompt, where=where, who=who)
        return "" if said == t("long.sent") else said
    return ""


def _launch(d: Path, number: int) -> tuple[int, str]:
    """The launcher as a unit under the umbrella (Linux with systemd), else as a child in its
    own session (it then dies with a restart of the buddy: the session can be resumed)."""
    argv = [sys.executable, "-I", str(LAUNCHER), str(d)]
    if procs.umbrella():
        unit = f"buddy-live-{number}"
        p = subprocess.run(["systemd-run", "--user", "--quiet", "--collect", f"--slice={SLICE}", f"--unit={unit}",
                            f"--description=buddy long story #{number}: live session",
                            "-p", "StandardInput=null", "-p", f"StandardOutput=append:{d / 'out'}",
                            "-p", f"StandardError=append:{d / 'err'}", "--", *argv],
                           capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            raise OSError(f"systemd-run: {p.stderr.strip()}")
        for _ in range(50):
            q = subprocess.run(["systemctl", "--user", "show", "-p", "MainPID", "--value", unit],
                               capture_output=True, text=True, timeout=10)
            pid = int((q.stdout or "0").strip() or 0)
            if pid:
                return pid, unit
            time.sleep(0.1)
        raise OSError(f"{unit} has no process")
    with open(d / "out", "ab") as out, open(d / "err", "ab") as err:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
    return p.pid, ""


def put(state: Path, number: int, obj: dict) -> bool:
    """One JSON line into the live session's FIFO; False when no launcher reads it."""
    import fcntl  # POSIX only, like the FIFO itself
    path = folder(state, number) / "in"
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode()
    end = time.monotonic() + READER_WAIT
    with _lock(number):  # one line at a time: a long one must not interleave with another
        while True:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)  # ENXIO: nobody reads (yet)
                break
            except OSError as e:
                if e.errno != errno.ENXIO or time.monotonic() > end:
                    return False
                time.sleep(0.2)
        try:
            fcntl.fcntl(fd, fcntl.F_SETFL, fcntl.fcntl(fd, fcntl.F_GETFL) & ~os.O_NONBLOCK)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
        except OSError:
            return False
        finally:
            os.close(fd)
    return True


def send(state: Path, channels: list, number: int, text: str, *, where: str, who: str = "") -> str:
    """A message for the live session: one user line. Sent while a turn runs, it folds into
    that turn (tested), like typing while it works."""
    ok = put(state, number, {"type": "user", "message": {"role": "user", "content": text}})

    def note(s):
        s["inbox"].append({"at": L._now(), "where": where, "who": who, "text": text, "done": ok,
                           "how": "sent" if ok else "failed"})
        L._event(s, "say", L._cut(text, 120), how="sent" if ok else "failed", where=where)
        if ok:
            s["state"], s["prompt"] = "busy", text
    L._change(state, number, note)
    return t("long.sent") if ok else t("long.failed", e="the live session does not read")


def answer(state: Path, number: int, req: str, decision: str) -> None:
    """✅ Allow / ⛔ Deny on a permission the live session waits on."""
    story = L.load(state, number)
    live = (story or {}).get("live") or {}
    asked = live.get("perms", {}).pop(req, None)
    if asked is None:
        return  # answered already, or the session is gone
    resp = ({"behavior": "allow", "updatedInput": asked} if decision == "allow"
            else {"behavior": "deny", "message": "denied by the owner from his phone"})
    put(state, number, {"type": "control_response",
                        "response": {"subtype": "success", "request_id": req, "response": resp}})

    def done(s):
        s.get("live", {}).get("perms", {}).pop(req, None)
        s["state"] = "busy"
        L._event(s, "decide", decision)
    L._change(state, number, done)


def drain(state: Path, channels: list, number: int) -> None:
    """Read what the live session wrote since last time, and act on it; when it ended and
    everything is read, the story lets go of it."""
    story = L.load(state, number)
    live = (story or {}).get("live")
    if not live:
        return
    d = Path(live["dir"])
    ended = not alive(story)  # looked at before reading: all it wrote before the end gets read
    try:
        with open(d / "out", "rb") as f:
            f.seek(live["offset"])
            data = f.read()
    except OSError:
        data = b""
    cut = data.rfind(b"\n") + 1
    for line in data[:cut].splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        try:
            _event(state, channels, number, e)
        except Exception:
            log.exception("long story #%s: an event failed: %s", number, line[:200])
    if cut:
        L._change(state, number, lambda s: s["live"].update(offset=s["live"]["offset"] + cut))
    if ended:
        _ended(state, channels, number)


def _event(state: Path, channels: list, number: int, e: dict) -> None:
    kind = e.get("type")
    if kind == "system" and e.get("subtype") == "init" and e.get("session_id"):
        story = L._change(state, number, lambda s: s.update(session_id=e["session_id"]))
        L._mark(state, story["session_id"])
    elif kind == "assistant":
        def said(s):
            for b in (e.get("message") or {}).get("content") or []:
                if b.get("type") == "tool_use":
                    s["live"]["said"] = []  # what it says between tool calls stays in the transcript
                elif b.get("type") == "text" and b.get("text", "").strip():
                    s["live"]["said"].append(b["text"])
        L._change(state, number, said)
    elif kind == "control_request":
        _request(state, channels, number, e)
    elif kind == "result":
        u = e.get("usage") or {}
        cost = {"usd_session": e.get("total_cost_usd") or 0, "turns": e.get("num_turns") or 0,
                "agent_ms": e.get("duration_ms") or 0,
                "tokens_in": (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0),
                "tokens_cached": u.get("cache_read_input_tokens") or 0, "tokens_out": u.get("output_tokens") or 0}
        text = []

        def end(s):
            text.append(e.get("result") or "\n\n".join(s["live"]["said"]))
            s["live"]["said"] = []
            s["turns"] += 1
            s["state"] = "idle"
            L._event(s, "turn", L._cut(text[0], 120), ok=not e.get("is_error"), engine="claude-code", cost=cost)
        story = L._change(state, number, end)
        L._post(channels, story, L._cut(text[0]) or t("long.no_text"), title=t("long.title.turn", n=story["turns"]),
                subtitle=L._cut(story.get("prompt", ""), 80), failed=bool(e.get("is_error")))


def _request(state: Path, channels: list, number: int, e: dict) -> None:
    """A control request from the session: a permission becomes a card; anything else gets a
    plain error back, so the session never waits on a question nobody answers."""
    from . import asks
    req, r = e.get("request_id", ""), e.get("request") or {}
    if r.get("subtype") != "can_use_tool":
        put(state, number, {"type": "control_response",
                            "response": {"subtype": "error", "request_id": req, "error": "not supported by the buddy"}})
        return
    tool, given = r.get("tool_name", "?"), r.get("input") or {}
    what = given.get("description") or given.get("command") or json.dumps(given, ensure_ascii=False)

    def wait(s):
        s["live"]["perms"][req] = given
        s["state"] = "waiting"
        L._event(s, "wait", f"{tool}: {L._cut(str(what), 100)}")
    story = L._change(state, number, wait)
    asks.create(state, channels, list(story["threads"]),
                t("long.perm", label=L.label(story), tool=tool, what=L._cut(str(what), 1500)),
                title=t("long.title.waiting"), options=[["allow", t("long.allow")], ["deny", t("long.deny")]],
                then={"allow": ["long", "perm", number, req, "allow"], "deny": ["long", "perm", number, req, "deny"]})


def _ended(state: Path, channels: list, number: int) -> None:
    story = L.load(state, number)
    live = story.get("live") or {}
    try:
        rc = (Path(live.get("dir", "")) / "rc").read_text().strip()
    except OSError:
        rc = "?"
    handed = bool(live.get("release")) and not story.get("takeback")

    def let_go(s):
        s.pop("live", None)
        s["state"] = "idle"
        if handed:
            s["reserved"] = True
        L._event(s, "away", f"rc {rc}")
    story = L._change(state, number, let_go)
    if handed:
        L._post(channels, story, t("long.handed_over", n=number, cwd=story["cwd"], id=story["session_id"]),
                title=t("long.title.handover"))
    elif not story.get("takeback"):
        L._post(channels, story, t("long.live.ended", rc=rc), title=t("long.title.away"))


def pump(state: Path, channels: list, engines: dict | None = None) -> None:
    """Every live story's new events; a /takeback that waited for a terminal or a release
    to end starts the live session once nobody holds it."""
    for s in L.all_stories(state):
        if s.get("live"):
            drain(state, channels, s["id"])
            s = L.load(state, s["id"])
        if s.get("takeback") and engines is not None and holder(s) in ("nobody", "reserved"):
            L._change(state, s["id"], lambda x: x.pop("takeback", None))
            why = start(state, channels, s["id"], L.pick(engines, "claude-code", s.get("mode")),
                        _waiting(state, s["id"]))
            if why:
                L._post(channels, s, why, title=t("long.title.away"))


def start_pump(state: Path, channels: list, engines: dict) -> None:
    """`buddy serve`'s thread for pump()."""
    def loop():
        while True:
            try:
                pump(state, channels, engines)
            except Exception:
                log.exception("pumping long stories failed")
            time.sleep(PUMP_EVERY)
    threading.Thread(target=loop, name="long-pump", daemon=True).start()


def _waiting(state: Path, number: int) -> str:
    """The messages that waited for the session (asked, kept, reserved), as one; marked done."""
    got = []

    def take(s):
        for m in s.get("inbox", []):
            if not m.get("done") and m.get("how") in ("asked", "waits", "queued"):
                m["done"], m["how"] = True, "resumed"
                got.append(m["text"])
    L._change(state, number, take)
    return "\n\n".join(got)


# --- into a terminal through pys: the workbench's Resume and Attach, and /takeover, /shift ------

def pys_path() -> str:
    """Where pys is, or "" when this machine has none."""
    p = shutil.which("pys") or os.path.expanduser("~/.local/bin/pys")
    return p if os.path.isfile(p) else ""


def to_terminal(story: dict, what: str, pys: str | None = None) -> tuple[int, str, object]:
    """A long story into a terminal (the owner, 2026-11-10: "attach or resume by pys"; 2026-11-12:
    "/takeover … /shift to the active pys window, if one exists"). (status, what to say, window):
    - "resume" (/takeover, the page's Resume): a new pys window in its folder runs
      `buddy-long take N`, which hands over when the buddy holds it, waits for its turn to end
      and resumes the session there;
    - "attach" (/shift, the page's Attach): the pys window a terminal holds it in comes to the
      front (pys select), checked first: the same process still runs there.
    200 done; 409 not possible now (said why); 502/504 pys failed."""
    n = story["id"]
    pys = pys if pys is not None else pys_path()
    # the pys server: the one that holds it when the story knows it (a test server has windows with
    # the same numbers), else the default one, the owner's own (the buddy runs as the owner)
    env = {**os.environ, "PYS_SESSION": story["session"]} if story.get("session") else None
    if not pys:  # buddy setup only looks for pys: a user without it gets the command, not a dropped call
        return 409, t("long.term.no_pys", n=n), ""
    try:
        if what == "attach":
            if not (L.held(story) and story.get("mux") == "pys" and story.get("window") != ""):
                return 409, t("long.term.not_held", n=n), ""
            r = subprocess.run([pys, "select", str(story["window"])], capture_output=True, text=True, timeout=10,
                               **({"env": env} if env else {}))
            if r.returncode != 0:
                return 502, t("long.term.failed", why=r.stderr.strip() or "pys select"), ""
            return 200, t("long.term.shown", n=n, w=story["window"]), story["window"]
        if L.held(story):
            return 409, t("long.term.held", n=n), ""
        if story.get("engine", "claude-code") != "claude-code":
            return 409, t("long.term.codex", id=story.get("session_id") or "?"), ""
        if not os.path.isdir(story["cwd"]):
            return 409, t("long.term.gone", cwd=story["cwd"]), ""
        r = subprocess.run([pys, "new", "--select", "--", "bash"], cwd=story["cwd"], capture_output=True,
                           text=True, timeout=10)
        try:
            wid = json.loads(r.stdout)["wid"]
        except (ValueError, KeyError, TypeError):
            return 502, t("long.term.failed", why=(r.stderr or r.stdout).strip() or "pys new"), ""
        subprocess.run([pys, "send", "--enter", str(wid), f"buddy-long take {n}"], capture_output=True, timeout=10)
        log.info("long story #%s: taken over in pys window %s", n, wid)
        return 200, t("long.term.opened", n=n, w=wid, cwd=story["cwd"]), wid
    except subprocess.TimeoutExpired:
        return 504, t("long.term.failed", why="pys did not answer within 10 s"), ""
    except OSError as e:
        return 502, t("long.term.failed", why=str(e)), ""


# --- the owner's commands: /status, /handover, /takeback, buddy-long take --------------------

def status(state: Path, number: int) -> str:
    story = L.load(state, number)
    if story is None:
        return t("long.no_such", n=number)
    h = holder(story)
    pid = (story.get("live") or {}).get("pid") if h in ("buddy", "releasing") else story.get("pid")
    waiting = sum(1 for m in story.get("inbox", []) if not m.get("done"))
    return t("long.status", who=t(f"long.h.{h}", pid=pid), state=story.get("state", "idle"),
             turns=story["turns"], waiting=waiting, id=story.get("session_id") or "-", cwd=story["cwd"],
             next=t(f"long.next.{h}", n=number))


def handover(state: Path, channels: list, number: int) -> str:
    """🤖 → ⏳ → 📌: the running turn ends, the process closes, the session is kept for the
    owner's terminal (`buddy-long take <n>`)."""
    story = L.load(state, number)
    if story is None:
        return t("long.no_such", n=number)
    h = holder(story)
    if h == "buddy":
        L._change(state, number, lambda s: s["live"].update(release=True) or s.pop("takeback", None))
        put(state, number, {"type": "buddy_release"})
        return t("long.releasing", n=number)
    if h == "terminal":
        return t("long.live.terminal", pid=story.get("pid"))
    if h == "nobody":
        L._change(state, number, lambda s: s.update(reserved=True))
    return "" if h == "releasing" else t("long.handed_over", n=number, cwd=story["cwd"], id=story["session_id"])


def takeback(state: Path, channels: list, number: int, engine, *, force: bool = False) -> str:
    """📌 or 💤 → 🤖 now. From 👤: once the terminal's session closes (the pump starts it then);
    force ends that session first, with SIGTERM between turns (tested safe), never mid-turn."""
    from . import asks
    story = L.load(state, number)
    if story is None:
        return t("long.no_such", n=number)
    h = holder(story)
    if h == "buddy":
        return t("long.live.already")
    if h == "releasing":  # a /handover not finished yet: the buddy keeps it after all
        L._change(state, number, lambda s: s.update(takeback=True))
        return t("long.takeback.soon")
    if h in ("reserved", "nobody"):
        L._change(state, number, lambda s: s.pop("reserved", None))
        return start(state, channels, number, engine, _waiting(state, number))
    # a terminal holds it
    if force:
        if story.get("state") == "busy":
            return t("long.takeback.busy")
        L._change(state, number, lambda s: s.update(takeback=True))
        try:
            os.kill(int(story["pid"]), signal.SIGTERM)
        except (OSError, TypeError, ValueError) as e:
            return t("long.failed", e=e)
        return t("long.takeback.ending")
    L._change(state, number, lambda s: s.update(takeback=True))
    asks.create(state, channels, list(story["threads"]), t("long.takeback.wait", pid=story.get("pid")),
                title=t("long.title.handover"), options=[["end", t("long.takeback.end_btn")]],
                then={"end": ["long", "takeback", number, "force"]})
    return ""


def take(state: Path, channels: list, number: int, wait: float = 900) -> tuple[str, str]:
    """`buddy-long take <n>`: hand over when the buddy holds it, wait for its turn to end, and
    return (folder, session id) for the terminal to resume; ValueError says why not."""
    story = L.load(state, number)
    if story is None:
        raise ValueError(t("long.no_such", n=number))
    if holder(story) == "terminal":
        raise ValueError(t("long.live.terminal", pid=story.get("pid")))
    if holder(story) == "buddy":
        handover(state, channels, number)
        print(t("long.take.waiting"), file=sys.stderr)
    end = time.monotonic() + wait
    while alive(L.load(state, number)):
        if time.monotonic() > end:
            raise ValueError(t("long.take.timeout"))
        time.sleep(0.5)
    story = L.load(state, number)
    others = holders(story["session_id"]) if story.get("session_id") else []
    if others:
        raise ValueError(t("long.live.held_elsewhere", pids=", ".join(map(str, others))))
    if not story.get("session_id"):
        raise ValueError(t("long.no_engine"))
    return story["cwd"], story["session_id"]
