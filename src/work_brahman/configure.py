"""`buddy config`: a menu in the terminal that changes the setup `buddy init` wrote, so
nobody has to edit buddy.toml and .env by hand for the everyday settings: the language, the
workspace, the chats (Telegram, Lark, Feishu), who is paired, the web port.

    buddy config · ~/.config/work_brahman/buddy.toml
      1  language    zh-CN
      3  Telegram    ✅ token …a9F2
      ...
      s  save · q  quit

Nothing is written until `s`. Saving keeps the old files as dated backups, writes only the
lines it changes (comments, jobs, duties and tools stay as they are), reads the result back
to check it says what was meant, then runs doctor's checks and offers to restart the service.
A setting it can't find in a form it can change safely (a multi-line value) is left alone,
and it says so: that one is edited by hand.

Lark and Feishu: one Lark-type app is FEISHU_APP_ID on `domain`, its ids as they are; with
both, Feishu is FEISHU_APP_ID and Lark is LARK_APP_ID, whose ids read lark:... (feishu.py).
Adding or removing Feishu moves Lark between the two, and its paired ids and chats with it.
"""

import copy
import getpass
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from . import install

FIELDS = ["language", "workspace", "telegram", "lark", "feishu", "paired", "port"]


def _show(secret: str) -> str:
    return f"…{secret[-4:]}" if len(secret) > 6 else "set"


def load(path: Path) -> dict:
    """The settings, as the menu sees them (Lark and Feishu by platform, not by variable)."""
    cfg = tomllib.loads(path.read_text())
    env = _env(path.parent / ".env")
    first = (env.get("FEISHU_APP_ID", ""), env.get("FEISHU_APP_SECRET", ""))
    second = (env.get("LARK_APP_ID", ""), env.get("LARK_APP_SECRET", ""))
    on_feishu = cfg.get("domain", "feishu") == "feishu"
    return {"language": cfg.get("buddy", {}).get("language", "zh-CN"),
            "workspace": cfg.get("workspace", "~"),
            "telegram": env.get("TELEGRAM_BOT_TOKEN", ""),
            "lark": list(second if on_feishu else first),
            "feishu": list(first if on_feishu else ("", "")),
            "allowed": [x for x in env.get("BUDDY_ALLOWED_IDS", "").split(",") if x.strip()],
            "chats": list(cfg.get("me", {}).get("chats", [])),
            "port": cfg.get("web", {}).get("port", 8787)}


