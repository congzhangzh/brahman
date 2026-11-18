"""A private/public tag on every short and long story, and the page's public view (the owner,
2026-11-14: "don't disturb brahman much: give every short and long story a tag, private or not,
and a UI switch to show them or not; the marking a script that takes the content and returns a
mark, rule-based; the buddy ships a sample and loads the user's own from ~/bin").

- **The tag** is set when a story is saved and its text changed (title, steps, notes, asks):
  `private` (bool), `private_by` (which marker said so, or "hand"), `private_sig` (the text's
  hash, so an unchanged story is not judged again).
- **The marker** is `brahman-privacy-mark`: the owner's own on PATH (or in ~/bin, ~/.local/bin,
  which a systemd unit's PATH may lack) wins; else the shipped sample (privacy_mark.py, rules
  only). JSON in, one word out. Missing, failing, slow (over TIMEOUT) or unclear: **private**.
  A script, $0 (TEAM.md: automatic work is script only).
- **By hand** (`buddy-short private <n> on|off|auto`, `buddy-long private …`): a hand mark wins
  until `auto` gives the story back to the marker.
- **The public view** is a cookie per browser (web.py): private stories, their chat cards and
  threads, and the Setup tab are left out of what the page is sent.
"""

from __future__ import annotations

import hashlib
import re
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

_run = subprocess.run  # bound once: a test that fakes subprocess.run for its own command keeps this one real
NAME = "brahman-privacy-mark"
SAMPLE = Path(__file__).with_name("privacy_mark.py")
TIMEOUT = 2.0
HAND = "hand"


def text_of(kind: str, story: dict) -> str:
    """What a person reads of the story; not its machine fields (who ran it where, chat ids)."""
    parts = [story.get("title", "")]
    if kind == "short":
        parts += [story.get("next_step") or ""]
        for m in story.get("marks", []):
            parts += [m.get("name", ""), m.get("note", ""), m.get("tag", "")]
            parts += [s.get("name", "") + " " + s.get("note", "") for s in m.get("steps", []) if isinstance(s, dict)]
            for key in ("in", "out"):  # --in/--out values: the page's detail shows them (review, 2026-11-14)
                parts += [f"{k}={v}" for k, v in (m.get(key) or {}).items()]
            for r in (m.get("loop") or {}).get("rounds", []):  # a loop's rounds: often rig or host names
                parts += [str(r.get("name", "")), str(r.get("note", ""))]
            for o in (m.get("fork") or {}).get("options", []):
                parts += [str(o.get("name", ""))] + [str(x.get("name", "")) for x in o.get("steps", []) if isinstance(x, dict)]
        for e in story.get("events", []):
            parts += [e.get("text", "")] + [f"{k}={v}" for key in ("in", "out") for k, v in (e.get(key) or {}).items()]
        for q in story.get("questions", []):
            if isinstance(q, dict):
                parts += [q.get("text", "")] + [str(o) for o in q.get("options", [])]
                parts += [str((q.get("answer") or {}).get("text", ""))]
        parts += [str(r.get("text", "")) for r in story.get("replies", [])]
    else:
        parts += [story.get("prompt", ""), story.get("cwd", "")]
        parts += [m.get("text", "") for m in story.get("inbox", [])]
        parts += [e.get("text", "") for e in story.get("events", [])]
    return "\n".join(str(p) for p in parts if p)


def sig(kind: str, story: dict) -> str:
    return hashlib.sha1(text_of(kind, story).encode()).hexdigest()[:16]


def words() -> list[str]:
    from . import notify
    return [str(w) for w in ((notify.config().get("privacy") or {}).get("words") or [])]


def marker() -> tuple[list[str], str]:
    """(the command, its name for private_by): the owner's own first, else the sample."""
    home = Path.home()
    path = os.pathsep.join([os.environ.get("PATH", ""), str(home / "bin"), str(home / ".local" / "bin")])
    own = shutil.which(NAME, path=path)
    if own:
        return [own], own.replace(str(home), "~", 1)
    return [sys.executable, "-I", str(SAMPLE)], "sample"


