"""Short stories: work that waits on pipelines between its steps (wait on CI, do a step,
wait on the next pipeline, do another, ..., done) and reports its progress to the owner's
chats, one card per story. A family's instance is a story (families/); these are the short
ones, minutes to hours, next to an upstream MR's weeks.

A session (Claude Code, a script, a person) that starts long work tells the buddy:

    buddy short start "Release 20261026.3" --mark "FreeRDP PR" --mark "kit build" --mark live
    → 12                         (the story's number; the card is posted to every linked chat)
    buddy short mark 12 "FreeRDP PR" --note "#13865"
    buddy short mark 12 "found a race" --status failed     (a mark nobody planned: it is added)
    buddy short plan 12 "rerun tests"                      (more marks expected)
    buddy short done 12         (or: --failed --note why)

Marks are a guide, not a contract: they can arrive in any order, ones never planned are
added where they happen, and the plan can grow while the work goes on. Each change edits
the same card in place in every chat (Lark patches the card, Telegram edits the message),
so the chat holds one up-to-date card per story, not a stream of notices.

Each story is a small file both the CLI and `buddy serve` read:

    $XDG_STATE_HOME/work_brahman/short_stories/<number>.json

Numbers count up from 1 and are never reused. Writes take a lock on the file, so two
sessions marking the same story do not lose each other's marks.

While a story runs, its card has two buttons, so the owner is in the loop, not only
watching it:

- **■ Stop** asks the executor to stop. The buddy cannot kill a session it did not start,
  so the request goes into the story file, and the executor's next `buddy short` call
  (mark, plan, note, check) prints it and exits with code 3. ■ STOP in chat does the same
  for every running story.
- **💬 Reply** asks for a message: the owner answers it, or quotes the card itself (in Lark,
  long-press → Reply), or sends `/short 12 <text>`. The executor's next call prints each
  reply once. Waiting on a pipeline, it polls `buddy short check N`, which needs no chat.

An executor that needs the owner to decide something asks on the card instead of in a
terminal he may not be at:

    buddy short ask 12 "Publish the live site now?" --option "Publish" --option "Wait"
    → question 1                  (the card shows it first, waiting for him, with one button
                                   per option; 💬 Reply answers it in his own words)
    buddy short check 12          → answer 1: Publish       (printed once, when it came)
    buddy short wait 12 1 --timeout 540     (block at a safe point; exit 0 answered,
                                             3 stop asked, 4 still waiting)

While a question is open, a reply (💬 Reply, quoting the card, `/short 12 <text>`) answers
the oldest open question rather than going to the executor as a plain reply.

The story file stays what carries a reply. On top of it, `buddy serve` tries to wake the
session: `start` (or `buddy short attach 12`, for a story already running) records the window
of the Claude Code session it runs in, and when something is unread a fixed line is typed there,
telling it to run `check`. Never the owner's words, so nothing he writes reaches a terminal. A
busy session is tried again until it is idle; each piece of news is typed once. A wake that
never comes costs nothing but time: the session still reads it at its next call.

A mark can be a fork, an if/else or a switch, so the card is a small map that keeps its
shape while ◀ moves along it, and the owner sees where the work is and what may come next:

    buddy short start "Release" --mark build --mark "CI green? yes | no=fix, rerun" --mark publish
    buddy short mark 12 "CI green?" --took no      (the branch taken; the others fade)
    buddy short mark 12 fix                        (a step under an option: that option is taken)
    buddy short ask 12 --fork "CI green?"          (or he picks it: the options are the buttons)

A fork has 2 to 4 options, each with up to 3 small steps, and then the main line goes on.

A mark can be a loop with an index, the same step a few times, over a count or a list:

    buddy short start "Builds" --mark "build × linux, mac, win" --mark "soak ×3" --mark pick
    buddy short mark 12 build --status doing       (the current round: build 1/3 · linux)
    buddy short mark 12 build                      (that round is done; the next one is current)
    buddy short mark 12 soak --round 3             (a round by its index)
    buddy short mark 12 soak --status skipped      (the rounds left are skipped: the loop ends early)

A loop whose count nobody knows ahead, a retry or a poll, is open: "rerun ×?". Each try is
its own line with its time and note, so a try never overwrites the one before it:

    buddy short mark 12 rerun --status failed --note "flaky net"   (try 1 failed; try 2 is next)
    buddy short mark 12 rerun                                      (try 2 worked: the loop is done)
    buddy short mark 12 rerun --status doing                       (one more try, after all)
    buddy short mark 12 rerun --status skipped                     (giving up)

A loop has 2 to 9 rounds, an open one up to 20 tries. Nothing nests (no fork in a loop, no loop in a fork): work that
needs more is more than one short story.

That other story can be linked to the step it does: a child story (specs/story-levels.md).
Cards stay flat; a link gives the depth, one level only (a child has no children of its own):

    buddy short start "Releases" --mark "build → child" --mark pick      (the link planned)
    buddy short start "FreeRDP build" --parent 12:build                  → 13
    BUDDY_STORY=12:build buddy short start "FreeRDP build"               (the same, from the session's env)
    buddy short start "mac build" --parent 12:build:mac                  (a loop's round, by item or index)

The parent's step follows its child: doing while it runs, done or failed when it ends, and
one line on the parent's card (→ #13 · 2/5 · freerdp). The child's card says ↑ part of #12.
■ Stop on the parent stops its children too; `done` on a parent waits for its children
(exit 2), and `done --failed` asks them to stop.

Every story is read three ways (specs/multi-brahman.md): what (the steps), who (each mark's
`by`, filled in here from the session's environment, never typed; the card shows only its
short tag on the step being done) and with what (`--in`/`--out key=value` on a mark: the
SHAs, run ids and artefacts a step used and made). All of it is optional and read loosely:
an old story or a mark without these keys draws as before. Each step has a stable id (s1,
s2, …, never reused), so marks from several sessions land on one step.
"""

import os
import re
import socket
import subprocess

import json
import time
from contextlib import contextmanager
from pathlib import Path

from . import portable
from .channel import Channel
from .text import app, join, t

