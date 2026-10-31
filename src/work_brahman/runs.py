"""Run records: one JSON file per run of a job, a duty or a chat turn.

A run is one trigger to one result. Its record says what started it, the steps it took
(with the agent session id of each step that ran an agent), how it ended, and, when it
failed, the tail of its output: the black box. When it ends, its fitness: what the run
cost (wall time, and for agent steps list-price dollars, turns and tokens) and how many
tools a person would have had to allow. Selection reads these numbers. The scheduler,
`buddy /log` and a workbench all read these files; nothing else holds that history.

    $XDG_STATE_HOME/work_brahman/runs/<YYYY-MM-DD>/<run id>.json
"""

import json
import os
import secrets
import subprocess
import time
from datetime import datetime
from pathlib import Path

TAIL = 4000  # characters of output kept for a failed run
COSTS = ("usd_list", "turns", "agent_ms", "tokens_in", "tokens_cached", "tokens_out")
STAMP = "%Y-%m-%dT%H:%M:%S%z"


# The SOP of each kind of run: the steps its code can record, in order, so a workbench can
# draw a run as the path it took through them. An item is [label, step], the step's name
# as Run.step() records it; a fork is {"fork": [[when, items], ...]}, and the items after it
# follow whichever branch was taken; an empty branch is a step that may be skipped. "quiet"
# is not recorded: it is a run that ended with "no changes". These follow cli.run_duty,
# cli.run_job, cli.run_family and the bot's chat turn; change them together.
SOPS = {
    "duty": [
        {"fork": [["on schedule, or /update", [["check what changed", "check"]]],
                  ["an answer asked for it", [["do what the answer asked", "act"]]]]},
        {"fork": [["when it has a question", [["ask you, with buttons", "ask"]]], ["", []]]},
        {"fork": [["something to say", [["post it to every linked chat", "post"]]],
                  ["nothing new", [["say nothing", "quiet"]]]]},
        {"fork": [["when you answer", [["your answer, from any app", "answer"]]], ["", []]]},
    ],
    "job": [["run the agent on the job's prompt", "agent"],
            ["post the answer; a failure asks ↻ retry?", "post"]],
    "family": [["render the scheduled message", "render"], ["post it to every linked chat", "post"]],
    "chat": [["your message, to the chat's agent session", "agent"]],
}


def _now() -> str:
    return time.strftime(STAMP)


def _version() -> str:
    """The commit of the code this process loaded, so a record says which code produced it.

    Read once, at import: that is the code the process runs, even if the tree moves on.
    "+dirty" when the package had uncommitted changes, which the commit alone can't show.
    """
    here = str(Path(__file__).resolve().parent)
    try:
        sha = subprocess.run(["git", "-C", here, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        if sha and subprocess.run(["git", "-C", here, "diff", "--quiet", "HEAD", "--", "."],
                                  capture_output=True, timeout=5).returncode == 1:
            sha += "+dirty"
        return sha or _installed()
    except (OSError, subprocess.SubprocessError):
        return _installed()


def _installed() -> str:
    """An installed buddy (a wheel) has no git: its package version stands for the code."""
    try:
        from importlib.metadata import version
        for name in ("brahman-buddy", "work-brahman"):  # its name on PyPI; the one before 0.2.6
            try:
                return version(name)
            except Exception:  # noqa: BLE001
                continue
        return "n/a"
    except Exception:  # noqa: BLE001 - no version is no reason to fail
        return "n/a"


VERSION = _version()


class Run:
    def __init__(self, root: Path, kind: str, name: str, trigger: str, run_id: str = ""):
        """A new run, or, given run_id, the run the scheduler already opened for this process."""
        self.root = root
        self.id = run_id or f"{time.strftime('%Y%m%d-%H%M%S')}-{name}-{secrets.token_hex(2)}"
        self.path = root / "runs" / f"{self.id[:4]}-{self.id[4:6]}-{self.id[6:8]}" / f"{self.id}.json"
        if run_id and self.path.exists():
            self.rec = json.loads(self.path.read_text())
            return
        self.rec = {"id": self.id, "kind": kind, "name": name, "trigger": trigger,
                    "started": _now(), "status": "running", "version": VERSION, "steps": []}
        self.save()

    def step(self, name: str, **fields) -> None:
        cost = fields.get("cost")
        if cost and "usd_session" in cost and fields.get("session_id"):
            cost.update(turn_cost(self.root, fields["session_id"], cost["usd_session"],
                                  cost.get("resumed", True)))
        self.rec["steps"].append({"step": name, "at": _now(), **fields})
        self.save()

    def finish(self, status: str, **fields) -> None:
        """status: ok | failed | timeout. Fields such as result, error or output are kept."""
        self.rec.update(fields, status=status, ended=_now())
        self.rec["fitness"] = fitness(self.rec)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.rec, indent=1, ensure_ascii=False))
        os.replace(tmp, self.path)


