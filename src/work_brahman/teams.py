"""Teams (specs/team.md): one live card per kit, one row per long-lived member, linked to its
short stories. A team has no steps and no end, so it is not a story: members join, age, hand over
and retire, and the team goes on.

    buddy team new app-release-kit --rel app-rel-g1 --book role-release-manager.md
    buddy team join app-release-kit app-comp-audio-g1 --role comp --area audio --project ~/projects/app
    buddy team join web-release-kit app-po-g2 --role po          a member another team has already
    buddy team handover app-comp-audio-g1 app-comp-audio-g2   the same for a rel or a po
    buddy team rename app-release-kit-cf app-rel-g1 | set NAME --area A | retire TEAM NAME
    buddy team show TEAM | check TEAM | list

A member is keyed by its **lineage**, its name without `-g<N>` (`app-comp-audio`), unique across
every team; its generations hang under it. Its role (rel, po, comp, rev, dbg, edu) and area are
fields: one rel and one po per team, any number of the others, a comp per area (the PO, 2026-11-20).
Only what can't be observed is kept, written by `buddy team` alone (the RM runs it): a file per
member (members/<lineage>.json: role, area, book, generations, so a member on two teams has one
history) and one per team (<team>.json: its roster, its card, the owner's replies). Everything else
is looked at each time the card is drawn: the session (~/.claude/sessions/<pid>.json, by its
name), its pys window (`pys ls --json`, agent_owner), its ctx (the status line, `pys capture`),
its years (compactions in its transcript), its book, and its running short stories. A source
that can't be read draws as "-": it never fails the card.

The team only records; it never starts, stops or messages a session (the RM does, with the
owner's go). The card changes on an event (join, handover, rename, retire, a reply) and when the
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

KINDS = ("rel", "po", "comp", "rev", "dbg", "edu")  # the card's order (the PO, 2026-11-20)
ONE_PER_TEAM = ("rel", "po")  # any number of the others: a comp per area
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
    """teams/: a file per team (<team>.json) and members/, a file per member (<lineage>.json). The
    records of before 2026-11-20 (roles/, keyed by a role word) are converted once, here."""
    d = state / "teams"
    if (d / "roles").is_dir() or not (d / "members").is_dir():  # roles/ goes last: until then, not done
        d.mkdir(parents=True, exist_ok=True)
        with portable.locked(d / ".lock"):
            if (d / "roles").is_dir() or not (d / "members").is_dir():
                _convert(d)
    return d


@contextmanager
def _locked(state: Path):
    """One lock for every team and member: a handover changes a member and redraws each team that
    names it, so finer locks would only buy deadlocks. Changes are rare."""
    d = _dir(state)
    with portable.locked(d / ".lock"):
        yield


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _read(p: Path) -> dict | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _write(p: Path, data: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def lineage(name: str) -> str:
    """A member's key: its name without the generation (`app-comp-audio-g2` → `app-comp-audio`).
    A name of the old kind (`app-live-8f`) has no generation in it; see _key."""
    return re.sub(r"-g\d+$", "", name)


def _has_gen(name: str) -> bool:
    return lineage(name) != name


def _key(name: str, now: str) -> str:
    """The lineage a member is filed under when its live name is `name` and it is filed under `now`:
    a name in the new form says it; an old-kind name keeps the key it has, or is its own key when that
    key is a bare role word (`comp`, `rel`: they are what broke, one team couldn't hold two comps)."""
    if _has_gen(name):
        return lineage(name)
    return name if not now or now in KINDS or now in ("rm", "member") else now


def _guess(name: str) -> str:
    """An old "member" row's role, from its name's words; never po or rel, which only the old
    rosters' rm and po say. `buddy team set NAME --role R` corrects a wrong one."""
    words = set(name.split("-"))
    return "rev" if words & {"rev", "review"} else "dbg" if "dbg" in words else "edu" if "edu" in words else "comp"


def _convert(d: Path) -> None:
    """Once (2026-11-20): roles/<role>.json, keyed by a word the RM chose and global across teams,
    becomes members/<lineage>.json with the role a field (an old rm → rel, po → po, a member's from
    its name); every roster follows. The generations, names, dates, books and events are kept as
    they were, and roles/ stays beside it as roles.pre-lineage/ (results are never deleted)."""
    old, tmp, done = d / "roles", d / "members.tmp", d / "members"
    rosters = {p: json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))}
    if not done.is_dir():  # 1. the records, into members.tmp/, then renamed whole (the rosters still old)
        tmp.mkdir(exist_ok=True)
        kinds = {x["role"]: x.get("kind", "member") for tm in rosters.values() for x in tm.get("roster", []) if "role" in x}
        for p in sorted(old.glob("*.json")) if old.is_dir() else []:
            r = json.loads(p.read_text(encoding="utf-8"))
            was, gens = p.stem, r.get("generations") or []
            r.pop("role", None)
            name = (live(r) or (gens[-1] if gens else {"name": was}))["name"]
            key = _key(name, was)
            if not NAME.match(key) or (key != was and (tmp / f"{key}.json").exists()):
                key = was  # two old roles with one lineage (not in the data of 2026-11-20): it keeps its old key
            role_ = {"rm": "rel", "po": "po"}.get(kinds.get(was, "member")) or _guess(name)
            _write(tmp / f"{key}.json", {"lineage": key, "role": role_, **r, "converted_from": was})
        tmp.rename(done)
    moved = {r["converted_from"]: r["lineage"] for r in
             (json.loads(p.read_text(encoding="utf-8")) for p in done.glob("*.json")) if r.get("converted_from")}
    for p, tm in rosters.items():  # 2. the rosters (each entry once: a converted one is left alone)
        if any("role" in x for x in tm.get("roster", [])):
            tm["roster"] = [{"member": moved.get(x["role"], x["role"]), "joined": x.get("joined", "")}
                            if "role" in x else x for x in tm["roster"]]
            _write(p, tm)
    if old.is_dir():  # 3. last: kept beside, never deleted (a second one, e.g. an old buddy's, numbered)
        keep, n = d / "roles.pre-lineage", 2
        while keep.exists():
            keep, n = d / f"roles.pre-lineage.{n}", n + 1
        old.rename(keep)