ICON = {"todo": "⏳", "doing": "▶️", "done": "✅", "failed": "❌", "skipped": "⏭"}
STATUSES = tuple(ICON)
EVENTS_SHOWN = 6
FORK_OPTIONS, OPTION_STEPS = 4, 3  # a fork's options at most, and the small steps under one
LOOP_ROUNDS = 9  # a loop's rounds at most
LOOP_COUNT = re.compile(r"^(.+?)(?:\s*×|\s+[x*])\s*(\d+)$")  # "soak ×3", "soak x3"
LOOP_ITEMS = re.compile(r"^(.+?)\s*×\s*(.+[,，].+)$")  # "build × linux, mac, win"
LOOP_OPEN = re.compile(r"^(.+?)(?:\s*×|\s+[x*])\s*[?？…]$")  # "rerun ×?": until a try works
LOOP_TRIES, TRIES_SHOWN = 20, 5  # an open loop's tries at most, and the last ones on the card
INDENT = "\u2003"  # an em space: Lark and Telegram keep it at the start of a line
CHILD_MARK = re.compile(r"^(.+?)\s*(?:→|->)\s*child$")  # "build → child": a child story is expected here
EVENT_V = 1  # the event record's version: readers keep what they don't know


def executor(env=None, cwd: str | None = None) -> dict:
    """Who is marking, as far as code can tell: the `by` path (specs/multi-brahman.md), never
    typed. instance@environment/session[:engine-id][/sub:agent], plus the short tag the card
    shows (the session's project, and the agent). What can't be told is left out, not guessed:
    a subagent is named only when its master hands it BUDDY_AGENT."""
    env = os.environ if env is None else env
    folder = _project(cwd or os.getcwd())
    sid = env.get("CLAUDE_CODE_SESSION_ID") or env.get("CODEX_THREAD_ID") or env.get("CODEX_SESSION_ID") or ""
    engine = "claude" if env.get("CLAUDE_CODE_SESSION_ID") else "codex" if sid else ""
    long = env.get("BUDDY_LONG", "")
    session = (long if long not in ("", "1") else folder) or "unknown"  # BUDDY_LONG=1: the folder's name
    agent = env.get("BUDDY_AGENT", "")
    sub = f"/sub:{agent}" if agent else ""
    path = (f"{env.get('BUDDY_INSTANCE', 'buddy')}@{socket.gethostname().split('.')[0]}/{session}"
            + (f":{engine}-{sid[:8]}" if sid else "") + sub)
    return {"by": path, "tag": session + sub}


def _project(cwd: str) -> str:
    """The name of the repository the folder belongs to (a worktree counts as its repo), else
    the folder's own name."""
    try:
        p = subprocess.run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                           capture_output=True, text=True, timeout=5)
        if p.returncode == 0 and p.stdout.strip():
            common = Path(p.stdout.strip())
            return (common.parent if common.name == ".git" else common).name.removesuffix(".git")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return Path(cwd).name


def _ids(story: dict) -> None:
    """Every step gets a stable id (s1, s2, …), never reused: the text of a step may change,
    its id doesn't. Old stories get theirs on their next change."""
    n = story.get("next_step", 1)
    for m in story["marks"]:
        for x in [m] + [st for o in (m.get("fork") or {}).get("options", []) for st in o["steps"]]:
            if not x.get("id"):
                x["id"], n = f"s{n}", n + 1
    story["next_step"] = n


def _dir(state: Path) -> Path:
    d = state / "short_stories"
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def _locked(state: Path, name: str):
    with portable.locked(_dir(state) / f".{name}.lock"):
        yield


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def load(state: Path, number: int) -> dict | None:
    p = _dir(state) / f"{number}.json"
    return json.loads(p.read_text()) if p.exists() else None


def _save(state: Path, story: dict) -> None:
    p = _dir(state) / f"{story['id']}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(story, ensure_ascii=False, indent=1))
    tmp.replace(p)


def all_stories(state: Path) -> list[dict]:
    out = []
    for p in _dir(state).glob("*.json"):
        if p.stem.isdigit():
            out.append(json.loads(p.read_text()))
    return sorted(out, key=lambda t: t["id"])


def start(state: Path, channels: list[Channel], chats: list[str], title: str, marks: list[str],
          *, who: str = "", note: str = "", parent: str = "", by: dict | None = None,
          team: str = "") -> dict:
    """A new story. parent: "12:build" (or "12:build:mac", "12:build:2" for a loop's round)
    makes it a child of that step of story 12; the rules (one level, one child per step, a
    running parent) raise ValueError before anything is written. team: the team(s) of the
    session that starts it (teams.of_session), shown beside `who`."""
    if not parent:
        story = _new(state, chats, title, marks, who, note, by=by, team=team)
    else:
        with _locked(state, str(_parent_number(parent))):  # parent's lock, then "next": never the other way
            p, m, k = _parent_ref(state, parent)
            story = _new(state, chats, title, marks, who, note, by=by, team=team, parent=
                         {"story": p["id"], "step": m["name"], "round": None if k is None else k + 1,
                          "title": p["title"], "label": m["name"] + _round(m.get("loop"), None if k is None else k + 1)})
            slot = m["loop"]["rounds"][k] if k is not None else m
            slot["child"] = _snapshot(story)
            _mark(p, m["name"], "doing", round=None if k is None or m["loop"].get("open") else k + 1)
            _event(p, t("short.ev.child", n=story["id"], step=m["name"] + _round(m.get("loop"), None if k is None else k + 1),
                        title=title))
            _show(channels, p.get("chats") or list(p["copies"]), p)
            _save(state, p)
    n = story["id"]
    with _locked(state, str(n)):
        _show(channels, chats, story)
        _undrawn(story, channels)
        _save(state, story)
    return story


def _undrawn(story: dict, channels: list[Channel]) -> None:
    """A change made with no channel (a session behind the gate holds no chat token) leaves the
    card to `buddy serve`: redraw() draws it there."""
    if channels:
        story.pop("undrawn", None)
    elif story.get("chats"):
        story["undrawn"] = True


WAKE = "short story #{n} has news from the owner: run buddy-short check {n}"


def hold(state: Path, number: int, window: dict) -> dict | None:
    """The window of the session that runs this story (cli._window): where wake() types. The
    last session to attach has it; one marking a step for a story it didn't start does not."""
    return _change(state, [], number, lambda s: s.update(window=window), show=False)


TITLE_MAX = 24  # characters: each tab in pys's status line shows its whole title (the owner's rule)


def _same_window(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("mux", "session", "window", "pid", "started"))


STATES = ("busy", "free")  # the title's last word (the owner's rule, 2026-11-11)


