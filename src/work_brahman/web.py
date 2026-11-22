"""The web workbench: a third frontend beside Lark and Telegram, local only.

`buddy serve` also listens on http://127.0.0.1:<port> ([web] port in buddy.toml). The page
reads the files the buddy already writes (run records, asks, the scheduler's pause), so
the web keeps no state of its own:

    GET  /            the workbench (static/index.html)
    GET  /tokens.css  the design tokens (design/tokens.css)
    GET  /api/data    workflows, recent runs, asks, pause, and each kind's SOP (runs.SOPS)
    GET  /api/chat    the web chat: my messages, the buddy's answers, mirrors from the apps,
                      and the topics (topics.py); a message's "thread" is its topic, "" = main
    POST /api/chat    {"text": ..., "thread": topic id or "", "reply_to": a question's id}: a message, as one typed in Lark
    POST /api/topic   {"name": ...}: a new topic, opened in Lark (a thread) and here (a tab)
    POST /api/answer  {"ask": id, "choice": key}: an answer, as a button press in a chat is
    POST /api/stop    ■ STOP, the same as /stop in a chat
    POST /api/resume
    POST /api/short   {"n": 12, "what": "stop" | "reply" | "a1.0", "text": ...}: ■ Stop, 💬 Reply
                      or an option of question 1 on a short story, as under its card in a chat

The Setup tab (settings.py; the same as `buddy tui`): the doctor, the config, the peers, the log.
No secret is ever sent to the page; it sends a new one, or "-" to remove one.

    GET  /api/setup          the config as shown (secrets masked), the peers, the peer log
    POST /api/setup/doctor   {"full": bool}: the checks (full: calls the chat apps and the peers)
    POST /api/setup/config   {"changes": {key: value}}: written with backups; used after a restart
    POST /api/setup/peers    {"cfg": peers.toml as GET gave it}: written with a backup
    POST /api/setup/check    {"name": ..., "url": ...}: a signed ping to one peer, answered signed
    POST /api/setup/rescheck {"name": ..., "kind": ..., "url": ...}: can this machine reach that resource
    POST /api/setup/pair     {"sender": ..., "restart": bool}: pair someone who wrote to the bot
                             (refused.json, never a typed id), its chat linked; then a restart
    POST /api/setup/link     {"chat": ..., "restart": bool}: link a chat a paired person wrote from
    POST /api/setup/ignore_chat {"chat": ...}: forget such a chat
    POST /api/setup/ignore   {"sender": ...}: forget who wrote (back only if it writes again)
    GET  /sw.js           the Service Worker for web push (static/sw.js)
    GET  /manifest.webmanifest, /icon-{180,192,512}.png   the web app manifest and icons (a Home Screen)
    GET  /api/push        web push: {mode, key (VAPID, when on), browsers}
    POST /api/push/subscribe {"subscription": PushSubscription.toJSON()} · /api/push/unsubscribe {"endpoint"}
    POST /api/push/test   one test push to every subscribed browser: {sent, failed, dropped}
    GET  /api/health      200 {ok, version, commit, up_s, beat_age_s, channels, scheduler}, 503 when a part fails
                          (health.py); without the web token, {ok} only
    GET  /api/changes     What's new: {version, commit, checkout, markdown (CHANGES.md), latest}
    GET  /api/lanes?id=short-story:12   the story's swimlanes: who did what, where, in parallel (lanes.py)
    POST /api/lanes/attach {id, machine, session}   that session's pys window, brought up (pys select)
    POST /api/setup/ping     {"chat": ...}: "🏓 ping <code>" into that linked chat (pings.py); `pong`
                             typed back there closes the loop; GET shows each chat's last ping
                             and who wrote without being paired (with the `buddy allow` line)

Families show in level 1 too: a family with runs() is one workflow, and its stories are its
runs (families/short_story).

Only the loopbacks are bound, 127.0.0.1 and ::1 on the same port (LoopbackServer: "localhost" may
resolve to ::1 first, and another program must not take that one), and two checks keep other
pages in your own browser out: the Host
header must be the local address (a DNS-rebinding page has its own name there), and a POST
needs the X-Buddy header, which a cross-site request can't send without a CORS preflight
that this server never answers.
"""

import copy
import errno
import json
import logging
import os
import re
import subprocess
import shutil
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import lanes, long_stories as longs, pings, portable, settings, topics
from .meter import meter
from .channel import Incoming
from .runs import SOPS, read as read_run
from . import text
from .text import t

log = logging.getLogger("buddy.web")

HERE = Path(__file__).resolve().parent


class _V6(ThreadingHTTPServer):
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)  # ::1 only, never a mapped IPv4
        super().server_bind()


class LoopbackServer(ThreadingHTTPServer):
    """Both loopbacks on one port, 127.0.0.1 and ::1, and never another address (the PO, 2026-11-18).
    "localhost" may resolve to ::1 first (GLib, browsers): bound to 127.0.0.1 alone, another local
    program could take [::1]:<port> and get the page and its login. `twin` is the ::1 server: served
    and stopped with this one. When ::1 can't be had, `twin_error` says why: no IPv6 here (then
    localhost is 127.0.0.1 only, nothing to take), or the port taken on ::1: `twin_taken`, said in
    health's `warn` (the service still runs: refusing to start would let any local user stop it by
    binding [::1] first). Port 0 picks one free on both."""

    def __init__(self, port: int, handler):
        for _ in range(8):
            super().__init__(("127.0.0.1", port), handler)
            self.twin, self.twin_error, self.twin_taken, self._twin_on = None, "", False, False
            try:
                self.twin = _V6(("::1", self.server_port), handler)
                return
            except OSError as e:
                if port == 0 and e.errno == errno.EADDRINUSE:  # a free v4 port taken on v6: pick again
                    self.server_close()
                    continue
                self.twin_error = f"[::1]:{self.server_port}: {e.strerror or e}"
                self.twin_taken = e.errno == errno.EADDRINUSE  # another program listens there
                return
        raise OSError(errno.EADDRINUSE, "no port free on both 127.0.0.1 and ::1 after 8 tries")

    @property
    def warn(self) -> str:
        """"loopback6_taken" when another program holds [::1] on this port (health's `warn`), else ""."""
        return "loopback6_taken" if self.twin_taken else ""

    def serve_forever(self, poll_interval=0.5):
        if self.twin:
            self._twin_on = True
            threading.Thread(target=self.twin.serve_forever, args=(poll_interval,), name="web-v6", daemon=True).start()
        super().serve_forever(poll_interval)

    def shutdown(self):
        if self.twin and self._twin_on:  # never served: shutdown() would wait forever
            self.twin.shutdown()
        super().shutdown()

    def server_close(self):
        if self.twin:
            self.twin.server_close()
        super().server_close()


