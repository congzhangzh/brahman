"""What the buddy sends to Feishu and Lark on its own (the owner, 2026-11-12: "as the Feishu quota is
limited, Feishu-related notifications should have a switch, off by default, for unneeded or
unintended interactions").

Every call the buddy makes to the Feishu/Lark message API counts against the app's monthly
quota (code 99991403 once it is spent). A short story's card is one call to show and one per
change (each mark, note, plan) in each Feishu or Lark chat; mirrors of what was said in another
linked chat (Telegram, the web) are calls too. `[notify]` in buddy.toml says, per app:

  off   (the default)  nothing on its own: no story, team or long-story cards, no duty posts,
                       no mirrors, no questions, no topic threads
  ends                 a card when a story starts and when it ends (no change in between); posts
                       and questions as before
  all                  everything, as before this switch

Untouched in every mode: an answer to what the owner writes or presses in that app (reply,
progress, answer), and what the owner sends on purpose (the Setup tab's 🏓 ping, which goes
through direct()). A chat the owner wrote or pressed in within LIVE seconds is "live": posts,
questions and topic threads go to it as usual (the pong button's answer, ■ Stop's, /long's
"which folder?"), story cards still by mode. Telegram and the web have no quota and are never
wrapped.

A skipped card or question gets the handle QUIET, stored like a real one, so a story knows it was
seen: under "ends" a story whose start was skipped gets its card when it ends, never mid-way.
A skipped post counts as delivered: the owner chose not to get it, it did not fail.

calls() counts this process's message calls per app and month (state/feishu-calls.json), for
doctor and the Setup tab: the API's own count (tokens, user lookups) is the developer console's.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

MODES = ("off", "ends", "all")
LIVE = 600  # seconds a chat stays "live" after the owner wrote or pressed in it
QUIET = "quiet"  # the handle of a card or question this switch kept back
log = logging.getLogger("buddy.notify")
_lock = threading.Lock()


def app_of(prefix: str, domain: str) -> str:
    """Which [notify] key a Feishu channel follows: "lark" for the lark: app, else the first app's domain."""
    return "lark" if prefix == "lark:" else ("lark" if domain == "lark" else "feishu")


def mode(cfg: dict, app: str) -> str:
    m = str((cfg.get("notify") or {}).get(app, "off"))
    return m if m in MODES else "off"


class Quiet:
    """A Feishu channel with its proactive calls held back by mode. Everything else passes through."""

    def __init__(self, inner, mode: str, app: str):
        self.inner, self.mode, self.app = inner, mode, app
        self.heard: dict[str, float] = {}  # chat -> when the owner last wrote or pressed there

    def __getattr__(self, name):  # owns, reply, progress, answer, failure, api, name …
        return getattr(self.inner, name)

    def listen(self, on_message) -> None:
        def heard(msg):
            self.heard[msg.chat] = time.time()
            on_message(msg)
        self.inner.listen(heard)

    def live(self, chat: str) -> bool:
        return time.time() - self.heard.get(chat, 0) < LIVE

    def _skip(self, what: str, chat: str) -> None:
        log.info("%s: %s to %s kept back (notify = %s)", self.app, what, chat, self.mode)

    def post(self, chat: str, markdown: str, **kw) -> bool:
        if self.mode == "off" and not self.live(chat):
            self._skip("a post", chat)
            return True
        return self.inner.post(chat, markdown, **kw)

    def ask(self, chat: str, markdown: str, **kw) -> str | None:
        if self.mode == "off" and not self.live(chat):
            self._skip("a question", chat)
            return QUIET
        return self.inner.ask(chat, markdown, **kw)

    def settle(self, chat: str, handle: str, markdown: str, **kw) -> bool:
        if handle == QUIET or (self.mode == "off" and not self.live(chat)):
            return True
        return self.inner.settle(chat, handle, markdown, **kw)

    def track(self, chat: str, markdown: str, *, handle: str | None = None, done: bool = False,
              failed: bool = False, **kw) -> str | None:
        if self.mode == "off":
            if handle is None:
                self._skip("a card", chat)
            return handle or QUIET
        ends = done or failed
        if self.mode == "ends" and handle is not None and not ends:
            return handle  # a change mid-way: the card shows the start until the end
        if handle == QUIET:  # its start was kept back: the end comes as a card of its own
            if not ends and self.mode == "ends":
                return QUIET
            handle = None
        return self.inner.track(chat, markdown, handle=handle, done=done, failed=failed, **kw)

    def open_topic(self, chat: str, topic: dict) -> str | None:
        if self.mode == "off" and not self.live(chat):
            self._skip("a topic thread", chat)
            return None
        return self.inner.open_topic(chat, topic)

    def stop_button(self, chat: str, markdown: str) -> bool:
        if self.mode == "off" and not self.live(chat):
            return True
        return self.inner.stop_button(chat, markdown)


def direct(channel):
    """The channel itself, for what the owner sends on purpose (the Setup tab's ping)."""
    return getattr(channel, "inner", channel)


def wrap(channel, cfg: dict, prefix: str, domain: str):
    app = app_of(prefix, domain)
    return Quiet(channel, mode(cfg, app), app)


# The count: message calls per app and month, this machine's buddy only.

def _path(state: Path) -> Path:
    return state / "feishu-calls.json"


def count(state: Path | None, app: str, n: int = 1) -> None:
    if state is None:
        return
    month = time.strftime("%Y-%m")
    with _lock:
        p = _path(state)
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            d = {}
        d.setdefault(app, {})
        d[app][month] = d[app].get(month, 0) + n
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(d))
        except OSError:
            pass


def calls(state: Path, month: str | None = None) -> dict[str, int]:
    """{app: message calls this month} (or the month given, "YYYY-MM")."""
    month = month or time.strftime("%Y-%m")
    try:
        d = json.loads(_path(state).read_text())
    except (OSError, ValueError):
        return {}
    return {app: int(v.get(month, 0)) for app, v in d.items() if isinstance(v, dict)}
