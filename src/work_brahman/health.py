"""GET /api/health: is this buddy working, for a monitor or a script (the PO's order, 2026-11-17).

The answer is 200 when every part works and 503 when one doesn't, so `curl -f` alone tells:
- the heartbeat (alive.py) beat less than alive.GAP seconds ago: the process is not stuck;
- every chat channel's listener is still running (Bot.listening: set when its listen starts,
  cleared when it returns or raises);
- the scheduler's thread is alive, when there are jobs or duties to schedule (none: null).

`instance` is a random id made when the process starts (128 bits, hex), and nothing else: a client
that reaches the buddy on both loopbacks (127.0.0.1 and [::1], web.LoopbackServer) compares it to
know both are this process, not another program on one of them (the PO, 2026-11-18; the shell's
Local mode). Without the owner's login the body is {"ok", "instance"} only: whether it works, nothing about
which channels or which version. Logged in (a key slot), the details too. Only names and numbers: no chat ids, no paths, no hosts.
"""

from __future__ import annotations

import secrets
import threading
import time
from functools import lru_cache
from pathlib import Path


INSTANCE = secrets.token_hex(16)  # new at each start of the process


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
    return {"ok": ok, "instance": INSTANCE, "version": changes.version(), "commit": _commit(), "up_s": round(now - started),
            "beat_age_s": age, "channels": channels, "scheduler": sched}
