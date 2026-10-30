"""Feishu/Lark over the outbound long connection: no public IP, no inbound port.

Only works for a self-built app (企业自建应用), with the im.message.receive_v1
event subscribed in "long connection" mode. Buttons (asks, ■ Stop) also need the
card.action.trigger callback subscribed in long-connection mode (开发者后台 → 事件与回调 →
回调配置); without it a press shows an error in Lark and nothing reaches the buddy.

Lark (international) and Feishu (China) are two platforms with the same API, and both name
chats oc_... and people ou_.... One buddy can run an app on each: the first (FEISHU_APP_ID,
on buddy.toml's `domain`) keeps the ids as they are, the second (LARK_APP_ID) writes them as
lark:oc_... and lark:ou_..., so a chat id always says which app owns it.
"""

import json
from typing import Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import (CreateMessageRequest, CreateMessageRequestBody,
                                 P2ImMessageReceiveV1, PatchMessageRequest,
                                 PatchMessageRequestBody, ReplyMessageRequest,
                                 ReplyMessageRequestBody)

from lark_oapi.event.callback.model.p2_card_action_trigger import (P2CardActionTrigger,
                                                                   P2CardActionTriggerResponse)

from .channel import Incoming
from .text import t

DOMAINS = {"feishu": lark.FEISHU_DOMAIN, "lark": lark.LARK_DOMAIN}
MAX_TEXT = 8000  # keep replies readable on a phone; the full answer stays in the session
MAX_CARD = 24_000  # UTF-8 bytes of Markdown; Lark rejects a card whose JSON is over 30 KB
# The SDK logs the full connect URL, including its access_key and ticket, at INFO.
LOG_LEVEL = lark.LogLevel.WARNING


