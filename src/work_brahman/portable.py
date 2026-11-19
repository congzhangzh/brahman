"""What differs between Linux/macOS and Windows, in one place, so the rest of the buddy
reads the same on both:

- locked(path): an exclusive lock held while a block runs. POSIX: flock. Windows: msvcrt
  locks the file's first byte (a mandatory lock; LK_LOCK retries for 10 s, so this loops).
- hold(path): that lock for the life of the process, without waiting (None: someone has it);
  `buddy serve` takes one so a second buddy never fights the first for the chats.
- alive(pid): is that process still running? POSIX: os.kill(pid, 0). Never that on Windows:
  there os.kill with 0 sends CTRL_C_EVENT to the process's console group, so it asks
  OpenProcess + GetExitCodeProcess instead (259 = STILL_ACTIVE).
- kill_tree(p): stop a child and everything it started. POSIX: SIGKILL to its session's
  group (procs.run starts each child in a new session). Windows: taskkill /T /F, since
  Popen.kill() would stop only the child itself and leave claude's node running.
- peer_uid(client_port, server_port): who opened a TCP connection to this machine's loopback.
  Linux reads /proc/net/tcp(6): the client socket's line names its owner. None elsewhere.
- parent(pid): (parent pid, program name). Linux reads /proc; macOS has none, so it asks
  ps; Windows: None (long stories follow a terminal there through screen, which it lacks).
- argv(cmd): the command as the system can start it. POSIX: as it is. Windows: CreateProcess
  finds only an .exe on PATH, and npm installs claude and codex as .cmd shims, so ["claude", ...]
  was "file not found" there (2026-11-18, the VM). The shim's own target is used when it names an
  .exe (`"%dp0%/.../claude.exe" %*`, with backslashes): no cmd.exe in between, which would cut a multi-line
  argument (--append-system-prompt) at its first newline and read & | < > in it. Else the shim
  itself (cmd.exe runs it), else the name unchanged.
"""

import os
import re
import shutil
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


def hold(path: Path):
    """Take path's lock for the life of this process, without waiting: the open file (keep
    it; closing it, or exiting, frees the lock), or None when another process holds it. The
    holder's pid is in path + ".pid" (a side file: Windows' lock keeps the locked byte from
    being read)."""
    f = open(path, "a+")
    try:
        if WINDOWS:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    Path(str(path) + ".pid").write_text(f"{os.getpid()}\n")
    return f


def holder(path: Path) -> int | None:
    try:
        return int(Path(str(path) + ".pid").read_text().strip())
    except (OSError, ValueError):
        return None


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


def peer_uid(client_port: int, server_port: int) -> int | None:
    """The uid that owns the client end of a loopback connection (Linux), else None."""
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            f = line.split()  # sl local rem st tx:rx tr:when retrnsmt uid ...
            try:
                if int(f[1].rsplit(":", 1)[1], 16) == client_port and int(f[2].rsplit(":", 1)[1], 16) == server_port:
                    return int(f[7])
            except (IndexError, ValueError):
                continue
    return None


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


def process(pid: int) -> dict | None:
    """pid as ps sees it, or None when it is gone: its name, process group, the foreground group
    of its terminal (tpgid), the terminal ("?" or "??" for none) and when it started, which tells
    it from a later process given the same number. One path on Linux and macOS."""
    if WINDOWS:
        return None
    try:
        f = subprocess.run(["ps", "-o", "pgid=,tpgid=,tty=,lstart=,comm=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=5).stdout.split()
        return {"pgid": int(f[0]), "tpgid": int(f[1]), "tty": f[2], "started": " ".join(f[3:8]),
                "comm": Path(" ".join(f[8:])).name}  # macOS ps gives the program's path
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


_SHIM_TARGET = re.compile(r'"%(?:~?dp0)%\\?([^"%]+\.(exe|js))"', re.I)  # "%dp0%\node_modules\...\x.exe|js"


def _which(name: str) -> str:
    """shutil.which without Windows' current-folder-first search (review, 2026-11-19: a claude.cmd
    planted in a story's folder would run as the owner): NoDefaultCurrentDirectoryInExePath, which
    which() honours since Python 3.12 and CreateProcess too; and only an absolute result."""
    os.environ.setdefault("NoDefaultCurrentDirectoryInExePath", "1")
    found = shutil.which(name)
    return found if found and os.path.isabs(found) else ""


def argv(cmd: list[str]) -> list[str]:
    """cmd with its program resolved where the system needs it (Windows; see the module's notes).
    Never from the current folder (_which). An npm shim is read on its last line, the one that runs
    `... %*` (a guard like codex.cmd's `IF EXIST "%dp0%\\node.exe" (` is never taken for it):
    `"%dp0%\\...\\x.exe" %*` gives that exe (claude); `"%_prog%" "%dp0%\\...\\x.js" %*` gives node
    and that script (codex; node.exe beside the shim when there is one, as the shim itself prefers,
    else node from PATH). Neither goes through cmd.exe, which splits an argument at & | (a folder
    named R&D) and cuts it at a newline. Else the shim itself."""
    if not WINDOWS or not cmd:
        return list(cmd)
    found = _which(cmd[0])
    if not found:
        return list(cmd)
    if found.lower().endswith((".cmd", ".bat")):
        try:
            runs = [ln for ln in Path(found).read_text(errors="replace").splitlines() if ln.rstrip().endswith("%*")]
        except OSError:
            runs = []
        m = _SHIM_TARGET.search(runs[-1]) if runs else None
        if m:
            base = Path(found).parent
            target = base.joinpath(*[x for x in re.split(r"[\\/]+", m[1]) if x])
            if target.is_file() and m[2].lower() == "exe":
                return [str(target), *cmd[1:]]
            if target.is_file() and "%_prog%" in runs[-1]:
                node = base / "node.exe"
                node = str(node) if node.is_file() else _which("node")
                if node:
                    return [node, str(target), *cmd[1:]]
        import logging  # a shim whose form we don't know: visible in buddy.log (review, 2026-11-19)
        logging.getLogger("buddy.portable").info("running %s through cmd.exe (its form is not known)", found)
    return [found, *cmd[1:]]