def load(state: Path, team: str) -> dict | None:
    return _read(_dir(state) / f"{team}.json") if NAME.match(team) else None


def record(state: Path, key: str) -> dict | None:
    """A member's record by its lineage: its role, area, project, book and generations."""
    return _read(_dir(state) / "members" / f"{key}.json") if NAME.match(key) else None


def all_teams(state: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(_dir(state).glob("*.json"))]


def all_members(state: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted((_dir(state) / "members").glob("*.json"))]


def live(r: dict) -> dict | None:
    """The generation carrying the member now, or None once it retired."""
    g = r["generations"][-1] if r.get("generations") else None
    return g if g and not g.get("retired") else None


def carrying(state: Path, name: str) -> dict | None:
    """The member whose live generation is called `name`."""
    return next((r for r in all_members(state) if (g := live(r)) and g["name"] == name), None)


def sessions_to_members(state: Path) -> dict[str, str]:
    """Every session that ever carried a member (each generation's name) -> its lineage."""
    return {g["name"]: r["lineage"] for r in all_members(state) for g in r.get("generations", [])}


def member(story: dict, index: dict[str, str], team_of: dict[str, list[str]]) -> dict | None:
    """The team member a short story belongs to, from its `who` (a session's name): its lineage
    (`role` here: the page's links name it so) and the team to show it in. The team the story
    recorded first, then the member's only team; one on several teams with none recorded gives
    them all ("teams"), for the page to ask."""
    key = index.get(story.get("who") or "")
    if not key:
        return None
    teams = team_of.get(key, [])
    if story.get("team") in teams:
        return {"role": key, "team": story["team"]}
    if len(teams) == 1:
        return {"role": key, "team": teams[0]}
    return {"role": key, "team": "", "teams": teams} if teams else None


def team_index(state: Path) -> dict[str, list[str]]:
    """lineage -> the teams whose roster has it."""
    out: dict[str, list[str]] = {}
    for tm in all_teams(state):
        for x in tm["roster"]:
            out.setdefault(x["member"], []).append(tm["team"])
    return out


def teams_of(state: Path, key: str) -> list[str]:
    return [tm["team"] for tm in all_teams(state) if any(x["member"] == key for x in tm["roster"])]


