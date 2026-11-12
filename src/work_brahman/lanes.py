"""Who did what, where, in parallel: a story as swimlanes on a time axis (the owner, 2026-11-12:
"can the 'How the system works' part have a standard parallel-execution view that shows which
environment does what?"; chosen: the selected story, lanes machine → session, real time, a tab
in level 2; then "machine, session, subagent" as levels, with an attach to py-screen).

Three levels from each mark's `by`: the machine, the session, and in it the main agent's row and
one row per subagent (one level lower: the session started it). `by` is `buddy@<host>/<session>[:
<engine-id>][/sub:<agent>]` (short_stories.executor; /sub: only when the master marks a step it
handed to a subagent with BUDDY_AGENT set) or `<machine>/<session>[/sub:<agent>]` (a peer's
step, peer_story). Each session row carries the pys window its marks came from, when one did
(a mark's `win`, else the story's own window for the session that holds it), and whether that
same process still runs there: the page's Attach brings it up with `pys select`. A
step's bar runs from its first "doing" to its first done, failed or skipped after it; a step
only ever marked done gets an inferred start (the end before it, or the story's start), drawn
hatched; a step still doing runs to now. The story's
children (one level) add their own steps, in their own lanes.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import short_stories as shorts

END = ("done", "failed", "skipped")


def where(by: str) -> tuple[str, str, str]:
    """(machine, session, agent) from a `by`, agent "" for the main agent:
    "buddy@host/project:claude-1a2b…/sub:review" or "mac/comp-mac-g1"."""
    if not by:
        return "?", "?", ""
    rest, _, agent = by.partition("/sub:")
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    machine, _, session = rest.partition("/")
    return machine or "?", (session.split(":")[0] or "?"), agent.split(":")[0].split("/")[0]


def here() -> set[str]:
    """The names this machine marks under: its short hostname (executor) and this side's peer name."""
    import socket
    from . import peer
    names = {socket.gethostname().split(".")[0]}
    try:
        names.add(peer.load()["name"])
    except (OSError, ValueError, KeyError):
        pass
    return names


def live(win: dict | None) -> bool:
    """The session that marked from this pys window still runs in it: same pid, same start."""
    if not win or not win.get("window") or not win.get("pid"):
        return False
    from . import portable
    p = portable.process(int(win["pid"]))
    return bool(p) and p["started"] == win.get("started")


def _ts(at: str) -> float:
    try:
        return time.mktime(time.strptime(at[:19], "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return 0.0


def _bars(story: dict, prefix: str = "") -> list[dict]:
    """One bar per step that has events: when it ran, where, how it ended."""
    names = {m.get("id"): m["name"] for m in story.get("marks", [])}
    order = {m.get("id"): k + 1 for k, m in enumerate(story.get("marks", []))}
    by_step: dict[str, list[dict]] = {}
    for e in story.get("events", []):
        if e.get("step"):
            by_step.setdefault(e["step"], []).append(e)
    out = []
    for sid, evs in by_step.items():
        start = next((e for e in evs if e.get("status") == "doing"), None)
        end = next((e for e in evs if e.get("status") in END and (start is None or e["at"] >= start["at"])), None)
        last = end or start or evs[-1]
        by = (end or start or evs[-1]).get("by") or next((e.get("by") for e in reversed(evs) if e.get("by")), "")
        if not by:
            m = next((m for m in story.get("marks", []) if m.get("id") == sid), {})
            by = m.get("by", "")
        machine, session, agent = where(by)
        win = next((e["win"] for e in reversed(evs) if isinstance(e.get("win"), dict)), None)
        out.append({"step": prefix + names.get(sid, sid), "n": order.get(sid), "machine": machine, "session": session,
                    "agent": agent, "win": win,
                    "start": _ts((start or last)["at"]), "end": _ts(end["at"]) if end else None,
                    "status": (end or last).get("status", "doing"), "open": end is None and start is not None})
    return out


def _held(story: dict, local: set[str]) -> dict:
    """The story's own window (the session that started or holds it), by (machine, session)."""
    w = story.get("window")
    if not isinstance(w, dict) or w.get("mux") != "pys" or not w.get("tag"):
        return {}
    return {(m, w["tag"].split("/sub:")[0]): w for m in local}


def of_story(state: Path, number: int, now: float | None = None, local: set[str] | None = None) -> dict | None:
    """The lanes of short story `number` and its children: {title, t0, t1, lanes: [{machine,
    session, agent, bars, window, live, here}]}, this machine first, then by machine, session, the main agent
    first and its subagents after it. window: the pys window of that session ("" when none is
    known), live: the same process still runs there, here: the session is on this machine."""
    s = shorts.load(state, number)
    if s is None:
        return None
    now = now if now is not None else time.time()
    local = here() if local is None else local
    bars, held = _bars(s), _held(s, local)
    for c in shorts.children(s):
        child = shorts.load(state, c)
        if child:
            bars += _bars(child, prefix=f"#{c} ")
            held = {**_held(child, local), **held}
    for b in bars:
        if b["open"]:
            b["end"] = now
    # A step only ever marked done: it ran, at the latest, from the end before it (any lane) or the
    # story's start. Drawn as inferred (hatched), never as a measured start.
    started = _ts(s.get("started", ""))
    ends = sorted(b["end"] for b in bars if b["end"] is not None)
    for b in bars:
        b["inferred"] = False
        if b["end"] is not None and b["start"] == b["end"] and not b["open"]:
            before = [e for e in ends if e < b["end"]]
            b["start"], b["inferred"] = (before[-1] if before else started or b["end"]), True
    lanes: dict[tuple[str, str, str], list[dict]] = {}
    for b in bars:
        lanes.setdefault((b["machine"], b["session"], b["agent"]), []).append(b)
    wins: dict[tuple[str, str], dict] = dict(held)  # a mark's own window beats the story's
    for b in sorted(bars, key=lambda b: b["end"] or b["start"]):
        if b["win"] and b["machine"] in local:
            wins[(b["machine"], b["session"])] = b["win"]
    first = {}
    for (m, se, _a), bs in lanes.items():
        first[(m, se)] = min([first.get((m, se), 1e18)] + [b["start"] for b in bs])
    rows = []
    for (m, se, a), bs in lanes.items():
        w = wins.get((m, se))
        for b in bs:
            del b["win"]
        rows.append({"machine": m, "session": se, "agent": a, "bars": sorted(bs, key=lambda b: b["start"]),
                     "here": m in local, "window": str(w["window"]) if w else "", "live": live(w)})
    rows.sort(key=lambda r: (not r["here"], r["machine"], first[(r["machine"], r["session"])], r["session"], r["agent"] != "",
                             min(b["start"] for b in r["bars"])))
    times = [b["start"] for b in bars] + [b["end"] for b in bars if b["end"]]
    t0 = min(times + [_ts(s.get("started", ""))]) if times else _ts(s.get("started", ""))
    t1 = max(times + [t0 + 60]) if times else t0 + 60
    if s.get("status") == "running":
        t1 = max(t1, now)
    return {"id": f"short-story:{number}", "title": s["title"], "status": s.get("status"), "t0": t0, "t1": t1,
            "lanes": rows}
