"""buddy init | buddy allow ID | buddy config | buddy doctor | buddy service install
| buddy serve | buddy job NAME | buddy jobs | buddy duty NAME | buddy ask QUESTION KEY=LABEL…
| buddy bitable | buddy stories | buddy family NAME
| buddy short start|mark|plan|note|ask|wait|check|done|list | buddy long hook|list

Secrets come from the environment only: FEISHU_APP_ID + FEISHU_APP_SECRET for the app on
buddy.toml's `domain`, LARK_APP_ID + LARK_APP_SECRET for a Lark app beside a Feishu one,
TELEGRAM_BOT_TOKEN for Telegram (any of them, or all), BUDDY_ALLOWED_IDS; the .env
next to buddy.toml fills in what the environment lacks. Everything else comes from buddy.toml:
-c, else ./buddy.toml (a checkout), else ~/.config/work_brahman/buddy.toml (install.py).
"""

import argparse
import json
import logging
import os
import sys
import time
import tomllib
from pathlib import Path

from . import asks, configure, install, long_stories as longs, portable, short_stories as shorts, text, topics
from .bitable import Bitable
from .bot import Bot, Sessions
from .channel import Linked, post_all
from .duties import DUTIES
from .engine import ClaudeCode, long_engines
from .families import FAMILIES
from .feishu import DOMAINS, Feishu
from .runs import VERSION, from_env, tail
from .scheduler import Scheduler
from .telegram import Telegram
from .web import CHAT, Web, WebChat, WebRelay


def feishu_domain(cfg: dict):
    return DOMAINS[cfg.get("domain", "feishu")]



def _private(state: Path) -> None:
    """The state folder holds the chats, the runs and terminal captures: the user's own (0700)."""
    try:
        state.mkdir(parents=True, exist_ok=True)
        state.chmod(0o700)
    except OSError:
        pass


def _status(config: str | None) -> int:
    """`buddy` on its own: what needs fixing, the page, the stories running; quick, offline."""
    path = install.config_path(config)
    if not path.exists():
        print(f"buddy {VERSION}: not set up yet (no {path})\n\n{install.START}")
        return 0
    rows = install.checks(path, offline=True)
    cfg = tomllib.loads(path.read_text())
    text.set_language(cfg.get("buddy", {}).get("language"))
    state = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "work_brahman"
    ok = sum(r[0] is True for r in rows)
    print(f"buddy {VERSION} · {path}")
    print(f"✅ {ok} of {len(rows)} checks ok" + ("" if ok == len(rows) else "; the rest:"))
    for r in rows:
        if r[0] is not True:
            print(install.line(*r))
    if port := cfg.get("web", {}).get("port"):
        print(f"\nworkbench: http://127.0.0.1:{port}/")
    running = [s for s in shorts.all_stories(state) if s["status"] == "running"]
    print(f"\nshort stories running: {len(running)}" + (" (buddy short list)" if running else ""))
    for line in shorts.board(state).splitlines()[:5] if running else []:
        print("  " + (line if len(line) <= 100 else line[:99] + "…"))
    if len(running) > 5:
        print(f"  … {len(running) - 5} more")
    print(f"long stories: {len(longs.all_stories(state))} (buddy long list)")
    print("\nhelp: buddy --help · buddy <command> --help · /help in the chat")
    return 1 if any(r[0] is False for r in rows) else 0


def _short(args, state: Path, channels: list, chats: list[str]) -> int:
    """buddy short start|ask|mark|plan|note|done: change the story, print its title and news."""
    a = args.action
    if a == "start":
        t = shorts.start(state, channels, chats, args.title, args.mark, who=args.who, note=args.note)
        print(t["id"])
        return 0 if t["copies"] else 1
    if a == "ask":
        if not (args.question or args.fork):
            print("ask needs a question, or --fork NAME", file=sys.stderr)
            return 2
        t, q = shorts.ask(state, channels, args.number, args.question, args.option, fork=args.fork)
        if q is None:
            print(f"no short story #{args.number}" if t is None
                  else f"short story #{args.number} has ended: nobody would answer", file=sys.stderr)
            return 2
        print(shorts.title(t))
        print(f"question {q['id']}")
        if args.wait:
            return _wait(state, args.number, q["id"], args.timeout)
        return _news(state, args.number)
    t = (shorts.mark(state, channels, args.number, args.name, status=args.status, note=args.note,
                     took=args.took, round=args.round)
         if a == "mark" else
         shorts.plan(state, channels, args.number, args.marks) if a == "plan" else
         shorts.note(state, channels, args.number, args.text) if a == "note" else
         shorts.finish(state, channels, args.number, failed=args.failed, note=args.note))
    if t is None:
        print(f"no short story #{args.number}", file=sys.stderr)
        return 2
    print(shorts.title(t))
    return _news(state, args.number) if a != "done" else 0

