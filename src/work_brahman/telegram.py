"""Telegram over long polling (getUpdates): outbound HTTPS only, like Lark's long connection.

Create a bot with @BotFather and put its token in TELEGRAM_BOT_TOKEN. Chats are
"tg:<chat id>", senders "tg:<user id>". Plain standard library: no SDK needed.

Topics (topics.py) are forum topics in the private chat with the bot, which needs the bot's
topic mode on in @BotFather (getMe then says has_topics_enabled). Without it, opening a
topic fails quietly and the chat stays the main conversation.

In topic mode the app files every message typed outside a topic under a new thread of its
own. Those threads are not topics: what is written there is the main conversation (bot.py),
and the main conversation has one home, a 🏠 topic the bot opens once, where everything it
posts outside a topic goes (cards, digests, asks), so it reads as one thread.

Telegram renders a small HTML subset, not Markdown tables, so answers are converted:
bold, italic, code, links and quotes map to tags. A table that fits a phone becomes an
aligned monospace block; a wider one becomes one line per row, since a phone wraps a
wide <pre> block out of its columns, and links inside <pre> don't work.
"""

import html
import http.client
import json
import logging
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .channel import Incoming
from .text import t

log = logging.getLogger("buddy.telegram")

API = "https://api.telegram.org/bot{token}/{method}"
MAX_TEXT = 4000  # Telegram allows 4096 characters per message; leave room for tags
CONNECT_TIMEOUT = 5  # per address; the request timeout covers the long poll's read
CALL_TIMEOUT = 10  # seconds, for every call but the long poll
GENERAL = "1"  # General's message_thread_id, as some clients send it in a chat with topics


class _Connection(http.client.HTTPSConnection):
    """Connect with a short timeout per address, trying the family that worked last first.

    urllib tries getaddrinfo's addresses in order, each with the whole request timeout
    and no Happy Eyeballs, so a host whose IPv6 route is dead would wait the full
    timeout (80 s for a long poll) on every call before trying IPv4.
    """
    good_family = None

    def connect(self):
        addrs = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        addrs.sort(key=lambda a: a[0] != _Connection.good_family)
        err = None
        for family, kind, proto, _, addr in addrs:
            sock = socket.socket(family, kind, proto)
            try:
                sock.settimeout(CONNECT_TIMEOUT)
                sock.connect(addr)
            except OSError as e:
                sock.close()
                err = e
                continue
            sock.settimeout(self.timeout)
            _Connection.good_family = family
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
            return
        raise err or OSError(f"no address for {self.host}")


class _Handler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_Connection, req, context=self._context)


_opener = urllib.request.build_opener(_Handler)