def of_session(state: Path, name: str) -> list[str]:
    """The teams whose roster has the member carried now by the session called `name`."""
    r = carrying(state, name)
    return teams_of(state, r["lineage"]) if r else []


def roster(state: Path, tm: dict) -> list[tuple[dict, dict]]:
    """The team's (roster entry, member record) pairs in the card's order: by role (rel, po, comp,
    rev, dbg, edu), then area, then name. A record that is missing draws as a comp with no name."""
    out = []
    for x in tm["roster"]:
        r = record(state, x["member"]) or {"lineage": x["member"], "role": "comp", "generations": []}
        out.append((x, r))
    return sorted(out, key=lambda p: (ORDER.get(p[1].get("role"), 9), p[1].get("area", ""), _who_r(p[1])))


def _who_r(r: dict) -> str:
    g = live(r) or (r["generations"][-1] if r.get("generations") else None)
    return g["name"] if g else r["lineage"]


def _rel(state: Path, tm: dict) -> str:
    """The lineage of the team's rel (its RM), or ""."""
    return next((r["lineage"] for _, r in roster(state, tm) if r.get("role") == "rel"), "")


def _check_name(what: str, name: str) -> None:
    if not NAME.match(name or ""):
        raise ValueError(f"{what} {name!r}: lower case letters, digits, '.', '_' and '-' only")


def _names(gens: list[dict]) -> set[str]:
    return {n for g in gens for n in (g["name"], *g.get("renamed_from", []))}


def _taken(state: Path, name: str, own: str = "") -> str | None:
    """The lineage a generation name already belongs to: a name is never reused. A name held only
    in the merged history of `own` (a duplicate of the same member, merge()) is its own to take."""
    for r in all_members(state):
        if name in _names(r["generations"]):
            return r["lineage"]
        if r["lineage"] != own and any(name in _names(m["record"].get("generations", [])) for m in r.get("merged", [])):
            return r["lineage"]
    return None


def _carried(state: Path, name: str) -> dict:
    r = carrying(state, name)
    if r is None:
        raise ValueError(f"no member is carried by {name!r} now" + (
            f" ({name} was a generation of {k})" if (k := _taken(state, name)) else ""))
    return r


def _one_per_team(state: Path, tm: dict, role_: str, key: str) -> None:
    if role_ in ONE_PER_TEAM:
        for _, r in roster(state, tm):
            if r["lineage"] != key and r.get("role") == role_:
                raise ValueError(f"team {tm['team']} has its {role_} already ({_who_r(r)}): one per team")


def _event(team: dict, text: str, **keys) -> None:
    team.setdefault("events", []).append({"at": _now(), "text": text, **keys})
    team["events"] = team["events"][-50:]


def new(state: Path, channels: list[Channel], chats: list[str], team: str, rel: str, *, area: str = "",
        project: str = "", book: str = "", opts: dict | None = None) -> dict:
    """A team, its rel (the RM: a new member, or one carried now on another team), and its card."""
    _check_name("team", team)
    with _locked(state):
        if load(state, team):
            raise ValueError(f"team {team} exists already")
        tm = {"team": team, "started": _now(), "chats": chats, "copies": {}, "roster": [],
              "asks": [], "events": []}
        _enrol(state, tm, rel, "rel", area, project, book)
        _event(tm, t("team.ev.new", rm=rel))
        _save(state, tm)
        return _draw(state, channels, tm, opts, force=True)


def join(state: Path, channels: list[Channel], team: str, name: str, *, role: str = "comp", area: str = "",
         project: str = "", book: str = "", opts: dict | None = None) -> dict:
    """A member joins the team: the session `name` carries it. A new lineage starts at its first
    generation; a retired one gets its next; one carried now by `name` on another team is the same
    member there too. One rel and one po per team, any number of the others (a comp per area)."""
    with _locked(state):
        tm = _team(state, team)
        _enrol(state, tm, name, role, area, project, book)
        _event(tm, t("team.ev.join", who=name, kind=t(f"team.kindname.{role}")))
        _save(state, tm)
        out = _draw(state, channels, tm, opts, force=True)
        _others(state, channels, lineage_of(state, name), team, opts)
        return out


