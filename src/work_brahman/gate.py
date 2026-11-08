"""The gate: the one point a session's work comes in through, checked by plain code, never by a
model (specs/multi-brahman.md, "A call runs as a session behind a gate"). A model can be talked
into anything, so it can't guard its own door; the rule is capability, not permission: what a
session must not do, it should not be able to do.

Version 1 runs on one machine, for the sessions the buddy starts itself (/newlong, and its
headless turns after that):

- **In:** `check_new` checks a start before anything is written: the folder is never the home
  folder or /, the task is text of a sane size, and a short story it works for (`12:test`) is
  a step a child story may take (short_stories' own rules).
- **Inside:** `session_env` gives the session an environment built from an allowlist, not the
  buddy's own minus a few names. The buddy runs inside the owner's desktop session, so its
  environment holds the ssh agent (SSH_AUTH_SOCK: `git push` over ssh), the gpg agent and the
  D-Bus address (the desktop keyring), besides the chat apps' tokens; none of them reach the
  session. `BUDDY_SHORT` tells the session which short story it works on.
- **Audit:** every decision is a line in `<state>/gate.jsonl`: what was asked, the verdict, and
  the *names* of the variables kept out (never a value).

Version 2 closes what an environment can't: credentials kept in files. Where bubblewrap is
installed (Linux), `sandbox` runs the session in its own view of the files (`bwrap_args`): the
system read-only; the folders that hold credentials (~/.ssh, gh's, the keyring's, ...) and
/run/user/<uid> (the agents' sockets) replaced by empty ones; the buddy's own .env replaced by
an empty file; only its folder, the engines' own folders and the buddy's short stories
writable. It holds no chat token, so `buddy short` there (BUDDY_GATE=1) writes the story file
only, and `buddy serve` draws the card (short_stories.redraw). Without bwrap (macOS, Windows)
a session runs as in version 1, and the audit line says `sandbox: none`.

Open, by the owner's choice (2026-11-01): the sandbox fits work; for his own use the owner may
start a session open, like one in a terminal: his ssh agent, gh, the keyring and every folder.
Only a human picks it, at the start (`/newlong --open`, or 🔓 on the /newlong card), so it comes in
through the chat's allowlist; the session can't pick it for itself, and its later turns keep the
start's choice. Open still keeps the buddy's own keys away (the chat tokens in its environment
and its .env), since they are the buddy's, not the owner's. `[gate] open = false` (a company's
instance) takes the choice away; the audit line says which one ran, and who chose it.

Not closed yet: the network is open (bwrap can only cut it all, and the engines need theirs),
and "out" waits for the first peer: today a session's words go only to the owner's own chats.
"""

import json
import os
import time
from pathlib import Path

from .text import t

# Kept by name, or by prefix: what a shell, a build and the engines themselves need. The
# engines' own sign-in (ANTHROPIC_*, CLAUDE_*, OPENAI_*, CODEX_*) is kept, since a session
# can't run without it; everything else the buddy holds stays with the buddy.
KEEP = {"HOME", "USER", "LOGNAME", "SHELL", "PATH", "LANG", "LANGUAGE", "TERM", "TZ", "TMPDIR",
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_DATA_DIRS",
        "XDG_CONFIG_DIRS", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy",
        "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "NO_COLOR"}
KEEP_PREFIX = ("LC_", "ANTHROPIC_", "CLAUDE_", "OPENAI_", "CODEX_", "UV_")
QUIET = {"SPEAK": "off"}  # a headless session has nobody at a speaker
TASK_MAX = 8000  # characters: a task is a message, not a file


def session_env(extra: dict | None = None, base: dict | None = None) -> dict:
    """The environment of a session the buddy starts: the allowlist out of base (default: the
    buddy's own), then SPEAK=off and extra (BUDDY_SHORT)."""
    base = os.environ if base is None else base
    kept = {k: v for k, v in base.items() if k in KEEP or k.startswith(KEEP_PREFIX)}
    return {**kept, **QUIET, **(extra or {})}


def dropped(base: dict | None = None) -> list[str]:
    """The names session_env keeps out, for the audit line: names only, never values."""
    base = os.environ if base is None else base
    return sorted(k for k in base if k not in KEEP and not k.startswith(KEEP_PREFIX))


def check_new(state: Path, task: str, cwd: str, short: str = "", sandbox: str = "", who: str = "") -> None:
    """A session's start, checked before anything is written; ValueError says why. Logged."""
    why = ""
    if os.path.realpath(os.path.expanduser(cwd)) in (os.path.realpath(Path.home()), "/"):
        why = t("long.not_home", cwd=cwd)
    elif not task.strip() or "\0" in task:
        why = t("gate.no_task")
    elif len(task) > TASK_MAX:
        why = t("gate.too_long", n=len(task), max=TASK_MAX)
    elif short:
        # the child story's rules (one level, one child per step, ...): imported late, it imports text too
        from . import short_stories as shorts
        try:
            shorts._parent_ref(state, short)
        except ValueError as e:
            why = str(e)
    log(state, "long.new", "refused" if why else "ok", why=why, cwd=cwd, short=short, sandbox=sandbox,
        who=who, task=task[:120], dropped=dropped() if not sandbox.startswith("open") else [])
    if why:
        raise ValueError(why)


def log(state: Path, kind: str, verdict: str, **fields) -> None:
    line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind, "verdict": verdict,
            **{k: v for k, v in fields.items() if v not in ("", None, [])}}
    with open(Path(state) / "gate.jsonl", "a") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


