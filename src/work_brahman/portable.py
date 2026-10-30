"""What differs between Linux/macOS and Windows, in one place, so the rest of the buddy
reads the same on both:

- locked(path): an exclusive lock held while a block runs. POSIX: flock. Windows: msvcrt
  locks the file's first byte (a mandatory lock; LK_LOCK retries for 10 s, so this loops).
- alive(pid): is that process still running? POSIX: os.kill(pid, 0). Never that on Windows:
  there os.kill with 0 sends CTRL_C_EVENT to the process's console group, so it asks
  OpenProcess + GetExitCodeProcess instead (259 = STILL_ACTIVE).
- kill_tree(p): stop a child and everything it started. POSIX: SIGKILL to its session's
  group (procs.run starts each child in a new session). Windows: taskkill /T /F, since
  Popen.kill() would stop only the child itself and leave claude's node running.
- parent(pid): (parent pid, program name). Linux reads /proc; macOS has none, so it asks
  ps; Windows: None (long stories follow a terminal there through screen, which it lacks).
"""

import os
import signal
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

WINDOWS = os.name == "nt"
if WINDOWS:
    import msvcrt
else:
    import fcntl


@contextmanager
def locked(path: Path):
    with open(path, "a+") as f:
        if not WINDOWS:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield f
            return
        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                break
            except OSError:  # still held after LK_LOCK's own 10 tries
                time.sleep(0.1)
        try:
            yield f
        finally:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def alive(pid: int) -> bool:
    if not WINDOWS:
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:  # it runs, as someone else
            return True
        except OSError:
            return False
    import ctypes
    from ctypes import wintypes
    k = ctypes.windll.kernel32
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        return bool(k.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259
    finally:
        k.CloseHandle(h)


def parent(pid: int) -> tuple[int, str] | None:
    if WINDOWS:
        return None
    try:
        if Path(f"/proc/{pid}/stat").exists():
            name = Path(f"/proc/{pid}/comm").read_text().strip()
            return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1]), name
        return _ps(pid)
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _ps(pid: int) -> tuple[int, str]:
    out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True,
                         text=True, timeout=5).stdout.strip()
    ppid, _, comm = out.partition(" ")
    return int(ppid), Path(comm.strip()).name  # macOS ps gives the program's path


def kill_tree(p: subprocess.Popen) -> None:
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, check=False)
        else:
            os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
