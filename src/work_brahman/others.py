"""Another buddy on the same Feishu or Lark bot (the PO, 2026-11-19; Boss Cong: "detect them and give a
meaningful message"). Two buddies on one app both hold its long connection, and Feishu gives each
message to only one of them, at random: half the answers come from the other one (an old version's
errors, "Not paired" from its own list). Feishu can't list the clients of an app, so a buddy looks for
messages its app sent that it didn't send itself. Script only, no model call.

- **The ring**: every message id this buddy sends (feishu.Feishu._reply, _send), from every process
  of it (`buddy serve`, `buddy-short` in a terminal), in state/feishu-sent.json under a lock: the last
  RING per app, and `since`, when the ring began for that app.
- **At start** (check_start, once, in the background): one list call per linked chat of each app, the
  last LOOK messages (a message call: it counts against the app's monthly quota, notify.py, so never
  on a timer). A message from our app, newer than `since`, that isn't in the ring: another buddy.
  Only ids, times and senders are read, never a message's text. A refusal is kept for the doctor
  with the scope it needs.
- **On a press, for free**: a card press on a card this buddy never made (its id not in the ring nor
  in any ask, story or team this buddy keeps).
- **Said once** (state/second-buddy.json): a post to the owner's chats and a doctor row; a start that
  finds nothing clears it, so a later one is said again.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from . import portable
from .text import t

RING = 500  # message ids kept per app
LOOK = 20  # messages read per chat at start
SCOPE_CODES = {99991672, 99991679, 230027}  # Feishu's "no permission" answers: the scope to add
SCOPE = "im:message:readonly"  # or im:message.p2p_msg:readonly, which the buddy has for a DM
log = logging.getLogger("buddy.others")
tell = None  # tell(markdown): post to the owner's chats (cli sets it); None: logged only


def state_dir() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser() / "work_brahman"


def _ring_path() -> Path:
    return state_dir() / "feishu-sent.json"


def _found_path() -> Path:
    return state_dir() / "second-buddy.json"


def _load(p: Path) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(p: Path, d: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(p)


def remember(app_id: str, message_id: str, now: float | None = None) -> None:
    """A message this buddy just sent."""
    if not message_id:
        return
    p = _ring_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with portable.locked(p.with_suffix(".lock")):
            d = _load(p)
            a = d.setdefault(app_id, {"since": now or time.time(), "ids": []})
            if message_id not in a["ids"]:
                a["ids"] = (a["ids"] + [message_id])[-RING:]
            _write(p, d)
    except OSError:
        log.warning("could not keep a sent message id")


def ring(app_id: str) -> dict:
    return _load(_ring_path()).get(app_id) or {}


def ours(app_id: str, message_id: str) -> bool:
    """Did this buddy send that message? The ring, else anything it keeps (asks, stories, teams hold
    their cards' ids: a card from before the ring began is still ours)."""
    if message_id in ring(app_id).get("ids", []):
        return True
    root = state_dir()
    for sub in ("asks", "short_stories", "teams", "long_stories"):
        for f in (root / sub).glob("**/*.json") if (root / sub).is_dir() else []:
            try:
                if message_id in f.read_text(errors="replace", encoding="utf-8"):
                    return True
            except OSError:
                continue
    return False


def found(app: str, how: str, now: float | None = None) -> bool:
    """Another buddy uses this app: kept, and said once. True when it was said now."""
    p = _found_path()
    d = _load(p)
    if d.get("app") == app:
        return False
    _write(p, {"app": app, "how": how, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(now))})
    text = t("others.found", app=app, how=how)
    log.warning("another buddy uses the %s bot: %s", app, how)
    if tell:
        try:
            tell(text)
        except Exception:  # noqa: BLE001 - a notice never stops the buddy
            log.exception("the notice about another buddy failed")
    return True


def clear(app: str) -> None:
    d = _load(_found_path())
    if d.get("app") == app:
        _found_path().unlink(missing_ok=True)


def refused(app: str, code, why: str) -> None:
    """The start check could not list messages: kept for the doctor (which scope)."""
    d = _load(state_dir() / "second-buddy-check.json")
    d[app] = {"code": code, "why": why[:200], "scope": SCOPE if code in SCOPE_CODES else ""}
    _write(state_dir() / "second-buddy-check.json", d)


def answered(app: str) -> None:
    """The app's chats could be listed again (its quota back, its scope granted): its refusal goes."""
    p = state_dir() / "second-buddy-check.json"
    d = _load(p)
    if d.pop(app, None) is not None:
        _write(p, d)


def pressed(app_id: str, app: str, message_id: str) -> None:
    """A card press: a card this buddy never made means another buddy sent it."""
    if message_id and not ours(app_id, message_id):
        found(app, t("others.how_press"))


def check(app_id: str, app: str, items: list[dict], now: float | None = None) -> bool:
    """items: the chat's last messages, {id, time (ms), sender_type, sender_id}. True: another buddy."""
    r = ring(app_id)
    since = r.get("since")
    if since is None:  # nothing sent since the ring began: nothing to compare with yet
        return False
    mine = set(r.get("ids", []))
    for m in items:
        if (m.get("sender_type") == "app" and m.get("sender_id") == app_id and m.get("id") not in mine
                and int(m.get("time") or 0) > since * 1000 and not ours(app_id, m["id"])):
            at = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(m["time"]) / 1000))
            found(app, t("others.how_reply", at=at), now)
            return True
    return False


def check_start(channels: list, chats: list[str]) -> None:
    """Once at start, in the background: each Feishu/Lark app's linked chats, LOOK messages each."""
    def run():
        for ch in channels:
            inner = getattr(ch, "inner", ch)
            if not hasattr(inner, "recent"):
                continue
            mine = [c for c in chats if inner.owns(c)]
            seen = False
            for chat in mine:
                got = inner.recent(chat, LOOK)
                if isinstance(got, tuple):  # (code, why): refused
                    refused(inner.name, *got)
                    break
                seen = check(inner.app_id, inner.name, got) or seen
            else:
                if mine:
                    answered(inner.name)  # listed without a refusal: the doctor's ⚠️ goes (review, 2026-11-19)
                if mine and not seen:
                    clear(inner.name)
    threading.Thread(target=run, name="others", daemon=True).start()


def doctor_rows() -> list[tuple]:
    """(ok, what, how) rows for buddy doctor."""
    rows = []
    d = _load(_found_path())
    if d:
        rows.append((False, f"another buddy is also using the {d['app']} bot ({d.get('how', '')}, seen {d.get('at', '')})",
                     "Feishu gives each message to only one of them: stop the other buddy, or give it its own bot"))
    for app, r in _load(state_dir() / "second-buddy-check.json").items():
        rows.append((None, f"{app}: can't look for a second buddy on the bot ({r.get('why', '')})",
                     f"grant the scope {r['scope']} in the developer console" if r.get("scope") else ""))
    return rows
