"""`buddy tui`: the buddy's doctor and setup in one terminal screen (the owner, 2026-11-12: "make
an all-in-one tui focused on doctor and config, and keep doctor for batch use"). Tab moves
between four views:

  Doctor  what works, what is missing, how to fix each: the buddy's checks and the peer checks.
          It opens with the quick ones (no call to the chat apps); r runs them all.
  Ping    through the running buddy: 🏓 ping into each linked chat (Feishu, Lark, Telegram,
          web); answer pong there and the row turns ✅ both ways. Who wrote without being
          paired, with the `buddy allow` line.
  Config  what `buddy config` changes: language, workspace, Telegram, Lark, Feishu, who is
          paired, the linked chats, the web port. ↑↓ choose · Enter change · w write
  Peers   the peer build's peers.toml (buddy peer): a add · e edit · d delete · s this side ·
          c check one (a signed ping, answered signed) · w write
  Log     the peer log: what came, what was refused and why, what was sent, each check

Who wrote to the bot and isn't paired shows on every view; P pairs them (written at once,
with a backup) and offers to restart the buddy. Otherwise nothing is written until `w`, and then
with a backup; quitting with changes unwritten asks
first. A config change takes effect when the buddy starts again: the TUI says so and never
restarts it. `buddy doctor [--json] [--peers]` stays the command for scripts and cron. The
same views are the workbench's Setup tab; both go through settings.py. What is shown comes
from `Model` and `body()` without curses (tested without a terminal); `run()` draws and reads
keys only.
"""

from __future__ import annotations

import copy
import os
import time
import unicodedata
from pathlib import Path

from . import configure, settings
from . import peer as P

VIEWS = ("Doctor", "Ping", "Config", "Peers", "Log")
KEYS = {"Doctor": "r run every check (calls the chat apps) · P pair who wrote · Tab next view · q quit",
        "Ping": "↑↓ choose · Enter send a ping (answer pong in that chat) · r refresh · Tab · q quit",
        "Config": "↑↓ choose · Enter change · p pair who wrote · w write · Tab · q quit",
        "Peers": "↑↓ choose · a add · e edit · d delete · s this side · c check · w write · Tab · q quit",
        "Log": "↑↓ PgUp PgDn scroll · Tab · q quit"}
MARK = {True: "✅", False: "❌", None: "⚠ "}  # two columns each; ⚠ without U+FE0F, whose width terminals disagree on


def width(ch: str) -> int:
    """Columns a character takes in a terminal: 2 for wide (CJK, ✅ ❌), 0 for a combining mark."""
    if unicodedata.combining(ch) or ch in "\ufe0f\u200d":
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def wrap(line: str, cols: int, rows: int = 2) -> list[str]:
    """line over at most rows rows of cols columns (the status: a path and a restart hint)."""
    out = []
    while line and len(out) < rows:
        part = fit(line, cols)
        out.append(part)
        line = line.replace("\ufe0f", "")[len(part):]
    return out


def fit(line: str, cols: int) -> str:
    """line cut to cols terminal columns (curses' addnstr counts characters, not columns, so a
    line with ✅ or Chinese ran into the next row)."""
    out, used = [], 0
    for ch in line.replace("\ufe0f", ""):
        w = width(ch)
        if used + w > cols:
            break
        out.append(ch)
        used += w
    return "".join(out)


