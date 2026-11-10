"""Teams (specs/team.md): one live card per kit, one row per long-lived member, linked to its
short stories. A team has no steps and no end, so it is not a story: roles join, age, hand over
and retire, and the team goes on.

    buddy team new app-release-kit --rm app-rm app-release-kit-1 --book role-release-manager.md
    buddy team join app-release-kit server-a1 --role server --area "the server, its daemon" --project ~/projects/server
    buddy team join web-release-kit --role po --kind po          a role already on another team
    buddy team handover server server-a1 server-g2                    the same for an RM or a PO
    buddy team leave web-release-kit po | retire server server-g2 | show NAME | check NAME | list

Only what can't be observed is kept, written by `buddy team` alone (the RM runs it): a file per
role (roles/<role>.json: its generations, so a role in two teams has one history) and one per
team (<team>.json: its roster of {role, kind}, its card, the owner's replies). Everything else
is looked at each time the card is drawn: the session (~/.claude/sessions/<pid>.json, by its
name), its pys window (`pys ls --json`, agent_owner), its ctx (the status line, `pys capture`),
its years (compactions in its transcript), its book, and its running short stories. A source
that can't be read draws as "-": it never fails the card.

The team only records; it never starts, stops or messages a session (the RM does, with the
owner's go). The card changes on an event (join, handover, retire, leave, a reply) and when the
banded facts change: a story started or ended, age across age_warn, a compaction, a session
gone or back. Never on a timer: the Lark quota (specs/team.md). The exact ctx is as of the last
edit; `buddy team show` gives it live.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from . import portable, short_stories as shorts
from .channel import Channel
from .text import t

KINDS = ("rm", "po", "member")
ORDER = {k: i for i, k in enumerate(KINDS)}
AGE_WARN = 60  # % ctx: the owner's threshold (2026-11-07); years are shown, never flagged
LOOK_EVERY = 60  # seconds between looks in `buddy serve`
HANDOVERS_SHOWN = 3
NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
CTX = re.compile(r"\bctx (\d+)% of (\S+)")
COMPACT = b'"subtype":"compact_boundary"'
WAKE = "team {team} has news from the owner: run buddy-team check {team}"
INDENT = shorts.INDENT
log = logging.getLogger("buddy.teams")


def settings(cfg: dict | None) -> dict:
    """[families.team] in buddy.toml: age_warn (% ctx), channels (the channel names the card
    goes to, e.g. ["telegram", "web"] while Lark is over its quota; empty: every chat)."""
    c = ((cfg or {}).get("families") or {}).get("team") or {}
    return {"age_warn": int(c.get("age_warn", AGE_WARN)), "channels": list(c.get("channels") or [])}


# ── the store ────────────────────────────────────────────────────────────────────────────────

def _dir(state: Path) -> Path:
    d = state / "teams" / "roles"
    d.mkdir(parents=True, exist_ok=True)
    return d.parent


@contextmanager
def _locked(state: Path):
    """One lock for every team and role: a handover changes a role and redraws each team that
    names it, so finer locks would only buy deadlocks. Changes are rare."""
    with portable.locked(_dir(state) / ".lock"):
        yield


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _read(p: Path) -> dict | None:
    return json.loads(p.read_text()) if p.exists() else None


def _write(p: Path, data: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(p)


def load(state: Path, team: str) -> dict | None:
    return _read(_dir(state) / f"{team}.json") if NAME.match(team) else None


def role(state: Path, name: str) -> dict | None:
    return _read(_dir(state) / "roles" / f"{name}.json") if NAME.match(name) else None


def all_teams(state: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(_dir(state).glob("*.json"))]


def all_roles(state: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((_dir(state) / "roles").glob("*.json"))]


def live(r: dict) -> dict | None:
    """The generation carrying the role now, or None once it retired."""
    g = r["generations"][-1] if r.get("generations") else None
    return g if g and not g.get("retired") else None


def sessions_to_roles(state: Path) -> dict[str, str]:
    """Every session that ever carried a role (each generation's name) -> that role. A session
    named in two roles maps to the last one read (not in the data as of 2026-11-10: 13 sessions,
    13 names); the team view's rows still list its stories under both."""
    return {g["name"]: r["role"] for r in all_roles(state) for g in r.get("generations", [])}


def member(story: dict, index: dict[str, str], team_of: dict[str, list[str]]) -> dict | None:
    """The team member a short story belongs to, from its `who` (a session's name): its role
    and the team to show it in. The team the story recorded first, then the role's only team;
    a role on several teams with none recorded gives them all ("teams"), for the page to ask."""
    role_name = index.get(story.get("who") or "")
    if not role_name:
        return None
    teams = team_of.get(role_name, [])
    if story.get("team") in teams:
        return {"role": role_name, "team": story["team"]}
    if len(teams) == 1:
        return {"role": role_name, "team": teams[0]}
    return {"role": role_name, "team": "", "teams": teams} if teams else None


def team_index(state: Path) -> dict[str, list[str]]:
    """role -> the teams whose roster has it."""
    out: dict[str, list[str]] = {}
    for tm in all_teams(state):
        for x in tm["roster"]:
            out.setdefault(x["role"], []).append(tm["team"])
    return out


def teams_of(state: Path, role_name: str) -> list[str]:
    return [tm["team"] for tm in all_teams(state) if any(x["role"] == role_name for x in tm["roster"])]


def of_session(state: Path, name: str) -> list[str]:
    """The teams whose roster has a role carried now by the session called `name`."""
    carried = {r["role"] for r in all_roles(state) if (g := live(r)) and g["name"] == name}
    return [tm["team"] for tm in all_teams(state) if any(x["role"] in carried for x in tm["roster"])]


def _check_name(what: str, name: str) -> None:
    if not NAME.match(name or ""):
        raise ValueError(f"{what} {name!r}: lower case letters, digits, '.', '_' and '-' only")


def _taken(state: Path, name: str) -> str | None:
    """The role a generation name already belongs to: a name is never reused."""
    for r in all_roles(state):
        if any(g["name"] == name for g in r["generations"]):
            return r["role"]
    return None


def _event(team: dict, text: str, **keys) -> None:
    team.setdefault("events", []).append({"at": _now(), "text": text, **keys})
    team["events"] = team["events"][-50:]


def new(state: Path, channels: list[Channel], chats: list[str], team: str, rm_role: str,
        rm_name: str = "", *, area: str = "", project: str = "", book: str = "",
        opts: dict | None = None) -> dict:
    """A team, its RM's role (new, or one that exists already), and its card."""
    _check_name("team", team)
    with _locked(state):
        if load(state, team):
            raise ValueError(f"team {team} exists already")
        tm = {"team": team, "started": _now(), "chats": chats, "copies": {}, "roster": [],
              "asks": [], "events": []}
        _enrol(state, tm, rm_role, rm_name, "rm", area, project, book)
        _event(tm, t("team.ev.new", rm=_who(state, rm_role)))
        _save(state, tm)
        return _draw(state, channels, tm, opts, force=True)


def join(state: Path, channels: list[Channel], team: str, role_name: str, name: str = "", *,
         kind: str = "member", area: str = "", project: str = "", book: str = "",
         opts: dict | None = None) -> dict:
    """A role joins the team's roster: a new role with its first generation `name`, or a role
    another team has already (no name, or its live one's)."""
    with _locked(state):
        tm = _team(state, team)
        if any(x["role"] == role_name for x in tm["roster"]):
            raise ValueError(f"{role_name} is on team {team} already")
        _enrol(state, tm, role_name, name, kind, area, project, book)
        _event(tm, t("team.ev.join", who=_who(state, role_name), kind=t(f"team.kindname.{kind}")))
        _save(state, tm)
        out = _draw(state, channels, tm, opts, force=True)
        _others(state, channels, role_name, team, opts)
        return out


def _enrol(state: Path, tm: dict, role_name: str, name: str, kind: str, area: str, project: str,
           book: str) -> None:
    _check_name("role", role_name)
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r}: one of {', '.join(KINDS)}")
    if kind in ("rm", "po") and any(x["kind"] == kind for x in tm["roster"]):
        raise ValueError(f"team {tm['team']} has its {kind} already: one per team")
    r = role(state, role_name)
    if r is None:
        _check_name("session", name)
        if (other := _taken(state, name)):
            raise ValueError(f"{name} is a generation of {other} already: a name is never reused")
        r = {"role": role_name, "area": area, "project": project, "book": book or f"role-{role_name}.md",
             "started": _now(), "generations": [{"name": name, "gen": 1, "joined": _now(), "retired": None}]}
    else:
        g = live(r)
        if g is None:
            raise ValueError(f"role {role_name} has retired")
        if name and name != g["name"]:
            raise ValueError(f"role {role_name} is carried by {g['name']}, not {name}: "
                             f"use handover for a new generation")
        for k, v in (("area", area), ("project", project), ("book", book)):
            if v:
                r[k] = v
    _write(_dir(state) / "roles" / f"{role_name}.json", r)
    tm["roster"].append({"role": role_name, "kind": kind, "joined": _now()})


