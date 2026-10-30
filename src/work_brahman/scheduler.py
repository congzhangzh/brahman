"""The buddy's own scheduler: no systemd timers, the same on Linux, macOS and Windows.

A task is a [jobs.<name>] or [duties.<name>] with a `schedule`, either cron (five fields:
minute hour day-of-month month day-of-week, e.g. "*/10 * * * *" or "0 9 * * 1-5") or an
interval ("every 10m", "every 2h"). It runs inside `buddy serve`:

- Each run is a child process (`buddy job|duty <name>`), so a task that crashes or hangs
  never takes the chat bot down, and gets killed after its timeout.
- A run missed while the buddy was down or the machine asleep runs once on start
  (what systemd calls Persistent=true). Missed slots never pile up into several runs.
- A task never overlaps itself: a slot that comes while it still runs is skipped.
- Every run gets a run record (runs.py); the child adds its steps to it, and the
  scheduler adds the exit code and, on failure, the tail of the output.
- STOP pauses it (state/paused.json, so a restart stays paused): slots that come while
  paused are skipped, not caught up on resume, and runs it kills end as "stopped".

Only `buddy serve` itself needs the OS to keep it running.
"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import procs
from .runs import Run, tail
from .text import t

log = logging.getLogger("buddy.scheduler")

LOOKBACK = timedelta(days=7)  # how far back a missed run is still caught up


class Cron:
    """Five-field cron. Day-of-month and day-of-week combine with OR when both are
    restricted, as in classic cron. Day-of-week: 0 or 7 is Sunday."""

    RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]

    def __init__(self, expr: str):
        fields = expr.split()
        if len(fields) != 5:
            raise ValueError(f"cron needs 5 fields: {expr!r}")
        self.sets = [self._field(f, lo, hi) for f, (lo, hi) in zip(fields, self.RANGES)]
        if 7 in self.sets[4]:
            self.sets[4] |= {0}
        self.dom_any, self.dow_any = fields[2] == "*", fields[4] == "*"

    @staticmethod
    def _field(f: str, lo: int, hi: int) -> set[int]:
        out = set()
        for part in f.split(","):
            rng, _, step = part.partition("/")
            if rng == "*":
                a, b = lo, hi
            elif "-" in rng:
                a, b = map(int, rng.split("-"))
            else:
                a = b = int(rng)
                if step:
                    b = hi
            if not (lo <= a <= b <= hi):
                raise ValueError(f"cron field {f!r} outside {lo}-{hi}")
            out |= set(range(a, b + 1, int(step) if step else 1))
        return out

    def matches(self, t: datetime) -> bool:
        mi, ho, dom, mo, dow = self.sets
        if t.minute not in mi or t.hour not in ho or t.month not in mo:
            return False
        day, wday = t.day in dom, (t.isoweekday() % 7) in dow
        if self.dom_any or self.dow_any:
            return day and wday
        return day or wday

    def last_due(self, after: datetime, now: datetime) -> datetime | None:
        """The latest scheduled minute in (after, now], or None."""
        t = now.replace(second=0, microsecond=0)
        stop = max(after, now - LOOKBACK)
        while t > stop:
            if self.matches(t):
                return t
            t -= timedelta(minutes=1)
        return None


class Every:
    UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

    def __init__(self, expr: str):
        m = re.fullmatch(r"every\s+(\d+)\s*([smhd])", expr.strip())
        if not m:
            raise ValueError(f"interval must look like 'every 10m': {expr!r}")
        self.step = timedelta(seconds=int(m[1]) * self.UNITS[m[2]])

    def last_due(self, after: datetime, now: datetime) -> datetime | None:
        return after + self.step if now >= after + self.step else None


def parse(expr: str):
    return Every(expr) if expr.strip().startswith("every") else Cron(expr)


class Scheduler:
    def __init__(self, cfg: dict, config_path: str, state: Path):
        self.config_path, self.state = config_path, state
        self.tasks = {}  # "duty:code-review" -> (kind, name, schedule, timeout)
        self.timeouts = {}  # every job and duty, scheduled or not, for run_now()
        for kind, section in (("job", "jobs"), ("duty", "duties"), ("family", "families")):
            for name, t in cfg.get(section, {}).items():
                timeout = t.get("timeout", cfg.get("claude", {}).get("timeout", 1800) + 60)
                self.timeouts[f"{kind}:{name}"] = timeout
                if "schedule" in t:
                    self.tasks[f"{kind}:{name}"] = (kind, name, parse(t["schedule"]), timeout)
        self.last_path = state / "scheduler.json"
        self.last = json.loads(self.last_path.read_text()) if self.last_path.exists() else {}
        self.running: set[str] = set()
        self.stopping: set[str] = set()  # killed by STOP: they end as "stopped"
        self.paused_path = state / "paused.json"
        self.paused = json.loads(self.paused_path.read_text()) if self.paused_path.exists() else None
        self.lock = threading.Lock()

    def pause(self, where: str, who: str) -> list[str]:
        """Start nothing new until resume(); the running tasks will end as "stopped" once
        killed (the caller kills them with procs.kill_all()). Returns those tasks."""
        with self.lock:
            self.paused = {"where": where, "who": who, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
            self.paused_path.parent.mkdir(parents=True, exist_ok=True)
            self.paused_path.write_text(json.dumps(self.paused))
            self.stopping |= self.running
            return sorted(self.running)

    def resume(self) -> bool:
        with self.lock:
            was, self.paused = self.paused, None
            self.paused_path.unlink(missing_ok=True)
            return was is not None

    def run_now(self, kind: str, name: str, trigger: str, act: str = "") -> str:
        """Start a job or duty now (an answer asked for it), with --act when given.
        Returns what happened."""
        base = f"{kind}:{name}"
        if base not in self.timeouts:
            return t("sched.none", kind=t(f"kind.{kind}"), name=repr(name))
        key = f"{base} {act}" if act else base  # an act never waits for the duty's own check
        with self.lock:
            if self.paused:
                return t("sched.paused")
            if key in self.running:
                return t("sched.running")
            self.running.add(key)
        threading.Thread(target=self._run, args=(key, kind, name, trigger, self.timeouts[base], act),
                         name=key, daemon=True).start()
        return t("sched.started")

    def start(self) -> None:
        self._close_interrupted()
        if not self.tasks:
            return
        log.info("scheduling %s", ", ".join(self.tasks))
        threading.Thread(target=self._loop, name="scheduler", daemon=True).start()

    def _close_interrupted(self) -> None:
        """Runs still "running" from before this start died with the last buddy process."""
        for day in sorted((self.state / "runs").glob("*"))[-2:]:
            for f in day.glob("*.json"):
                try:
                    rec = json.loads(f.read_text())
                except (OSError, ValueError):
                    continue
                if rec.get("status") == "running" and rec.get("trigger") != "manual":
                    Run(self.state, rec["kind"], rec["name"], rec["trigger"], rec["id"]).finish(
                        "interrupted", error="the buddy stopped while this run was going")

    def _loop(self) -> None:
        while True:
            try:
                self.tick(datetime.now().astimezone())
            except Exception:
                log.exception("scheduler tick failed")
            time.sleep(60 - time.time() % 60 + 1)  # just after the next minute starts

    def tick(self, now: datetime) -> None:
        for key, (kind, name, sched, timeout) in self.tasks.items():
            last = self.last.get(key)
            if last is None:  # a new task starts counting now; it doesn't run at once
                self._mark(key, now)
                continue
            due = sched.last_due(datetime.fromisoformat(last), now)
            if due is None:
                continue
            trigger = "schedule" if now - due < timedelta(minutes=2) else f"catch-up of {due:%Y-%m-%d %H:%M}"
            if self.paused:  # skipped, not caught up later: a stop means stop
                self._mark(key, now)
                log.info("%s: paused, skipped the %s slot", key, f"{due:%H:%M}")
                continue
            with self.lock:
                busy = key in self.running
                if not busy:
                    self.running.add(key)
            self._mark(key, now)
            if busy:
                log.warning("%s: still running, skipped the %s slot", key, f"{due:%H:%M}")
                continue
            threading.Thread(target=self._run, args=(key, kind, name, trigger, timeout),
                             name=key, daemon=True).start()

    def _mark(self, key: str, t: datetime) -> None:
        with self.lock:
            self.last[key] = t.isoformat(timespec="seconds")
            self.last_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.last_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.last, indent=1))
            os.replace(tmp, self.last_path)

    def _run(self, key: str, kind: str, name: str, trigger: str, timeout: int, act: str = "") -> None:
        run = Run(self.state, kind, name, trigger)
        env = dict(os.environ, BUDDY_RUN_ID=run.id, BUDDY_TRIGGER=trigger)
        cmd = [sys.executable, "-m", "work_brahman.cli", "-c", self.config_path, kind, name]
        cmd += ["--act", act] if act else []
        log.info("%s: run %s (%s)", key, run.id, trigger)
        try:
            p = procs.run(cmd, env=env, timeout=timeout)  # in its own group: STOP kills it all
            code, out, status = p.returncode, (p.stderr or "") + (p.stdout or ""), None
        except subprocess.TimeoutExpired as e:
            code, status = None, "timeout"
            out = "".join(x.decode(errors="replace") if isinstance(x, bytes) else (x or "")
                          for x in (e.stderr, e.stdout))
        finally:
            with self.lock:
                self.running.discard(key)
                stopped = key in self.stopping
                self.stopping.discard(key)
        run = Run(self.state, kind, name, trigger, run.id)  # re-read: the child wrote its steps
        if stopped:
            status = "stopped"
            out = f"stopped from {(self.paused or {}).get('where', '?')}\n" + out
        if status is None:
            status = run.rec["status"] if run.rec["status"] != "running" else ("ok" if code == 0 else "failed")
            if code != 0 and status == "ok":
                status = "failed"
        fields = {"exit_code": code}
        if status != "ok":
            fields["output"] = tail(out)  # the black box: what the child said before it died
        run.finish(status, **fields)
        log.log(logging.INFO if status == "ok" else logging.WARNING, "%s: %s (%s)", key, status, run.id)