class Model:
    def __init__(self, path: Path, env=None, pinger=None):
        self.path = path
        self.env = env if env is not None else os.environ
        self.pinger = pinger
        self.before = configure.load(path) if path.exists() else None
        self.s = copy.deepcopy(self.before)
        self.psaved = P.load(self.env)
        self.pcfg = copy.deepcopy(self.psaved)
        self.view, self.csel, self.psel, self.scroll = 0, 0, 0, 0
        self.status, self.checks, self.doctor_rows, self.full = "", {}, [], False
        self.cnotes: list[str] = []  # what the last config write said (backups, the restart)
        self.ping_state: dict | None = None
        self.gsel = 0
        self.log = settings.log(self.env)

    # what changed
    @property
    def config_dirty(self) -> bool:
        return self.s != self.before

    @property
    def peers_dirty(self) -> bool:
        return self.pcfg != self.psaved

    @property
    def dirty(self) -> bool:
        return self.config_dirty or self.peers_dirty

    def refresh(self) -> None:
        self.log = settings.log(self.env)

    # Doctor
    def run_doctor(self, offline: bool) -> None:
        self.doctor_rows = settings.doctor(self.path, offline=offline, pinger=self.pinger)
        self.full = not offline
        self.refresh()

    # Ping: through the running buddy's workbench (only it holds the chats' connections)
    def _bench(self, path: str, body: dict | None = None) -> dict:
        import json
        import urllib.request
        port = (self.s or {}).get("port", 8787)
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="GET" if body is None else "POST",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Buddy": "1"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())

    def load_pings(self) -> str:
        try:
            s = self._bench("/api/setup")
        except OSError as e:
            self.ping_state = None
            return f"the buddy isn't answering on its web port ({e}): is buddy.service running?"
        self.ping_state = {k: s.get(k) for k in ("chats", "pings", "refused")}
        return ""

    def send_ping(self, chat: str) -> str:
        try:
            r = self._bench("/api/setup/ping", {"chat": chat})
        except OSError as e:
            return f"the buddy isn't answering: {e}"
        self.load_pings()
        return f"ping {r['code']} sent to {chat}: answer pong there" if r.get("ok") else f"❌ {r.get('why')}"

    # Config
    @property
    def fields(self) -> list[dict]:
        return settings.view(self.s) if self.s else []

    def set(self, key: str, value) -> str:
        why = settings.change(self.s, key, value)
        return why or f"{key} changed: w writes it"

    @property
    def state(self) -> Path:
        return Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "work_brahman"

    def refused(self) -> list[dict]:
        """Who wrote to the bot and was refused, not paired since (pings.py), newest first."""
        from . import pings
        return pings.refused(self.state, set(self.s["allowed"]) if self.s else set())

    def pair(self, n: int) -> str:
        """The pairing helper: the n-th who wrote (1 = newest) into paired people and linked
        chats, as `buddy allow <id> --chat <chat>` does; w writes it, a restart uses it."""
        who = self.refused()
        if not 1 <= n <= len(who):
            return "nobody with that number"
        r = who[n - 1]
        self.set("allowed", self.s["allowed"] + [r["sender"]])
        if r["chat"] and r["chat"] not in self.s["chats"]:
            self.set("chats", self.s["chats"] + [r["chat"]])
        return f"{r['sender']} paired (its chat {r['chat']} linked): w writes it, then restart the buddy"

    def banner(self) -> str:
        """Who wrote to the bot and isn't paired, on every view: P pairs them."""
        who = self.refused()
        if not who:
            return ""
        r = who[0]
        more = f" (+{len(who) - 1} more in Config)" if len(who) > 1 else ""
        return f"⚠ {r['sender']} wrote on {r['channel']} ({r['times']}×, last {r['last'][11:]}), not paired: P pairs it, X ignores it{more}"

    def quick_pair(self) -> str:
        """P: pair who wrote last (written at once, with a backup); the restart is asked after."""
        who = self.refused()
        if not who:
            return "nobody has written unpaired"
        if self.config_dirty:
            return "Config has unwritten changes: w writes them first (or q drops them)"
        ok, notes = settings.pair(self.path, self.state, who[0]["sender"])
        if ok:
            self.before = configure.load(self.path)
            self.s = copy.deepcopy(self.before)
            settings.reload_env(self.path, os.environ)
        self.cnotes = notes
        return " · ".join(notes[:1] if ok else notes)

    def write_config(self) -> str:
        ok, notes = settings.write(self.path, self.s, self.before)
        self.cnotes = notes
        if ok:
            self.before = copy.deepcopy(self.s)
            settings.reload_env(self.path, os.environ)
        return " · ".join(notes)

    # Peers
    @property
    def names(self) -> list[str]:
        return sorted(self.pcfg["points"])

    def put(self, name: str, url: str, note: str, old: str = "") -> str:
        """Add or change a peer (old: its name before an edit). "" or what is wrong."""
        new = copy.deepcopy(self.pcfg)
        if old and old != name:
            new["points"].pop(old, None)
        if name in new["points"] and name != old:
            return f"{name} is there already"
        new["points"][name] = {"url": url.strip(), "note": note.strip()}
        why = P.validate(new)
        if why:
            return why
        self.pcfg = new
        self.psel = self.names.index(name)
        return ""

    def delete(self, name: str) -> None:
        self.pcfg["points"].pop(name, None)
        self.psel = max(0, min(self.psel, len(self.names) - 1))

    def side(self, name: str, bind: str, port: str) -> str:
        new = copy.deepcopy(self.pcfg)
        new.update(name=name.strip(), bind=bind.strip() or "127.0.0.1")
        try:
            new["port"] = int(port)
        except ValueError:
            return "the port is a number"
        why = P.validate(new)
        if why:
            return why
        self.pcfg = new
        return ""

    def write_peers(self) -> str:
        ok, said = settings.save_peers(self.pcfg, self.env)
        if ok:
            self.psaved = copy.deepcopy(self.pcfg)
            self.refresh()
        return said

    def check(self, name: str) -> str:
        r = settings.check_peer(name, self.pcfg["points"][name]["url"], self.env, self.pinger)
        self.checks[name] = r
        self.refresh()
        return f"{name}: " + (f"✅ same secret, clocks {abs(r['skew'])} s apart, {r['ms']} ms" if r["ok"] else f"❌ {r['why']}")


