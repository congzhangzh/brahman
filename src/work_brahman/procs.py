"""Child processes that STOP can kill, with everything they started.

A job runs `buddy job` as a child, which runs `claude`, which runs tools. Killing only
the child would leave `claude` and its tools running, so each child gets its own process
group (a new session on POSIX, a new process group on Windows) and STOP kills the group.
"""

import os
import subprocess
import threading

from . import portable

_live: set[subprocess.Popen] = set()
_lock = threading.Lock()


def run(cmd: list[str], *, input: str | None = None, timeout: float | None = None,
        **kw) -> subprocess.CompletedProcess:
    """Like subprocess.run(capture_output=True, text=True), but kill() can stop it.

    On timeout the whole group is killed and TimeoutExpired is raised with what it said.
    """
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


def kill_all() -> int:
    """Kill every child started through run(), with its group. Returns how many."""
    with _lock:
        live = list(_live)
    for p in live:
        _kill(p)
    return len(live)


def _kill(p: subprocess.Popen) -> None:
    portable.kill_tree(p)