def _news(state: Path, number: int, seen: set | None = None) -> int:
    """Tell the executor what the owner said on the card: each reply and each answer once,
    then the stop request (exit 3) if there is one. An answer is one line to parse,
    `answer <question>: <text>` (a newline in it written as \\n); seen collects their ids."""
    stop, replies, answered = shorts.news(state, number)
    for r in replies:
        print(f"💬 reply from the owner ({r['where']}, {r['at'][11:16]}): {r['text']}")
    for q in answered:
        print(_answer_line(q))
        if seen is not None:
            seen.add(q["id"])
    if stop:
        print(f"■ stop asked from {stop['where']} at {stop['at'][11:16]}: stop the work, then "
              f"`buddy short done {number} --failed --note \"stopped by the owner\"`")
        return 3
    return 0


def _claude_pid() -> int | None:
    """The `claude` process this hook runs under: the nearest ancestor of that name."""
    pid = os.getppid()
    for _ in range(12):
        up = portable.parent(pid)  # /proc on Linux, ps on macOS
        if up is None:
            return None
        if up[1] == "claude":
            return pid
        pid = up[0]
        if pid <= 1:
            return None
    return None


def run_long_hook(state: Path, channels: list, chats: list[str], engine: str = "claude-code") -> int:
    """One hook event (JSON on stdin) for the long story of this session. Claude Code: only
    sessions started with BUDDY_LONG set have one. Codex: sessions `buddy-long codex` registered
    (its hooks run in Codex's daemon, not under the terminal). scripts/buddy-long checks first."""
    try:
        e = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0  # never break the session over a hook
    sid, event = e.get("session_id", ""), e.get("hook_event_name", "")
    if engine == "codex":
        return _codex_hook(state, channels, chats, e, sid, event)
    if event == "SessionStart":
        title = os.environ.get("BUDDY_LONG", "")
        longs.attach(state, channels, chats, session_id=sid, cwd=e.get("cwd", ""),
                     transcript=e.get("transcript_path", ""), title="" if title == "1" else title,
                     pid=_claude_pid(), sty=os.environ.get("STY", ""), window=os.environ.get("WINDOW", ""),
                     source=e.get("source", "startup"))
    elif event == "UserPromptSubmit":
        longs.busy(state, sid, e.get("prompt", ""))
    elif event == "Stop":
        said = longs.last_message(e.get("transcript_path", ""))
        handed = longs.turn(state, channels, sid, said)
        if handed:  # what the owner wrote while it worked: it carries on with that
            print(json.dumps({"decision": "block", "reason": handed}, ensure_ascii=False))
    elif event == "Notification":
        longs.waiting(state, channels, sid, e.get("message", ""))
    elif event == "SessionEnd":
        longs.detach(state, channels, sid, e.get("reason", ""))
    return 0


PERMISSION_WAIT = {True: 45, False: 540}  # seconds: a terminal holds it (its own dialog waits) / none does


