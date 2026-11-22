"""The buddy's event stream: GET /api/events, Server-Sent Events of what web push sends (the PO's
brief, 2026-11-18; the owner's rule: "the platform's push first; our own push switches on by itself
where it doesn't work; never both for one event").

One decision of what notifies (webpush.WebPush: which card, question or post becomes an event, its
title, body and link), two deliveries, each with its own level (the PO, 2026-11-18: the stream is
"our own push" and must work with web push off):

- the browsers' web push, under [notify] webpush (pseudo-chat "webpush:");
- this stream, under [notify] events (pseudo-chat "events:", EventStream below): off (the default)
  writes nothing, "ends" a story's start and end, "all" every change.

The stream's chat comes first, so its event is written before the browsers' push is sent, and the
push carries the same id (recent()): a client that gets both drops the second. Generic and opt-in:
any client may read it; nothing in brahman depends on one.

- **The ring** is a file, state/events.jsonl (chmod 600), the last RING events (and the id of the
  newest one dropped, for the gap): a card is drawn by
  whichever process changes the story (`buddy-short` in a terminal as well as `buddy serve`), so
  an in-memory list in serve would miss most of them. Appends and trims are under a lock.
- **The id** is milliseconds since the epoch, made strictly larger than the last one under the lock:
  unique, ordered, and still ordered if the file is removed (a client's Last-Event-ID stays valid).
- **The line**: `id: <id>` and one `data:` JSON line {id, kind, title, body, link, tag, ref, at};
  kind is story | team | ask | post. `ref` (short-story:12, long-story:3, team:kit) is what the page's privacy
  rules judge. A private story or team (privacy.private_ref; one not found too: fail closed) is written
  with a fixed title, "🔒 A private story changed", and no body or link, here and in web push alike
  (webpush.WebPush._event): the owner's own view of the stream is a device token's, a bearer token. A comment `: keep-alive` every KEEPALIVE seconds keeps a tunnel from idling out.
- **Resume**: Last-Event-ID (the header, or ?last= for a client that can't set it) replays what
  came after it from the ring; when one after it was already dropped → `event: gap` first. No Last-Event-ID:
  new events only (a client that connects for the first time is not shown the past).
- **Device tokens** (`buddy token add <name> --scope events`): a read-only credential for a native
  client, for /api/events and nothing else. Stored as a SHA-256 hash in state/device-tokens.json
  (chmod 600); listed and revoked by name. None exist until one is added.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path

from . import portable
from .webpush import WebPush

CHAT = "events:"
RING = 200  # events kept for a client that reconnects
KEEPALIVE = 15.0  # seconds between keep-alive comments
STALL = 60.0  # seconds a write may wait on a reader that stopped reading (a sleeping phone); then the stream ends
POLL = 1.0  # seconds between looks at the ring
KINDS = ("story", "team", "ask", "post")
SCOPES = ("events",)


def path(state: Path) -> Path:
    return state / "events.jsonl"


def _load(state: Path) -> tuple[list[dict], int]:
    """The events in the ring, and the id of the newest one already dropped from it (0: none). The
    file's first line is {"dropped": id} once the ring is full."""
    try:
        lines = path(state).read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    out, dropped = [], 0
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:  # a line cut short by a crash: skip it
            continue
        if isinstance(ev, dict) and isinstance(ev.get("id"), int):
            out.append(ev)
        elif isinstance(ev, dict) and isinstance(ev.get("dropped"), int):
            dropped = ev["dropped"]
    return out, dropped


def read(state: Path) -> list[dict]:
    return _load(state)[0]


