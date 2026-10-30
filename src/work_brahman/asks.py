"""Asks: a question with buttons, sent to every chat of one person; the first answer wins.

A job or duty runs as a child process that ends long before anyone answers, while the
answer arrives in `buddy serve`. So an ask is a small file both can read:

    $XDG_STATE_HOME/work_brahman/asks/<ask id>.json

The child posts the card to each linked chat and writes what to do for each choice
(`then`: a job or duty to run). When someone presses a button in any app, serve marks the
first answer, changes every copy to "answered on Telegram: …", and starts what the choice
asked for. A later press on another copy is told it was already answered. STOP closes
every ask still open.
"""

import json
import os
import secrets
import threading
import time
from pathlib import Path

from .channel import Channel
from .text import app, t

_lock = threading.Lock()  # one answer at a time, in serve


def create(state: Path, channels: list[Channel], chats: list[str], markdown: str, *,
           title: str, options: list[list[str]], then: dict | None = None, run: str = "",
           failed: bool = False, note: str = "") -> dict:
    """Post the question to every chat. options: [[key, label], ...]; then: key -> [kind, name]."""
    ask = {"id": f"{time.strftime('%m%d%H%M')}{secrets.token_hex(2)}", "run": run, "title": title,
           "markdown": markdown, "options": options, "then": then or {}, "failed": failed,
           "note": note, "asked": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "copies": {},
           "answer": None}
    for chat in chats:
        ch = next((c for c in channels if c.owns(chat)), None)
        handle = ch.ask(chat, markdown, title=title, options=options, ask_id=ask["id"],
                        failed=failed, note=note) if ch else None
        if handle:
            ask["copies"][chat] = handle
    _save(state, ask)
    return ask


def answer(state: Path, channels: list[Channel], ask_id: str, choice: str, where: str,
           who: str) -> tuple[str, dict | None]:
    """Record the first answer and settle every copy. Returns (what to tell the presser,
    the ask if this answer won, else None)."""
    with _lock:
        ask = load(state, ask_id)
        if ask is None:
            return t("ask.unknown"), None
        if ask["answer"]:
            a = ask["answer"]
            return t("ask.already", said=_said(a)), None
        label = dict(map(tuple, ask["options"])).get(choice)
        if label is None:
            return t("ask.bad_choice"), None
        ask["answer"] = {"choice": choice, "label": label, "where": where, "who": who,
                         "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        _save(state, ask)
    _settle_all(channels, ask, t("ask.settled", where=app(where), label=label))
    return f"✓ {label}", ask


def stop_all(state: Path, channels: list[Channel], where: str, who: str) -> int:
    """Close every ask still open, as STOP does. Returns how many."""
    closed = []
    with _lock:
        for f in sorted((state / "asks").glob("*.json")):
            if time.time() - f.stat().st_mtime > 14 * 86400:  # Lark can't edit it any more
                continue
            ask = load(state, f.stem)
            if ask and not ask["answer"]:
                ask["answer"] = {"choice": "", "label": t("ask.stopped_label"), "where": where, "who": who,
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "stopped": True}
                _save(state, ask)
                closed.append(ask)
    for ask in closed:
        _settle_all(channels, ask, t("ask.stopped_line", where=app(where)))
    return len(closed)


def still_open(state: Path, title: str) -> dict | None:
    """The unanswered ask with this title, if there is one: the same question is asked once,
    and its card stands until it is answered."""
    for f in sorted((state / "asks").glob("*.json"), reverse=True):
        if time.time() - f.stat().st_mtime > 14 * 86400:  # as in stop_all: past editing
            continue
        ask = load(state, f.stem)
        if ask and not ask["answer"] and ask["title"] == title:
            return ask
    return None


def load(state: Path, ask_id: str) -> dict | None:
    if not ask_id.isalnum():  # it came from a button press: never a path
        return None
    try:
        return json.loads((state / "asks" / f"{ask_id}.json").read_text())
    except (OSError, ValueError):
        return None


def _said(a: dict) -> str:
    return (t("ask.stopped_from", where=app(a["where"])) if a.get("stopped")
            else t("ask.answered_on", where=app(a["where"]), label=a["label"]))


def _settle_all(channels: list[Channel], ask: dict, line: str) -> None:
    """Every copy loses its buttons and says how the question was settled."""
    for chat, handle in ask["copies"].items():
        ch = next((c for c in channels if c.owns(chat)), None)
        if ch:
            ch.settle(chat, handle, ask["markdown"], title=ask["title"], line=line,
                      failed=ask["failed"], note=ask["note"])


def _save(state: Path, ask: dict) -> None:
    d = state / "asks"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{ask['id']}.tmp"
    tmp.write_text(json.dumps(ask, indent=1, ensure_ascii=False))
    os.replace(tmp, d / f"{ask['id']}.json")