def window_title(state: Path, window: dict, name: str, free: bool = False) -> str:
    """The title of a pys window, `<name> [<job>] <state>` (the owner's rule, 2026-11-11:
    `rel-rdp-g1 #333 busy`, `comp-shadow-g3 #331 keys busy`, `rev-freerdp-g2 free`; at most
    about 24 characters, each tab in pys's status line shows its whole title). The name is the
    session's first word; the job is the numbers of the running stories held in this window,
    newest first, then the session's own work words; the state is last. Only numbers: the card
    has the titles. When it would grow too long the work words go first, then older numbers; the
    name, the newest number and the state always show. A name of one word with neither `-` nor
    `·` ("bash") is its program's: the session's tag stands in. With a story running the state
    is `busy`; with none, the session's name and work and its own state, or `<name> free` when
    it asks (done --free)."""
    tag = window.get("tag") or "?"
    words = name.split()
    given = words.pop() if words and words[-1] in STATES else ""
    if not words or (len(words) == 1 and not given and not any(c in words[0] for c in "-·")):
        words = [tag]
    head, work = words[:1], [x for x in words[1:] if not x.startswith("#")]
    mine = sorted((s for s in all_stories(state) if s["status"] == "running" and s.get("window")
                   and _same_window(s["window"], window)), key=lambda s: -s["id"])
    if free and not mine:
        return f"{head[0]} free"
    if not mine:
        return " ".join(head + work + ([given] if given else []))
    shown = []
    for x in mine:
        if shown and len(" ".join(head + shown + [f"#{x['id']}", "busy"])) > TITLE_MAX:
            break
        shown.append(f"#{x['id']}")
    full = " ".join(head + shown + work + ["busy"])
    return full if len(full) <= TITLE_MAX else " ".join(head + shown + ["busy"])


def retitle(state: Path, window: dict, free: bool = False) -> bool:
    """Write the stories of this window into its title (window_title), if it is a pys window and
    the same `claude` (pid and start time) still holds it: a title never lands on a terminal
    someone else has now. As `pys title --owner <that claude>`, so the title goes when the
    session does; OSC 2 on a pys without it. False when nothing was written; it never fails a
    command."""
    from . import long_stories as longs
    p = portable.process(window.get("pid") or 0)
    if window.get("mux") != "pys" or not p or p["comm"] != "claude" or p["started"] != window.get("started") \
            or p["tty"].strip("?") == "":
        return False
    w = longs._pys_titles(window["window"], window.get("session", ""))
    if w is None:
        return False
    # The session's part: its agent title (`pys title`); before that rule, an app title in the
    # role form (`rel·kit …`, from printf). In a Claude pane the app title is otherwise Claude
    # Code's own (a spinner and its task), never a session's part.
    app = w.get("app_title") or ""
    name = w.get("agent_title") or (app if "·" in (app.split() or [""])[0] else "")
    if "agent_title" not in w:  # a pys before `pys title`: only what shows, set by OSC 2
        name = w.get("name") or ""
    title = window_title(state, window, name, free)
    if title == w.get("agent_title"):
        return True
    return longs._pys_title(window["window"], window.get("session", ""), title, window["pid"]) \
        or longs._set_title(p["tty"], title)


def _unread(story: dict) -> set[str]:
    """What its session has not read, by name (r0 the first reply, a2 question 2's answer, stop),
    not by time: the times count seconds, and a stop can follow a reply within one. A stop has
    no read mark, so it counts until the story ends; wake() types each name once."""
    return ({f"r{i}" for i, r in enumerate(story.get("replies", [])) if not r["read"]}
            | {f"a{q['id']}" for q in story.get("questions", []) if q["answer"] and not q["answer"]["read"]}
            | ({"stop"} if story.get("stop") else set()))


def wake(state: Path) -> list[int]:
    """Wake the sessions with news waiting, a nudge on top of the story file (the docstring
    above); `buddy serve` calls it every few seconds, so a busy session is reached when it goes
    idle. Returns the stories woken. Only this one thread types, outside the story's lock."""
    from . import long_stories as longs
    woken = []
    for s in all_stories(state):
        news = _unread(s) - set(s.get("woken", []))
        if s["status"] == "running" and s.get("window") and news \
                and longs.wake(s["window"], WAKE.format(n=s["id"])):
            _change(state, [], s["id"], lambda x: x.update(woken=sorted(set(x.get("woken", [])) | news)),
                    show=False)
            woken.append(s["id"])
    return woken


def redraw(state: Path, channels: list[Channel]) -> list[int]:
    """Draw the cards of the stories changed where no channel was (behind the gate). Returns
    their numbers. A redraw changes nothing in the story but its card."""
    drawn = []
    for s in all_stories(state):
        if s.get("undrawn") and channels:
            _change(state, channels, s["id"], lambda x: None)  # None: changed, so drawn and saved
            drawn.append(s["id"])
    return drawn


def start_drawer(state: Path, channels: list[Channel], every: float = 5) -> None:
    """`buddy serve`'s thread for redraw() and wake(): every few seconds, so a mark made in the
    sandbox shows on the phone about as fast as one made outside, and a reply reaches a session
    about as fast as it is sent."""
    import logging
    import threading
    log = logging.getLogger("buddy.shorts")

    def loop():
        while True:
            try:
                redraw(state, channels)
            except Exception:
                log.exception("redrawing short stories failed")
            try:
                for n in wake(state):
                    log.info("short story #%d: its session was woken", n)
            except Exception:
                log.exception("waking short-story sessions failed")
            time.sleep(every)
    threading.Thread(target=loop, name="short-drawer", daemon=True).start()


def _new(state: Path, chats: list[str], title: str, marks: list[str], who: str, note: str,
         parent: dict | None = None, by: dict | None = None, team: str = "") -> dict:
    with _locked(state, "next"):
        n = max([t["id"] for t in all_stories(state)] or [0]) + 1
        story = {"id": n, "title": title, "who": who, "started": _now(), "ended": None,
                 "status": "running", "marks": [], "events": [], "chats": chats, "copies": {}}
        if parent:
            story["parent"] = parent
        if team:
            story["team"] = team
        if by:
            story["by"] = by["by"]
        for m in marks:
            _mark(story, m, "todo")
        _ids(story)
        _event(story, _noted(t("short.ev.started"), note), **({"by": by["by"]} if by else {}))
        _save(state, story)
    return story


def _parent_number(ref: str) -> int:
    number, _, rest = ref.strip().partition(":")
    if not number.strip().isdigit() or not rest.strip():
        raise ValueError(t("short.child.ref", ref=ref))
    return int(number)