def body(m: Model, height: int = 20) -> list[str]:
    """The lines under the header for the current view."""
    view = VIEWS[m.view]
    if view == "Doctor":
        if not m.doctor_rows:
            return ["r runs the checks"]
        out = [("every check" if m.full else "the quick checks (no call to the chat apps): r runs them all"), ""]
        for ok, what, how in m.doctor_rows:
            out.append(f"{MARK[ok]} {what}")
            if how:
                out.append(f"     → {how}")
        bad = sum(1 for r in m.doctor_rows if r[0] is False)
        out += ["", f"{bad} ❌ to fix: each says how (→)" if bad else "no ❌"]
        return out
    if view == "Ping":
        if m.ping_state is None:
            return ["r asks the running buddy (its workbench) for the chats"]
        lines = ["🏓 ping into a chat through its own app; answer pong there: ✅ both ways", ""]
        chats = m.ping_state.get("chats") or []
        if not chats:
            lines.append("no linked chats yet (buddy.toml [me] chats): pair one, then restart")
        for i, c in enumerate(chats):
            p = (m.ping_state.get("pings") or {}).get(c["chat"])
            sent = "–" if not p else (f"✅ {p['code']}" if p.get("ok") else f"❌ {p.get('why', '')}")
            back = "" if not p or not p.get("ok") else (f"pong ✅ {p['pong_at'][11:]}" if p.get("pong_at") else "waiting for pong")
            lines.append(f"{'›' if i == m.gsel else ' '} {c['channel'] or '?':<9} {c['chat']:<40} {sent}  {back}")
        refused = m.ping_state.get("refused") or []
        if refused:
            lines += ["", "wrote, but not paired:"]
            for r in refused:
                lines += [f"  ⚠ {r['sender']} on {r['channel']} ({r['times']}×, last {r['last']})", f"     → {r['allow']}"]
        return lines
    if view == "Config":
        if m.s is None:
            return [f"not set up: {m.path} is missing; run `buddy init` first"]
        lines = [f"{m.path}", ""]
        for i, f in enumerate(m.fields):
            star = "*" if m.s[f["key"]] != m.before[f["key"]] else " "
            lines.append(f"{'›' if i == m.csel else ' '}{star} {f['label']:<14} {f['shown']}")
        if m.config_dirty:
            lines += ["", "* changed, not written yet: w writes it (with a backup); the buddy uses it after a restart"]
        elif m.cnotes:
            lines += [""] + m.cnotes
        who = m.refused()
        lines += ["", "pair: write anything to the bot in the new app; it shows here; p pairs it"]
        for i, r in enumerate(who, 1):
            lines.append(f"  {i}. {r['sender']} on {r['channel']} ({r['times']}×, last {r['last']}) chat {r['chat']}")
        if not who:
            lines.append("  nobody has written unpaired")
        return lines
    if view == "Peers":
        c = m.pcfg
        lines = [f"this side: {c['name'] or '(no name)'} · serve on {c['bind']}:{c['port']}"
                 + ("" if c["bind"] in ("127.0.0.1", "::1", "localhost") else "  ⚠️  reachable from the network"), ""]
        if not m.names:
            lines.append("no peers yet: a adds one (a name, its address http://host:8790, a note)")
        for i, k in enumerate(m.names):
            v, r = c["points"][k], m.checks.get(k)
            mark = "  " if r is None else ("✅" if r["ok"] else "❌")
            lines.append(f"{'›' if i == m.psel else ' '} {mark} {k:<14} {v['url']:<28} {v['note']}")
        if m.peers_dirty:
            lines += ["", "changes not written yet: w writes them"]
        return lines
    lines = m.log or ["nothing logged yet"]
    top = max(0, len(lines) - height - m.scroll)
    return lines[top:top + height]


