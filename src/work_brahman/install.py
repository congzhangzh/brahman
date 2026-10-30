"""Setting the buddy up on a new machine, for someone who installed it as an app
(`uv tool install work-brahman`) rather than working in its repository:

    buddy init                 ask for the language, the chats (Telegram, Lark, Feishu), the workspace
    buddy allow tg:123456789   the id the bot told you when it refused you: you are paired
    buddy doctor               what works, what is missing, and how to fix it
    buddy service install      a systemd user unit that keeps `buddy serve` running (Linux)
    buddy                      on its own: where things stand (problems, stories, the page)

The config lives in the user's config dir, $XDG_CONFIG_HOME/work_brahman/ (buddy.toml, and
.env for the secrets, chmod 600). A buddy.toml in the current folder still wins, so a checkout
that has one (the live buddy) runs as it always did. Nothing here writes over a file: an
existing one is kept as a dated backup with --force, or the command refuses and says where
it is.
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "work_brahman"
HERE = Path(__file__).resolve().parent
START = """start here, on a new machine:
  buddy init                      language, workspace, your chats: the token from @BotFather
                                  (Telegram), a Lark and/or a Feishu app's ID and secret
  buddy service install --enable  keep it running (Linux; elsewhere: buddy serve)
  write to your bot               it answers "Not paired" with the line to run:
  buddy allow tg:123456789        then: systemctl --user restart buddy.service
  buddy doctor                    what works, what is off, and how to turn it on

every day:
  buddy                           where things stand: problems, stories, the page
  buddy config                    change the language, chats, tokens, pairing, port
  buddy <command> --help          a command's options; in the chat: /help"""
UNITS = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser() / "systemd" / "user"
# Windows: a .cmd in the user's Startup folder starts the buddy at logon, no admin needed.
STARTUP = Path(os.environ.get("APPDATA", "~")).expanduser() / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
WINDOWS = os.name == "nt"
MACOS = sys.platform == "darwin"
# macOS: a launchd agent, started at login and again if it stops (KeepAlive).
AGENTS = Path("~/Library/LaunchAgents").expanduser()


def label(name: str) -> str:
    return f"com.work-brahman.{name}"


def service_file(name: str) -> Path:
    """Where `buddy service install` puts the service, on this system."""
    if WINDOWS:
        return STARTUP / f"{name}.cmd"
    if MACOS:
        return AGENTS / f"{label(name)}.plist"
    return UNITS / f"{name}.service"


def restart_hint(name: str) -> str:
    if WINDOWS:
        return "close its window and run it again from the Startup folder"
    if MACOS:
        return f"launchctl kickstart -k gui/{os.getuid()}/{label(name)}"
    return f"systemctl --user restart {name}.service"

# What each part needs; a part whose tool is missing stays off, and doctor says so.
TOOLS = [
    ("claude", True, "chat turns, jobs and duties", "https://docs.claude.com/en/docs/claude-code (then: claude, to log in)"),
    ("git", True, "the agent's work in your repositories", "your package manager"),
    ("gh", False, "the code-review and threads duties (GitHub)", "https://cli.github.com, then: gh auth login"),
    ("screen", False, "long stories you type into from the phone (Claude Code)", "your package manager"),
    ("codex", False, "long stories run by Codex", "npm install -g @openai/codex"),
]

TOML = """\
# Written by `buddy init` on {date}. Every setting: buddy.example.toml in the repository.
# Secrets never go here: they are in .env, next to this file.

workspace = "{workspace}"   # where chat turns run
domain = "{domain}"         # FEISHU_APP_ID's platform: "lark" (open.larksuite.com) or "feishu" (open.feishu.cn)

[buddy]
language = "{language}"     # "zh-CN" or "en": what the buddy says in the chats and on the page

[claude]
# Headless Claude Code cannot ask for approval: anything not listed is denied, and the
# reply says which tool was blocked. It starts read-only; add what you trust.
permission_mode = "default"
allowed_tools = [
  "Read", "Grep", "Glob", "Skill",
  "Bash(git log:*)", "Bash(git diff:*)", "Bash(git show:*)", "Bash(git status:*)",
  "Bash(gh pr list:*)", "Bash(gh pr view:*)", "Bash(gh pr diff:*)", "Bash(gh pr checks:*)",
]
timeout = 1800
append_system_prompt = \"\"\"Your reply is shown in a chat app on a phone. Markdown renders:
bold, lists, links, code blocks, ### headings and tables. The screen is narrow:
keep tables to 3 or 4 short columns and lead with the answer.
Do not run text-to-speech or other audio commands: audio would play on the
server, not on the reader's phone.\"\"\"

# Your chats: posts go to every one, and what you do in one shows in the others.
# `buddy allow <id>` adds yours.
[me]
chats = []

# The web workbench on http://127.0.0.1:<port>, this machine only.
[web]
port = {port}
"""


