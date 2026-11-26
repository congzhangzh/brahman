"""Which chat app can't be reached right now (the owner, 2026-11-26: a limited network is a normal place for
the buddy, "friendly for this, and give notice if needed").

Every chat call has a timeout (feishu.TIMEOUT, telegram.CALL_TIMEOUT) and a network failure is no error: the
app's call returns nothing, and this records it. One record per app in state/reach.json, said once per change:

- down: "<App> can't be reached since 10:02 (timed out): the web page, the scheduler and the duties work;
  <App> is tried again on its next call." Logged once, shown on every page (/api/data) and by buddy doctor.
- back: "<App> reached again at 10:14", logged once and shown for BACK_SHOWN seconds.

A call that reaches the app (even one it refuses: a quota, a bad id) counts as reached.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

BACK_SHOWN = 600  # seconds "reached again" stays on the page
NAMES = {"feishu": "Feishu", "lark": "Lark", "telegram": "Telegram"}
log = logging.getLogger("buddy.reach")
_lock = threading.Lock()
state: Path | None = None  # set by cli once the state folder is known; None: nothing is kept (a test, a script)
_seen: dict[str, bool] = {}  # app -> down, as last written: a call that changes nothing touches no file


def _path(st: Path) -> Path:
    return st / "reach.json"


def read(st: Path | None = None) -> dict:
    st = st or state
    if st is None:
        return {}
    try:
        d = json.loads(_path(st).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _write(st: Path, d: dict) -> None:
    p = _path(st)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def name(app: str) -> str:
    return NAMES.get(app, app)


def down(app: str, why: str, now: float | None = None) -> bool:
    """The app's call didn't get through (a timeout, no route, a refused connection, TLS cut). True when
    this is news (said now); False when it was already known: the caller says nothing more."""
    if _seen.get(app) is True:
        return False
    if state is None:
        return True
    with _lock:
        d = read()
        if (d.get(app) or {}).get("down"):
            _seen[app] = True
            return False
        d[app] = {"down": now or time.time(), "why": why[:80]}
        _write(state, d)
        _seen[app] = True
    log.warning("%s can't be reached (%s): the web page, the scheduler and the duties work; %s is tried "
                "again on its next call", name(app), why[:80], name(app))
    return True


def up(app: str, now: float | None = None) -> None:
    """The app answered."""
    if _seen.get(app) is False or state is None:
        return
    with _lock:
        d = read()
        was = (d.get(app) or {}).get("down")
        _seen[app] = False
        if not was:
            return
        d[app] = {"back": now or time.time(), "was_down": was}
        _write(state, d)
    log.info("%s reached again (it couldn't be reached for %s)", name(app), took((now or time.time()) - was))


def took(s: float) -> str:
    s = int(s)
    return f"{s // 3600} h {s % 3600 // 60} min" if s >= 3600 else f"{s // 60} min" if s >= 60 else f"{s} s"


def notices(st: Path | None = None, now: float | None = None) -> list[dict]:
    """For the page and the doctor: [{app, name, down|back: "HH:MM", why}], the down ones first."""
    now = now or time.time()
    out = []
    for app, r in sorted(read(st).items(), key=lambda kv: "back" in kv[1]):
        if not isinstance(r, dict):
            continue
        if r.get("down"):
            out.append({"app": app, "name": name(app), "down": time.strftime("%H:%M", time.localtime(r["down"])),
                        "why": str(r.get("why", ""))})
        elif r.get("back") and now - r["back"] < BACK_SHOWN:
            out.append({"app": app, "name": name(app), "back": time.strftime("%H:%M", time.localtime(r["back"]))})
    return out
