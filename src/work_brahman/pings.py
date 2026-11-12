"""Ping and pong through each chat, and who wrote without being paired (the owner, 2026-11-12:
"add a simu to the setting part, which allows me to send to feishu/telegram/lark/web to at
least check it works, something like ping and pong"; and his Feishu, added beside a paired Lark
and Telegram, stayed silent).

- send(): the running buddy posts "🏓 ping <code>" into one linked chat through that chat's own
  channel, and keeps whether the post went out (a Lark or Feishu failure keeps its code:
  99991403 is the month's API quota).
- pong(): `pong` typed back in that chat closes the loop, both ways proven; the buddy answers.
- refused(): every sender the buddy refused, with its chat and channel, and the `buddy allow`
  line that pairs it, for the Setup tab and `buddy doctor`.

All in <state>/pings.json and <state>/refused.json: the workbench, the TUI and doctor read them.
"""

from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path

LOCK = threading.Lock()


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def kind(person: str) -> str:
    """Which app a person id belongs to: tg:..., ou_... (the first Lark-type app), lark:ou_...
    (the second), web."""
    if "ou_" in person:
        return person[:person.index("ou_") + 3]
    return person.split(":", 1)[0] + ":" if ":" in person else person


def paired_here(allowed, sender: str) -> bool:
    """Is someone of sender's app paired already? Nobody is: the buddy tells the sender how
    to pair. Someone is: a stranger there gets silence."""
    return any(kind(a) == kind(sender) for a in allowed)


def allow_line(sender: str, chat: str) -> str:
    return f"buddy allow {sender}" + ("" if chat == sender else f" --chat {chat}")


def refuse(state: Path, channel: str, sender: str, chat: str) -> None:
    with LOCK:
        data = _read(state / "refused.json")
        e = data.get(sender, {"first": _now(), "times": 0})
        e.update(channel=channel, chat=chat, last=_now(), times=e["times"] + 1)
        data[sender] = e
        _write(state / "refused.json", data)


def forget(state: Path, sender: str) -> bool:
    """Ignore: the entry goes (paired since, or not wanted); it comes back only if it writes again."""
    with LOCK:
        data = _read(state / "refused.json")
        if data.pop(sender, None) is None:
            return False
        _write(state / "refused.json", data)
    return True


def refused(state: Path, allowed=()) -> list[dict]:
    """Refused senders not paired since, newest first, each with its `buddy allow` line."""
    data = _read(state / "refused.json")
    out = [{"sender": s, **e, "allow": allow_line(s, e["chat"])} for s, e in data.items() if s not in allowed]
    return sorted(out, key=lambda e: e["last"], reverse=True)


def unlinked_seen(state: Path, channel: str, sender: str, chat: str) -> None:
    """A paired person wrote from a chat that isn't linked (a QR scan pairs but can't link:
    the Mac, 2026-11-12): kept, so the Setup tab offers Link and doctor prints the line."""
    with LOCK:
        data = _read(state / "unlinked.json")
        e = data.get(chat, {"first": _now(), "times": 0})
        e.update(channel=channel, sender=sender, last=_now(), times=e["times"] + 1)
        data[chat] = e
        _write(state / "unlinked.json", data)


def unlinked(state: Path, linked=()) -> list[dict]:
    """Chats a paired person wrote from that aren't linked, newest first, with the line."""
    data = _read(state / "unlinked.json")
    out = [{"chat": c, **e, "allow": allow_line(e["sender"], c)} for c, e in data.items() if c not in linked]
    return sorted(out, key=lambda e: e["last"], reverse=True)


def unlink_forget(state: Path, chat: str) -> bool:
    with LOCK:
        data = _read(state / "unlinked.json")
        if data.pop(chat, None) is None:
            return False
        _write(state / "unlinked.json", data)
    return True


def send(state: Path, channels, chat: str) -> dict:
    """Post a ping into chat through its channel; what happened is kept and returned."""
    ch = next((c for c in channels if c.owns(chat)), None)
    code = f"{random.randint(1000, 9999)}"
    rec = {"chat": chat, "channel": ch.name if ch else "", "code": code, "sent_at": _now(), "pong_at": ""}
    if ch is None:
        rec.update(ok=False, why="no channel for this chat: is its app set up, and the buddy restarted?")
    else:
        from . import feishu, notify
        ch = notify.direct(ch)  # sent on purpose: past [notify]
        feishu._last.code = None
        text = f"🏓 ping {code}: answer **pong** here, or press the button (it tests the card callback)."
        try:
            if hasattr(ch, "ask"):  # a button: on Lark and Feishu it needs card.action.trigger (卡片回传交互)
                ok = ch.ask(chat, text, title=f"ping {code}", options=[["pong", "🏓 Pong"]],
                            ask_id=f"ping-{code}", stop=False) is not None
            else:
                ok = ch.post(chat, text, title=f"🏓 ping {code}")
        except Exception as e:  # noqa: BLE001 - the page shows it, the buddy keeps going
            ok, rec["why"] = False, f"{type(e).__name__}: {e}"
        failed = getattr(feishu._last, "code", None)
        rec["ok"] = bool(ok)
        if not ok and "why" not in rec:
            rec["why"] = ("this month's API quota is used up (99991403)" if failed == 99991403
                          else f"the app refused it ({failed})" if failed else "not sent: see the buddy's log")
    with LOCK:
        data = _read(state / "pings.json")
        data[chat] = rec
        _write(state / "pings.json", data)
    return rec


def pong(state: Path, chat: str, text: str) -> dict | None:
    """`pong` in a chat with a ping waiting: the loop is closed. The ping, or None."""
    if text.strip().lower().rstrip("!.。 ") not in ("pong", "🏓 pong", "🏓"):
        return None
    with LOCK:
        data = _read(state / "pings.json")
        rec = data.get(chat)
        if not rec or rec.get("pong_at") or not rec.get("ok"):
            return None
        rec["pong_at"] = _now()
        _write(state / "pings.json", data)
    return rec


def button(state: Path, chat: str, action: str) -> dict | None:
    """The ping card's 🏓 Pong button pressed (action "ask:ping-<code>:pong"): the card callback
    works (on Lark and Feishu: card.action.trigger, 卡片回传交互). The ping, or None."""
    code = action.split(":")[1].removeprefix("ping-") if action.count(":") >= 2 else ""
    with LOCK:
        data = _read(state / "pings.json")
        rec = data.get(chat)
        if not rec or rec.get("code") != code:
            return None
        rec["button_at"] = _now()
        rec["pong_at"] = rec.get("pong_at") or rec["button_at"]  # a press is an answer too
        _write(state / "pings.json", data)
    return rec


def pings(state: Path) -> dict:
    return _read(state / "pings.json")
