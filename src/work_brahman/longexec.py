"""What a long story's live unit runs (long_live.py): `python -I longexec.py <folder>`.

It holds one agent session that speaks the stream-json protocol (`claude -p --input-format
stream-json --output-format stream-json`) for as long as the session lives:

- <folder>/spec.json: the command, its environment and folder; read, then deleted at once
  (the environment may carry tokens).
- <folder>/in: a FIFO. The launcher holds it open for reading AND writing, so no writer leaving
  (the buddy restarting) ever reads as the end of input; each line it gets goes to the
  session's stdin as it is. Windows has no FIFOs: there `in` is a plain file the launcher tails
  from its start (a short sleep when there is nothing new), and <folder>/ready (its pid) says it
  reads, which the buddy waits for before writing (long_live._put_file). A line {"type": "buddy_release"} closes that stdin instead: the
  session ends its running turn, then exits (tested 2026-11-03).
- The session's stdout and stderr are the unit's, which systemd appends to <folder>/out and
  <folder>/err; `buddy serve` reads out from where it stopped.
- <folder>/rc: the session's exit code when it ended (minus the signal that killed it).

Standalone on purpose (run with -I, no package imports), like unitexec.py.
"""

import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time


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

    path = os.path.join(folder, "in")
    for line in (fifo_lines(path) if stat.S_ISFIFO(os.stat(path).st_mode) else tail_lines(path, folder)):
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


def tail_lines(path: str, folder: str, every: float = 0.1):
    """The lines appended to a plain file, from its start, forever (Windows: no FIFOs)."""
    with open(path, "rb") as f:
        tmp = os.path.join(folder, "ready.tmp")
        with open(tmp, "w") as r:
            r.write(str(os.getpid()))
        os.replace(tmp, os.path.join(folder, "ready"))  # the buddy writes only after this
        part = b""
        while True:
            chunk = f.readline()
            if not chunk:
                time.sleep(every)
                continue
            part += chunk
            if part.endswith(b"\n"):  # a line written in two pieces waits for its end
                yield part
                part = b""


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