class Telegram:
    name = "telegram"
    threads_are_main = True  # a thread nobody opened as a topic is the main conversation

    def __init__(self, token: str, state: Path | None = None):
        self.token = token  # part of every URL: never log a URL
        self.homes = state / "telegram-home.json" if state else None  # {chat: 🏠's thread}
        self.tried: set[str] = set()  # chats where 🏠 could not be opened, this run

    def owns(self, chat: str) -> bool:
        return chat.startswith("tg:")

    def listen(self, on_message: Callable[[Incoming], None]) -> None:
        params = {"timeout": 50, "allowed_updates": ["message", "callback_query"]}
        while True:
            updates = self._call("getUpdates", **params)
            if updates is None:
                time.sleep(5)  # network or API trouble: retry, don't spin
                continue
            for u in updates:
                params["offset"] = u["update_id"] + 1  # acknowledges everything before it
                if q := u.get("callback_query"):  # a button press under one of our messages
                    self._call("answerCallbackQuery", callback_query_id=q["id"], text=t("ack"))
                    if q.get("data") and q.get("message"):
                        on_message(Incoming(f"tg:{q['message']['chat']['id']}", f"press:{q['id']}",
                                            f"tg:{q['from']['id']}", None, q["data"]))
                    continue
                m = u.get("message")
                if not m or "from" not in m:
                    continue
                text = m.get("text")
                if text:  # "/new@my_bot" in a group means /new
                    text = re.sub(r"^(/\w+)@\w+", r"\1", text)
                topic = str(m["message_thread_id"]) if m.get("is_topic_message") else ""
                if topic == GENERAL:  # the pinned General topic is the main conversation
                    topic = ""
                reply = m.get("reply_to_message", {})
                if topic and str(reply.get("message_id")) == topic:
                    reply = {}  # in a topic, every message replies to its first one: not a quote
                on_message(Incoming(f"tg:{m['chat']['id']}", str(m["message_id"]),
                                    f"tg:{m['from']['id']}", text,
                                    reply_to=str(reply.get("message_id", "")), thread=topic))

    def reply(self, msg: Incoming, text: str) -> None:
        self._send(msg.chat, html.escape(text, quote=False), reply_to=msg.message_id, thread=msg.thread)

    def progress(self, msg: Incoming, text: str, handle: str | None = None) -> str | None:
        body = html.escape(text, quote=False)
        if handle:
            return handle if self._edit(msg.chat, handle, body, _buttons([_stop()])) else None
        return self._send(msg.chat, body, reply_to=msg.message_id, markup=_buttons([_stop()]),
                          thread=msg.thread)

    def answer(self, msg: Incoming, handle: str | None, markdown: str, *,
               failed: bool = False, note: str = "") -> None:
        head = f"<b>{t('failed')}</b>\n\n" if failed else ""
        parts = _pages(head + to_html(markdown) + _note(note))
        first, rest = parts[0], parts[1:]
        if not (handle and self._edit(msg.chat, handle, first)):
            if self._send(msg.chat, first, reply_to=msg.message_id, thread=msg.thread) is None:
                # Telegram refused the HTML (a tag it can't parse): send the raw text.
                for p in _pages(html.escape(markdown, quote=False)):
                    self._send(msg.chat, p, reply_to=msg.message_id, thread=msg.thread)
                return
        for p in rest:
            self._send(msg.chat, p, thread=msg.thread)

    def post(self, chat: str, markdown: str, *, title: str, subtitle: str = "",
             failed: bool = False, note: str = "", thread: str = "") -> bool:
        head = f"<b>{'⚠️ ' if failed else ''}{html.escape(title, quote=False)}</b>"
        if subtitle:
            head += f"  <i>{html.escape(subtitle, quote=False)}</i>"
        ok = True
        for p in _pages(head + "\n\n" + to_html(markdown) + _note(note)):
            ok = self._send(chat, p, thread=thread) is not None and ok
        return ok

    def open_topic(self, chat: str, topic: dict) -> str | None:
        """A topic is a forum topic in the private chat; its handle is its message_thread_id."""
        made = self._call("createForumTopic", chat_id=int(chat[3:]), name=f"🧵 {topic['name']}"[:128])
        if not made:  # topic mode is off for this bot (or not a chat that can hold topics)
            return None
        thread = str(made["message_thread_id"])
        self._send(chat, t("topic.tg_first"), thread=thread)
        return thread

    def close_topic(self, chat: str, handle: str) -> bool:
        """Close a forum topic: it stays, with what was said, but takes no new messages."""
        return bool(self._call("closeForumTopic", chat_id=int(chat[3:]), message_thread_id=int(handle)))

    def ask(self, chat: str, markdown: str, *, title: str, options: list[list[str]],
            ask_id: str, failed: bool = False, note: str = "") -> str | None:
        head = f"<b>{'⚠️' if failed else '❓'} {html.escape(title, quote=False)}</b>\n\n"
        rows = [(label, f"ask:{ask_id}:{key}") for key, label in options] + [_stop()]
        return self._send(chat, _pages(head + to_html(markdown) + _note(note))[0],
                          markup=_buttons(rows))

    def settle(self, chat: str, handle: str, markdown: str, *, title: str, line: str,
               failed: bool = False, note: str = "") -> bool:
        head = f"<b>{'⚠️ ' if failed else ''}{html.escape(title, quote=False)}</b>\n\n"
        body = head + to_html(markdown) + f"\n\n<b>{html.escape(line, quote=False)}</b>" + _note(note)
        return self._edit(chat, handle, _pages(body)[0])  # no markup: the buttons go

    def track(self, chat: str, markdown: str, *, title: str, handle: str | None = None,
              failed: bool = False, done: bool = False,
              buttons: list[tuple[str, str]] = ()) -> str | None:
        body = _pages(f"<b>{html.escape(title, quote=False)}</b>\n\n" + to_html(markdown))[0]
        markup = _buttons(list(buttons)) if buttons else None
        if handle:
            return handle if self._edit(chat, handle, body, markup) else None
        return self._send(chat, body, markup=markup)

    def prompt(self, chat: str, text: str) -> str | None:
        return self._send(chat, html.escape(text, quote=False), markup={
            "force_reply": True, "input_field_placeholder": t("reply.tg_placeholder")})

    def stop_button(self, chat: str, markdown: str) -> bool:
        """A notice with ■ Stop or ▶ Resume under it, for /stop and /resume answers."""
        resume = markdown.startswith("■")
        return self._send(chat, to_html(markdown), markup=_buttons([_resume() if resume else _stop()])) is not None

    def pin_stop(self, chat: str, text: str) -> None:
        """A ■ STOP key that stays under the message box: one tap sends its label, which the bot
        takes as /stop (bot.STOP_WORDS, or "■ 停止" in Chinese)."""
        self._send(chat, html.escape(text, quote=False), markup={
            "keyboard": [[{"text": t("btn.stop_key")}]], "is_persistent": True, "resize_keyboard": True})

    def home(self, chat: str) -> str:
        """The 🏠 topic of the main conversation, opened the first time it is needed; "" when
        the chat can't hold topics (topic mode off), and the post goes to the chat itself."""
        homes = self._homes()
        if chat in homes or not self.homes or chat in self.tried:
            return homes.get(chat, "")
        self.tried.add(chat)
        made = self._call("createForumTopic", chat_id=int(chat[3:]), name=t("topic.tg_home_name"))
        if not made:
            return ""
        homes[chat] = str(made["message_thread_id"])
        self.homes.parent.mkdir(parents=True, exist_ok=True)
        self.homes.write_text(json.dumps(homes))
        self._send(chat, html.escape(t("topic.tg_home"), quote=False), thread=homes[chat])
        return homes[chat]

    def _homes(self) -> dict:
        try:
            return json.loads(self.homes.read_text()) if self.homes else {}
        except (OSError, ValueError):
            return {}

    def _send(self, chat: str, body: str, reply_to: str | None = None,
              markup: dict | None = None, thread: str = "") -> str | None:
        params = {"chat_id": int(chat[3:]), "text": body, "parse_mode": "HTML",
                  "link_preview_options": {"is_disabled": True}}
        home = "" if thread or reply_to else self.home(chat)
        if thread or home:
            params["message_thread_id"] = int(thread or home)
        if markup:
            params["reply_markup"] = markup
        if reply_to:
            params["reply_parameters"] = {"message_id": int(reply_to),
                                          "allow_sending_without_reply": True}
        r = self._call("sendMessage", **params)
        if r is None and home:  # 🏠 was closed or deleted: forget it, post to the chat itself
            homes = self._homes()
            homes.pop(chat, None)
            self.homes.write_text(json.dumps(homes))
            params.pop("message_thread_id")
            r = self._call("sendMessage", **params)
        return str(r["message_id"]) if r else None

    def _edit(self, chat: str, message_id: str, body: str, markup: dict | None = None) -> bool:
        """Change a message; without markup its buttons go."""
        extra = {"reply_markup": markup} if markup else {}
        return self._call("editMessageText", chat_id=int(chat[3:]), message_id=int(message_id),
                          text=body, parse_mode="HTML",
                          link_preview_options={"is_disabled": True}, **extra) is not None

    def _call(self, method: str, **params):
        req = urllib.request.Request(API.format(token=self.token, method=method),
                                     data=json.dumps(params).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            # The long poll waits its own timeout; anything else gives up soon. A card is
            # re-rendered whole on every change, so an edit lost now is repaired by the next,
            # and a call that hangs holds the caller (a short story's lock, say) for nothing.
            with _opener.open(req, timeout=params["timeout"] + 30 if "timeout" in params
                              else CALL_TIMEOUT) as r:
                return json.load(r)["result"]
        except urllib.error.HTTPError as e:  # its str() has no URL, so no token
            try:
                why = json.load(e).get("description", "")
            except ValueError:
                why = ""
            log.error("telegram %s: HTTP %s %s", method, e.code, why)
        except (urllib.error.URLError, OSError) as e:
            log.error("telegram %s: %s", method, getattr(e, "reason", e))
        return None


def _stop() -> tuple[str, str]:
    return t("btn.stop"), "stop"


def _resume() -> tuple[str, str]:
    return t("btn.resume"), "resume"


def _buttons(rows: list[tuple[str, str]]) -> dict:
    """An inline keyboard, one button per row; callback_data must stay under 64 bytes."""
    return {"inline_keyboard": [[{"text": label, "callback_data": data}] for label, data in rows]}


# Markdown -> Telegram HTML (https://core.telegram.org/bots/api#html-style)

PHONE = 40  # monospace columns a phone shows in a <pre> block before it wraps

def to_html(md: str) -> str:
    out, lines, i = [], md.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            lang, body = line.strip()[3:].strip(), []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                body.append(lines[i])
                i += 1
            i += 1
            cls = f' class="language-{html.escape(lang)}"' if re.fullmatch(r"[\w+-]+", lang) else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(body), quote=False)}</code></pre>")
            continue
        if _row(line) and i + 1 < len(lines) and _rule(lines[i + 1]):
            rows = []
            while i < len(lines) and _row(lines[i]):
                if not _rule(lines[i]):
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            table = _table([[_plain(c) for c in r] for r in rows])
            if max(map(_width, table.split("\n"))) <= PHONE:
                out.append(f"<pre>{html.escape(table, quote=False)}</pre>")
            else:  # the header row names the columns; the rows say enough on their own
                out += ["• " + " · ".join(f"<b>{_inline(c)}</b>" if k == 0 else _inline(c)
                                          for k, c in enumerate(c for c in r if c not in ("", "-")))
                        for r in rows[1:]]
            continue
        out.append(_block(line))
        i += 1
    return "\n".join(out).strip()


