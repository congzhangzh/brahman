"""Chat message -> one agent turn -> reply. One agent session per chat, on any channel."""

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import asks, long_live, long_stories as longs, pings, procs, short_stories as shorts, teams, topics
from .channel import Channel, Incoming, Linked, post_all
from .engine import Codex, Engine
from .runs import Run, note_step, tail
from .text import app, join, t, task, tn, workflow

log = logging.getLogger("buddy")

STOP_WORDS = {"/stop", "■ STOP", "stop", "STOP"}  # and the ■ STOP key's label (text.py)
CANCEL_WORDS = {"cancel", "/cancel", "取消", "算了"}  # a reply to /long's "what should it do?"


def _touch(path: Path) -> None:
    """Mark when the owner last wrote from a chat; `speak` compares it with the desktop's
    last input, and stays silent while the phone is the newer."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError as e:
        log.warning("could not mark %s: %s", path, e)


class Sessions:
    """chat_id -> agent session id, saved to disk so a restart resumes the same sessions."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.map = json.loads(path.read_text()) if path.exists() else {}

    def get(self, chat: str) -> str | None:
        return self.map.get(chat)

    def set(self, chat: str, session_id: str | None) -> None:
        with self.lock:
            if session_id:
                self.map[chat] = session_id
            else:
                self.map.pop(chat, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.map, indent=1))
            os.replace(tmp, self.path)


