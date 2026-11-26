"""The quick start's fix step on the server (specs/quick-start.v1.md, the owner's choice A, 2026-11-24):
`buddy setup --remote <verb> [<space>]`, one fixed script, never a shell.

    buddy setup --remote check [SPACE]      each space: its units, port, state and the one fix line
    buddy setup --remote install [SPACE]    what is missing, from this checkout's own installers
    buddy setup --remote start [SPACE]      `systemctl --user enable --now` for each unit not running
    buddy setup --remote restart SPACE      `systemctl --user restart` (one space, named: never all)
    buddy setup --remote line               the key line it is meant for, to copy (never added here)

The spaces are the shell's (shell/web/spaces.js SPACE_INFO; test_remote_setup keeps the two equal):
work (the workbench), personal (the assistant), desktop (Remote Desktop), terminal (the Terminal and
its login front), browser (the Browser's proxy). SPACE defaults to all of them, except for restart.

Under a forced command (`restrict,command="<abs>/buddy setup --remote" <key>`) sshd puts what the
client asked for in $SSH_ORIGINAL_COMMAND: it is split on white space and must be exactly a verb and
at most one space (an optional leading `buddy setup --remote` is allowed); anything else is refused
before any command runs, so `;`, `$()`, backticks, quotes, redirections and options never reach one.
Every command here is a fixed argument list (no shell). It changes only the owner's own user (the
unit files, `systemctl --user`, the config folder): no sudo, and the model is never called. Each verb
is idempotent and prints one JSON object (UTF-8): {"verb", "space", "ok", "rows": [...]}; exit 0
when every row is ok, 1 when one is not, 2 when refused. No key, path or host is printed or logged.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

# The shell's SPACE_INFO, its units and default ports (test_remote_setup reads spaces.js to compare)
SPACES = {
    "work": {"title": "the workbench", "units": ["buddy.service"], "port": 8787},
    "personal": {"title": "the assistant", "units": ["assistant-web.service", "assistant-run.service"], "port": 8791},
    "desktop": {"title": "Remote Desktop", "units": ["rdp-wasm.service"], "port": 3397},
    "terminal": {"title": "the Terminal", "units": ["brahman-terminal.service"], "port": 7681},
    "browser": {"title": "the Browser's proxy", "units": ["brahman-socks.service"], "port": 1080},
}
VERBS = ("check", "install", "start", "restart", "line")
PREFIX = ("buddy", "setup", "--remote")
REPO = Path(__file__).resolve().parents[3]  # brahman/src/work_brahman/remote_setup.py → the checkout


class Refused(ValueError):
    """A request that is not one verb and at most one space: nothing runs."""


def parse(words: list[str]) -> tuple[str, str]:
    """(verb, space or "") from the words asked for; Refused for anything else."""
    if tuple(words[:3]) == PREFIX:
        words = words[3:]
    if not words:
        raise Refused(f"no verb: one of {', '.join(VERBS)}")
    if len(words) > 2:
        raise Refused("too many words: a verb and at most one space")
    verb, space = words[0], (words[1] if len(words) == 2 else "")
    if verb not in VERBS:
        raise Refused(f"unknown verb: one of {', '.join(VERBS)}")
    if space and space not in SPACES:
        raise Refused(f"unknown space: one of {', '.join(SPACES)}")
    if verb == "restart" and not space:
        raise Refused(f"restart needs one space: {', '.join(SPACES)}")
    if verb == "line" and space:
        raise Refused("line takes no space")
    return verb, space


def request(argv: list[str], env) -> tuple[str, str]:
    """The request: $SSH_ORIGINAL_COMMAND under a forced command, else the arguments. Under a forced
    command that names a verb itself (a key pinned to `check`), the client may only ask for that."""
    pinned = parse(argv) if argv else None
    if "SSH_ORIGINAL_COMMAND" in env:
        asked = env["SSH_ORIGINAL_COMMAND"].split()
        if not asked and pinned:
            return pinned
        got = parse(asked)
        if pinned and got != pinned:
            raise Refused("this key is pinned to another request")
        return got
    if pinned is None:
        raise Refused(f"no verb: one of {', '.join(VERBS)}")
    return pinned


def _run(cmd: list[str], timeout: float = 60) -> tuple[int, str]:
    """A fixed argument list, no shell; (exit code, its output, UTF-8). Tests replace it."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False,
                           encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, type(e).__name__