def lineage_of(state: Path, name: str) -> str:
    r = carrying(state, name)
    return r["lineage"] if r else ""


def _enrol(state: Path, tm: dict, name: str, role_: str, area: str, project: str, book: str) -> None:
    _check_name("name", name)
    if role_ not in KINDS:
        raise ValueError(f"role {role_!r}: one of {', '.join(KINDS)}")
    r = carrying(state, name)
    if r is None:
        if (other := _taken(state, name)):
            raise ValueError(f"{name} is a generation of {other} already: a name is never reused")
        key = lineage(name)
        r = record(state, key)
        if r and (g := live(r)):
            raise ValueError(f"{key} is carried by {g['name']}, not {name}: use handover for a new generation")
        if r:  # a retired lineage: its next generation
            last = r["generations"][-1]
            r["generations"].append({"name": name, "gen": last["gen"] + 1, "joined": _now(), "retired": None,
                                     "after": last["name"]})
            r["role"] = role_
        else:
            r = {"lineage": key, "role": role_, "area": area, "project": project, "book": book or f"role-{key}.md",
                 "started": _now(), "generations": [{"name": name, "gen": 1, "joined": _now(), "retired": None}]}
    elif r.get("role") != role_:
        raise ValueError(f"{name} is a {r.get('role')} already: `buddy team set {name} --role {role_}` changes it")
    key = r["lineage"]
    if any(x["member"] == key for x in tm["roster"]):
        raise ValueError(f"{name} is on team {tm['team']} already")
    _one_per_team(state, tm, role_, key)
    for k, v in (("area", area), ("project", project), ("book", book)):
        if v:
            r[k] = v
    _write(_dir(state) / "members" / f"{key}.json", r)
    tm["roster"].append({"member": key, "joined": _now()})


def _target(state: Path, r: dict, name: str) -> str:
    """The lineage the member is filed under once `name` carries it; refused when another has it."""
    key = _key(name, r["lineage"])
    if key != r["lineage"] and record(state, key):
        raise ValueError(f"lineage {key} exists already (another member): pick another name")
    return key


def _move(state: Path, r: dict, key: str) -> None:
    """Write the record under `key`, and every roster naming its old lineage with it."""
    was = r["lineage"]
    if key != was:
        r["lineage"] = key
        r.setdefault("lineages_before", []).append(was)
    _write(_dir(state) / "members" / f"{key}.json", r)  # the record first: a roster never names a missing one
    if key != was:
        for tm in all_teams(state):
            if any(x["member"] == was for x in tm["roster"]):
                tm["roster"] = [{**x, "member": key} if x["member"] == was else x for x in tm["roster"]]
                _save(state, tm)
        (_dir(state) / "members" / f"{was}.json").unlink(missing_ok=True)


def handover(state: Path, channels: list[Channel], old: str, new_name: str, *, note: str = "",
             opts: dict | None = None) -> list[dict]:
    """The old generation retires, the new one carries the member: the same for a rel, a po or a
    comp. Every team naming it is redrawn."""
    with _locked(state):
        r = _carried(state, old)
        _check_name("name", new_name)
        if (other := _taken(state, new_name)):
            raise ValueError(f"{new_name} is a generation of {other} already: a name is never reused")
        key = _target(state, r, new_name)
        g = live(r)
        g["retired"] = _now()
        r["generations"].append({"name": new_name, "gen": g["gen"] + 1, "joined": _now(), "retired": None,
                                 "after": old, **({"note": note} if note else {})})
        _move(state, r, key)
        return _redraw(state, channels, key, opts, "handover", t(
            "team.ev.handover", old=old, gen=g["gen"], new=new_name, book=r.get("book", "")),
            role=key, old=old, new=new_name, book=r.get("book", ""))


