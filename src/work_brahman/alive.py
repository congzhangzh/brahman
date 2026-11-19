"""The buddy's heartbeat, so a start after an outage tells the owner (the owner, 2026-11-14: "recover
the stories if they're missing when the server didn't start").

`buddy serve` writes state/alive.json every BEAT seconds. At the next start, a last beat older than
GAP means the buddy was down (stopped, killed, out of memory, the machine off), not restarted:
it posts one notice to the owner's chats with when and how long, and asks him to resend what got
no answer. Telegram keeps a bot's updates for 24 hours and hands them over at the next poll; what
Lark and Feishu keep for a long connection that was away is not known, so the notice says
"may not". The post follows [notify] like any other (Lark and Feishu: kept back while off, so
it costs no quota); Telegram and the web always get it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

BEAT = 60     # seconds between beats
GAP = 300     # seconds without a beat that count as being down; a restart takes a few
log = logging.getLogger("buddy.alive")


def _path(state: Path) -> Path:
    return state / "alive.json"


def last(state: Path) -> float | None:
    try:
        return float(json.loads(_path(state).read_text(encoding="utf-8"))["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def beat(state: Path, now: float | None = None) -> None:
    p = _path(state)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": now or time.time()}), encoding="utf-8")
    tmp.replace(p)


def outage(state: Path, now: float | None = None) -> tuple[float, float] | None:
    """(down since, back at) when the last beat is older than GAP; None on a restart or a first start."""
    now = now or time.time()
    at = last(state)
    return (at, now) if at is not None and now - at > GAP else None


def took(seconds: float) -> str:
    m = int(seconds // 60)
    d, h, m = m // 1440, m // 60 % 24, m % 60
    return " ".join(f"{n} {u}" for n, u in ((d, "d"), (h, "h"), (m, "min")) if n) or "0 min"


def notice(since: float, until: float) -> str:
    from .text import t
    stamp = lambda x: time.strftime("%m-%d %H:%M", time.localtime(x))  # noqa: E731
    return t("alive.was_down", since=stamp(since), until=stamp(until), took=took(until - since))


def start(state: Path, channels: list, chats: list[str], post_all=None, every: float = BEAT) -> str:
    """At serve's start: the notice when the buddy was down, then the beat. Returns what it did."""
    gap = outage(state)
    beat(state)
    said = ""
    if gap and chats:
        if post_all is None:
            from .channel import post_all
        text = notice(*gap)
        try:
            post_all(channels, chats, text, title="⚠️ brahman-buddy")
            said = text
        except Exception:  # noqa: BLE001 - a notice never stops the buddy
            log.exception("the was-down notice failed")

    def loop():
        while True:
            time.sleep(every)
            try:
                beat(state)
            except OSError as e:  # a full disk: say so, keep beating
                log.warning("heartbeat: %s", e)

    threading.Thread(target=loop, name="alive", daemon=True).start()
    return said