def config_path(arg: str | None) -> Path:
    """-c, else a buddy.toml here (a checkout), else the user's config dir."""
    if arg:
        return Path(arg).expanduser()
    return Path("buddy.toml") if Path("buddy.toml").exists() else CONFIG_DIR / "buddy.toml"


def load_env(config: Path) -> None:
    """The .env next to the config, for what the environment doesn't set already (a systemd
    unit's EnvironmentFile, the shell): it never overrides."""
    env = config.parent / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        key, eq, value = line.strip().partition("=")
        if eq and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _keep(path: Path) -> Path:
    """Move an existing file aside as path.bak-<stamp>; returns where it went."""
    old = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    path.rename(old)
    return old


def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
    if secret:
        import getpass
        got = getpass.getpass(f"{prompt}: ")
    else:
        got = input(f"{prompt}{f' [{default}]' if default else ''}: ")
    return got.strip() or default


def init(args) -> int:
    path = Path(args.config).expanduser() if args.config else CONFIG_DIR / "buddy.toml"
    env = path.parent / ".env"
    if (path.exists() or env.exists()) and not args.force:
        print(f"already set up: {path} (and {env}). `buddy doctor` checks it; "
              "--force starts over and keeps the old files as backups", file=sys.stderr)
        return 2
    asking = not args.yes and sys.stdin.isatty()
    language = args.language or (_ask("Language, zh-CN or en", "zh-CN") if asking else "zh-CN")
    workspace = args.workspace or (_ask("Workspace, where chat turns run", "~") if asking else "~")
    token = args.telegram_token
    if token is None and asking:
        print(f"Telegram: a token from {GET['telegram']} (empty: skip).")
        token = _ask("Telegram bot token", secret=True)
    lark_id, lark_secret = args.lark_app_id, args.lark_app_secret
    if lark_id is None and asking:
        print(f"Lark, international (optional): App ID and Secret from {GET['lark']} (empty: skip).")
        lark_id = _ask("Lark App ID")
        lark_secret = _ask("Lark App Secret", secret=True) if lark_id else ""
    feishu_id, feishu_secret = args.feishu_app_id, args.feishu_app_secret
    if feishu_id is None and asking:
        print(f"Feishu, China (optional): App ID and Secret from {GET['feishu']} (empty: skip).")
        feishu_id = _ask("Feishu App ID")
        feishu_secret = _ask("Feishu App Secret", secret=True) if feishu_id else ""
    if not token and not lark_id and not feishu_id:
        print("no chat set: give a Telegram token (--telegram-token), a Lark app (--lark-app-id, "
              "--lark-app-secret) or a Feishu app (--feishu-app-id, --feishu-app-secret)", file=sys.stderr)
        return 2
    # One Lark-type app is FEISHU_APP_ID on its domain, ids as they are; with both, Feishu is
    # that one and Lark is LARK_APP_ID, whose ids read lark:oc_... (feishu.py).
    domain = "feishu" if feishu_id else "lark"
    first, second = ((feishu_id, feishu_secret), (lark_id, lark_secret)) if feishu_id else ((lark_id, lark_secret), (None, ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = [str(_keep(p)) for p in (path, env) if p.exists()]
    path.write_text(TOML.format(date=time.strftime("%Y-%m-%d"), workspace=workspace,
                                domain=domain, language=language, port=args.port))
    secrets = [f"TELEGRAM_BOT_TOKEN={token or ''}",
               f"FEISHU_APP_ID={first[0] or ''}", f"FEISHU_APP_SECRET={first[1] or ''}",
               f"LARK_APP_ID={second[0] or ''}", f"LARK_APP_SECRET={second[1] or ''}",
               "BUDDY_ALLOWED_IDS="]
    env.touch(mode=0o600)
    env.chmod(0o600)
    env.write_text("# Written by `buddy init`. Secrets: keep this file to yourself (chmod 600).\n"
                   + "\n".join(secrets) + "\n")
    skills = install_skills()
    print(f"wrote {path}\nwrote {env} (chmod 600)" + "".join(f"\nkept {k}" for k in kept)
          + "".join(f"\nskill {s}" for s in skills))
    print("next: buddy service install (or: buddy serve), then write to your bot; it answers "
          "with your id, and `buddy allow <that id>` pairs you")
    return 0


def install_skills() -> list[str]:
    """The skills that come with the buddy, into ~/.claude/skills; one that is there stays."""
    src = next((p for p in (HERE / "skills", HERE.parent.parent / "skills") if p.is_dir()), None)
    if src is None:
        return []
    dest = Path("~/.claude/skills").expanduser()
    out = []
    for skill in sorted(p for p in src.iterdir() if (p / "SKILL.md").exists()):
        if not (dest / skill.name).exists():
            shutil.copytree(skill, dest / skill.name)
            out.append(f"{dest / skill.name} (new)")
    return out


def allow(args) -> int:
    """Pair an id: it may use the bot (.env), and its chat is one of yours (buddy.toml)."""
    path = config_path(args.config)
    env = path.parent / ".env"
    if not path.exists() or not env.exists():
        print(f"not set up: {path} is missing; run `buddy init` first", file=sys.stderr)
        return 2
    lines = env.read_text().splitlines()
    k = next((i for i, x in enumerate(lines) if x.startswith("BUDDY_ALLOWED_IDS=")), None)
    ids = [x for x in (lines[k].partition("=")[2].split(",") if k is not None else []) if x.strip()]
    ids += [args.id] if args.id not in ids else []
    line = "BUDDY_ALLOWED_IDS=" + ",".join(ids)
    lines = lines[:k] + [line] + lines[k + 1:] if k is not None else lines + [line]
    env.write_text("\n".join(lines) + "\n")
    chat = args.chat or (args.id if args.id.startswith("tg:") else "")  # a Telegram DM's chat is its sender
    toml = path.read_text()
    if chat and chat not in toml:
        new, n = re.subn(r"^chats = \[(.*)\]$",
                         lambda m: f"chats = [{m[1] + ', ' if m[1].strip() else ''}\"{chat}\"]",
                         toml, count=1, flags=re.M)
        if n:
            path.write_text(new)
        else:
            print(f"add {chat!r} to [me] chats in {path} by hand", file=sys.stderr)
    restart = restart_hint(args.name)
    print(f"allowed {args.id}" + (f"; chat {chat} is yours" if chat else "") + f"\nrestart the buddy to use it: {restart}")
    return 0


# Where each chat's credentials come from: shown when one isn't set, never opened for you.
GET = {"telegram": "https://t.me/BotFather (/newbot)",
       "lark": "https://open.larksuite.com/app (a self-built app → Credentials)",
       "feishu": "https://open.feishu.cn/app (企业自建应用 → 凭证与基础信息)"}
CONSOLE = {"lark": "https://open.larksuite.com/app/{id}", "feishu": "https://open.feishu.cn/app/{id}"}
# The scopes a Lark/Feishu app needs, by what uses them: (scope, part, required).
SCOPES = [("im:message.p2p_msg:readonly", "your messages to the bot", True),
          ("im:message:send_as_bot", "the bot's answers and cards", True),
          ("im:message.group_at_msg:readonly", "@bot in groups", False),
          ("bitable:app", "the Base workbench ([bitable])", "bitable"),
          ("docs:permission.member:create", "sharing the Base with you ([bitable])", "bitable")]


def _get(url: str, data: dict | None = None, token: str = "") -> dict:
    import json
    req = urllib.request.Request(url, method="POST" if data is not None else "GET",
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:  # the APIs say why in the body
        return json.load(e)


def _telegram(token: str) -> dict:
    """getMe: the bot's name, and whether threaded mode (topics in your DM) is on."""
    try:
        return _get(f"https://api.telegram.org/bot{token}/getMe").get("result") or {"error": "token refused"}
    except Exception as e:  # noqa: BLE001 - any failure is a finding for the report
        return {"error": type(e).__name__}


def _lark(app_id: str, secret: str, domain: str) -> dict:
    """The app's scopes and whether each is granted ({name: granted}), or why not."""
    base = {"lark": "https://open.larksuite.com", "feishu": "https://open.feishu.cn"}[domain]
    try:
        t = _get(base + "/open-apis/auth/v3/tenant_access_token/internal", {"app_id": app_id, "app_secret": secret})
        if t.get("code"):
            return {"error": f"App ID or Secret refused ({t.get('msg', t.get('code'))})"}
        s = _get(base + "/open-apis/application/v6/scopes", token=t["tenant_access_token"])
        if s.get("code"):
            return {"error": f"scopes not readable ({s.get('msg', s.get('code'))})"}
        # grant_status 1 is granted (what a working app shows); anything else is asked for only
        return {"scopes": {x["scope_name"]: x.get("grant_status") == 1 for x in s["data"].get("scopes", [])}}
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


def _app_rows(row, label: str, app_id: str, secret: str, domain: str, cfg: dict) -> None:
    got = _lark(app_id, secret, domain)
    console = CONSOLE[domain].format(id=app_id)
    if "error" in got:
        row(False, f"{label} app {app_id}: {got['error']}", f"check its App ID and Secret: {console}")
        return
    scopes = got["scopes"]
    row(True, f"{label} app {app_id}: {sum(scopes.values())} scopes granted")
    for scope, part, needed in SCOPES:
        if needed == "bitable" and "bitable" not in cfg:
            continue
        if scopes.get(scope):
            continue
        state = "asked for, not granted yet" if scope in scopes else "missing"
        row(False if needed else None, f"{label} scope {scope}: {state} ({part})",
            f"{console} → Permissions & Scopes; then publish a new version"
            + (" (an admin may have to approve it)" if scope in scopes else ""))


def line(ok, what: str, how: str = "") -> str:
    return f"{'✅' if ok else '❌' if ok is False else '⚠️ '} {what}" + (f"\n     → {how}" if how else "")


def doctor(args) -> int:
    """One line per check: ✅ works, ⚠️ off or worth fixing, ❌ the buddy can't run."""
    rows = checks(config_path(args.config), offline=args.offline, name=args.name)
    print("\n".join(line(*r) for r in rows))
    return 1 if any(r[0] is False for r in rows) else 0


def checks(path: Path, *, offline: bool = False, name: str = "buddy") -> list[tuple]:
    """(ok, what, how to fix): ok is True, None (off, or worth fixing) or False (can't run)."""
    load_env(path)
    rows = []

    def row(ok, what, how=""):
        rows.append((ok, what, how))

    row(path.exists() or False, f"config {path}", "" if path.exists() else "buddy init")
    env = path.parent / ".env"
    if env.exists() and WINDOWS:  # no chmod there: the file sits in the user's own profile
        row(True, f"secrets {env}")
    elif env.exists():
        loose = env.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        row(None if loose else True, f"secrets {env}", f"chmod 600 {env}" if loose else "")
    tg = os.environ.get("TELEGRAM_BOT_TOKEN")
    domain = (tomllib.loads(path.read_text()).get("domain", "feishu") if path.exists() else "feishu")
    first = os.environ.get("FEISHU_APP_ID") and ("Feishu" if domain == "feishu" else "Lark")
    second = os.environ.get("LARK_APP_ID") and "Lark (lark: ids)"
    chats = [c for c in ("Telegram" if tg else "", first, second) if c]
    row(bool(chats) or False, "a chat: " + ", ".join(chats) if chats else "a chat",
        "" if chats else "buddy config (or buddy init) with a token or app from: "
        + "; ".join(GET.values()))
    if first == "Lark" and second:
        row(False, "two Lark apps: FEISHU_APP_ID is on domain = \"lark\" and LARK_APP_ID is set",
            'set domain = "feishu" in buddy.toml if FEISHU_APP_ID is a Feishu (China) app')
    if tg and not offline:
        me = _telegram(tg)
        if "error" in me:
            row(False, f"Telegram bot: {me['error']}", "check the token with @BotFather")
        else:
            row(True, f"Telegram bot @{me.get('username')}")
            row(True if me.get("has_topics_enabled") else None,
                "Telegram threaded mode" + ("" if me.get("has_topics_enabled") else " off: no 🏠 main topic, "
                                            "long stories and topics share one chat"),
                "" if me.get("has_topics_enabled") else
                "@BotFather → open the Mini App → your bot → Bot Settings → Threaded Mode")
    cfg = tomllib.loads(path.read_text()) if path.exists() else {}
    if not offline and os.environ.get("FEISHU_APP_ID"):
        _app_rows(row, "Feishu" if domain == "feishu" else "Lark", os.environ["FEISHU_APP_ID"],
                  os.environ.get("FEISHU_APP_SECRET", ""), domain, cfg)
    if not offline and os.environ.get("LARK_APP_ID"):
        _app_rows(row, "Lark", os.environ["LARK_APP_ID"], os.environ.get("LARK_APP_SECRET", ""), "lark", cfg)
    allowed = [x for x in os.environ.get("BUDDY_ALLOWED_IDS", "").split(",") if x.strip()]
    row(True if allowed else None, f"paired: {', '.join(allowed)}" if allowed else "not paired yet",
        "" if allowed else "write to your bot; it answers with your id: buddy allow <id>")
    for tool, needed, part, how in TOOLS:
        found = shutil.which(tool)
        row(True if found else (False if needed else None),
            f"{tool}: {part}" + ("" if found else " (off)"), "" if found else how)
    unit = service_file(name)
    if WINDOWS or MACOS or sys.platform.startswith("linux"):
        row(True if unit.exists() else None, f"service {unit}", "" if unit.exists() else "buddy service install")
    return rows


def unit_text(config: Path) -> str:
    """A systemd user unit for an installed buddy. User services start with a minimal PATH,
    so it carries the folders of the tools the buddy runs (claude, gh, codex and its node)."""
    buddy = _buddy()
    dirs = [os.path.dirname(p) for p in map(shutil.which, ["claude", "codex", "gh", "uv"]) if p]
    path = ":".join(dict.fromkeys([*dirs, os.path.dirname(buddy), "/usr/local/bin", "/usr/bin", "/bin"]))
    env = config.parent / ".env"
    return f"""[Unit]
Description=work_brahman buddy (chat, scheduler, duties)
After=network-online.target

[Service]
WorkingDirectory={config.parent}
EnvironmentFile={env}
Environment=PATH={path}
ExecStart={buddy} -c {config} serve
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""


def _buddy() -> str:
    """This buddy's own command, as a path a service can run (with .exe on Windows)."""
    path = (sys.executable if getattr(sys, "frozen", False)  # a standalone build: itself
            else shutil.which("buddy") or os.environ.get("BUDDY_COMMAND") or sys.argv[0])
    if WINDOWS and not path.lower().endswith(".exe") and Path(path + ".exe").exists():
        path += ".exe"
    return str(Path(path).resolve()) if Path(path).exists() else path


def startup_text(config: Path) -> str:
    """Windows: a .cmd the Startup folder runs at logon, in a minimised window; it starts the
    buddy again 10 s after it stops, like the systemd unit's Restart=."""
    buddy = _buddy()
    return (f'@echo off\r\nrem buddy: started at logon by this file (buddy service install)\r\n'
            f'if not "%1"=="min" start "buddy" /min cmd /c "%~f0" min & exit /b\r\n'
            f':again\r\n"{buddy}" -c "{config}" serve\r\ntimeout /t 10 /nobreak >nul\r\ngoto again\r\n')


def _windows_service(args, config: Path) -> int:
    text = startup_text(config)
    if args.dry_run:
        print(text.replace("\r\n", "\n"), end="")
        return 0
    cmd = STARTUP / f"{args.name}.cmd"
    if cmd.exists() and not args.force:
        print(f"{cmd} exists: --name another, or --force (the old one is kept as a backup)", file=sys.stderr)
        return 2
    STARTUP.mkdir(parents=True, exist_ok=True)
    kept = _keep(cmd) if cmd.exists() else None
    cmd.write_text(text, newline="")
    print(f"wrote {cmd}" + (f"\nkept {kept}" if kept else "") + "\nit starts the buddy when you log on")
    if args.enable:
        subprocess.Popen(["cmd", "/c", str(cmd)], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        print("started now, in a minimised window")
    else:
        print(f"start it now: {cmd}  (or log off and on)")
    return 0


def plist_bytes(config: Path, name: str) -> bytes:
    """A launchd agent: at login (RunAtLoad), again 10 s after it stops (KeepAlive,
    ThrottleInterval), its output in ~/Library/Logs. The .env is read by the buddy itself."""
    import plistlib
    buddy = _buddy()
    dirs = [os.path.dirname(p) for p in map(shutil.which, ["claude", "codex", "gh", "uv"]) if p]
    path = ":".join(dict.fromkeys([*dirs, os.path.dirname(buddy), "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]))
    logs = Path("~/Library/Logs").expanduser() / f"{name}.log"
    return plistlib.dumps({"Label": label(name), "ProgramArguments": [buddy, "-c", str(config), "serve"],
                           "WorkingDirectory": str(config.parent), "EnvironmentVariables": {"PATH": path},
                           "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
                           "StandardOutPath": str(logs), "StandardErrorPath": str(logs)})


def _macos_service(args, config: Path) -> int:
    data = plist_bytes(config, args.name)
    if args.dry_run:
        print(data.decode(), end="")
        return 0
    plist = service_file(args.name)
    if plist.exists() and not args.force:
        print(f"{plist} exists: --name another, or --force (the old one is kept as a backup)", file=sys.stderr)
        return 2
    AGENTS.mkdir(parents=True, exist_ok=True)
    kept = _keep(plist) if plist.exists() else None
    plist.write_bytes(data)
    print(f"wrote {plist}" + (f"\nkept {kept}" if kept else ""))
    domain = f"gui/{os.getuid()}"
    if args.enable:
        subprocess.run(["launchctl", "bootout", f"{domain}/{label(args.name)}"], capture_output=True, check=False)
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=False)
        print(f"started: launchctl print {domain}/{label(args.name)}")
    else:
        print(f"start it: launchctl bootstrap {domain} {plist}")
    return 0


def service(args) -> int:
    config = config_path(args.config).resolve()
    if not config.exists():
        print(f"not set up: {config} is missing; run `buddy init` first", file=sys.stderr)
        return 2
    if WINDOWS:
        return _windows_service(args, config)
    if MACOS:
        return _macos_service(args, config)
    if not sys.platform.startswith("linux"):
        print("Linux (systemd), macOS (launchd) and Windows (the Startup folder) only: "
              "elsewhere, run `buddy serve` in a terminal", file=sys.stderr)
        return 2
    text = unit_text(config)
    if args.dry_run:
        print(text, end="")
        return 0
    unit = UNITS / f"{args.name}.service"
    if unit.exists() and not args.force:
        print(f"{unit} exists: --name another, or --force (the old one is kept as a backup)",
              file=sys.stderr)
        return 2
    UNITS.mkdir(parents=True, exist_ok=True)
    kept = _keep(unit) if unit.exists() else None
    unit.write_text(text)
    run = lambda *a: subprocess.run(["systemctl", "--user", *a], check=False)  # noqa: E731
    run("daemon-reload")
    print(f"wrote {unit}" + (f"\nkept {kept}" if kept else ""))
    if args.enable:
        run("enable", "--now", unit.name)
        print(f"started: systemctl --user status {unit.name}")
    else:
        print(f"start it: systemctl --user enable --now {unit.name}")
    print('keep it running after you log out: loginctl enable-linger "$USER"')
    return 0