def _codex_hook(state: Path, channels: list, chats: list[str], e: dict, sid: str, event: str) -> int:
    if event == "SessionStart":
        if longs.by_session(state, sid) is None and (r := longs.claim(state, e.get("cwd", ""))):
            longs.attach(state, channels, chats, session_id=sid, cwd=e.get("cwd", ""),
                         transcript=e.get("transcript_path", ""), title=r["title"], pid=r["pid"],
                         sty=r["sty"], window=r["window"], source=e.get("source", "startup"), engine="codex")
        elif (story := longs.by_session(state, sid)) and (r := longs.claim(state, e.get("cwd", ""))):
            longs.attach(state, channels, chats, session_id=sid, cwd=e.get("cwd", ""),  # codex resume
                         transcript=e.get("transcript_path", ""), title=story["title"], pid=r["pid"],
                         sty=r["sty"], window=r["window"], source="resume", engine="codex")
    elif event == "UserPromptSubmit":
        longs.busy(state, sid, e.get("prompt", ""))
    elif event == "Stop":
        longs.turn(state, channels, sid, e.get("last_assistant_message") or "")
    elif event == "PermissionRequest":
        story = longs.by_session(state, sid)
        if story:
            tool = e.get("tool_name", "")
            what = (e.get("tool_input") or {}).get("description") or json.dumps(e.get("tool_input") or {})[:1500]
            said = longs.permission(state, channels, chats, sid, tool=tool, what=what,
                                    wait=PERMISSION_WAIT[longs.held(story)])
            if said:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                  "decision": {"behavior": said} | ({"message": "denied by the owner from his phone"}
                                                                   if said == "deny" else {})}}))
    elif event == "Interrupt":
        if story := longs.by_session(state, sid):
            longs._change(state, story["id"], lambda s: s.update(state="idle"))
    elif event == "SessionEnd":
        longs.detach(state, channels, sid, e.get("reason", ""))
    return 0


def _answer_line(q: dict) -> str:
    return f"answer {q['id']}: " + q["answer"]["text"].replace("\\", "\\\\").replace("\n", "\\n")


WAIT_POLL = 2  # seconds between reads of the story file while waiting: a local file, no chat


def _wait(state: Path, number: int, question: int | None, timeout: float | None) -> int:
    """Block until the owner answers (exit 0, the answer printed), a stop is asked (3), the
    story ends or has no such question (2), or timeout seconds pass (4: still waiting; 0
    looks once). Replies that come meanwhile are printed as they come."""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        story = shorts.load(state, number)
        qs = story.get("questions", [])
        q = next((q for q in qs if q["id"] == question), None) if question else (qs[-1] if qs else None)
        if q is None:
            print(f"no question {question or ''} on short story #{number}".replace("  ", " "), file=sys.stderr)
            return 2
        if story["status"] != "running" and not q["answer"]:
            print(f"short story #{number} ended with question {q['id']} not answered", file=sys.stderr)
            return 2
        seen = set()
        code = _news(state, number, seen)
        q = next(x for x in shorts.load(state, number)["questions"] if x["id"] == q["id"])
        if q["answer"] and q["id"] not in seen:  # answered before: read by an earlier check
            print(_answer_line(q))
        if code or q["answer"]:
            return code
        if deadline is not None and time.monotonic() >= deadline:
            print(f"question {q['id']}: still waiting")
            return 4
        time.sleep(WAIT_POLL if deadline is None else max(0, min(WAIT_POLL, deadline - time.monotonic())))


def _utf8() -> None:
    """✅ and Chinese on every output: Windows writes a pipe or a file in its ANSI code page
    (cp1252 has no ✅), so a redirected `buddy doctor` would crash there. A console is fine."""
    for stream in (sys.stdout, sys.stderr):
        if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass


def _relaunch() -> int:
    """Windows: start again in Python's UTF-8 mode. Without it, Python 3.12 reads and writes
    every text file in the ANSI code page (cp1252), and a story with ⏳ in it can't be saved;
    the mode makes files and pipes UTF-8 for the buddy and every library it uses, as on Linux.
    A frozen build (PyInstaller) has the mode built in, and never comes here."""
    import subprocess
    try:
        return subprocess.call([sys.executable, "-X", "utf8", "-m", "work_brahman", *sys.argv[1:]],
                               env={**os.environ, "BUDDY_COMMAND": os.path.abspath(sys.argv[0])})
    except KeyboardInterrupt:  # Ctrl+C reached the child too; it says what it stopped
        return 130