class Bot:
    def __init__(self, channels: list[Channel], engine: Engine, sessions: Sessions,
                 workspace: str, allowed: set[str], commands: dict | None = None,
                 state: Path | None = None, linked: Linked | None = None, scheduler=None):
        self.channels, self.engine, self.sessions = channels, engine, sessions
        self.workspace = workspace
        self.allowed = allowed
        # Duty commands: "/name" -> (help line, fn(argument) -> Markdown answer).
        self.commands = commands or {}
        self.state = state  # where run records go; None: keep none
        self.linked = linked or Linked()  # one person's chats: shared session, mirrored
        self.scheduler = scheduler  # paused by STOP; runs what an answer asks for
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.guard = threading.Lock()
        self.chat_locks: dict[str, threading.Lock] = {}
        self.seen: OrderedDict[str, None] = OrderedDict()
        self.hinted: dict[tuple[int, str], float] = {}  # (story, chat) -> when the thread hint was given
        self.tried: set[tuple[str, str]] = set()  # (topic, chat) a thread was tried for
        self.long_prompts: dict[tuple[str, str], tuple[str, str, str]] = {}  # (chat, prompt) -> (engine, folder, mode)
        self.long_engines = {"claude-code": engine, "codex": Codex()}  # serve sets [long]'s (engine.long_engines)
        self.team_opts = None  # serve sets [families.team] (teams.settings); None: the defaults

    def serve(self) -> None:
        """Listen on every channel; the first one keeps the main thread."""
        if not self.allowed:
            log.warning("BUDDY_ALLOWED_IDS is empty: every sender is refused and told their id")
        for ch in self.channels[1:]:
            threading.Thread(target=self._listen, args=(ch,), name=ch.name, daemon=True).start()
        self._listen(self.channels[0])

    def _listen(self, ch: Channel) -> None:
        log.info("listening on %s", ch.name)
        ch.listen(lambda msg: self.on_message(ch, msg))

    def on_message(self, ch: Channel, msg: Incoming) -> None:
        """Runs on the channel's thread: check, de-duplicate, hand off, return."""
        if self._seen_before(f"{msg.chat}/{msg.message_id}"):
            return
        if msg.sender not in self.allowed:
            log.warning("refused message from %s in %s (%s)", msg.sender, msg.chat, ch.name)
            if self.state:  # the Setup tab and doctor show it, with its `buddy allow` line
                pings.refuse(self.state, ch.name, msg.sender, msg.chat)
            # Nobody of this app paired yet (a Feishu added beside a paired Lark and Telegram,
            # 2026-11-12): tell the sender how to pair. Someone is: a stranger gets silence.
            if not pings.paired_here(self.allowed, msg.sender):
                self.pool.submit(ch.reply, msg,
                                 t("bot.not_paired", sender=msg.sender,
                                   chat="" if msg.chat == msg.sender else f" --chat {msg.chat}"))
            return
        if self.state and ch.name != "web" and msg.chat not in self.linked.chats:
            pings.unlinked_seen(self.state, ch.name, msg.sender, msg.chat)  # paired, chat not linked
        if self.state and ch.name != "web":  # the owner is on the phone: `speak` goes quiet
            _touch(self.state.parent / "speak" / "remote")
        if msg.action:
            self.pool.submit(self._press, ch, msg)
            return
        if msg.text is None:
            self.pool.submit(ch.reply, msg, t("bot.text_only"))
            return
        if self.state and (p := pings.pong(self.state, msg.chat, msg.text)):  # the Setup tab's ping
            self.pool.submit(ch.reply, msg, t("bot.pong", code=p["code"], channel=ch.name))
            return
        if msg.text.strip() in STOP_WORDS | {t("btn.stop_key")}:  # not queued behind anything: a stop must stop now
            threading.Thread(target=self._safe, args=(self.stop, ch, msg), daemon=True).start()
            return
        self.pool.submit(self._handle, ch, msg)

    HINT_RECENT = 2 * 3600  # a long story that spoke in this chat within this long may be what was meant
    HINT_EVERY = 3600  # one hint per story and chat per this long, not one per message

    def _thread_hint(self, msg: Incoming) -> str | None:
        """Typed in the main chat while a live long story talks in a thread of it (2026-11-12, on
        Feishu: "I typed directly instead of replying; when I reply, the agent works"): one line
        says where it went and how to reach the story. None when no story fits, or said lately."""
        if not self.state:
            return None
        from datetime import datetime
        now = time.time()
        for s in sorted(longs.all_stories(self.state), key=lambda s: s["id"], reverse=True):
            if s["status"] != "live" or msg.chat not in s.get("threads", {}):
                continue
            last = s["events"][-1]["at"] if s.get("events") else s.get("started", "")
            try:
                ago = now - datetime.fromisoformat(last).timestamp()
            except ValueError:
                continue
            key = (s["id"], msg.chat)
            if ago > self.HINT_RECENT or now - self.hinted.get(key, 0) < self.HINT_EVERY:
                continue
            self.hinted[key] = now
            return t("long.thread_hint", story=longs.label(s))
        return None

    def _handle(self, ch: Channel, msg: Incoming) -> None:
        chat, text = self.linked.key(msg.chat), msg.text.strip()
        try:
            n = shorts.find(self.state, msg.chat, msg.reply_to) if msg.reply_to and self.state else None
            if n:  # an answer to a short story's card or its 💬 Reply: for the executor
                shorts.reply(self.state, self.channels, n, text, where=ch.name, who=msg.sender)
                return ch.reply(msg, t("short.sent", n=n))
            team = teams.find(self.state, msg.chat, msg.reply_to) if msg.reply_to and self.state else None
            if team:  # an answer to a team's card or its 💬 Reply: for its RM
                teams.reply(self.state, self.channels, team, text, where=ch.name, who=msg.sender,
                            opts=self.team_opts)
                return ch.reply(msg, t("team.sent", team=team))
            pending = self.long_prompts.pop((msg.chat, msg.reply_to), None) if msg.reply_to else None
            if pending and text.lower() in CANCEL_WORDS:
                return ch.reply(msg, t("long.cancelled"))
            if pending and pending[0] == "folder?":  # 📂 Other folder…: the path typed
                return self._safe(lambda c, m: self.long_folder(c, m, pending[1], text), ch, msg)
            if pending:  # the task asked for by /long's card: start the story
                return self._safe(lambda c, m: self.long_new(c, m, *pending, text), ch, msg)
            story = longs.find(self.state, msg.chat, msg.thread) if msg.thread and self.state else None
            word, _, arg = text.partition(" ")
            # /shiftto = /shift (into the owner's pys), /shiftfrom = /takeback (back to the buddy): the owner's
            # names, 2026-11-12; the old ones still work
            word = {"/shiftto": "/shift", "/shiftfrom": "/takeback"}.get(word, word) if story else word
            if story and word in ("/status", "/handover", "/takeback", "/takeover", "/shift"):  # long_live.py
                return self._safe(lambda c, m: self.long_holder(c, m, story, word), ch, msg)
            if story and (text in ("/help", "/start") or word in self.commands):
                # the buddy's own command in a long story's thread: answered there by its code,
                # not by a model (an engine's own command, like /compact, still goes to the session)
                if word in self.commands:
                    return ch.answer(msg, None, self.commands[word][1](arg))
                return ch.reply(msg, t("help") + "".join(f"\n{n}  {h}" for n, (h, _) in self.commands.items()))
            if story:  # a long story's thread: for its session, not for a buddy turn
                said = longs.say(self.state, self.channels, story["id"], text, where=ch.name,
                                 who=msg.sender, engine=longs.pick(self.long_engines, "claude-code", story.get("mode")))
                return ch.reply(msg, said) if said else None
            if not msg.thread and not msg.reply_to and not text.startswith("/") and (hint := self._thread_hint(msg)):
                ch.reply(msg, hint)  # then the main conversation answers it as usual
            command = text in ("", "/help", "/start", "/resume", "/new") or word in self.commands
            loose = msg.thread and self.state and not topics.find(self.state, msg.chat, msg.thread)
            if loose and (command or getattr(ch, "threads_are_main", False)):
                topic = None  # a command never opens a topic, and neither does a Telegram thread
                # nobody opened: the app puts each new message in a new thread of its own, and a
                # topic for each (with a Lark thread and a web tab) would bury the real ones
            else:
                topic = self._topic(ch, msg, text)
            if topic:  # its own session, and its own lock: it runs beside the main conversation
                chat = f"{chat}#{topic['id']}"
            if text == "/topic" or text.startswith("/topic "):
                new = self.new_topic(msg.chat, text[6:].strip(), ch.name)
                return ch.reply(msg, t("bot.topic_open", id=new["id"], name=new["name"]))
            if text in ("", "/help", "/start"):
                help_text = t("help") + "".join(f"\n{name}  {h}" for name, (h, _) in self.commands.items())
                if hasattr(ch, "pin_stop"):  # Telegram: a ■ STOP key under the message box
                    return ch.pin_stop(msg.chat, help_text)
                return ch.reply(msg, help_text)
            if text == "/resume":
                return self.resume(ch, msg)
            if word in self.commands:
                status = ch.progress(msg, t("bot.working"))
                start = time.monotonic()
                answer = self.commands[word][1](arg)
                ch.answer(msg, status, answer, note=t("seconds", n=f"{time.monotonic() - start:.0f}"))
                return self._mirror(ch, msg, text, answer, topic)
            if text == "/new":
                self.sessions.set(chat, None)
                ch.reply(msg, t("bot.new"))
                return self._mirror(ch, msg, text, t("bot.new"), topic)
            with self.guard:
                lock = self.chat_locks.setdefault(chat, threading.Lock())
            # A status line shows the turn has started; the answer replaces it.
            queued = lock.locked()
            status = ch.progress(msg, t("bot.queued") if queued else t("bot.working"))
            with lock:  # one writer per session: turns in a chat run one after another
                if queued and status:
                    status = ch.progress(msg, t("bot.working"), status)
                start = time.monotonic()
                run = (Run(self.state, "chat", "chat", f"message on {ch.name}"
                           + (f" · topic {topic['id']} {topic['name']}" if topic else ""))
                       if self.state else None)
                # under the umbrella (procs.py) the turn outlives a restart: tag says where its
                # answer goes, and the next instance delivers it (adopted)
                umbrella = {"kind": "chat", "tag": {"chat": msg.chat, "session": chat, "text": text[:200]}} \
                    if procs.umbrella() else {}
                r = self.engine.run(text, cwd=self.workspace, session_id=self.sessions.get(chat), **umbrella)
                self.sessions.set(chat, r.session_id)
            if run:
                run.step("agent", engine="claude-code", session_id=r.session_id, ok=r.ok,
                         denied=r.denied, cost=r.cost, prompt=text[:200])
                if r.ok:
                    run.finish("ok", result=tail(r.text))
                else:
                    run.finish("timeout" if r.text.startswith("timed out") else "failed",
                               error=r.text[:300], output=tail(r.detail))
            note = t("seconds", n=f"{time.monotonic() - start:.0f}")
            if r.denied:
                note += " · " + t("denied", tools=", ".join(r.denied))
            ch.answer(msg, status, r.text, failed=not r.ok, note=note)
            self._mirror(ch, msg, text, r.text, topic, failed=not r.ok, note=note)
        except Exception as e:
            log.exception("turn failed")
            ch.reply(msg, t("bot.error", e=e))

    def _safe(self, fn, ch: Channel, msg: Incoming) -> None:
        try:
            fn(ch, msg)
        except Exception as e:
            log.exception("%s failed", fn.__name__)
            ch.reply(msg, t("bot.error", e=e))

    def _press(self, ch: Channel, msg: Incoming) -> None:
        """A button: ■ Stop, ▶ Resume, or an answer to an ask."""
        if msg.action.startswith("ask:ping-"):  # the Setup tab's ping card (from a chat or the page)
            p = pings.button(self.state, msg.chat, msg.action) if self.state else None
            # a press is no message to reply to (Feishu: 99992354): a new one says it
            ch.post(msg.chat, t("bot.pong_button", code=p["code"], channel=ch.name) if p else t("bot.ping_old"),
                    title="🏓 pong")
            return
        if msg.action == "stop":
            return self._safe(self.stop, ch, msg)
        if msg.action == "resume":
            return self._safe(self.resume, ch, msg)
        kind, _, rest = msg.action.partition(":")
        ask_id, _, choice = rest.partition(":")
        if kind == "short" and self.state and ask_id.isdigit():
            return self._short(ch, msg, int(ask_id), choice)
        if kind == "team" and self.state and choice == "reply":
            return self._team(ch, msg, ask_id)
        if kind != "ask" or self.state is None:
            return log.warning("unknown button %r", msg.action)
        said, ask = asks.answer(self.state, self.channels, ask_id, choice, ch.name, msg.sender)
        log.info("ask %s: %s", ask_id, said)
        if ask is None:
            return
        if ask["run"]:
            note_step(self.state, ask["run"], "answer", ask=ask_id, choice=choice, where=ch.name)
        then = ask["then"].get(choice)
        if then and then[0] == "long":  # a long story's question: its family answers it
            return self.long_answer(ch, msg, then[1:])
        if then and self.scheduler:
            kind, name, *act = then
            started = self.scheduler.run_now(kind, name, f"answer {choice} on {ch.name}", *act)
            post_all(self.channels, self.linked.of(msg.chat), t("bot.then", kind=t(f"kind.{kind}"), name=workflow(name),
                                                                 said=started),
                     title=f"↻ {ask['answer']['label']}", subtitle=t("bot.answered_on", where=app(ch.name, False)))

    def long_answer(self, ch: Channel, msg: Incoming, step: list) -> None:
        """A press on a long story's card. /long's guided start: engine, folder, 🔒 sandboxed or
        🔓 open (only when this instance offers open), then the task as a reply; or Allow / Deny
        for a permission a Codex session waits on. A press comes from an allowed sender only
        (on_message), so the 🔓 is always a human's. The folder is a button (an earlier story's,
        a project touched last), ✏️ a typed path (Lark: a text box on the card itself; Telegram:
        a reply), or 📂 a walk from ~/projects."""
        kind, *rest = step
        if kind == "decide":
            return longs.decide(self.state, *rest)
        if kind == "later":
            return longs.later(self.state, int(rest[0]))
        if kind == "perm":  # ✅ Allow / ⛔ Deny on what the buddy's live session asks
            return long_live.answer(self.state, int(rest[0]), rest[1], rest[2])
        if kind == "takeback":  # ⏹ End the terminal session now
            story = longs.load(self.state, int(rest[0]))
            if story:
                said = long_live.takeback(self.state, self.channels, story["id"],
                                          longs.pick(self.long_engines, "claude-code", story.get("mode")), force=True)
                if said:
                    ch.reply(msg, said)
            return None
        if kind == "resume":  # ▶ Resume on the card a message to a session nobody held asked
            story = longs.load(self.state, int(rest[0]))
            if story:
                said = longs.resume(self.state, self.channels, story["id"], where=ch.name,
                                    engine=longs.pick(self.long_engines, "claude-code", story.get("mode")))
                if said:
                    ch.reply(msg, said)
            return None
        if kind == "engine":
            found = longs.folders(self.state)
            options = [[f"f{k}", f.replace(str(Path.home()), "~")] for k, f in enumerate(found)]
            options.append(["path:reply", t("long.type_path")])  # just the path, typed: no list
            options.append(["other", t("long.other_folder")])  # any folder: typed, with a listing
            options.append(["cancel", t("btn.cancel")])  # no `then`: the card just settles
            then = {f"f{k}": ["long", "folder", rest[0], f] for k, f in enumerate(found)}
            then["path:reply"] = ["long", "path", rest[0]]
            then["other"] = ["long", "other", rest[0]]
            asks.create(self.state, self.channels, self.linked.of(msg.chat), t("long.pick_folder"),
                        title=t("long.title.start"), options=options, then=then)
        elif kind == "path" and (msg.text or "").strip():  # typed in the card's text box (Lark)
            self.long_folder(ch, msg, rest[0], msg.text)
        elif kind == "path" and hasattr(ch, "prompt"):  # a plain button (Telegram): ask for it
            self._ask_folder(ch, msg, rest[0], t("long.ask_path"))
        elif kind == "other" and hasattr(ch, "prompt"):
            _, near, subs = longs.find_folder("")  # ~/projects, listed
            self._ask_folder(ch, msg, rest[0], t("long.ask_folder", near=near.replace(str(Path.home()), "~"),
                                                 subs=", ".join(subs) or "—"))
        elif kind == "folder" and f"{rest[0]}/open" in self.long_engines:
            engine, folder = rest
            asks.create(self.state, self.channels, self.linked.of(msg.chat), t("long.pick_mode"),
                        title=t("long.title.start"),
                        options=[["sandbox", t("long.mode.sandbox_btn")], ["open", t("long.mode.open_btn")],
                                 ["cancel", t("btn.cancel")]],
                        then={m: ["long", "mode", engine, folder, m] for m in ("sandbox", "open")})
        elif kind in ("folder", "mode") and hasattr(ch, "prompt"):
            engine, folder, mode = (*rest, "sandbox")[:3]
            handle = ch.prompt(msg.chat, t("long.ask_task", engine=longs.ENGINE_NAMES[engine],
                                           folder=folder.replace(str(Path.home()), "~"))
                               + (" " + t("long.mode.open_short") if mode == "open" else ""))
            if handle:
                self.long_prompts[(msg.chat, handle)] = (engine, folder, mode)

    def long_holder(self, ch: Channel, msg: Incoming, story: dict, word: str) -> None:
        """/status, /handover, /takeback, /takeover, /shift in a long story's thread: who holds the
        session, and moving it to a terminal (a new pys window, or the one that holds it)."""
        n = story["id"]
        if word in ("/takeover", "/shift"):
            now = longs.load(self.state, n) or story
            code, said, _ = long_live.to_terminal(now, "resume" if word == "/takeover" else "attach")
            if word == "/shift" and code == 409 and not longs.held(now):
                # the owner, 2026-11-12 ("a"): /shift with no pys window holding it takes it over into one
                _, said, _ = long_live.to_terminal(now, "resume")
        elif word == "/status":
            said = long_live.status(self.state, n)
        elif word == "/handover":
            said = long_live.handover(self.state, self.channels, n)
        else:
            said = long_live.takeback(self.state, self.channels, n,
                                      longs.pick(self.long_engines, "claude-code", story.get("mode")))
        if said:
            ch.reply(msg, said)

    def _ask_folder(self, ch: Channel, msg: Incoming, engine: str, text: str) -> None:
        handle = ch.prompt(msg.chat, text)
        if handle:
            self.long_prompts[(msg.chat, handle)] = ("folder?", engine)

    def long_folder(self, ch: Channel, msg: Incoming, engine: str, typed: str) -> None:
        """The answer to 📂 Other folder…: a folder that exists goes on like a button (🔒/🔓,
        then the task); anything else gets the nearest folder that exists and what is in it,
        and the question again, so the phone can walk the tree one reply at a time."""
        folder, near, subs = longs.find_folder(typed)
        if folder:
            return self.long_answer(ch, msg, ["folder", engine, folder])
        listing = t("long.ask_folder", near=near.replace(str(Path.home()), "~"), subs=", ".join(subs) or "—")
        looked = typed.strip().endswith("/")  # a path ending in /: a look inside, not a miss
        self._ask_folder(ch, msg, engine, listing if looked else t("long.no_folder", typed=typed.strip()) + "\n" + listing)

    def long_new(self, ch: Channel, msg: Incoming, kind: str, folder: str, mode: str, text: str) -> None:
        engine = longs.pick(self.long_engines, kind, mode)
        if engine is None:
            return ch.reply(msg, t("gate.open_off"))
        try:
            longs.new(self.state, self.channels, self.linked.of(msg.chat), text, cwd=folder, where=ch.name,
                      engine=engine, kind=kind, mode=mode, who=msg.sender)
        except ValueError as e:  # its home folder: a long story may write there
            ch.reply(msg, str(e))

    def _short(self, ch: Channel, msg: Incoming, n: int, what: str) -> None:
        """■ Stop, 💬 Reply, or an option of a question (a<question>.<option>) under a short
        story's card. A reply typed on the card (Lark's text box) comes with the press;
        otherwise the person is asked for it (Telegram)."""
        q, _, i = what[1:].partition(".")
        if what.startswith("a") and q.isdigit() and i.isdigit():
            shorts.answer(self.state, self.channels, n, int(q), choice=int(i), where=ch.name,
                          who=msg.sender)
        elif what == "stop":
            shorts.ask_stop(self.state, self.channels, n, where=ch.name, who=msg.sender)
        elif what == "reply" and msg.text:
            shorts.reply(self.state, self.channels, n, msg.text, where=ch.name, who=msg.sender)
        elif what == "reply" and hasattr(ch, "prompt"):
            how = t("bot.short_how.telegram" if ch.name == "telegram" else "bot.short_how.other")
            handle = ch.prompt(msg.chat, t("bot.short_prompt", n=n, how=how))
            if handle:
                shorts.prompted(self.state, n, msg.chat, handle)

    def _team(self, ch: Channel, msg: Incoming, team: str) -> None:
        """💬 Reply under a team's card, for its RM: typed on the card (Lark's text box), or
        asked for (Telegram), as for a short story."""
        if msg.text:
            teams.reply(self.state, self.channels, team, msg.text, where=ch.name, who=msg.sender,
                        opts=self.team_opts)
        elif hasattr(ch, "prompt"):
            how = t("bot.short_how.telegram" if ch.name == "telegram" else "bot.short_how.other")
            handle = ch.prompt(msg.chat, t("bot.team_prompt", team=team, how=how))
            if handle:
                teams.prompted(self.state, team, msg.chat, handle)

    def stop(self, ch: Channel, msg: Incoming) -> None:
        """■ STOP, like the button in a lift: everything in motion stops, from any app.

        Pause the schedule first (nothing new starts), then kill every child this process
        started, with its process group: scheduled jobs and duties, and agent turns in chat.
        Asks still open are closed in every chat, and every running short story is asked to
        stop (its executor stops at its next step). /resume lets the schedule run again.
        """
        tasks = self.scheduler.pause(ch.name, msg.sender) if self.scheduler else []
        killed = procs.kill_all()
        closed = asks.stop_all(self.state, self.channels, ch.name, msg.sender) if self.state else 0
        stories = (shorts.stop_all(self.state, self.channels, where=ch.name, who=msg.sender)
                   if self.state else [])
        log.warning("STOP from %s by %s: %d processes, tasks %s, %d asks", ch.name, msg.sender,
                    killed, tasks, closed)
        text = (t("stop.head", where=app(ch.name)) + tn("stop.killed", killed)
                + (t("stop.tasks", tasks=", ".join(map(task, tasks))) if tasks else "")
                + tn("stop.closed", closed)
                + (tn("stop.stories", len(stories), list=join(f"#{n}" for n in stories)) if stories else "")
                + t("stop.end") + (t("stop.paused") if self.scheduler else ""))
        self._notify(msg.chat, text)

    def resume(self, ch: Channel, msg: Incoming) -> None:
        was = self.scheduler.resume() if self.scheduler else False
        self._notify(msg.chat, t("resume.done", where=app(ch.name)) if was else t("resume.none"))

    def adopted(self, meta: dict, p) -> None:
        """A run the last instance started and a restart left running (procs.adopt): a chat
        turn's answer goes to its chats, a long story's to its thread, a job to its record."""
        kind, tag = meta.get("kind"), meta.get("tag") or {}
        if kind == "long" and self.state:
            longs.adopted(self.state, self.channels, meta, p, self.long_engines)
        elif kind == "chat" and tag.get("chat"):
            from .engine import ClaudeCode
            r = ClaudeCode.parse(p, tag.get("session_id") or "", bool(tag.get("resumed")))
            if tag.get("session"):
                self.sessions.set(tag["session"], r.session_id)
            text = t("bot.adopted", prompt=tag.get("text", "")) + "\n\n" + (r.text or "")
            for c in self.linked.of(tag["chat"]):
                owner = next((x for x in self.channels if x.owns(c)), None)
                if owner and getattr(owner, "mirrors", True):  # a chat's answer: not for web push
                    owner.post(c, text, title="buddy")
        elif kind == "job" and self.scheduler:
            self.scheduler.adopted(meta, p)

    def _notify(self, chat: str, markdown: str) -> None:
        """Tell every chat of this person, with ▶ Resume or ■ Stop under it."""
        for c in self.linked.of(chat):
            owner = next((x for x in self.channels if x.owns(c)), None)
            if owner and hasattr(owner, "stop_button"):
                owner.stop_button(c, markdown)
            elif owner:
                owner.post(c, markdown, title="buddy")

    def _mirror(self, ch: Channel, msg: Incoming, text: str, answer: str, topic: dict | None = None,
                **kw) -> None:
        """Show the person's other chats what was asked here and what came back: for a
        topic, only in its threads there (an app that can't hold the topic doesn't see it)."""
        asked = text if len(text) <= 80 else text[:79] + "…"
        if topic:
            topic = self._fill(topic, msg.chat)
            for c, handle in topic["threads"].items():
                owner = next((x for x in self.channels if x.owns(c)), None)
                if c != msg.chat and owner:
                    owner.post(c, answer, title=t("bot.mirror", where=app(ch.name, False)), subtitle=asked,
                               thread=handle, **kw)
            return
        others = self.linked.others(msg.chat)
        if others:
            post_all([c for c in self.channels if getattr(c, "mirrors", True)], others, answer,
                     title=t("bot.mirror", where=app(ch.name, False)),
                     subtitle=asked, **kw)

    def _topic(self, ch: Channel, msg: Incoming, text: str) -> dict | None:
        """The topic msg is in, if any. A thread nobody opened as a topic (a reply in a thread
        on any message, in Lark) becomes one, named after its first words."""
        if not (msg.thread and self.state):
            return None
        t = topics.find(self.state, msg.chat, msg.thread)
        if t is None:
            t = topics.create(self.state, text[:40], ch.name)
            topics.add_thread(self.state, t["id"], msg.chat, msg.thread)
            self._open_elsewhere(t, msg.chat)
            t = topics.find(self.state, msg.chat, msg.thread)
        return t

    def _fill(self, t: dict, chat: str) -> dict:
        """Open the topic where it has no thread yet: an app that could not hold it when it
        was opened (Telegram before its topic mode was on). Once per topic and chat per run."""
        missing = [c for c in self.linked.of(chat) if c not in t["threads"] and (t["id"], c) not in self.tried]
        if not missing:
            return t
        self.tried.update((t["id"], c) for c in missing)
        self._open_elsewhere(t, None, missing)
        return topics.all_topics(self.state).get(t["id"], t)

    def new_topic(self, chat: str, name: str, where: str) -> dict:
        """A topic, opened in every chat of this person whose app can hold one apart."""
        t = topics.create(self.state, name, where)
        self._open_elsewhere(t, None, self.linked.of(chat))
        return topics.all_topics(self.state)[t["id"]]  # with its threads

    def _open_elsewhere(self, t: dict, but: str | None, chats: list[str] | None = None) -> None:
        for c in chats or self.linked.of(but):
            owner = next((x for x in self.channels if x.owns(c)), None)
            if c != but and owner and hasattr(owner, "open_topic"):
                handle = owner.open_topic(c, t)
                if handle:
                    topics.add_thread(self.state, t["id"], c, handle)

    def _seen_before(self, key: str) -> bool:
        with self.guard:
            if key in self.seen:
                return True
            self.seen[key] = None
            if len(self.seen) > 1000:
                self.seen.popitem(last=False)
            return False