def _block(line: str) -> str:
    if m := re.match(r"\s*#{1,6}\s+(.*)", line):
        return f"<b>{_inline(m[1])}</b>"
    if m := re.match(r"\s*>\s?(.*)", line):
        return f"<blockquote>{_inline(m[1])}</blockquote>"
    if m := re.match(r"(\s*)[-*+]\s+(.*)", line):
        return f"{m[1]}• {_inline(m[2])}"
    if re.fullmatch(r"\s*([-*_])(\s*\1){2,}\s*", line):
        return "──────────"
    return _inline(line)


def _inline(s: str) -> str:
    """Escape, then tag; code spans are left as they are inside."""
    parts = re.split(r"(`[^`\n]+`)", s)
    for k, p in enumerate(parts):
        if k % 2:
            parts[k] = f"<code>{html.escape(p[1:-1], quote=False)}</code>"
            continue
        p = html.escape(p, quote=False)
        p = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
                   lambda m: f'<a href="{m[2].replace(chr(34), "%22")}">{m[1]}</a>', p)
        p = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<b>{m[1] or m[2]}</b>", p)
        p = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<i>\1</i>", p)
        p = re.sub(r"~~(.+?)~~", r"<s>\1</s>", p)
        parts[k] = p
    return "".join(parts)


def _row(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().count("|") >= 2


def _rule(line: str) -> bool:
    return bool(re.fullmatch(r"\s*\|?(\s*:?-{2,}:?\s*\|)+\s*:?-*:?\s*\|?\s*", line))


def _plain(cell: str) -> str:
    """A table cell without Markdown marks: a <pre> block shows them literally."""
    cell = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cell)
    return re.sub(r"\*\*|__|`|~~", "", cell)


def _width(s: str) -> int:
    """Columns on screen: CJK and most emoji take two."""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _table(rows: list[list[str]]) -> str:
    n = max(len(r) for r in rows)
    rows = [r + [""] * (n - len(r)) for r in rows]
    w = [max(_width(r[c]) for r in rows) for c in range(n)]
    fmt = lambda r: "  ".join(x + " " * (w[c] - _width(x)) for c, x in enumerate(r)).rstrip()
    return "\n".join([fmt(rows[0]), "  ".join("─" * x for x in w)] + [fmt(r) for r in rows[1:]])


def _note(note: str) -> str:
    return f"\n\n<i>{html.escape(note, quote=False)}</i>" if note else ""


def _pages(body: str) -> list[str]:
    """Split at line breaks into messages Telegram accepts; never inside a <pre> block."""
    pages, cur, in_pre = [], "", False
    for line in body.split("\n"):
        if len(cur) + len(line) + 1 > MAX_TEXT and cur and not in_pre:
            pages.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
        in_pre = (in_pre or "<pre>" in line) and "</pre>" not in line
    pages.append(cur)
    return [p[:MAX_TEXT] for p in pages]  # a single giant block is cut, not refused
