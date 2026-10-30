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
"""

import re

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
          *, who: str = "", note: str = "") -> dict:
    with _locked(state, "next"):
        n = max([t["id"] for t in all_stories(state)] or [0]) + 1
        story = {"id": n, "title": title, "who": who, "started": _now(), "ended": None,
                "status": "running", "marks": [], "events": [], "chats": chats, "copies": {}}
        for m in marks:
            _mark(story, m, "todo")
        _event(story, _noted(t("short.ev.started"), note))
        _save(state, story)
    with _locked(state, str(n)):
        _show(channels, chats, story)
        _save(state, story)
    return story


def mark(state: Path, channels: list[Channel], number: int, name: str, *, status: str = "done",
         note: str = "", took: str | None = None, round: int | None = None) -> dict | None:
    """A mark happened. took: the option a fork went down; round: a loop's round (from 1)."""
    def change(s):
        m = _mark(s, name, status, note, took, round)
        took_ = (m.get("fork") or {}).get("took") if took else None
        _event(s, _noted(f"{ICON[status]} {m['name']}" + (f" → {took_}" if took_ else "")
                         + _round(m.get("loop"), m["loop"].get("last") if m.get("loop") else None), note))
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
        s["status"], s["ended"] = ("failed" if failed else "done"), _now()
        _event(s, _noted(t("short.ev.failed" if failed else "short.ev.done"), note))
    return _change(state, channels, number, end)


def ask_stop(state: Path, channels: list[Channel], number: int, *, where: str,
             who: str = "") -> dict | None:
    """■ Stop on the card (or ■ STOP in chat): recorded for the executor, which stops at
    its next call. A story that ended, or was already asked, is left as it is."""
    def stop(s):
        if s["status"] != "running" or s.get("stop"):
            return False  # nothing new: a second press must not edit every card again
        s["stop"] = {"where": where, "who": who, "at": _now()}
        _event(s, t("short.ev.stop", where=app(where)))
    return _change(state, channels, number, stop)


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
        if show:
            _show(channels, story.get("chats") or list(story["copies"]), story)
        _save(state, story)
    return story


def _key(name: str) -> str:
    return name.strip().rstrip("?？").strip().casefold()  # "CI green?" is "ci green" too


def _find(story: dict, name: str) -> dict | None:
    return next((m for m in story["marks"] if _key(m["name"]) == _key(name)), None)


def _step(story: dict, name: str) -> tuple[dict, dict, dict] | None:
    """A small step under a fork's option: (the fork's mark, the option, the step)."""
    return next(((m, o, s) for m in story["marks"] for o in (m.get("fork") or {}).get("options", [])
                 for s in o["steps"] if _key(s["name"]) == _key(name)), None)


def _spec(text: str) -> dict:
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


def _event(story: dict, text: str) -> None:
    story["events"].append({"at": _now(), "text": text})


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
             + (f" · {story['who']}" if story.get("who") else "")]
    if (stop := story.get("stop")) and story["status"] == "running":
        lines.append(t("short.stop_asked", where=app(stop["where"]), at=stop["at"][11:16]))
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
        lines.append(f"{ICON[m['status']]} {m['name']}{took}{at}{extra}"
                     + (f" — {m['note']}" if m.get("note") else "") + (t("short.here") if m is here else ""))
        lines += _options(f, here) if f else []
        if loop and loop.get("open"):
            lines += _tries(loop, here)
        elif loop:
            lines.append(INDENT + "└ " + " → ".join(f"{ICON[r['status']]} {r['name']}"
                                                    + (t("short.here") if r is here else "") for r in loop["rounds"]))
    if story["events"]:
        lines.append("")
        lines += [f"`{e['at'][11:16]}` {e['text']}" for e in story["events"][-EVENTS_SHOWN:]]
    return "\n".join(lines)


def _tries(loop: dict, here: dict | None) -> list[str]:
    """An open loop's tries, one line each with its time and note, the last few only."""
    shown = loop["rounds"][-TRIES_SHOWN:]
    out = [INDENT + "├ " + t("short.loop.earlier", n=len(loop["rounds"]) - len(shown))] if len(shown) < len(loop["rounds"]) else []
    for k, r in enumerate(shown):
        out.append(INDENT + ("└" if k == len(shown) - 1 else "├") + f" {ICON[r['status']]} {r['name']}"
                   + (f" `{r['at'][11:16]}`" if r.get("at") else "") + (f" — {r['note']}" if r.get("note") else "")
                   + (t("short.here") if r is here else ""))
    return out


def _options(f: dict, here: dict | None) -> list[str]:
    """A fork's options under it, as branches: the one taken with its steps, the others faded."""
    out = []
    for k, o in enumerate(f["options"]):
        branch = INDENT + ("└" if k == len(f["options"]) - 1 else "├")
        steps = " → ".join(f"{ICON[s['status']]} {s['name']}" + (t("short.here") if s is here else "")
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
    out = []
    for s in ts:
        done = sum(m["status"] in ("done", "skipped") for m in s["marks"])
        last = s["events"][-1]["text"] if s["events"] else ""
        out.append(t("short.board_line", title=title(s), done=done, total=len(s["marks"]),
                     elapsed=_elapsed(s), last=last))
    return "\n".join(out)