def _owner(client_port: int, server_port: int) -> bool:
    """Is the connection from the user the buddy runs as? 127.0.0.1 is shared by every user
    and container on the machine (and ssh -L), and the page acts as you; on Linux the kernel
    says who owns the other end. Elsewhere (one user per desktop) the Host and X-Buddy
    checks stand alone."""
    if not sys.platform.startswith("linux"):
        return True
    return portable.peer_uid(client_port, server_port) == os.getuid()
TOKENS = next((p for p in (HERE.parent.parent / "design" / "tokens.css",  # in the repository
                           HERE / "static" / "tokens.css") if p.exists()),  # in a wheel (the kit copies it)
              HERE / "static" / "tokens.css")
DAYS = 3        # how far back the page shows the duties' and jobs' runs (stories and teams: all)
MAX_RUNS = 600
FULL = 40       # stories sent whole each time; older ones are light tiles, fetched when picked
METER_EVERY = 60  # s: the meter reads every record (0.2-0.3 s each window), the page asks every 5 s


CHAT = "web:local"  # the web's chat id, linked to my other chats; the sender id too
KEEP = 300          # messages the web chat keeps


OUTBOX = "web-outbox"  # under state/: what other processes post to the web chat


class _Posts:
    """What any process posts to the web chat: notices, asks, short stories' cards. Built on
    _add(message) -> id and _edit(id, fields), which WebChat does itself and WebRelay hands
    to `buddy serve` through the outbox."""
    name = "web"

    def owns(self, chat: str) -> bool:
        return chat == CHAT

    def post(self, chat: str, markdown: str, *, title: str, subtitle: str = "",
             failed: bool = False, note: str = "", thread: str = "") -> bool:
        return bool(self._add({"title": title, "subtitle": subtitle, "markdown": markdown,
                               "failed": failed, "note": note, "thread": thread}))

    def ask(self, chat: str, markdown: str, *, title: str, options: list[list[str]],
            ask_id: str, failed: bool = False, note: str = "", stop: bool = True) -> str | None:
        return self._add({"title": title, "markdown": markdown, "options": options, "ask": ask_id,
                          "failed": failed, "note": note})

    def settle(self, chat: str, handle: str, markdown: str, *, title: str, line: str,
               failed: bool = False, note: str = "") -> bool:
        return self._edit(handle, {"options": None, "line": line})

    def track(self, chat: str, markdown: str, *, title: str, handle: str | None = None,
              failed: bool = False, done: bool = False,
              buttons: list[tuple[str, str]] = ()) -> str | None:
        m = {"title": title, "markdown": markdown, "failed": failed,
             "buttons": [list(b) for b in buttons]}  # ■ Stop, 💬 Reply: the page draws them
        if handle:
            return handle if self._edit(handle, m) else None
        return self._add(m)

    def open_topic(self, chat: str, topic: dict) -> str | None:
        """A topic is a tab; its handle is the topic's id. A long story's tab too, opened from
        its hook's process (through the outbox) as well as from serve."""
        self._add({"title": f"🧵 {topic['name']}", "thread": topic["id"],
                   "markdown": t("topic.web")})
        return topic["id"]

    def _add(self, m: dict) -> str: ...

    def _edit(self, handle: str, fields: dict) -> bool: ...