def _parent_ref(state: Path, ref: str) -> tuple[dict, dict, int | None]:
    """"12:build", "12:build:mac" or "12:build:2" → (story 12, its mark build, the round's
    index from 0 or None), checked against the rules: a running top-level story, a step
    that is planned and not settled, and no child on it yet."""
    n, rest = _parent_number(ref), ref.strip().partition(":")[2].strip()
    p = load(state, n)
    if p is None:
        raise ValueError(t("short.no_such", n=n))
    if p.get("parent"):
        raise ValueError(t("short.child.one_level", n=n, parent=p["parent"]["story"]))
    if p["status"] != "running":
        raise ValueError(t("short.ended", n=n))
    m, rnd = _find(p, rest), None
    if m is None and ":" in rest:
        step, _, rnd = rest.rpartition(":")
        m = _find(p, step)
    if m is None:
        raise ValueError(t("short.child.no_step", n=n, step=rest, steps=join([x["name"] for x in p["marks"]])))
    k, loop = None, m.get("loop")
    if loop and loop.get("open") and rnd is not None:
        raise ValueError(t("short.loop.open_round", name=m["name"]))
    if loop and rnd is None:  # the current round: the first one not settled and not linked yet
        k = next((i for i, r in enumerate(loop["rounds"]) if r["status"] in ("todo", "doing") and not r.get("child")), None)
        if k is None:
            raise ValueError(t("short.child.no_free_round", name=m["name"]))
    elif loop:
        k = (int(rnd) - 1 if rnd.strip().isdigit()
             else next((i for i, r in enumerate(loop["rounds"]) if _key(r["name"]) == _key(rnd)), -1))
        if not 0 <= k < len(loop["rounds"]):
            raise ValueError(t("short.loop.no_round", name=m["name"], round=rnd, n=len(loop["rounds"])))
    elif rnd is not None:
        raise ValueError(t("short.loop.not_loop", name=m["name"]))
    slot = loop["rounds"][k] if k is not None else m
    if slot.get("child"):
        raise ValueError(t("short.child.taken", name=m["name"] + _round(loop, None if k is None else k + 1),
                           child=slot["child"]["id"]))
    if slot["status"] not in ("todo", "doing"):
        raise ValueError(t("short.child.settled", name=m["name"] + _round(loop, None if k is None else k + 1),
                           status=slot["status"]))
    return p, m, k


def _from(stop: dict) -> str:
    """Where a stop came from, in a sentence: a chat ("Lark"), or the parent story ("parent #12")."""
    return t("short.child.where", n=stop.get("who", "")) if stop["where"] == "parent" else app(stop["where"])


def _snapshot(child: dict) -> dict:
    """What the parent's card shows of a child: kept on the parent's step, and written again
    on every change of the child, so the parent's card needs no other file to be drawn."""
    way = _walk(child)
    return {"id": child["id"], "title": child["title"], "who": child.get("who", ""),
            "status": child["status"], "stop": bool(child.get("stop")),
            "done": sum(m["status"] in ("done", "skipped") for m in way), "total": len(way)}


def children(story: dict) -> list[int]:
    """The numbers of a story's child stories, in the order of its steps."""
    out = []
    for m in story["marks"]:
        for slot in [m] + ((m.get("loop") or {}).get("rounds") or []):
            if slot.get("child"):
                out.append(slot["child"]["id"])
    return out


def _sync_parent(state: Path, channels: list[Channel], child: dict) -> None:
    """The child changed: its parent's step follows it (doing, done, failed), and the
    parent's card shows the child's progress. The child's own lock is not held here."""
    ref = child["parent"]

    def follow(p):
        m = _find(p, ref["step"])
        k = ref.get("round")
        loop = (m or {}).get("loop")
        slot = loop["rounds"][k - 1] if m and loop and k and k <= len(loop["rounds"]) else m
        if slot is None or (slot.get("child") or {}).get("id") != child["id"]:
            return False
        snap = _snapshot(child)
        want = {"running": "doing", "done": "done", "failed": "failed"}[child["status"]]
        moved = slot["status"] in ("todo", "doing") and slot["status"] != want
        if snap == slot["child"] and not moved:
            return False
        slot["child"] = snap
        if moved:
            why = t("short.child.stopped") if child.get("stop") else (child["events"][-1]["text"] if child["events"] else "")
            _mark(p, m["name"], want, f"#{child['id']}: {why}" if want == "failed" else "",
                  round=None if not loop or loop.get("open") else k)
        if child["status"] != "running" and moved:
            _event(p, t("short.ev.child_ended", icon=ICON[want], n=child["id"],
                        step=m["name"] + _round(loop, k if loop else None)))
    _change(state, channels, ref["story"], follow)


def mark(state: Path, channels: list[Channel], number: int, name: str, *, status: str = "done",
         note: str = "", took: str | None = None, round: int | None = None, by: dict | None = None,
         inputs: dict | None = None, outputs: dict | None = None) -> dict | None:
    """A mark happened. took: the option a fork went down; round: a loop's round (from 1).
    by: who marks it (executor()); inputs, outputs: what the step used and made (key: value)."""
    def change(s):
        m = _mark(s, name, status, note, took, round)
        _ids(s)
        moved = ""
        if by and by.get("by"):
            if m.get("tag") and m["tag"] != by["tag"]:  # the step changed hands: one line says so
                moved = f" · ↪ {m['tag']} → {by['tag']}"
            m["by"], m["tag"] = by["by"], by["tag"]
        for key, got in (("in", inputs), ("out", outputs)):
            if got:
                m[key] = {**m.get(key, {}), **got}
        took_ = (m.get("fork") or {}).get("took") if took else None
        made = " · " + " ".join(f"{k}={v}" for k, v in outputs.items()) if outputs else ""
        _event(s, _noted(f"{ICON[status]} {m['name']}" + (f" → {took_}" if took_ else "")
                         + _round(m.get("loop"), m["loop"].get("last") if m.get("loop") else None)
                         + made + moved, note),
               step=m.get("id"), status=status, by=(by or {}).get("by"), **{"in": inputs or {}, "out": outputs or {}})
    return _change(state, channels, number, change)


def plan(state: Path, channels: list[Channel], number: int, marks: list[str]) -> dict | None:
    def add(s):
        new = [m for m in marks if not _find(s, _spec(m)["name"])]
        for m in new:
            _mark(s, m, "todo")
        if new:
            _event(s, t("short.ev.expected", marks=join(new)))
    return _change(state, channels, number, add)


