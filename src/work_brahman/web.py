"""The web workbench: a third frontend beside Lark and Telegram, local only.

`buddy serve` also listens on http://127.0.0.1:<port> ([web] port in buddy.toml). The page
reads the files the buddy already writes (run records, asks, the scheduler's pause), so
the web keeps no state of its own:

    GET  /            the workbench (static/index.html)
    GET  /tokens.css  the design tokens (design/tokens.css)
    GET  /api/data    workflows, recent runs, asks, pause, and each kind's SOP (runs.SOPS)
    GET  /api/chat    the web chat: my messages, the buddy's answers, mirrors from the apps,
                      and the topics (topics.py); a message's "thread" is its topic, "" = main
    POST /api/chat    {"text": ..., "thread": topic id or ""}: a message, as one typed in Lark
    POST /api/topic   {"name": ...}: a new topic, opened in Lark (a thread) and here (a tab)
    POST /api/answer  {"ask": id, "choice": key}: an answer, as a button press in a chat is
    POST /api/stop    ■ STOP, the same as /stop in a chat
    POST /api/resume
    POST /api/short   {"n": 12, "what": "stop" | "reply" | "a1.0", "text": ...}: ■ Stop, 💬 Reply
                      or an option of question 1 on a short story, as under its card in a chat

Families show in level 1 too: a family with runs() is one workflow, and its stories are its
runs (families/short_story).

Only 127.0.0.1 is bound, and two checks keep other pages in your own browser out: the Host
header must be the local address (a DNS-rebinding page has its own name there), and a POST
needs the X-Buddy header, which a cross-site request can't send without a CORS preflight
that this server never answers.
"""

import json
import logging
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import long_stories as longs, portable, topics
from .meter import meter
from .channel import Incoming
from .runs import SOPS, read as read_run
from . import text
from .text import t

log = logging.getLogger("buddy.web")

HERE = Path(__file__).resolve().parent


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
DAYS = 3        # how far back the page shows runs
MAX_RUNS = 600


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
            ask_id: str, failed: bool = False, note: str = "") -> str | None:
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
        tmp.write_text(json.dumps(change, ensure_ascii=False))
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
            self.msgs = json.loads(self.path.read_text())
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
                c = json.loads(f.read_text())
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

    def say(self, text: str, thread: str = "") -> None:
        """A message typed on the page; thread: the topic's tab it was typed in."""
        self._add({"from": "you", "markdown": text, "thread": thread})
        if self.on_message:
            self.on_message(Incoming(CHAT, f"web:{time.time_ns()}", CHAT, text, thread=thread))

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
        tmp.write_text(json.dumps(self.msgs, ensure_ascii=False))
        tmp.replace(self.path)


def page() -> bytes:
    """The workbench page with its words in every language (text.py's page.* keys); it starts
    in the buddy's, and its EN / 中文 switch changes them in place."""
    html = (HERE / "static" / "index.html").read_text()
    html = html.replace('<html lang="en">', f'<html lang="{text.language()}">', 1)
    words = {lang: text.page(lang) for lang in text.LANGUAGES}
    html = html.replace("const TEXTS = {};", f"const TEXTS = {json.dumps(words, ensure_ascii=False)};", 1)
    return html.encode()


class Web:
    def __init__(self, port: int, state: Path, bot, scheduler, cfg: dict, home: str,
                 chat: WebChat | None = None, families: dict | None = None):
        self.port, self.state, self.bot, self.scheduler, self.home = port, state, bot, scheduler, home
        self.chat = chat
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
                if path == "/":
                    return self._send(200, page(), "text/html")
                if path.startswith("/media/"):  # the corner's film and its poster
                    name = path[7:]
                    f = HERE / "static" / "media" / name
                    kind = {".mp4": "video/mp4", ".jpg": "image/jpeg"}.get(f.suffix)
                    if not re.fullmatch(r"[\w.-]+", name) or not kind or not f.is_file():
                        return self._send(404, b"not found", "text/plain")
                    return self._send(200, f.read_bytes(), kind)
                if path == "/tokens.css":
                    return self._send(200, TOKENS.read_bytes() if TOKENS.exists() else b"", "text/css")
                if path == "/api/data":
                    return self._json(web.data())
                if path == "/api/chat":
                    return self._json({"on": web.chat is not None,
                                       "messages": web.chat.history() if web.chat else [],
                                       "topics": [t for t in topics.all_topics(web.state).values()
                                                  if CHAT in t["threads"] and not t.get("closed")]
                                       + [{"id": x["threads"][CHAT], "name": longs.label(x), "where": "long",
                                           "threads": x["threads"]} for x in longs.all_stories(web.state)
                                          if CHAT in x["threads"]]})
                self._send(404, b"not found", "text/plain")

            def do_POST(self):
                if not self._local():
                    return
                if self.headers.get("X-Buddy") != "1":
                    return self._send(403, b"missing X-Buddy header", "text/plain")
                n = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self._send(400, b"bad json", "text/plain")
                path = self.path.split("?")[0]
                if path == "/api/chat":
                    text = str(body.get("text", "")).strip()
                    if not web.chat or not text:
                        return self._send(400, b"no chat, or nothing to say", "text/plain")
                    thread = str(body.get("thread") or "")
                    if thread and not (topics.find(web.state, CHAT, thread) or longs.find(web.state, CHAT, thread)):
                        return self._send(400, b"no such topic", "text/plain")
                    web.chat.say(text[:8000], thread)
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
                elif path in ("/api/stop", "/api/resume"):
                    action = path.rsplit("/", 1)[-1]
                else:
                    return self._send(404, b"not found", "text/plain")
                web.press(action, text)
                self._json({"ok": True, "action": action})

            def _local(self) -> bool:
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
                if host not in ("127.0.0.1", "localhost", "[::1]"):
                    self._send(421, b"use http://127.0.0.1", "text/plain")
                    return False
                if not _owner(self.client_address[1], self.server.server_port):
                    self._send(403, b"only the user the buddy runs as", "text/plain")
                    return False
                return True

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

        server = self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=server.serve_forever, name="web", daemon=True).start()
        log.info("web workbench on http://127.0.0.1:%d", self.port)

    def press(self, action: str, text: str | None = None) -> None:
        """A press on the page, handled as one in a chat (the person at this machine is trusted).
        text: what was typed with it (a short story's reply)."""
        msg = Incoming(CHAT if self.chat else self.home, f"web:{time.time_ns()}", CHAT, text, action)
        side = self.chat or type("WebSide", (), {"name": "web", "owns": lambda s, c: False})()
        threading.Thread(target=self.bot._press, args=(side, msg), daemon=True).start()

    def data(self) -> dict:
        runs = []
        days = sorted((self.state / "runs").glob("*"))[-DAYS:]
        for f in sorted((p for d in days for p in d.glob("*.json")), reverse=True)[:MAX_RUNS]:
            try:
                runs.append(read_run(json.loads(f.read_text())))
            except (OSError, ValueError):
                continue
        asks = []
        for f in sorted((self.state / "asks").glob("*.json"), reverse=True)[:50]:
            try:
                a = json.loads(f.read_text())
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
                runs += f.runs(DAYS)
            except Exception:
                log.exception("family %s: runs failed", f.name)
        return {"now": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "workflows": workflows, "sops": SOPS,
                "runs": runs, "asks": asks, "paused": paused, "running": running,
                "meter": meter(self.state)}
