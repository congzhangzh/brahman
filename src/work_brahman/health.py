"""GET /api/health: is this buddy working, for a monitor or a script (the PO's order, 2026-11-17).

The answer is 200 when every part works and 503 when one doesn't, so `curl -f` alone tells:
- the heartbeat (alive.py) beat less than alive.GAP seconds ago: the process is not stuck;
- every chat channel's listener is still running (Bot.listening: set when its listen starts,
  cleared when it returns or raises);
- the scheduler's thread is alive, when there are jobs or duties to schedule (none: null).

Without the web token (BUDDY_WEB_TOKEN set, none given) the body is {"ok": …} only: whether it
works, nothing about which channels or which version. With the token, or with no token set,
the details too. Only names and numbers: no chat ids, no paths, no hosts.
"""

from __future__ import annotations

import threading
import time
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def _commit() -> str:
    from . import changes
    return changes.commit()[0]  # git once per process, not once per poll


def report(state: Path, bot, scheduler, started: float, now: float | None = None) -> dict:
    from . import alive, changes
    now = now or time.time()
    beat = alive.last(state)
    age = round(now - beat) if beat else None
    channels = dict(getattr(bot, "listening", {}) or {}) if bot else {}
    sched = None
    if scheduler is not None and getattr(scheduler, "tasks", None):
        sched = any(t.name == "scheduler" and t.is_alive() for t in threading.enumerate())
    ok = age is not None and age < alive.GAP and all(channels.values()) and sched is not False
    return {"ok": ok, "version": changes.version(), "commit": _commit(), "up_s": round(now - started),
            "beat_age_s": age, "channels": channels, "scheduler": sched}