def handover(state: Path, channels: list[Channel], role_name: str, old: str, new_name: str, *,
             note: str = "", opts: dict | None = None) -> list[dict]:
    """The old generation retires, the new one carries the role: the same for an RM, a PO or an
    area member, and the teams' files don't change. Every team naming the role is redrawn."""
    with _locked(state):
        r = _role(state, role_name)
        g = live(r)
        if g is None or g["name"] != old:
            raise ValueError(f"{role_name} is carried by {g['name'] if g else 'nobody'}, not {old}")
        _check_name("session", new_name)
        if (other := _taken(state, new_name)):
            raise ValueError(f"{new_name} is a generation of {other} already: a name is never reused")
        g["retired"] = _now()
        r["generations"].append({"name": new_name, "gen": g["gen"] + 1, "joined": _now(), "retired": None,
                                 "after": old, **({"note": note} if note else {})})
        _write(_dir(state) / "roles" / f"{role_name}.json", r)
        return _redraw_role(state, channels, role_name, opts, "handover", t(
            "team.ev.handover", old=old, gen=g["gen"], new=new_name, book=r.get("book", "")),
            role=role_name, old=old, new=new_name, book=r.get("book", ""))


def retire(state: Path, channels: list[Channel], role_name: str, name: str, *, note: str = "",
           opts: dict | None = None) -> list[dict]:
    """The role ends, no successor: off every roster. An RM's role can't: a team needs its RM,
    so it is handed over instead."""
    with _locked(state):
        r = _role(state, role_name)
        g = live(r)
        if g is None or g["name"] != name:
            raise ValueError(f"{role_name} is carried by {g['name'] if g else 'nobody'}, not {name}")
        rms = [tm["team"] for tm in all_teams(state)
               if any(x["role"] == role_name and x["kind"] == "rm" for x in tm["roster"])]
        if rms:
            raise ValueError(f"{role_name} is the RM of {', '.join(rms)}: a team needs its RM, hand it over")
        g["retired"] = _now()
        if note:
            g["note"] = note
        _write(_dir(state) / "roles" / f"{role_name}.json", r)
        out = []
        for team in teams_of(state, role_name):
            tm = load(state, team)
            tm["roster"] = [x for x in tm["roster"] if x["role"] != role_name]
            _event(tm, _noted(t("team.ev.retire", who=name, gen=g["gen"]), note), kind="retire", role=role_name)
            _save(state, tm)
            out.append(_draw(state, channels, tm, opts, force=True))
        return out


