"""What a buddy unit runs (procs.py): `python -m work_brahman.unitexec <folder>`.

It reads <folder>/spec.json (the command, its environment and folder), deletes it at once, since
the environment may carry tokens, runs the command and writes <folder>/rc: its exit code, or
minus the signal that killed it. Its stdin, stdout and stderr are the unit's, which systemd
already points at files in the same folder. The unit's stop (■ STOP, a timeout) kills the whole
cgroup, this launcher included; then no rc is written and the buddy reads that as "killed".
"""

import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def main(folder: str) -> int:
    d = Path(folder)
    spec = json.loads((d / "spec.json").read_text())
    (d / "spec.json").unlink()
    p = subprocess.Popen(spec["cmd"], cwd=spec.get("cwd") or None, env=spec.get("env"))
    for s in (signal.SIGTERM, signal.SIGINT):  # pass a polite stop on, then report it
        signal.signal(s, lambda n, _f: p.send_signal(n))
    code = p.wait()
    tmp = d / "rc.tmp"
    tmp.write_text(str(code))
    os.replace(tmp, d / "rc")
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
