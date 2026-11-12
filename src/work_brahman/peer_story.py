"""One story on every machine (specs/multi-brahman.md, "One story on every machine"; the owner,
2026-11-12: "like Red Alert 2, where everyone has a view of the whole system, and they just ask
and sync"). Build steps 2 to 5, on top of the signed events of peer.py (step 1):

- the story is one, and every machine keeps all of it: a view folded from its event log, the
  same on every machine that has the same events. Its id names where it started: `a#12`, where
  12 is the short story on machine a that carries the card;
- each step has one owner machine (`b1@mac`); only the owner sends events for it, so copies
  never conflict;
- the starting machine shows the one card: an event it gets for one of its stories marks the
  local short story (no chat token needed: `buddy serve`'s drawer redraws marks made outside it);
- a step's running context is `<machine>/<session>`;
- sync: each side sends again what it has (kept once per (origin, seq) on the other side) and
  pulls what the other has (GET /events, signed), so a machine that was away catches up; a
  send-only peer (`direction = "in"` in peers.toml) is never pushed to, only pulled from by it.

Nothing a peer sends is run: events only mark, note and plan steps.
"""

from __future__ import annotations

import json
import os
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import peer as P

STATUSES = ("todo", "doing", "done", "failed", "skipped")


def _state(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "work_brahman"


def parse_steps(specs: list[str], me: str) -> list[dict]:
    """"a1", "b1@mac" → [{"name": "a1", "owner": me}, {"name": "b1", "owner": "mac"}]."""
    out = []
    for s in specs:
        name, _, owner = s.partition("@")
        name, owner = name.strip(), (owner.strip() or me)
        if not name or not P.NAME_RE.match(owner) or len(name) > 80:
            raise ValueError(f"a step is <name> or <name>@<machine>: {s!r}")
        if any(x["name"] == name for x in out):
            raise ValueError(f"step {name!r} twice")
        out.append({"name": name, "owner": owner})
    return out


def view(story: str, env=None) -> dict | None:
    """The whole story from the events kept here: title, steps with owner, status, by, last
    note. None before its plan has come."""
    evs = P.events(story, os.environ if env is None else env)
    plan = next((e for e in evs if e["what"] == "plan"), None)
    if plan is None:
        return None
    try:
        p = json.loads(plan.get("text") or "{}")
    except ValueError:
        return None
    steps = {s["name"]: {"name": s["name"], "owner": s["owner"], "status": "todo", "by": "", "note": "", "at": ""}
             for s in p.get("steps", []) if isinstance(s, dict) and "name" in s and "owner" in s}
    for e in evs:
        s = steps.get(e["step"])
        if s is None or e["origin"] != s["owner"]:  # only a step's owner speaks for it
            continue
        text = e.get("text", "")
        if e["what"] == "mark":  # "<status>: <note>"
            head, _, rest = text.partition(":")
            status, text = (head, rest.strip()) if head in STATUSES else ("doing", text)
        else:
            status = {"start": "doing", "done": "done", "fail": "failed"}.get(e["what"])
        if status:
            s["status"] = status
        if text and e["what"] in ("note", "mark", "start", "done", "fail"):
            s["note"] = text
        s["by"], s["at"] = e.get("by") or s["by"], e["at"]
    return {"story": story, "title": p.get("title", ""), "origin": plan["origin"], "short": p.get("short"),
            "steps": list(steps.values()), "events": len(evs)}


def stories(env=None) -> list[str]:
    return sorted({e["story"] for e in P.events("", os.environ if env is None else env)})


def _next_seq(story: str, me: str, env) -> int:
    return max((e["seq"] for e in P.events(story, env) if e["origin"] == me), default=0) + 1


def _push(cfg: dict, event: dict, key: bytes, sender: str) -> dict:
    """The event to every peer we may reach (not the send-only ones): {peer: what it said}."""
    said = {}
    for name, point in sorted(cfg["points"].items()):
        if point.get("direction") == "in":
            said[name] = "send-only: it pulls"
            continue
        try:
            said[name] = P.send_event(point["url"], sender, event, key)
        except (ValueError, OSError) as e:
            said[name] = f"not reached ({e}): sync later"
    return said


def _event(story: str, step: str, what: str, me: str, env, text: str = "", by: str = "") -> dict:
    e = {"story": story, "step": step, "origin": me, "seq": _next_seq(story, me, env), "what": what,
         "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    e.update({k: v for k, v in (("text", text), ("by", by)) if v})
    why = P.check_event(e, me)
    if why:
        raise ValueError(why)
    return e


def _linked_chats(config: Path) -> list[str]:
    try:
        return list(tomllib.loads(config.read_text()).get("me", {}).get("chats", []))
    except (OSError, ValueError):
        return []


def start(title: str, specs: list[str], cfg: dict, key: bytes, *, config: Path | None = None, env=None) -> tuple[str, dict]:
    """A shared story, started here: the local short story (its card, in the owner's chats)
    gives the number; the plan goes to every peer. (id, what each peer said)."""
    from . import install, short_stories as shorts
    env = os.environ if env is None else env
    me = cfg["name"]
    steps = parse_steps(specs, me)
    marks = [f"{s['name']}@{s['owner']}" for s in steps]
    config = config or install.config_path(None)
    local = shorts.start(_state(env), [], _linked_chats(config), title, marks, who=f"{me}/peer story")
    story = f"{me}#{local['id']}"
    e = _event(story, "plan", "plan", me, env, text=json.dumps({"title": title, "steps": steps, "short": local["id"]},
                                                                  ensure_ascii=False))
    P.keep_event(e, env)
    return story, _push(cfg, e, key, me)


def mark(story: str, step: str, status: str, cfg: dict, key: bytes, *, note: str = "", by: str = "", env=None) -> dict:
    """This machine's step moved: kept here, sent to every peer, and on the starting machine
    marked on its card. Only the step's owner may mark it."""
    env = os.environ if env is None else env
    me = cfg["name"]
    v = view(story, env)
    if v is None:
        raise ValueError(f"no plan for {story} here: buddy peer story sync first")
    s = next((x for x in v["steps"] if x["name"] == step), None)
    if s is None:
        raise ValueError(f"{story} has no step {step!r}: {', '.join(x['name'] for x in v['steps'])}")
    if s["owner"] != me:
        raise ValueError(f"{step} belongs to {s['owner']}: only it marks it")
    what = {"doing": "start", "done": "done", "failed": "fail"}.get(status, "mark")
    text = (f"{status}: {note}" if what == "mark" else note).strip()
    e = _event(story, step, what, me, env, text=text, by=by or f"{me}/{_session(env)}")
    P.keep_event(e, env)
    apply(e, me, env)
    return _push(cfg, e, key, me)


def _session(env) -> str:
    from . import short_stories as shorts
    try:
        return shorts.executor(env)["tag"]
    except Exception:  # noqa: BLE001 - only a label
        return "unknown"


def apply(event: dict, me: str, env=None) -> None:
    """On the machine a story started on, a step event marks the local short story (its card);
    elsewhere the event is only kept (the view shows it)."""
    from . import short_stories as shorts
    env = os.environ if env is None else env
    origin, _, n = event["story"].partition("#")
    if origin != me or event["what"] == "plan":
        return
    v = view(event["story"], env)
    s = next((x for x in (v or {}).get("steps", []) if x["name"] == event["step"]), None)
    if s is None or s["owner"] != event["origin"]:
        return
    name = f"{s['name']}@{s['owner']}"
    try:
        by = s["by"] or event["origin"]  # "<machine>/<session>": the step's lane in the swimlanes
        shorts.mark(_state(env), [], int(n), name, status=s["status"], note=s["note"], by={"by": by, "tag": by})
    except (ValueError, KeyError):  # the local story was ended or changed by hand: the view still has it
        pass


def pull(url: str, sender: str, key: bytes, story: str = "", now: float | None = None) -> list[dict]:
    """Every event the peer has (of one story, or all), signed by it over an empty body."""
    when = int(now if now is not None else time.time())
    q = f"?story={urllib.parse.quote(story)}" if story else ""
    req = urllib.request.Request(url.rstrip("/") + "/events" + q, method="GET", headers={
        "X-Peer-From": sender, "X-Peer-Time": str(when), "X-Peer-Sig": P.sign(key, sender, when, b"")})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise ValueError(f"refused: {e.read().decode().strip()}") from e


def sync(cfg: dict, key: bytes, env=None, peers: list[str] | None = None) -> dict:
    """Catch up both ways with each peer: send again what this machine started (kept once
    there), pull what it has (each event checked as if its origin sent it, kept once here,
    applied to our cards). {peer: (sent, new here) or why not}."""
    env = os.environ if env is None else env
    me, out = cfg["name"], {}
    mine = [e for e in P.events("", env) if e["origin"] == me]
    for name, point in sorted(cfg["points"].items()):
        if peers and name not in peers:
            continue
        sent = 0
        if point.get("direction") != "in":
            for e in mine:
                try:
                    P.send_event(point["url"], me, e, key)
                    sent += 1
                except (ValueError, OSError) as x:
                    out[name] = f"not reached ({x})"
                    break
            if name in out:
                continue
        try:
            got = pull(point["url"], me, key) if point.get("direction") != "in" else []
        except (ValueError, OSError) as x:
            out[name] = f"sent {sent}; pull failed ({x})"
            continue
        new = 0
        for e in got if isinstance(got, list) else []:
            if P.check_event(e, e.get("origin", "")) == "" and P.keep_event(e, env):
                new += 1
                apply(e, me, env)
        out[name] = (sent, new)
    return out


def show(v: dict) -> str:
    icon = {"todo": "⏳", "doing": "▶️", "done": "✅", "failed": "❌", "skipped": "⏭"}
    lines = [f"{v['story']}  {v['title']}  (started on {v['origin']}; {v['events']} events here)"]
    for s in v["steps"]:
        lines.append(f"  {icon.get(s['status'], '?')} {s['name']:<14} @{s['owner']:<12} {s['by']:<28} {s['note'][:60]}".rstrip())
    return "\n".join(lines)