def rename(state: Path, channels: list[Channel], old: str, new_name: str, *,
           opts: dict | None = None) -> list[dict]:
    """The live generation's new name, the same generation (the owner, 2026-11-19: names put the context
    first, `<context>-<role>-g<N>`), found by its name on whichever team it is. Its history keeps the
    old one in `renamed_from`; no alias: the old name no longer finds it, and is never taken again."""
    with _locked(state):
        r = _carried(state, old)
        _check_name("name", new_name)
        if (other := _taken(state, new_name, own=r["lineage"])):
            raise ValueError(f"{new_name} is a generation of {other} already: a name is never reused")
        key = _target(state, r, new_name)
        g = live(r)
        g["name"] = new_name
        g.setdefault("renamed_from", []).append(old)
        _move(state, r, key)
        return _redraw(state, channels, key, opts, "rename",
                       t("team.ev.rename", old=old, new=new_name, gen=g["gen"]), role=key, old=old, new=new_name)


def retire(state: Path, channels: list[Channel], team: str, name: str, *, note: str = "",
           opts: dict | None = None) -> dict:
    """The member leaves the team's card; its record stays (history, books). On no team any more,
    its generation retires, and its lineage may get a new generation later (join). A team's rel
    can't: a team needs its RM, so it is handed over instead."""
    with _locked(state):
        tm = _team(state, team)
        r = _carried(state, name)
        key = r["lineage"]
        if not any(x["member"] == key for x in tm["roster"]):
            raise ValueError(f"{name} is not on team {team}")
        if r.get("role") == "rel":
            raise ValueError(f"{name} is the RM of {team}: a team needs its RM, hand it over")
        tm["roster"] = [x for x in tm["roster"] if x["member"] != key]
        still = [n for n in teams_of(state, key) if n != team]
        if not still:
            g = live(r)
            g["retired"] = _now()
            if note:
                g["note"] = note
            _write(_dir(state) / "members" / f"{key}.json", r)
        _event(tm, _noted(t("team.ev.leave" if still else "team.ev.retire", who=name), note),
               kind="retire", role=key)
        _save(state, tm)
        out = _draw(state, channels, tm, opts, force=True)
        _others(state, channels, key, team, opts)
        return out


def merge(state: Path, channels: list[Channel], dup: str, into: str, *, opts: dict | None = None) -> list[dict]:
    """A duplicate record of the same member (2026-11-20: the conversion left one member twice,
    `app-shadow-comp` and `comp-shadow`) folds into the live member `into`: the duplicate must be
    retired and on no team; its whole record is kept inside the member's (`merged`), and a copy of its
    file in teams/merged/. After it, the member may take a name held only in that history (rename)."""
    with _locked(state):
        r = _carried(state, into)
        d = record(state, dup)
        if d is None:
            raise ValueError(f"no member with the lineage {dup!r}")
        if d["lineage"] == r["lineage"]:
            raise ValueError(f"{dup} is {into}'s own lineage")
        if live(d) or teams_of(state, dup):
            raise ValueError(f"{dup} is still live or on a team: retire it from every team first")
        keep = _dir(state) / "merged"
        keep.mkdir(exist_ok=True)
        _write(keep / f"{dup}.{time.strftime('%Y%m%d-%H%M%S')}.json", d)
        r.setdefault("merged", []).append({"lineage": dup, "at": _now(), "record": d})
        _write(_dir(state) / "members" / f"{r['lineage']}.json", r)
        (_dir(state) / "members" / f"{dup}.json").unlink()
        return _redraw(state, channels, r["lineage"], opts, "merge", t("team.ev.merge", dup=dup, who=into),
                       role=r["lineage"], dup=dup)


def set_(state: Path, channels: list[Channel], name: str, *, role: str = "", area: str = "", project: str = "",
         book: str = "", opts: dict | None = None) -> list[dict]:
    """Correct a member's role, area, project or book (a role the conversion guessed, a comp's area);
    one rel and one po per team still holds. Every team naming it is redrawn."""
    with _locked(state):
        r = _carried(state, name)
        key = r["lineage"]
        if role:
            if role not in KINDS:
                raise ValueError(f"role {role!r}: one of {', '.join(KINDS)}")
            if r.get("role") == "rel" and role != "rel" and (rm := [n for n in teams_of(state, key)]):
                raise ValueError(f"{name} is the RM of {', '.join(rm)}: a team needs its RM, hand it over")
            for n in teams_of(state, key):
                _one_per_team(state, load(state, n), role, key)
        for k, v in (("role", role), ("area", area), ("project", project), ("book", book)):
            if v:
                r[k] = v
        _write(_dir(state) / "members" / f"{key}.json", r)
        what = ", ".join(f"{k} {v}" for k, v in (("role", role), ("area", area), ("project", project), ("book", book)) if v)
        return _redraw(state, channels, key, opts, "set", t("team.ev.set", who=name, what=what or "-"), role=key)