def judge(kind: str, story: dict, extra: list[str] | None = None) -> tuple[bool, str]:
    """(private, who said so). Fail closed: anything but a clear "public" is private."""
    cmd, who = marker()
    payload = {"kind": kind, "text": text_of(kind, story), "words": extra if extra is not None else words(),
               "story": story}
    try:
        p = _run(cmd, input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                           text=True, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as e:
        return True, f"{who} (failed: {type(e).__name__}, so private)"
    said = (p.stdout.split() or [""])[0].lower()
    if p.returncode != 0 or said not in ("private", "public"):
        return True, f"{who} (failed: exit {p.returncode}, said {said or 'nothing'!r}, so private)"
    return said == "private", who


def tag(kind: str, story: dict) -> None:
    """Before a save: judge again when the text changed, unless marked by hand."""
    if story.get("private_by") == HAND:
        return
    s = sig(kind, story)
    if story.get("private_sig") == s and "private" in story:
        return
    story["private"], story["private_by"] = judge(kind, story)
    story["private_sig"] = s


def by_hand(story: dict, value: str) -> None:
    """on, off or auto."""
    if value == "auto":
        story.pop("private_sig", None)
        story["private_by"] = ""
    else:
        story["private"], story["private_by"] = value == "on", HAND


def is_private(story: dict | None) -> bool:
    """A story never judged (from before the tag) counts as private: fail closed."""
    return story is None or story.get("private", True) is not False


# ---- the page's public view: a cookie per browser, at most VIEW_HOURS --------------------------

VIEW_COOKIE = "buddy_view"
VIEW_HOURS = 3


def view_since(cookie_header: str, now: float | None = None) -> float:
    """When this browser's public view began (its cookie), or 0: the private view, his default."""
    import time
    now = now or time.time()
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == VIEW_COOKIE and v.startswith("public-") and v[7:].isdigit():
            since = float(v[7:])
            return since if 0 <= now - since <= VIEW_HOURS * 3600 else 0.0
    return 0.0


def view_cookie(public: bool, now: float | None = None) -> str:
    """Lax, not Strict: a Strict cookie is left off a navigation that starts elsewhere (a new tab, a
    notification's link) but rides every fetch, so the page came up unmarked over filtered data
    (2026-11-14). A cookie knows no port: every page on 127.0.0.1 shares it."""
    import time
    if not public:
        return f"{VIEW_COOKIE}=; SameSite=Lax; Path=/; Max-Age=0"
    return f"{VIEW_COOKIE}=public-{int(now or time.time())}; SameSite=Lax; Path=/; Max-Age={VIEW_HOURS * 3600}"


# ---- by hand, from the command line and the page: stories and teams ------------------------------
# A team has no marker: there are few, and its card is names and windows. Private until marked.

def _edit(state: Path, kind: str, ident: str, fn) -> dict | None:
    """Load a story ("short", "long": its number) or a team ("team": its name) under its lock, let
    fn change it (fn returns False: nothing changed, no save), save it; returns it, or None."""
    from . import long_stories, short_stories, teams
    if kind == "team":
        with teams._locked(state):
            tm = teams.load(state, ident)
            if tm is not None and fn(tm) is not False:
                teams._save(state, tm)
        return tm
    mod = short_stories if kind == "short" else long_stories
    if not str(ident).isdigit():
        return None
    with mod._locked(state, str(ident)):
        story = mod.load(state, int(ident))
        if story is not None and fn(story) is not False:
            mod._save(state, story)  # a story's save judges its privacy again (auto)
    return story


def mark(state: Path, kind: str, ident: str, value: str | None) -> dict | None:
    """Set (on, off, auto) or just read the private mark; auto is for stories."""
    def change(rec):
        if not value or (kind == "team" and value == "auto"):
            return False
        if kind == "team":
            rec["private"], rec["private_by"] = value == "on", HAND
        else:
            by_hand(rec, value)
    return _edit(state, kind, ident, change)


def demo(state: Path, kind: str, ident: str, value: str | None) -> dict | None:
    """The ⭐ demo tag (the owner, 2026-11-14: "a demo or presentation tag ... I can switch it on as a
    filter, for quick demos and focused demos"): by hand only, off until set; on, off, or None to read.
    It never makes anything public: the public view still leaves out a private demo."""
    def change(rec):
        if value not in ("on", "off"):
            return False
        rec["demo"] = value == "on"
    return _edit(state, kind, ident, change)


KINDS = {"short-story": "short", "long-story": "long", "team": "team"}  # a run's id prefix → mark()'s kind


def describe(kind: str, rec: dict) -> str:
    name = rec.get("team") if kind == "team" else f"#{rec['id']}"
    star = ", ⭐ demo" if rec.get("demo") else ""
    return f"{name}: {'private' if is_private(rec) else 'public'} ({rec.get('private_by') or 'not marked yet'}){star}"


# ---- what the public view sends: machine fields out, then one last check (review, 2026-11-14) ------

MACHINE = {"win", "window", "chats", "copies", "pid", "sty", "session_id", "transcript",
           "links"}  # a step's links: a private repo's URL, and visibility is never known here (fails closed)
MD_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://[^)\s]+)\)")
URL = re.compile(r"https?://[^\s)\]>\"']+")
REPO_REF = re.compile(r"\b[\w.-]+/[\w.-]+(?:/[\w.-]+)*([#@])(?=[\w.+-])")  # owner/name#123 → #123, group/proj@sha → @sha (links.py takes both)
REPO_KV = re.compile(r"\brepo=\S+")


def who(by: str) -> str:
    """A step's `by` without the machine: "buddy@host/my-repo:claude-ab12/sub:x" → "my-repo/sub:x"."""
    rest = str(by).split("@", 1)[-1]
    rest = rest.split("/", 1)[1] if "/" in rest else rest
    return re.sub(r":(?:claude|codex)-[0-9a-f]+", "", rest)


def scrub(o):
    """A copy without the fields that name this machine, its windows or its chats; `by` keeps the session only."""
    if isinstance(o, dict):
        return {k: (who(v) if k == "by" and isinstance(v, str) else "…" if k == "repo" and isinstance(v, str) else scrub(v))
                for k, v in o.items() if k not in MACHINE}
    if isinstance(o, list):
        return [scrub(x) for x in o]
    if isinstance(o, str):
        # no link, and no URL or repo name in a value's text either: a repo's visibility is never known here
        # (review, 2026-11-17: `--out log=https://…/secret-repo/…` reached the public view)
        o = MD_LINK.sub(r"\1", o)
        return REPO_KV.sub("repo=…", REPO_REF.sub(r"\1", URL.sub("<link>", o)))
    return o


def leaks(o) -> str:
    """The sample marker's verdict on everything a public answer would carry ("" when clean): a run that
    still holds a home path, this machine's name, an IP, an e-mail or a chat id is not sent at all."""
    global _sample
    if _sample is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location("brahman_privacy_sample", SAMPLE)
        _sample = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_sample)
    return _sample.why(json.dumps(o, ensure_ascii=False), words())


_sample = None
