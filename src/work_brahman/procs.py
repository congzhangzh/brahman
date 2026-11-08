"""Child processes under one umbrella: STOP kills them all, a restart loses none.

A job runs `buddy job` as a child, which runs `claude`, which runs tools; a chat or long-story
turn runs `claude -p` (in bubblewrap, for a long story). Two ways to hold them:

- **The umbrella (Linux with a systemd user manager, in `buddy serve`).** Each child is a
  transient user unit in `buddy.slice` (`buddy-chat.slice`, `buddy-long.slice`,
  `buddy-job.slice` under it), not in `buddy.service`'s cgroup, so restarting the buddy doesn't
  kill it. Its input, output, exit code and what it was for live in `<state>/procs/<unit>/`, so
  the next instance adopts what the last one left running and delivers it (`adopt`). STOP and
  `buddy procs kill` stop the whole slice. specs/umbrella.md says why.
- **Process groups (everywhere else: macOS, Windows, tests, one-shot CLI commands).** Each child
  gets its own process group (a new session on POSIX, a new process group on Windows) and STOP
  kills the group. A restart kills them too.

`configure(state)` switches the umbrella on; `buddy serve` calls it at the start.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from . import portable

log = logging.getLogger(__name__)

SLICE = "buddy.slice"
LAUNCHER = Path(__file__).with_name("unitexec.py")
MARGIN = 120  # RuntimeMaxSec past the caller's timeout: an adopted run never runs forever
POLL = 0.25

_live: set[subprocess.Popen] = set()
_units: set[str] = set()  # units this process is waiting on
_lock = threading.Lock()
_dir: Path | None = None


class Busy(Exception):
    """The unit of that name runs already (a long story's turn): queue, don't start a second."""


def configure(state: Path | None) -> bool:
    """Use the umbrella when a systemd user manager answers; returns whether it is on."""
    global _dir
    if state is None or portable.WINDOWS or not shutil.which("systemd-run"):
        _dir = None
        return False
    try:
        ok = subprocess.run(["systemctl", "--user", "show", "-p", "Version", "--value"],
                            capture_output=True, text=True, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    _dir = (state / "procs") if ok else None
    if _dir:
        _dir.mkdir(parents=True, exist_ok=True)
    return ok


def umbrella() -> bool:
    return _dir is not None


def run(cmd: list[str], *, input: str | None = None, timeout: float | None = None,
        kind: str = "job", unit: str = "", tag: dict | None = None, **kw) -> subprocess.CompletedProcess:
    """Like subprocess.run(capture_output=True, text=True), but kill_all() can stop it.

    kind picks the slice (chat, long, job); unit names it (else a fresh one); tag says what it is
    for, so a later instance can deliver it (adopt). On timeout the child is killed and
    TimeoutExpired is raised with what it said. Busy: a unit of that name runs already.
    """
    if _dir is None:
        return _run_group(cmd, input=input, timeout=timeout, **kw)
    name = unit or f"buddy-{kind}-{uuid.uuid4().hex[:8]}"
    d = _start(name, cmd, input=input, timeout=timeout, kind=kind, tag=tag,
               cwd=kw.get("cwd"), env=kw.get("env"))
    with _lock:
        _units.add(name)
    try:
        code = wait(name, timeout=timeout)
    finally:
        with _lock:
            _units.discard(name)
    out, err = _read(d / "out"), _read(d / "err")
    if code is None:  # timed out: stop it, say what it had said so far
        _stop(name)
        _finish(d, None, delivered=True)
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    _finish(d, code, delivered=True)
    return subprocess.CompletedProcess(cmd, code, out, err)


def _start(name: str, cmd: list[str], *, input, timeout, kind, tag, cwd, env) -> Path:
    d = _dir / name
    if d.exists() and active(name):
        raise Busy(name)
    shutil.rmtree(d, ignore_errors=True)  # a dead run's leftovers under the same name
    d.mkdir(parents=True)
    spec = d / "spec.json"
    fd = os.open(spec, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # the env may carry tokens
    with os.fdopen(fd, "w") as f:
        json.dump({"cmd": list(cmd), "cwd": str(cwd) if cwd else "",
                   "env": dict(env) if env is not None else dict(os.environ)}, f)
    (d / "in").write_text(input or "")
    meta = {"unit": name, "kind": kind, "tag": tag or {}, "cmd": [str(x) for x in cmd[:3]],
            "started": datetime.now().astimezone().isoformat(timespec="seconds"),
            "timeout": timeout, "owner": os.getpid(), "delivered": False}
    _write(d / "meta.json", meta)
    props = [f"StandardInput=file:{d / 'in'}", f"StandardOutput=file:{d / 'out'}",
             f"StandardError=file:{d / 'err'}"]
    if timeout:
        props.append(f"RuntimeMaxSec={int(timeout) + MARGIN}")
    argv = ["systemd-run", "--user", "--quiet", "--collect", f"--slice=buddy-{kind}.slice",
            f"--unit={name}", f"--description=buddy {kind}: {' '.join(meta['cmd'])}"]
    argv += [x for p in props for x in ("-p", p)]
    argv += ["--", sys.executable, "-I", str(LAUNCHER), str(d)]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    if p.returncode != 0:
        if "already" in p.stderr:
            raise Busy(name)
        shutil.rmtree(d, ignore_errors=True)
        raise OSError(f"systemd-run: {p.stderr.strip()}")
    return d


def wait(name: str, timeout: float | None = None) -> int | None:
    """Wait for a unit's run to end: its exit code (negative: killed by that signal; -9 when
    it was stopped and wrote none), or None when timeout seconds passed first."""
    d, end, next_check = _dir / name, (time.monotonic() + timeout) if timeout else None, 0.0
    while True:
        rc = _rc(d)
        if rc is not None:
            return rc
        now = time.monotonic()
        if now >= next_check:  # stopped (STOP, RuntimeMaxSec) leaves no rc
            next_check = now + 2
            if not active(name):
                return _rc(d) if _rc(d) is not None else -9
        if end is not None and now >= end:
            return None
        time.sleep(POLL)


def active(name: str) -> bool:
    try:
        p = subprocess.run(["systemctl", "--user", "is-active", "--quiet", f"{name}.service"],
                           capture_output=True, timeout=10)
        return p.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return True  # can't tell: assume it runs, never deliver half a result


def adopt(deliver) -> list[str]:
    """Runs a previous instance left behind: wait for each in a thread of its own and call
    deliver(meta, CompletedProcess); the run is then marked delivered. Returns their units."""
    if _dir is None:
        return []
    found = []
    for d in sorted(_dir.iterdir()):
        meta = _meta(d)
        if not meta or meta.get("delivered") or meta.get("owner") == os.getpid():
            continue
        found.append(d.name)
        threading.Thread(target=_adopt_one, args=(d, meta, deliver), daemon=True,
                         name=f"adopt {d.name}").start()
    return found


def _adopt_one(d: Path, meta: dict, deliver) -> None:
    name = d.name
    with _lock:
        _units.add(name)
    try:
        started = datetime.fromisoformat(meta["started"]).timestamp()
        left = (meta["timeout"] - (time.time() - started)) if meta.get("timeout") else None
        code = wait(name, timeout=max(left, 1) if left is not None else None)
        if code is None:
            _stop(name)
            code = -9
        p = subprocess.CompletedProcess(meta.get("cmd", []), code, _read(d / "out"), _read(d / "err"))
        log.info("adopted %s (%s) ended with %s", name, meta.get("kind"), code)
        try:
            deliver(meta, p)
        except Exception:
            log.exception("delivering adopted %s failed", name)
        _finish(d, code, delivered=True)
    finally:
        with _lock:
            _units.discard(name)


def listing() -> list[dict]:
    """Every run on record, with whether its unit still runs (buddy procs)."""
    if _dir is None:
        return []
    out = []
    for d in sorted(_dir.iterdir()):
        meta = _meta(d)
        if meta:
            out.append({**meta, "running": _rc(d) is None and "ended" not in meta and active(d.name)})
    return out


def kill_all() -> int:
    """Kill every child: the umbrella's whole slice (also runs adopted or left behind by an
    earlier instance) and every process group started here. Returns how many."""
    with _lock:
        live = list(_live)
    for p in live:
        _kill(p)
    n = len(live)
    if _dir is not None:
        n += sum(1 for m in listing() if m["running"])
        _stop(SLICE)
    return n


def clean(keep_days: float = 7) -> int:
    """Drop the folders of delivered runs older than keep_days; returns how many."""
    if _dir is None:
        return 0
    gone, cutoff = 0, time.time() - keep_days * 86400
    for d in _dir.iterdir():
        meta = _meta(d)
        if meta and meta.get("delivered") and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            gone += 1
    return gone


def _run_group(cmd, *, input=None, timeout=None, **kw) -> subprocess.CompletedProcess:
    group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
             else {"start_new_session": True})
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE if input is not None else None,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **group, **kw)
    with _lock:
        _live.add(p)
    try:
        out, err = p.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill(p)
        out, err = p.communicate()
        raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)
    finally:
        with _lock:
            _live.discard(p)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def _kill(p: subprocess.Popen) -> None:
    portable.kill_tree(p)


def _stop(unit: str) -> None:
    name = unit if re.search(r"\.(service|slice)$", unit) else f"{unit}.service"
    try:
        subprocess.run(["systemctl", "--user", "stop", name], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        log.warning("could not stop %s", name)


def _finish(d: Path, code: int | None, *, delivered: bool) -> None:
    meta = _meta(d) or {}
    meta.update(rc=code, delivered=delivered, ended=datetime.now().astimezone().isoformat(timespec="seconds"))
    _write(d / "meta.json", meta)
    (d / "in").unlink(missing_ok=True)


def _rc(d: Path) -> int | None:
    try:
        return int((d / "rc").read_text().strip())
    except (OSError, ValueError):
        return None


def _meta(d: Path) -> dict | None:
    try:
        return json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        return None


def _read(p: Path) -> str:
    try:
        return p.read_text(errors="replace")
    except OSError:
        return ""


def _write(p: Path, data: dict) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    os.replace(tmp, p)
