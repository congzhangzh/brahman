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
<state>/long_stories/live/<n>/: `in` a FIFO the launcher holds open (Windows, which has no
FIFOs: a named pipe the launcher listens on, its address and key in `pipe.json`; see _put_pipe()),
`out` the event stream, `rc` when it ended. `buddy serve` reads `out` from the offset kept in the story (pump), so a
restart loses no event; a permission the session needs becomes ✅ Allow / ⛔ Deny, and the
press goes back as a control_response line (answer).
"""

import errno
import json
import re
import logging
import os
import shutil
import secrets
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
        p = subprocess.run(portable.argv(["claude", "agents", "--json"]), capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace")
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
    pipe = None
    if hasattr(os, "mkfifo") and not PIPE:
        os.mkfifo(d / "in", 0o600)
    else:  # Windows (no FIFOs: "buddy long new" failed there): a named pipe, nothing on disk but its key
        pipe = _pipe(d, number)
    (d / "out").touch()
    fd = os.open(d / "spec.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # the env may carry tokens
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"cmd": portable.argv(cmd), "cwd": story["cwd"], "env": env}  # Windows: claude.cmd's exe
                  | ({"pipe": pipe} if pipe else {}), f)
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
    argv = [sys.executable, "-I", "-X", "utf8", str(LAUNCHER), str(d)]  # UTF-8 mode (-I ignores PYTHONUTF8)
    if procs.umbrella():
        unit = f"buddy-live-{number}"
        p = subprocess.run(["systemd-run", "--user", "--quiet", "--collect", f"--slice={SLICE}", f"--unit={unit}",
                            f"--description=buddy long story #{number}: live session",
                            "-p", "StandardInput=null", "-p", f"StandardOutput=append:{d / 'out'}",
                            "-p", f"StandardError=append:{d / 'err'}", "--", *argv],
                           capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            raise OSError(f"systemd-run: {p.stderr.strip()}")
        for _ in range(50):
            q = subprocess.run(["systemctl", "--user", "show", "-p", "MainPID", "--value", unit],
                               capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace")
            pid = int((q.stdout or "0").strip() or 0)
            if pid:
                return pid, unit
            time.sleep(0.1)
        raise OSError(f"{unit} has no process")
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if portable.WINDOWS  # no Ctrl-C from the buddy's console
             else {"start_new_session": True})
    with open(d / "out", "ab") as out, open(d / "err", "ab") as err:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=err, **group)
    return p.pid, ""


def put(state: Path, number: int, obj: dict) -> bool:
    """One JSON line into the live session's FIFO; False when no launcher reads it."""
    path = folder(state, number) / "in"
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode()
    end = time.monotonic() + READER_WAIT
    if (path.parent / "pipe.json").exists():
        return _put_pipe(state, number, data, end)
    if not hasattr(os, "mkfifo"):  # Windows with no pipe: a session left by 20261118.1's file inbox
        return False  # nobody reads it any more: the story offers a resume, as for any gone session
    import fcntl  # POSIX only, like the FIFO itself
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


PIPE = False  # True: the named-pipe inbox on POSIX too (tests), over a Unix socket
PIPE_WAIT = 10.0  # seconds each step of a send waits for the launcher's answer