def leave(state: Path, channels: list[Channel], team: str, role_name: str, *,
          opts: dict | None = None) -> dict:
    """Off this team's roster; the role goes on elsewhere (or nowhere, still not retired)."""
    with _locked(state):
        tm = _team(state, team)
        x = next((x for x in tm["roster"] if x["role"] == role_name), None)
        if x is None:
            raise ValueError(f"{role_name} is not on team {team}")
        if x["kind"] == "rm":
            raise ValueError(f"{role_name} is the RM of {team}: a team needs its RM, hand it over")
        tm["roster"].remove(x)
        _event(tm, t("team.ev.leave", who=_who(state, role_name)))
        _save(state, tm)
        out = _draw(state, channels, tm, opts, force=True)
        _others(state, channels, role_name, team, opts)
        return out


def _others(state: Path, channels: list[Channel], role_name: str, team: str, opts: dict | None) -> None:
    """The other teams naming the role: their "also" changed, so their banded facts did."""
    for name in teams_of(state, role_name):
        if name != team:
            _draw(state, channels, load(state, name), opts)


def _redraw_role(state, channels, role_name, opts, kind, text, **keys) -> list[dict]:
    out = []
    for name in teams_of(state, role_name):
        tm = load(state, name)
        _event(tm, text, kind=kind, **keys)
        _save(state, tm)
        out.append(_draw(state, channels, tm, opts, force=True))
    return out