def _others(state: Path, channels: list[Channel], key: str, team: str, opts: dict | None) -> None:
    """The other teams naming the member: their "also" changed, so their banded facts did."""
    for name in teams_of(state, key):
        if name != team:
            _draw(state, channels, load(state, name), opts)


def _redraw(state, channels, key, opts, kind, text, **keys) -> list[dict]:
    out = []
    for name in teams_of(state, key):
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


def _save(state: Path, tm: dict) -> None:
    _write(_dir(state) / f"{tm['team']}.json", tm)


def _who(state: Path, key: str) -> str:
    r = record(state, key)
    return _who_r(r) if r else key


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
            s = json.loads(p.read_text(encoding="utf-8"))
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
        p = subprocess.run(["pys", "ls", "--json"], capture_output=True, text=True, timeout=5, encoding="utf-8",
                           errors="replace", env=portable.child_env())
        ws = json.loads(p.stdout).get("windows", []) if p.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}
    return {w["agent_owner"]: w["wid"] for w in ws if w.get("agent_owner")}


def _ctx(wid: int) -> tuple[int, str] | None:
    """The `ctx N% of <size>` on the window's status line (the template's statusline.sh)."""
    try:
        p = subprocess.run(["pys", "capture", str(wid)], capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace", env=portable.child_env())
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
    """The card's rows in roster() order (rel, po, comp, rev, dbg, edu; then area), as they are
    right now. `kind` is the member's role, `role` its lineage (the page's links name it so)."""
    sessions, windows = _sessions(), _pys_windows()
    stories = [s for s in shorts.all_stories(state) if s["status"] == "running"]
    rows = []
    for x, r in roster(state, tm):
        g = live(r) or (r["generations"][-1] if r["generations"] else {"name": x["member"], "gen": 0})
        s = sessions.get(g["name"])
        pid = s["pid"] if s else None
        wid = windows.get(pid) if pid else None
        ctx = _ctx(wid) if wid is not None else None
        mine = [st for st in stories if (pid and (st.get("window") or {}).get("pid") == pid)
                or (st.get("who") and st["who"] == g["name"])]
        rows.append({"kind": r.get("role", "comp"), "role": r["lineage"], "area": r.get("area", ""), "name": g["name"],
                     "gen": g["gen"], "live": bool(s), "pid": pid, "wid": wid,
                     "started": s["started"] if s else "",
                     "ctx": ctx[0] if ctx else None, "csize": ctx[1] if ctx else "",
                     "years": _years(s["sessionId"]) if s else None,
                     "book": _book(r.get("project", ""), r.get("book", "")),
                     "stories": [{"id": st["id"], "part": bool(st.get("parent")),
                                  "step": (shorts._here(st) or {}).get("name", "")}
                                 for st in sorted(mine, key=lambda st: -st["id"])],
                     "also": [n for n in teams_of(state, r["lineage"]) if n != tm["team"]]})
    return rows


# ── the card ─────────────────────────────────────────────────────────────────────────────────

def title(tm: dict) -> str:
    return t("team.title", team=tm["team"])


def markdown(tm: dict, rows: list[dict], age_warn: int = AGE_WARN) -> str:
    lines = []
    for r in rows:
        gen = "" if _has_gen(r["name"]) else f" (g{r['gen']})"  # a name in the new form says it
        head = f"**{_kind(r['kind'])}** {r['name']}{gen}" + (f" · {r['area']}" if r["area"] else "")
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
        rm = _rel(state, tm)
        out.append(t("team.board_line", team=tm["team"], rm=_who(state, rm) if rm else "-",
                     n=len(tm["roster"])))
    return "\n".join(out) or t("team.none")


# ── buddy serve ──────────────────────────────────────────────────────────────────────────────

def _rm_window(state: Path, tm: dict) -> dict | None:
    """Where the RM can be woken: its claude in a pys window, as short_stories.hold records it."""
    rm = _rel(state, tm)
    r = record(state, rm) if rm else None
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