def turn_cost(root: Path, session_id: str, usd_session: float, resumed: bool = True) -> dict:
    """This turn's share of a session's running cost: the total now minus the last total seen.

    Claude Code reports the cost of the whole session so far, so a resumed chat turn would
    otherwise be charged for every turn before it. The last total per session is kept in
    state/session_costs.json. A new session's total is its first turn's cost; a resumed
    session first seen here (older than this file) can only be charged its whole total.
    usd_from says which: new, delta or session. Turns taken outside
    the buddy (claude --resume in a terminal) land in the next turn's share.
    """
    index = root / "session_costs.json"
    try:
        seen = json.loads(index.read_text())
    except (OSError, ValueError):
        seen = {}
    last = seen.get(session_id)
    seen[session_id] = usd_session
    tmp = index.with_suffix(".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(seen))
    os.replace(tmp, index)
    if not resumed:
        return {"usd_list": round(usd_session, 6), "usd_from": "new"}
    if last is None or usd_session < last:
        return {"usd_list": round(usd_session, 6), "usd_from": "session"}
    return {"usd_list": round(usd_session - last, 6), "usd_from": "delta"}


def fitness(rec: dict) -> dict:
    """The run's cost, summed over its steps; recomputed whenever the run is finished again."""
    started, ended = (datetime.strptime(rec[k], STAMP) for k in ("started", "ended"))
    f = {"seconds": round((ended - started).total_seconds())}
    agents = [s["cost"] for s in rec["steps"] if s.get("cost")]
    if agents:
        f.update({k: sum(c.get(k) or 0 for c in agents) for k in COSTS})
        f["usd_list"] = round(f["usd_list"], 6)
    f["denied"] = sum(len(s.get("denied") or []) for s in rec["steps"])
    return f


def from_env(root: Path, kind: str, name: str) -> Run:
    """The run for this process: the scheduler's (BUDDY_RUN_ID), or a new manual one."""
    run = Run(root, kind, name, os.environ.get("BUDDY_TRIGGER", "manual"),
              os.environ.get("BUDDY_RUN_ID", ""))
    if run.rec.get("version") != VERSION:
        # The scheduler opened the run with the code it loaded at its start; the steps
        # run on the code this process loaded. Record ours, keep theirs.
        run.rec["scheduler_version"] = run.rec.get("version")
        run.rec["version"] = VERSION
        run.save()
    return run


def note_step(root: Path, run_id: str, name: str, **fields) -> bool:
    """Add a step to a run that already has a record (an answer that came later)."""
    path = root / "runs" / f"{run_id[:4]}-{run_id[4:6]}-{run_id[6:8]}" / f"{run_id}.json"
    if not path.exists():
        return False
    run = Run(root, "", "", "", run_id)
    run.step(name, **fields)
    return True


def read(rec: dict) -> dict:
    """A run record as readers see it. Chat is one workflow, "chat", whichever app the
    message came in on; records written before 2026-10-26 named the app instead."""
    if rec.get("kind") == "chat" and rec.get("name") != "chat":
        rec = {**rec, "name": "chat", "trigger": f"{rec.get('trigger', 'message')} on {rec.get('name')}"}
    return rec


def tail(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= TAIL else "…" + text[-TAIL:]