# Version 2: the session in a bubblewrap sandbox (bwrap, Linux). Its own view of the files: the
# whole system read-only, the folders that hold credentials replaced by empty ones, its project
# folder and the engine's own folders writable, and only the short stories of the buddy's state.
# It can't read a key it can't see, so even a key without a passphrase can't push.
HIDE_DIRS = (".ssh", ".gnupg", ".config/gh", ".docker", ".local/share/keyrings", ".aws", ".kube",
             ".password-store", ".config/hub", ".config/glab-cli")
HIDE_FILES = (".git-credentials", ".netrc", ".pypirc", ".npmrc")
WRITE = (".claude", ".claude.json", ".codex", ".cache")  # the engines' own state, and caches


def sandbox(cfg: dict | None = None, *, env_file: str | Path | None = None, state: Path | None = None):
    """A function (cmd, cwd, env) -> (cmd, env) that runs cmd in bubblewrap, or None when the
    sandbox is off ([gate] sandbox = "none") or bwrap is missing (macOS, Windows): then a session
    runs as in version 1, and the audit line says so. env_file: the buddy's own .env, hidden."""
    import shutil
    g = (cfg or {}).get("gate", {})
    if g.get("sandbox", "bwrap") != "bwrap" or not shutil.which("bwrap"):
        return None
    hide = list(g.get("hide", []))
    write = list(g.get("write", []))
    st = Path(state) if state else Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "work_brahman"

    def wrap(cmd: list[str], cwd: str, env: dict) -> tuple[list[str], dict]:
        return bwrap_args(cwd, home=Path.home(), state=st, env_file=env_file, hide=hide, write=write) + cmd, \
            {**env, "BUDDY_GATE": "1"}
    return wrap


def bwrap_args(cwd: str, *, home: Path, state: Path, env_file=None, hide=(), write=()) -> list[str]:
    """The bwrap command line for a session in cwd. Order matters, since a later mount lies over
    an earlier one: the system read-only, then what may be written, then what is hidden, last,
    so a .env inside a writable folder is still hidden. Only paths that exist are named."""
    home, state = Path(home), Path(state)
    a = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    stories = state / "short_stories"
    stories.mkdir(parents=True, exist_ok=True)
    if state.is_dir():  # the buddy's state, but for its short stories: the session marks its card
        a += ["--tmpfs", str(state), "--bind", str(stories), str(stories)]
    for p in [home / x for x in WRITE] + [Path(os.path.expanduser(x)) for x in write] + [Path(cwd)]:
        if p.exists():
            a += ["--bind", str(p), str(p)]
    run = Path(f"/run/user/{os.getuid()}")  # the agents' sockets: ssh, gpg, the keyring, D-Bus
    if run.is_dir():
        a += ["--tmpfs", str(run)]
    empty = state / ".gate-empty"
    empty.touch(exist_ok=True)
    files = [home / x for x in HIDE_FILES] + ([Path(env_file)] if env_file else [])
    for p in [home / x for x in HIDE_DIRS] + [Path(os.path.expanduser(x)) for x in hide] + files:
        if p.is_dir():
            a += ["--tmpfs", str(p)]
        elif p.exists():  # a file: an empty one over it (a /dev/null bind would be nodev: EACCES)
            a += ["--ro-bind", str(empty), str(p)]
    return a + ["--chdir", cwd, "--unshare-pid", "--unshare-ipc", "--die-with-parent", "--new-session"]


# Open: the owner's own environment and files, as in a terminal, but never the buddy's own keys.
def open_allowed(cfg: dict | None = None) -> bool:
    """Whether this instance offers open sessions at all ([gate] open, default true)."""
    return bool((cfg or {}).get("gate", {}).get("open", True))


def env_names(env_file) -> set[str]:
    """The variable names in the buddy's .env (names only: the values are never read here)."""
    try:
        lines = Path(env_file).read_text().splitlines() if env_file else []
    except OSError:
        return set()
    return {ln.split("=", 1)[0].removeprefix("export ").strip() for ln in lines
            if "=" in ln and not ln.lstrip().startswith("#")}


def open_env(extra: dict | None = None, *, env_file=None, base: dict | None = None) -> dict:
    """An open session's environment: the buddy's own (the ssh agent, the keyring, ...) minus
    the buddy's keys: the chat apps' variables and every name in its .env."""
    from .engine import SECRETS  # engine imports this module late, so no cycle
    base = os.environ if base is None else base
    gone = set(SECRETS) | env_names(env_file)
    return {**{k: v for k, v in base.items() if k not in gone}, **QUIET, **(extra or {})}


def open_wrap(*, env_file=None, state: Path | None = None):
    """For an open session: bwrap with the whole system as it is (writable, the agents' sockets
    there), and only the buddy's .env replaced by an empty file. None without bwrap: then the
    .env stays readable, and the audit line says `open-nobwrap`."""
    import shutil
    if not shutil.which("bwrap") or not env_file or not Path(env_file).exists():
        return None
    st = Path(state) if state else Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "work_brahman"
    st.mkdir(parents=True, exist_ok=True)
    empty = st / ".gate-empty"
    empty.touch(exist_ok=True)

    def wrap(cmd: list[str], cwd: str, env: dict) -> tuple[list[str], dict]:
        a = ["bwrap", "--dev-bind", "/", "/", "--ro-bind", str(empty), str(env_file),
             "--chdir", cwd, "--die-with-parent", "--new-session"]
        return a + cmd, {**env, "BUDDY_GATE": "1"}  # no chat token: `buddy short` writes the file only
    return wrap
