"""Chat message -> one agent turn -> reply. One agent session per chat, on any channel."""

import json
import logging
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import asks, long_stories as longs, procs, short_stories as shorts, topics
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
        self.tried: set[tuple[str, str]] = set()  # (topic, chat) a thread was tried for
        self.long_prompts: dict[tuple[str, str], tuple[str, str]] = {}  # (chat, prompt) -> (engine, folder)
        self.long_engines = {"claude-code": engine, "codex": Codex()}  # serve sets [long]'s (engine.long_engines)

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
            log.warning("refused message from %s", msg.sender)
            if not self.allowed:  # not paired yet: tell the owner which id to allow
                self.pool.submit(ch.reply, msg,
                                 t("bot.not_paired", sender=msg.sender,
                                   chat="" if msg.chat == msg.sender else f" --chat {msg.chat}"))
            return
        if self.state and ch.name != "web":  # the owner is on the phone: `speak` goes quiet
            _touch(self.state.parent / "speak" / "remote")
        if msg.action:
            self.pool.submit(self._press, ch, msg)
            return
        if msg.text is None:
            self.pool.submit(ch.reply, msg, t("bot.text_only"))
            return
        if msg.text.strip() in STOP_WORDS | {t("btn.stop_key")}:  # not queued behind anything: a stop must stop now
            threading.Thread(target=self._safe, args=(self.stop, ch, msg), daemon=True).start()
            return
        self.pool.submit(self._handle, ch, msg)

    def _handle(self, ch: Channel, msg: Incoming) -> None:
        chat, text = self.linked.key(msg.chat), msg.text.strip()
        try:
            n = shorts.find(self.state, msg.chat, msg.reply_to) if msg.reply_to and self.state else None
            if n:  # an answer to a short story's card or its 💬 Reply: for the executor
                shorts.reply(self.state, self.channels, n, text, where=ch.name, who=msg.sender)
                return ch.reply(msg, t("short.sent", n=n))
            pending = self.long_prompts.pop((msg.chat, msg.reply_to), None) if msg.reply_to else None
            if pending and text.lower() in CANCEL_WORDS:
                return ch.reply(msg, t("long.cancelled"))
            if pending:  # the task asked for by /long's card: start the story
                return self._safe(lambda c, m: self.long_new(c, m, *pending, text), ch, msg)
            story = longs.find(self.state, msg.chat, msg.thread) if msg.thread and self.state else None
            word, _, arg = text.partition(" ")
            if story and (text in ("/help", "/start") or word in self.commands):
                # the buddy's own command in a long story's thread: answered there by its code,
                # not by a model (an engine's own command, like /compact, still goes to the session)
                if word in self.commands:
                    return ch.answer(msg, None, self.commands[word][1](arg))
                return ch.reply(msg, t("help") + "".join(f"\n{n}  {h}" for n, (h, _) in self.commands.items()))
            if story:  # a long story's thread: for its session, not for a buddy turn
                said = longs.say(self.state, self.channels, story["id"], text, where=ch.name,
                                 who=msg.sender, engine=self.long_engines["claude-code"])
                return ch.reply(msg, said) if said else None
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
                r = self.engine.run(text, cwd=self.workspace, session_id=self.sessions.get(chat))
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
        if msg.action == "stop":
            return self._safe(self.stop, ch, msg)
        if msg.action == "resume":
            return self._safe(self.resume, ch, msg)
        kind, _, rest = msg.action.partition(":")
        ask_id, _, choice = rest.partition(":")
        if kind == "short" and self.state and ask_id.isdigit():
            return self._short(ch, msg, int(ask_id), choice)
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
        """A press on a long story's card. /long's guided start: engine, then folder, then the
        task as a reply; or Allow / Deny for a permission a Codex session waits on."""
        kind, *rest = step
        if kind == "decide":
            return longs.decide(self.state, *rest)
        if kind == "engine":
            options = [[f"f{k}", f.replace(str(Path.home()), "~")] for k, f in enumerate(longs.folders(self.state))]
            options.append(["cancel", t("btn.cancel")])  # no `then`: the card just settles
            asks.create(self.state, self.channels, self.linked.of(msg.chat), t("long.pick_folder"),
                        title=t("long.title.start"), options=options,
                        then={key: ["long", "folder", rest[0], f]
                              for (key, _), f in zip(options, longs.folders(self.state))})
        elif kind == "folder" and hasattr(ch, "prompt"):
            engine, folder = rest
            handle = ch.prompt(msg.chat, t("long.ask_task", engine=longs.ENGINE_NAMES[engine],
                                           folder=folder.replace(str(Path.home()), "~")))
            if handle:
                self.long_prompts[(msg.chat, handle)] = (engine, folder)

    def long_new(self, ch: Channel, msg: Incoming, kind: str, folder: str, text: str) -> None:
        try:
            longs.new(self.state, self.channels, self.linked.of(msg.chat), text, cwd=folder, where=ch.name,
                      engine=self.long_engines[kind], kind=kind)
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
            post_all(self.channels, others, answer, title=t("bot.mirror", where=app(ch.name, False)),
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
