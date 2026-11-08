"""The local Codex app-server, read only: is a terminal holding a thread right now?

Codex keeps who holds a thread in its daemon's memory: the writer lock is the daemon's, and a
terminal (the TUI) keeps no file of its own for the thread. So the daemon is asked, over its
control socket, which speaks WebSocket (`codex app-server proxy` with plain JSON gets no
answer): `initialize`, then `thread/read` for the thread's status and folder. A terminal loads
its thread at once and the daemon unloads it 30 to 60 seconds after the terminal quits
(measured, codex-cli 0.167.0), so "loaded" alone would still say yes for that minute: a live
terminal running in the thread's folder is the second check (terminal_in).

Nothing here changes the daemon: no thread is started, resumed or subscribed to.
"""

import base64
import json
import os
import socket
import struct
from pathlib import Path

TIMEOUT = 5  # seconds for the whole talk; the daemon answers in milliseconds


def socket_path() -> Path:
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return home / "app-server-control" / "app-server-control.sock"


class _WS:
    """The least of a WebSocket client: text frames, masked as a client must, pings answered."""

    def __init__(self, path: Path):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(TIMEOUT)
        self.s.connect(str(path))
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.s.recv(1)
            if not chunk:
                raise EOFError("closed during the upgrade")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise OSError(f"no websocket: {head[:80]!r}")
        self.buf, self.n = b"", 0

    def close(self) -> None:
        self.s.close()

    def _frame(self, op: int, data: bytes) -> bytes:
        n, mask = len(data), os.urandom(4)
        size = (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536
                else bytes([0x80 | 127]) + struct.pack(">Q", n))
        return bytes([0x80 | op]) + size + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data))

    def send(self, obj: dict) -> None:
        self.s.sendall(self._frame(0x1, json.dumps(obj).encode()))

    def _read(self, k: int) -> bytes:
        while len(self.buf) < k:
            chunk = self.s.recv(65536)
            if not chunk:
                raise EOFError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:k], self.buf[k:]
        return out

    def recv(self) -> dict | None:
        b1, b2 = self._read(2)
        n = b2 & 0x7F
        n = struct.unpack(">H", self._read(2))[0] if n == 126 else struct.unpack(">Q", self._read(8))[0] if n == 127 else n
        data = self._read(n)
        op = b1 & 0x0F
        if op == 0x9:  # ping
            self.s.sendall(self._frame(0xA, data))
            return None
        if op == 0x8:
            raise EOFError("closed by the daemon")
        return json.loads(data) if op == 0x1 else None

    def call(self, method: str, params: dict | None = None) -> dict:
        self.n += 1
        self.send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})
        while True:  # notifications may come in between
            m = self.recv()
            if m and m.get("id") == self.n:
                if "error" in m:
                    raise OSError(f"{method}: {m['error']}")
                return m.get("result") or {}


def alive(path: Path | None = None) -> bool:
    """Does the daemon answer on its control socket (the handshake the wake needs)?"""
    try:
        ws = _WS(path or socket_path())
    except (OSError, EOFError, ValueError):
        return False
    try:
        ws.call("initialize", {"clientInfo": {"name": "work-brahman", "version": "1"}})
        return True
    except (OSError, EOFError, ValueError, KeyError, struct.error):
        return False
    finally:
        ws.close()


def thread(thread_id: str, path: Path | None = None) -> dict | None:
    """The thread as the daemon has it (status, cwd, …), or None when the daemon can't be asked
    or doesn't know it."""
    try:
        ws = _WS(path or socket_path())
    except (OSError, EOFError, ValueError):
        return None
    try:
        ws.call("initialize", {"clientInfo": {"name": "work-brahman", "version": "1"}})
        ws.send({"jsonrpc": "2.0", "method": "initialized"})
        return ws.call("thread/read", {"threadId": thread_id}).get("thread")
    except (OSError, EOFError, ValueError, KeyError, struct.error):
        return None
    finally:
        ws.close()


def terminal_in(cwd: str, proc: Path = Path("/proc")) -> bool:
    """Is a Codex terminal running in this folder now: a process named codex, on a terminal (the
    daemon has none), whose working folder is cwd. Linux (/proc); elsewhere False, so nothing is
    sent."""
    if not cwd or not proc.is_dir():
        return False
    want = os.path.realpath(cwd)
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            if (d / "comm").read_text().strip() != "codex":
                continue
            tty = int((d / "stat").read_text().rsplit(")", 1)[1].split()[4])  # tty_nr: 0 = none
            if tty and os.path.realpath(os.readlink(d / "cwd")) == want:
                return True
        except (OSError, ValueError, IndexError):
            continue
    return False