def _team(state: Path, team: str) -> dict:
    tm = load(state, team)
    if tm is None:
        raise ValueError(f"no team {team!r}")
    return tm


def _role(state: Path, name: str) -> dict:
    r = role(state, name)
    if r is None:
        raise ValueError(f"no role {name!r}")
    return r


def _save(state: Path, tm: dict) -> None:
    _write(_dir(state) / f"{tm['team']}.json", tm)


def _who(state: Path, role_name: str) -> str:
    r = role(state, role_name) or {}
    g = live(r) if r else None
    return g["name"] if g else role_name


def _kind(kind: str) -> str:
    return t(f"team.kind.{kind}")


def _noted(head: str, note: str) -> str:
    return f"{head}: {note}" if note else head


# ── the owner's replies ──────────────────────────────────────────────────────────────────────

def reply(state: Path, channels: list[Channel], team: str, text: str, *, where: str, who: str = "",
          opts: dict | None = None) -> dict | None:
    """What the owner wrote on the card (💬 Reply, quoting it, /team NAME <text>): kept for the
    RM, printed once by `buddy team check`; its window is woken (wake())."""
    with _locked(state):
        tm = load(state, team)
        if tm is None:
            return None
        asks = tm.setdefault("asks", [])
        asks.append({"id": len(asks) + 1, "at": _now(), "where": where, "who": who, "text": text,
                     "read": False})
        _save(state, tm)
        return _draw(state, channels, tm, opts)


def news(state: Path, team: str) -> list[dict] | None:
    """For the RM: the replies not read yet, each once. None: no such team."""
    with _locked(state):
        tm = load(state, team)
        if tm is None:
            return None
        fresh = [a for a in tm.get("asks", []) if not a["read"]]
        for a in fresh:
            a["read"] = True
        if fresh:
            _save(state, tm)
        return fresh


def prompted(state: Path, team: str, chat: str, handle: str) -> None:
    """The message 💬 Reply sent (Telegram): an answer to it is a reply to this team."""
    with _locked(state):
        tm = load(state, team)
        if tm:
            tm.setdefault("prompts", {}).setdefault(chat, []).append(handle)
            tm["prompts"][chat] = tm["prompts"][chat][-20:]
            _save(state, tm)


def find(state: Path, chat: str, message_id: str) -> str | None:
    """The team whose card (or reply prompt) message_id is, in chat."""
    for tm in all_teams(state):
        if tm["copies"].get(chat) == message_id or message_id in tm.get("prompts", {}).get(chat, []):
            return tm["team"]
    return None


# ── looking ──────────────────────────────────────────────────────────────────────────────────
# Each source on its own, so a test fakes it (conftest refuses the real ones) and one that
# fails gives "-" for its column only.

def _claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()