def _pipe(d: Path, number: int) -> dict:
    """A new inbox for the launcher (Windows): a named pipe with a random name and a random 32-byte
    key. The launcher listens with multiprocessing.connection and the key answers its HMAC challenge,
    so a client without it is refused (a pipe's default DACL lets others open it). The key goes in
    the launcher's spec (read, then deleted) and in pipe.json here (0600, removed at the end): the
    buddy needs it for every message. Never logged."""
    if portable.WINDOWS:
        address, family = rf"\\.\pipe\brahman-long-{number}-{secrets.token_hex(8)}", "AF_PIPE"
    else:
        address, family = str(d / "in.sock"), "AF_UNIX"
    pipe = {"address": address, "family": family, "authkey": secrets.token_hex(32)}
    fd = os.open(d / "pipe.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(pipe, f)
    return pipe


def _put_pipe(state: Path, number: int, data: bytes, end: float) -> bool:
    """One message to the launcher's named pipe (Windows), the FIFO's contract: False when no launcher
    reads, i.e. none listens within READER_WAIT (it just started), or it ended (rc), or its process is
    gone; one whole message per send. Both sides prove the key (longexec._serve_one), each read with a
    deadline; True only once the launcher said "ok", so the next message never overtakes this one."""
    import hashlib
    import hmac
    from multiprocessing.connection import Client
    d = folder(state, number)
    try:
        pipe = json.loads((d / "pipe.json").read_text(encoding="utf-8"))
        key = bytes.fromhex(pipe["authkey"])
    except (OSError, ValueError, KeyError):
        return False
    proof = lambda who, nonce: hmac.new(key, who + nonce, hashlib.sha256).digest()  # noqa: E731
    pid = ((L.load(state, number) or {}).get("live") or {}).get("pid")
    with _lock(number):
        while True:
            if (d / "rc").exists() or (pid and not portable.alive(pid)):
                return False
            try:
                c = Client(pipe["address"], family=pipe["family"])
            except OSError:  # not listening yet
                if time.monotonic() > end:
                    return False
                time.sleep(0.2)
                continue
            try:
                with c:
                    if not c.poll(PIPE_WAIT):
                        return False
                    hello = c.recv_bytes(256)
                    if not hello.startswith(b"brahman-long-pipe 1") or len(hello) != 19 + 32:
                        return False
                    mine = os.urandom(32)
                    c.send_bytes(proof(b"client", hello[19:]) + mine)
                    if not c.poll(PIPE_WAIT) or not hmac.compare_digest(c.recv_bytes(256), proof(b"server", mine)):
                        return False  # not our launcher: it never gets the message
                    c.send_bytes(data)
                    return c.poll(PIPE_WAIT) and c.recv_bytes(16) == b"ok"
            except (EOFError, OSError):
                return False


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
    always = live.get("always", {}).get(req) or []
    if decision == "always" and always:  # allow, and the rule into <cwd>/.claude/settings.local.json
        resp = {"behavior": "allow", "updatedInput": asked,
                "updatedPermissions": [dict(s, destination="localSettings") for s in always]}  # never userSettings
    elif decision in ("allow", "always"):
        resp = {"behavior": "allow", "updatedInput": asked}
    else:
        resp = {"behavior": "deny", "message": "denied by the owner from his phone"}
    put(state, number, {"type": "control_response",
                        "response": {"subtype": "success", "request_id": req, "response": resp}})

    def done(s):
        s.get("live", {}).get("perms", {}).pop(req, None)
        s.get("live", {}).get("always", {}).pop(req, None)
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
    always = always_rules(r.get("permission_suggestions"))

    def wait(s):
        s["live"]["perms"][req] = given
        if always:
            s["live"].setdefault("always", {})[req] = always
        s["state"] = "waiting"
        L._event(s, "wait", f"{tool}: {L._cut(str(what), 100)}")
    story = L._change(state, number, wait)
    body = t("long.perm", label=L.label(story), tool=tool, what=L._cut(str(what), 1500))
    if always:  # the whole rule in the card, so a long one can't hide its end on the button
        body += "\n\n" + t("long.always_rule", rule=rule_text(always))
    asks.create(state, channels, list(story["threads"]), body,
                title=t("long.title.waiting"),
                options=[["allow", t("long.allow")]] + ([["always", t("long.always", rule=rule_text(always, 60))]] if always else [])
                + [["deny", t("long.deny")]],
                then={"allow": ["long", "perm", number, req, "allow"], "always": ["long", "perm", number, req, "always"],
                      "deny": ["long", "perm", number, req, "deny"]})


def always_rules(suggestions) -> list[dict]:
    """The session's own suggestions (can_use_tool's permission_suggestions, checked against Claude Code
    2026-11-19): addRules that allow, each rule narrower than a whole tool ("Bash" alone would allow
    every command). [] when there is none such: then no "Always" button."""
    out = []
    for s in suggestions or []:
        if not isinstance(s, dict) or s.get("type") != "addRules" or s.get("behavior") != "allow":
            continue
        rules = [r for r in s.get("rules") or [] if isinstance(r, dict) and r.get("toolName")
                 and _narrow(str(r.get("ruleContent") or ""))]
        if rules and len(rules) == len(s.get("rules") or []):  # a suggestion with any whole-tool rule: none of it
            out.append({"type": "addRules", "rules": rules, "behavior": "allow"})
    return out


def _narrow(content: str) -> bool:
    """A rule narrower than its whole tool: not only wildcards, separators and slashes (Bash(*:*),
    Bash(* *), Read(**), Edit(/**) each grant the whole tool; the review, 2026-11-19), and no control
    character (a newline could hide the rule's end on the button)."""
    if any(ord(c) < 32 or ord(c) == 127 or c == "`" for c in content):  # a backtick would break the card's code
        return False
    return not re.fullmatch(r"[\s*:/.]*", content)


def rule_text(always: list[dict], cut: int = 0) -> str:
    """Bash(pys title *), Bash(samvada speak *): what the button writes, as settings.json spells it;
    cut: at most that many characters (the button), with "…" (the card shows it whole)."""
    text = ", ".join(f"{r['toolName']}({r['ruleContent']})" for s in always for r in s["rules"])
    return text if not cut or len(text) <= cut else text[:cut - 1] + "…"


def _forget_inbox(d: Path) -> None:
    """Windows: the named pipe's key (pipe.json). Once its launcher is gone (rc written, or stopped)
    nobody listens: removed. A FIFO (POSIX) holds nothing and stays for the next start to clear."""
    for name in ("pipe.json", "in.sock"):
        try:
            (d / name).unlink(missing_ok=True)
        except OSError:
            pass


def _ended(state: Path, channels: list, number: int) -> None:
    story = L.load(state, number)
    live = story.get("live") or {}
    if live.get("dir"):
        _forget_inbox(Path(live["dir"]))  # the launcher is gone, so the file is closed (Windows can delete it)
    try:
        rc = (Path(live.get("dir", "")) / "rc").read_text(encoding="utf-8").strip()
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
    """Every live story's new events; a /frompys that waited for a terminal or a release
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


# --- into a terminal through pys: the workbench's Resume and Attach, and /topys, /frompys ---

def pys_path() -> str:
    """Where pys is, or "" when this machine has none."""
    p = shutil.which("pys") or os.path.expanduser("~/.local/bin/pys")
    return p if os.path.isfile(p) else ""


def to_terminal(story: dict, what: str, pys: str | None = None, *, state: Path | None = None,
                channels: list | None = None) -> tuple[int, str, object]:
    """A long story into a terminal (the owner, 2026-11-10: "attach or resume by pys"; 2026-11-12:
    "/topys … /frompys"). (status, what to say, window):
    - "resume" (/topys when no window holds it, the page's Resume): what `buddy-long take N` does, in
      the buddy itself, no shell (Windows has none; 2026-11-19): hand over when the buddy holds it,
      wait for its turn to end, then a new pys window in its folder runs `claude --resume <id>` with
      BUDDY_LONG=1 (never the buddy's .env). When the buddy must first finish a turn, the answer is
      202 "handing over" at once and the window is reported to the story's threads later;
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
                               encoding="utf-8", errors="replace", env=portable.child_env(env))
            if r.returncode != 0:
                return 502, t("long.term.failed", why=r.stderr.strip() or "pys select"), ""
            return 200, t("long.term.shown", n=n, w=story["window"]), story["window"]
        if L.held(story):
            return 409, t("long.term.held", n=n), ""
        if story.get("engine", "claude-code") != "claude-code":
            return 409, t("long.term.codex", id=story.get("session_id") or "?"), ""
        if not os.path.isdir(story["cwd"]):
            return 409, t("long.term.gone", cwd=story["cwd"]), ""
        if state is None:
            return 409, t("long.term.no_pys", n=n), ""
        has_env = pys_has_env(pys, env)
        if not has_env and portable.WINDOWS:
            return 409, t("long.term.old_pys"), ""
        if not pys_running(pys, env):  # never a server started by the buddy (the PO, 2026-11-19)
            return 409, t("long.term.no_server"), ""
        if alive(story):  # the buddy holds it: its turn ends first; never block the chat for that
            threading.Thread(target=_take_then_open, args=(state, channels or [], n, pys, env, has_env),
                             name=f"topys-{n}", daemon=True).start()
            return 202, t("long.term.handing", n=n), ""
        cwd, sid = take(state, channels or [], n, wait=0)
        return _open(n, pys, env, cwd, sid, has_env)
    except ValueError as e:  # take(): why not
        return 409, str(e), ""
    except subprocess.TimeoutExpired:
        return 504, t("long.term.failed", why="pys did not answer within 10 s"), ""
    except OSError as e:
        return 502, t("long.term.failed", why=str(e)), ""


_HAS_ENV: dict[str, bool] = {}


def pys_has_env(pys: str, env: dict | None = None) -> bool:
    """Does this pys take `new --env K=V` (py-screen, 2026-11-19)? Asked once per pys path."""
    if pys not in _HAS_ENV:
        try:
            r = subprocess.run([pys, "new", "--help"], capture_output=True, text=True, timeout=10,
                               encoding="utf-8", errors="replace", env=portable.child_env(env))
            _HAS_ENV[pys] = "--env" in (r.stdout or "")
        except (OSError, subprocess.TimeoutExpired):
            _HAS_ENV[pys] = False
    return _HAS_ENV[pys]


def pys_running(pys: str, env: dict | None = None) -> bool:
    """Does a pys server run (`pys ls --json` answers)? /topys never starts one: a server the buddy
    started would be a child of the buddy's environment and lifetime, and so would every window in it."""
    return subprocess.run([pys, "ls", "--json"], capture_output=True, text=True, timeout=10, encoding="utf-8",
                          errors="replace", env=portable.child_env(env)).returncode == 0  # a hung or missing pys: 504/502


def window_cmd(pys: str, sid: str, has_env: bool) -> list[str]:
    """The pys command that opens a window running the session, BUDDY_LONG=1 through `--env` where pys
    takes it, else (POSIX) through `env`.
    - Windows: claude's exe itself (portable.argv), no shell;
    - POSIX: a login shell (the PO, 2026-11-19): `$SHELL -lc 'claude --resume <id>; exec $SHELL -l'`, so
      claude is found on the login PATH (one set only in .zshrc too), and the shell stays when it exits,
      as the old `buddy-long take` window did. The id is quoted (shlex), never raw; no $SHELL: the
      login shell from the passwords database, else /bin/sh."""
    env = ["--env", "BUDDY_LONG=1", "--"] if has_env else ["--", "env", "BUDDY_LONG=1"]
    if portable.WINDOWS:
        return [pys, "new", "--select", *env, *portable.argv(["claude", "--resume", sid])]
    import shlex
    shell = os.environ.get("SHELL") or _login_shell() or "/bin/sh"
    script = f"claude --resume {shlex.quote(sid)}; exec {shlex.quote(shell)} -l"
    return [pys, "new", "--select", *env, shell, "-lc", script]


def _login_shell() -> str:
    """The user's login shell from the passwords database (a launchd agent or a service may run with no
    SHELL; the review, 2026-11-19)."""
    try:
        import pwd
        return pwd.getpwuid(os.getuid()).pw_shell or ""
    except (ImportError, KeyError, OSError):
        return ""


def _open(n: int, pys: str, env: dict | None, cwd: str, sid: str, has_env: bool) -> tuple[int, str, object]:
    try:
        r = subprocess.run(window_cmd(pys, sid, has_env), cwd=cwd, capture_output=True, text=True, timeout=10,
                           encoding="utf-8", errors="replace", env=portable.child_env(env))
    except subprocess.TimeoutExpired:
        return 504, t("long.term.failed", why="pys did not answer within 10 s"), ""
    except OSError as e:
        return 502, t("long.term.failed", why=str(e)), ""
    try:
        wid = json.loads(r.stdout)["wid"]
    except (ValueError, KeyError, TypeError):
        return 502, t("long.term.failed", why=(r.stderr or r.stdout).strip() or "pys new"), ""
    log.info("long story #%s: resumed in pys window %s", n, wid)
    return 200, t("long.term.opened", n=n, w=wid, cwd=cwd), wid


def _take_then_open(state: Path, channels: list, n: int, pys: str, env: dict | None, has_env: bool) -> None:
    """/topys while the buddy holds it: hand over, wait for its turn, open the window; said in its threads."""
    try:
        cwd, sid = take(state, channels, n)
        _, said, _ = _open(n, pys, env, cwd, sid, has_env)
    except ValueError as e:
        said = str(e)
    story = L.load(state, n)
    if story:
        L._post(channels, story, said, title=t("long.title.handover"))


# --- the owner's commands: /status, /handover, /frompys, buddy-long take --------------------

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
        if portable.WINDOWS:  # os.kill there is TerminateProcess, a hard kill, not an end between turns
            return t("long.takeback.windows")
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
