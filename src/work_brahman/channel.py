"""A channel is a chat app the buddy talks through (Lark/Feishu, Telegram).

The bot sees only Incoming messages (a text, or a button press) and the methods below,
so another chat app is one more file. Chat ids name their channel ("oc_…" is Lark, "tg:…" is Telegram),
so a job's chat_id alone says where to post.
"""

from dataclasses import dataclass
from typing import Callable, Protocol


@dataclass
class Incoming:
    chat: str              # where to answer
    message_id: str        # what to reply to; unique within the chat
    sender: str            # who sent it, as listed in BUDDY_ALLOWED_IDS
    text: str | None       # None: not a text message
    action: str = ""       # a button press: "ask:<ask id>:<choice>", "short:<n>:stop|reply",
                           # "short:<n>:a<question>.<option>", "stop" or "resume"
    reply_to: str = ""     # the message this one answers (quotes), in the same chat
    thread: str = ""       # the thread it is in (topics.py): the channel's handle, "" = main


class Channel(Protocol):
    name: str

    def owns(self, chat: str) -> bool: ...

    def listen(self, on_message: Callable[[Incoming], None]) -> None:
        """Block forever, calling on_message for each message; it must return fast."""

    def reply(self, msg: Incoming, text: str) -> None:
        """A short plain-text notice. Like progress and answer, it stays in msg's thread."""

    def progress(self, msg: Incoming, text: str, handle: str | None = None) -> str | None:
        """Show a status line, or change the one at handle. Returns its handle."""

    def answer(self, msg: Incoming, handle: str | None, markdown: str, *,
               failed: bool = False, note: str = "") -> None:
        """The agent's answer, in place of the status line when there is one."""

    def post(self, chat: str, markdown: str, *, title: str, subtitle: str = "",
             failed: bool = False, note: str = "", thread: str = "") -> bool:
        """A new message with a title (a job's result); in a topic's thread when given."""

    def open_topic(self, chat: str, topic: dict) -> str | None:
        """Optional: show a topic (topics.py) apart from the main conversation, e.g. as a
        thread. Returns its handle, which comes back as Incoming.thread."""

    def ask(self, chat: str, markdown: str, *, title: str, options: list[list[str]],
            ask_id: str, failed: bool = False, note: str = "") -> str | None:
        """A new message with one button per option, plus ■ Stop. Returns its handle.
        A press comes back through listen() as Incoming(action="ask:<ask_id>:<key>")."""

    def settle(self, chat: str, handle: str, markdown: str, *, title: str, line: str,
               failed: bool = False, note: str = "") -> bool:
        """Change an ask in place: the buttons go, `line` says how it was settled."""

    def track(self, chat: str, markdown: str, *, title: str, handle: str | None = None,
              failed: bool = False, done: bool = False,
              buttons: list[tuple[str, str]] = ()) -> str | None:
        """A short story's card (short_stories.py): a new message, or the one at handle changed
        in place, with buttons (label, action) under it; without buttons any old ones go.
        An action ending in ":reply" takes text: a channel that can put a text box on the card
        (Lark) does, and the press brings the text as Incoming.text; one that can't (Telegram)
        sends a press with no text, and the bot answers with prompt().
        Returns its handle, or None when it could not be shown."""

    def prompt(self, chat: str, text: str) -> str | None:
        """A message to answer by replying to it (Telegram opens the reply box). Returns its
        handle; the answer comes back as Incoming(reply_to=handle)."""


class Linked:
    """Chats that belong to one person ([me] chats in buddy.toml), e.g. a Lark DM and a
    Telegram chat. They act as one: a post to one goes to all, what happens in one is
    mirrored to the others, and they share one agent session. The first chat names the
    group, so the session it already had carries on."""

    def __init__(self, chats: list[str] = ()):
        self.chats = list(chats)

    def of(self, chat: str) -> list[str]:
        """Every chat a post to `chat` goes to."""
        return self.chats if chat in self.chats else [chat]

    def others(self, chat: str) -> list[str]:
        return [c for c in self.of(chat) if c != chat]

    def key(self, chat: str) -> str:
        """The session and lock key: one per person, not per chat app."""
        return self.chats[0] if chat in self.chats else chat


def post_all(channels: list[Channel], chats: list[str], markdown: str, **kw) -> dict[str, bool]:
    """Post to each chat through the channel that owns it. Returns chat -> posted."""
    done = {}
    for chat in chats:
        ch = next((c for c in channels if c.owns(chat)), None)
        done[chat] = bool(ch) and ch.post(chat, markdown, **kw)
    return done