def _env(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines() if path.exists() else []:
        key, eq, value = line.strip().partition("=")
        if eq and key and not key.startswith("#"):
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _moved(s: dict, before: dict) -> None:
    """Lark moved between the first app (plain ids) and the second (lark: ids) when Feishu
    came or went: its paired ids and chats move with it."""
    was, now = bool(before["feishu"][0]), bool(s["feishu"][0])
    if was == now or not s["lark"][0]:
        return
    if now:  # Lark becomes the second app: its plain ids were Lark's, Feishu had none yet
        fix = lambda x: "lark:" + x if x[:3] in ("ou_", "oc_") else x  # noqa: E731
    else:  # Lark becomes the first app again: Feishu's plain ids go with Feishu
        fix = lambda x: x.removeprefix("lark:") if x.startswith("lark:") else (None if x[:3] in ("ou_", "oc_") else x)  # noqa: E731
    s["allowed"] = [y for y in map(fix, s["allowed"]) if y]
    s["chats"] = [y for y in map(fix, s["chats"]) if y]


def _toml_set(text: str, section: str | None, key: str, literal: str) -> str | None:
    """Set key = literal in [section] (None: the top, before any section), keeping the line's
    comment; insert it if missing. None when the key is there in a form this can't safely
    change (a value over several lines)."""
    lines = text.splitlines()
    starts = [i for i, x in enumerate(lines) if re.match(r"^\s*\[", x)]
    if section is None:
        lo, hi = 0, starts[0] if starts else len(lines)
    else:
        head = next((i for i in starts if re.match(rf"^\s*\[{re.escape(section)}\]\s*(#.*)?$", lines[i])), None)
        if head is None:
            return text.rstrip("\n") + f"\n\n[{section}]\n{key} = {literal}\n"
        lo, hi = head + 1, next((i for i in starts if i > head), len(lines))
    value = r'("[^"\\]*"|\d+|\[[^\]]*\])'
    hits = [i for i in range(lo, hi) if re.match(rf"^\s*{re.escape(key)}\s*=", lines[i])]
    if len(hits) > 1:
        return None
    if hits:
        m = re.match(rf"^(\s*{re.escape(key)}\s*=\s*){value}(\s*(#.*)?)$", lines[hits[0]])
        if not m:
            return None
        lines[hits[0]] = m[1] + literal + m[3]
    else:
        at = lo
        if section is None:  # after the comments at the very top
            while at < hi and (lines[at].startswith("#") or not lines[at].strip()):
                at += 1
        lines.insert(at, f"{key} = {literal}")
    return "\n".join(lines) + "\n"


def _env_set(text: str, values: dict) -> str:
    lines = text.splitlines()
    for key, value in values.items():
        k = next((i for i, x in enumerate(lines) if x.split("=", 1)[0].strip() == key), None)
        if k is None:
            if value:  # an empty key that isn't there stays out
                lines.append(f"{key}={value}")
        else:
            lines[k] = f"{key}={value}"
    return "\n".join(lines) + "\n"


def _q(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(path: Path, s: dict, before: dict) -> list[str]:
    """Write what changed; returns what was kept (backups) and what was left for the hand.
    Raises ValueError, writing nothing, when the result wouldn't read back as meant."""
    env_path = path.parent / ".env"
    toml, notes = path.read_text(), []
    on_feishu = bool(s["feishu"][0])
    first, second = (s["feishu"], s["lark"]) if on_feishu else (s["lark"], ["", ""])
    changes = [(None, "workspace", _q(s["workspace"])), (None, "domain", _q("feishu" if on_feishu else "lark")),
               ("buddy", "language", _q(s["language"])), ("web", "port", str(int(s["port"]))),
               ("me", "chats", "[" + ", ".join(_q(c) for c in s["chats"]) + "]")]
    old = tomllib.loads(toml)
    for section, key, literal in changes:
        now = (old.get(section, {}) if section else old).get(key)
        if now is not None and tomllib.loads(f"x = {literal}")["x"] == now:
            continue  # unchanged: the line is not touched
        new = _toml_set(toml, section, key, literal)
        if new is None:
            notes.append(f"left {key} as it is: it is written over several lines in {path}; edit it by hand")
            continue
        toml = new
    got = tomllib.loads(toml)  # read back: does it say what was meant?
    for section, key, literal in changes:
        mine = tomllib.loads(f"x = {literal}")["x"]
        value = (got.get(section, {}) if section else got).get(key)
        if value != mine and not any(key in n for n in notes):
            raise ValueError(f"{key} would read {value!r}, not {mine!r}: nothing written")
    env = _env_set(env_path.read_text() if env_path.exists() else "", {
        "TELEGRAM_BOT_TOKEN": s["telegram"], "FEISHU_APP_ID": first[0], "FEISHU_APP_SECRET": first[1],
        "LARK_APP_ID": second[0], "LARK_APP_SECRET": second[1], "BUDDY_ALLOWED_IDS": ",".join(s["allowed"])})
    if toml != path.read_text():
        notes.append(f"kept {install._keep(path)}")
        path.write_text(toml)
    if not env_path.exists() or env != env_path.read_text():
        if env_path.exists():
            notes.append(f"kept {install._keep(env_path)}")
        env_path.touch(mode=0o600)
        env_path.write_text(env)
        env_path.chmod(0o600)
    return notes


def _menu(path: Path, s: dict, before: dict) -> str:
    def mark(k):
        return "*" if s.get(k) != before.get(k) else " "
    tg = f"✅ token {_show(s['telegram'])}" if s["telegram"] else "⚠️  not set"
    app = lambda a: f"✅ {a[0]} (secret {_show(a[1])})" if a[0] else "⚠️  not set"  # noqa: E731
    lark = app(s["lark"]) + ("  ids: lark:…" if s["lark"][0] and s["feishu"][0] else "")
    rows = [("1", "language", "language", s["language"]), ("2", "workspace", "workspace", s["workspace"]),
            ("3", "Telegram", "telegram", tg), ("4", "Lark", "lark", lark), ("5", "Feishu", "feishu", app(s["feishu"])),
            ("6", "paired", "allowed", ", ".join(s["allowed"]) or "⚠️  nobody yet"), ("7", "web port", "port", s["port"])]
    out = [f"buddy config · {path}"]
    out += [f" {mark(k)}{n}  {label:<10} {value}" for n, label, k, value in rows]
    out.append("   s  save · q  quit" + ("   (* changed, not saved yet)" if s != before else ""))
    return "\n".join(out)


def _ask(prompt: str, default: str = "") -> str:
    return install._ask(prompt, default)


def _secret(prompt: str) -> str:
    return getpass.getpass(f"{prompt} (empty: keep, - : remove): ").strip()


def _app(s: dict, which: str, label: str) -> None:
    if not s[which][0]:  # a new one: say where it comes from (an existing one needs no page)
        print(f"get one: {install.GET[which]}")
    app_id = _ask(f"{label} App ID (empty: keep, - : remove)", "").strip()
    if app_id == "-":
        s[which] = ["", ""]
        return
    if app_id:
        s[which] = [app_id, s[which][1]]
    secret = _secret(f"{label} App Secret")
    if secret == "-":
        s[which] = ["", ""]
    elif secret:
        s[which] = [s[which][0], secret]


def _paired(s: dict) -> None:
    while True:
        print("people:", "  ".join(f"{k + 1}) {x}" for k, x in enumerate(s["allowed"])) or "nobody")
        print("chats: ", "  ".join(f"c{k + 1}) {x}" for k, x in enumerate(s["chats"])) or "none")
        got = _ask("a <id> [<chat>] add · r <n> / r c<n> remove · enter: back").split()
        if not got:
            return
        if got[0] == "a" and len(got) >= 2:
            s["allowed"] += [got[1]] if got[1] not in s["allowed"] else []
            chat = got[2] if len(got) > 2 else (got[1] if got[1].startswith("tg:") else "")
            s["chats"] += [chat] if chat and chat not in s["chats"] else []
        elif got[0] == "r" and len(got) == 2:
            which, n = ("chats", got[1][1:]) if got[1].startswith("c") else ("allowed", got[1])
            if n.isdigit() and 0 < int(n) <= len(s[which]):
                s[which].pop(int(n) - 1)


def config(args) -> int:
    path = install.config_path(args.config)
    if not path.exists():
        print(f"not set up: {path} is missing; run `buddy init` first", file=sys.stderr)
        return 2
    s = load(path)
    before = copy.deepcopy(s)
    while True:
        print(_menu(path, s, before))
        c = _ask("choose").strip().lower()
        was = copy.deepcopy(s)
        if c == "1":
            s["language"] = _ask("language, zh-CN or en", s["language"])
            if s["language"] not in ("zh-CN", "en"):
                print("zh-CN or en")
                s["language"] = was["language"]
        elif c == "2":
            s["workspace"] = _ask("workspace, where chat turns run", s["workspace"])
        elif c == "3":
            if not s["telegram"]:
                print(f"get one: {install.GET['telegram']}")
            token = _secret("Telegram bot token, from @BotFather")
            s["telegram"] = "" if token == "-" else token or s["telegram"]
        elif c == "4":
            _app(s, "lark", "Lark (international)")
        elif c == "5":
            _app(s, "feishu", "Feishu (China)")
            _moved(s, was)
        elif c == "6":
            _paired(s)
        elif c == "7":
            port = _ask("web port", str(s["port"]))
            s["port"] = int(port) if port.isdigit() else s["port"]
        elif c == "s":
            if s == before:
                print("nothing changed")
                return 0
            if not (s["telegram"] or s["lark"][0] or s["feishu"][0]):
                print("no chat left: keep at least one of Telegram, Lark, Feishu")
                continue
            try:
                notes = save(path, s, before)
            except ValueError as e:
                print(f"not saved: {e}")
                continue
            print("\n".join(["saved"] + notes))
            rows = install.checks(path, offline=args.offline)
            print("\n".join(install.line(*r) for r in rows if r[0] is not True) or "✅ every check ok")
            if install.WINDOWS:
                print("restart the buddy to use this: close its window, then run "
                      f"{install.STARTUP / (args.name + '.cmd')}")
                return 0
            unit = install.service_file(args.name)
            if unit.exists() and _ask(f"restart the buddy now, so it uses this? [y/N]").lower() == "y":
                subprocess.run(install.restart_hint(args.name).split(), check=False)
                print(f"restarted: {install.restart_hint(args.name)}")
            return 0
        elif c == "q":
            if s == before or _ask("quit without saving? [y/N]").lower() == "y":
                return 0