class Feishu:
    """The Channel for Lark/Feishu: answers are cards, the status card is patched in place."""

    name = "lark"
    prefix = ""  # "lark:" for the second app: its chat and sender ids carry it

    def __init__(self, app_id: str, app_secret: str, domain: str = "feishu", prefix: str = ""):
        self.app_id, self.app_secret = app_id, app_secret
        self.domain = DOMAINS[domain]
        self.name = "feishu" if domain == "feishu" else "lark"
        self.prefix = prefix
        self.api = (lark.Client.builder().app_id(app_id).app_secret(app_secret)
                    .domain(self.domain).log_level(LOG_LEVEL).build())

    def reply_text(self, message_id: str, text: str, in_thread: bool = False) -> str | None:
        """Reply to a message; in a topic group the reply stays in the topic."""
        return self._reply(message_id, "text", _text(text), in_thread)

    def send_text(self, chat_id: str, text: str) -> str | None:
        return self._send(chat_id, "text", _text(text))

    def reply_card(self, message_id: str, card: dict, in_thread: bool = False) -> str | None:
        """Like reply_text, with a card (see card()). Returns the new message's id, or None."""
        return self._reply(message_id, "interactive", json.dumps(card, ensure_ascii=False), in_thread)

    def send_card(self, chat_id: str, card: dict) -> str | None:
        return self._send(chat_id, "interactive", json.dumps(card, ensure_ascii=False))

    def update_card(self, message_id: str, card: dict) -> bool:
        """Replace a card the bot sent, in place (Lark allows this for 14 days)."""
        body = PatchMessageRequestBody.builder().content(json.dumps(card, ensure_ascii=False)).build()
        req = PatchMessageRequest.builder().message_id(message_id).request_body(body).build()
        return _check(self.api.im.v1.message.patch(req)) is not None

    def _reply(self, message_id: str, msg_type: str, content: str, in_thread: bool = False) -> str | None:
        body = (ReplyMessageRequestBody.builder().msg_type(msg_type).content(content)
                .reply_in_thread(in_thread).build())
        req = ReplyMessageRequest.builder().message_id(message_id).request_body(body).build()
        return _check(self.api.im.v1.message.reply(req))

    def _send(self, chat_id: str, msg_type: str, content: str) -> str | None:
        body = (CreateMessageRequestBody.builder().receive_id(chat_id.removeprefix(self.prefix)).msg_type(msg_type)
                .content(content).build())
        req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
        return _check(self.api.im.v1.message.create(req))

    def owns(self, chat: str) -> bool:
        return chat.startswith(self.prefix + "oc_")

    def listen(self, on_message: Callable[[Incoming], None]) -> None:
        """Block forever. on_message must return fast: Feishu re-sends any
        event not acknowledged within 3 seconds."""
        def receive(data: P2ImMessageReceiveV1) -> None:
            m = data.event.message
            on_message(Incoming(self.prefix + m.chat_id, m.message_id,
                                self.prefix + data.event.sender.sender_id.open_id,
                                message_text(data) if m.message_type == "text" else None,
                                reply_to=m.parent_id or "",
                                thread=(m.root_id or "") if m.thread_id else ""))  # a thread = its root

        def press(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
            e = data.event
            action = (e.action.value or {}).get("action", "") if e.action else ""
            typed = ((e.action.form_value or {}).get(REPLY_FIELD) or "").strip() if e.action else ""
            if action:  # the token is unique per press: Lark's retries are de-duplicated
                on_message(Incoming(self.prefix + e.context.open_chat_id, f"press:{e.token}",
                                    self.prefix + e.operator.open_id, typed or None, action))
            return P2CardActionTriggerResponse({"toast": {"type": "info", "content": t("ack")}})
        handler = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(receive)
                   .register_p2_card_action_trigger(press).build())
        lark.ws.Client(self.app_id, self.app_secret, event_handler=handler,
                       domain=self.domain, log_level=LOG_LEVEL).start()

    # Channel methods, on top of the message calls above.

    def reply(self, msg: Incoming, text: str) -> None:
        self.reply_text(msg.message_id, text, bool(msg.thread))

    def progress(self, msg: Incoming, text: str, handle: str | None = None) -> str | None:
        c = card(text, buttons=[_stop()])  # while a turn works, ■ Stop is one tap away
        if handle:
            return handle if self.update_card(handle, c) else None
        return self.reply_card(msg.message_id, c, bool(msg.thread))

    def answer(self, msg: Incoming, handle: str | None, markdown: str, *,
               failed: bool = False, note: str = "") -> None:
        c = (card(markdown, title=t("failed"), color="red", note=note) if failed
             else card(markdown, note=note))
        if handle and self.update_card(handle, c):
            return
        if self.reply_card(msg.message_id, c, bool(msg.thread)) is None:  # a card Lark refused: the text
            self.reply_text(msg.message_id, ("⚠️ " if failed else "") + markdown
                       + (f"\n\n({note})" if note else ""), bool(msg.thread))

    def post(self, chat: str, markdown: str, *, title: str, subtitle: str = "",
             failed: bool = False, note: str = "", thread: str = "") -> bool:
        c = card(markdown, title=("⚠️ " if failed else "") + title, subtitle=subtitle,
                 color="red" if failed else "blue", note=note)
        if thread:  # a topic's thread: a reply in it, to its root
            return self.reply_card(thread, c, True) is not None
        if self.send_card(chat, c) is not None:
            return True
        return self.send_text(chat, f"[{title}]\n{markdown}") is not None

    def open_topic(self, chat: str, topic: dict) -> str | None:
        """A topic is a thread: a 🧵 card, and a first reply in its thread to open it."""
        root = self.send_card(chat, card(t("topic.lark_card"), title=f"🧵 {topic['name']}",
                                         subtitle=topic["id"], color="purple"))
        if root:
            self._reply(root, "text", _text(t("topic.lark_first")), in_thread=True)
        return root

    def ask(self, chat: str, markdown: str, *, title: str, options: list[list[str]],
            ask_id: str, failed: bool = False, note: str = "") -> str | None:
        buttons = [(f"{label}", f"ask:{ask_id}:{key}", "primary" if k == 0 else "default")
                   for k, (key, label) in enumerate(options)] + [_stop()]
        return self.send_card(chat, card(markdown, title=("⚠️ " if failed else "❓ ") + title,
                                         color="red" if failed else "orange", note=note,
                                         buttons=buttons))

    def settle(self, chat: str, handle: str, markdown: str, *, title: str, line: str,
               failed: bool = False, note: str = "") -> bool:
        body = f"{markdown}\n\n**{line}**"
        return self.update_card(handle, card(body, title=("⚠️ " if failed else "") + title,
                                             color="grey", note=note))

    def track(self, chat: str, markdown: str, *, title: str, handle: str | None = None,
              failed: bool = False, done: bool = False,
              buttons: list[tuple[str, str]] = ()) -> str | None:
        c = card(markdown, title=title, color="red" if failed else "green" if done else "blue",
                 buttons=[(label, action, "danger" if action.endswith(":stop") else "default")
                          for label, action in buttons if not action.endswith(":reply")],
                 reply=next((a for _, a in buttons if a.endswith(":reply")), ""))
        if handle:
            return handle if self.update_card(handle, c) else None
        return self.send_card(chat, c)

    def prompt(self, chat: str, text: str) -> str | None:
        return self.send_text(chat, text)  # Lark has no reply box to open: quote it to answer

    def stop_button(self, chat: str, markdown: str) -> bool:
        """A notice with ■ Stop or ▶ Resume on it, for /stop and /resume answers."""
        resume = markdown.startswith("■")
        return self.send_card(chat, card(markdown, buttons=[_resume() if resume else _stop()])) is not None