def _listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


class Server:
    """What one request looks at and does; run and listening are swapped in tests."""

    def __init__(self, cfg: dict | None = None, run=_run, listening=_listening, home: Path | None = None,
                 repo: Path = REPO):
        self.run, self.listening, self.repo = run, listening, repo
        self.home = home or Path.home()
        self.ports = {name: s["port"] for name, s in SPACES.items()}
        port = ((cfg or {}).get("web") or {}).get("port")
        if isinstance(port, int):
            self.ports["work"] = port  # the buddy's own [web] port

    # -- looking ---------------------------------------------------------------------------------
    def unit(self, name: str) -> dict:
        code, out = self.run(["systemctl", "--user", "show", name, "-p", "LoadState", "-p", "ActiveState",
                              "-p", "UnitFileState"], 15)
        if code != 0:
            return {"unit": name, "installed": False, "active": "unknown", "enabled": "unknown", "systemd": False}
        props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
        return {"unit": name, "installed": props.get("LoadState") == "loaded",
                "active": props.get("ActiveState", "unknown"), "enabled": props.get("UnitFileState", "") or "unknown",
                "systemd": True}

    def check(self, space: str) -> dict:
        s = SPACES[space]
        units = [self.unit(u) for u in s["units"]]
        port = self.ports[space]
        up = self.listening(port)
        if not all(u["systemd"] for u in units):
            fix = "no systemd user manager answers here: start the space's program by hand"
        elif not all(u["installed"] for u in units):
            fix = ("installed by the RDP kit, not by the buddy: its own install" if space == "desktop"
                   else f"buddy setup --remote install {space}")
        elif not all(u["active"] == "active" for u in units):
            fix = f"buddy setup --remote start {space}"
        elif not up:
            fix = f"buddy setup --remote restart {space}"
        else:
            fix = ""
        return {"space": space, "title": s["title"], "units": units, "port": port, "listening": up,
                "ok": not fix, "fix": fix}

    # -- doing -------------------------------------------------------------------------------------
    def _installers(self, space: str) -> list[list[str]] | None:
        """The checkout's own installer for a space (None: not here, or not the buddy's to install)."""
        if space == "work":
            return [[sys.executable, "-m", "work_brahman", "service", "install"]]
        if space == "personal":
            script = self.repo / "assistant" / "deploy" / "install-units.sh"
            return [["bash", str(script)]] if script.is_file() else None
        if space in ("terminal", "browser"):
            name = "brahman-terminal" if space == "terminal" else "brahman-socks"
            script = self.repo / "shell" / ("terminal" if space == "terminal" else "browser") / name
            return [["bash", str(script), "install"]] if script.is_file() else None
        return None  # desktop: the RDP kit's

    def _login(self, space: str) -> list[str] | None:
        """The space's login file, made once (`<name> setup` is idempotent): the Terminal and the proxy."""
        if space not in ("terminal", "browser"):
            return None
        name = "brahman-terminal" if space == "terminal" else "brahman-socks"
        script = self.home / ".local" / "share" / name / name
        return ["bash", str(script), "setup"] if script.is_file() else None

    def install(self, space: str) -> dict:
        before = self.check(space)
        did, ok = [], True
        if not all(u["installed"] for u in before["units"]):
            steps = self._installers(space)
            if steps is None:
                return before | {"did": [], "ok": False, "fix": (
                    "installed by the RDP kit, not by the buddy: its own install" if space == "desktop"
                    else "its installer isn't in this checkout: install it where the project is")}
            for cmd in steps:
                code, _ = self.run(cmd, 120)
                did.append({"step": "install", "ok": code == 0})
                ok = ok and code == 0
            if ok:
                self.run(["systemctl", "--user", "daemon-reload"], 30)
        if ok and (login := self._login(space)):
            code, _ = self.run(login, 30)
            did.append({"step": "login file", "ok": code == 0})
            ok = code == 0
        after = self.check(space)
        installed = all(u["installed"] for u in after["units"])
        return after | {"did": did or [{"step": "nothing to install", "ok": True}],
                        "ok": ok and installed,
                        "fix": after["fix"] if installed or not ok else f"buddy setup --remote install {space}"}

    def start(self, space: str) -> dict:
        before = self.check(space)
        did, ok = [], True
        for u in before["units"]:
            if not u["installed"]:
                ok = False
                continue
            if u["active"] != "active":
                code, _ = self.run(["systemctl", "--user", "enable", "--now", u["unit"]], 60)
                did.append({"step": f"enable --now {u['unit']}", "ok": code == 0})
                ok = ok and code == 0
        after = self.check(space)
        return after | {"did": did or [{"step": "already running" if ok else "not installed", "ok": ok}],
                        "ok": ok and all(u["active"] == "active" for u in after["units"])}

    def restart(self, space: str) -> dict:
        before = self.check(space)
        missing = [u["unit"] for u in before["units"] if not u["installed"]]
        if missing:
            return before | {"did": [], "ok": False}
        code, _ = self.run(["systemctl", "--user", "restart", *SPACES[space]["units"]], 90)
        after = self.check(space)
        return after | {"did": [{"step": "restart", "ok": code == 0}],
                        "ok": code == 0 and all(u["active"] == "active" for u in after["units"])}


