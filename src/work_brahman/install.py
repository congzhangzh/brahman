"""Setting the buddy up on a new machine, for someone who installed it as an app
(`uv tool install brahman-buddy`) rather than working in its repository:

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

import json
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
# Headless Claude Code cannot ask for approval: a tool this list (and your Claude Code
# settings, ~/.claude/settings.json and the folder's .claude/) doesn't allow is denied, and
# the reply says which. It starts read-only, but Read sees every file you can, and git's
# --output can write one: chat only with repositories and folders you trust.
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


STDIN_ENDED = False  # set once a prompt met the end of its input (a pipe, a script)


def _ask(prompt: str, default: str = "", secret: bool = False) -> str:
    """A line typed, or the default. At the end of the input it answers as Enter would (a "[y/N]"
    is N) instead of an EOFError traceback (2026-11-10, `printf ... | buddy config` on macOS),
    and says so in STDIN_ENDED, so a menu that loops can stop."""
    global STDIN_ENDED
    try:
        if secret:
            import getpass
            got = getpass.getpass(f"{prompt}: ")
        else:
            got = input(f"{prompt}{f' [{default}]' if default else ''}: ")
    except EOFError:
        print()
        STDIN_ENDED = True
        return default
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
    china = language == "zh-CN"  # the owner, 2026-11-12: a zh-CN setup doesn't offer Telegram or Lark (flags still work)
    if token is None and asking and not china:
        print(f"Telegram: a token from {GET['telegram']} (empty: skip).")
        token = _ask("Telegram bot token", secret=True)
    apps = {"lark": [args.lark_app_id, args.lark_app_secret, ""], "feishu": [args.feishu_app_id, args.feishu_app_secret, ""]}
    linked_by_scan: dict[str, str] = {}  # brand -> the chat a scan linked (hello)
    for brand, title, where in (("lark", "Lark, international", GET["lark"]), ("feishu", "Feishu, China", GET["feishu"])):
        app = apps[brand]
        scanning = getattr(args, f"{brand}_scan", False)
        if app[0] is None and asking and not scanning and not (china and brand == "lark"):
            print(f"{title} (optional): App ID and Secret from {where}, or `scan`: a QR code makes the "
                  "app for you (empty: skip).")
            app[0] = _ask(f"{brand.capitalize()} App ID")
            scanning = app[0].strip().lower() == "scan"
            if not scanning:
                app[1] = _ask(f"{brand.capitalize()} App Secret", secret=True) if app[0] else ""
        if scanning:
            try:
                got = scan_app(brand)
            except RuntimeError as e:
                print(f"{title}: {e}", file=sys.stderr)
                return 2
            # a Feishu scan on a Lark tenant makes a Lark app (the SDK switches by itself): it goes
            # where it belongs, unless a Lark app is given already
            where = got["domain"]
            if where != brand:
                if apps[where][0]:
                    print(f"{title}: the scan made a {where.capitalize()} app, but one is given already; "
                          f"scan it again as {where}, or leave that one out", file=sys.stderr)
                    return 2
                app[:] = [None, "", ""]
            apps[where][:] = [got["app_id"], got["secret"], got["open_id"]]
            print(f"{where.capitalize()} app {got['app_id']} made; you are {got['open_id'] or '?'}")
            chat = hello(got["app_id"], got["secret"], where, got["open_id"]) if got["open_id"] else ""
            linked_by_scan[where] = chat
            print("note: a QR scan makes a PersonalAgent app; if its chats refuse thread replies, topics and "
                  "long stories answer in the main chat and buddy doctor says so (threads: make the app by hand)")
            print(f"your chat {chat} is linked: the bot wrote to you there" if chat else
                  "your chat isn't linked yet: write anything to the bot, then link it in the "
                  "workbench's Setup tab (or buddy tui, L)")
    (lark_id, lark_secret, lark_me), (feishu_id, feishu_secret, feishu_me) = apps["lark"], apps["feishu"]
    # chats a scan linked (hello): the first app's as they are, the second's as lark:...
    scan_chats = [c for c in (linked_by_scan.get("feishu" if feishu_id else "lark", ""),
                              ("lark:" + linked_by_scan["lark"]) if feishu_id and linked_by_scan.get("lark") else "") if c]
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
    toml = TOML.format(date=time.strftime("%Y-%m-%d"), workspace=workspace, domain=domain,
                       language=language, port=args.port)
    if scan_chats:
        toml = toml.replace("\nchats = []", "\nchats = [" + ", ".join(f'"{c}"' for c in scan_chats) + "]", 1)
    path.write_text(toml)
    secrets = [f"TELEGRAM_BOT_TOKEN={token or ''}",
               f"FEISHU_APP_ID={first[0] or ''}", f"FEISHU_APP_SECRET={first[1] or ''}",
               f"LARK_APP_ID={second[0] or ''}", f"LARK_APP_SECRET={second[1] or ''}",
               "BUDDY_ALLOWED_IDS=" + ",".join(
                   x for x in ((feishu_me if feishu_id else lark_me),  # the first app's ids as they are
                               (f"lark:{lark_me}" if feishu_id and lark_me else "")) if x)]
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
    """The skills that come with the buddy, into ~/.claude/skills, as `buddy setup` does it:
    linked (copied by the one-file executable), ours kept current, the user's left alone (a
    warning with its hint)."""
    from .workbench import skill_steps
    done = []
    for what, state, do, hint in skill_steps(Path(os.path.expanduser("~"))):
        if do is not None:
            do()
            done.append(f"{what}: {state}")
        elif hint:
            done.append(f"{what}: ⚠ {state}\n  hint: {hint}")
    return done


def hello(app_id: str, secret: str, domain: str, open_id: str) -> str:
    """Right after a QR scan: the new app writes to the one who scanned, by open_id, and the
    answer names the chat that made, so it is linked at once (the owner, 2026-11-12: on the Mac
    a scan paired him but linked no chat, so cards, asks and pings had nowhere to go). The
    chat's oc_ id, or "" when the platform refuses (scopes not granted yet: write to the bot
    once, and the Setup tab offers to link that chat)."""
    import json
    base = {"lark": "https://open.larksuite.com", "feishu": "https://open.feishu.cn"}[domain]
    try:
        t = _get(base + "/open-apis/auth/v3/tenant_access_token/internal", {"app_id": app_id, "app_secret": secret})
        if t.get("code"):
            return ""
        r = _get(base + "/open-apis/im/v1/messages?receive_id_type=open_id",
                 {"receive_id": open_id, "msg_type": "text",
                  "content": json.dumps({"text": "✅ buddy: this is your chat with me; it is linked. "
                                                 "Asks, cards and pings come here."})},
                 token=t["tenant_access_token"])
        return "" if r.get("code") else (r.get("data") or {}).get("chat_id", "")
    except Exception:  # noqa: BLE001 - linking later is the fallback
        return ""


def scan_app(brand: str) -> dict:
    """The app made by a QR scan (feishu_scan.py), with the scopes the buddy needs."""
    from . import feishu_scan
    return feishu_scan.scan(brand, [s for s, _, need in SCOPES if need is True])


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
    missing = [sc for sc, _, need in SCOPES if need is True and not scopes.get(sc)]
    if missing:  # one QR code turns them all on (feishu_scan.enable_url, as the official lark-cli does)
        from .feishu_scan import enable_url
        row(None, f"{label}: turn on what is missing in one go: buddy scan {domain} --current shows a QR code",
            enable_url(domain, app_id, missing))
    for scope, part, needed in SCOPES:
        if needed == "bitable" and "bitable" not in cfg:
            continue
        if scopes.get(scope):
            continue
        state = "asked for, not granted yet" if scope in scopes else "missing"
        row(False if needed else None, f"{label} scope {scope}: {state} ({part})",
            f"{console} → Permissions & Scopes; then publish a new version"
            + (" (an admin may have to approve it)" if scope in scopes else ""))


def codex_server() -> tuple:
    """Codex's own server (its app-server daemon): Codex starts it when first needed and keeps it
    itself, outside the buddy; it updates itself every hour, and an update restarts it, which may
    cut a running turn. The buddy needs it to wake a Codex short story (codexd): down, a reply
    waits for the session's own `check`, silently. So doctor says how it is."""
    from . import codexd
    try:
        p = subprocess.run(["codex", "app-server", "daemon", "version"], capture_output=True, text=True, timeout=15)
        v = json.loads(p.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        v = {}
    server, cli = v.get("appServerVersion"), v.get("cliVersion")
    start = "codex app-server daemon start (or just run codex)"
    if v.get("status") != "running" or not codexd.alive():
        return (None, "Codex server not running: Codex short stories are not woken, replies wait for check",
                start)
    if server and cli and server != cli:
        return (None, f"Codex server {server}, but codex is {cli}",
                "codex app-server daemon restart (it may cut a running Codex turn)")
    return (True, f"Codex server {server or '?'}: Codex short stories are woken "
                  "(Codex runs it, and an hourly update may restart it)", "")


def line(ok, what: str, how: str = "") -> str:
    return f"{'✅' if ok else '❌' if ok is False else '⚠️ '} {what}" + (f"\n     → {how}" if how else "")


def doctor(args) -> int:
    """One line per check: ✅ works, ⚠️ off or worth fixing, ❌ the buddy can't run."""
    rows = all_checks(config_path(args.config), offline=args.offline, name=args.name,
                      peers=getattr(args, "peers", False))
    if getattr(args, "json", False):  # for scripts and cron: the same rows, the same exit code
        print(json.dumps([{"ok": ok, "what": what, "how": how} for ok, what, how in rows], ensure_ascii=False, indent=1))
    else:
        print("\n".join(line(*r) for r in rows))
    return 1 if any(r[0] is False for r in rows) else 0


def all_checks(path: Path, *, offline: bool = False, name: str = "buddy", peers: bool = False, pinger=None) -> list[tuple]:
    """The buddy's checks, then (peers) the peer build's (peer.doctor: the secret, where serve
    listens, a signed ping both ways to every peer), each of those named "peer: ..."."""
    rows = checks(path, offline=offline, name=name)
    if peers:
        from . import peer as P
        cfg = P.load(os.environ)
        if not os.environ.get(P.ENV) and not cfg["points"]:  # not used here: off, not broken
            rows.append((None, "peer: off (no peers, no BRAHMAN_PEER_SECRET)", "buddy tui → Peers, if two brahmans should pass bundles"))
        else:
            rows += [(ok, f"peer: {what}", how) for ok, what, how in P.doctor(cfg, os.environ, pinger)]
    return rows


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
    # Each app set up (its id and token) needs someone paired there, and a chat linked there for
    # cards and asks: a Feishu added beside a paired Lark and Telegram was silent (2026-11-12).
    linked = list(cfg.get("me", {}).get("chats", []))
    from . import pings
    state = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "work_brahman"
    wrote = pings.refused(state, allowed)  # who wrote and was refused: their exact `buddy allow` line
    apps = [("Telegram", tg, "tg:", "tg:"), (first or "", os.environ.get("FEISHU_APP_ID"), "ou_", "oc_"),
            ("Lark (lark: ids)", os.environ.get("LARK_APP_ID"), "lark:ou_", "lark:oc_")]
    for app, on, person, chat in apps:
        if not on:
            continue
        mine = lambda x, p: x.startswith(p) and not (p in ("ou_", "oc_") and x.startswith("lark:"))  # noqa: E731
        here = [w for w in wrote if mine(w["sender"], person)]
        if not any(mine(a, person) for a in allowed):
            if here:  # it wrote already: the line is known, id and chat
                w = here[0]
                row(None, f"{app}: set up, but nobody paired there; {w['sender']} wrote {w['times']}× (last {w['last']})",
                    f"if that is you: {w['allow']}  (or Pair in buddy tui → Config, or the workbench's Setup tab), "
                    "then restart the buddy: systemctl --user restart buddy")
            else:
                row(None, f"{app}: set up, but nobody paired there",
                    f"1) write anything to its bot (a direct message); 2) it answers with your line, "
                    f"buddy allow {person}… --chat {chat}…, and buddy doctor shows it here too; 3) run it, or Pair "
                    "in buddy tui → Config; 4) restart the buddy")
        elif person != "tg:":  # Lark and Feishu: card buttons need a callback no API shows; a press proves it
            done = [p for c, p in pings.pings(state).items() if mine(c, chat) and p.get("button_at")]
            if done:
                row(True, f"{app}: card buttons work (卡片回传交互, card.action.trigger): pressed {done[0]['button_at'][5:16]}")
            else:
                row(None, f"{app}: card buttons not tested yet (卡片回传交互, card.action.trigger: asks, ■ Stop)",
                    "Setup tab → Ping → Send ping to this app's chat, then press 🏓 Pong on the card; if it shows "
                    "an error, switch it on: developer console → 事件与回调 (Events & Callbacks) → 回调配置 "
                    "(Callbacks) → long connection → add 卡片回传交互 (card.action.trigger); publish a version"
                    f" (or buddy scan {'lark' if app.startswith('Lark') else 'feishu'} --current: a QR code that opens "
                    "the console on that app with it to turn on)")
        if any(mine(a, person) for a in allowed) and not any(mine(c, chat) for c in linked):
            mine_id = next(a for a in allowed if mine(a, person))
            seen = [u for u in pings.unlinked(state, linked) if mine(u["chat"], chat)]
            row(None, f"{app}: {mine_id} paired, but no chat linked there: cards, asks and pings won't reach it",
                (f"{seen[0]['allow']} (you wrote from it {seen[0]['times']}×; or Link in the Setup tab, L in buddy tui)"
                 if seen else f"write anything to the bot, then Link in the Setup tab (L in buddy tui), or "
                 f"buddy allow {mine_id} --chat {chat}<your direct chat with the bot>") + ", then restart the buddy")
    from .feishu import threads_refusals  # a thread reply refused: the buddy replied in the main chat
    for app_id, r in threads_refusals().items():
        if app_id in (os.environ.get("FEISHU_APP_ID"), os.environ.get("LARK_APP_ID")):
            row(None, f"{r['app'].capitalize()} app {app_id}: thread replies refused (code {r['code']}, {r['at']}): "
                      "topics and long stories answer in the main chat instead",
                "for threads, make the app by hand (README, section 1: a self-built app with the Bot "
                "capability); a QR scan makes a PersonalAgent app, which may not take threads")
    from . import notify  # what Feishu and Lark get on their own, and the calls spent this month
    used = notify.calls(state)
    for app, on in (("feishu", bool(os.environ.get("FEISHU_APP_ID")) and domain == "feishu"),
                    ("lark", bool(os.environ.get("LARK_APP_ID")) or (bool(os.environ.get("FEISHU_APP_ID")) and domain == "lark")),
                    ("telegram", bool(os.environ.get("TELEGRAM_BOT_TOKEN"))), ("web", bool(cfg.get("web", {}).get("port")))):
        if not on:
            continue
        m = notify.mode(cfg, app)
        said = {"off": "nothing on its own: it answers you; story cards and posts go to the other chats",
                "ends": "a story's card at its start and end only; posts and questions",
                "all": "every card change, post and mirror"}[m]
        quota = app in ("feishu", "lark")  # the apps whose message calls count against a monthly quota
        row(True if m != "all" or not quota else None,
            f"notify · {'the web chat' if app == 'web' else app.capitalize()}: {m}, {said}"
            + (f" · {used.get(app, 0)} message calls this month (this buddy)" if quota else ""),
            "" if m != "all" or not quota else "each card change is a call against the monthly quota: notify = ends "
            "saves most (buddy tui → Config, or the Setup tab)")
    # Outside calls (the owner, 2026-11-12: "ready to deploy by default in an enterprise or offline"):
    # a fresh install calls nothing until something is switched on; this row says what is on, so an
    # admin sees every endpoint this buddy talks to (Claude Code's own is Claude Code's to configure).
    out = []
    if os.environ.get("FEISHU_APP_ID"):
        out.append("open.feishu.cn" if domain == "feishu" else "open.larksuite.com")
    if os.environ.get("LARK_APP_ID"):
        out.append("open.larksuite.com")
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        out.append("api.telegram.org")
    from . import notify as _n, webpush as _wp
    if _n.mode(cfg, "webpush") != "off":
        out += sorted({__import__("urllib.parse").parse.urlparse(x["endpoint"]).netloc for x in _wp.subs(state)}) or ["(web push on, no browser yet)"]
    try:
        from . import peer as _peer
        out += [f"peer {k}" for k in sorted(_peer.load(os.environ)["points"])]
    except Exception:  # noqa: BLE001 - no peers file: nothing to list
        pass
    row(True, "outside calls: " + (", ".join(dict.fromkeys(out)) if out else "none (nothing is switched on)")
        + "; plus Claude Code's own endpoint (ANTHROPIC_BASE_URL or its default)", "")
    from . import webauth  # who may use the workbench (webauth.py)
    tok, hosts = webauth.token(), list(cfg.get("web", {}).get("hosts") or [])
    if tok:
        row(None if hosts else True, "workbench: a web token is set" + (f"; also reachable as {', '.join(hosts)}" if hosts else ""),
            "plain HTTP: on that network the token and the pages can be read; an SSH client on the device "
            "(-L to 127.0.0.1) needs no token" if hosts else "")
    elif hosts:
        row(None, f"workbench: [web] hosts lists {', '.join(hosts)}, but no BUDDY_WEB_TOKEN is set: they are ignored",
            "set a long random BUDDY_WEB_TOKEN (Setup → Config) to use them")
    from . import webpush  # web push to the owner's browsers (webpush.py): off by default
    wp, browsers = webpush.mode(cfg), webpush.subs(state)
    if wp != "off" or browsers:
        row(True if wp == "off" or browsers else None,
            f"web push: notify = {wp}, {len(browsers)} browser(s) subscribed · {used.get('webpush', 0)} pushes this month"
            + (" (off: nothing is sent)" if wp == "off" else ""),
            "" if browsers or wp == "off" else "🔔 in the workbench subscribes this browser (buddy push test sends one)")
    for r in wrote[:5]:  # wrote, refused, not paired since
        row(None, f"wrote but not paired: {r['sender']} on {r['channel']} ({r['times']}×, last {r['last']})",
            f"if that is you: {r['allow']}, then restart the buddy")
    for tool, needed, part, how in TOOLS:
        found = shutil.which(tool)
        row(True if found else (False if needed else None),
            f"{tool}: {part}" + ("" if found else " (off)"), "" if found else how)
        if tool == "codex" and found:
            row(*codex_server())
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


def _holder() -> tuple[int | None, bool]:
    """Who holds `buddy serve`'s lock (portable.hold): its pid, and whether it runs."""
    from . import portable
    state = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "work_brahman"
    pid = portable.holder(state / "serve.lock")
    return pid, bool(pid and portable.alive(pid))


def service_ctl(action: str, name: str = "buddy", run=subprocess.run) -> int:
    """`buddy service status | restart | uninstall` (the owner, 2026-11-12: "maybe you could add
    uninstall, status and restart?"), on each system the way `install` set it up: a systemd
    user unit, a launchd agent, a Startup-folder .cmd. Status exits 0 when it runs, 3 when not
    (as systemctl). Uninstall stops it and keeps the file as a backup beside where it was."""
    f = service_file(name)
    pid, up = _holder()
    holder = f"buddy serve: pid {pid}, {'running' if up else 'not running'}" if pid else "buddy serve: no lock held"
    if not f.exists() and action != "status":
        print(f"no service {f}: buddy service install first", file=sys.stderr)
        return 2
    out = lambda *a: run(list(a), capture_output=True, text=True, check=False)  # noqa: E731
    if WINDOWS:
        def stop():
            if up:
                out("taskkill", "/PID", str(pid), "/T", "/F")
        if action == "status":
            print(f"{'✅' if up else '❌'} {holder}\nservice: {f if f.exists() else 'not installed'}")
            return 0 if up else 3
        if action == "restart":
            stop()
            subprocess.Popen(["cmd", "/c", str(f)], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            print(f"restarted: {f}, in a minimised window")
            return 0
        stop()
    elif MACOS:
        target = f"gui/{os.getuid()}/{label(name)}"
        if action == "status":
            r = out("launchctl", "print", target)
            state = next((x.split("=", 1)[1].strip() for x in r.stdout.splitlines() if x.strip().startswith("state =")), "")
            running = r.returncode == 0 and state == "running"
            print(f"{'✅' if running else '❌'} {target}: {state or 'not loaded'}\n{holder}\n"
                  f"log: {Path('~/Library/Logs').expanduser() / (name + '.log')}")
            return 0 if running else 3
        if action == "restart":
            r = out("launchctl", "kickstart", "-k", target)
            print(f"restarted: {target}" if r.returncode == 0 else f"not restarted: {r.stderr.strip() or r.returncode}")
            return 0 if r.returncode == 0 else 1
        out("launchctl", "bootout", target)
    else:
        unit = f.name
        if action == "status":
            r = out("systemctl", "--user", "show", unit, "-p", "ActiveState", "-p", "MainPID", "-p",
                    "ActiveEnterTimestamp", "-p", "NRestarts")
            p = dict(x.split("=", 1) for x in r.stdout.splitlines() if "=" in x)
            running = p.get("ActiveState") == "active"
            print(f"{'✅' if running else '❌'} {unit}: {p.get('ActiveState', 'unknown')}"
                  + (f" since {p['ActiveEnterTimestamp']}, pid {p.get('MainPID')}, {p.get('NRestarts', '0')} restarts"
                     if running else "") + f"\n{holder}\nlog: journalctl --user -u {unit} -e")
            return 0 if running else 3
        if action == "restart":
            r = out("systemctl", "--user", "restart", unit)
            print(f"restarted: {unit}" if r.returncode == 0 else f"not restarted: {r.stderr.strip() or r.returncode}")
            return 0 if r.returncode == 0 else 1
        out("systemctl", "--user", "disable", "--now", unit)
    kept = _keep(f)  # uninstall: stopped above; the file moves aside as a backup
    if not WINDOWS and not MACOS:
        out("systemctl", "--user", "daemon-reload")
    print(f"uninstalled: {f} (kept {kept}); the buddy no longer starts by itself. Its config and state stay.")
    return 0


def service(args) -> int:
    if args.action != "install":
        return service_ctl(args.action, args.name)
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