def note(state: Path, channels: list[Channel], number: int, text: str) -> dict | None:
    return _change(state, channels, number, lambda s: _event(s, text))


def finish(state: Path, channels: list[Channel], number: int, *, failed: bool = False,
           note: str = "") -> dict | None:
    def end(s):
        running = [c for c in children(s) if (x := load(state, c)) and x["status"] == "running"]
        if running and not failed:  # a parent waits for its children; --failed stops them
            raise ValueError(t("short.child.running", n=s["id"], children=join([f"#{c}" for c in running])))
        s["status"], s["ended"] = ("failed" if failed else "done"), _now()
        _event(s, _noted(t("short.ev.failed" if failed else "short.ev.done"), note))
    s = _change(state, channels, number, end)
    for c in children(s) if s and failed else []:
        ask_stop(state, channels, c, where="parent", who=f"#{number}")
    return s


def ask_stop(state: Path, channels: list[Channel], number: int, *, where: str,
             who: str = "") -> dict | None:
    """■ Stop on the card (or ■ STOP in chat): recorded for the executor, which stops at
    its next call. A story that ended, or was already asked, is left as it is."""
    def stop(s):
        if s["status"] != "running" or s.get("stop"):
            return False  # nothing new: a second press must not edit every card again
        s["stop"] = {"where": where, "who": who, "at": _now()}
        _event(s, t("short.ev.stop", where=_from(s["stop"])))
    s = _change(state, channels, number, stop)
    for c in children(s) if s else []:  # stopping a release stops every part of it
        ask_stop(state, channels, c, where="parent", who=f"#{number}")
    return s


def stop_all(state: Path, channels: list[Channel], *, where: str, who: str = "") -> list[int]:
    """■ STOP in chat: ask every running story to stop. Returns their numbers."""
    asked = [s["id"] for s in all_stories(state) if s["status"] == "running" and not s.get("stop")]
    for n in asked:
        ask_stop(state, channels, n, where=where, who=who)
    return asked


def reply(state: Path, channels: list[Channel], number: int, text: str, *, where: str,
          who: str = "") -> dict | None:
    """A message from the owner to the executor, shown on the card until it is read. While
    a question is open, it is the answer to the oldest one."""
    def add(s):
        if s["status"] != "running":  # it ended: nobody would read it
            return False
        if waiting := _open(s):
            return _answer(s, waiting[0], text, None, where, who)
        s.setdefault("replies", []).append({"at": _now(), "where": where, "who": who,
                                            "text": text, "read": False})
        _event(s, f"💬 {text}")
    return _change(state, channels, number, add)


def ask(state: Path, channels: list[Channel], number: int, text: str,
        options: list[str] = (), fork: str = "") -> tuple[dict | None, dict | None]:
    """A question for the owner, at the top of the card until he answers it. Returns the
    story and the question (None when the story is missing or has ended). fork: a fork's
    name; its options are the buttons, and his answer is the branch taken."""
    q = {}

    def add(s):
        if s["status"] != "running":
            return False
        qs = s.setdefault("questions", [])
        q.update({"id": len(qs) + 1, "text": text, "options": list(options), "asked": _now(),
                  "answer": None})
        if fork:
            m = _find(s, fork)
            if m is None or not m.get("fork"):
                raise ValueError(t("short.fork.not_fork", name=fork))
            q.update({"fork": m["name"], "text": text or m["name"],
                      "options": [o["name"] for o in m["fork"]["options"]]})
        qs.append(q)
        _event(s, t("short.ev.asked", q=q["id"], text=q["text"]))
    return _change(state, channels, number, add), (q or None)


def answer(state: Path, channels: list[Channel], number: int, question: int, *,
           choice: int | None = None, text: str = "", where: str, who: str = "") -> dict | None:
    """The owner's answer: an option's button (choice, its index) or his own words (text).
    The first answer wins; one to a question already answered, or a story that ended, is
    left as it is."""
    def settle(s):
        q = _question(s, question)
        if s["status"] != "running" or q is None or q["answer"]:
            return False
        if choice is not None and not 0 <= choice < len(q["options"]):
            return False
        _answer(s, q, q["options"][choice] if choice is not None else text, choice, where, who)
    return _change(state, channels, number, settle)


def _question(story: dict, question: int) -> dict | None:
    return next((q for q in story.get("questions", []) if q["id"] == question), None)


def _open(story: dict) -> list[dict]:
    return [q for q in story.get("questions", []) if not q["answer"]]


def _answer(story: dict, q: dict, text: str, choice: int | None, where: str, who: str) -> None:
    q["answer"] = {"text": text, "choice": choice, "where": where, "who": who, "at": _now(),
                   "read": False}
    _event(story, t("short.ev.answered", q=q["id"], where=app(where), text=text))
    m = _find(story, q["fork"]) if q.get("fork") else None
    picked = next((o for o in q["options"] if o.casefold() == text.strip().casefold()), None)
    if m and picked:  # the answer is the branch the fork takes
        m["fork"]["took"], m["status"], m["at"] = picked, "done", _now()


def prompted(state: Path, number: int, chat: str, handle: str) -> None:
    """The message 💬 Reply sent: an answer to it is a reply to this story."""
    _change(state, [], number,
            lambda s: s.setdefault("prompts", {}).setdefault(chat, []).append(handle), show=False)


def find(state: Path, chat: str, message_id: str) -> int | None:
    """The running story whose card (or reply prompt) message_id is, in chat."""
    for s in all_stories(state):
        if s["status"] == "running" and (s["copies"].get(chat) == message_id
                                         or message_id in s.get("prompts", {}).get(chat, [])):
            return s["id"]
    return None


def news(state: Path, number: int) -> tuple[dict | None, list[dict], list[dict]]:
    """For the executor: the stop request if any, the replies it has not read yet, and the
    questions answered since it last looked; both count as read from now on. Changes no card."""
    with _locked(state, str(number)):
        story = load(state, number)
        if story is None:
            return None, [], []
        fresh = [r for r in story.get("replies", []) if not r["read"]]
        answered = [q for q in story.get("questions", []) if q["answer"] and not q["answer"]["read"]]
        for r in fresh + [q["answer"] for q in answered]:
            r["read"] = True
        if fresh or answered:
            _save(state, story)
    return story.get("stop"), fresh, answered