class WebRelay(_Posts):
    """The web chat as every process but `buddy serve` sees it (duties, jobs, asks,
    `buddy short`): what they post goes to state/web-outbox/, one file per change, and serve
    applies the files in order every second and when it starts. So serve stays the only
    writer of web-chat.json, and what is posted while serve is down shows up once it runs.
    Ids are made here, so a card can be changed again (a short story's next mark) before
    serve has seen it."""

    def __init__(self, state: Path):
        self.dir = state / OUTBOX

    def _add(self, m: dict) -> str:
        m = {"id": f"{time.time_ns()}", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "from": "buddy", **m}
        self._put({"op": "add", "m": m})
        return m["id"]

    def _edit(self, handle: str, fields: dict) -> bool:
        self._put({"op": "edit", "id": handle, "fields": fields})
        return True

    def _put(self, change: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        n = time.time_ns()
        tmp = self.dir / f".{n}.tmp"  # not *.json: serve skips it until it is whole
        tmp.write_text(json.dumps(change, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.dir / f"{n}.json")


class WebChat(_Posts):
    """The Channel for the web: a chat that lives in state/web-chat.json.

    It joins my linked chats in `buddy serve`, so a message typed here goes to the same
    agent session as Lark and Telegram and is mirrored to them, and what is asked or
    mirrored there shows up here. Other processes (duties, jobs, `buddy short`) post to it
    through WebRelay; listen() applies what they left in the outbox.
    """

    def __init__(self, state: Path):
        self.path = state / "web-chat.json"
        self.outbox = state / OUTBOX
        self.lock = threading.Lock()
        try:
            self.msgs = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.msgs = []
        self.on_message = None

    def listen(self, on_message) -> None:
        """The web server hands typed messages to on_message; here, the outbox is drained."""
        self.on_message = on_message
        while True:
            try:
                self.drain()
            except Exception:
                log.exception("web outbox")
            time.sleep(1)

    def drain(self) -> int:
        """Apply what other processes posted, in the order they posted it. Returns how many."""
        done = 0
        for f in sorted(self.outbox.glob("*.json"), key=lambda f: int(f.stem)):
            try:
                c = json.loads(f.read_text(encoding="utf-8"))
                if c["op"] == "add":
                    self._add(c["m"])  # its id and time came with it
                elif c["op"] == "edit":
                    self._edit(c["id"], c["fields"])  # gone (older than KEEP): nothing to change
                f.unlink()
                done += 1
            except (OSError, ValueError, KeyError) as e:
                log.warning("web outbox: %s left aside: %s", f.name, e)
                f.rename(f.with_suffix(".bad"))
        return done

    def say(self, text: str, thread: str = "", reply_to: str = "") -> None:
        """A message typed on the page; thread: the topic's tab it was typed in; reply_to: the
        question (prompt()) it answers, which is then marked answered."""
        if reply_to and not any(m["id"] == reply_to and m.get("prompt") for m in self.history()):
            reply_to = ""  # not an open question: an ordinary message
        self._add({"from": "you", "markdown": text, "thread": thread, **({"reply_to": reply_to} if reply_to else {})})
        if reply_to:
            self._edit(reply_to, {"prompt": False, "line": t("chat.replied")})
        if self.on_message:
            self.on_message(Incoming(CHAT, f"web:{time.time_ns()}", CHAT, text, reply_to=reply_to, thread=thread))

    def prompt(self, chat: str, text: str) -> str | None:
        """A question answered by a reply (a long story's task, a folder typed by hand): the page
        puts ↩ Reply on it and makes the newest open one the reply box's target, as Telegram's
        force reply does; the answer comes back as Incoming(reply_to=its id)."""
        return self._add({"markdown": text, "prompt": True})

    def history(self) -> list[dict]:
        with self.lock:
            return list(self.msgs)

    # Channel methods (post, ask, settle, track: _Posts)
    def reply(self, msg: Incoming, text: str) -> None:
        self._add({"markdown": text, "thread": msg.thread})

    def progress(self, msg: Incoming, text: str, handle: str | None = None) -> str | None:
        if handle:
            return handle if self._edit(handle, {"markdown": text, "working": True}) else None
        return self._add({"markdown": text, "working": True, "thread": msg.thread})

    def answer(self, msg: Incoming, handle: str | None, markdown: str, *,
               failed: bool = False, note: str = "") -> None:
        m = {"markdown": markdown, "failed": failed, "note": note, "working": False}
        if not (handle and self._edit(handle, m)):
            self._add({**m, "thread": msg.thread})

    def stop_button(self, chat: str, markdown: str) -> bool:
        return bool(self._add({"markdown": markdown, "resume": markdown.startswith("■")}))

    def _add(self, m: dict) -> str:
        with self.lock:
            m = {"id": f"{time.time_ns()}", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "from": "buddy", **m}
            self.msgs = (self.msgs + [m])[-KEEP:]
            self._save()
            return m["id"]

    def _edit(self, handle: str, fields: dict) -> bool:
        with self.lock:
            for m in self.msgs:
                if m["id"] == handle:
                    m.update(fields)
                    self._save()
                    return True
        return False

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.msgs, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)


def pys_path() -> str:
    """Where pys is, or "" when this machine has none (the page then shows no attach/resume)."""
    from .long_live import pys_path as found
    return found()


def page(public: float = 0) -> bytes:
    """The workbench page with its words in every language (text.py's page.* keys); it starts
    in the buddy's, and its EN / 中文 switch changes them in place. In the public view the server
    marks the body, so the page's own state can't drop the badge (privacy.py)."""
    html = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    if public:  # the exact tags, so a mention of them elsewhere in the file is never the one marked
        for tag, marked in (('<body data-view="private">', '<body data-view="public" class="public">'),
                            ('data-t="view.off"', 'data-t="view.on"')):
            if html.count(tag) != 1:
                raise RuntimeError(f"the page has {html.count(tag)} {tag!r}: the public view can't mark it")
            html = html.replace(tag, marked)
    html = html.replace('<html lang="en">', f'<html lang="{text.language()}">', 1)
    words = {lang: text.page(lang) for lang in text.LANGUAGES}
    html = html.replace("const TEXTS = {};", f"const TEXTS = {json.dumps(words, ensure_ascii=False)};", 1)
    return html.encode()


class Web:
    def __init__(self, port: int, state: Path, bot, scheduler, cfg: dict, home: str,
                 chat: WebChat | None = None, families: dict | None = None, config: Path | None = None):
        self.port, self.state, self.bot, self.scheduler, self.home = port, state, bot, scheduler, home
        self.config = config  # buddy.toml, for the Setup tab (None: where `buddy init` puts it)
        self.cfg = cfg  # as the buddy started with it ([notify] webpush, read at start like every setting)
        self.chat = chat
        self.started = time.time()  # GET /api/health: up_s
        self.closed = threading.Event()  # set it to end every open event stream (tests; a stopping buddy)
        self.families = [f for f in (families or {}).values() if hasattr(f, "runs")]
        self.workflows = [{"kind": kind, "name": name, "schedule": t.get("schedule", ""),
                           "about": t.get("prompt", "")[:120]}
                          for kind, section in (("job", "jobs"), ("duty", "duties"))
                          for name, t in cfg.get(section, {}).items()]

    def start(self) -> None:
        web = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # quiet: the page polls every few seconds
                pass

            def do_GET(self):
                if not self._local():
                    return
                path = self.path.split("?")[0]
                if not self._authed(path):  # BUDDY_WEB_TOKEN (webauth.py)
                    return
                if path == "/":
                    return self._send(200, page(self._public()), "text/html")
                if path.startswith("/media/"):  # the corner's film and its poster
                    name = path[7:]
                    f = HERE / "static" / "media" / name
                    kind = {".mp4": "video/mp4", ".jpg": "image/jpeg"}.get(f.suffix)
                    if not re.fullmatch(r"[\w.-]+", name) or not kind or not f.is_file():
                        return self._send(404, b"not found", "text/plain")
                    return self._send(200, f.read_bytes(), kind)
                if path == "/manifest.webmanifest" or re.fullmatch(r"/icon-(180|192|512)\.png", path):
                    # pinned to a Home Screen: the web app manifest and its icons (static/)
                    kind = "application/manifest+json" if path.endswith(".webmanifest") else "image/png"
                    return self._send(200, (HERE / "static" / path[1:]).read_bytes(), kind)
                if path == "/sw.js":  # the Service Worker for web push (static/sw.js), scope /
                    return self._send(200, (HERE / "static" / "sw.js").read_bytes(), "text/javascript")
                if path == "/api/push":  # web push: on or off, this buddy's VAPID key, how many browsers
                    from . import webpush
                    from . import notify
                    m = webpush.mode(notify.config() or web.cfg)  # followed live
                    rows = webpush.subs(web.state)
                    return self._json({"mode": m, "key": webpush.public_key(web.state) if m != "off" else "",
                                       "browsers": len(rows), "list": [webpush.describe(r) for r in rows]})
                if path == "/api/health":  # health.py: 200 when it works, 503 when a part doesn't
                    from . import health, webauth
                    got = health.report(web.state, web.bot, web.scheduler, web.started)
                    if warn := getattr(web, "server", None) and web.server.warn:
                        got["warn"] = warn  # another program on [::1]:<port>; the buddy still works
                    if not webauth.ok(self.headers):
                        got = {k: got[k] for k in ("ok", "instance", "warn") if k in got}  # works or not, which process
                    return self._send(200 if got["ok"] else 503, json.dumps(got).encode(), "application/json")
                if path == "/api/events":  # the event stream (events.py): what web push sends, as SSE
                    return self._events()
                if path == "/tokens.css":
                    return self._send(200, TOKENS.read_bytes() if TOKENS.exists() else b"", "text/css")
                if path == "/api/data":
                    return self._json(web.public_data(web.data()) if self._public() else web.marked(web.data()))
                if path == "/api/run":  # one story or team whole: a light tile was picked
                    key = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                    run = web.public_detail(key) if self._public() else web.detail(key)
                    # every answer says its view, so a public page never draws a private one (viewEnded)
                    return self._json({**run, "public": bool(self._public())}) if run else self._send(404, b"no such run", "text/plain")
                if path == "/api/changes":  # What's new (changes.py): the notes and the running version
                    from . import changes
                    sha, checkout = changes.commit()
                    return self._json({"version": changes.version(), "commit": sha, "checkout": checkout,
                                       "markdown": changes.text(), "latest": (changes.released() or ("", ""))[0]})
                if path == "/api/setup":
                    if self._public():  # the config, the peers and their hosts: never in the public view
                        return self._send(403, b"hidden in the public view", "text/plain")
                    return self._json(web.setup(show_all=parse_qs(urlparse(self.path).query).get("all", [""])[0] == "1"))
                if path == "/api/args":  # ⚙ debug: arguments: a story's copied tool arguments (tooluse.py), this machine only
                    q = parse_qs(urlparse(self.path).query)
                    key, step = q.get("id", [""])[0], q.get("step", [None])[0]
                    kind, _, n = key.partition(":")
                    if self._public() or kind != "short-story" or not n.isdigit():
                        return self._send(403, b"arguments: a short story, in the private view only", "text/plain")
                    from . import tooluse
                    return self._json({"id": key, "step": step, "calls": tooluse.read_args(web.state, int(n), step)[-50:]})
                if path == "/api/lanes":  # who did what, where, in parallel (lanes.py): a story's swimlanes
                    key = parse_qs(urlparse(self.path).query).get("id", [""])[0]
                    kind, _, n = key.partition(":")
                    got = (lanes.of_story(web.state, int(n)) if kind == "short-story" and n.isdigit()
                           and not (self._public() and web.private(key) is not False) else None)
                    if got and self._public():
                        got = web.public_lanes(got)
                    got = {**got, "public": bool(self._public())} if got else got
                    return self._json(got) if got else self._send(404, b"lanes are for short stories", "text/plain")
                if path == "/api/chat":
                    if since := self._public():
                        return self._json(web.public_chat(self._chat(), since))
                    return self._json(self._chat())
                self._send(404, b"not found", "text/plain")

            def _chat(self) -> dict:
                return {"on": web.chat is not None, "public": False,
                        "messages": web.chat.history() if web.chat else [],
                        "topics": [t for t in topics.all_topics(web.state).values()
                                   if CHAT in t["threads"] and not t.get("closed")]
                        + [{"id": x["threads"][CHAT], "name": longs.label(x), "where": "long",
                            "threads": x["threads"], "story": x["id"]} for x in longs.all_stories(web.state)
                           if CHAT in x["threads"]]}

            def _public(self) -> float:
                """When this browser's public view began, or 0 (privacy.py: a cookie, at most 3 h)."""
                from . import privacy
                return privacy.view_since(self.headers.get("Cookie") or "")

            def do_POST(self):
                if not self._local():
                    return
                if self.path.split("?")[0] == "/login":  # the token typed into the login page (webauth.py)
                    return self._login()
                if not self._authed(self.path.split("?")[0]):
                    return
                if self.headers.get("X-Buddy") != "1":
                    return self._send(403, b"missing X-Buddy header", "text/plain")
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._send(400, b"bad json", "text/plain")
                path = self.path.split("?")[0]
                if path == "/api/demo":  # {"id": "short-story:12" | "team:kit", "value": on|off}: the ⭐ tag
                    from . import privacy
                    if self._public():
                        return self._send(403, b"hidden in the public view", "text/plain")
                    kind, _, ident = str(body.get("id", "")).partition(":")
                    value = str(body.get("value", ""))
                    if kind not in privacy.KINDS or value not in ("on", "off"):
                        return self._send(400, b"id: short-story:N, long-story:N or team:NAME; value: on or off", "text/plain")
                    rec = privacy.demo(web.state, privacy.KINDS[kind], ident, value)
                    if rec is None:
                        return self._send(404, b"no such story or team", "text/plain")
                    return self._json({"id": body["id"], "demo": bool(rec.get("demo"))})
                if path == "/api/private":  # {"id": "short-story:12" | "team:kit", "value": on|off|auto}: by hand
                    from . import privacy
                    if self._public():  # marking is for the owner's own view
                        return self._send(403, b"hidden in the public view", "text/plain")
                    kind, _, ident = str(body.get("id", "")).partition(":")
                    value = str(body.get("value", ""))
                    if kind not in privacy.KINDS or value not in ("on", "off", "auto") or (kind == "team" and value == "auto"):
                        return self._send(400, b"id: short-story:N, long-story:N or team:NAME; value: on, off or auto", "text/plain")
                    rec = privacy.mark(web.state, privacy.KINDS[kind], ident, value)
                    if rec is None:
                        return self._send(404, b"no such story or team", "text/plain")
                    return self._json({"id": body["id"], "private": privacy.is_private(rec), "by": rec.get("private_by", "")})
                if path == "/api/view":  # the public view on or off, for this browser (privacy.py)
                    from . import privacy
                    self.send_response(204)
                    self.send_header("Set-Cookie", privacy.view_cookie(bool(body.get("public"))))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if self._public() and (path.startswith("/api/setup/") or path == "/api/lanes/attach"):
                    return self._send(403, b"hidden in the public view", "text/plain")
                if path.startswith("/api/setup/"):
                    got = web.setup_post(path[11:], body if isinstance(body, dict) else {})
                    return self._json(got) if got is not None else self._send(404, b"not found", "text/plain")
                if path == "/api/chat":
                    text = str(body.get("text", "")).strip()
                    if not web.chat or not text:
                        return self._send(400, b"no chat, or nothing to say", "text/plain")
                    thread = str(body.get("thread") or "")
                    if thread and not (topics.find(web.state, CHAT, thread) or longs.find(web.state, CHAT, thread)):
                        return self._send(400, b"no such topic", "text/plain")
                    web.chat.say(text[:8000], thread, str(body.get("reply_to") or ""))
                    return self._json({"ok": True})
                if path == "/api/topic":
                    if not web.chat:
                        return self._send(400, b"no web chat", "text/plain")
                    t = web.bot.new_topic(CHAT, str(body.get("name", "")).strip(), "web")
                    return self._json({"ok": True, "topic": t})
                text = None
                if path == "/api/answer":
                    action = f"ask:{body.get('ask', '')}:{body.get('choice', '')}"
                elif path == "/api/short":
                    n, what = str(body.get("n", "")), body.get("what")
                    text = str(body.get("text") or "").strip()[:4000] or None
                    option = isinstance(what, str) and re.fullmatch(r"a\d+\.\d+", what)
                    if not n.isdigit() or not (what in ("stop", "reply") or option) \
                            or (what == "reply" and not text):
                        return self._send(400, b"n, what (stop|reply|a<q>.<i>), and text for a reply",
                                          "text/plain")
                    action = f"short:{n}:{what}"
                elif path == "/api/push/test":  # one test push to every subscribed browser (the Setup tab)
                    from . import webpush
                    if not webpush.subs(web.state):
                        return self._send(409, "no browser is subscribed: turn web push on (Config), reload, then 🔔"
                                          .encode(), "text/plain")
                    return self._json(webpush.send(web.state, "brahman · push test",
                                                   "If you see this, web push works.", "/"))
                elif path in ("/api/push/subscribe", "/api/push/unsubscribe"):  # web push (webpush.py)
                    from . import webpush
                    if path.endswith("/unsubscribe"):
                        return self._json({"ok": webpush.unsubscribe(web.state, str(body.get("endpoint", "")))})
                    from . import notify
                    if webpush.mode(notify.config() or web.cfg) == "off":
                        return self._send(409, b"web push is off: [notify] webpush = \"ends\" or \"all\"", "text/plain")
                    why = webpush.subscribe(web.state, body.get("subscription"), self.headers.get("User-Agent", ""))
                    return self._json({"ok": True}) if not why else self._send(400, why.encode(), "text/plain")
                elif path == "/api/lanes/attach":  # a session lane's pys window, brought up (lanes.py)
                    code, out = web.lane_attach(str(body.get("id", "")), str(body.get("machine", "")),
                                                str(body.get("session", "")))
                    return self._json(out) if code == 200 else self._send(code, out.encode(), "text/plain")
                elif path == "/api/long":  # attach to its pys window, or resume it in a new one
                    code, out = web.long(str(body.get("n", "")), body.get("what"))
                    return self._json(out) if code == 200 else self._send(code, out.encode(), "text/plain")
                elif path in ("/api/stop", "/api/resume"):
                    action = path.rsplit("/", 1)[-1]
                else:
                    return self._send(404, b"not found", "text/plain")
                web.press(action, text)
                self._json({"ok": True, "action": action})

            def _local(self) -> bool:
                from . import webauth  # loopback, or an allowlisted host while a token is set
                if not webauth.host_ok(self.headers.get("Host") or "", webauth.hosts(web.cfg), webauth.token()):  # owner secret: loopback only
                    self._send(421, b"use http://127.0.0.1 (or an address in [web] hosts, with BUDDY_WEB_TOKEN set)",
                               "text/plain")
                    return False
                if not _owner(self.client_address[1], self.server.server_port):
                    self._send(403, b"only the user the buddy runs as", "text/plain")
                    return False
                return True

            def _authed(self, path: str) -> bool:
                """BUDDY_WEB_TOKEN: the cookie or a Bearer token; ?token=… once sets the cookie. A
                device token (events.py) reads GET /api/events and nothing else."""
                from . import events, webauth
                if path not in webauth.PUBLIC and events.device(web.state, self.headers) is not None:
                    if self.command == "GET" and path == "/api/events":
                        return True
                    self._send(403, b"a device token reads GET /api/events only", "text/plain")
                    return False
                if path in webauth.PUBLIC or webauth.ok(self.headers):  # the owner secret first (webauth.py)
                    return True
                given = parse_qs(urlparse(self.path).query).get("token", [""])[0]
                if self.command == "GET" and given and (cookie := webauth.login(given)):
                    self.send_response(303)
                    self.send_header("Set-Cookie", cookie)
                    self.send_header("Location", webauth.strip_token(self.path))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return False
                if self.command == "GET" and not path.startswith("/api/"):
                    self._send(401, webauth.login_page(webauth.strip_token(self.path), bad=bool(given)), "text/html")
                else:
                    self._send(401, b"a secret is needed: the owner secret, or BUDDY_WEB_TOKEN", "text/plain")
                return False

            def _events(self) -> None:
                """SSE: the events after Last-Event-ID (or ?last=), then each new one as it is written,
                a keep-alive comment between. The public view (or ?public=1) gets only the events of
                stories and teams the page shows publicly (web.public_event)."""
                from . import events, privacy
                q = parse_qs(urlparse(self.path).query)
                last = events.last_id(self.headers.get("Last-Event-ID") or q.get("last", [""])[0])
                public = bool(self._public()) or q.get("public", [""])[0] == "1"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
                self.end_headers()
                self.connection.settimeout(events.STALL)  # a reader that stalls ends its stream; it resumes later
                if last is None:  # a first connection: from now on
                    rows = events.read(web.state)
                    last = rows[-1]["id"] if rows else 0
                    newer, gap = [], False
                else:
                    newer, gap = events.after(web.state, last)
                try:
                    self.wfile.write(f"retry: 3000\n\n".encode())
                    if gap:
                        self.wfile.write(f"event: gap\ndata: {json.dumps({'after': last})}\n\n".encode())
                    quiet = time.monotonic()
                    while not web.closed.is_set():
                        for ev in newer:
                            last = ev["id"]
                            if not public:
                                self.wfile.write(events.frame(ev))
                            elif web.public_event(ev):  # as the public page: scrubbed (privacy.scrub)
                                self.wfile.write(events.frame(privacy.scrub(ev)))
                                quiet = time.monotonic()
                        if time.monotonic() - quiet >= events.KEEPALIVE:
                            self.wfile.write(b": keep-alive\n\n")
                            quiet = time.monotonic()
                        self.wfile.flush()
                        web.closed.wait(events.POLL)
                        newer, _ = events.after(web.state, last)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass  # the client went away
                self.close_connection = True

            def _login(self) -> None:
                from . import webauth
                n = min(int(self.headers.get("Content-Length") or 0), 4096)
                form = parse_qs(self.rfile.read(n).decode(errors="replace"))
                nxt = form.get("next", ["/"])[0]
                nxt = nxt if nxt.startswith("/") and not nxt.startswith("//") else "/"
                cookie = webauth.login(form.get("token", [""])[0]) if webauth.required() else None
                if not cookie:
                    return self._send(401, webauth.login_page(nxt, bad=True), "text/html")
                self.send_response(303)
                self.send_header("Set-Cookie", cookie)
                self.send_header("Location", nxt)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def _json(self, obj):
                self._send(200, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

            def _send(self, code: int, body: bytes, kind: str):
                self.send_response(code)
                self.send_header("Content-Type", f"{kind}; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Frame-Options", "DENY")  # no other page may frame it (clickjacking)
                self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = self.server = LoopbackServer(self.port, Handler)  # 127.0.0.1 and ::1
        threading.Thread(target=server.serve_forever, name="web", daemon=True).start()
        log.info("web workbench on http://127.0.0.1:%d%s", self.port, " and http://[::1]:%d" % self.port if server.twin else "")
        if server.twin_error:
            (log.warning if "in use" in server.twin_error else log.info)("web workbench: not on %s", server.twin_error)

    def _config_path(self) -> Path:
        from . import install
        return self.config or install.config_path(None)

    def setup(self, show_all: bool = False) -> dict:
        """The Setup tab: what `buddy tui` shows, secrets masked; show_all: Telegram and Lark too (zh-CN)."""
        from . import configure
        path = self._config_path()
        s = configure.load(path) if path.exists() else None
        linked = list(self.bot.linked.chats) if self.bot else []
        # paired: by the running buddy, or written in the config since (used after a restart)
        allowed = set(self.bot.allowed if self.bot else ()) | set(s["allowed"] if s else ())
        return {"path": str(path), "config": settings.view(s, show_all) if s else None, "restart": settings.RESTART,
                "intl_hidden": bool(s and settings.hidden(s, show_all)),
                "peers": settings.peers(), "log": settings.log(last=300),
                "chats": [{"chat": c, "channel": next((ch.name for ch in self.bot.channels if ch.owns(c)), "")}
                          for c in linked] if self.bot else [],
                "pings": pings.pings(self.state), "refused": pings.refused(self.state, allowed),
                "unlinked": pings.unlinked(self.state, set(linked) | set(s["chats"] if s else ()))}

    def setup_post(self, what: str, body: dict) -> dict | None:
        from . import configure
        path = self._config_path()
        if what == "doctor":  # the checks of what this running buddy has (its environment)
            return {"rows": settings.doctor(path, offline=not body.get("full"))}
        if what == "config":
            if not path.exists():
                return {"ok": False, "notes": [f"not set up: {path} is missing; run `buddy init` first"]}
            before = configure.load(path)
            s = copy.deepcopy(before)
            changes = body.get("changes") if isinstance(body.get("changes"), dict) else {}
            for key, value in changes.items():
                why = settings.change(s, str(key), value)
                if why:
                    return {"ok": False, "notes": [why]}
            ok, notes = settings.write(path, s, before)
            return {"ok": ok, "notes": notes, "config": settings.view(configure.load(path))}
        if what == "peers":
            ok, said = settings.save_peers(body.get("cfg") if isinstance(body.get("cfg"), dict) else {})
            return {"ok": ok, "said": said, "peers": settings.peers()}
        if what == "check":
            return settings.check_peer(str(body.get("name", "")), str(body.get("url", "")))
        if what == "rescheck":  # a resource, reached from this machine (as typed, else as written)
            return settings.check_resource(str(body.get("name", "")), str(body.get("kind", "")), str(body.get("url", "")))
        if what == "pair":  # the quick pair: only someone who wrote to the bot; then, if asked, a restart
            ok, notes = settings.pair(path, self.state, str(body.get("sender", "")))
            if ok and body.get("restart"):
                notes.append(settings.restart_soon())
            return {"ok": ok, "notes": notes, "restarting": bool(ok and body.get("restart"))}
        if what == "link":  # a chat a paired person wrote from: linked; then, if asked, a restart
            ok, notes = settings.link(path, self.state, str(body.get("chat", "")))
            if ok and body.get("restart"):
                notes.append(settings.restart_soon())
            return {"ok": ok, "notes": notes, "restarting": bool(ok and body.get("restart"))}
        if what == "ignore_chat":
            chat = str(body.get("chat", ""))
            return {"ok": pings.unlink_forget(self.state, chat), "notes": [f"{chat} ignored"]}
        if what == "ignore":  # who wrote, not wanted: off the list (back only if it writes again)
            sender = str(body.get("sender", ""))
            return {"ok": pings.forget(self.state, sender), "notes": [f"{sender} ignored"]}
        if what == "ping":
            chat = str(body.get("chat", ""))
            if not self.bot or chat not in self.bot.linked.chats:
                return {"ok": False, "why": "not one of the linked chats (buddy.toml [me] chats)"}
            return pings.send(self.state, self.bot.channels, chat)
        return None

    def press(self, action: str, text: str | None = None) -> None:
        """A press on the page, handled as one in a chat (the person at this machine is trusted).
        text: what was typed with it (a short story's reply)."""
        msg = Incoming(CHAT if self.chat else self.home, f"web:{time.time_ns()}", CHAT, text, action)
        side = self.chat or type("WebSide", (), {"name": "web", "owns": lambda s, c: False})()
        threading.Thread(target=self.bot._press, args=(side, msg), daemon=True).start()

    def meter(self) -> dict:
        """The last DAYS days, with all of it as "all"; counted again at most every METER_EVERY s."""
        at, m = getattr(self, "_meter", (0.0, None))
        if m is None or time.time() - at > METER_EVERY:
            m = meter(self.state) | {"all": meter(self.state, None)}
            self._meter = (time.time(), m)
        return m

    def lane_attach(self, sid: str, machine: str, session: str) -> tuple[int, dict | str]:
        """A session lane into the terminal: `pys select` its window, only on this machine and only
        while the process that marked from it still runs there (a window number is reused)."""
        kind, _, n = sid.partition(":")
        v = lanes.of_story(self.state, int(n)) if kind == "short-story" and n.isdigit() else None
        row = next((r for r in (v or {}).get("lanes", []) if r["machine"] == machine and r["session"] == session
                    and r["window"]), None)
        if row is None:
            return 404, "no pys window is known for that session"
        if not row["here"]:
            return 409, f"that session runs on {machine}: attach from its own workbench, or `pys attach` there"
        if not row["live"]:
            return 409, f"window {row['window']} no longer holds that session"
        pys = pys_path()
        if not pys:
            return 409, "pys is not installed (uv tool install py-screen)"
        try:
            r = subprocess.run([pys, "select", row["window"]], capture_output=True, text=True, timeout=10, encoding="utf-8",
                               errors="replace", env=portable.child_env())  # never the buddy's .env into pys
        except subprocess.TimeoutExpired:
            return 504, "pys did not answer within 10 s"
        except OSError as e:
            return 502, f"pys could not run: {e}"
        return (200, {"ok": True, "window": row["window"]}) if r.returncode == 0 else \
            (502, r.stderr.strip() or "pys select failed")

    def long(self, n: str, what) -> tuple[int, dict | str]:
        """A long story into a terminal, through pys (long_live.to_terminal: the page's Resume and
        Attach, and the chats' /topys). Codex: take is Claude's only."""
        s = longs.load(self.state, int(n)) if n.isdigit() else None
        if s is None or what not in ("attach", "resume", "topys", "frompys"):
            return 400, "n (a long story's number) and what (topys|frompys|attach|resume)"
        from . import long_live
        if what == "frompys":  # back to the buddy, as /frompys: it waits for a terminal to let go
            if not self.bot:
                return 409, "the buddy is not running here"
            said = long_live.takeback(self.state, self.bot.channels, s["id"],
                                      longs.pick(getattr(self.bot, "long_engines", {}), "claude-code", s.get("mode")))
            return 200, {"ok": True, "said": said}
        where = {"state": self.state, "channels": getattr(self.bot, "channels", []) if self.bot else []}
        code, said, window = long_live.to_terminal(s, "attach" if what == "topys" else what, pys_path(), **where)
        if what == "topys" and code == 409 and not longs.held(s):  # no pys window holds it: take it over
            code, said, window = long_live.to_terminal(s, "resume", pys_path(), **where)
        return (200, {"ok": True, "window": window, "said": said}) if code in (200, 202) else (code, said)

    def detail(self, key: str) -> dict | None:
        """A family's run whole, by its id ("short-story:12", "team:kit")."""
        family, _, rest = key.partition(":")
        f = next((f for f in self.families if f.name == family and hasattr(f, "detail")), None)
        try:
            return f.detail(rest) if f and rest else None
        except Exception:
            log.exception("family %s: detail %s failed", family, rest)
            return None

    def record(self, key: str) -> dict | None:
        """The story or team a run id names ("short-story:12", "long-story:3", "team:kit"), or None."""
        from . import short_stories, teams
        kind, _, n = key.partition(":")
        if kind == "team":
            return teams.load(self.state, n)
        if kind in ("short-story", "long-story") and n.isdigit():
            return (short_stories if kind == "short-story" else longs).load(self.state, int(n))
        return None

    def private(self, key: str) -> bool | None:
        """A story's or a team's run id ("short-story:12", "long-story:3", "team:kit") is private;
        None: not one of those (a duty's run, a chat)."""
        from . import privacy
        return privacy.private_ref(self.state, key)

    def marked(self, d: dict) -> dict:
        """/api/data with each story's and team's mark, for the page's 🔒 / 🔓 button; "public": False
        so the page always learns the view from the data it got."""
        for r in d["runs"]:
            if (p := self.private(str(r.get("id", "")))) is not None:
                r["private"] = p
        return {**d, "runs": self.starred(d["runs"]), "public": False}

    def starred(self, runs: list[dict]) -> list[dict]:
        """Each story's and team's ⭐ demo tag, for the page's ⭐ Demo filter (privacy.demo)."""
        for r in runs:
            if str(r.get("id", "")).split(":")[0] in ("short-story", "long-story", "team"):
                r["demo"] = bool((self.record(str(r["id"])) or {}).get("demo"))
        return runs

    def public_data(self, d: dict) -> dict:
        """/api/data for the public view: only the stories and teams judged or marked public, without
        their machine fields, and none that still leaks (privacy.leaks); no duty run and no ask at all,
        since nothing judges them (review, 2026-11-14: a code-review run carried chat ids and an IP)."""
        from . import privacy
        runs = [privacy.scrub(r) for r in d["runs"] if self.private(str(r.get("id", ""))) is False]
        runs = [r for r in runs if not privacy.leaks(r)]
        total = sum(1 for r in d["runs"] if str(r.get("id", "")).split(":")[0] in ("short-story", "long-story", "team"))
        return {**d, "runs": self.starred(runs), "asks": [], "public": True, "total": total}  # a count only: "10 of 388"

    def public_event(self, ev: dict) -> bool:
        """May the public view see this event? Only one about a story or team the page shows publicly
        (judged or marked public) and that doesn't leak; never a question or post about nothing."""
        from . import privacy
        return bool(ev.get("ref")) and self.private(ev["ref"]) is False and not privacy.leaks(ev)

    def public_detail(self, key: str) -> dict | None:
        """/api/run in the public view: a public story or team only, scrubbed, and not when it leaks."""
        from . import privacy
        if self.private(key) is not False:
            return None
        run = self.detail(key)
        run = privacy.scrub(run) if run else None
        return run if run and not privacy.leaks(run) else None

    def public_lanes(self, got: dict) -> dict | None:
        """/api/lanes in the public view: machines as "this machine" or "a peer", no windows, no leaks."""
        from . import privacy
        here, peer = t("page.lanes.this_machine"), t("page.lanes.a_peer")
        got = privacy.scrub(got)
        for row in got.get("lanes", []):
            row["machine"] = here if row.get("here") else peer
            row.pop("live", None)
            for b in row.get("bars", []):
                b["machine"] = row["machine"]
        return None if privacy.leaks(got) else got

    def public_chat(self, chat: dict, since: float) -> dict:
        """The chat in the public view: public stories' cards, and only what was said since the
        view began (his own words can't be judged by a rule); no private long story's thread."""
        from datetime import datetime
        from . import privacy, short_stories
        shorts = short_stories.all_stories(self.state)
        hide = {s["copies"].get(CHAT) for s in shorts if privacy.is_private(s)} - {None}
        show = {s["copies"].get(CHAT) for s in shorts if not privacy.is_private(s)} - {None}
        threads = {x["threads"].get(CHAT) for x in longs.all_stories(self.state) if privacy.is_private(x)} - {None}

        def new(m):
            try:
                return datetime.strptime(str(m.get("at", "")), "%Y-%m-%dT%H:%M:%S%z").timestamp() >= since
            except ValueError:
                return False
        msgs = [privacy.scrub(m) for m in chat["messages"] if m.get("id") not in hide and m.get("thread") not in threads
                and (m.get("id") in show or new(m)) and not privacy.leaks(m)]  # a duty's post during a demo too; no links
        topics_ = [x for x in chat["topics"] if x.get("id") not in threads]
        return {**chat, "messages": msgs, "topics": topics_, "public": True}

    def data(self) -> dict:
        runs = []
        days = sorted((self.state / "runs").glob("*"))[-DAYS:]
        for f in sorted((p for d in days for p in d.glob("*.json")), reverse=True)[:MAX_RUNS]:
            try:
                runs.append(read_run(json.loads(f.read_text(encoding="utf-8"))))
            except (OSError, ValueError):
                continue
        asks = []
        for f in sorted((self.state / "asks").glob("*.json"), reverse=True)[:50]:
            try:
                a = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not a["answer"] or time.time() - f.stat().st_mtime < 86400:
                asks.append({k: a[k] for k in ("id", "title", "markdown", "options", "answer",
                                                "asked", "failed", "note", "run")})
        paused = self.scheduler.paused if self.scheduler else None
        running = sorted(self.scheduler.running) if self.scheduler else []
        workflows = self.workflows + [f.workflow() for f in self.families]
        for f in self.families:  # after the duties' runs: a failed run there is picked first
            try:
                runs += f.runs(None, FULL)
            except Exception:
                log.exception("family %s: runs failed", f.name)
        return {"now": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "workflows": workflows, "sops": SOPS,
                "runs": runs, "asks": asks, "paused": paused, "running": running,
                "meter": self.meter(), "pys": bool(pys_path())}


def loopback6_notice(state: Path, channels: list, chats: list[str], server, port: int) -> bool:
    """One notice to the owner when another program first holds [::1]:<port> (the PO, 2026-11-18);
    a marker in state keeps it to once until the buddy has ::1 again. True when it was posted now."""
    from .channel import post_all
    mark = state / "loopback6-taken"
    if not getattr(server, "twin_taken", False):
        mark.unlink(missing_ok=True)
        return False
    if mark.exists():
        return False
    post_all(channels, chats, t("web.loopback6_taken", port=port), title="buddy")
    mark.write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z") + "\n", encoding="utf-8")
    return True
