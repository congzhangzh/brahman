"""Native push to the owner's own apps through Apple's APNs (the owner, 2026-11-25: native push; iOS wakes an
app only through Apple). Any app with its own bundle id may register: nothing here is for one app only.

Off unless `[notify] apns = "ends"` (or "all") in buddy.toml, and off while the key is missing:

- **The key** (token-based APNs): the .p8 file from the owner's Apple Developer account (Keys → Apple Push
  Notifications service) at ~/.config/brahman/apns.p8 (0600), and `[apns] key_id` and `team_id` in buddy.toml.
  Each request carries `authorization: bearer <JWT>`, ES256 over {iss: team id, iat}, made again before it is
  50 minutes old (Apple takes one up to 60 minutes; not more than one new one in 20).
- **A device registers** (POST /api/push/apns, behind the owner login): {device, token, topic (its bundle id),
  environment: sandbox | production}; one row per device name in state/apns-devices.json (0600); DELETE (or
  POST {device, remove: true}) takes it away. A token APNs answers 410 (Unregistered) for is dropped.
- **What is sent** is decided once with web push and the event stream (webpush.WebPush._event: which card,
  question or post, the fixed line for a private story or team) and carries the stream's id for the same event,
  so an app that gets both drops the second. The payload holds the title only, as the chat cards show it
  (`[notify] apns_text = "none"`: "brahman: news on #N"), never the story's text or a path; `apns-collapse-id`
  is the story's tag, so a newer card replaces the older notification.
- HTTP/2 (APNs takes nothing else) through httpx with its http2 extra (h2, pure Python); one connection kept.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from pathlib import Path

from .webpush import WebPush

CHAT = "apns:"
HOSTS = {"production": "https://api.push.apple.com", "sandbox": "https://api.sandbox.push.apple.com"}
KEY_FILE = "apns.p8"
JWT_AGE = 50 * 60  # seconds a provider token is reused (Apple: at most 60 minutes, a new one at most every 20)
TTL = 86400  # apns-expiration: a day, as web push's TTL
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
TOKEN = re.compile(r"[0-9a-fA-F]{64,200}")
TOPIC = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,154}")
KID = re.compile(r"[A-Z0-9]{10}")
log = logging.getLogger("buddy.apns")
_lock = threading.Lock()


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


# The devices: one row per name

def devices_path(state: Path) -> Path:
    return state / "apns-devices.json"


def devices(state: Path) -> dict[str, dict]:
    try:
        rows = json.loads(devices_path(state).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in rows.items() if isinstance(v, dict) and NAME.fullmatch(k)} if isinstance(rows, dict) else {}


def _save(state: Path, rows: dict) -> None:
    p = devices_path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(p)


def register(state: Path, body, agent: str = "") -> str:
    """Keep one device's token (a new one replaces its old); "" when kept, else why not."""
    if not isinstance(body, dict):
        return "a JSON object: {device, token, topic, environment}"
    name, token = str(body.get("device", "")), str(body.get("token", ""))
    topic, env = str(body.get("topic", "")), str(body.get("environment", ""))
    if not NAME.fullmatch(name):
        return "device: a name of letters, digits, . _ - (at most 64)"
    if not TOKEN.fullmatch(token):
        return "token: the device token in hex"
    if not TOPIC.fullmatch(topic):
        return "topic: the app's bundle id"
    if env not in HOSTS:
        return "environment: sandbox or production"
    with _lock:
        rows = devices(state)
        rows[name] = {"token": token.lower(), "topic": topic, "environment": env,
                      "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "agent": agent[:200]}
        _save(state, rows)
    return ""


def unregister(state: Path, name: str, token: str = "") -> bool:
    """Drop a device (with `token`: only while that is still its token, so a 410 for an old token never drops a
    newer registration)."""
    with _lock:
        rows = devices(state)
        if name not in rows or (token and rows[name].get("token") != token):
            return False
        del rows[name]
        _save(state, rows)
    return True


def describe(name: str, row: dict) -> dict:
    """A device for the page and the doctor: never its whole token."""
    return {"device": name, "topic": row.get("topic", ""), "environment": row.get("environment", ""),
            "token": "…" + str(row.get("token", ""))[-6:], "at": row.get("at", "")}


# The key

def key_path() -> Path:
    from . import install
    return install.config_dir() / KEY_FILE


def settings(cfg: dict) -> tuple[str, str]:
    a = cfg.get("apns") or {}
    return str(a.get("key_id", "")).strip(), str(a.get("team_id", "")).strip()


def missing(cfg: dict) -> list[str]:
    """What the sender still needs (empty: ready): the key file, [apns] key_id, [apns] team_id."""
    kid, team = settings(cfg)
    out = [] if key_path().is_file() else [str(key_path())]
    out += [] if KID.fullmatch(kid) else ["[apns] key_id (10 letters or digits, from the key's page)"]
    out += [] if KID.fullmatch(team) else ["[apns] team_id (10 letters or digits, your Apple team)"]
    return out


_jwt: dict = {}


def bearer(cfg: dict, now: float | None = None) -> str:
    """The provider token: a JWT, ES256 with the .p8 key, kept for JWT_AGE seconds."""
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import ECC
    from Crypto.Signature import DSS
    now = time.time() if now is None else now
    kid, team = settings(cfg)
    with _lock:
        if _jwt.get("for") == (kid, team) and now - _jwt.get("iat", 0) < JWT_AGE:
            return _jwt["token"]
        key = ECC.import_key(key_path().read_text(encoding="utf-8"))
        head = b64u(json.dumps({"alg": "ES256", "kid": kid}, separators=(",", ":")).encode())
        body = b64u(json.dumps({"iss": team, "iat": int(now)}, separators=(",", ":")).encode())
        sig = DSS.new(key, "fips-186-3", encoding="binary").sign(SHA256.new(f"{head}.{body}".encode()))  # r || s
        _jwt.update({"for": (kid, team), "iat": now, "token": f"{head}.{body}.{b64u(sig)}"})
        return _jwt["token"]


def forget_bearer() -> None:
    """APNs refused the provider token (expired or not trusted): the next send makes a new one."""
    with _lock:
        _jwt.clear()


# Sending

def payload(title: str, ref: str, tag: str, id: int | None, text: str = "title") -> bytes:
    """The notification: the title only, as the chat cards show it; "none": the story's number only."""
    n = ref.rsplit(":", 1)[-1] if ref.startswith(("short-story:", "long-story:")) else ""
    alert = (f"brahman: news on #{n}" if n else "brahman: news") if text == "none" else title[:120]
    aps = {"alert": {"title": "brahman", "body": alert}, "sound": "default", "thread-id": tag or "brahman"}
    return json.dumps({"aps": aps, "brahman": {"ref": ref} | ({"id": id} if id is not None else {})},
                      ensure_ascii=False).encode()


def collapse_id(tag: str) -> str:
    """apns-collapse-id: the tag (story-12), at most 64 bytes of ASCII (an HTTP/2 header); a tag in another
    script (a team's name) becomes a short hash of it, still one per tag."""
    import hashlib
    return tag[:64] if tag.isascii() else "t-" + hashlib.sha256(tag.encode()).hexdigest()[:40]


class Http:
    """One HTTP/2 connection to APNs, kept (Apple asks for that); a test gives its own base."""

    def __init__(self):
        self._client = None
        self._lock = threading.Lock()  # cards are drawn from several threads; one request at a time is plenty

    def __call__(self, url: str, headers: dict, body: bytes) -> tuple[int, str]:
        import httpx
        try:
            with self._lock:
                if self._client is None:
                    self._client = httpx.Client(http1=False, http2=True, timeout=15)
                r = self._client.post(url, headers=headers, content=body)
        except (httpx.HTTPError, OSError, ValueError) as e:
            log.warning("apns: %s", type(e).__name__)
            self._client = None
            return 0, ""
        try:
            reason = str((r.json() or {}).get("reason", "")) if r.content else ""
        except ValueError:
            reason = ""
        return r.status_code, reason


_http = Http()


def send(state: Path, cfg: dict, title: str, ref: str = "", tag: str = "", id: int | None = None,
         http=None, hosts: dict | None = None) -> dict:
    """One event to every registered device: {sent, failed, dropped}, or {off: what is missing}."""
    if (why := missing(cfg)):
        return {"sent": 0, "failed": 0, "dropped": 0, "off": "; ".join(why)}
    text = str((cfg.get("notify") or {}).get("apns_text", "title"))
    body = payload(title, ref, tag, id, "none" if text == "none" else "title")
    http, hosts = http or _http, hosts or HOSTS
    out = {"sent": 0, "failed": 0, "dropped": 0}
    rows = devices(state)
    if not rows:
        return out
    try:
        token = bearer(cfg)
    except (OSError, ValueError, IndexError, TypeError) as e:  # a key that can't be read: off, never a broken card
        log.warning("apns: the key %s can't be used (%s)", key_path(), type(e).__name__)
        return out | {"off": f"{key_path()} can't be read as a P-256 key (.p8)"}
    from . import notify
    for name, row in rows.items():
        headers = {"authorization": "bearer " + token, "apns-topic": row["topic"], "apns-push-type": "alert",
                   "apns-priority": "10", "apns-expiration": str(int(time.time()) + TTL)}
        if tag:
            headers["apns-collapse-id"] = collapse_id(tag)
        status, reason = http(f"{hosts[row['environment']]}/3/device/{row['token']}", headers, body)
        if status == 200:
            out["sent"] += 1
        elif status == 410 or reason == "Unregistered":
            unregister(state, name, row["token"])
            out["dropped"] += 1
        else:
            out["failed"] += 1
            if reason in ("ExpiredProviderToken", "InvalidProviderToken"):
                forget_bearer()
            log.warning("apns to %s: HTTP %s %s", name, status, reason)
        notify.count(state, "apns")
    return out


class Apns(WebPush):
    """The channel: what web push would send, to the registered devices through APNs instead."""
    name = "apns"

    def __init__(self, state: Path, cfg: dict, http=None, hosts: dict | None = None):
        super().__init__(state)
        self.cfg, self._send_http, self.hosts = cfg, http, hosts

    def owns(self, chat: str) -> bool:
        return chat == CHAT

    def deliver(self, kind: str, title: str, body: str, link: str, tag: str, ref: str) -> bool:
        from . import events, notify
        r = send(self.state, notify.config() or self.cfg, title, ref, tag,
                 events.recent(self.state, kind, title, body, ref=ref), http=self._send_http, hosts=self.hosts)
        return r["failed"] == 0


def mode(cfg: dict) -> str:
    """[notify] apns: off (the default) | ends | all."""
    from . import notify
    return notify.mode(cfg, "apns")


def channel(cfg: dict, state: Path):
    """The APNs channel in its mode, followed live like every [notify] key (notify.watch)."""
    from . import notify
    return notify.Quiet(Apns(state, cfg), mode(cfg), "apns")