def main() -> int:
    if os.name == "nt" and not sys.flags.utf8_mode and not getattr(sys, "frozen", False):
        return _relaunch()
    _utf8()
    ap = argparse.ArgumentParser(prog="buddy", epilog=install.START,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=None,
                    help="default: ./buddy.toml if there is one, else ~/.config/work_brahman/buddy.toml")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")
    t = sub.add_parser("init", help="set the buddy up: language, a chat (Telegram and/or Lark), workspace",
                       description="Write ~/.config/work_brahman/buddy.toml and .env (chmod 600). Asks for "
                       "what no flag gives, unless --yes. Never writes over a setup: --force keeps backups.")
    t.add_argument("--language", choices=["zh-CN", "en"], help="what the buddy says in the chats (default zh-CN)")
    t.add_argument("--workspace", help="the folder chat turns run in (default ~)")
    t.add_argument("--telegram-token", help="the token @BotFather gives you for a new bot (/newbot)")
    t.add_argument("--lark-app-id", help="a Lark (international, open.larksuite.com) self-built app's "
                   "App ID, cli_... (the README says how to make one)")
    t.add_argument("--lark-app-secret", default="", help="that app's App Secret")
    t.add_argument("--feishu-app-id", help="a Feishu (China, open.feishu.cn) self-built app's App ID")
    t.add_argument("--feishu-app-secret", default="", help="that app's App Secret")
    t.add_argument("--port", type=int, default=8787, help="the web workbench's port")
    t.add_argument("--yes", action="store_true", help="ask nothing: flags and defaults only")
    t.add_argument("--force", action="store_true", help="start over; the old files are kept as backups")
    t = sub.add_parser("allow", help="pair an id the bot told you (tg:..., ou_...): it may use the bot")
    t.add_argument("id")
    t.add_argument("--chat", default="", help="its chat, for Lark (oc_...); a Telegram DM's is its id")
    t.add_argument("--name", default="buddy", help="the service's name, for the restart hint")
    t = sub.add_parser("config", help="a menu to change the setup: language, chats, tokens, pairing, port")
    t.add_argument("--offline", action="store_true", help="after saving, check without calling the chat apps")
    t.add_argument("--name", default="buddy", help="the service to restart after saving")
    t = sub.add_parser("doctor", help="what works, what is missing, and how to fix it")
    t.add_argument("--offline", action="store_true", help="don't call the chat apps")
    t.add_argument("--name", default="buddy", help="the service's name")
    t = sub.add_parser("service", help="keep `buddy serve` running: a systemd user unit (Linux)")
    t.add_argument("action", choices=["install"])
    t.add_argument("--name", default="buddy", help="the unit's name (default buddy.service)")
    t.add_argument("--enable", action="store_true", help="also enable and start it")
    t.add_argument("--dry-run", action="store_true", help="print the unit, write nothing")
    t.add_argument("--force", action="store_true", help="replace a unit; the old one is kept as a backup")
    sub.add_parser("serve", help="answer chat messages on every configured channel")
    sub.add_parser("jobs", help="list scheduled jobs")
    job = sub.add_parser("job", help="run one job and post the result")
    job.add_argument("name")
    duty = sub.add_parser("duty", help="run one check of a duty and post what changed")
    duty.add_argument("name")
    duty.add_argument("--act", default="", help="what an answer asked for, e.g. 'draft <PR key>'")
    ask = sub.add_parser("ask", help="ask your chats a question with buttons; the first answer wins")
    ask.add_argument("question")
    ask.add_argument("options", nargs="+", metavar="KEY=LABEL")
    sub.add_parser("bitable", help="sync runs, asks, PRs, threads to the Lark Base now; print its link")
    stories = sub.add_parser("stories", help="every upstream MR story, and whose turn it is")
    stories.add_argument("--json", action="store_true")
    family = sub.add_parser("family", help="post a family's scheduled message (the monthly report, ...)")
    family.add_argument("name")
    family.add_argument("--print", action="store_true", help="print it here instead of posting")
    short = sub.add_parser("short", help="a short story: work that waits on pipelines between its steps, "
                                         "one card in your chats, changed as it goes")
    tsub = short.add_subparsers(dest="action", required=True)
    t = tsub.add_parser("start", help="a new short story; prints its number")
    t.add_argument("title")
    t.add_argument("--mark", action="append", default=[], help="a mark expected (repeat, in order); "
                   "a fork: \"CI green? yes | no=fix, rerun\" (2-4 options, up to 3 steps each); "
                   "a loop: \"soak ×3\" or \"build × linux, mac, win\" (2-9 rounds); "
                   "an open loop, a retry: \"rerun ×?\" (one line per try, up to 20)")
    t.add_argument("--who", default="", help="who does the work, e.g. the session's name")
    t.add_argument("--note", default="")
    t = tsub.add_parser("mark", help="a mark happened (added if it was not planned)")
    t.add_argument("number", type=int)
    t.add_argument("name")
    t.add_argument("--status", default="done", choices=shorts.STATUSES)
    t.add_argument("--note", default="")
    t.add_argument("--took", help="for a fork: the option it went down")
    t.add_argument("--round", type=int, help="for a loop: the round, from 1 (default: the current one)")
    t = tsub.add_parser("plan", help="more marks expected")
    t.add_argument("number", type=int)
    t.add_argument("marks", nargs="+")
    t = tsub.add_parser("note", help="a line on the card that is not a mark")
    t.add_argument("number", type=int)
    t.add_argument("text")
    t = tsub.add_parser("ask", help="a question for the owner on the card; prints `question <id>`")
    t.add_argument("number", type=int)
    t.add_argument("question", nargs="?", default="", help="the question (with --fork: the fork's name)")
    t.add_argument("--option", action="append", default=[], help="a button to answer with (repeat)")
    t.add_argument("--fork", default="", help="a fork's name: its options are the buttons, the answer the branch")
    t.add_argument("--wait", action="store_true", help="block until it is answered, as `wait` does")
    t.add_argument("--timeout", type=float, help="with --wait: give up after this many seconds (exit 4)")
    t = tsub.add_parser("wait", help="block until a question is answered (0), a stop is asked (3), "
                                     "or --timeout seconds pass (4); needs no chat")
    t.add_argument("number", type=int)
    t.add_argument("question", type=int, nargs="?", help="its id (default: the last one asked)")
    t.add_argument("--timeout", type=float, help="seconds; 0 looks once; default: wait for ever")
    t = tsub.add_parser("done", help="the short story ended")
    t.add_argument("number", type=int)
    t.add_argument("--failed", action="store_true")
    t.add_argument("--note", default="")
    t = tsub.add_parser("check", help="stop asked? replies not read yet? (exit 3: stop); needs no chat")
    t.add_argument("number", type=int)
    t = tsub.add_parser("list", help="the short stories still running (--all: every one)")
    t.add_argument("--all", action="store_true")
    long = sub.add_parser("long", help="a long story: one agent session mirrored to a thread in your chats")
    lsub = long.add_subparsers(dest="action", required=True)
    t = lsub.add_parser("hook", help="a hook event on stdin (scripts/buddy-long hook)")
    t.add_argument("--engine", default="claude-code", choices=longs.ENGINES)
    t = lsub.add_parser("register", help="this terminal's Codex is a long story (scripts/buddy-long codex)")
    t.add_argument("--pid", type=int, required=True)
    t.add_argument("--title", default="")
    lsub.add_parser("list", help="every long story")
    topic = sub.add_parser("topic", help="topics: conversations beside the main one")
    tosub = topic.add_subparsers(dest="action", required=True)
    t = tosub.add_parser("close", help="close topics: the web drops their tabs, Telegram closes "
                         "its forum topics (Lark threads stay); nothing is deleted")
    t.add_argument("ids", nargs="+", help="topic ids, e.g. t4 t6")
    args = ap.parse_args()
    if args.cmd is None:  # `buddy` on its own: where things stand
        return _status(args.config)
    if args.cmd in ("init", "allow", "config", "doctor", "service"):  # setting up: no channel, no state
        return {"init": install.init, "allow": install.allow, "config": configure.config,
                "doctor": install.doctor, "service": install.service}[args.cmd](args)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.config = str(install.config_path(args.config))
    install.load_env(Path(args.config))
    cfg = tomllib.loads(Path(args.config).read_text()) if Path(args.config).exists() else {}
    text.set_language(cfg.get("buddy", {}).get("language"))
    jobs = cfg.get("jobs", {})
    state = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "work_brahman"
    _private(state)
    linked = Linked(cfg.get("me", {}).get("chats", []))
    home = linked.chats[0] if linked.chats else ""  # where a job or duty without chat_id posts
    duties = {name: DUTIES[name]({"chat_id": home} | d, state)
              for name, d in cfg.get("duties", {}).items() if name in DUTIES}
    families = {name: f(state, cfg) for name, f in FAMILIES.items()}

    if args.cmd == "stories":  # read-only, needs no channel
        if args.json:
            print(json.dumps({n: f.stories() for n, f in families.items()}, indent=1, ensure_ascii=False))
        else:
            print(families["upstream-mr"].markdown("all"))
        return 0
    if args.cmd == "family" and args.print:
        if args.name not in families:
            print(f"no family {args.name!r} (known: {', '.join(families)})", file=sys.stderr)
            return 2
        print(families[args.name].scheduled())
        return 0
    if args.cmd == "long" and args.action == "list":  # read-only, needs no channel
        print(longs.board(state))
        return 0
    if args.cmd == "long" and args.action == "register":  # before codex starts: no chat calls
        longs.register(state, pid=args.pid, cwd=os.getcwd(), title=args.title or os.path.basename(os.getcwd()),
                       sty=os.environ.get("STY", ""), window=os.environ.get("WINDOW", ""))
        return 0
    if args.cmd == "short" and args.action == "list":  # read-only, needs no channel
        print(shorts.board(state, everything=args.all))
        return 0
    if args.cmd == "short" and args.action == "check":  # polled while waiting: no chat calls
        if shorts.load(state, args.number) is None:
            print(f"no short story #{args.number}", file=sys.stderr)
            return 2
        return _news(state, args.number)
    if args.cmd == "short" and args.action == "wait":  # blocks on a file: no chat calls
        if shorts.load(state, args.number) is None:
            print(f"no short story #{args.number}", file=sys.stderr)
            return 2
        return _wait(state, args.number, args.question, args.timeout)
    if args.cmd == "jobs":
        for name, j in jobs.items():
            print(f"{name}\t{j.get('prompt', '')[:70]}")
        return 0

    engine = ClaudeCode(**cfg.get("claude", {}))
    channels = []
    if os.environ.get("FEISHU_APP_ID"):
        channels.append(Feishu(os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"],
                               cfg.get("domain", "feishu")))
    if os.environ.get("LARK_APP_ID"):  # a Lark app beside a Feishu one: its ids are lark:...
        if os.environ.get("FEISHU_APP_ID") and cfg.get("domain", "feishu") == "lark":
            print("LARK_APP_ID is a second Lark app: FEISHU_APP_ID is already on domain = \"lark\" "
                  "(set domain = \"feishu\" if that one is Feishu)", file=sys.stderr)
            return 2
        channels.append(Feishu(os.environ["LARK_APP_ID"], os.environ.get("LARK_APP_SECRET", ""),
                               "lark", prefix="lark:"))
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        channels.append(Telegram(os.environ["TELEGRAM_BOT_TOKEN"], state))
    if not channels:
        print("no channel: set TELEGRAM_BOT_TOKEN, LARK_APP_ID/LARK_APP_SECRET or "
              "FEISHU_APP_ID/FEISHU_APP_SECRET", file=sys.stderr)
        return 2
    workspace = os.path.expanduser(cfg.get("workspace", "~"))

    if args.cmd == "topic" and args.action == "close":
        bad = 0
        for tid in args.ids:
            tp = topics.close(state, tid)
            if tp is None:
                print(f"{tid}: no such topic", file=sys.stderr)
                bad = 1
                continue
            shut = [chat for chat, handle in tp["threads"].items()
                    for c in channels if c.owns(chat) and hasattr(c, "close_topic") and c.close_topic(chat, handle)]
            print(f"{tid} closed ({tp['name']}){': ' + ', '.join(shut) if shut else ''}")
        return bad

    if args.cmd == "serve":
        # BUDDY_ALLOWED_OPEN_IDS is the older, Lark-only name; both are read.
        ids = os.environ.get("BUDDY_ALLOWED_IDS", "") + "," + os.environ.get("BUDDY_ALLOWED_OPEN_IDS", "")
        allowed = {x.strip() for x in ids.split(",") if x.strip()}
        commands = {name: c for d in [*duties.values(), *families.values()]
                    for name, c in d.commands().items()}
        port = cfg.get("web", {}).get("port")
        chat = None
        if port and linked.chats:  # the web joins my chats: same session, mirrored both ways
            chat = WebChat(state)
            channels.append(chat)
            linked.chats.append(CHAT)
            allowed.add(CHAT)  # the person at this machine; the server only listens on 127.0.0.1
        scheduler = Scheduler(cfg, str(Path(args.config).resolve()), state)
        scheduler.start()
        families["short-story"].channels = channels  # /short N <text> changes the cards
        long = long_engines(cfg)  # a long story may write ([long]); the chat and duties stay as [claude]
        families["long-story"].connect(channels, long, linked, workspace)  # /long new
        bot = Bot(channels, engine, Sessions(state / "sessions.json"), workspace, allowed, commands,
                  state, linked, scheduler)
        bot.long_engines = long
        if port:  # the workbench, on 127.0.0.1 only
            Web(int(port), state, bot, scheduler, cfg, home, chat, families).start()
        if "bitable" in cfg and os.environ.get("FEISHU_APP_ID"):  # the Lark Base workbench
            Bitable(os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"],
                    feishu_domain(cfg), state, cfg).start()
        bot.serve()
        return 0

    if cfg.get("web", {}).get("port") and linked.chats:
        # The web chat lives in `buddy serve`; this process posts to it through the outbox.
        channels.append(WebRelay(state))
        linked.chats.append(CHAT)

    if args.cmd == "bitable":
        if not os.environ.get("FEISHU_APP_ID"):
            print("the Base needs FEISHU_APP_ID/FEISHU_APP_SECRET", file=sys.stderr)
            return 2
        b = Bitable(os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"], feishu_domain(cfg), state, cfg)
        print(f"{b.sync()} rows sent; {b.url()}")
        return 0

    if args.cmd == "long":  # the hook: post to the story's threads, never fail the session
        try:
            return run_long_hook(state, channels, linked.of(home), args.engine)
        except Exception:
            logging.exception("long story hook failed")
            return 0

    if args.cmd == "short":
        try:
            return _short(args, state, channels, linked.of(home))
        except ValueError as e:  # a fork that breaks the rules, an option it doesn't have
            print(e, file=sys.stderr)
            return 2

    if args.cmd == "ask":  # try the buttons: the answer shows up in every chat and in asks/
        options = [o.split("=", 1) for o in args.options]
        if any(len(o) != 2 for o in options):
            print("options look like KEY=LABEL, e.g. A='keep ours'", file=sys.stderr)
            return 2
        a = asks.create(state, channels, linked.of(home), args.question, title=text.t("ask.title"),
                        options=options)
        print(f"ask {a['id']} posted to {', '.join(a['copies']) or 'no chat'}")
        return 0 if a["copies"] else 1

    if args.cmd == "duty":
        return run_duty(duties, args.name, channels, state, linked, args.act)

    if args.cmd == "family":
        return run_family(families, args.name, cfg.get("families", {}).get(args.name, {}),
                          channels, state, linked, home)

    j = jobs.get(args.name)
    if j is None:
        print(f"no job {args.name!r} in {args.config}", file=sys.stderr)
        return 2
    chats = linked.of(j.get("chat_id") or home)
    if not any(c.owns(chat) for chat in chats for c in channels):
        print(f"no configured channel for {chats!r}", file=sys.stderr)
        return 2
    run = from_env(state, "job", args.name)
    r = engine.run(j["prompt"], cwd=os.path.expanduser(j.get("cwd", workspace)))
    run.step("agent", engine="claude-code", session_id=r.session_id, ok=r.ok, denied=r.denied,
             cost=r.cost)
    note = text.t("denied", tools=", ".join(r.denied)) if r.denied else ""
    if r.ok:
        posted = post_all(channels, chats, r.text, title=text.workflow(args.name),
                          subtitle=text.stamp("day_date_time"), note=note)
    else:  # a failure asks what to do; ↻ runs the job again through `buddy serve`
        a = asks.create(state, channels, chats, r.text, title=text.t("ask.job_failed", name=text.workflow(args.name)),
                        options=[["retry", text.t("ask.run_again")], ["leave", text.t("btn.leave")]],
                        then={"retry": ["job", args.name]}, run=run.id, failed=True, note=note)
        posted = {c: c in a["copies"] for c in chats}
    run.step("post", chats=posted, ok=all(posted.values()))
    if r.ok:
        run.finish("ok", result=tail(r.text))
    else:
        run.finish("timeout" if r.text.startswith("timed out") else "failed",
                   error=r.text[:300], output=tail(r.detail))
    return 0 if r.ok else 1


def run_family(families: dict, name: str, fcfg: dict, channels: list, state: Path,
               linked: Linked, home: str) -> int:
    """Post a family's scheduled message, with a run record like a duty's."""
    f = families.get(name)
    if f is None:
        print(f"no family {name!r} (known: {', '.join(families)})", file=sys.stderr)
        return 2
    run = from_env(state, "family", name)
    chats = linked.of(fcfg.get("chat_id") or home)
    if not any(c.owns(chat) for chat in chats for c in channels):
        print(f"no configured channel for {chats!r}", file=sys.stderr)
        run.finish("failed", error=f"no channel for {chats}")
        return 2
    markdown = f.scheduled()
    run.step("render", chars=len(markdown))
    posted = post_all(channels, chats, markdown, title=text.workflow(name), subtitle=text.stamp("day_date"))
    ok = all(posted.values())
    run.step("post", chats=posted, ok=ok)
    run.finish("ok" if ok else "failed", result=tail(markdown))
    return 0 if ok else 1


def run_duty(duties: dict, name: str, channels: list, state: Path, linked: Linked,
             act: str = "") -> int:
    """One check, or what an answer asked for (act); post only when there is something to say.

    A duty may leave questions in d.offers: (markdown, title, options, then). They are
    posted as asks after its message; `buddy serve` runs what the answer asks for.
    """
    d = duties.get(name)
    if d is None:
        print(f"no duty {name!r} under [duties] in buddy.toml (known: {', '.join(DUTIES)})", file=sys.stderr)
        return 2
    run = from_env(state, "duty", name)
    chats = linked.of(d.chat_id)
    if act:
        try:
            changes = d.act(act)
        except Exception as e:  # a git or forge error: say it, keep the black box
            changes = f"⚠️ {act}: {e}"
            run.finish("failed", error=str(e)[:300], output=tail(str(e)))
        run.step("act", act=act, said=changes[:200])
    else:
        changes, _ = d.tick()
        run.step("check", changes=changes.count("\n- ") + changes.startswith("- "))
    for markdown, title, options, then in getattr(d, "offers", []):
        same = asks.still_open(state, title)
        if same:  # asked before and not answered yet: that card stands, no second one
            run.step("ask", ask=same["id"], title=title, again=True)
            continue
        a = asks.create(state, channels, chats, markdown, title=title, options=options, then=then, run=run.id)
        run.step("ask", ask=a["id"], title=title)
    if not changes:
        if run.rec["status"] == "running":
            run.finish("ok", result="no changes")
        return 0
    if not any(c.owns(chat) for chat in chats for c in channels):
        print(f"no configured channel for {chats!r}", file=sys.stderr)
        run.finish("failed", error=f"no channel for {chats}")
        return 2
    posted = post_all(channels, chats, changes, title=text.workflow(name), subtitle=text.stamp("day_time"),
                      note="" if act else getattr(d, "note", ""))
    ok = all(posted.values())
    run.step("post", chats=posted, ok=ok)
    if run.rec["status"] == "running":
        run.finish("ok" if ok else "failed", result=tail(changes))
    return 0 if ok and run.rec["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