def _change(state: Path, channels: list[Channel], number: int, fn, show: bool = True) -> dict | None:
    with _locked(state, str(number)):
        story = load(state, number)
        if story is None:
            return None
        if fn(story) is False:  # fn changed nothing
            return story
        _ids(story)
        if show:
            _show(channels, story.get("chats") or list(story["copies"]), story)
            _undrawn(story, channels)
        _save(state, story)
    if show and story.get("parent"):  # outside the child's lock: the parent's is taken next
        _sync_parent(state, channels, story)
    return story


def _key(name: str) -> str:
    return name.strip().rstrip("?？").strip().casefold()  # "CI green?" is "ci green" too


def _find(story: dict, name: str) -> dict | None:
    """A step by its text, else by its stable id (s3)."""
    return (next((m for m in story["marks"] if _key(m["name"]) == _key(name)), None)
            or next((m for m in story["marks"] if m.get("id") == name.strip()), None))


def _step(story: dict, name: str) -> tuple[dict, dict, dict] | None:
    """A small step under a fork's option: (the fork's mark, the option, the step)."""
    return next(((m, o, s) for m in story["marks"] for o in (m.get("fork") or {}).get("options", [])
                 for s in o["steps"] if _key(s["name"]) == _key(name)), None)


def _spec(text: str) -> dict:
    """A mark as planned; "build → child" (or "->") also says a child story will do it."""
    hit = CHILD_MARK.match(text.strip())
    spec = _shape(hit[1] if hit else text)
    if hit:
        spec["expects_child"] = True
    return spec


def _shape(text: str) -> dict:
    """A mark as planned. "CI green? yes | no=fix, rerun" is a fork: a question, then 2 to 4
    options split by |, each with up to 3 small steps after =, split by commas."""
    mark = min((c for c in "?？" if c in text), key=text.index, default="?")  # 通知？ too
    head, q, rest = text.partition(mark)
    if not q or "|" not in rest:
        return _loop(text)
    options = []
    for part in rest.split("|"):
        label, _, steps = part.partition("=")
        options.append({"name": label.strip(), "steps": [{"name": x.strip(), "status": "todo"}
                                                         for x in steps.split(",") if x.strip()]})
    if not 2 <= len(options) <= FORK_OPTIONS or not all(o["name"] for o in options):
        raise ValueError(t("short.fork.options", max=FORK_OPTIONS, spec=text))
    if any(len(o["steps"]) > OPTION_STEPS for o in options):
        raise ValueError(t("short.fork.steps", max=OPTION_STEPS, spec=text))
    return {"name": head.strip() + mark, "fork": {"options": options, "took": None}}


def _loop(text: str) -> dict:
    """A plain mark, or a loop: "soak ×3" (x3 or *3 too) runs 3 rounds, "build × linux, mac,
    win" one round per item; 2 to 9 rounds. "rerun ×?" is open: it tries until a try works."""
    if hit := LOOP_OPEN.match(text.strip()):
        return {"name": hit[1].strip(), "loop": {"rounds": [{"name": "1", "status": "todo"}],
                                                 "items": False, "open": True}}
    if hit := LOOP_ITEMS.match(text.strip()):
        rounds = [x.strip() for x in re.split("[,，]", hit[2]) if x.strip()]
    elif hit := LOOP_COUNT.match(text.strip()):
        rounds = [str(k + 1) for k in range(int(hit[2]))]
    else:
        return {"name": text.strip()}
    if not 2 <= len(rounds) <= LOOP_ROUNDS:
        raise ValueError(t("short.loop.rounds", max=LOOP_ROUNDS, spec=text))
    return {"name": hit[1].strip(), "loop": {"rounds": [{"name": r, "status": "todo"} for r in rounds],
                                             "items": not LOOP_COUNT.match(text.strip())}}


def _try(m: dict, status: str, note: str) -> None:
    """An open loop's try happened: the current one, or a new one when none is open. A failed
    try queues the next; a try that worked ends the loop; doing starts one (again, if the loop
    had ended); skipped gives up. A try is never written over: each keeps its time and note."""
    rounds = m["loop"]["rounds"]
    cur = next((r for r in rounds if r["status"] in ("todo", "doing")), None)
    if status == "skipped":
        while rounds and rounds[-1]["status"] == "todo" and len(rounds) > 1:
            rounds.pop()  # the try queued after a failure never began
        if rounds[-1]["status"] in ("todo", "doing"):
            rounds[-1].update(status="skipped", at=_now(), note=note)
        m["loop"]["last"] = len(rounds)
        tried = [r for r in rounds if r["status"] != "skipped"]
        m["status"] = "failed" if tried and tried[-1]["status"] == "failed" else "skipped"
        return
    if status == "todo":
        return
    if cur is None:
        if len(rounds) >= LOOP_TRIES:
            raise ValueError(t("short.loop.tries", name=m["name"], max=LOOP_TRIES))
        cur = {"name": str(len(rounds) + 1), "status": "todo"}
        rounds.append(cur)
    cur.update(status=status, at=_now(), note=note)
    m["loop"]["last"] = rounds.index(cur) + 1
    if status == "failed" and len(rounds) < LOOP_TRIES:
        rounds.append({"name": str(len(rounds) + 1), "status": "todo"})
    m["status"] = {"done": "done", "doing": "doing"}.get(status, "doing" if rounds[-1] is not cur else "failed")


def _turn(m: dict, status: str, round: int | None) -> None:
    """A loop's round happened: the one asked for, else the current one (the first not
    settled); skipped without a round skips every round left. The loop is done when every
    round is settled (failed if one failed), and doing once one has begun."""
    rounds = m["loop"]["rounds"]
    if round is not None and m["loop"].get("open"):
        raise ValueError(t("short.loop.open_round", name=m["name"]))
    if round is not None and not 1 <= round <= len(rounds):
        raise ValueError(t("short.loop.no_round", name=m["name"], round=round, n=len(rounds)))
    left = [k for k, r in enumerate(rounds) if r["status"] in ("todo", "doing")]
    if round is None and status == "skipped":
        picked = left
    else:
        picked = [round - 1] if round is not None else left[:1]
    for k in picked:
        rounds[k]["status"], rounds[k]["at"] = status, _now()
    m["loop"]["last"] = picked[0] + 1 if picked else None
    if all(r["status"] not in ("todo", "doing") for r in rounds):
        m["status"] = "failed" if any(r["status"] == "failed" for r in rounds) else "done"
    else:
        m["status"] = "todo" if all(r["status"] == "todo" for r in rounds) else "doing"


