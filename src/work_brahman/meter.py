"""The workbench's meter: how much of the last days' work was digital, and how much human.

Counted from the records the buddy already keeps, never estimated:

- **digital**: what agents and the buddy did. A run that worked (an agent turn, a post, an
  act; a check that found nothing does not count), a short story's mark, a long story's turn.
- **human**: what the owner decided or said. A chat message, an ask answered (not one STOP
  closed), a short story's reply or stop, a long story's message, start, or Allow / Deny.

Neither side is the goal: the meter shows the mix, part digital and part human.
"""

import json
import time
from pathlib import Path

from . import long_stories as longs, short_stories as shorts

DAYS = 7
WORKED = {"agent", "post", "act", "render"}  # steps that mean a run did something


def meter(state: Path, days: int = DAYS) -> dict:
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))
    digital = human = 0
    for d in sorted((state / "runs").glob("*")):
        if d.name < since[:10]:
            continue
        for f in d.glob("*.json"):
            try:
                r = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            if r.get("started", "") < since:
                continue
            steps = {s.get("step") for s in r.get("steps", [])}
            digital += bool(steps & WORKED)
            human += r.get("kind") == "chat"  # a message the owner sent
    for f in (state / "asks").glob("*.json") if (state / "asks").exists() else []:
        try:
            a = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if a.get("answer") and not a["answer"].get("stopped") and a["answer"].get("at", "") >= since:
            human += 1
    for s in shorts.all_stories(state):
        digital += sum(1 for m in s["marks"] if (m.get("at") or "") >= since)
        human += sum(1 for r in s.get("replies", []) if r["at"] >= since)
        human += bool(s.get("stop") and s["stop"]["at"] >= since)
    for s in longs.all_stories(state):
        for e in s.get("events", []):
            if e["at"] >= since:
                digital += e["step"] == "turn"
                human += e["step"] in ("say", "new", "decide")
    total = digital + human
    return {"digital": digital, "human": human, "days": days,
            "digital_pct": round(100 * digital / total) if total else 0,
            "human_pct": 100 - round(100 * digital / total) if total else 0}
