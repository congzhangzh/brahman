"""The buddy's setup in one place, for `buddy tui` and the workbench's Setup tab (the owner,
2026-11-12: "make an all-in-one tui focused on doctor and config, and keep doctor for batch
use"; the page "part of the workbench"). Both show and change the same things through here:

- doctor: `buddy doctor`'s rows and the peer checks (install.all_checks), ✅ / ⚠️ / ❌ and how
  to fix each; `buddy doctor [--json] [--peers]` stays the command for scripts and cron.
- config: what `buddy config` changes (configure.load / save), shown with secrets masked:
  no secret ever leaves here, the page and the TUI only send a new one or "-" to remove it.
- peers and the log: peer.py's (load, validate, save, ping, read_log).

A change to the config takes effect when the buddy starts again: written, it says so, and the
restart is the owner's (`systemctl --user restart buddy`); nothing here restarts it.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

from . import configure, install
from . import peer as P

LANGUAGES = ("zh-CN", "en")
FIELDS = [  # key, label, kind
    ("language", "language", "choice"), ("workspace", "workspace", "text"),
    ("telegram", "Telegram", "secret"), ("lark", "Lark", "app"), ("feishu", "Feishu", "app"),
    ("allowed", "paired people", "list"), ("chats", "linked chats", "list"), ("port", "web port", "int")]
RESTART = "the buddy reads this when it starts: restart it to use it (systemctl --user restart buddy)"


def mask(secret: str) -> str:
    return f"…{secret[-4:]}" if len(secret) > 6 else ("set" if secret else "")


def view(s: dict) -> list[dict]:
    """The settings as shown: no secret in it."""
    out = []
    for key, label, kind in FIELDS:
        v = s[key]
        if kind == "secret":
            shown = f"token {mask(v)}" if v else "not set"
        elif kind == "app":
            shown = f"{v[0]} (secret {mask(v[1])})" if v[0] else "not set"
            if key == "lark" and v[0] and s["feishu"][0]:
                shown += "  ids: lark:…"
        elif kind == "list":
            shown = ", ".join(v) or ("nobody yet" if key == "allowed" else "none")
        else:
            shown = str(v)
        out.append({"key": key, "label": label, "kind": kind, "shown": shown,
                    "value": v if kind in ("choice", "text", "int", "list") else (v[0] if kind == "app" else ""),
                    "set": bool(v[0] if kind == "app" else v)})
    return out


def change(s: dict, key: str, value) -> str:
    """Change one setting in s (in place): "" or what is wrong (s untouched then).
    secret: a new one, "" keeps it, "-" removes it. app: [app id, secret], each "" to keep,
    an id of "-" removes the app. list: the items (a string is split at commas)."""
    kinds = {k: kind for k, _, kind in FIELDS}
    if key not in kinds:
        return f"no setting {key}"
    kind, was = kinds[key], copy.deepcopy(s)
    if kind == "choice":
        if value not in LANGUAGES:
            return "the language is " + " or ".join(LANGUAGES)
        s[key] = value
    elif kind == "text":
        if not str(value).strip():
            return f"{key} can't be empty"
        s[key] = str(value).strip()
    elif kind == "int":
        try:
            port = int(value)
        except (TypeError, ValueError):
            return "the port is a number"
        if not 1 <= port <= 65535:
            return "the port is 1-65535"
        s[key] = port
    elif kind == "secret":
        v = str(value or "").strip()
        s[key] = "" if v == "-" else (v or s[key])
    elif kind == "app":
        app_id, secret = (list(value) + ["", ""])[:2] if isinstance(value, (list, tuple)) else (str(value), "")
        app_id, secret = str(app_id or "").strip(), str(secret or "").strip()
        if app_id == "-" or secret == "-":
            s[key] = ["", ""]
        else:
            s[key] = [app_id or s[key][0], secret or s[key][1]]
            if s[key][0] and not s[key][1]:
                s[key] = was[key]
                return f"{key}: the App Secret too"
        if key == "feishu":
            configure._moved(s, was)  # Lark moves between the first and second app with it
    elif kind == "list":
        items = value.split(",") if isinstance(value, str) else list(value or [])
        s[key] = list(dict.fromkeys(str(x).strip() for x in items if str(x).strip()))
    return ""


def write(path: Path, s: dict, before: dict) -> tuple[bool, list[str]]:
    """Write the changes with backups (configure.save). (written, notes)."""
    if s == before:
        return False, ["nothing changed"]
    if not (s["telegram"] or s["lark"][0] or s["feishu"][0]):
        return False, ["no chat left: keep at least one of Telegram, Lark, Feishu"]
    try:
        notes = configure.save(path, s, before)
    except ValueError as e:
        return False, [f"not written: {e}"]
    return True, ["written"] + notes + [RESTART]


ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "FEISHU_APP_ID", "FEISHU_APP_SECRET", "LARK_APP_ID", "LARK_APP_SECRET",
            "BUDDY_ALLOWED_IDS")


def reload_env(path: Path, env=None) -> None:
    """After a write, in `buddy tui` only: its doctor checks the new tokens, as the buddy will
    after its restart. Never in the running buddy (its doctor checks what it runs with)."""
    env = os.environ if env is None else env
    now = configure._env(path.parent / ".env")
    for k in ENV_KEYS:
        if now.get(k):
            env[k] = now[k]
        else:
            env.pop(k, None)


def doctor(path: Path, *, offline: bool = False, peers: bool = True, pinger=None) -> list[tuple]:
    return install.all_checks(path, offline=offline, peers=peers, pinger=pinger)


def peers(env=None) -> dict:
    env = os.environ if env is None else env
    """peers.toml as shown (no secret is in it), and whether the secret is set."""
    try:
        P.secret(env)
        has = True
    except ValueError:
        has = False
    return {"cfg": P.load(env), "secret": has, "path": str(P.config_path(env))}


def save_peers(cfg: dict, env=None) -> tuple[bool, str]:
    env = os.environ if env is None else env
    cfg = {"name": str(cfg.get("name", "")), "bind": str(cfg.get("bind") or "127.0.0.1"),
           "port": cfg.get("port") or 8790,
           "points": {str(k): {"url": str(v.get("url", "")), "note": str(v.get("note", ""))}
                      for k, v in (cfg.get("points") or {}).items() if isinstance(v, dict)}}
    try:
        return True, f"written: {P.save(cfg, env)}"
    except (ValueError, TypeError) as e:
        return False, str(e)


def check_peer(name: str, url: str = "", env=None, pinger=None) -> dict:
    """A signed ping to one peer, answered signed: the same secret both ways, the clock skew."""
    env = os.environ if env is None else env
    try:
        key = P.secret(env)
    except ValueError as e:
        return {"ok": False, "why": str(e)}
    cfg = P.load(env)
    url = url or cfg["points"].get(name, {}).get("url", "")
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "why": f"no address for {name or 'that peer'}"}
    r = (pinger or P.ping)(url, cfg["name"], key)
    P.record("check", env, peer=name, ok=r["ok"], why=r.get("why", ""), skew=r.get("skew"), ms=r.get("ms"))
    return r


def log(env=None, last: int = 200) -> list[str]:
    env = os.environ if env is None else env
    return [P.log_line(e) for e in P.read_log(env, last)]


def pair(path: Path, state: Path, sender: str) -> tuple[bool, list[str]]:
    """The quick pair: someone who wrote to the bot (pings.refused, never an id typed into the
    page) into paired people, and its chat into linked chats, as `buddy allow <id> --chat <chat>`
    does. (written, notes); the buddy uses it after a restart."""
    from . import pings
    if not path.exists():
        return False, [f"not set up: {path} is missing"]
    before = configure.load(path)
    who = next((r for r in pings.refused(state, before["allowed"]) if r["sender"] == sender), None)
    if who is None:
        return False, [f"{sender} hasn't written to the bot (or is paired already): only who wrote can be paired here"]
    s = copy.deepcopy(before)
    change(s, "allowed", s["allowed"] + [sender])
    if who["chat"] and who["chat"] not in s["chats"]:
        change(s, "chats", s["chats"] + [who["chat"]])
    ok, notes = write(path, s, before)
    if ok:
        pings.forget(state, sender)  # paired: no longer someone who wrote unpaired
    return ok, ([f"paired {sender} on {who['channel']}, its chat {who['chat']} linked"] + notes[1:]) if ok else notes


def restart_soon(name: str = "buddy", delay: float = 1.5) -> str:
    """Restart the buddy's service a moment from now, so the page that asked gets its answer
    first. --no-block: systemd takes the job and this process may end with the unit. Only the
    owner's own click (the workbench's owner check, or the TUI) calls it."""
    import subprocess
    import threading
    from . import install
    if install.WINDOWS or not install.service_file(name).exists():
        return f"restart the buddy by hand: {install.restart_hint(name)}"
    cmd = install.restart_hint(name).split()
    if cmd[:2] == ["systemctl", "--user"]:
        cmd.insert(2, "--no-block")
    threading.Timer(delay, lambda: subprocess.run(cmd, check=False)).start()
    return "restarting the buddy: back in about 10 s"

