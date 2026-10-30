"""Topics: conversations of their own beside the main one, each with its own agent session.

The main conversation is the one your linked chats share (Lark DM, Telegram, web). A topic
is another one, for a different piece of work: it has its own Claude Code session, its
turns run in parallel with the main conversation's, and it shows in each app that can hold
it apart:

- Lark: a thread. `/topic NAME` posts a 🧵 card and opens its thread; replying in a thread
  on any other message starts a topic too.
- web: a tab in the Chat view (＋ starts one).
- Telegram: a forum topic in the private chat (topic mode on). The main conversation has
  its own 🏠 topic there, and a thread the app opened by itself is the main conversation too.

A channel shows a topic through open_topic(chat, topic) -> handle, and tells which topic a
message is in through Incoming.thread (that handle). The topics live in

    $XDG_STATE_HOME/work_brahman/topics.json   {"t3": {"id", "name", "created", "where",
                                                         "threads": {chat: handle}}}

and a topic's session is kept under "<the person's key>#<topic id>" in sessions.json.
"""

import json
import os
import threading
import time
from pathlib import Path

from .text import t as text

_lock = threading.Lock()  # `buddy serve` alone writes topics


def _path(state: Path) -> Path:
    return state / "topics.json"


def all_topics(state: Path) -> dict[str, dict]:
    try:
        return json.loads(_path(state).read_text())
    except (OSError, ValueError):
        return {}


def _save(state: Path, topics: dict) -> None:
    p = _path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(topics, ensure_ascii=False, indent=1))
    os.replace(tmp, p)


def create(state: Path, name: str, where: str) -> dict:
    with _lock:
        topics = all_topics(state)
        n = max([int(k[1:]) for k in topics] or [0]) + 1
        t = {"id": f"t{n}", "name": name.strip()[:60] or text("topic.default", n=n), "where": where,
             "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "threads": {}}
        topics[t["id"]] = t
        _save(state, topics)
    return t


def add_thread(state: Path, topic_id: str, chat: str, handle: str) -> None:
    with _lock:
        topics = all_topics(state)
        if topic_id in topics:
            topics[topic_id]["threads"][chat] = handle
            _save(state, topics)


def close(state: Path, topic_id: str) -> dict | None:
    """Close a topic: the web drops its tab and Telegram closes its forum topic (cli.py).
    It stays in the file, with its session, so nothing it said is lost."""
    with _lock:
        topics = all_topics(state)
        if topic_id not in topics:
            return None
        topics[topic_id]["closed"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _save(state, topics)
        return topics[topic_id]


def find(state: Path, chat: str, handle: str) -> dict | None:
    """The topic whose thread in chat is handle."""
    return next((t for t in all_topics(state).values() if t["threads"].get(chat) == handle), None)