def line(env) -> str:
    """The key line's options, for `<options> <the device's public key>`: the buddy command found on
    PATH (its wrapper, ~/.local/bin/buddy), by its absolute path."""
    venv = Path(sys.prefix).resolve()
    outside = os.pathsep.join(d for d in (env.get("PATH") or "").split(os.pathsep)  # not the venv's own entry
                              if d and not Path(d).resolve().is_relative_to(venv))  # point: `uv run` puts it first
    found = shutil.which("buddy", path=outside)
    cmd = str(Path(found).absolute()) if found else str(REPO / "brahman" / "scripts" / "buddy")
    return f'restrict,command="{cmd} setup --remote"'


def _out(doc: dict) -> None:
    sys.stdout.buffer.write((json.dumps(doc, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.flush()


def main(argv: list[str], env=os.environ, cfg: dict | None = None, server: Server | None = None) -> int:
    try:
        verb, space = request(argv, env)
    except Refused as e:
        _out({"verb": None, "space": None, "ok": False, "refused": str(e), "rows": []})
        return 2
    if verb == "line":
        sys.stdout.buffer.write((line(env) + "\n").encode("utf-8"))
        return 0
    srv = server or Server(cfg)
    rows = [getattr(srv, verb)(name) for name in ([space] if space else list(SPACES))]
    ok = all(r["ok"] for r in rows)
    _out({"verb": verb, "space": space or None, "ok": ok, "rows": rows})
    return 0 if ok else 1


def doctor_rows(cfg: dict | None = None, server: Server | None = None) -> list[tuple]:
    """For buddy doctor: one row per space, ⚠️ when it isn't running (a stopped space never stops the
    buddy, so never ❌), with its fix; the JSON form carries the whole check."""
    srv = server or Server(cfg)
    rows = []
    for name in SPACES:
        c = srv.check(name)
        state = ("running, port %d" % c["port"]) if c["ok"] else ", ".join(
            f"{u['unit']} {u['active'] if u['installed'] else 'not installed'}" for u in c["units"]) + (
            "" if c["listening"] else f", port {c['port']} closed")
        rows.append((True if c["ok"] else None, f"space · {c['title']} ({name}): {state}", c["fix"], c))
    return rows