def header(m: Model) -> str:
    tabs = "  ".join(f"[{v}]" if i == m.view else f" {v} " for i, v in enumerate(VIEWS))
    return f"buddy · {tabs}" + ("   (unwritten changes)" if m.dirty else "")


def run(path: Path) -> int:  # pragma: no cover - the terminal part
    try:
        import curses
    except ImportError:
        print("no curses here: use the workbench's Setup tab, or `pip install windows-curses`")
        return 2

    def ask(scr, prompt: str, default: str = "", secret: bool = False) -> str | None:
        h, w = scr.getmaxyx()
        text = f"{prompt} [{default}]: " if default else f"{prompt}: "
        scr.move(h - 1, 0)
        scr.clrtoeol()
        scr.addstr(h - 1, 0, fit(text, w - 1))
        if not secret:
            curses.echo()
        curses.curs_set(1)
        try:
            got = scr.getstr(h - 1, min(w - 2, sum(map(width, text))), 300).decode(errors="replace")
        except KeyboardInterrupt:
            got = None
        finally:
            curses.noecho()
            curses.curs_set(0)
        if got is None:
            return None
        return got.strip() or default

    def busy(scr, m, h, w, what):
        m.status = what
        for row in (h - 3, h - 2):
            scr.move(row, 0)
            scr.clrtoeol()
        scr.addstr(h - 3, 0, fit(what, w - 1))
        scr.refresh()

    def config_key(scr, m, k, h, w):
        f = m.fields[m.csel] if m.fields else None
        if k == curses.KEY_UP:
            m.csel = max(0, m.csel - 1)
        elif k == curses.KEY_DOWN:
            m.csel = min(len(m.fields) - 1, m.csel + 1)
        elif k in (10, 13, curses.KEY_ENTER, ord("e")) and f:
            key, kind = f["key"], f["kind"]
            if kind == "secret":
                v = ask(scr, f"{f['label']} token (hidden; empty keeps it, - removes it)", secret=True)
            elif kind == "app":
                app_id = ask(scr, f"{f['label']} App ID (empty keeps it, - removes the app)")
                if app_id is None:
                    return
                sec = "" if app_id == "-" else ask(scr, f"{f['label']} App Secret (hidden; empty keeps it)", secret=True)
                v = None if sec is None else [app_id, sec]
            elif kind == "list":
                v = ask(scr, f"{f['label']}, comma separated", ", ".join(f["value"]))
            elif kind == "choice":
                v = ask(scr, f"{f['label']}: " + " or ".join(settings.LANGUAGES), f["value"])
            else:
                v = ask(scr, f["label"], str(f["value"]))
            if v is not None:
                m.status = m.set(key, v)
        elif k == ord("p"):
            who = m.refused()
            n = ask(scr, f"pair which one, 1-{len(who)}", "1") if who else None
            m.status = m.pair(int(n)) if n and n.isdigit() else ("nobody has written unpaired" if not who else "")
        elif k == ord("w"):
            m.status = m.write_config()

    def peers_key(scr, m, k, h, w):
        cur = m.names[m.psel] if m.names else ""
        if k == curses.KEY_UP:
            m.psel = max(0, m.psel - 1)
        elif k == curses.KEY_DOWN:
            m.psel = min(len(m.names) - 1, m.psel + 1) if m.names else 0
        elif k in (ord("a"), ord("e")) and (k == ord("a") or cur):
            old = cur if k == ord("e") else ""
            v = m.pcfg["points"].get(old, {"url": "http://", "note": ""})
            name = ask(scr, "peer name", old)
            url = name and ask(scr, "its address", v["url"])
            note = url and ask(scr, "a note", v["note"])
            if name and url and note is not None:
                m.status = m.put(name, url, note, old) or f"{name}: w writes it"
        elif k == ord("d") and cur:
            if (ask(scr, f"delete {cur}? y/N") or "n").lower() == "y":
                m.delete(cur)
                m.status = f"{cur} deleted: w writes it"
        elif k == ord("s"):
            c = m.pcfg
            name = ask(scr, "this side's name", c["name"])
            bind = name and ask(scr, "serve listens on", c["bind"])
            port = bind and ask(scr, "port", str(c["port"]))
            if name and bind and port:
                m.status = m.side(name, bind, port) or "this side changed: w writes it"
        elif k == ord("c") and cur:
            busy(scr, m, h, w, f"checking {cur} …")
            m.status = m.check(cur)
        elif k == ord("w"):
            m.status = m.write_peers()

    def main(scr):
        curses.curs_set(0)
        curses.use_default_colors()
        scr.timeout(5000)  # the log is read again every 5 s
        m = Model(path)
        h, w = scr.getmaxyx()
        busy(scr, m, h, w, "the quick checks …")
        m.run_doctor(offline=True)
        m.status = ""
        while True:
            h, w = scr.getmaxyx()
            scr.erase()
            scr.addstr(0, 0, fit(header(m), w - 1), curses.A_BOLD)
            if m.banner():
                scr.addstr(1, 0, fit(m.banner(), w - 1), curses.A_REVERSE)
            for i, line in enumerate(body(m, h - 5)[: h - 5]):
                scr.addstr(2 + i, 0, fit(line, w - 1))
            for i, line in enumerate(wrap(m.status, w - 1)):
                scr.addstr(h - 3 + i, 0, line)
            scr.addstr(h - 1, 0, fit(KEYS[VIEWS[m.view]], w - 1), curses.A_DIM)
            k = scr.getch()
            if k == -1:
                m.refresh()
                if VIEWS[m.view] == "Ping" and m.ping_state is not None:
                    m.load_pings()  # a pong typed in a chat shows within 5 s
                continue
            m.status = ""
            if k == ord("q"):  # not Esc: a stray escape sequence would quit
                if m.dirty and (ask(scr, "changes not written: quit anyway? y/N") or "n").lower() != "y":
                    continue
                return 0
            if k == ord("X") and m.refused():  # ignore who wrote last: off the list until it writes again
                who = m.refused()[0]["sender"]
                from . import pings
                pings.forget(m.state, who)
                m.status = f"{who} ignored"
                continue
            if k == ord("P"):  # the quick pair, from any view
                m.status = m.quick_pair()
                if m.status.startswith("paired") and (ask(scr, "restart the buddy now, so it uses this? y/N") or "n").lower() == "y":
                    m.status = settings.restart_soon(delay=0.2)
                continue
            if k == 9:  # Tab
                m.view = (m.view + 1) % len(VIEWS)
            elif k == curses.KEY_BTAB:
                m.view = (m.view - 1) % len(VIEWS)
            elif VIEWS[m.view] == "Doctor" and k == ord("r"):
                busy(scr, m, h, w, "every check: calling the chat apps and the peers …")
                m.run_doctor(offline=False)
                m.status = "done"
            elif VIEWS[m.view] == "Ping":
                chats = (m.ping_state or {}).get("chats") or []
                if k == ord("r") or m.ping_state is None:
                    m.status = m.load_pings() or "refreshed"
                elif k == curses.KEY_UP:
                    m.gsel = max(0, m.gsel - 1)
                elif k == curses.KEY_DOWN:
                    m.gsel = min(len(chats) - 1, m.gsel + 1) if chats else 0
                elif k in (10, 13, curses.KEY_ENTER) and chats:
                    busy(scr, m, h, w, f"sending a ping to {chats[m.gsel]['chat']} …")
                    m.status = m.send_ping(chats[m.gsel]["chat"])
            elif VIEWS[m.view] == "Config" and m.s is not None:
                config_key(scr, m, k, h, w)
            elif VIEWS[m.view] == "Peers":
                peers_key(scr, m, k, h, w)
            elif VIEWS[m.view] == "Log":
                step = {curses.KEY_UP: 1, curses.KEY_DOWN: -1, curses.KEY_PPAGE: h - 5, curses.KEY_NPAGE: -(h - 5)}.get(k, 0)
                m.scroll = max(0, min(max(0, len(m.log) - (h - 5)), m.scroll + step))

    from . import install
    install.load_env(path)  # the chat tokens and BRAHMAN_PEER_SECRET, as the buddy has them
    if os.environ.get(P.ENV):
        print(P.WARNING)
        time.sleep(1.0)
    return curses.wrapper(main)
