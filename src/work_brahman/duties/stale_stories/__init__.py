"""Duty: stale-stories. Close a short story the owner asked to stop whose session is gone. See DUTY.md.

The owner, 2026-11-17 (through the PO: "Close them + a rule"): a running story with a ■ Stop request,
quiet for `quiet_hours` (default 24), whose session no longer exists, is closed as failed with the
note "stopped by the owner; the session was gone". Script only: no model, $0.

"Gone" needs evidence on this machine; anything unsure keeps the story (it is counted, not closed):
- the story was marked on this machine (its `by` names this host or this side's peer name);
- Claude Code: `claude agents --json` answers and lists no session starting with the story's id,
  and the window it marked from, if any, no longer runs that process (same pid, same start:
  lanes.live). The listing is this user's Claude Code (its CLAUDE_CONFIG_DIR): a session of another
  user or config dir reads as gone, which only counts after a stop request and a quiet day;
- Codex: the daemon answers and knows no such thread;
- a story with no session in its `by` is kept: nothing says which session to look for.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from ... import portable
from ...text import t

NOTE = "stopped by the owner; the session was gone"


def _ts(at: str) -> float:
    try:
        return datetime.strptime(at, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except (ValueError, TypeError):
        return 0.0


def quiet_since(story: dict) -> float:
    """The last thing that happened on the story: its newest event, its start or its stop request."""
    times = [_ts(e.get("at", "")) for e in story.get("events", [])]
    times += [_ts(story.get("started", "")), _ts((story.get("stop") or {}).get("at", ""))]
    return max(times + [0.0])


def claude_sessions() -> list[str] | None:
    """The session ids Claude Code lists as running, or None when it can't be asked."""
    try:
        p = subprocess.run(portable.argv(["claude", "agents", "--json"]), capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
        if p.returncode:
            return None
        return [str(r.get("sessionId") or "") for r in json.loads(p.stdout or "[]")]
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        return None


def gone(story: dict, here: set[str], sessions, codex_thread) -> bool | None:
    """True: the session is gone (evidence). False: it runs. None: unsure (another machine, no session
    named, a check that can't be asked)."""
    by = story.get("by") or ""
    m = re.match(r"[^@/]+@([^/]+)/[^:]*:(claude|codex)-(\w+)", by)
    if not m or m.group(1) not in here:
        return None
    engine, sid = m.group(2), m.group(3)
    if engine == "claude":
        from ... import lanes
        listed = sessions()
        if listed is None:
            return None
        if any(s.startswith(sid) for s in listed):
            return False
        win = story.get("window") or {}
        return not (win.get("pid") and lanes.live(win))
    thread = (story.get("window") or {}).get("thread") or ""
    if not thread.startswith(sid):
        return None  # only the 8 first characters: the daemon is asked by the whole id
    known = codex_thread(thread)
    return None if known is None else not known


def _codex_thread(thread_id: str) -> bool | None:
    from ... import codexd
    if not codexd.alive():
        return None
    return codexd.thread(thread_id) is not None


class StaleStories:
    name = "stale-stories"
    note = ""

    def __init__(self, cfg: dict, state_dir: Path):
        self.chat_id = cfg.get("chat_id", "")
        self.quiet = float(cfg.get("quiet_hours", 24)) * 3600
        self.state_dir = state_dir
        self.channels: list = []  # `buddy duty` hands the chat channels in: closing edits the cards
        self.last: dict = {}  # the last check, for the audit (run record)

    def commands(self) -> dict:
        return {}

    def _stale(self, s: dict, now: float) -> bool:
        return s["status"] == "running" and bool(s.get("stop")) and now - quiet_since(s) >= self.quiet

    def tick(self, now: float | None = None, sessions=claude_sessions, codex_thread=_codex_thread,
             here: set[str] | None = None) -> tuple[str, str]:
        from ... import lanes, short_stories as shorts
        now = time.time() if now is None else now
        here = lanes.here() if here is None else here
        asked = [s for s in shorts.all_stories(self.state_dir) if s["status"] == "running" and s.get("stop")]
        quiet = [s for s in asked if self._stale(s, now)]
        cache: dict = {}

        def listed():  # `claude agents --json` once per check
            if "s" not in cache:
                cache["s"] = sessions()
            return cache["s"]

        closed, unsure = [], []
        for s in quiet:
            g = gone(s, here, listed, codex_thread)
            if g and shorts.finish(self.state_dir, self.channels, s["id"], failed=True, note=NOTE,
                                   only_if=lambda x: self._stale(x, now)):  # still so, under its lock
                closed.append(s)
            elif g is None:
                unsure.append(s["id"])
        self.last = {"asked": len(asked), "quiet": len(quiet), "closed": [s["id"] for s in closed], "unsure": unsure}
        if not closed:
            return "", ""
        rows = "\n".join(f"- #{s['id']} {s['title']}" for s in closed)
        return t("stale.closed", n=len(closed), h=round(self.quiet / 3600)) + "\n" + rows, ""