def _sessions() -> dict[str, dict]:
    """Live Claude Code sessions by name: pid, sessionId, cwd. A file left by a process that is
    gone (or a later process given its number) doesn't count."""
    out = {}
    for p in sorted((_claude_home() / "sessions").glob("*.json"), key=lambda p: p.stat().st_mtime):
        try:
            s = json.loads(p.read_text())
            pid = int(s["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        proc = portable.process(pid)
        if s.get("name") and proc and proc["comm"] == "claude":
            out[s["name"]] = {"pid": pid, "sessionId": s.get("sessionId", ""), "cwd": s.get("cwd", ""),
                              "status": s.get("status", ""), "started": proc["started"]}
    return out


def _pys_windows() -> dict[int, int]:
    """claude pid → pys window, from `pys ls --json` (agent_owner)."""
    try:
        p = subprocess.run(["pys", "ls", "--json"], capture_output=True, text=True, timeout=5)
        ws = json.loads(p.stdout).get("windows", []) if p.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    return {w["agent_owner"]: w["wid"] for w in ws if w.get("agent_owner")}


def _ctx(wid: int) -> tuple[int, str] | None:
    """The `ctx N% of <size>` on the window's status line (the template's statusline.sh)."""
    try:
        p = subprocess.run(["pys", "capture", str(wid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    hits = CTX.findall(p.stdout) if p.returncode == 0 else []
    return (int(hits[-1][0]), hits[-1][1]) if hits else None


_YEARS: dict[str, tuple[int, int, int]] = {}  # transcript → (inode, bytes read, compactions)


def _years(session_id: str) -> int | None:
    """Compactions so far: the transcript's compact_boundary lines (undocumented, as `agents`
    reads it). Read on from where the last look stopped, so a long transcript costs once."""
    if not session_id:
        return None
    hits = list((_claude_home() / "projects").glob(f"*/{session_id}.jsonl"))
    if not hits:
        return None
    path = hits[0]
    try:
        st = path.stat()
        ino, done, n = _YEARS.get(str(path), (st.st_ino, 0, 0))
        if ino != st.st_ino or st.st_size < done:
            ino, done, n = st.st_ino, 0, 0
        with path.open("rb") as f:
            f.seek(done)
            for line in f:
                if not line.endswith(b"\n"):  # half written: read it next time
                    break
                done += len(line)
                n += COMPACT in line
    except OSError:
        return None
    _YEARS[str(path)] = (ino, done, n)
    return n


def _slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(path).expanduser()))


def _book(project: str, book: str) -> float | None:
    """When the role's book was last written, or None: no book there."""
    if not project or not book:
        return None
    p = _claude_home() / "projects" / _slug(project) / "memory" / book
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def observe(state: Path, tm: dict) -> list[dict]:
    """The card's rows, RM first, then the PO, then the members, as they are right now."""
    sessions, windows = _sessions(), _pys_windows()
    stories = [s for s in shorts.all_stories(state) if s["status"] == "running"]
    rows = []
    for x in sorted(tm["roster"], key=lambda x: ORDER.get(x["kind"], 9)):
        r = role(state, x["role"]) or {"role": x["role"], "generations": []}
        g = live(r) or (r["generations"][-1] if r["generations"] else {"name": x["role"], "gen": 0})
        s = sessions.get(g["name"])
        pid = s["pid"] if s else None
        wid = windows.get(pid) if pid else None
        ctx = _ctx(wid) if wid is not None else None
        mine = [st for st in stories if (pid and (st.get("window") or {}).get("pid") == pid)
                or (st.get("who") and st["who"] == g["name"])]
        rows.append({"kind": x["kind"], "role": x["role"], "area": r.get("area", ""), "name": g["name"],
                     "gen": g["gen"], "live": bool(s), "pid": pid, "wid": wid,
                     "started": s["started"] if s else "",
                     "ctx": ctx[0] if ctx else None, "csize": ctx[1] if ctx else "",
                     "years": _years(s["sessionId"]) if s else None,
                     "book": _book(r.get("project", ""), r.get("book", "")),
                     "stories": [{"id": st["id"], "part": bool(st.get("parent")),
                                  "step": (shorts._here(st) or {}).get("name", "")}
                                 for st in sorted(mine, key=lambda st: -st["id"])],
                     "also": [n for n in teams_of(state, x["role"]) if n != tm["team"]]})
    return rows


# ── the card ─────────────────────────────────────────────────────────────────────────────────

def title(tm: dict) -> str:
    return t("team.title", team=tm["team"])


def markdown(tm: dict, rows: list[dict], age_warn: int = AGE_WARN) -> str:
    lines = []
    for r in rows:
        head = f"**{_kind(r['kind'])}** {r['name']} (g{r['gen']})" + (f" · {r['area']}" if r["area"] else "")
        win = f"w{r['wid']}" if r["wid"] is not None else (t("team.no_window") if r["live"] else t("team.gone"))
        age = (t("team.years", n=r["years"]) if r["years"] is not None else "-") + " · " \
            + (f"{r['ctx']}%" + (" ⚠️" if r["ctx"] >= age_warn else "") if r["ctx"] is not None else "-")
        book = "📖 " + (time.strftime("%m-%d", time.localtime(r["book"])) if r["book"] else "-")
        now = ", ".join(f"#{s['id']}" + (f" ▶ {s['step']}" if s["step"] else "") + (" " + t("team.part") if s["part"] else "")
                        for s in r["stories"][:3]) or "-"
        also = (" · " + t("team.also", teams=", ".join(r["also"]))) if r["also"] else ""
        lines += [head, f"{INDENT}{win} · {age} · {book} · {now}{also}"]
    if not rows:
        lines.append(t("team.empty"))
    handovers = [e for e in tm.get("events", []) if e.get("kind") == "handover"][-HANDOVERS_SHOWN:]
    if handovers:
        lines.append("")
        lines += [f"{INDENT}{e['text']} · {e['at'][5:10]} {e['at'][11:16]}" for e in handovers]
    unread = sum(not a["read"] for a in tm.get("asks", []))
    if unread:
        lines.append(t("team.unread", n=unread))
    if (last := [e for e in tm.get("events", []) if e.get("kind") != "handover"][-1:]):
        lines.append(f"_{last[0]['text']}_ · {last[0]['at'][11:16]}")
    lines.append(t("team.as_of", at=time.strftime("%H:%M")))
    return "\n".join(lines)


def digest(tm: dict, rows: list[dict], age_warn: int = AGE_WARN) -> str:
    """The banded facts: what makes the card worth an edit. Age is only on either side of
    age_warn, never the exact ctx; a story's step is not in it (its own card has those)."""
    facts = [[r["kind"], r["name"], r["gen"], r["wid"] if r["live"] else "gone", r["years"],
              r["ctx"] is not None and r["ctx"] >= age_warn, bool(r["book"]),
              [s["id"] for s in r["stories"]], r["also"]] for r in rows]
    facts.append(sum(not a["read"] for a in tm.get("asks", [])))
    facts.append([e["at"] for e in tm.get("events", [])][-1:])
    return hashlib.sha1(json.dumps(facts).encode()).hexdigest()[:16]


def buttons(tm: dict) -> list[tuple[str, str]]:
    """💬 Reply only (the owner's decision, 2026-11-07): it goes to the RM."""
    return [(t("btn.reply"), f"team:{tm['team']}:reply")]


def _draw(state: Path, channels: list[Channel], tm: dict, opts: dict | None, force: bool = False) -> dict:
    """Post the card where it is not yet, change it where it is: only when forced (an event)
    or when the banded facts changed. Called under the lock."""
    opts = opts or settings(None)
    rows = observe(state, tm)
    d = digest(tm, rows, opts["age_warn"])
    if not force and d == tm.get("digest"):
        return tm
    only = opts["channels"]
    md = markdown(tm, rows, opts["age_warn"])
    for chat in tm["chats"]:
        ch = next((c for c in channels if c.owns(chat)), None)
        if ch is None or not hasattr(ch, "track") or (only and ch.name not in only):
            continue
        handle = ch.track(chat, md, title=title(tm), handle=tm["copies"].get(chat), buttons=buttons(tm))
        if handle:
            tm["copies"][chat] = handle
    # Kept even when a chat refused the edit: a failed one waits for the next change rather
    # than being tried again every minute (the Lark quota).
    tm["digest"] = d
    _save(state, tm)
    return tm


def draw(state: Path, channels: list[Channel], team: str, opts: dict | None = None,
         force: bool = False) -> dict | None:
    with _locked(state):
        tm = load(state, team)
        return _draw(state, channels, tm, opts, force) if tm else None


def show(state: Path, team: str, opts: dict | None = None) -> str | None:
    """The card's text, looked at now (exact ctx), for the terminal and /team NAME."""
    tm = load(state, team)
    if tm is None:
        return None
    opts = opts or settings(None)
    return f"**{title(tm)}**\n\n{markdown(tm, observe(state, tm), opts['age_warn'])}"


def board(state: Path) -> str:
    """Every team, one line each, for /team and `buddy team list`."""
    out = []
    for tm in all_teams(state):
        rm = next((x["role"] for x in tm["roster"] if x["kind"] == "rm"), "")
        out.append(t("team.board_line", team=tm["team"], rm=_who(state, rm) if rm else "-",
                     n=len(tm["roster"])))
    return "\n".join(out) or t("team.none")


# ── buddy serve ──────────────────────────────────────────────────────────────────────────────

def _rm_window(state: Path, tm: dict) -> dict | None:
    """Where the RM can be woken: its claude in a pys window, as short_stories.hold records it."""
    rm = next((x["role"] for x in tm["roster"] if x["kind"] == "rm"), None)
    r = role(state, rm) if rm else None
    g = live(r) if r else None
    s = _sessions().get(g["name"]) if g else None
    wid = _pys_windows().get(s["pid"]) if s else None
    if wid is None:
        return None
    return {"mux": "pys", "sty": "", "window": str(wid), "session": "", "pid": s["pid"],
            "started": s["started"], "tag": tm["team"]}


def wake(state: Path) -> list[str]:
    """Type the fixed line into the RM's window when a reply waits, once per reply, under the
    short stories' rules (long_stories.wake): never the owner's words."""
    from . import long_stories as longs
    woken = []
    for tm in all_teams(state):
        fresh = {a["id"] for a in tm.get("asks", []) if not a["read"]} - set(tm.get("woken", []))
        if not fresh or not (w := _rm_window(state, tm)):
            continue
        if longs.wake(w, WAKE.format(team=tm["team"])):
            with _locked(state):
                cur = load(state, tm["team"])
                cur["woken"] = sorted(set(cur.get("woken", [])) | fresh)[-50:]
                _save(state, cur)
            woken.append(tm["team"])
    return woken


def look(state: Path, channels: list[Channel], opts: dict | None = None) -> list[str]:
    """Redraw the cards whose banded facts changed. Returns their teams."""
    drawn = []
    for tm in all_teams(state):
        before = tm.get("digest")
        after = draw(state, channels, tm["team"], opts)
        if after and after.get("digest") != before:
            drawn.append(tm["team"])
    return drawn


def start_looker(state: Path, channels: list[Channel], opts: dict | None = None,
                 every: float = LOOK_EVERY, tick: float = 5) -> None:
    """`buddy serve`'s thread: wake the RM on a reply every few seconds, look at the teams
    every minute. Read-only on the sessions it looks at."""
    import threading

    def loop():
        last = 0.0
        while True:
            try:
                for n in wake(state):
                    log.info("team %s: its RM was woken", n)
            except Exception:
                log.exception("waking a team's RM failed")
            if time.monotonic() - last >= every:
                last = time.monotonic()
                try:
                    for n in look(state, channels, opts):
                        log.info("team %s: card redrawn", n)
                except Exception:
                    log.exception("looking at the teams failed")
            time.sleep(tick)
    threading.Thread(target=loop, name="team-looker", daemon=True).start()