def publish(state: Path, kind: str, title: str, body: str = "", link: str = "", tag: str = "",
            ref: str = "", now: float | None = None) -> dict:
    """Add one event to the ring and return it (its id is new)."""
    state.mkdir(parents=True, exist_ok=True)
    with portable.locked(state / ".events.lock"):
        rows, dropped = _load(state)
        last = rows[-1]["id"] if rows else 0
        ev = {"id": max(last + 1, int((now or time.time()) * 1000)), "kind": kind, "title": title[:200],
              "body": body[:1500], "link": link, "tag": tag, "ref": ref,
              "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))}
        rows = rows + [ev]
        if len(rows) > RING:
            dropped = max(dropped, rows[-RING - 1]["id"])
            rows = rows[-RING:]
        p = path(state)
        tmp = p.with_suffix(".tmp")
        head = json.dumps({"dropped": dropped}) + "\n" if dropped else ""
        tmp.write_text(head + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(p)
    return ev


def with_stream(chats: list[str]) -> list[str]:
    """A story's chats with the stream's just before web push's: a story started before the stream
    existed (its chats were fixed at its start) reaches it too (the PO, 2026-11-18: "our own push
    can't miss what's running now"). Before, so the push of the same event carries the stream's id."""
    from .webpush import CHAT as PUSH
    if PUSH not in chats or CHAT in chats:
        return chats
    i = chats.index(PUSH)
    return chats[:i] + [CHAT] + chats[i:]


def recent(state: Path, kind: str, title: str, body: str, within: float = 10.0, now: float | None = None,
           ref: str | None = None) -> int | None:
    """The id of the same event written to the stream in the last `within` seconds (its id is its time
    in ms), for the browsers' push of it; None when the stream didn't carry it (off, or "ends"). With
    `ref`, its ref must match too: two private stories' events read the same fixed line."""
    edge = int(((now or time.time()) - within) * 1000)
    for ev in reversed(read(state)):
        if ev["id"] < edge:
            return None
        if (ev["kind"], ev["title"], ev["body"]) == (kind, title[:200], body[:1500]) and (ref is None or ev.get("ref") == ref):
            return ev["id"]
    return None


def after(state: Path, last: int | None) -> tuple[list[dict], bool]:
    """The events after id `last`, and whether some between were already dropped from the ring."""
    rows, dropped = _load(state)
    if last is None:
        return [], False
    return [r for r in rows if r["id"] > last], last < dropped  # a gap: one after `last` was dropped


def last_id(header: str | None) -> int | None:
    try:
        return int((header or "").strip())
    except ValueError:
        return None


def frame(ev: dict) -> bytes:
    """One event as SSE: its id and one data line (JSON has no raw newline in it)."""
    return f"id: {ev['id']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()


# Device tokens: read-only, one scope

def tokens_path(state: Path) -> Path:
    return state / "device-tokens.json"


def tokens(state: Path) -> list[dict]:
    try:
        d = json.loads(tokens_path(state).read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _hash(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def _save_tokens(state: Path, rows: list[dict]) -> None:
    p = tokens_path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(p)


def add_token(state: Path, name: str, scope: str = "events") -> str:
    """A new device token: returned once, only its hash kept. A name already there is an error."""
    if scope not in SCOPES:
        raise ValueError(f"scope: one of {', '.join(SCOPES)}")
    if not name or not all(c.isalnum() or c in "-_." for c in name) or len(name) > 40:
        raise ValueError("name: letters, digits, - _ . (up to 40)")
    tok = "bev_" + secrets.token_urlsafe(32)
    state.mkdir(parents=True, exist_ok=True)
    with portable.locked(state / ".events.lock"):
        rows = tokens(state)
        if any(r["name"] == name for r in rows):
            raise ValueError(f"a token named {name!r} exists: revoke it first")
        rows.append({"name": name, "scope": scope, "sha256": _hash(tok),
                     "added": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
        _save_tokens(state, rows)
    return tok


def revoke_token(state: Path, name: str) -> bool:
    if not tokens(state):
        return False
    with portable.locked(state / ".events.lock"):
        rows = tokens(state)
        left = [r for r in rows if r["name"] != name]
        _save_tokens(state, left)
    return len(left) != len(rows)


def device(state: Path, headers) -> dict | None:
    """The device token this request carries (Authorization: Bearer), or None."""
    auth = headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None
    rows = tokens(state)
    if not rows:
        return None
    h = _hash(auth[7:].strip())
    for r in rows:
        if hmac.compare_digest(r.get("sha256", ""), h):
            return r
    return None


# The channel: the same decision as web push, delivered to the ring

class EventStream(WebPush):
    """What web push would send, written to the stream instead (WebPush._event decides; this delivers)."""
    name = "events"

    def owns(self, chat: str) -> bool:
        return chat == CHAT

    def deliver(self, kind: str, title: str, body: str, link: str, tag: str, ref: str) -> bool:
        publish(self.state, kind, title, body, "/" + link, tag, ref)  # relative: a tunnel's port differs
        return True


def mode(cfg: dict) -> str:
    """[notify] events: off (the default) | ends | all."""
    from . import notify
    return notify.mode(cfg, "events")


def channel(cfg: dict, state: Path):
    """The stream's channel in its mode, followed live like every [notify] key (notify.watch)."""
    from . import notify
    return notify.Quiet(EventStream(state), mode(cfg), "events")
