"""What a long story's live unit runs (long_live.py): `python -I longexec.py <folder>`.

It holds one agent session that speaks the stream-json protocol (`claude -p --input-format
stream-json --output-format stream-json`) for as long as the session lives:

- <folder>/spec.json: the command, its environment and folder; read, then deleted at once
  (the environment may carry tokens).
- <folder>/in: a FIFO. The launcher holds it open for reading AND writing, so no writer leaving
  (the buddy restarting) ever reads as the end of input; each line it gets goes to the
  session's stdin as it is. Windows has no FIFOs: there the spec names a named pipe and its key
  (spec["pipe"]); the launcher listens on it with multiprocessing.connection, one message per
  connection, and a client without the key fails its HMAC challenge and is dropped. Nothing of
  what is said touches the disk (long_live._pipe). A line {"type": "buddy_release"} closes that stdin instead: the
  session ends its running turn, then exits (tested 2026-11-03).
- The session's stdout and stderr are the unit's, which systemd appends to <folder>/out and
  <folder>/err; `buddy serve` reads out from where it stopped.
- <folder>/rc: the session's exit code when it ended (minus the signal that killed it).

Standalone on purpose (run with -I, no package imports), like unitexec.py.
"""

import json
import os
import signal
import subprocess
import sys
import threading


def main(folder: str) -> int:
    spec_path = os.path.join(folder, "spec.json")
    with open(spec_path) as f:
        spec = json.load(f)
    os.unlink(spec_path)
    p = subprocess.Popen(spec["cmd"], cwd=spec.get("cwd") or None, env=spec.get("env"),
                         stdin=subprocess.PIPE)
    for s in (signal.SIGTERM, signal.SIGINT):  # a polite stop (■ STOP, the unit's stop) goes on
        signal.signal(s, lambda n, _f: p.send_signal(n))

    def ended():  # the session exits on its own or after a release: so does the launcher
        code = p.wait()
        tmp = os.path.join(folder, "rc.tmp")
        with open(tmp, "w") as f:
            f.write(str(code))
        os.replace(tmp, os.path.join(folder, "rc"))
        os._exit(code if code >= 0 else 128 - code)
    threading.Thread(target=ended, daemon=True).start()

    lines = pipe_lines(spec["pipe"]) if spec.get("pipe") else fifo_lines(os.path.join(folder, "in"))
    for line in lines:
        try:
            kind = json.loads(line).get("type")
        except (ValueError, AttributeError):
            continue  # only whole JSON lines go to the session
        try:
            if kind == "buddy_release":
                p.stdin.close()
                break
            p.stdin.write(line if line.endswith(b"\n") else line + b"\n")
            p.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):  # the session is gone already
            break
    threading.Event().wait()  # `ended` exits the process when the session does
    return 0


def fifo_lines(path: str):
    fifo = os.open(path, os.O_RDWR)  # read-write: never an EOF from a writer leaving
    with os.fdopen(fifo, "rb") as lines:
        yield from lines


HELLO = b"brahman-long-pipe 1"
AUTH_WAIT = 5.0  # seconds a connection has to prove the key; then it is dropped (a stalling opener)


def _proof(key: bytes, who: bytes, nonce: bytes) -> bytes:
    import hashlib
    import hmac
    return hmac.new(key, who + nonce, hashlib.sha256).digest()


def _serve_one(conn, key: bytes, out) -> None:
    """One connection: each side proves the key (the launcher too, so nobody posing as it gets a
    message), every read with a deadline, then one message, acknowledged. Anything else: dropped."""
    import hmac
    try:
        with conn:
            nonce = os.urandom(32)
            conn.send_bytes(HELLO + nonce)
            if not conn.poll(AUTH_WAIT):
                return
            got = conn.recv_bytes(256)
            if len(got) != 64 or not hmac.compare_digest(got[:32], _proof(key, b"client", nonce)):
                return
            conn.send_bytes(_proof(key, b"server", got[32:]))
            if not conn.poll(AUTH_WAIT):
                return
            out.put(conn.recv_bytes())
            conn.send_bytes(b"ok")
    except (EOFError, OSError):
        pass


def _listener(pipe: dict):
    """multiprocessing.connection's listener; on Windows one that refuses remote clients (SMB): its
    PipeListener sets no PIPE_REJECT_REMOTE_CLIENTS (CPython 3.15, connection.py _new_handle)."""
    from multiprocessing import connection
    if pipe["family"] != "AF_PIPE":
        return connection.Listener(pipe["address"], family=pipe["family"])
    import _winapi

    class Local(connection.PipeListener):
        def _new_handle(self, first=False):
            flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
            if first:
                flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
            return _winapi.CreateNamedPipe(
                self._address, flags,
                _winapi.PIPE_TYPE_MESSAGE | _winapi.PIPE_READMODE_MESSAGE | _winapi.PIPE_WAIT
                | getattr(_winapi, "PIPE_REJECT_REMOTE_CLIENTS", 0x8),
                _winapi.PIPE_UNLIMITED_INSTANCES, 8192, 8192, _winapi.NMPWAIT_WAIT_FOREVER, _winapi.NULL)
    return Local(pipe["address"])


def pipe_lines(pipe: dict):
    """Each message sent to the named pipe (Windows), forever. Every connection is checked on its own
    thread with a deadline (_serve_one), so one that opens the pipe and never answers (anyone may open
    it: its default DACL) never holds up the buddy's messages; the buddy sends the next only after the
    "ok" for the last, so they come in order. After a buddy_release the session's stdin is closed and
    nothing more is read, yet a message sent then is still acknowledged and lost, as a write to the
    FIFO after a release is. _listener's Windows class overrides CPython's private
    PipeListener._new_handle: tests/test_long_windows_inbox.py, run on a Windows VM, guards it whenever
    the buddy's Python moves (3.15 today)."""
    import queue
    key = bytes.fromhex(pipe["authkey"])
    got: "queue.Queue[bytes]" = queue.Queue()
    listener = _listener(pipe)

    def accept():
        while True:
            try:
                conn = listener.accept()
            except OSError:
                continue
            threading.Thread(target=_serve_one, args=(conn, key, got), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
    while True:
        yield got.get()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