def rounds(m: dict) -> str:
    """A loop's progress for a one-line step name: " ×3 · 2/3", or "" for any other mark."""
    loop = m.get("loop")
    if not loop:
        return ""
    if loop.get("open"):
        return f" ×? · {t('short.loop.try', k=len(loop['rounds']))}"
    done = sum(r["status"] in ("done", "skipped", "failed") for r in loop["rounds"])
    return f" ×{len(loop['rounds'])} · {done}/{len(loop['rounds'])}"


def _round(loop: dict | None, k: int | None) -> str:
    """ "2/3", plus the item for a loop over a list: "2/3 · mac"."""
    if not loop or not k:
        return ""
    if loop.get("open"):
        return " " + t("short.loop.try", k=k)
    r = loop["rounds"][k - 1]
    return " " + t("short.loop.round", k=k, n=len(loop["rounds"])) + (f" · {r['name']}" if loop["items"] else "")


def _mark(story: dict, name: str, status: str, note: str = "", took: str | None = None,
          round: int | None = None) -> dict:
    if status not in ICON:
        raise ValueError(f"status {status!r} is not one of {', '.join(STATUSES)}")
    m = _find(story, name)
    if m is None and took is None and (hit := _step(story, name)):
        fork, option, step = hit  # its option is the branch taken, whatever was said before
        fork["fork"]["took"] = option["name"]
        if fork["status"] in ("todo", "doing"):
            fork["status"], fork["at"] = "done", _now()
        m = step
    elif m is None:  # not planned: it happened anyway, so it goes on the list where it is now
        m = {**_spec(name), "planned": status == "todo"}
        story["marks"].append(m)
    if took is not None:
        f = m.get("fork")
        if not f:
            raise ValueError(t("short.fork.not_fork", name=m["name"]))
        o = next((o for o in f["options"] if _key(o["name"]) == _key(took)), None)
        if o is None:
            raise ValueError(t("short.fork.no_option", name=m["name"], took=took,
                               options=join([o["name"] for o in f["options"]])))
        f["took"] = o["name"]
    if round is not None and not m.get("loop"):
        raise ValueError(t("short.loop.not_loop", name=m["name"]))
    if m.get("loop") and m["loop"].get("open") and not (status == "todo" and round is None):
        if round is not None:
            raise ValueError(t("short.loop.open_round", name=m["name"]))
        _try(m, status, note)  # the note is the try's own
        m["at"] = _now()
        return m
    if m.get("loop") and not (status == "todo" and round is None):
        _turn(m, status, round)
        m["note"], m["at"] = note or m.get("note", ""), _now()
        return m
    m["status"], m["note"] = status, note or m.get("note", "")
    m["at"] = _now() if status != "todo" else m.get("at")
    return m


def _walk(story: dict) -> list[dict]:
    """Every mark on the way, in order: a fork's taken option adds its steps after it, and a
    loop is its rounds."""
    out = []
    for m in story["marks"]:
        if m.get("loop"):  # a loop counts its rounds, not itself, so ◀ lands on a round
            out += m["loop"]["rounds"]
            continue
        out.append(m)
        f = m.get("fork")
        out += next((o["steps"] for o in f["options"] if o["name"] == f["took"]), []) if f else []
    return out


def _here(story: dict) -> dict | None:
    """Where the work is: the mark being done, else the first one still to do."""
    if story["status"] != "running":
        return None
    way = _walk(story)
    return (next((m for m in way if m["status"] == "doing"), None)
            or next((m for m in way if m["status"] == "todo"), None))


def _noted(head: str, note: str) -> str:
    return t("with_note", head=head, note=note) if note else head


def _event(story: dict, text: str, **keys) -> None:
    """One line of the story's log. keys (step, by, status, in, out) make it readable three
    ways; a line without them is still a line."""
    keys = {k: v for k, v in keys.items() if v not in (None, "", {})}
    story["events"].append({"at": _now(), "text": text, **({"v": EVENT_V, **keys} if keys else {})})