def message_text(data: P2ImMessageReceiveV1) -> str:
    """Plain text of a text message, without the @bot placeholders (@_user_1)."""
    text = json.loads(data.event.message.content or "{}").get("text", "")
    return " ".join(w for w in text.split() if not w.startswith("@_user_")).strip()


def _stop() -> tuple[str, str, str]:
    return t("btn.stop"), "stop", "danger"


def _resume() -> tuple[str, str, str]:
    return t("btn.resume"), "resume", "primary"


REPLY_FIELD = "reply"  # the text box's name; its text comes back in the press's form_value


def card(markdown: str, *, title: str = "", subtitle: str = "", color: str = "blue",
         note: str = "", buttons: list[tuple[str, str, str]] = (), reply: str = "") -> dict:
    """A card (JSON schema 2.0): Markdown body, optional buttons, optional coloured header,
    optional grey note under a rule. Card Markdown renders headings, lists, links, code
    blocks and tables, unlike a text message. A button is (label, action, type); a press
    comes back as card.action.trigger with value {"action": action}. With reply (an action),
    a text box and 💬 Send go above the buttons, in a form: Send comes back as that action,
    with the text in form_value[REPLY_FIELD]."""
    body = [{"tag": "markdown", "content": _clip(markdown)}]
    body += [{"tag": "button", "text": {"tag": "plain_text", "content": label}, "type": kind,
              "width": "fill", "behaviors": [{"type": "callback", "value": {"action": action}}]}
             for label, action, kind in buttons]
    if reply:
        body.insert(1, {"tag": "form", "name": "reply_form", "elements": [
            {"tag": "input", "name": REPLY_FIELD, "width": "fill",
             "placeholder": {"tag": "plain_text", "content": t("reply.placeholder")}},
            {"tag": "button", "name": "send", "text": {"tag": "plain_text", "content": t("btn.send")},
             "type": "primary", "width": "fill", "form_action_type": "submit",
             "behaviors": [{"type": "callback", "value": {"action": reply}}]}]})
    if note:
        body += [{"tag": "hr"}, {"tag": "markdown", "content": note, "text_size": "notation"}]
    c = {"schema": "2.0", "config": {"update_multi": True}, "body": {"elements": body}}
    if title:
        c["header"] = {"title": {"tag": "plain_text", "content": title}, "template": color}
        if subtitle:
            c["header"]["subtitle"] = {"tag": "plain_text", "content": subtitle}
    return c


def _clip(s: str) -> str:
    b = s.encode()
    if len(b) <= MAX_CARD:
        return s
    return b[:MAX_CARD].decode(errors="ignore") + "\n\n" + t("truncated")


def _text(s: str) -> str:
    if len(s) > MAX_TEXT:
        s = s[:MAX_TEXT] + "\n" + t("truncated")
    return json.dumps({"text": s}, ensure_ascii=False)


def _check(resp) -> str | None:
    """The message id of a successful call ("" for a patch), None after logging a failure."""
    if not resp.success():
        lark.logger.error(f"feishu: {resp.code} {resp.msg} log_id={resp.get_log_id()}")
        return None
    return getattr(getattr(resp, "data", None), "message_id", None) or ""  # a patch has no data