def _elapsed(story: dict) -> str:
    t0 = time.mktime(time.strptime(story["started"][:19], "%Y-%m-%dT%H:%M:%S"))
    t1 = (time.mktime(time.strptime(story["ended"][:19], "%Y-%m-%dT%H:%M:%S"))
          if story["ended"] else time.time())
    m = int(t1 - t0) // 60
    return t("short.hours", h=m // 60, m=m % 60) if m >= 60 else t("short.minutes", m=m)


def title(story: dict) -> str:
    head = {"running": "❓" if _open(story) else "⏳", "done": "✅", "failed": "❌"}[story["status"]]
    return t("short.title", head=head, n=story["id"], title=story["title"])


def markdown(story: dict) -> str:
    marks, here = _walk(story), _here(story)
    done = sum(m["status"] in ("done", "skipped") for m in marks)
    bar = "▓" * done + "░" * (len(marks) - done)
    lines = [f"**{done}/{len(marks)}** {bar} · {_elapsed(story)}"
             + (f" · {story['who']}" if story.get("who") else "")
             + (f" · 👥 {story['team']}" if story.get("team") else "")]
    if up := story.get("parent"):
        lines.append(t("short.child.up", n=up["story"], title=up["title"], step=up.get("label") or up["step"]))
    if (stop := story.get("stop")) and story["status"] == "running":
        lines.append(t("short.stop_asked", where=_from(stop), at=stop["at"][11:16]))
    unread = sum(not r["read"] for r in story.get("replies", []))
    if unread and story["status"] == "running":
        lines.append(t("short.unread.many" if unread > 1 else "short.unread.one", n=unread))
    lines += _questions(story)
    for m in story["marks"]:
        at = f" `{m['at'][11:16]}`" if m.get("at") else ""
        extra = "" if m.get("planned", True) else t("short.not_planned")
        f = m.get("fork")
        took = f" → **{f['took']}**" if f and f["took"] else ""
        loop = m.get("loop")
        if loop and loop.get("open"):
            took = f" ×? · {t('short.loop.try', k=len(loop['rounds']))}" if m["status"] != "todo" else " ×?"
        elif loop:
            rounds = loop["rounds"]
            now = next((k + 1 for k, r in enumerate(rounds) if r["status"] in ("todo", "doing")), None)
            took = f" ×{len(rounds)}" + (f" ·{_round(loop, now)}" if now and m["status"] != "todo" else "")
        took += _child_tag(m) if not loop else ""
        lines.append(f"{ICON[m['status']]} {m['name']}{took}{at}{extra}"
                     + (f" — {m['note']}" if m.get("note") else "") + (t("short.here") + _tag(m) if m is here else ""))
        lines += _options(f, here) if f else []
        if loop and loop.get("open"):
            lines += _tries(loop, here)
        elif loop:
            lines.append(INDENT + "└ " + " → ".join(f"{ICON[r['status']]} {r['name']}"
                                                    + (t("short.here") + _tag(m) if r is here else "") for r in loop["rounds"]))
            lines += [INDENT + INDENT + f"↳ {r['name']}{_child_tag(r)}" for r in loop["rounds"] if r.get("child")]
    if story["events"]:
        lines.append("")
        lines += [f"`{e['at'][11:16]}` {e['text']}" for e in story["events"][-EVENTS_SHOWN:]]
    return "\n".join(lines)


def _tag(m: dict) -> str:
    """ · app-release-kit/sub:mac: who is on the step being done, the card's only word on the
    execution (specs/multi-brahman.md)."""
    return f" · {m['tag']}" if m.get("tag") else ""


def _child_tag(slot: dict) -> str:
    """ → #13 · 2/5 · freerdp: the child story doing this step (or round), as one tag. A
    step planned as "→ child" whose child has not started yet says so."""
    c = slot.get("child")
    if not c:
        return t("short.child.expected") if slot.get("expects_child") else ""
    head = f" → #{c['id']}" + ("" if c["status"] == "running" else " " + ICON[c["status"]])
    return (head + f" · {c['done']}/{c['total']}" + (f" · {c['who']}" if c.get("who") else "")
            + (" · ■" if c.get("stop") and c["status"] == "running" else ""))


def _tries(loop: dict, here: dict | None) -> list[str]:
    """An open loop's tries, one line each with its time and note, the last few only."""
    shown = loop["rounds"][-TRIES_SHOWN:]
    out = [INDENT + "├ " + t("short.loop.earlier", n=len(loop["rounds"]) - len(shown))] if len(shown) < len(loop["rounds"]) else []
    for k, r in enumerate(shown):
        out.append(INDENT + ("└" if k == len(shown) - 1 else "├") + f" {ICON[r['status']]} {r['name']}"
                   + _child_tag(r) + (f" `{r['at'][11:16]}`" if r.get("at") else "")
                   + (f" — {r['note']}" if r.get("note") else "") + (t("short.here") if r is here else ""))
    return out


def _options(f: dict, here: dict | None) -> list[str]:
    """A fork's options under it, as branches: the one taken with its steps, the others faded."""
    out = []
    for k, o in enumerate(f["options"]):
        branch = INDENT + ("└" if k == len(f["options"]) - 1 else "├")
        steps = " → ".join(f"{ICON[s['status']]} {s['name']}" + (t("short.here") + _tag(s) if s is here else "")
                           for s in o["steps"])
        if f["took"] and o["name"] != f["took"]:
            out.append(f"{branch} ┄ {o['name']}{t('short.fork.not_taken')}")
        else:
            out.append(f"{branch} {'▶' if f['took'] else '○'} **{o['name']}**" + (f": {steps}" if steps else ""))
    return out


def _questions(story: dict) -> list[str]:
    """The questions, above the marks: open ones first and in bold, so he sees what waits
    for him; answered ones with what he said."""
    qs, running = story.get("questions", []), story["status"] == "running"
    out = [t("short.q.open", q=q["id"], text=q["text"]) for q in qs if not q["answer"] and running]
    if out:  # a reply answers the oldest open question (reply())
        out.append(t("short.q.hint_options" if any(q["options"] for q in _open(story))
                     else "short.q.hint", q=_open(story)[0]["id"]))
    for q in qs:
        if a := q["answer"]:
            out.append(t("short.q.answered", q=q["id"], text=q["text"], answer=a["text"],
                         where=app(a["where"]), at=a["at"][11:16]))
        elif not running:
            out.append(t("short.q.unanswered", q=q["id"], text=q["text"]))
    return out


def _show(channels: list[Channel], chats: list[str], story: dict) -> None:
    """Post the card where it is not yet, change it where it is. Called under the story's
    lock, so two sessions marking at once cannot leave the older state on the card."""
    for chat in chats:
        ch = next((c for c in channels if c.owns(chat)), None)
        if ch is None or not hasattr(ch, "track"):
            continue
        handle = ch.track(chat, markdown(story), title=title(story), handle=story["copies"].get(chat),
                          failed=story["status"] == "failed", done=story["status"] == "done",
                          buttons=buttons(story))
        if handle:
            story["copies"][chat] = handle


def buttons(story: dict) -> list[tuple[str, str]]:
    """(label, action) under a running story's card; a press comes back as that action.
    The ":reply" one takes text (see Channel.track); "short:<n>:a<question>.<option>" answers
    a question, its label numbered when more than one is open."""
    if story["status"] != "running":
        return []
    n, waiting = story["id"], _open(story)
    options = [(o if len(waiting) == 1 else f"{q['id']} · {o}", f"short:{n}:a{q['id']}.{i}")
               for q in waiting for i, o in enumerate(q["options"])]
    return options + (([] if story.get("stop") else [(t("btn.stop"), f"short:{n}:stop")])
            + [(t("btn.reply"), f"short:{n}:reply")])


def board(state: Path, everything: bool = False) -> str:
    """The open short stories (or all), one line each, for /short and `buddy short list`."""
    ts = [s for s in all_stories(state) if everything or s["status"] == "running"]
    if not ts:
        return t("short.none_running") if not everything else t("short.none")
    shown = {s["id"] for s in ts}
    under = {}  # a child is listed under its parent when the parent is listed too
    for s in ts:
        if (up := s.get("parent")) and up["story"] in shown:
            under.setdefault(up["story"], []).append(s)
    out = []
    for s in ts:
        if (up := s.get("parent")) and up["story"] in shown:
            continue
        out.append(_board_line(s))
        out += [INDENT + "↳ " + _board_line(c) for c in under.get(s["id"], [])]
    return "\n".join(out)


def _board_line(s: dict) -> str:
    done = sum(m["status"] in ("done", "skipped") for m in s["marks"])
    last = s["events"][-1]["text"] if s["events"] else ""
    return t("short.board_line", title=title(s), done=done, total=len(s["marks"]),
             elapsed=_elapsed(s), last=last)
